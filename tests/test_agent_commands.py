"""The web app's requests to the laptop, through the agent's real client with stubbed models."""

import json
import time

import httpx
import test_preparation as preparation_fixtures
from test_assessment import D3
from test_embedding_dispatch import GATEWAY_CONFIG

from agent.__main__ import execute, run_command
from agent.embed import Gateway
from server import commands
from server.db import fingerprint

prepared_workspace = preparation_fixtures.prepared_workspace
UNIT = [1.0] + [0.0] * 15


def gateway(state="ready", d3=D3):
    def handle(request):
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "embedding": {
                        "state": state,
                        "indexConfig": GATEWAY_CONFIG,
                        "fingerprint": fingerprint(GATEWAY_CONFIG),
                    },
                    "d3": d3,
                },
            )
        size = len(json.loads(request.content)["input"])
        return httpx.Response(
            200,
            json={
                "fingerprint": fingerprint(GATEWAY_CONFIG),
                "data": [{"embedding": UNIT} for _ in range(size)],
            },
        )

    return Gateway(
        "http://gateway.test", "token", transport=httpx.MockTransport(handle)
    )


def connect(client, claim):
    # The agent reports in by asking for work.
    response = client.post(
        "/api/agent/claim",
        json={
            "owner": "laptop",
            "stage": "prepare",
            "fingerprint": claim["fingerprint"],
        },
    )
    assert response.status_code == 200, response.text


def shown(client, kind):
    return client.get("/api/laptop/commands").json()[kind]


def test_refused_while_the_laptop_is_away(prepared_workspace):
    client, db, *_ = prepared_workspace
    with db.write() as c:
        c.execute("DELETE FROM agent_contacts")
    response = client.post("/api/laptop/commands", json={"kind": "encode-prompts"})
    assert response.status_code == 409
    assert "isn't connected" in response.json()["detail"]
    assert shown(client, "encode-prompts") is None


def test_only_the_fixed_requests_exist(prepared_workspace):
    client, _, _, _, _, claim = prepared_workspace
    connect(client, claim)
    for body in ({"kind": "run"}, {"kind": "encode-prompts", "command": "x"}, {}):
        assert client.post("/api/laptop/commands", json=body).status_code == 422


def test_agent_routes_need_the_token(prepared_workspace):
    client, *_ = prepared_workspace
    bare = {"Authorization": "Bearer wrong"}
    assert client.post("/api/agent/commands/claim", headers=bare).status_code == 401
    assert (
        client.post(
            "/api/agent/commands/x/finish",
            headers=bare,
            json={"state": "done", "message": "x"},
        ).status_code
        == 401
    )


def test_turn_on_then_encode_from_the_web_app(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    execute(client, claim, {root: str(source)}, config)
    connect(client, claim)

    asked = client.post("/api/laptop/commands", json={"kind": "encode-prompts"}).json()
    assert asked["state"] == "queued"
    assert run_command(client, gateway(), config) is True
    # No index is active yet: the reason reaches the web app, and nothing is saved.
    failed = shown(client, "encode-prompts")
    assert failed["state"] == "failed"
    assert "Turn on models" in failed["message"]

    client.post("/api/laptop/commands", json={"kind": "turn-on-models"})
    assert run_command(client, gateway(), config) is True
    done = shown(client, "turn-on-models")
    assert done["state"] == "done", done
    assert "1 to embed, 1 to assess" in done["message"]
    assert client.get("/api/pipeline/embedding").json()
    assert client.get("/api/pipeline/assessment").json()

    client.post("/api/laptop/commands", json={"kind": "turn-on-models"})
    assert run_command(client, gateway(), config) is True
    again = shown(client, "turn-on-models")
    assert "0 to prepare, 0 to embed, 0 to assess" in again["message"]

    client.post("/api/laptop/commands", json={"kind": "encode-prompts"})
    assert run_command(client, gateway(), config) is True
    encoded = shown(client, "encode-prompts")
    assert encoded["state"] == "done", encoded
    assert "prompts saved" in encoded["message"]
    assert client.get("/api/comparison").json()["promptPacks"]

    client.post("/api/laptop/commands", json={"kind": "encode-prompts"})
    assert run_command(client, gateway(), config) is True
    assert "vectors kept" in shown(client, "encode-prompts")["message"]
    assert run_command(client, gateway(), config) is False


def test_models_down_changes_nothing_on_the_server(prepared_workspace):
    client, _, _, _, config, claim = prepared_workspace
    connect(client, claim)
    client.post("/api/laptop/commands", json={"kind": "turn-on-models"})
    assert run_command(client, gateway(state="loading"), config) is True
    failed = shown(client, "turn-on-models")
    assert failed["state"] == "failed"
    assert "loading" in failed["message"]
    assert client.get("/api/pipeline/embedding").json() is None
    assert client.get("/api/pipeline/assessment").json() is None


def test_a_second_click_does_not_queue_twice(prepared_workspace):
    client, _, _, _, config, claim = prepared_workspace
    connect(client, claim)
    first = client.post("/api/laptop/commands", json={"kind": "encode-prompts"}).json()
    second = client.post("/api/laptop/commands", json={"kind": "encode-prompts"}).json()
    assert first["id"] == second["id"]
    assert run_command(client, gateway(), config) is True
    assert run_command(client, gateway(), config) is False


def test_an_old_request_expires_instead_of_running_later(prepared_workspace):
    client, db, _, _, config, claim = prepared_workspace
    connect(client, claim)
    client.post("/api/laptop/commands", json={"kind": "turn-on-models"})
    with db.write() as c:
        c.execute(
            "UPDATE agent_commands SET created=?",
            (time.time() - commands.EXPIRE_SECONDS - 1,),
        )
    assert shown(client, "turn-on-models")["state"] == "failed"
    assert run_command(client, gateway(), config) is False
    assert client.get("/api/pipeline/embedding").json() is None
    asked = client.post("/api/laptop/commands", json={"kind": "turn-on-models"}).json()
    assert asked["state"] == "queued"


def test_a_late_answer_does_not_undo_the_expiry(prepared_workspace):
    client, db, *_, claim = prepared_workspace
    connect(client, claim)
    asked = client.post("/api/laptop/commands", json={"kind": "encode-prompts"}).json()
    client.post("/api/agent/commands/claim")
    with db.write() as c:
        c.execute(
            "UPDATE agent_commands SET created=?",
            (time.time() - commands.EXPIRE_SECONDS - 1,),
        )
        commands.expire(c)
    response = client.post(
        f"/api/agent/commands/{asked['id']}/finish",
        json={"state": "done", "message": "late"},
    )
    assert response.status_code == 200
    assert shown(client, "encode-prompts")["message"] == commands.EXPIRED


def test_an_older_server_is_not_an_outage(prepared_workspace):
    _, _, _, _, config, _ = prepared_workspace
    old = httpx.Client(
        base_url="http://old.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    assert run_command(old, gateway(), config) is False


def test_pause_holds_work_queued_after_it(prepared_workspace):
    client, _, source, root, config, claim = prepared_workspace
    execute(client, claim, {root: str(source)}, config)
    connect(client, claim)
    client.post("/api/analyze/pause")

    client.post("/api/laptop/commands", json={"kind": "turn-on-models"})
    assert run_command(client, gateway(), config) is True
    assert shown(client, "turn-on-models")["state"] == "done"
    target = client.get("/api/pipeline/embedding").json()
    body = {"owner": "laptop", "stage": "embed", "fingerprint": target["fingerprint"]}
    assert client.post("/api/agent/claim", json=body).json() is None

    client.post("/api/analyze/resume")
    assert (
        client.post("/api/agent/claim", json=body).json()["sha256"] == claim["sha256"]
    )
