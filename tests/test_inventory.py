import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import assert_ok
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Database
from worker.scanner import queue_scan, scan


def snapshot(root):
    # Reading can update OS-managed atime. File contents, names, sizes, modes and mtimes
    # are invariant; deployments should mount originals RO (and noatime where supported).
    return {
        p.relative_to(root).as_posix(): (
            p.is_dir(),
            p.stat().st_mode,
            p.stat().st_size if p.is_file() else None,
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None,
        )
        for p in [root, *root.rglob("*")]
    }


def test_scanner_never_modifies_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "nested").mkdir()
    (source / "nested" / "photo.jpg").write_bytes(b"photo bytes")
    (source / "photo-copy.jpg").write_bytes(b"photo bytes")
    (source / "unsupported.bin").write_bytes(b"unknown")
    db = Database(tmp_path / "app" / "state.sqlite3")
    client = TestClient(create_app(db.path))
    root = assert_ok(
        client.post("/api/roots", json={"path": str(source), "name": "Read only"}), 201
    )["id"]
    before = snapshot(source)
    original_open = os.open

    def guarded_open(path, flags, *args, **kwargs):
        target = Path(path).resolve() if isinstance(path, (str, Path)) else None
        if target and target.is_relative_to(source):
            assert not flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            )
        return original_open(path, flags, *args, **kwargs)

    with patch("os.open", side_effect=guarded_open):
        for verify in (False, False, True):
            scan(db, queue_scan(db, root, verify=verify))
    assert snapshot(source) == before
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM contents").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM file_instances").fetchone()[0] == 3
        assert c.execute("SELECT COUNT(*) FROM stage_jobs").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM file_versions").fetchone()[0] == 3
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_incremental_rehash_and_content_history(workspace):
    client, db, source, root = workspace
    with patch(
        "worker.scanner.hash_source",
        side_effect=AssertionError("Unchanged source was rehashed"),
    ):
        scan(db, queue_scan(db, root))
    path = source / "b.png"
    previous = path.stat()
    path.write_bytes(
        b"changed imag"
    )  # same byte length, preserve timestamp to exercise verify mode
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    scan(db, queue_scan(db, root, verify=True))
    with db.read() as c:
        instance = c.execute(
            "SELECT * FROM file_instances WHERE path='b.png'"
        ).fetchone()
        versions = c.execute(
            "SELECT * FROM file_versions WHERE instance_id=?", (instance["id"],)
        ).fetchall()
        assert len(versions) == 2
        assert instance["content_id"] == hashlib.sha256(b"changed imag").hexdigest()
        assert c.execute("SELECT COUNT(*) FROM contents").fetchone()[0] == 4


def test_complete_only_missing_and_subtree_boundary(workspace):
    _, db, source, root = workspace
    (source / "a.jpg").unlink()
    scan(db, queue_scan(db, root, folder="copies"))
    with db.read() as c:
        assert (
            c.execute("SELECT state FROM file_instances WHERE path='a.jpg'").fetchone()[
                0
            ]
            == "available"
        )
    scan(db, queue_scan(db, root))
    with db.read() as c:
        assert (
            c.execute("SELECT state FROM file_instances WHERE path='a.jpg'").fetchone()[
                0
            ]
            == "missing"
        )
    disconnected = source.with_name("disconnected")
    source.rename(disconnected)
    identifier = queue_scan(db, root)
    scan(db, identifier)
    with db.read() as c:
        assert (
            c.execute(
                "SELECT state FROM scan_runs WHERE id=?", (identifier,)
            ).fetchone()[0]
            == "unavailable"
        )
        assert (
            c.execute("SELECT state FROM file_instances WHERE path='b.png'").fetchone()[
                0
            ]
            == "available"
        )


def test_partial_scan_records_unreadable_without_mass_missing(workspace):
    _, db, source, root = workspace
    (source / "a.jpg").unlink()
    identifier = queue_scan(db, root, verify=True)
    with patch(
        "worker.scanner.hash_source", side_effect=PermissionError("Disconnected")
    ):
        scan(db, identifier)
    with db.read() as c:
        assert (
            c.execute(
                "SELECT state FROM scan_runs WHERE id=?", (identifier,)
            ).fetchone()[0]
            == "partial"
        )
        assert (
            c.execute("SELECT state FROM file_instances WHERE path='a.jpg'").fetchone()[
                0
            ]
            == "available"
        )
        assert (
            c.execute("SELECT state FROM file_instances WHERE path='b.png'").fetchone()[
                0
            ]
            == "unreadable"
        )


def test_scanner_skips_symlinks(workspace, tmp_path):
    _, db, source, root = workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.jpg").write_bytes(b"secret")
    try:
        (source / "alias").symlink_to(outside, target_is_directory=True)
        (source / "alias.jpg").symlink_to(outside / "secret.jpg")
    except OSError:
        pytest.skip("OS does not permit test symlink creation")
    scan(db, queue_scan(db, root))
    with db.read() as c:
        assert not c.execute(
            "SELECT 1 FROM file_instances WHERE path LIKE 'alias/%'"
        ).fetchone()
        row = c.execute(
            "SELECT * FROM file_instances WHERE path='alias.jpg'"
        ).fetchone()
        assert row["state"] == "unsupported" and row["content_id"] is None


def test_root_boundaries_and_traversal(workspace):
    client, db, source, root = workspace
    assert (
        client.post(
            "/api/roots", json={"name": "overlap", "path": str(source / "copies")}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/roots", json={"name": "db", "path": str(db.path.parent)}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/roots/{root}/scans", json={"folder": "../escape"}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/photos", params={"root": root, "folder": "../escape"}
        ).status_code
        == 422
    )
    assert client.get("/api/photos", params={"folder": "copies"}).status_code == 422


def test_migrations_reopen_and_detect_modified_ledger(workspace):
    _, db, _, _ = workspace
    Database(db.path)
    with db.write() as c:
        c.execute(
            "UPDATE schema_migrations SET sha256='bad' WHERE name='001_foundation.sql'"
        )
    with pytest.raises(RuntimeError, match="Applied migration changed"):
        Database(db.path)


def test_expired_scan_restarts_seen_membership(workspace):
    _, db, source, root = workspace
    identifier = queue_scan(db, root)
    with db.write() as c:
        c.execute(
            "UPDATE scan_runs SET state='running',expires=0,token='old' WHERE id=?",
            (identifier,),
        )
        c.execute(
            "UPDATE file_instances SET last_scan=? WHERE path='b.png'", (identifier,)
        )
    (source / "b.png").unlink()
    scan(db, identifier)
    with db.read() as c:
        assert (
            c.execute("SELECT state FROM file_instances WHERE path='b.png'").fetchone()[
                0
            ]
            == "missing"
        )
