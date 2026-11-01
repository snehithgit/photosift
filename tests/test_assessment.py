"""Leased D3 jobs through the agent's real gateway client, with a stubbed model."""

import httpx
import pytest
import test_preparation as preparation_fixtures
from fastapi.testclient import TestClient

from agent.__main__ import Notes, assessment_target, execute, execute_assessment
from agent.assess import QUESTIONS, contract
from agent.embed import Gateway, Unavailable
from laptop.app import create_app as create_gateway
from laptop.config import Settings
from laptop.d3 import D3Error

prepared_workspace = preparation_fixtures.prepared_workspace

D3 = {
    "state": "ready",
    "model": "fixture-d3",
    "protocol": "ollama-systemone",
    "endpoint": "/v1/systemone",
    "digest": "d" * 64,
    "runtime": "ollama=fixture",
}


def answers(sensitive=0.01, important=0.01, people=0.01, content="screenshot"):
    return {
        "sensitive": {"type": "noul", "noul": sensitive},
        "important": {"type": "noul", "noul": important},
        "people": {"type": "noul", "noul": people},
        "text": {"type": "noul", "noul": 0.9},
        "content": {
            "type": "choice",
            "choice": content,
            "probabilities": {content: 0.9},
            "confidence": 0.8,
        },
    }


def gateway(reply=None, d3=D3):
    received = []

    def handle(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"d3": d3})
        received.append(request)
        if reply:
            return reply(request)
        return httpx.Response(
            200,
            json={
                "model": D3["model"],
                "answers": answers(),
                "usage": {"input_tokens": 10, "output_tokens": 0},
                "durationMs": 1300.0,
            },
        )

    return (
        Gateway("http://gateway.test", "token", transport=httpx.MockTransport(handle)),
        received,
    )


def ready(workspace):
    client, db, source, root, config, claim = workspace
    execute(client, claim, {root: str(source)}, config)
    response = client.post("/api/pipeline/assessment/configure", json=contract(D3))
    assert response.status_code == 200, response.text
    target = response.json()
    return client, db, source, root, target


def claim_assess(client, digest):
    response = client.post(
        "/api/agent/claim",
        json={"owner": "laptop", "stage": "assess", "fingerprint": digest},
    )
    assert response.status_code == 200, response.text
    return response.json()


def run(client, source, root, target, model=None):
    model = model or gateway()[0]
    job = claim_assess(client, target["fingerprint"])
    ack = execute_assessment(client, job, {root: str(source)}, model, target)
    return job, ack


def stage_job(db):
    with db.read() as c:
        row = c.execute(
            "SELECT state,error FROM stage_jobs WHERE stage='assess'"
        ).fetchone()
    return row["state"], row["error"]


def reply_with(**values):
    return lambda request: httpx.Response(
        200,
        json={
            "model": D3["model"],
            "answers": answers(**values),
            "usage": {},
            "durationMs": 1.0,
        },
    )


def test_clear_answers_resolve_the_holds_without_a_decision(prepared_workspace):
    client, db, source, root, target = ready(prepared_workspace)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
    job, ack = run(client, source, root, target)
    assert (ack["sensitive"], ack["importance"], ack["people"]) == ("no", "no", "no")
    photo = client.get("/api/photos/" + job["sha256"]).json()
    assert (photo["sensitive"], photo["importance"], photo["people"]) == (
        "no",
        "no",
        "no",
    )
    assert photo["decision"] == "undecided"
    assert [row for row in photo["evidence"] if row["source"] == "d3nano"]
    assert not any("unresolved" in row["text"] for row in photo["evidence"])
    assert {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()
    } == before
    stage = next(
        s for s in client.get("/api/stages").json()["items"] if s["name"] == "assess"
    )
    assert stage["state"] == "done" and stage["done"] == 1
    assert client.get("/api/stages").json()["d3"]["state"] == "configured"


@pytest.mark.parametrize(
    "sensitive,expected",
    [(0.9, "yes"), (0.5, "yes"), (0.14, "unknown"), (0.05, "unknown"), (0.01, "no")],
)
def test_sensitive_is_only_cleared_when_clearly_not(
    prepared_workspace, sensitive, expected
):
    client, _, source, root, target = ready(prepared_workspace)
    model, _ = gateway(reply_with(sensitive=sensitive))
    job, ack = run(client, source, root, target, model)
    assert ack["sensitive"] == expected
    photo = client.get("/api/photos/" + job["sha256"]).json()
    assert photo["sensitive"] == expected
    assert photo["triage"] != "clutter_candidate"


def test_people_protect_the_photo(prepared_workspace):
    client, _, source, root, target = ready(prepared_workspace)
    model, _ = gateway(reply_with(people=0.9))
    job, _ = run(client, source, root, target, model)
    photo = client.get("/api/photos/" + job["sha256"]).json()
    assert photo["people"] == "yes"
    assert "faces" in photo["protected"]["reasons"]
    assert photo["triage"] == "needs_review"


def test_clear_screenshot_becomes_a_suggestion_only_after_d3(prepared_workspace):
    client, db, source, root, target = ready(prepared_workspace)
    job = claim_assess(client, target["fingerprint"])
    with db.write() as c:
        c.execute(
            "UPDATE group_assignments SET ai_group='screenshots' WHERE content_id=?",
            (job["sha256"],),
        )
        c.execute("UPDATE photos SET band='high' WHERE content_id=?", (job["sha256"],))
    assert client.get("/api/photos/" + job["sha256"]).json()["triage"] != (
        "clutter_candidate"
    )
    execute_assessment(client, job, {root: str(source)}, gateway()[0], target)
    photo = client.get("/api/photos/" + job["sha256"]).json()
    assert photo["triage"] == "clutter_candidate"
    assert photo["decision"] == "undecided"


def test_outage_hands_the_job_back(prepared_workspace):
    client, db, source, root, target = ready(prepared_workspace)

    def down(request):
        raise httpx.ConnectError("refused")

    job = claim_assess(client, target["fingerprint"])
    with pytest.raises(Unavailable):
        execute_assessment(client, job, {root: str(source)}, gateway(down)[0], target)
    assert stage_job(db) == ("queued", "Model service is not reachable")
    assert client.get("/api/photos/" + job["sha256"]).json()["sensitive"] == "unknown"


def test_refused_image_fails_only_that_photo(prepared_workspace):
    client, db, source, root, target = ready(prepared_workspace)
    model, _ = gateway(lambda request: httpx.Response(400, json={"detail": "bad"}))
    job, ack = run(client, source, root, target, model)
    assert ack is None and stage_job(db) == ("failed", "D3 refused this image")
    assert client.get("/api/photos/" + job["sha256"]).json()["sensitive"] == "unknown"


def test_invalid_answers_change_nothing(prepared_workspace):
    client, db, source, root, target = ready(prepared_workspace)
    for bad in (reply_with(sensitive=1.5), reply_with(content="invented")):
        job, ack = run(client, source, root, target, gateway(bad)[0])
        assert ack is None
        assert stage_job(db)[0] == "failed"
        assert (
            client.get("/api/photos/" + job["sha256"]).json()["sensitive"] == "unknown"
        )
        client.post("/api/analyze/retry")


def test_missing_answer_is_rejected(prepared_workspace):
    client, _, source, root, target = ready(prepared_workspace)
    partial = answers()
    del partial["sensitive"]
    model, _ = gateway(
        lambda request: httpx.Response(
            200,
            json={
                "model": D3["model"],
                "answers": partial,
                "usage": {},
                "durationMs": 1.0,
            },
        )
    )
    job, ack = run(client, source, root, target, model)
    assert ack is None
    assert client.get("/api/photos/" + job["sha256"]).json()["sensitive"] == "unknown"


def test_replay_is_idempotent_and_conflict_refused(prepared_workspace):
    client, _, source, root, target = ready(prepared_workspace)
    job = claim_assess(client, target["fingerprint"])
    payload = {
        "model": D3["model"],
        "answers": answers(),
        "usage": {},
        "durationMs": 1.0,
    }
    body = {key: job[key] for key in ("owner", "token", "attempt", "fingerprint")}
    body.update({"sha256": job["sha256"], "payload": payload})
    url = "/api/agent/jobs/" + job["jobId"] + "/result"
    first = client.post(url, json=body)
    assert first.status_code == 200, first.text
    assert client.post(url, json=body).json() == first.json()
    body["payload"] = {**payload, "answers": answers(sensitive=0.9)}
    assert client.post(url, json=body).status_code == 409


def test_changed_setup_is_never_claimed(prepared_workspace):
    client, _, _, _, target = ready(prepared_workspace)
    notes = Notes()
    assert (
        assessment_target(client, gateway()[0], notes)["fingerprint"]
        == target["fingerprint"]
    )
    changed = {**D3, "digest": "e" * 64}
    assert assessment_target(client, gateway(d3=changed)[0], notes) is None
    assert "differs from the active one" in notes.last
    missing = {**D3, "state": "model_missing"}
    assert assessment_target(client, gateway(d3=missing)[0], notes) is None
    assert notes.last == "Assessment waiting: D3 Nano is model_missing"


def test_preparation_queues_assessment_once_active(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    response = client.post("/api/pipeline/assessment/configure", json=contract(D3))
    assert response.json()["queued"] == 0
    execute(client, claim, {root: str(source)}, config)
    assert stage_job(db) == ("queued", None)


def test_configure_needs_every_question(prepared_workspace):
    client, *_ = prepared_workspace
    partial = {**contract(D3), "questions": {"people": QUESTIONS["people"]}}
    response = client.post("/api/pipeline/assessment/configure", json=partial)
    assert response.status_code == 422


class FakeD3:
    def status(self):
        return D3

    def assess(self, state, images, questions):
        if images == ["refuse"]:
            raise D3Error(400, "D3 refused this request")
        return {"model": "fixture-d3", "answers": {}, "usage": {}, "durationMs": 1.0}


class FakeEncoder:
    def status(self):
        return {"state": "ready"}

    def unload(self):
        return self.status()


def test_gateway_route_needs_token_and_passes_errors(tmp_path):
    token = tmp_path / "token"
    token.write_text("x" * 48)
    settings = Settings(tokenFile=str(token), loadOnStart=False)
    app = create_gateway(settings, FakeEncoder(), FakeD3())
    with TestClient(app) as client:
        auth = {"Authorization": "Bearer " + "x" * 48}
        body = {"state": "s", "images": ["a"], "questions": QUESTIONS}
        assert client.get("/health").json()["d3"]["state"] == "ready"
        assert client.post("/v1/systemone", json=body).status_code == 401
        assert client.post("/v1/systemone", json=body, headers=auth).status_code == 200
        refused = {**body, "images": ["refuse"]}
        assert (
            client.post("/v1/systemone", json=refused, headers=auth).status_code == 400
        )
        for invalid in (
            {**body, "images": []},
            {**body, "path": "C:/original.jpg"},
            {**body, "model": "other"},
        ):
            assert (
                client.post("/v1/systemone", json=invalid, headers=auth).status_code
                == 422
            )
