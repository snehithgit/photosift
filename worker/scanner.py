"""Inventory reads source bytes only. All writes are short app-database transactions."""

import os
import stat
import time
from pathlib import Path

from server.db import bump, now, packed, uid
from server.paths import checked_path, clean_folder, hash_source, is_link

# Eligibility for future preparation; these formats have NOT yet been decoded/validated.
SUPPORTED = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".heif",
    ".avif",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
}


def queue_scan(db, root_id, folder="", verify=False):
    folder = clean_folder(folder)
    identifier = uid()
    with db.write() as c:
        if not c.execute("SELECT 1 FROM roots WHERE id=?", (root_id,)).fetchone():
            raise ValueError("Unknown library root")
        c.execute(
            "INSERT INTO scan_runs(id,root_id,folder,verify,state,started_at) VALUES (?,?,?,?,?,?)",
            (identifier, root_id, folder, verify, "queued", now()),
        )
    return identifier


def scan(db, scan_id, owner="zima", lease_seconds=300, hash_mib_per_second=32):
    token = uid()
    with db.write() as c:
        run = c.execute(
            "SELECT s.*,r.path AS root_path FROM scan_runs s JOIN roots r ON r.id=s.root_id WHERE s.id=?",
            (scan_id,),
        ).fetchone()
        if not run or run["state"] not in ("queued", "running"):
            return False
        if run["state"] == "running" and (run["expires"] or 0) > time.time():
            return False
        c.execute(
            "UPDATE scan_runs SET state='running',owner=?,token=?,expires=? WHERE id=?",
            (owner, token, time.time() + lease_seconds, scan_id),
        )
        # A restarted traversal must not retain the previous traversal's seen markers.
        c.execute(
            "UPDATE file_instances SET last_scan=NULL WHERE last_scan=?", (scan_id,)
        )
    errors, seen = [], 0

    def renew(c):
        control = c.execute(
            "SELECT value FROM settings WHERE section='analyze'"
        ).fetchone()
        if control:
            import json

            state = json.loads(control[0])["state"]
            if state in ("paused", "cancelled"):
                raise InterruptedError(state)
        changed = c.execute(
            "UPDATE scan_runs SET expires=? WHERE id=? AND token=? AND state='running' AND expires>?",
            (time.time() + lease_seconds, scan_id, token, time.time()),
        ).rowcount
        if not changed:
            raise RuntimeError("Inventory lease expired; retry traversal")

    def walk_error(error):
        errors.append(str(error))

    def progress():
        with db.write() as c:
            renew(c)

    try:
        start = checked_path(run["root_path"], run["folder"])
        if not start.is_dir():
            raise OSError("Source folder unavailable")
        for directory, directories, files in os.walk(
            start, followlinks=False, onerror=walk_error
        ):
            allowed = []
            for name in directories:
                try:
                    if not is_link(Path(directory) / name):
                        allowed.append(name)
                except OSError as exc:
                    errors.append(str(exc))
            directories[:] = allowed
            for name in files:
                path = Path(directory) / name
                relative = path.relative_to(run["root_path"]).as_posix()
                size = mtime = digest = None
                error = None
                state = (
                    "available" if path.suffix.lower() in SUPPORTED else "unsupported"
                )
                with db.read() as c:
                    old = c.execute(
                        "SELECT * FROM file_instances WHERE root_id=? AND path=?",
                        (run["root_id"], relative),
                    ).fetchone()
                try:
                    if is_link(path):
                        state, error = "unsupported", "Symlink/reparse file skipped"
                    else:
                        info = checked_path(run["root_path"], relative).stat()
                        if not stat.S_ISREG(info.st_mode):
                            raise ValueError("Not a regular source file; skipped")
                        size, mtime = info.st_size, info.st_mtime_ns
                        if (
                            not run["verify"]
                            and old
                            and old["state"] == state
                            and old["size"] == size
                            and old["mtime_ns"] == mtime
                            and old["content_id"]
                        ):
                            digest = old["content_id"]
                        else:
                            digest, info = hash_source(
                                run["root_path"],
                                relative,
                                bytes_per_second=hash_mib_per_second * 1024 * 1024,
                                on_progress=progress,
                            )
                            size, mtime = info.st_size, info.st_mtime_ns
                except (OSError, ValueError) as exc:
                    state, error = "unreadable", str(exc)
                    # Do not reconcile missing files if a traversal lost access mid-scan.
                    errors.append(f"{relative}: {exc}")
                with db.write() as c:
                    renew(c)
                    if digest:
                        c.execute(
                            "INSERT OR IGNORE INTO contents VALUES (?,?,?)",
                            (digest, size, now()),
                        )
                    c.execute(
                        "INSERT INTO file_instances(root_id,path,size,mtime_ns,content_id,state,error,last_scan) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(root_id,path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,content_id=excluded.content_id,state=excluded.state,error=excluded.error,last_scan=excluded.last_scan",
                        (
                            run["root_id"],
                            relative,
                            size,
                            mtime,
                            digest,
                            state,
                            error,
                            scan_id,
                        ),
                    )
                    c.execute(
                        "UPDATE file_instances SET folder=? WHERE root_id=? AND path=?",
                        (
                            str(Path(relative).parent).replace("\\", "/"),
                            run["root_id"],
                            relative,
                        ),
                    )
                    instance = c.execute(
                        "SELECT id FROM file_instances WHERE root_id=? AND path=?",
                        (run["root_id"], relative),
                    ).fetchone()[0]
                    current = (digest, size, mtime, state)
                    if not old or current != (
                        old["content_id"],
                        old["size"],
                        old["mtime_ns"],
                        old["state"],
                    ):
                        c.execute(
                            "INSERT INTO file_versions(instance_id,content_id,size,mtime_ns,state,scan_id,at) VALUES (?,?,?,?,?,?,?)",
                            (instance, digest, size, mtime, state, scan_id, now()),
                        )
                        affected = (
                            ({digest, old["content_id"]} - {None}) if old else set()
                        )
                        for content_id in affected:
                            c.execute(
                                "UPDATE photos SET review_version=review_version+1 WHERE content_id=?",
                                (content_id,),
                            )
                        bump(c)
                    if digest and state == "available":
                        c.execute(
                            "INSERT OR IGNORE INTO photos(content_id,filename,format,folder,evidence) VALUES (?,?,?,?,?)",
                            (
                                digest,
                                name,
                                "JPEG"
                                if path.suffix.lower() in (".jpg", ".jpeg")
                                else path.suffix[1:].upper(),
                                str(Path(relative).parent).replace("\\", "/"),
                                packed(
                                    [
                                        {
                                            "source": "rule",
                                            "text": "Inventoried only; protection checks not run",
                                            "strength": "not_checked",
                                        }
                                    ]
                                ),
                            ),
                        )
                        c.execute(
                            "INSERT OR IGNORE INTO group_assignments(content_id) VALUES (?)",
                            (digest,),
                        )
                        c.execute(
                            "INSERT OR IGNORE INTO decisions(content_id) VALUES (?)",
                            (digest,),
                        )
                        spec = c.execute(
                            "SELECT fingerprint FROM stage_specs WHERE stage='prepare' AND active=1"
                        ).fetchone()
                        c.execute(
                            "INSERT OR IGNORE INTO stage_jobs(id,content_id,stage,fingerprint) VALUES (?,?,?,?)",
                            (
                                uid(),
                                digest,
                                "prepare",
                                spec[0] if spec else "prepare-contract-v1:unconfigured",
                            ),
                        )
                seen += 1
        # Probe reachability again, even for an empty source, before reconciliation.
        with os.scandir(checked_path(run["root_path"], run["folder"])) as entries:
            next(entries, None)
        with db.write() as c:
            renew(c)
            if not errors:
                candidates = c.execute(
                    "SELECT * FROM file_instances WHERE root_id=? AND state!='missing' AND (last_scan IS NULL OR last_scan!=?)",
                    (run["root_id"], scan_id),
                ).fetchall()
                for item in candidates:
                    if run["folder"] and not item["path"].startswith(
                        run["folder"] + "/"
                    ):
                        continue
                    c.execute(
                        "UPDATE file_instances SET state='missing' WHERE id=?",
                        (item["id"],),
                    )
                    c.execute(
                        "UPDATE photos SET review_version=review_version+1 WHERE content_id=?",
                        (item["content_id"],),
                    )
                    c.execute(
                        "INSERT INTO file_versions(instance_id,content_id,size,mtime_ns,state,scan_id,at) VALUES (?,?,?,?,?,?,?)",
                        (
                            item["id"],
                            item["content_id"],
                            item["size"],
                            item["mtime_ns"],
                            "missing",
                            scan_id,
                            now(),
                        ),
                    )
                    bump(c)
            c.execute(
                "UPDATE scan_runs SET state=?,finished_at=?,error=?,seen=?,expires=NULL WHERE id=? AND token=?",
                (
                    "partial" if errors else "done",
                    now(),
                    "\n".join(errors) or None,
                    seen,
                    scan_id,
                    token,
                ),
            )
    except InterruptedError as exc:
        with db.write() as c:
            c.execute(
                "UPDATE scan_runs SET state=?,error=?,expires=NULL WHERE id=? AND token=?",
                (
                    str(exc),
                    "Inventory interrupted by owner; no missing reconciliation",
                    scan_id,
                    token,
                ),
            )
    except (OSError, ValueError, RuntimeError) as exc:
        with db.write() as c:
            c.execute(
                "UPDATE scan_runs SET state='unavailable',finished_at=?,error=?,seen=?,expires=NULL WHERE id=? AND token=?",
                (now(), str(exc), seen, scan_id, token),
            )
    return True
