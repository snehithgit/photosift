import json

from fastapi import HTTPException

from server.db import action, audit, bump, fingerprint, now, packed, revision, uid
from server.queries import detail, matched, query_fingerprint


def freeze(c, request):
    if request.queryRevision != revision(c):
        raise HTTPException(409, "Query changed; reload before selecting")
    if request.queryFingerprint != query_fingerprint(
        request.query, request.queryRevision
    ):
        raise HTTPException(
            409, "Selection does not match the loaded scope and filters"
        )
    rows = matched(c, request.query)
    exclusions = set(request.exclusions)
    requested = set(request.contentIds) if request.contentIds is not None else None
    matching = {r["content_id"] for r in rows}
    if requested is not None and not requested <= matching:
        raise HTTPException(
            409, "Explicit selection contains photos outside this query"
        )
    if not exclusions <= matching:
        raise HTTPException(409, "Exclusions do not belong to this query")
    protected = sum(
        r["is_protected"]
        for r in rows
        if requested is None or r["content_id"] in requested
    )
    selected = [
        r
        for r in rows
        if (requested is None or r["content_id"] in requested)
        and r["content_id"] not in exclusions
        and (request.includeProtected or not r["is_protected"])
    ]
    identifier = uid()
    scope = request.query.model_dump()
    policy = c.execute("SELECT value FROM meta WHERE key='policyRevision'").fetchone()[
        0
    ]
    digest = fingerprint(
        {
            "query": scope,
            "members": [(r["content_id"], r["review_version"]) for r in selected],
            "policy": policy,
        }
    )
    c.execute(
        "INSERT INTO selections VALUES (?,?,?,?,?,?,?,?,?)",
        (
            identifier,
            packed(scope),
            digest,
            revision(c),
            policy,
            request.includeProtected,
            len(exclusions),
            protected,
            now(),
        ),
    )
    c.executemany(
        "INSERT INTO selection_items VALUES (?,?,?)",
        [(identifier, r["content_id"], r["review_version"]) for r in selected],
    )
    return {
        "selectionId": identifier,
        "count": len(selected),
        "excluded": len(exclusions),
        "protected": protected,
        "fingerprint": digest,
        "revision": revision(c),
    }


def enqueue_bulk(c, request):
    existing = c.execute(
        "SELECT * FROM bulk_jobs WHERE selection_id=?", (request.selectionId,)
    ).fetchone()
    if existing:
        if existing["decision"] != request.decision:
            raise HTTPException(409, "This frozen selection already has another action")
        return job(c, existing["id"])
    selection = c.execute(
        "SELECT * FROM selections WHERE id=?", (request.selectionId,)
    ).fetchone()
    if not selection:
        raise HTTPException(404, "Unknown frozen selection")
    identifier = uid()
    action_id = action(c, f"Set decision: {request.decision}", selection["query"])
    c.execute(
        "INSERT INTO bulk_jobs(id,selection_id,decision,action_id,created_at) VALUES (?,?,?,?,?)",
        (identifier, request.selectionId, request.decision, action_id, now()),
    )
    c.execute(
        "INSERT INTO bulk_items(job_id,content_id,expected_version) SELECT ?,content_id,version FROM selection_items WHERE selection_id=?",
        (identifier, request.selectionId),
    )
    return job(c, identifier)


def job(c, identifier):
    row = c.execute("SELECT * FROM bulk_jobs WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown bulk job")
    counts = {
        r["state"]: r["n"]
        for r in c.execute(
            "SELECT state,COUNT(*) n FROM bulk_items WHERE job_id=? GROUP BY state",
            (identifier,),
        )
    }
    conflicts = [
        dict(r)
        for r in c.execute(
            "SELECT content_id AS contentId,reason FROM bulk_items WHERE job_id=? AND state='conflict' ORDER BY content_id LIMIT 100",
            (identifier,),
        )
    ]
    return {
        **dict(row),
        "total": sum(counts.values()),
        "progress": sum(counts.values()) - counts.get("pending", 0),
        "applied": counts.get("applied", 0),
        "unchanged": counts.get("unchanged", 0),
        "conflicts": counts.get("conflict", 0),
        "conflictItems": conflicts,
        "actionId": row["action_id"],
    }


def set_decision(c, content_id, decision, action_id):
    current = c.execute(
        "SELECT * FROM decisions WHERE content_id=?", (content_id,)
    ).fetchone()
    if current["decision"] == decision:
        return False
    c.execute(
        "UPDATE decisions SET decision=?,version=version+1 WHERE content_id=?",
        (decision, content_id),
    )
    c.execute(
        "UPDATE photos SET review_version=review_version+1 WHERE content_id=?",
        (content_id,),
    )
    version = c.execute(
        "SELECT review_version FROM photos WHERE content_id=?", (content_id,)
    ).fetchone()[0]
    audit(c, action_id, content_id, "decision", current["decision"], decision, version)
    bump(c)
    return True


def process_bulk(db, batch_size=100):
    # One batch and its per-item outcomes commit atomically. Multiple Zima processes serialize
    # only this bounded DB transaction; a crash retries only pending items.
    with db.write() as c:
        row = c.execute(
            "SELECT b.*,s.policy_revision,s.include_protected FROM bulk_jobs b JOIN selections s ON s.id=b.selection_id WHERE b.state IN ('queued','running') ORDER BY b.created_at LIMIT 1"
        ).fetchone()
        if not row:
            return False
        c.execute("UPDATE bulk_jobs SET state='running' WHERE id=?", (row["id"],))
        pending = c.execute(
            "SELECT * FROM bulk_items WHERE job_id=? AND state='pending' ORDER BY content_id LIMIT ?",
            (row["id"], batch_size),
        ).fetchall()
        policy = c.execute(
            "SELECT value FROM meta WHERE key='policyRevision'"
        ).fetchone()[0]
        for item in pending:
            p = detail(c, item["content_id"])
            reason = None
            if policy != row["policy_revision"]:
                reason = "Protection policy changed after selection"
            elif p["reviewVersion"] != item["expected_version"]:
                reason = "Photo changed after selection"
            elif p["protected"] and not row["include_protected"]:
                reason = "Photo is now protected"
            if reason:
                state = "conflict"
            else:
                state = (
                    "applied"
                    if set_decision(
                        c, item["content_id"], row["decision"], row["action_id"]
                    )
                    else "unchanged"
                )
            c.execute(
                "UPDATE bulk_items SET state=?,reason=? WHERE job_id=? AND content_id=?",
                (state, reason, row["id"], item["content_id"]),
            )
        if not c.execute(
            "SELECT 1 FROM bulk_items WHERE job_id=? AND state='pending' LIMIT 1",
            (row["id"],),
        ).fetchone():
            c.execute("UPDATE bulk_jobs SET state='done' WHERE id=?", (row["id"],))
    return True


def patch_photo(c, content_id, request):
    p = detail(c, content_id)
    if p["reviewVersion"] != request.expectedVersion:
        raise HTTPException(409, "Changed elsewhere; reload photo")
    if (
        request.decision == "clutter"
        and p["protected"]
        and not request.overrideProtected
    ):
        raise HTTPException(409, "Explicit protected-photo override required")
    action_id = action(c, "Review correction", content_id)
    if request.decision is not None:
        set_decision(c, content_id, request.decision, action_id)
    elif request.groupId is not None:
        if request.groupId != p["groupId"]:
            c.execute(
                "UPDATE photos SET duplicate_set=NULL WHERE content_id=?", (content_id,)
            )
        before = c.execute(
            "SELECT manual_group FROM group_assignments WHERE content_id=?",
            (content_id,),
        ).fetchone()[0]
        c.execute(
            "UPDATE group_assignments SET manual_group=? WHERE content_id=?",
            (request.groupId, content_id),
        )
        c.execute(
            "UPDATE photos SET review_version=review_version+1 WHERE content_id=?",
            (content_id,),
        )
        audit(
            c,
            action_id,
            content_id,
            "group",
            before,
            request.groupId,
            p["reviewVersion"] + 1,
        )
        bump(c)
    elif request.removeFromEvent:
        before = p.get("eventId")
        c.execute("DELETE FROM event_members WHERE content_id=?", (content_id,))
        c.execute(
            "UPDATE photos SET review_version=review_version+1 WHERE content_id=?",
            (content_id,),
        )
        if before:
            c.execute("UPDATE events SET version=version+1 WHERE id=?", (before,))
        audit(c, action_id, content_id, "event", before, None, p["reviewVersion"] + 1)
        bump(c)
    return {"photo": detail(c, content_id), "actionId": action_id}


def undo(c, identifier):
    if not c.execute("SELECT 1 FROM actions WHERE id=?", (identifier,)).fetchone():
        raise HTTPException(404, "Unknown action")
    if c.execute("SELECT 1 FROM actions WHERE undo_of=?", (identifier,)).fetchone():
        raise HTTPException(409, "Action already reversed")
    if c.execute(
        "SELECT 1 FROM bulk_jobs WHERE action_id=? AND state!='done'", (identifier,)
    ).fetchone():
        raise HTTPException(409, "Wait for the bulk job to finish")
    if c.execute(
        "SELECT 1 FROM undo_jobs WHERE action_id=? AND state!='done'", (identifier,)
    ).fetchone():
        raise HTTPException(409, "Wait for the undo job to finish")
    count = c.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action_id=? AND kind IN ('decision','group','event')",
        (identifier,),
    ).fetchone()[0]
    if not count:
        raise HTTPException(409, "This action has no reversible photo changes")
    undo_id = action(c, "Undo review action", identifier, identifier)
    job_id = uid()
    c.execute(
        "INSERT INTO undo_jobs(id,action_id,original_action,created_at) VALUES (?,?,?,?)",
        (job_id, undo_id, identifier, now()),
    )
    c.execute(
        "INSERT INTO undo_items(job_id,audit_id) SELECT ?,id FROM audit_log WHERE action_id=? AND kind IN ('decision','group','event')",
        (job_id, identifier),
    )
    if count <= 100:
        apply_undo_batch(c, job_id, 100)
    return undo_job(c, job_id)


def undo_job(c, identifier):
    row = c.execute("SELECT * FROM undo_jobs WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown undo job")
    counts = {
        r["state"]: r["n"]
        for r in c.execute(
            "SELECT state,COUNT(*) n FROM undo_items WHERE job_id=? GROUP BY state",
            (identifier,),
        )
    }
    conflicts = [
        r[0]
        for r in c.execute(
            "SELECT a.content_id FROM undo_items i JOIN audit_log a ON a.id=i.audit_id WHERE i.job_id=? AND i.state='conflict' ORDER BY a.content_id LIMIT 100",
            (identifier,),
        )
    ]
    return {
        **dict(row),
        "actionId": row["action_id"],
        "total": sum(counts.values()),
        "applied": counts.get("applied", 0),
        "conflicts": counts.get("conflict", 0),
        "conflictIds": conflicts,
        "progress": sum(counts.values()) - counts.get("pending", 0),
        "kind": "undo",
    }


def apply_undo_batch(c, job_id, batch_size):
    job = c.execute("SELECT * FROM undo_jobs WHERE id=?", (job_id,)).fetchone()
    undo_id = job["action_id"]
    c.execute("UPDATE undo_jobs SET state='running' WHERE id=?", (job_id,))
    changes = c.execute(
        "SELECT a.* FROM undo_items i JOIN audit_log a ON a.id=i.audit_id WHERE i.job_id=? AND i.state='pending' ORDER BY a.id DESC LIMIT ?",
        (job_id, batch_size),
    ).fetchall()
    for row in changes:
        p = detail(c, row["content_id"])
        if p["reviewVersion"] != row["applied_version"]:
            c.execute(
                "UPDATE undo_items SET state='conflict',reason='Photo changed after original action' WHERE job_id=? AND audit_id=?",
                (job_id, row["id"]),
            )
            continue
        before = json.loads(row["before_json"])
        if row["kind"] == "decision":
            set_decision(c, row["content_id"], before, undo_id)
        else:
            if row["kind"] == "group":
                c.execute(
                    "UPDATE group_assignments SET manual_group=? WHERE content_id=?",
                    (before, row["content_id"]),
                )
            else:
                if before:
                    event = c.execute(
                        "SELECT archived FROM events WHERE id=?", (before,)
                    ).fetchone()
                    if not event or event["archived"]:
                        c.execute(
                            "UPDATE undo_items SET state='conflict',reason='Original event is no longer active' WHERE job_id=? AND audit_id=?",
                            (job_id, row["id"]),
                        )
                        continue
                    current_event = c.execute(
                        "SELECT event_id FROM event_members WHERE content_id=?",
                        (row["content_id"],),
                    ).fetchone()
                    if current_event:
                        c.execute(
                            "UPDATE events SET version=version+1 WHERE id=?",
                            (current_event[0],),
                        )
                    c.execute(
                        "INSERT INTO event_members(content_id,event_id) VALUES (?,?) ON CONFLICT(content_id) DO UPDATE SET event_id=excluded.event_id,source='manual'",
                        (row["content_id"], before),
                    )
                    c.execute(
                        "UPDATE events SET version=version+1 WHERE id=?", (before,)
                    )
                else:
                    current_event = c.execute(
                        "SELECT event_id FROM event_members WHERE content_id=?",
                        (row["content_id"],),
                    ).fetchone()
                    if current_event:
                        c.execute(
                            "UPDATE events SET version=version+1 WHERE id=?",
                            (current_event[0],),
                        )
                    c.execute(
                        "DELETE FROM event_members WHERE content_id=?",
                        (row["content_id"],),
                    )
            c.execute(
                "UPDATE photos SET review_version=review_version+1 WHERE content_id=?",
                (row["content_id"],),
            )
            audit(
                c,
                undo_id,
                row["content_id"],
                row["kind"],
                json.loads(row["after_json"]),
                before,
                p["reviewVersion"] + 1,
            )
            bump(c)
        c.execute(
            "UPDATE undo_items SET state='applied' WHERE job_id=? AND audit_id=?",
            (job_id, row["id"]),
        )
    if not c.execute(
        "SELECT 1 FROM undo_items WHERE job_id=? AND state='pending' LIMIT 1", (job_id,)
    ).fetchone():
        c.execute("UPDATE undo_jobs SET state='done' WHERE id=?", (job_id,))


def process_undo(db, batch_size=100):
    with db.write() as c:
        row = c.execute(
            "SELECT id FROM undo_jobs WHERE state IN ('queued','running') ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            return False
        apply_undo_batch(c, row[0], batch_size)
        return True
