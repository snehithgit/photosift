"""A removed original leaves analysis after a safe rescan, without deleting its history."""

import pytest
import test_preparation as preparation_fixtures

from agent.__main__ import report_failure
from worker.scanner import queue_scan, scan

prepared_workspace = preparation_fixtures.prepared_workspace


def failures(client, **scope):
    return client.get("/api/pipeline/jobs", params={"state": "failed", **scope}).json()


def prepare_stage(client, **scope):
    return next(
        s
        for s in client.get("/api/stages", params=scope).json()["items"]
        if s["name"] == "prepare"
    )


def test_removed_failed_original_leaves_counts_but_preserves_history(
    prepared_workspace,
):
    client, db, source, root, _, claim = prepared_workspace
    report_failure(client, claim, ValueError("Corrupted image"))
    original = source / claim["relativePath"]
    contents = original.read_bytes()
    original.unlink()  # Only this generated fixture; PhotoSift never removes originals.
    # A deletion is not inferred from a retry error: a complete scan must confirm it.
    assert failures(client)["total"] == 1
    scan(db, queue_scan(db, root))
    for scope in ({}, {"root": root}):
        assert failures(client, **scope)["total"] == 0
        stage = prepare_stage(client, **scope)
        assert stage["counts"] == {} and stage["total"] == 0
    client.post("/api/analyze/retry")
    with db.read() as c:
        job = c.execute(
            "SELECT state,error FROM stage_jobs WHERE id=?", (claim["jobId"],)
        ).fetchone()
        assert (job["state"], job["error"]) == ("failed", "Corrupted image")
        assert (
            c.execute(
                "SELECT COUNT(*) FROM photos WHERE content_id=?", (claim["sha256"],)
            ).fetchone()[0]
            == 1
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM file_versions WHERE state='missing'"
            ).fetchone()[0]
            == 1
        )
    assert client.get("/api/photos/" + claim["sha256"]).json()["fileState"] == "missing"
    # Restoring the same bytes makes that retained failure visible/retryable again.
    original.write_bytes(contents)
    scan(db, queue_scan(db, root))
    assert failures(client)["total"] == 1
    client.post("/api/analyze/retry")
    with db.read() as c:
        assert (
            c.execute(
                "SELECT state FROM stage_jobs WHERE id=?", (claim["jobId"],)
            ).fetchone()[0]
            == "queued"
        )


def test_a_remaining_exact_copy_is_still_an_active_failure(prepared_workspace):
    client, db, source, root, _, claim = prepared_workspace
    report_failure(client, claim, ValueError("Corrupted image"))
    original = source / claim["relativePath"]
    copies = source / "copies"
    copies.mkdir()
    (copies / "copy.jpg").write_bytes(original.read_bytes())
    scan(db, queue_scan(db, root))
    original.unlink()
    scan(db, queue_scan(db, root))
    assert failures(client)["total"] == 1
    assert failures(client, root=root, folder="copies")["total"] == 1
    assert prepare_stage(client)["counts"]["failed"] == 1
    # A missing copy in one folder must not borrow availability from another folder.
    with db.write() as c:
        c.execute(
            "UPDATE file_instances SET path='gone/original.jpg' WHERE path=?",
            (claim["relativePath"],),
        )
    assert failures(client, root=root, folder="gone")["total"] == 0
    assert prepare_stage(client, root=root, folder="gone")["counts"] == {}


@pytest.mark.parametrize("offline", [True, False])
def test_incomplete_scans_do_not_clear_current_failures(
    prepared_workspace, monkeypatch, offline
):
    client, db, source, root, _, claim = prepared_workspace
    report_failure(client, claim, ValueError("Corrupted image"))
    if offline:
        source.rename(source.with_name("disconnected"))
    else:

        def interrupted_walk(*args, onerror, **kwargs):
            onerror(PermissionError("One folder is inaccessible"))
            return iter(())

        monkeypatch.setattr("worker.scanner.os.walk", interrupted_walk)
    identifier = queue_scan(db, root)
    scan(db, identifier)
    assert failures(client)["total"] == 1
    assert prepare_stage(client)["counts"]["failed"] == 1
    with db.read() as c:
        assert c.execute(
            "SELECT state FROM scan_runs WHERE id=?", (identifier,)
        ).fetchone()[0] == ("unavailable" if offline else "partial")
