"""Preparing on Zima itself: the same contract rules as the laptop, with the library at hand."""

import os
import shutil

import test_preparation as preparation_fixtures
from test_agent_commands import gateway as model_gateway
from test_assessment import D3
from test_assessment import gateway as d3_gateway

from agent.__main__ import execute, execute_assessment, turn_on_models
from agent.assess import contract
from agent.prepare import configuration
from server import pipeline
from server.db import fingerprint
from server.preparation_contract import PreparationConfig
from worker import preparer

prepared_workspace = preparation_fixtures.prepared_workspace
OWNER = "zima-prepare-0"


def local():
    config = configuration()
    return config, fingerprint(config.model_dump())


def laptop_contract(config):
    return PreparationConfig.model_validate(
        {**config.model_dump(), "runtime": "3.12.0-Windows-AMD64"}
    )


def job_state(db, digest):
    with db.read() as c:
        return c.execute(
            "SELECT state,error FROM stage_jobs WHERE stage='prepare' AND fingerprint=?",
            (digest,),
        ).fetchone()


def on_the_laptop(client, config):
    """The workspace's only photo, queued under a contract that isn't this machine's."""
    response = client.post(
        "/api/pipeline/preparation/configure",
        json=laptop_contract(config).model_dump(),
    )
    assert response.status_code == 200, response.text
    return response.json()["fingerprint"]


def move_here(client):
    response = client.post("/api/pipeline/preparation/run-here")
    assert response.status_code == 200, response.text
    return response.json()


def test_it_does_nothing_while_the_laptop_holds_the_contract(prepared_workspace):
    client, db, _, _, config, _ = prepared_workspace
    laptop = on_the_laptop(client, config)
    assert client.get("/api/pipeline/preparation").json()["runner"] == "laptop"
    stages = client.get("/api/stages").json()
    assert stages["preparationRunner"] == "laptop"
    assert stages["items"][1]["location"] == "laptop"
    config, digest = local()
    assert preparer.prepare_next(db, config, digest, OWNER) is False
    assert job_state(db, laptop)["state"] == "queued"


def test_switching_prepares_from_the_library_and_leaves_originals(prepared_workspace):
    client, db, source, _, config, claim = prepared_workspace
    on_the_laptop(client, config)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
    assert move_here(client)["queued"] == 1
    stages = client.get("/api/stages").json()
    assert stages["preparationRunner"] == "server"
    assert stages["items"][1]["location"] == "zima"
    assert stages["items"][1]["state"] == "waiting"

    config, digest = local()
    assert preparer.prepare_next(db, config, digest, OWNER) is True
    assert job_state(db, digest)["state"] == "done"
    photo = client.get("/api/photos/" + claim["sha256"]).json()
    assert (photo["width"], photo["height"]) == (40, 80)
    assert photo["thumbUrl"]
    assert preparer.prepare_next(db, config, digest, OWNER) is False
    assert {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()
    } == before


def test_the_laptop_gets_no_prepare_work_after_the_switch(prepared_workspace):
    client, _, _, _, config, _ = prepared_workspace
    laptop = on_the_laptop(client, config)
    move_here(client)
    response = client.post(
        "/api/agent/claim",
        json={"owner": "laptop", "stage": "prepare", "fingerprint": laptop},
    )
    assert response.json() is None


def test_switching_back_owes_the_work_again(prepared_workspace):
    client, db, _, _, config, _ = prepared_workspace
    laptop = on_the_laptop(client, config)
    move_here(client)
    assert job_state(db, laptop)["state"] == "skipped"
    assert on_the_laptop(client, config) == laptop
    assert job_state(db, laptop)["state"] == "queued"


def test_zima_never_makes_the_laptop_look_online(prepared_workspace):
    client, db, _, _, config, _ = prepared_workspace
    on_the_laptop(client, config)
    move_here(client)
    with db.write() as c:
        c.execute("DELETE FROM agent_contacts")
    config, digest = local()
    assert preparer.prepare_next(db, config, digest, OWNER) is True
    assert client.get("/api/status").json()["laptop"]["state"] == "offline"


def test_pause_holds_it(prepared_workspace):
    client, db, _, _, config, _ = prepared_workspace
    on_the_laptop(client, config)
    move_here(client)
    client.post("/api/analyze/pause")
    config, digest = local()
    assert preparer.prepare_next(db, config, digest, OWNER) is False
    assert job_state(db, digest)["state"] == "paused"
    client.post("/api/analyze/resume")
    assert preparer.prepare_next(db, config, digest, OWNER) is True


def test_a_bad_photo_fails_alone_with_its_reason(prepared_workspace):
    client, db, source, _, config, _ = prepared_workspace
    on_the_laptop(client, config)
    move_here(client)
    path = next(source.iterdir())
    original = path.read_bytes()
    found = path.stat()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    os.utime(path, ns=(found.st_atime_ns, found.st_mtime_ns))
    config, digest = local()
    assert preparer.prepare_next(db, config, digest, OWNER) is True
    row = job_state(db, digest)
    assert row["state"] == "failed"
    assert "mismatch" in row["error"]


def test_an_unmounted_library_fails_no_photo(prepared_workspace):
    client, db, source, _, config, _ = prepared_workspace
    on_the_laptop(client, config)
    move_here(client)
    moved = source.with_name("elsewhere")
    shutil.move(source, moved)
    config, digest = local()
    assert preparer.prepare_next(db, config, digest, OWNER) is False
    row = job_state(db, digest)
    assert row["state"] == "queued"
    assert row["error"] == preparer.UNMOUNTED
    shutil.move(moved, source)
    assert preparer.prepare_next(db, config, digest, OWNER) is True
    assert job_state(db, digest)["state"] == "done"


def test_preparing_again_keeps_d3s_answers(prepared_workspace):
    client, db, source, root, config, _ = prepared_workspace
    laptop = on_the_laptop(client, config)
    job = client.post(
        "/api/agent/claim",
        json={"owner": "laptop", "stage": "prepare", "fingerprint": laptop},
    ).json()
    execute(client, job, {root: str(source)}, laptop_contract(config))
    target = client.post("/api/pipeline/assessment/configure", json=contract(D3)).json()
    job = client.post(
        "/api/agent/claim",
        json={
            "owner": "laptop",
            "stage": "assess",
            "fingerprint": target["fingerprint"],
        },
    ).json()
    assert execute_assessment(client, job, {root: str(source)}, d3_gateway()[0], target)
    url = "/api/photos/" + job["sha256"]
    assessed = client.get(url).json()
    answers = [row for row in assessed["evidence"] if row["source"] == "d3nano"]
    assert answers and assessed["sensitive"] == "no"

    move_here(client)
    # Held while it is being prepared again, never less careful than before.
    assert client.get(url).json()["triage"] == "needs_review"
    config, digest = local()
    assert preparer.prepare_next(db, config, digest, OWNER) is True
    again = client.get(url).json()
    assert [row for row in again["evidence"] if row["source"] == "d3nano"] == answers
    assert again["sensitive"] == "no"
    assert again["triage"] == assessed["triage"]
    assert not any(
        "importance and sensitivity unresolved" in row["text"]
        for row in again["evidence"]
    )


def test_turn_on_models_does_not_move_it_back(prepared_workspace):
    client, _, _, _, config, _ = prepared_workspace
    on_the_laptop(client, config)
    move_here(client)
    message = turn_on_models(client, model_gateway(), laptop_contract(config))
    assert "Zima prepares" in message
    assert "0 to prepare" in message
    assert client.get("/api/pipeline/preparation").json()["runner"] == "server"


def test_turn_on_models_still_turns_preparation_on_the_laptop(prepared_workspace):
    client, _, _, _, config, _ = prepared_workspace
    on_the_laptop(client, config)
    message = turn_on_models(client, model_gateway(), laptop_contract(config))
    assert message.startswith("Preparation, SigLIP 2 and D3 Nano are on.")
    assert client.get("/api/pipeline/preparation").json()["runner"] == "laptop"


def test_without_opencv_the_server_says_so(prepared_workspace, monkeypatch):
    client, *_ = prepared_workspace
    monkeypatch.setattr(pipeline, "local_preparation", lambda: None)
    assert client.get("/api/pipeline/preparation").json()["runner"] == "laptop"
    assert client.post("/api/pipeline/preparation/run-here").status_code == 503


def test_thread_count_leaves_a_core(monkeypatch):
    monkeypatch.delenv("PHOTOSIFT_PREPARE_THREADS", raising=False)
    monkeypatch.setattr(preparer.os, "cpu_count", lambda: 4)
    assert preparer.thread_count() == 3
    monkeypatch.setattr(preparer.os, "cpu_count", lambda: 1)
    assert preparer.thread_count() == 1
    monkeypatch.setenv("PHOTOSIFT_PREPARE_THREADS", "0")
    assert preparer.thread_count() == 0
