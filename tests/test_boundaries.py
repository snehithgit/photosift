import os
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import assert_ok
from test_review import listing, select

from server.db import Database
from worker.scanner import queue_scan, scan


def test_concurrent_fresh_database_startup(tmp_path):
    for iteration in range(5):
        path = tmp_path / str(iteration) / "state.sqlite3"
        with ThreadPoolExecutor(max_workers=4) as pool:
            databases = list(pool.map(Database, [path] * 4))
        with databases[0].read() as c:
            assert (
                c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 4
            )
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_scan_pause_resume_cancel_and_missing_safety(workspace):
    client, db, source, root = workspace
    (source / "a.jpg").unlink()
    identifier = queue_scan(db, root)
    assert_ok(client.post("/api/analyze/pause"))
    assert scan(db, identifier) is False
    with pytest.raises(sqlite3.IntegrityError):
        queue_scan(db, root)
    with db.read() as c:
        assert (
            c.execute("SELECT state FROM file_instances WHERE path='a.jpg'").fetchone()[
                0
            ]
            == "available"
        )
    assert_ok(client.post("/api/analyze/resume"))
    scan(db, identifier)
    assert assert_ok(client.get("/api/scans/" + identifier))["state"] == "done"
    identifier = queue_scan(db, root)
    assert_ok(client.post("/api/analyze/cancel"))
    assert assert_ok(client.get("/api/scans/" + identifier))["state"] == "cancelled"


def test_replacement_preserves_history_but_exits_active_content(workspace):
    client, db, source, root = workspace
    before = next(p for p in listing(client)["items"] if p["filename"] == "b.png")
    (source / "b.png").write_bytes(b"replacement bytes")
    scan(db, queue_scan(db, root))
    current = listing(client)
    assert current["total"] == 2
    assert before["contentId"] not in [p["contentId"] for p in current["items"]]
    historic = assert_ok(client.get("/api/photos/" + before["contentId"]))
    assert historic["paths"] == [] and historic["fileState"] == "missing"
    with db.read() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM file_versions WHERE content_id=?",
                (before["contentId"],),
            ).fetchone()[0]
            == 1
        )


def test_source_inventory_change_conflicts_with_frozen_bulk(workspace):
    client, db, source, root = workspace
    frozen = select(client)
    (source / "b.png").write_bytes(b"changed bytes")
    scan(db, queue_scan(db, root))
    job = assert_ok(
        client.post(
            "/api/bulk",
            json={"selectionId": frozen["selectionId"], "decision": "later"},
        ),
        202,
    )
    from server.review import process_bulk

    while process_bulk(db):
        pass
    assert assert_ok(client.get("/api/jobs/" + job["id"]))["conflicts"] == 1
    assert (
        assert_ok(client.get("/api/jobs/" + job["id"] + "/items?state=conflict"))[
            "total"
        ]
        == 1
    )


def test_possible_matches_unticked_group_scoped_and_paginated(workspace):
    client, db, _, _ = workspace
    with db.write() as c:
        c.execute("UPDATE photos SET duplicate_set='possible-test'")
    sets = assert_ok(client.get("/api/duplicates", params={"limit": 1}))
    assert sets["total"] == 2  # one exact set plus one possible set, paged as SETS
    other = assert_ok(client.get("/api/duplicates", params={"limit": 1, "page": 2}))
    possible = next(
        s for s in [*sets["items"], *other["items"]] if s["kind"] == "possible"
    )
    assert possible["preselected"] is False and len(possible["contentIds"]) == 2
    p = listing(client)["items"][0]
    assert_ok(
        client.patch(
            "/api/photos/" + p["contentId"],
            json={"expectedVersion": 0, "groupId": "people"},
        )
    )
    assert all(
        s["kind"] != "possible"
        for s in assert_ok(client.get("/api/duplicates"))["items"]
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction safety check")
def test_windows_junctions_are_never_followed(workspace, tmp_path):
    client, db, source, root = workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.jpg").write_bytes(b"not part of library")
    junction = source / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    try:
        scan(db, queue_scan(db, root))
        with db.read() as c:
            assert not c.execute(
                "SELECT 1 FROM file_instances WHERE path LIKE 'junction/%'"
            ).fetchone()
        assert (
            client.post(
                "/api/roots", json={"name": "alias", "path": str(junction)}
            ).status_code
            == 422
        )
    finally:
        # rmdir on the junction entry itself does not traverse or touch the target.
        os.rmdir(junction)
