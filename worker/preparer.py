"""Prepare photos on Zima when that is the active contract. It needs no GPU, and the library is
already here, so nothing crosses the network. The laptop keeps the GPU stages."""

import os
import threading
import time
from pathlib import Path

from fastapi import HTTPException

from agent.prepare import configuration, prepare
from server import leases, pipeline
from server.contracts import ClaimInput, FailureInput, ReleaseInput, ResultInput
from server.db import fingerprint

# A lease only guards against a stopped worker, so it can be long enough for the largest photo.
LEASE_SECONDS = 600
IDLE_SECONDS = 2
UNMOUNTED = "The library folder is not reachable on Zima"


def thread_count():
    """Leave a core for the web app and scans, unless the owner set a number."""
    setting = os.environ.get("PHOTOSIFT_PREPARE_THREADS")
    if setting:
        return max(0, int(setting))
    return min(3, max(1, (os.cpu_count() or 2) - 1))


def prepare_next(db, config, digest, owner):
    """Prepare one photo. False means there was nothing to do here right now."""
    with db.read() as c:
        if not c.execute(
            "SELECT 1 FROM stage_specs WHERE stage='prepare' AND active=1 AND fingerprint=?",
            (digest,),
        ).fetchone():
            return False
    with db.write() as c:
        claim = leases.claim(
            c,
            ClaimInput(
                owner=owner,
                stage="prepare",
                fingerprint=digest,
                leaseSeconds=LEASE_SECONDS,
            ),
        )
        if not claim:
            return False
        root = Path(
            c.execute(
                "SELECT path FROM roots WHERE id=?", (claim["rootId"],)
            ).fetchone()[0]
        )
    lease = {key: claim[key] for key in ("owner", "token", "attempt")}
    if not root.is_dir():
        # An unmounted disk says nothing about the photo, so it goes back in the queue.
        with db.write() as c:
            leases.release(c, claim["jobId"], ReleaseInput(**lease, reason=UNMOUNTED))
        print(UNMOUNTED, flush=True)
        return False
    try:
        pipeline.submit_preparation(
            db,
            claim["jobId"],
            ResultInput(
                **lease,
                fingerprint=digest,
                sha256=claim["sha256"],
                payload=prepare(root, claim, config).model_dump(),
            ),
        )
    except Exception as exc:
        reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
        failure = FailureInput(
            **lease,
            state="failed",
            reason=str(reason)[:1000] or type(exc).__name__,
        )
        try:
            with db.write() as c:
                leases.fail(c, claim["jobId"], failure)
        except HTTPException:
            pass  # The job was superseded or expired; it isn't this photo's failure.
    return True


def run(db, config, digest, owner):
    # Other apps on the machine come first; this work can wait a moment.
    if hasattr(os, "nice"):
        os.nice(5)
    while True:
        try:
            if not prepare_next(db, config, digest, owner):
                time.sleep(IDLE_SECONDS)
        except Exception as exc:
            print("Preparation waiting:", str(exc)[:200], flush=True)
            time.sleep(5)


def start(db):
    config = configuration()
    digest = fingerprint(config.model_dump())
    for number in range(thread_count()):
        threading.Thread(
            target=run,
            args=(db, config, digest, f"{leases.LOCAL_OWNER}prepare-{number}"),
            daemon=True,
        ).start()
