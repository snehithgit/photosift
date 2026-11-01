"""Leased embedding jobs through the agent's real gateway client, with a stubbed model."""

import io
import json

import httpx
import numpy as np
import test_preparation as preparation_fixtures
from PIL import Image

from agent.__main__ import (
    MAX_EMBED_ATTEMPTS,
    Notes,
    embedding_target,
    execute,
    execute_embedding,
)
from agent.embed import AGENT_INPUT, Gateway, Unavailable, index_config, model_input
from server.db import fingerprint

prepared_workspace = preparation_fixtures.prepared_workspace

GATEWAY_CONFIG = {
    "backend": "siglip2",
    "checkpoint": "test-fixture-only",
    "revision": "a" * 40,
    "dimension": 16,
    "preprocessing": {"image": {"size": 384}},
    "precision": "float16",
    "normalization": "l2",
    "runtime": "fixture-v1",
}
GATEWAY_FINGERPRINT = fingerprint(GATEWAY_CONFIG)
UNIT = [1.0] + [0.0] * 15


def gateway(encode=None, state="ready", config=GATEWAY_CONFIG):
    """The real client against a stub; `encode` overrides the /v1/embeddings reply."""
    received = []

    def handle(request):
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "embedding": {
                        "state": state,
                        "indexConfig": config,
                        "fingerprint": fingerprint(config),
                    }
                },
            )
        received.append(json.loads(request.content))
        if encode:
            return encode(request)
        return httpx.Response(
            200,
            json={
                "fingerprint": fingerprint(config),
                "data": [{"embedding": UNIT}],
            },
        )

    return (
        Gateway("http://gateway.test", "token", transport=httpx.MockTransport(handle)),
        received,
    )


def activate(client):
    response = client.post(
        "/api/pipeline/embedding/configure", json=index_config(GATEWAY_CONFIG)
    )
    assert response.status_code == 200, response.text
    return response.json()


def claim_embed(client, digest):
    response = client.post(
        "/api/agent/claim",
        json={"owner": "laptop", "stage": "embed", "fingerprint": digest},
    )
    assert response.status_code == 200, response.text
    return response.json()


def job_state(db, stage="embed"):
    with db.read() as c:
        row = c.execute(
            "SELECT state,error FROM stage_jobs WHERE stage=?", (stage,)
        ).fetchone()
    return (row["state"], row["error"]) if row else None


def ready_workspace(workspace):
    client, db, source, root, config, claim = workspace
    execute(client, claim, {root: str(source)}, config)
    active = activate(client)
    target = {**active, "gatewayFingerprint": GATEWAY_FINGERPRINT}
    return client, db, source, root, target


def test_activation_queues_prepared_photos_and_later_preparation(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    # Not yet prepared, so nothing to embed.
    active = activate(client)
    assert active["queued"] == 0
    assert client.get("/api/pipeline/embedding").json()["indexId"] == active["indexId"]
    index = client.get("/api/indexes").json()["items"][0]
    assert index["role"] == "eval", "Filling an index must never promote it"
    # The result of a running preparation queues the next stage by itself.
    execute(client, claim, {root: str(source)}, config)
    assert job_state(db) == ("queued", None)
    # Asking again queues nothing twice.
    assert activate(client)["queued"] == 0


def test_real_read_stores_vector_and_keeps_results_small(prepared_workspace):
    client, db, source, root, target = ready_workspace(prepared_workspace)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
    model, received = gateway()
    job = claim_embed(client, target["fingerprint"])
    ack = execute_embedding(client, job, {root: str(source)}, model, target)
    assert ack["accepted"] is True and ack["indexId"] == target["indexId"]
    assert job_state(db) == ("done", None)
    with db.read() as c:
        vector = c.execute(
            "SELECT vector FROM embeddings WHERE content_id=?", (job["sha256"],)
        ).fetchone()[0]
        record = c.execute(
            "SELECT payload FROM stage_results WHERE job_id=?", (job["jobId"],)
        ).fetchone()[0]
    assert np.frombuffer(vector, dtype="<f4").tolist() == UNIT
    assert len(record) < 100, "The vector must not be stored a second time"
    # The gateway got pixels only, never a path.
    assert list(received[0]) == ["images"]
    body = {key: job[key] for key in ("owner", "token", "attempt", "fingerprint")}
    body.update(
        {
            "sha256": job["sha256"],
            "payload": {"indexId": target["indexId"], "vector": UNIT},
        }
    )
    url = "/api/agent/jobs/" + job["jobId"] + "/result"
    assert client.post(url, json=body).json() == ack
    body["payload"]["vector"] = [0.0, 1.0] + [0.0] * 14
    assert client.post(url, json=body).status_code == 409
    assert {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()
    } == before


def test_gateway_outage_hands_job_back_without_failing_photo(prepared_workspace):
    client, db, source, root, target = ready_workspace(prepared_workspace)

    def down(request):
        raise httpx.ConnectError("refused")

    model, _ = gateway(down)
    job = claim_embed(client, target["fingerprint"])
    try:
        execute_embedding(client, job, {root: str(source)}, model, target)
        raise AssertionError("Outage was not reported to the loop")
    except Unavailable:
        pass
    assert job_state(db) == ("queued", "Model service is not reachable")
    # It runs once the service is back.
    model, _ = gateway()
    again = claim_embed(client, target["fingerprint"])
    assert again["jobId"] == job["jobId"]
    assert execute_embedding(client, again, {root: str(source)}, model, target)
    assert job_state(db) == ("done", None)


def test_offline_library_hands_job_back(prepared_workspace, tmp_path):
    client, db, _, root, target = ready_workspace(prepared_workspace)
    model, received = gateway()
    job = claim_embed(client, target["fingerprint"])
    try:
        execute_embedding(
            client, job, {root: str(tmp_path / "unplugged")}, model, target
        )
        raise AssertionError("Offline library was not reported to the loop")
    except Unavailable:
        pass
    assert job_state(db)[0] == "queued" and not received


def test_contract_change_stores_nothing(prepared_workspace):
    client, db, source, root, target = ready_workspace(prepared_workspace)
    model, _ = gateway(
        lambda request: httpx.Response(
            200, json={"fingerprint": "other", "data": [{"embedding": UNIT}]}
        )
    )
    job = claim_embed(client, target["fingerprint"])
    try:
        execute_embedding(client, job, {root: str(source)}, model, target)
        raise AssertionError("Changed contract was accepted")
    except Unavailable:
        pass
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0
    assert job_state(db)[0] == "queued"


def test_refused_image_fails_only_that_photo(prepared_workspace):
    client, db, source, root, target = ready_workspace(prepared_workspace)
    model, _ = gateway(lambda request: httpx.Response(422, json={"detail": "bad"}))
    job = claim_embed(client, target["fingerprint"])
    assert execute_embedding(client, job, {root: str(source)}, model, target) is None
    assert job_state(db) == ("failed", "Model service refused this image")


def test_repeated_gateway_error_stops_blocking_the_queue(prepared_workspace):
    client, db, source, root, target = ready_workspace(prepared_workspace)
    model, _ = gateway(lambda request: httpx.Response(503, json={"detail": "bad"}))
    for attempt in range(1, MAX_EMBED_ATTEMPTS + 1):
        job = claim_embed(client, target["fingerprint"])
        assert job["attempt"] == attempt
        try:
            execute_embedding(client, job, {root: str(source)}, model, target)
            assert attempt == MAX_EMBED_ATTEMPTS
        except Unavailable:
            assert attempt < MAX_EMBED_ATTEMPTS
    assert job_state(db) == ("failed", "Model service returned 503")


def test_mismatched_model_is_never_claimed(prepared_workspace):
    client, _, _, _, target = ready_workspace(prepared_workspace)
    notes = Notes()
    model, _ = gateway()
    assert (
        embedding_target(client, model, notes)["fingerprint"] == target["fingerprint"]
    )
    changed = {**GATEWAY_CONFIG, "runtime": "fixture-v2"}
    model, _ = gateway(config=changed)
    assert embedding_target(client, model, notes) is None
    assert "differs from the active index" in notes.last
    model, _ = gateway(state="loading")
    assert embedding_target(client, model, notes) is None
    assert notes.last == "Embedding waiting: Model service is loading"


def test_new_contract_supersedes_old_queue_and_can_return(prepared_workspace):
    client, db, source, root, target = ready_workspace(prepared_workspace)
    changed = {**GATEWAY_CONFIG, "runtime": "fixture-v2"}
    response = client.post(
        "/api/pipeline/embedding/configure", json=index_config(changed)
    )
    assert response.json()["indexId"] != target["indexId"]
    with db.read() as c:
        states = {
            row["fingerprint"]: row["state"]
            for row in c.execute(
                "SELECT fingerprint,state FROM stage_jobs WHERE stage='embed'"
            )
        }
    assert states == {
        target["fingerprint"]: "skipped",
        response.json()["fingerprint"]: "queued",
    }
    # The agent for the new space cannot be handed the old space's job.
    assert claim_embed(client, target["fingerprint"]) is None
    back = activate(client)
    assert back["indexId"] == target["indexId"] and back["queued"] == 1
    stage = next(
        s for s in client.get("/api/stages").json()["items"] if s["name"] == "embed"
    )
    assert stage["total"] == 1 and stage["counts"]["skipped"] == 1


def test_release_needs_the_current_lease(prepared_workspace):
    client, _, _, _, target = ready_workspace(prepared_workspace)
    job = claim_embed(client, target["fingerprint"])
    response = client.post(
        "/api/agent/jobs/" + job["jobId"] + "/release",
        json={
            "owner": job["owner"],
            "token": "stale",
            "attempt": job["attempt"],
            "reason": "x",
        },
    )
    assert response.status_code == 409


def test_model_input_is_oriented_and_bounded(tmp_path):
    # Worst case: incompressible pixels at the maximum edge must still fit the gateway bound.
    noise = np.random.default_rng(1).integers(0, 255, (3000, 4000, 3), dtype=np.uint8)
    path = tmp_path / "noise.png"
    Image.fromarray(noise).save(path, compress_level=0)
    import hashlib

    stat = path.stat()
    claim = {
        "relativePath": "noise.png",
        "size": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    data = model_input(tmp_path, claim)
    assert len(data) < 8 * 1024 * 1024
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG" and max(image.size) == AGENT_INPUT["maxEdge"]
    # Rotated EXIF is applied before the model sees it.
    tall = tmp_path / "tall.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (80, 40), "red").save(tall, exif=exif)
    stat = tall.stat()
    data = model_input(
        tmp_path,
        {
            "relativePath": "tall.jpg",
            "size": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
            "sha256": hashlib.sha256(tall.read_bytes()).hexdigest(),
        },
    )
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (40, 80)


def test_agent_input_enters_the_index_fingerprint():
    assert fingerprint(index_config(GATEWAY_CONFIG)) != GATEWAY_FINGERPRINT
    assert "agentInput" not in GATEWAY_CONFIG["preprocessing"]
