"""Validated per-image preparation acceptance; cache IO stays outside write locks."""

import base64
import functools
import hashlib
import io
import json
import os

from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError

from server import assessments, embeddings, leases
from server.db import bump, fingerprint, now, packed, uid
from server.paths import checked_path
from server.preparation_contract import PreparationConfig, PreparationResult


def configure_preparation(c, config: PreparationConfig):
    value = config.model_dump()
    digest = fingerprint(value)
    c.execute(
        "UPDATE photos SET protection=json_insert(protection,'$[#]','faces') WHERE json_array_length(faces)>0 AND NOT EXISTS(SELECT 1 FROM json_each(photos.protection) WHERE value='faces')"
    )
    previous = c.execute(
        "SELECT fingerprint FROM stage_specs WHERE stage='prepare' AND active=1"
    ).fetchone()
    c.execute("UPDATE stage_specs SET active=0 WHERE stage='prepare'")
    c.execute(
        "INSERT INTO stage_specs VALUES (?,'prepare',?,1,?) ON CONFLICT(fingerprint) DO UPDATE SET active=1",
        (digest, packed(value), now()),
    )
    c.execute(
        "UPDATE stage_jobs SET state='skipped',token=NULL,expires=NULL,error='Preparation contract superseded' WHERE stage='prepare' AND fingerprint!=? AND state IN ('queued','running','paused')",
        (digest,),
    )
    # Back to a contract used before: what the switch away set aside is owed again.
    queued = c.execute(
        "UPDATE stage_jobs SET state='queued',error=NULL WHERE stage='prepare' AND fingerprint=? AND state='skipped' AND error='Preparation contract superseded'",
        (digest,),
    ).rowcount
    for row in c.execute(
        "SELECT DISTINCT p.content_id FROM photos p JOIN file_instances f ON f.content_id=p.content_id WHERE f.state='available'"
    ).fetchall():
        queued += c.execute(
            "INSERT OR IGNORE INTO stage_jobs(id,content_id,stage,fingerprint) VALUES (?,?,'prepare',?)",
            (uid(), row[0], digest),
        ).rowcount
    if not previous or previous[0] != digest:
        c.execute(
            "UPDATE photos SET faces=CASE WHEN json_array_length(faces)>0 THEN faces ELSE '\"not_checked\"' END,triage='needs_review',review_version=review_version+1 WHERE NOT EXISTS(SELECT 1 FROM preparation_outputs p WHERE p.content_id=photos.content_id AND p.fingerprint=?)",
            (digest,),
        )
        for row in c.execute(
            "SELECT content_id,payload FROM preparation_outputs WHERE fingerprint=?",
            (digest,),
        ).fetchall():
            data = json.loads(row["payload"])
            faces = data["faces"] if data["faceStatus"] == "done" else "not_checked"
            evidence = [
                {
                    "source": "rule",
                    "text": "Cached preparation reactivated; importance and sensitivity unresolved",
                    "strength": "not_checked",
                }
            ]
            c.execute(
                "UPDATE photos SET width=?,height=?,format=?,capture_date=?,capture_source=?,camera=?,gps=?,faces=?,evidence=?,triage='needs_review',review_version=review_version+1 WHERE content_id=?",
                (
                    data["width"],
                    data["height"],
                    data["format"],
                    data["captureDate"],
                    data["captureDateSource"],
                    data["camera"],
                    packed(data["gps"]) if data["gps"] else None,
                    packed(faces),
                    packed(evidence),
                    row["content_id"],
                ),
            )
        bump(c)
    return {"fingerprint": digest, "queued": queued, "config": value}


@functools.lru_cache(maxsize=1)
def local_preparation():
    """The contract this machine would prepare under, or None where OpenCV isn't installed."""
    try:
        from agent.prepare import configuration
    except ImportError:
        return None
    return configuration()


def preparation_runner(c):
    """The worker beside this server prepares exactly when the active contract is its own."""
    active = c.execute(
        "SELECT fingerprint FROM stage_specs WHERE stage='prepare' AND active=1"
    ).fetchone()
    if not active:
        return None
    config = local_preparation()
    here = config and fingerprint(config.model_dump()) == active[0]
    return "server" if here else "laptop"


def store_media(db, content_id, digest, result, config):
    records = []
    for kind, encoded, edge, max_bytes in [
        ("thumb", result.thumbnail, config.thumbnailEdge, 2_000_000),
        ("preview", result.preview, config.previewEdge, 8_000_000),
    ]:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > max_bytes:
            raise ValueError("Cache artifact exceeds byte bound")
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "WEBP" or max(image.size) > edge or min(image.size) < 1:
                raise ValueError("Expected a bounded WebP artifact")
            image.verify()
        sha = hashlib.sha256(data).hexdigest()
        directory = db.path.parent / "cache" / content_id / digest
        directory.mkdir(parents=True, exist_ok=True)
        checked_path(directory)
        path = directory / (kind + "-" + sha + ".webp")
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError("Existing cache artifact was changed")
        else:
            temporary = directory / ("." + uid() + ".pending")
            try:
                with temporary.open("xb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        records.append((uid(), content_id, digest, kind, str(path), len(data), sha))
    return records


def submit_preparation(db, identifier, request):
    with db.read() as c:
        accepted = c.execute(
            "SELECT * FROM stage_results WHERE job_id=?", (identifier,)
        ).fetchone()
        job = c.execute("SELECT * FROM stage_jobs WHERE id=?", (identifier,)).fetchone()
        if accepted:
            if (
                not job
                or job["owner"] != request.owner
                or job["attempt"] != request.attempt
                or accepted["token"] != request.token
                or accepted["fingerprint"] != request.fingerprint
                or accepted["content_id"] != request.sha256
                or accepted["payload"] != packed(request.payload)
            ):
                raise HTTPException(409, "Conflicting duplicate result")
            return json.loads(accepted["ack"])
        job = leases.valid_lease(c, identifier, request)
        spec = c.execute(
            "SELECT * FROM stage_specs WHERE fingerprint=? AND stage='prepare' AND active=1",
            (request.fingerprint,),
        ).fetchone()
        if job["stage"] != "prepare" or not spec:
            raise HTTPException(
                501, "No validated adapter is configured for this stage fingerprint"
            )
        if (
            job["content_id"] != request.sha256
            or job["fingerprint"] != request.fingerprint
        ):
            raise HTTPException(409, "Input hash or fingerprint mismatch")
        config = PreparationConfig.model_validate_json(spec["config"])
    try:
        result = PreparationResult.model_validate(request.payload)
        if result.width * result.height > config.maxPixels:
            raise ValueError("Decoded dimensions exceed configured bound")
        if result.faceStatus == "done" and config.faceModelSha256 is None:
            raise ValueError("Face results require a fingerprinted detector")
        artifacts = store_media(db, request.sha256, request.fingerprint, result, config)
    except (ValueError, ValidationError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    with db.write() as c:
        job = leases.valid_lease(c, identifier, request)
        if not c.execute(
            "SELECT 1 FROM stage_specs WHERE fingerprint=? AND active=1",
            (request.fingerprint,),
        ).fetchone():
            raise HTTPException(409, "Preparation configuration superseded")
        source = c.execute(
            "SELECT * FROM file_instances WHERE id=?", (job["source_instance"],)
        ).fetchone()
        if (
            not source
            or source["state"] != "available"
            or (source["content_id"], source["size"], source["mtime_ns"])
            != (request.sha256, job["source_size"], job["source_mtime_ns"])
        ):
            raise HTTPException(409, "Claimed source was superseded; rescan required")
        for artifact in artifacts:
            c.execute(
                "INSERT OR IGNORE INTO cached_media VALUES (?,?,?,?,?,?,?)", artifact
            )
        c.execute(
            "UPDATE photos SET protection=json_insert(protection,'$[#]','faces') WHERE content_id=? AND json_array_length(faces)>0 AND NOT EXISTS(SELECT 1 FROM json_each(photos.protection) WHERE value='faces')",
            (request.sha256,),
        )
        metadata = result.model_dump(exclude={"thumbnail", "preview"})
        c.execute(
            "INSERT INTO preparation_outputs VALUES (?,?,?,?) ON CONFLICT(content_id,fingerprint) DO UPDATE SET payload=excluded.payload,created_at=excluded.created_at",
            (request.sha256, request.fingerprint, packed(metadata), now()),
        )
        evidence = [
            {
                "source": "rule",
                "text": "Oriented decode prepared; importance and sensitivity unresolved",
                "strength": "not_checked",
            },
            {
                "source": "yunet",
                "text": f"{len(result.faces)} faces detected"
                if result.faceStatus == "done" and result.faces
                else "No faces detected; not evidence of clutter"
                if result.faceStatus == "done"
                else "Face check " + result.faceStatus.replace("_", " "),
                "strength": "strong"
                if result.faces
                else "weak"
                if result.faceStatus == "done"
                else "not_checked",
            },
        ]
        # Preparing a photo again must not lose D3's answers: that stage won't run for it again.
        before = c.execute(
            "SELECT evidence,sensitive,importance FROM photos WHERE content_id=?",
            (request.sha256,),
        ).fetchone()
        assessed = [
            row for row in json.loads(before["evidence"]) if row["source"] == "d3nano"
        ]
        if assessed and "unknown" not in (before["sensitive"], before["importance"]):
            evidence = evidence[1:]
        evidence += assessed
        c.execute(
            "UPDATE photos SET width=?,height=?,format=?,capture_date=?,capture_source=?,camera=?,gps=?,faces=?,evidence=?,triage='needs_review',review_version=review_version+1 WHERE content_id=?",
            (
                result.width,
                result.height,
                result.format,
                result.captureDate,
                result.captureDateSource,
                result.camera,
                packed(result.gps.model_dump()) if result.gps else None,
                packed([face.model_dump() for face in result.faces])
                if result.faceStatus == "done"
                else packed("not_checked"),
                packed(evidence),
                request.sha256,
            ),
        )
        if assessed:
            assessments.retriage(c, request.sha256)
        ack = {
            "accepted": True,
            "jobId": identifier,
            "contentId": request.sha256,
            "fingerprint": request.fingerprint,
        }
        c.execute(
            "INSERT INTO stage_results VALUES (?,?,?,?,?,?,?,?)",
            (
                request.sha256,
                "prepare",
                request.fingerprint,
                identifier,
                request.token,
                packed(request.payload),
                packed(ack),
                now(),
            ),
        )
        c.execute(
            "UPDATE stage_jobs SET state='done',expires=NULL,error=NULL WHERE id=?",
            (identifier,),
        )
        embeddings.queue_active(c, request.sha256)
        assessments.queue_active(c, request.sha256)
        bump(c)
        return ack
