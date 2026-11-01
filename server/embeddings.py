"""Isolated vector spaces and reproducible comparison; no model is silently selected."""

import json
from typing import Literal

import numpy as np
from fastapi import HTTPException
from pydantic import Field

from server.contracts import Contract, GroupId, PhotoQuery
from server.db import bump, fingerprint, now, packed, uid
from server.leases import valid_lease
from server.queries import matched, photo


class IndexConfig(Contract):
    backend: Literal["siglip2", "mobileclip2"]
    checkpoint: str = Field(min_length=1, max_length=500)
    revision: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    dimension: int = Field(ge=16, le=16384)
    preprocessing: dict
    precision: Literal["float32", "float16", "bfloat16"]
    normalization: Literal["l2"]
    runtime: str = Field(min_length=1, max_length=500)


class VectorResult(Contract):
    indexId: str
    vector: list[float] = Field(min_length=16, max_length=16384)


class QueueEmbeddings(Contract):
    contentIds: list[str] = Field(min_length=1, max_length=100000)


class SampleMember(Contract):
    contentId: str
    category: GroupId
    eventGroup: str = Field(min_length=1, max_length=200)
    split: Literal["train", "test"]


class ComparisonSample(Contract):
    members: list[SampleMember] = Field(min_length=1, max_length=10000)


class Prompt(Contract):
    category: GroupId
    text: str = Field(min_length=1, max_length=2000)
    vector: list[float] = Field(min_length=16, max_length=16384)


class PromptPack(Contract):
    version: str = Field(min_length=1, max_length=200)
    prompts: list[Prompt] = Field(min_length=2, max_length=1000)


class EvaluationRequest(Contract):
    sampleId: str
    promptVersion: str


class PromoteIndex(Contract):
    evaluationId: str


def get_index(c, identifier):
    row = c.execute(
        "SELECT * FROM embedding_indexes WHERE id=?", (identifier,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Unknown embedding index")
    return {**dict(row), "config": json.loads(row["config"])}


def vector_bytes(vector, dimension):
    array = np.asarray(vector, dtype="<f4")
    if (
        array.shape != (dimension,)
        or not np.isfinite(array).all()
        or abs(float(np.linalg.norm(array)) - 1) > 0.001
    ):
        raise HTTPException(
            422, "Expected a finite, L2-normalized vector of the exact index dimension"
        )
    return array.tobytes()


def register(c, config):
    if not config.preprocessing:
        raise HTTPException(422, "Full checkpoint preprocessing must be explicit")
    value, digest = config.model_dump(), fingerprint(config.model_dump())
    old = c.execute(
        "SELECT id FROM embedding_indexes WHERE fingerprint=?", (digest,)
    ).fetchone()
    if old:
        return get_index(c, old[0])
    identifier = uid()
    c.execute(
        "INSERT INTO embedding_indexes VALUES (?,?,?,'eval','building')",
        (identifier, digest, packed(value)),
    )
    return get_index(c, identifier)


def queue(c, identifier, request):
    index = get_index(c, identifier)
    queued = 0
    for content in set(request.contentIds):
        if not c.execute(
            "SELECT 1 FROM preparation_outputs p JOIN file_instances f ON f.content_id=p.content_id WHERE p.content_id=? AND f.state='available'",
            (content,),
        ).fetchone():
            raise HTTPException(
                409, "Prepare every selected content before queuing embeddings"
            )
        queued += c.execute(
            "INSERT OR IGNORE INTO stage_jobs(id,content_id,stage,fingerprint) VALUES (?,?,'embed',?)",
            (uid(), content, index["fingerprint"]),
        ).rowcount
    return {
        "queued": queued,
        "indexId": identifier,
        "fingerprint": index["fingerprint"],
    }


def accept(c, identifier, request):
    previous = c.execute(
        "SELECT * FROM stage_results WHERE job_id=?", (identifier,)
    ).fetchone()
    if previous:
        job = c.execute(
            "SELECT owner,attempt FROM stage_jobs WHERE id=?", (identifier,)
        ).fetchone()
        if (
            previous["token"] != request.token
            or previous["payload"] != packed(request.payload)
            or previous["fingerprint"] != request.fingerprint
            or previous["content_id"] != request.sha256
            or job["owner"] != request.owner
            or job["attempt"] != request.attempt
        ):
            raise HTTPException(409, "Conflicting duplicate embedding result")
        return json.loads(previous["ack"])
    job = valid_lease(c, identifier, request)
    payload = VectorResult.model_validate(request.payload)
    index = get_index(c, payload.indexId)
    if (
        job["stage"] != "embed"
        or index["fingerprint"] != request.fingerprint
        or job["fingerprint"] != request.fingerprint
        or job["content_id"] != request.sha256
    ):
        raise HTTPException(409, "Embedding space or input mismatch")
    source = c.execute(
        "SELECT * FROM file_instances WHERE id=?", (job["source_instance"],)
    ).fetchone()
    if (
        not source
        or source["state"] != "available"
        or (source["content_id"], source["size"], source["mtime_ns"])
        != (request.sha256, job["source_size"], job["source_mtime_ns"])
    ):
        raise HTTPException(409, "Embedding source version superseded")
    vector = vector_bytes(payload.vector, index["config"]["dimension"])
    c.execute(
        "INSERT INTO embeddings VALUES (?,?,?)",
        (request.sha256, payload.indexId, vector),
    )
    ack = {"accepted": True, "jobId": identifier, "indexId": payload.indexId}
    c.execute(
        "INSERT INTO stage_results VALUES (?,?,?,?,?,?,?,?)",
        (
            request.sha256,
            "embed",
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
    return ack


def save_sample(c, request):
    groups, contents = {}, set()
    for member in request.members:
        if member.contentId in contents:
            raise HTTPException(422, "A sample content must appear only once")
        contents.add(member.contentId)
        if groups.setdefault(member.eventGroup, member.split) != member.split:
            raise HTTPException(422, "Train/test event or similarity-group leakage")
        if not c.execute(
            "SELECT 1 FROM photos WHERE content_id=?", (member.contentId,)
        ).fetchone():
            raise HTTPException(404, "Unknown labelled sample content")
        actual = c.execute(
            "SELECT event_id FROM event_members WHERE content_id=?", (member.contentId,)
        ).fetchone()
        if actual and member.eventGroup != actual[0]:
            raise HTTPException(
                422, "Existing event membership must be the split group"
            )
    identifier = uid()
    c.execute(
        "INSERT INTO comparison_samples VALUES (?,?,?)",
        (identifier, packed(request.model_dump()["members"]), now()),
    )
    return {"id": identifier, "members": len(contents)}


def save_prompts(c, identifier, request):
    index = get_index(c, identifier)
    for prompt in request.prompts:
        vector_bytes(prompt.vector, index["config"]["dimension"])
    value = request.model_dump()["prompts"]
    c.execute(
        "INSERT INTO prompt_packs VALUES (?,?,?,?)",
        (identifier, request.version, packed(value), fingerprint(value)),
    )
    return {"indexId": identifier, "version": request.version}


def evaluate(c, identifier, request):
    index = get_index(c, identifier)
    sample = c.execute(
        "SELECT members FROM comparison_samples WHERE id=?", (request.sampleId,)
    ).fetchone()
    pack = c.execute(
        "SELECT prompts FROM prompt_packs WHERE index_id=? AND version=?",
        (identifier, request.promptVersion),
    ).fetchone()
    if not sample or not pack:
        raise HTTPException(404, "Comparison sample or prompt pack not found")
    prompts = json.loads(pack[0])
    matrix = np.asarray([p["vector"] for p in prompts], dtype=np.float32)
    pairs = []
    for member in json.loads(sample[0]):
        if member["split"] != "test":
            continue
        row = c.execute(
            "SELECT vector FROM embeddings WHERE index_id=? AND content_id=?",
            (identifier, member["contentId"]),
        ).fetchone()
        if not row:
            raise HTTPException(
                409, "The exact same held-out sample must be embedded before comparison"
            )
        vector = np.frombuffer(row[0], dtype="<f4")
        if vector.size != index["config"]["dimension"]:
            raise HTTPException(409, "Stored vector dimension mismatch")
        predicted = prompts[int(np.argmax(matrix @ vector))]["category"]
        pairs.append((member["category"], predicted))
    if not pairs:
        raise HTTPException(422, "At least one held-out test sample is required")
    metrics = {}
    for label in sorted({label for pair in pairs for label in pair}):
        tp = sum(a == b == label for a, b in pairs)
        predicted = sum(b == label for _, b in pairs)
        actual = sum(a == label for a, _ in pairs)
        precision, recall = (
            tp / predicted if predicted else 0,
            tp / actual if actual else 0,
        )
        metrics[label] = {
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall)
            if precision + recall
            else 0,
            "support": actual,
        }
    report = {
        "testCount": len(pairs),
        "macroF1": sum(v["f1"] for v in metrics.values()) / len(metrics),
        "categories": metrics,
        "fingerprint": index["fingerprint"],
        "performanceMeasured": False,
    }
    evaluation = uid()
    c.execute(
        "INSERT INTO evaluations VALUES (?,?,?,?,?,?)",
        (
            evaluation,
            identifier,
            request.sampleId,
            request.promptVersion,
            packed(report),
            now(),
        ),
    )
    return {"id": evaluation, **report}


def promote(c, identifier, request):
    get_index(c, identifier)
    if not c.execute(
        "SELECT 1 FROM evaluations WHERE id=? AND index_id=?",
        (request.evaluationId, identifier),
    ).fetchone():
        raise HTTPException(
            409, "Explicit promotion requires this index comparison report"
        )
    active = c.execute(
        "SELECT id FROM embedding_indexes WHERE role='primary'"
    ).fetchone()
    if active and active[0] == identifier:
        return get_index(c, identifier)
    missing = c.execute(
        "SELECT COUNT(DISTINCT p.content_id) FROM photos p JOIN file_instances f ON f.content_id=p.content_id WHERE f.state='available' AND NOT EXISTS(SELECT 1 FROM embeddings e WHERE e.content_id=p.content_id AND e.index_id=?)",
        (identifier,),
    ).fetchone()[0]
    if missing:
        raise HTTPException(
            409, f"Primary backfill incomplete: {missing} contents have no vectors"
        )
    c.execute("UPDATE embedding_indexes SET role='eval' WHERE role='primary'")
    c.execute(
        "UPDATE embedding_indexes SET role='primary',status='ready' WHERE id=?",
        (identifier,),
    )
    value = json.loads(
        c.execute("SELECT value FROM settings WHERE section='models'").fetchone()[0]
    )
    value["primaryIndex"] = identifier
    c.execute(
        "UPDATE settings SET value=?,version=version+1 WHERE section='models'",
        (packed(value),),
    )
    c.execute(
        "UPDATE photos SET triage='needs_review',band='low',review_version=review_version+1"
    )
    c.execute("UPDATE meta SET value=value+1 WHERE key='policyRevision'")
    bump(c)
    return get_index(c, identifier)


def similar(c, content, query: PhotoQuery):
    primary = c.execute(
        "SELECT id FROM embedding_indexes WHERE role='primary' AND status='ready'"
    ).fetchone()
    if not primary:
        raise HTTPException(503, "No explicitly promoted primary embedding index")
    index = get_index(c, primary[0])
    row = c.execute(
        "SELECT vector FROM embeddings WHERE content_id=? AND index_id=?",
        (content, primary[0]),
    ).fetchone()
    if not row:
        raise HTTPException(409, "Example has no vector in the primary index")
    example = np.frombuffer(row[0], dtype="<f4")
    # A bounded top-k heap avoids an NxN matrix or a full-library vector allocation.
    import heapq

    best = []
    for member in matched(c, query):
        if member["content_id"] == content:
            continue
        vector = c.execute(
            "SELECT vector FROM embeddings WHERE content_id=? AND index_id=?",
            (member["content_id"], primary[0]),
        ).fetchone()
        if vector:
            array = np.frombuffer(vector[0], dtype="<f4")
            if array.size != index["config"]["dimension"]:
                raise HTTPException(409, "Index dimension mismatch")
            score = float(np.dot(example, array))
            entry = (score, member["content_id"])
            if len(best) < 500:
                heapq.heappush(best, entry)
            elif entry > best[0]:
                heapq.heapreplace(best, entry)
    ordered = sorted(best, reverse=True)
    lookup = {member["content_id"]: member for member in matched(c, query)}
    offset = (query.page - 1) * query.limit
    return {
        "items": [
            {**photo(c, lookup[identifier]), "similarity": score}
            for score, identifier in ordered[offset : offset + query.limit]
        ],
        "total": len(ordered),
        "truncated": len(ordered) == 500,
        "indexId": primary[0],
        "mode": "same-index-image-similarity",
    }
