"""Durable dispatch primitives. No inference adapters or assumed D3 payloads."""

import json
import time

from fastapi import HTTPException

from server.db import packed, uid

# Zima's own workers. They must never make the laptop look online.
LOCAL_OWNER = "zima-"


def note_contact(c, owner, stage, fingerprint):
    if owner.startswith(LOCAL_OWNER):
        return
    c.execute(
        "INSERT INTO agent_contacts VALUES (?,?,?,?) ON CONFLICT(owner) DO UPDATE SET last_contact=excluded.last_contact,stage=excluded.stage,fingerprint=excluded.fingerprint",
        (owner, time.time(), stage, fingerprint),
    )


def claim(c, request):
    if request.fingerprint.endswith(":unconfigured"):
        raise HTTPException(409, "Stage adapter is not configured for this fingerprint")
    note_contact(c, request.owner, request.stage, request.fingerprint)
    # Pause also holds work queued after it, such as by turning on a model.
    control = c.execute("SELECT value FROM settings WHERE section='analyze'").fetchone()
    if control and json.loads(control[0])["state"] != "running":
        return None
    row = c.execute(
        "SELECT * FROM stage_jobs WHERE stage=? AND fingerprint=? AND (state='queued' OR state='running' AND expires<?) ORDER BY id LIMIT 1",
        (request.stage, request.fingerprint, time.time()),
    ).fetchone()
    if not row:
        return None
    source = c.execute(
        "SELECT f.*,r.path AS root_path FROM file_instances f JOIN roots r ON r.id=f.root_id WHERE content_id=? AND state='available' ORDER BY id LIMIT 1",
        (row["content_id"],),
    ).fetchone()
    if not source:
        c.execute(
            "UPDATE stage_jobs SET state='paused',error='Source unavailable' WHERE id=?",
            (row["id"],),
        )
        return None
    token, expiry = uid(), time.time() + request.leaseSeconds
    c.execute(
        "UPDATE stage_jobs SET state='running',owner=?,token=?,expires=?,attempt=attempt+1,error=NULL WHERE id=?",
        (request.owner, token, expiry, row["id"]),
    )
    c.execute(
        "UPDATE stage_jobs SET source_instance=?,source_size=?,source_mtime_ns=? WHERE id=?",
        (source["id"], source["size"], source["mtime_ns"], row["id"]),
    )
    note_contact(c, request.owner, request.stage, request.fingerprint)
    return dict(
        jobId=row["id"],
        stage=row["stage"],
        fingerprint=row["fingerprint"],
        sha256=row["content_id"],
        rootId=source["root_id"],
        relativePath=source["path"],
        size=source["size"],
        mtimeNs=source["mtime_ns"],
        owner=request.owner,
        token=token,
        expires=expiry,
        attempt=row["attempt"] + 1,
    )


def valid_lease(c, job_id, request):
    row = c.execute("SELECT * FROM stage_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown stage job")
    if (
        row["state"] != "running"
        or row["owner"] != request.owner
        or row["token"] != request.token
        or row["attempt"] != request.attempt
        or row["expires"] <= time.time()
    ):
        raise HTTPException(409, "Expired or superseded lease")
    return row


def renew(c, job_id, request):
    job = valid_lease(c, job_id, request)
    note_contact(c, request.owner, job["stage"], job["fingerprint"])
    expiry = time.time() + 120
    c.execute("UPDATE stage_jobs SET expires=? WHERE id=?", (expiry, job_id))
    return {"expires": expiry}


def release(c, job_id, request):
    # The laptop could not run this job right now; that says nothing about the photo.
    valid_lease(c, job_id, request)
    c.execute(
        "UPDATE stage_jobs SET state='queued',token=NULL,expires=NULL,error=? WHERE id=?",
        (request.reason, job_id),
    )
    return {"state": "queued"}


def fail(c, job_id, request):
    valid_lease(c, job_id, request)
    c.execute(
        "UPDATE stage_jobs SET state=?,error=?,expires=NULL WHERE id=?",
        (request.state, request.reason, job_id),
    )
    return {"state": request.state}


def submit(c, job_id, request):
    accepted = c.execute(
        "SELECT * FROM stage_results WHERE job_id=?", (job_id,)
    ).fetchone()
    if accepted and accepted["token"] == request.token:
        if (
            accepted["fingerprint"] != request.fingerprint
            or accepted["content_id"] != request.sha256
            or accepted["payload"] != packed(request.payload)
        ):
            raise HTTPException(409, "Conflicting duplicate submission")
        import json

        return json.loads(accepted["ack"])
    row = valid_lease(c, job_id, request)
    if row["content_id"] != request.sha256 or row["fingerprint"] != request.fingerprint:
        raise HTTPException(409, "Stage fingerprint or source hash mismatch")
    if not c.execute(
        "SELECT 1 FROM file_instances WHERE content_id=? AND state='available'",
        (row["content_id"],),
    ).fetchone():
        raise HTTPException(409, "Input superseded; inventory refresh required")
    # Stage-specific schemas are deliberately not invented ahead of the preparation/D3 phases.
    raise HTTPException(
        501, "Stage result adapter is not implemented in Phase 1; no result accepted"
    )
