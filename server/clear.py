"""Start the library over. Originals, exported copies and the owner's setup are never touched."""

import json
import shutil
import sqlite3

from fastapi import HTTPException

from server.db import bump
from server.pipeline import SLIM_PAYLOAD

# What the library holds now: rebuilt by the next scan and the laptop.
ERASED = (
    "actions",
    "audit_log",
    "bulk_items",
    "bulk_jobs",
    "cached_media",
    "content_scores",
    "contents",
    "decisions",
    "embeddings",
    "event_edits",
    "event_members",
    "events",
    "export_entries",
    "export_journal",
    "export_metadata",
    "export_operations",
    "export_plans",
    "export_runs",
    "file_instances",
    "file_versions",
    "group_assignments",
    "photos",
    "preparation_outputs",
    "query_vectors",
    "scan_runs",
    "scoring_runs",
    "selection_items",
    "selections",
    "stage_jobs",
    "stage_results",
    "suggestion_runs",
    "undo_items",
    "undo_jobs",
)
# The owner's setup, so nothing has to be taught or configured again. Labels and reports refer to
# photos by content hash, so they still match the same photos after a rescan.
KEPT = (
    "agent_commands",
    "agent_contacts",
    "comparison_samples",
    "embedding_indexes",
    "evaluations",
    "meta",
    "prompt_packs",
    "roots",
    "schema_migrations",
    "settings",
    "stage_specs",
)
BUSY = (
    (
        "SELECT 1 FROM scan_runs WHERE state IN ('queued','running')",
        "A scan is running; pause or wait for it first",
    ),
    (
        "SELECT 1 FROM bulk_jobs WHERE state IN ('queued','running') UNION ALL SELECT 1 FROM undo_jobs WHERE state IN ('queued','running')",
        "A bulk action is running; wait for it first",
    ),
    (
        "SELECT 1 FROM export_runs WHERE state IN ('queued','running','rollback-requested','rolling-back')",
        "An export is running; pause or wait for it first",
    ),
)


def refuse_while_busy(c):
    for query, message in BUSY:
        if c.execute(query).fetchone():
            raise HTTPException(409, message)


def size_on_disk(db):
    files = (db.path, db.path.with_name(db.path.name + "-wal"))
    return sum(path.stat().st_size for path in files if path.exists())


def space(db):
    with db.read() as c:
        page = c.execute("PRAGMA page_size").fetchone()[0]
        free = c.execute("PRAGMA freelist_count").fetchone()[0]
        slimming = c.execute(
            "SELECT 1 FROM stage_results WHERE stage='prepare' AND length(payload)>? LIMIT 1",
            (SLIM_PAYLOAD,),
        ).fetchone()
    return {
        "bytes": size_on_disk(db),
        "freeBytes": free * page,
        "slimming": bool(slimming),
    }


def compact(db):
    """Give room inside the database back to the disk. Nothing in it changes. It holds up every
    write while it runs, so it waits for a pause: a laptop result refused partway could fail a
    good photo."""
    with db.read() as c:
        control = c.execute(
            "SELECT value FROM settings WHERE section='analyze'"
        ).fetchone()
        if not control or json.loads(control[0])["state"] == "running":
            raise HTTPException(409, "Pause Analyze first, then try again")
        refuse_while_busy(c)
    if space(db)["slimming"]:
        raise HTTPException(
            409,
            "Duplicate thumbnails are still being removed; try again when that finishes",
        )
    before = size_on_disk(db)
    connection = db.connect()
    try:
        connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            409, f"Couldn't free the space, and nothing was changed: {exc}"
        ) from exc
    finally:
        connection.close()
    return {"before": before, "after": size_on_disk(db)}


def clear(db):
    with db.write() as c:
        refuse_while_busy(c)
        c.execute("PRAGMA defer_foreign_keys=ON")
        for table in ERASED:
            c.execute(f"DELETE FROM {table}")
        # A pause applied to the work just erased; it must not hold back the next scan.
        c.execute("DELETE FROM settings WHERE section='analyze'")
        bump(c)
    shutil.rmtree(db.path.parent / "cache", ignore_errors=True)
    # Give the disk space back. Harmless to skip if something is reading right now.
    connection = db.connect()
    try:
        connection.execute("VACUUM")
        reclaimed = True
    except sqlite3.OperationalError:
        reclaimed = False
    finally:
        connection.close()
    return {"cleared": True, "spaceReclaimed": reclaimed}
