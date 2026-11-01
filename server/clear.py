"""Start the library over. Originals, exported copies and the owner's setup are never touched."""

import shutil
import sqlite3

from fastapi import HTTPException

from server.db import bump

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


def clear(db):
    with db.write() as c:
        for query, message in BUSY:
            if c.execute(query).fetchone():
                raise HTTPException(409, message)
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
