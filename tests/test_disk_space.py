"""A prepare result keeps no images in the database: the cache has them, and a digest is enough."""

import json
import sqlite3

import test_preparation as preparation_fixtures

from agent.prepare import prepare
from server import clear, pipeline
from server.db import fingerprint, packed

prepared_workspace = preparation_fixtures.prepared_workspace


def lease(claim):
    return {key: claim[key] for key in ("owner", "token", "attempt")}


def submit(client, claim, payload):
    return client.post(
        "/api/agent/jobs/" + claim["jobId"] + "/result",
        json={
            **lease(claim),
            "sha256": claim["sha256"],
            "fingerprint": claim["fingerprint"],
            "payload": payload,
        },
    )


def stored(db, claim):
    with db.read() as c:
        return c.execute(
            "SELECT payload FROM stage_results WHERE job_id=?", (claim["jobId"],)
        ).fetchone()[0]


def result_of(workspace):
    client, db, source, _, config, claim = workspace
    payload = prepare(source, claim, config).model_dump()
    response = submit(client, claim, payload)
    assert response.status_code == 200, response.text
    return client, db, claim, payload


def as_before_this_fix(db, claim, payload):
    with db.write() as c:
        c.execute(
            "UPDATE stage_results SET payload=? WHERE job_id=?",
            (packed(payload), claim["jobId"]),
        )


def test_a_new_result_stores_no_images_and_the_photo_still_has_them(prepared_workspace):
    client, db, claim, payload = result_of(prepared_workspace)
    assert json.loads(stored(db, claim)) == {"sha256": fingerprint(payload)}
    assert len(stored(db, claim)) < pipeline.SLIM_PAYLOAD
    photo = client.get("/api/photos/" + claim["sha256"]).json()
    assert client.get(photo["thumbUrl"] + "?reveal=true").status_code == 200
    assert client.get(photo["previewUrl"] + "?reveal=true").status_code == 200


def test_a_replay_is_recognised_and_a_different_answer_refused(prepared_workspace):
    client, _, claim, payload = result_of(prepared_workspace)
    first = submit(client, claim, payload)
    assert first.status_code == 200
    assert first.json()["accepted"] is True
    assert submit(client, claim, {**payload, "meanLuminance": 1.0}).status_code == 409


def test_an_older_result_is_recognised_before_and_after_slimming(prepared_workspace):
    client, db, claim, payload = result_of(prepared_workspace)
    as_before_this_fix(db, claim, payload)
    assert len(stored(db, claim)) > pipeline.SLIM_PAYLOAD
    assert submit(client, claim, payload).status_code == 200
    assert pipeline.slim_results(db) == 1
    assert json.loads(stored(db, claim)) == {"sha256": fingerprint(payload)}
    assert submit(client, claim, payload).status_code == 200
    assert submit(client, claim, {**payload, "meanLuminance": 1.0}).status_code == 409


def test_slimming_finishes_and_changes_nothing_else(prepared_workspace):
    client, db, claim, payload = result_of(prepared_workspace)
    as_before_this_fix(db, claim, payload)
    before = client.get("/api/photos/" + claim["sha256"]).json()
    with db.read() as c:
        ack = c.execute(
            "SELECT ack,token,created_at FROM stage_results WHERE job_id=?",
            (claim["jobId"],),
        ).fetchone()
    assert client.get("/api/database/space").json()["slimming"] is True
    assert pipeline.slim_results(db) == 1
    assert pipeline.slim_results(db) == 0
    assert client.get("/api/database/space").json()["slimming"] is False
    assert client.get("/api/photos/" + claim["sha256"]).json() == before
    with db.read() as c:
        assert tuple(
            c.execute(
                "SELECT ack,token,created_at FROM stage_results WHERE job_id=?",
                (claim["jobId"],),
            ).fetchone()
        ) == tuple(ack)


def test_slimming_leaves_other_stages_alone(prepared_workspace):
    client, db, claim, payload = result_of(prepared_workspace)
    long_answer = packed({"answers": "x" * 1000})
    with db.write() as c:
        c.execute(
            "INSERT INTO stage_jobs(id,content_id,stage,fingerprint,state) VALUES ('a',?,'assess','f','done')",
            (claim["sha256"],),
        )
        c.execute(
            "INSERT INTO stage_results VALUES (?,'assess','f','a','t',?,'{}','now')",
            (claim["sha256"], long_answer),
        )
    assert pipeline.slim_results(db) == 0
    with db.read() as c:
        assert (
            c.execute(
                "SELECT payload FROM stage_results WHERE stage='assess'"
            ).fetchone()[0]
            == long_answer
        )


def test_freeing_space_needs_a_pause(prepared_workspace):
    client, *_ = result_of(prepared_workspace)
    response = client.post("/api/database/compact")
    assert response.status_code == 409
    assert "Pause Analyze" in response.json()["detail"]
    client.post("/api/analyze/pause")
    client.post("/api/analyze/resume")
    assert client.post("/api/database/compact").status_code == 409


def test_freeing_space_waits_for_slimming_and_for_running_work(prepared_workspace):
    client, db, claim, payload = result_of(prepared_workspace)
    as_before_this_fix(db, claim, payload)
    client.post("/api/analyze/pause")
    response = client.post("/api/database/compact")
    assert response.status_code == 409
    assert "still being removed" in response.json()["detail"]
    pipeline.slim_results(db)
    with db.write() as c:
        c.execute(
            "INSERT INTO scan_runs(id,root_id,folder,verify,state,started_at) SELECT 'busy',id,'',0,'running','now' FROM roots"
        )
    response = client.post("/api/database/compact")
    assert response.status_code == 409
    assert "scan" in response.json()["detail"].lower()


def test_freeing_space_shrinks_the_file_and_loses_nothing(prepared_workspace):
    client, db, claim, payload = result_of(prepared_workspace)
    with db.write() as c:
        c.execute(
            "UPDATE stage_results SET payload=? WHERE job_id=?",
            (packed({**payload, "padding": "x" * 30_000_000}), claim["jobId"]),
        )
    connection = db.connect()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    grown = clear.size_on_disk(db)
    pipeline.slim_results(db)
    before = client.get("/api/photos/" + claim["sha256"]).json()
    assert client.get("/api/database/space").json()["freeBytes"] > 20_000_000
    client.post("/api/analyze/pause")
    response = client.post("/api/database/compact")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["after"] < 10_000_000 < 20_000_000 < grown
    assert client.get("/api/database/space").json()["freeBytes"] < 1_000_000
    assert client.get("/api/photos/" + claim["sha256"]).json() == before


def test_a_failed_vacuum_reports_and_changes_nothing(prepared_workspace, monkeypatch):
    client, db, claim, _ = result_of(prepared_workspace)
    client.post("/api/analyze/pause")
    real = db.connect

    class Refuses:
        def __init__(self):
            self.inner = real()

        def execute(self, sql, *args):
            if sql == "VACUUM":
                raise sqlite3.OperationalError("database or disk is full")
            return self.inner.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(db, "connect", Refuses)
    response = client.post("/api/database/compact")
    assert response.status_code == 409
    assert "disk is full" in response.json()["detail"]
    monkeypatch.setattr(db, "connect", real)
    assert client.get("/api/photos/" + claim["sha256"]).status_code == 200
