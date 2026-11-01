"""Independent copies, exclusive publication and identity-checked recovery/rollback."""

import ctypes
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from server.db import now, packed, uid
from server.paths import checked_path, is_link, overlap


def identity(path):
    info = path.stat(follow_symlinks=False)
    return {
        "dev": info.st_dev,
        "ino": info.st_ino,
        "size": info.st_size,
        "mtime": info.st_mtime_ns,
    }


def same_file(actual, expected):
    return all(actual[key] == expected[key] for key in ("dev", "ino", "size", "mtime"))


def hash_file(path, progress=lambda: None):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
            progress()
    return digest.hexdigest()


def publish_exclusive(temporary, destination):
    """No hardlinks and no overwrite, including when another process wins a race."""
    if os.name == "nt":
        os.rename(temporary, destination)  # Windows refuses an existing destination.
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError(
                "Atomic no-replace rename is unavailable; refusing unsafe fallback"
            )
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(temporary), -100, os.fsencode(destination), 1) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(destination))
        descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    else:
        raise OSError(
            "Atomic no-replace publication is supported on Windows/Linux only"
        )


def journal(c, run_id, content, event, details):
    c.execute(
        "INSERT INTO export_journal(run_id,content_id,event,details,at) VALUES (?,?,?,?,?)",
        (run_id, content, event, packed(details), now()),
    )


def safe_directory(base, directory):
    checked_path(base)
    if not directory.is_relative_to(base):
        raise ValueError("Output escaped the approved run directory")
    current = base
    for part in directory.relative_to(base).parts:
        current = current / part
        if not current.exists():
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
        if is_link(current) or not current.is_dir():
            raise ValueError(
                "Output directory is a symlink/reparse point or non-directory"
            )
    checked_path(directory)


class ExportLease:
    def __init__(self, db, run_id, token):
        self.db, self.run_id, self.token = db, run_id, token
        self.last = 0

    def pulse(self, force=False):
        if not force and time.monotonic() - self.last < 1:
            return
        with self.db.write() as c:
            row = c.execute(
                "SELECT state,token,expires FROM export_runs WHERE id=?", (self.run_id,)
            ).fetchone()
            if (
                row["token"] != self.token
                or not row["expires"]
                or row["expires"] < time.time()
            ):
                raise InterruptedError("Export lease superseded")
            if row["state"] not in ("running", "rolling-back"):
                raise InterruptedError("Export control requested: " + row["state"])
            c.execute(
                "UPDATE export_runs SET expires=? WHERE id=? AND token=?",
                (time.time() + 300, self.run_id, self.token),
            )
        self.last = time.monotonic()


def checked_output(db, run_id, destination, inputs):
    base = Path(inputs["destination"])
    run_root = base / ("PhotoSift-" + run_id)
    target = Path(destination)
    if (
        not target.is_absolute()
        or not target.is_relative_to(run_root)
        or ".." in target.parts
    ):
        raise ValueError("Output does not belong to this approved run")
    with db.read() as c:
        sources = [r[0] for r in c.execute("SELECT path FROM roots")]
    if any(overlap(base, source) for source in sources) or overlap(
        base, db.path.parent
    ):
        raise ValueError("Output overlaps sources or app data")
    checked_path(base)
    return base, run_root, target


def copy_one(db, run_id, entry, operation, inputs, lease):
    content = entry["content_id"]
    base, run_root, target = checked_output(db, run_id, entry["destination"], inputs)
    # A ready journal entry can recover publication after a crash, but only by inode/hash.
    if target.exists() or target.is_symlink():
        expected = (
            json.loads(operation["temporary_identity"])
            if operation["temporary_identity"]
            else None
        )
        if (
            operation["state"] == "ready"
            and expected
            and not is_link(target)
            and same_file(identity(target), expected)
            and hash_file(target, lease.pulse) == entry["sha256"]
        ):
            recovered_identity = identity(target)
            with db.write() as c:
                c.execute(
                    "UPDATE export_operations SET state='verified',output_hash=?,output_identity=?,error=NULL WHERE run_id=? AND content_id=?",
                    (entry["sha256"], packed(recovered_identity), run_id, content),
                )
                journal(
                    c,
                    run_id,
                    content,
                    "recovered-publication",
                    {"destination": str(target)},
                )
            return
        raise FileExistsError(
            "Destination exists without matching publication ownership; preserved"
        )
    with db.read() as c:
        source = c.execute(
            "SELECT f.*,r.path root_path FROM file_instances f JOIN roots r ON r.id=f.root_id WHERE f.id=?",
            (entry["instance_id"],),
        ).fetchone()
    if (
        not source
        or source["content_id"] != entry["sha256"]
        or source["state"] != "available"
    ):
        raise ValueError("Frozen source instance was superseded or unavailable")
    path = checked_path(source["root_path"], source["path"])
    before = identity(path)
    if before["size"] != entry["size"]:
        raise ValueError("Frozen source size changed")
    if shutil.disk_usage(base).free < entry["size"]:
        raise OSError("Insufficient destination capacity")
    safe_directory(base, target.parent)
    temporary = target.parent / (".photosift-" + uid() + ".partial")
    with temporary.open("xb") as output:
        owned = identity(temporary)
        with db.write() as c:
            c.execute(
                "UPDATE export_operations SET state='copying',temporary_path=?,temporary_identity=?,error=NULL WHERE run_id=? AND content_id=?",
                (str(temporary), packed(owned), run_id, content),
            )
            journal(
                c,
                run_id,
                content,
                "temporary-created",
                {"path": str(temporary), "identity": owned},
            )
        digest = hashlib.sha256()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as original:
            opened = os.fstat(original.fileno())
            if (opened.st_dev, opened.st_ino) != (before["dev"], before["ino"]):
                raise ValueError("Source identity changed before copy")
            while block := original.read(1024 * 1024):
                output.write(block)
                digest.update(block)
                lease.pulse()
            after = os.fstat(original.fileno())
        output.flush()
        os.fsync(output.fileno())
    if (
        (after.st_size, after.st_mtime_ns) != (before["size"], before["mtime"])
        or not same_file(
            identity(checked_path(source["root_path"], source["path"])), before
        )
        or digest.hexdigest() != entry["sha256"]
    ):
        raise ValueError("Source checksum/version changed; temporary output preserved")
    if hash_file(temporary, lease.pulse) != entry["sha256"]:
        raise ValueError("Independent copy checksum failed")
    lease.pulse(force=True)
    owned = identity(temporary)
    with db.write() as c:
        c.execute(
            "UPDATE export_operations SET state='ready',temporary_identity=?,output_hash=? WHERE run_id=? AND content_id=?",
            (packed(owned), entry["sha256"], run_id, content),
        )
        journal(
            c,
            run_id,
            content,
            "verified-temporary",
            {"path": str(temporary), "identity": owned, "sha256": entry["sha256"]},
        )
    # Recheck parents after the expensive copy; publishing never overwrites a collision.
    checked_path(target.parent)
    publish_exclusive(temporary, target)
    published_identity = identity(target)
    with db.write() as c:
        c.execute(
            "UPDATE export_operations SET state='verified',output_identity=?,error=NULL WHERE run_id=? AND content_id=?",
            (packed(published_identity), run_id, content),
        )
        journal(
            c,
            run_id,
            content,
            "published",
            {
                "destination": str(target),
                "identity": published_identity,
                "sha256": entry["sha256"],
            },
        )


def rollback_one(db, run_id, entry, operation, inputs, lease):
    _, _, target = checked_output(db, run_id, entry["destination"], inputs)
    if operation["state"] == "rolled-back":
        return
    if operation["state"] not in ("verified", "rollback-ready"):
        if not target.exists() and not target.is_symlink():
            with db.write() as c:
                c.execute(
                    "UPDATE export_operations SET state='cancelled' WHERE run_id=? AND content_id=?",
                    (run_id, entry["content_id"]),
                )
                journal(
                    c,
                    run_id,
                    entry["content_id"],
                    "unpublished-cancelled",
                    {"destination": str(target)},
                )
            return
        if (
            operation["state"] not in ("ready", "failed")
            or not operation["temporary_identity"]
            or operation["output_hash"] != entry["sha256"]
        ):
            raise ValueError(
                "Unjournaled output preserved; publication ownership is unknown"
            )
        expected = json.loads(operation["temporary_identity"])
    else:
        expected = json.loads(operation["output_identity"])
    if not target.exists() and operation["state"] == "rollback-ready":
        # A crash after the journaled removal is safe to acknowledge idempotently.
        with db.write() as c:
            c.execute(
                "UPDATE export_operations SET state='rolled-back' WHERE run_id=? AND content_id=?",
                (run_id, entry["content_id"]),
            )
        return
    if (
        is_link(target)
        or not same_file(identity(target), expected)
        or hash_file(target, lease.pulse) != operation["output_hash"]
        or not same_file(identity(target), expected)
    ):
        raise ValueError(
            "Output changed or ownership is ambiguous; preserved during rollback"
        )
    with db.write() as c:
        c.execute(
            "UPDATE export_operations SET state='rollback-ready' WHERE run_id=? AND content_id=?",
            (run_id, entry["content_id"]),
        )
        journal(
            c,
            run_id,
            entry["content_id"],
            "rollback-ready",
            {"destination": str(target), "identity": expected},
        )
    checked_path(target.parent)
    target.unlink()
    with db.write() as c:
        c.execute(
            "UPDATE export_operations SET state='rolled-back' WHERE run_id=? AND content_id=?",
            (run_id, entry["content_id"]),
        )
        journal(
            c, run_id, entry["content_id"], "rolled-back", {"destination": str(target)}
        )


def write_manifests(db, run_id, inputs, lease, rollback=False, final_state="done"):
    from server.exports import manifest

    with db.read() as c:
        report = manifest(c, run_id, "json")
        report["state"] = final_state
        values = {
            "json": json.dumps(report, indent=2).encode(),
            "csv": manifest(c, run_id, "csv").encode("utf-8"),
        }
    for kind, data in values.items():
        name = ("rollback-manifest" if rollback else "manifest") + "." + kind
        base, run_root, target = checked_output(
            db,
            run_id,
            str(Path(inputs["destination"]) / ("PhotoSift-" + run_id) / name),
            inputs,
        )
        safe_directory(base, run_root)
        sha = hashlib.sha256(data).hexdigest()
        with db.read() as c:
            old = c.execute(
                "SELECT * FROM export_metadata WHERE run_id=? AND kind=?",
                (run_id, name),
            ).fetchone()
        replace_owned = False
        if target.exists():
            if (
                old
                and not is_link(target)
                and same_file(identity(target), json.loads(old["identity"]))
                and hash_file(target, lease.pulse) == old["sha256"]
            ):
                if old["sha256"] == sha:
                    continue
                replace_owned = True
            else:
                raise FileExistsError(
                    "Manifest collision or changed manifest preserved"
                )
        temporary = run_root / ("." + uid() + ".manifest-partial")
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        owned = identity(temporary)
        with db.write() as c:
            c.execute(
                "INSERT INTO export_metadata VALUES (?,?,?,?,?) ON CONFLICT(run_id,kind) DO UPDATE SET path=excluded.path,sha256=excluded.sha256,identity=excluded.identity",
                (run_id, name, str(target), sha, packed(owned)),
            )
            journal(
                c,
                run_id,
                None,
                "manifest-ready",
                {"path": str(target), "identity": owned, "sha256": sha},
            )
        if replace_owned:
            # Only an unchanged, journal-owned manifest is replaced, never a photo output.
            if (
                not same_file(identity(target), json.loads(old["identity"]))
                or hash_file(target, lease.pulse) != old["sha256"]
            ):
                raise ValueError("Manifest changed before atomic update")
            os.replace(temporary, target)
        else:
            publish_exclusive(temporary, target)


def process_export(db):
    token = uid()
    with db.write() as c:
        run = c.execute(
            "SELECT r.*,p.inputs FROM export_runs r JOIN export_plans p ON p.id=r.plan_id WHERE (r.state IN ('queued','rollback-requested') AND (r.expires IS NULL OR r.expires<?)) OR r.state IN ('running','rolling-back') AND r.expires<? ORDER BY r.created_at LIMIT 1",
            (time.time(), time.time()),
        ).fetchone()
        if not run:
            return False
        rollback = run["state"] in ("rollback-requested", "rolling-back")
        c.execute(
            "UPDATE export_runs SET state=?,owner=?,token=?,expires=?,error=NULL WHERE id=?",
            (
                "rolling-back" if rollback else "running",
                "zima",
                token,
                time.time() + 300,
                run["id"],
            ),
        )
        journal(c, run["id"], None, "claimed", {"rollback": rollback, "token": token})
    inputs = json.loads(run["inputs"])
    lease = ExportLease(db, run["id"], token)
    failed = False
    try:
        while True:
            lease.pulse(force=True)
            with db.read() as c:
                eligible = (
                    "o.state IN ('verified','rollback-ready','ready','copying','pending','failed')"
                    if rollback
                    else "o.state IN ('pending','copying','ready')"
                )
                operation = c.execute(
                    "SELECT o.* FROM export_operations o WHERE o.run_id=? AND "
                    + eligible
                    + " ORDER BY o.id LIMIT 1",
                    (run["id"],),
                ).fetchone()
                entry = (
                    c.execute(
                        "SELECT * FROM export_entries WHERE plan_id=? AND content_id=?",
                        (run["plan_id"], operation["content_id"]),
                    ).fetchone()
                    if operation
                    else None
                )
            if not operation:
                break
            try:
                (rollback_one if rollback else copy_one)(
                    db, run["id"], entry, operation, inputs, lease
                )
            except InterruptedError:
                raise
            except (OSError, ValueError) as exc:
                failed = True
                with db.write() as c:
                    c.execute(
                        "UPDATE export_operations SET state=?,error=? WHERE id=?",
                        (
                            "rollback-conflict" if rollback else "failed",
                            str(exc),
                            operation["id"],
                        ),
                    )
                    journal(
                        c,
                        run["id"],
                        operation["content_id"],
                        "rollback-conflict" if rollback else "failed",
                        {"error": str(exc)},
                    )
        with db.read() as c:
            failed = failed or bool(
                c.execute(
                    "SELECT 1 FROM export_operations WHERE run_id=? AND state IN ('failed','rollback-conflict')",
                    (run["id"],),
                ).fetchone()
            )
        # The API manifest can also report failures; files are published atomically.
        final = (
            ("rollback-conflicts" if failed else "rolled-back")
            if rollback
            else ("failed" if failed else "done")
        )
        write_manifests(db, run["id"], inputs, lease, rollback, final)
        with db.write() as c:
            c.execute(
                "UPDATE export_runs SET state=?,expires=NULL,error=? WHERE id=? AND token=?",
                (
                    final,
                    "Some outputs require manual review" if failed else None,
                    run["id"],
                    token,
                ),
            )
            journal(c, run["id"], None, "finished", {"state": final})
    except InterruptedError:
        with db.write() as c:
            c.execute(
                "UPDATE export_runs SET expires=NULL WHERE id=? AND token=? AND state NOT IN ('running','rolling-back')",
                (run["id"], token),
            )
    except (OSError, ValueError) as exc:
        with db.write() as c:
            c.execute(
                "UPDATE export_runs SET state='failed',error=?,expires=NULL WHERE id=? AND token=?",
                (str(exc), run["id"], token),
            )
    return True
