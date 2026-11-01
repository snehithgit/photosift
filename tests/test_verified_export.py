import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from test_workflows import make_exportable

from worker.exporter import process_export, publish_exclusive


def approve(workspace, tmp_path):
    client, db, source, root = workspace
    make_exportable(client, db)
    destination = tmp_path / "output"
    destination.mkdir()
    response = client.post(
        "/api/export/plans",
        json={"query": {"root": root}, "destination": str(destination)},
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    response = client.post(
        "/api/export/plans/" + plan["id"] + "/approve",
        json={"revision": plan["revision"], "fingerprint": plan["fingerprint"]},
    )
    assert response.status_code == 202, response.text
    return client, db, source, destination, plan, response.json()


def snapshot(source):
    return {
        p.relative_to(source).as_posix(): (
            p.read_bytes(),
            p.stat().st_mtime_ns,
            p.stat().st_ino,
        )
        for p in source.rglob("*")
        if p.is_file()
    }


def test_independent_verified_copies_disk_manifest_and_safe_rollback(
    workspace, tmp_path
):
    client, db, source, destination, plan, run = approve(workspace, tmp_path)
    original = snapshot(source)
    assert process_export(db)
    status = client.get("/api/export/runs/" + run["id"]).json()
    assert status["state"] == "done", status
    assert status["copied"] == status["verified"] == 2
    for entry in plan["rows"]:
        output = Path(entry["destination"])
        assert hashlib.sha256(output.read_bytes()).hexdigest() == entry["sha256"]
        assert output.stat().st_ino != Path(entry["source"]).stat().st_ino
    manifest_path = destination / ("PhotoSift-" + run["id"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["state"] == "done" and manifest["verified"] is True
    # A user-edited output must survive rollback; the other unchanged owned output goes.
    changed = Path(plan["rows"][0]["destination"])
    changed.write_bytes(b"user-edited export")
    assert client.post("/api/export/runs/" + run["id"] + "/rollback").status_code == 200
    assert process_export(db)
    status = client.get("/api/export/runs/" + run["id"]).json()
    assert status["state"] == "rollback-conflicts", status
    assert changed.read_bytes() == b"user-edited export"
    assert not Path(plan["rows"][1]["destination"]).exists()
    assert snapshot(source) == original
    journal = client.get("/api/export/runs/" + run["id"] + "/journal").json()
    assert any(entry["event"] == "published" for entry in journal["items"])
    assert any(entry["event"] == "rollback-conflict" for entry in journal["items"])


def test_crash_after_atomic_publish_recovers_without_overwrite(workspace, tmp_path):
    client, db, source, _, plan, run = approve(workspace, tmp_path)
    original = snapshot(source)

    class Crash(BaseException):
        pass

    def crash(temporary, destination):
        publish_exclusive(temporary, destination)
        raise Crash("process stopped after rename but before acknowledgement")

    with (
        patch("worker.exporter.publish_exclusive", side_effect=crash),
        pytest.raises(Crash),
    ):
        process_export(db)
    with db.write() as c:
        c.execute("UPDATE export_runs SET expires=0 WHERE id=?", (run["id"],))
    assert process_export(db)
    status = client.get("/api/export/runs/" + run["id"]).json()
    assert status["state"] == "done", status
    assert all(Path(row["destination"]).exists() for row in plan["rows"])
    assert snapshot(source) == original
    events = client.get("/api/export/runs/" + run["id"] + "/journal").json()["items"]
    assert any(entry["event"] == "recovered-publication" for entry in events)


def test_destination_collision_preserved_and_retry_is_explicit(workspace, tmp_path):
    client, db, source, _, plan, run = approve(workspace, tmp_path)
    original = snapshot(source)
    collision = Path(plan["rows"][0]["destination"])
    collision.parent.mkdir(parents=True)
    collision.write_bytes(b"unowned collision")
    assert process_export(db)
    status = client.get("/api/export/runs/" + run["id"]).json()
    assert status["state"] == "failed"
    assert collision.read_bytes() == b"unowned collision"
    # Test cleanup removes only the file this test created, then requests a real retry.
    collision.unlink()
    assert client.post("/api/export/runs/" + run["id"] + "/retry").status_code == 200
    assert process_export(db)
    assert client.get("/api/export/runs/" + run["id"]).json()["state"] == "done"
    assert snapshot(source) == original


def test_pause_before_worker_and_rollback_all_verified_outputs(workspace, tmp_path):
    client, db, source, _, plan, run = approve(workspace, tmp_path)
    original = snapshot(source)
    assert client.post("/api/export/runs/" + run["id"] + "/pause").status_code == 200
    assert not process_export(db)
    assert not any(Path(row["destination"]).exists() for row in plan["rows"])
    assert client.post("/api/export/runs/" + run["id"] + "/resume").status_code == 200
    assert process_export(db)
    assert client.post("/api/export/runs/" + run["id"] + "/rollback").status_code == 200
    assert process_export(db)
    assert client.get("/api/export/runs/" + run["id"]).json()["state"] == "rolled-back"
    assert not any(Path(row["destination"]).exists() for row in plan["rows"])
    assert snapshot(source) == original


def test_source_replacement_after_approval_never_publishes_wrong_bytes(
    workspace, tmp_path
):
    client, db, _, _, plan, run = approve(workspace, tmp_path)
    entry = plan["rows"][0]
    path = Path(entry["source"])
    data = path.read_bytes()
    path.write_bytes(b"X" * len(data))
    assert process_export(db)
    assert client.get("/api/export/runs/" + run["id"]).json()["state"] == "failed"
    assert not Path(entry["destination"]).exists()
    assert path.read_bytes() == b"X" * len(data)


def test_rollback_directly_after_publication_crash_never_copies_pending_items(
    workspace, tmp_path
):
    client, db, source, _, plan, run = approve(workspace, tmp_path)
    original = snapshot(source)

    class Crash(BaseException):
        pass

    def crash(temporary, destination):
        publish_exclusive(temporary, destination)
        raise Crash()

    with (
        patch("worker.exporter.publish_exclusive", side_effect=crash),
        pytest.raises(Crash),
    ):
        process_export(db)
    client.post("/api/export/runs/" + run["id"] + "/rollback")
    with db.write() as c:
        c.execute("UPDATE export_runs SET expires=0 WHERE id=?", (run["id"],))
    assert process_export(db)
    assert client.get("/api/export/runs/" + run["id"]).json()["state"] == "rolled-back"
    assert not any(Path(row["destination"]).exists() for row in plan["rows"])
    assert snapshot(source) == original
