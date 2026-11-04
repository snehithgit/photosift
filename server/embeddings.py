"""Isolated vector spaces and reproducible comparison; no model is silently selected."""

import json
from collections import Counter
from datetime import datetime
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
    # Teach has one Personal label; it doesn't say whether a photo is people, events or places.
    category: GroupId | Literal["personal"]
    # Both or neither, for the whole sample. Left out, the server works them out when saving.
    eventGroup: str | None = Field(default=None, min_length=1, max_length=200)
    split: Literal["train", "test"] | None = None


class ComparisonSample(Contract):
    members: list[SampleMember] = Field(min_length=1, max_length=10000)


class Prompt(Contract):
    category: GroupId
    text: str = Field(min_length=1, max_length=2000)
    vector: list[float] = Field(min_length=16, max_length=16384)


class PromptPack(Contract):
    version: str = Field(min_length=1, max_length=200)
    # The fingerprint the vectors were encoded under, so a stale encoder is refused on save.
    indexFingerprint: str
    prompts: list[Prompt] = Field(min_length=2, max_length=1000)


class EvaluationRequest(Contract):
    sampleId: str
    promptVersion: str


class PromoteIndex(Contract):
    evaluationId: str


# People, events and places are one family: telling them apart says nothing about whether a
# photo is personal, so they only compete with other families.
PERSONAL = ("people", "events", "places")
CLUTTER = ("screenshots", "memes", "badshots")
TARGET_PRECISION = {"high": 0.95, "medium": 0.8}
MIN_THRESHOLD_SUPPORT = 5
SESSION_GAP_SECONDS = 1800
SIMILAR_HAMMING = 10


def family(category):
    return "personal" if category in PERSONAL else category


def rank(prompts, matrix, vector):
    """Best category, its closest rival from another family, the margin, every class score."""
    classes = {}
    for prompt, value in zip(prompts, matrix @ vector, strict=True):
        classes[prompt["category"]] = max(
            classes.get(prompt["category"], -2), float(value)
        )
    ordered = sorted(classes.items(), key=lambda pair: pair[1], reverse=True)
    group, best = ordered[0]
    rival = next((item for item in ordered if family(item[0]) != family(group)), None)
    if not rival:
        raise HTTPException(
            422, "At least two competing content categories are required"
        )
    return group, rival[0], best - rival[1], classes


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


def active_index(c):
    row = c.execute(
        "SELECT i.id FROM stage_specs s JOIN embedding_indexes i ON i.fingerprint=s.fingerprint WHERE s.stage='embed' AND s.active=1"
    ).fetchone()
    return get_index(c, row[0]) if row else None


def queue_active(c, content):
    index = active_index(c)
    if index:
        c.execute(
            "INSERT OR IGNORE INTO stage_jobs(id,content_id,stage,fingerprint) VALUES (?,?,'embed',?)",
            (uid(), content, index["fingerprint"]),
        )


def configure(c, config):
    """Choose the index the laptop fills. Filling it never promotes it to primary."""
    index = register(c, config)
    digest = index["fingerprint"]
    c.execute(
        "UPDATE stage_jobs SET state='skipped',token=NULL,expires=NULL,error='Embedding contract superseded' WHERE stage='embed' AND fingerprint!=? AND state IN ('queued','running','paused','failed') AND fingerprint IN (SELECT fingerprint FROM stage_specs WHERE stage='embed' AND active=1)",
        (digest,),
    )
    c.execute("UPDATE stage_specs SET active=0 WHERE stage='embed'")
    c.execute(
        "INSERT INTO stage_specs VALUES (?,'embed',?,1,?) ON CONFLICT(fingerprint) DO UPDATE SET active=1",
        (digest, packed(index["config"]), now()),
    )
    queued = c.execute(
        "UPDATE stage_jobs SET state='queued',error=NULL WHERE stage='embed' AND fingerprint=? AND state='skipped'",
        (digest,),
    ).rowcount
    for row in c.execute(
        "SELECT DISTINCT p.content_id FROM preparation_outputs p JOIN stage_specs s ON s.fingerprint=p.fingerprint AND s.active=1 JOIN file_instances f ON f.content_id=p.content_id AND f.state='available'"
    ).fetchall():
        queued += c.execute(
            "INSERT OR IGNORE INTO stage_jobs(id,content_id,stage,fingerprint) VALUES (?,?,'embed',?)",
            (uid(), row[0], digest),
        ).rowcount
    return {"indexId": index["id"], "fingerprint": digest, "queued": queued}


def result_record(payload):
    # A replay only has to prove it is the same vector, so 1152 numbers per photo stay out of
    # the results table; the vector itself is in `embeddings`.
    return packed({"sha256": fingerprint(payload)})


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
            or previous["payload"] != result_record(request.payload)
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
            result_record(request.payload),
            packed(ack),
            now(),
        ),
    )
    c.execute(
        "UPDATE stage_jobs SET state='done',expires=NULL,error=NULL WHERE id=?",
        (identifier,),
    )
    return ack


def separate(c, members):
    """Keep photos of one moment on one side, so a burst can't sit in both train and test."""
    parent = {member["contentId"]: member["contentId"] for member in members}
    events = {}

    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def join(first, second):
        first, second = find(first), find(second)
        # An event is always its own split group, so two events are never merged.
        if first == second or (
            first in events and second in events and events[first] != events[second]
        ):
            return
        parent[second] = first
        if second in events:
            events[first] = events.pop(second)

    keyed, timed, hashes = {}, {}, {}
    for member in members:
        content = member["contentId"]
        row = c.execute(
            "SELECT p.capture_date,p.capture_source,p.duplicate_set,m.event_id FROM photos p LEFT JOIN event_members m ON m.content_id=p.content_id WHERE p.content_id=?",
            (content,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Unknown labelled sample content")
        if row["event_id"]:
            events[content] = row["event_id"]
        if row["duplicate_set"]:
            keyed.setdefault("set:" + row["duplicate_set"], []).append(content)
        # A file time can be the day a whole folder was copied, so it says nothing here.
        date = (
            row["capture_date"] if row["capture_source"] in ("exif", "filename") else ""
        )
        if date and "T" in date:
            try:
                moment = datetime.fromisoformat(date)
                timed.setdefault(bool(moment.tzinfo), []).append((moment, content))
            except ValueError:
                pass
        elif date:
            keyed.setdefault("day:" + date[:10], []).append(content)
        prepared = c.execute(
            "SELECT p.payload FROM preparation_outputs p JOIN stage_specs s ON s.fingerprint=p.fingerprint WHERE p.content_id=? AND s.active=1",
            (content,),
        ).fetchone()
        if prepared:
            hashes[content] = int(json.loads(prepared[0])["pHash"], 16)
    for event in set(events.values()):
        keyed["event:" + event] = [k for k, v in events.items() if v == event]
    for contents in keyed.values():
        for content in contents[1:]:
            join(contents[0], content)
    for moments in timed.values():
        moments.sort()
        for before, after in zip(moments, moments[1:]):
            if (after[0] - before[0]).total_seconds() <= SESSION_GAP_SECONDS:
                join(before[1], after[1])
    known = list(hashes)
    values = np.asarray([hashes[content] for content in known], dtype=np.uint64)
    for i, content in enumerate(known):
        near = np.bitwise_count(values[i + 1 :] ^ values[i]) <= SIMILAR_HAMMING
        for j in np.nonzero(near)[0]:
            join(content, known[i + 1 + int(j)])

    groups = {}
    for member in members:
        groups.setdefault(find(member["contentId"]), []).append(member)
    counts = {"train": Counter(), "test": Counter()}
    # Largest moments first, in an order that doesn't depend on the order of labelling.
    for root, group in sorted(
        groups.items(),
        key=lambda item: (
            -len(item[1]),
            fingerprint(sorted(m["contentId"] for m in item[1])),
        ),
    ):
        kinds = Counter(family(m["category"]) for m in group)
        main = kinds.most_common(1)[0][0]
        side = min(
            ("test", "train"),
            key=lambda name: (counts[name][main], sum(counts[name].values())),
        )
        counts[side].update(kinds)
        name = (
            events.get(root)
            or "moment-" + fingerprint(sorted(m["contentId"] for m in group))[:16]
        )
        for member in group:
            member.update(eventGroup=name, split=side)
    if not counts["train"] or not counts["test"]:
        raise HTTPException(
            422, "Label photos from at least two separate moments to hold some out"
        )


def save_sample(c, request):
    members = request.model_dump()["members"]
    contents = {member["contentId"] for member in members}
    if len(contents) != len(members):
        raise HTTPException(422, "A sample content must appear only once")
    given = [m["eventGroup"] is not None and m["split"] is not None for m in members]
    if any(given) and not all(given):
        raise HTTPException(
            422, "Give an event group and split for every member, or for none"
        )
    if not all(given):
        separate(c, members)
    groups = {}
    for member in members:
        if groups.setdefault(member["eventGroup"], member["split"]) != member["split"]:
            raise HTTPException(422, "Train/test event or similarity-group leakage")
        if not c.execute(
            "SELECT 1 FROM photos WHERE content_id=?", (member["contentId"],)
        ).fetchone():
            raise HTTPException(404, "Unknown labelled sample content")
        actual = c.execute(
            "SELECT event_id FROM event_members WHERE content_id=?",
            (member["contentId"],),
        ).fetchone()
        if actual and member["eventGroup"] != actual[0]:
            raise HTTPException(
                422, "Existing event membership must be the split group"
            )
    identifier = uid()
    c.execute(
        "INSERT INTO comparison_samples VALUES (?,?,?)",
        (identifier, packed(members), now()),
    )
    return {"id": identifier, **sample_summary(members)}


def add_labels(c, request):
    """Save a Teach round on top of every earlier label, as one new frozen sample."""
    latest = c.execute(
        "SELECT members FROM comparison_samples ORDER BY created_at DESC,rowid DESC LIMIT 1"
    ).fetchone()
    labels = {
        member["contentId"]: member["category"]
        for member in (json.loads(latest[0]) if latest else [])
    }
    # A photo labelled again keeps only its newest label.
    labels.update({member.contentId: member.category for member in request.members})
    if len(labels) > 10000:
        raise HTTPException(422, "A sample holds at most 10000 labels")
    # Splits are worked out again over all labels, so a moment stays on one side.
    return save_sample(
        c,
        ComparisonSample(
            members=[
                SampleMember(contentId=content, category=category)
                for content, category in labels.items()
            ]
        ),
    )


def sample_summary(members):
    return {
        "members": len(members),
        "train": sum(m["split"] == "train" for m in members),
        "test": sum(m["split"] == "test" for m in members),
        "groups": len({m["eventGroup"] for m in members}),
        "categories": dict(Counter(family(m["category"]) for m in members)),
    }


def save_prompts(c, identifier, request):
    index = get_index(c, identifier)
    if request.indexFingerprint != index["fingerprint"]:
        raise HTTPException(
            409, "These prompt vectors belong to a different embedding space"
        )
    if len({family(prompt.category) for prompt in request.prompts}) < 2:
        raise HTTPException(
            422, "At least two competing content categories are required"
        )
    for prompt in request.prompts:
        vector_bytes(prompt.vector, index["config"]["dimension"])
    value = request.model_dump()["prompts"]
    existing = c.execute(
        "SELECT prompts FROM prompt_packs WHERE index_id=? AND version=?",
        (identifier, request.version),
    ).fetchone()
    summary = {"indexId": identifier, "version": request.version, "prompts": len(value)}
    if existing:
        # Encoding again can differ in the last digits; the first vectors stay in force.
        if [(p["category"], p["text"]) for p in json.loads(existing[0])] != [
            (p["category"], p["text"]) for p in value
        ]:
            raise HTTPException(409, "A saved prompt pack version can't change")
        return {**summary, "created": False}
    c.execute(
        "INSERT INTO prompt_packs VALUES (?,?,?,?)",
        (identifier, request.version, packed(value), fingerprint(value)),
    )
    return {**summary, "created": True}


def cut_off(rows, target):
    """The lowest margin where the photos at or above it were right often enough, or None."""
    chosen, right = None, 0
    ordered = sorted(rows, key=lambda row: row[0], reverse=True)
    for count, (margin, correct) in enumerate(ordered, start=1):
        right += correct
        if count >= MIN_THRESHOLD_SUPPORT and right / count >= target and margin > 0:
            chosen = margin
    return chosen


def choose_thresholds(train):
    """Cut-offs from the train split only, so the held-out figures can test them."""
    high = cut_off(train, TARGET_PRECISION["high"])
    # Medium's target is for the photos in that band, not for them plus the High ones above.
    below = [row for row in train if high is None or row[0] < high]
    medium = cut_off(below, TARGET_PRECISION["medium"])
    top = (
        high if high is not None else 2.0
    )  # A margin can't reach 2: nothing goes in High.
    return {
        "high": top,
        "medium": medium if medium is not None else top - 1e-6,  # An empty Medium band.
        "highProven": high is not None,
        "mediumProven": medium is not None,
        "targetPrecision": TARGET_PRECISION,
        "minSupport": MIN_THRESHOLD_SUPPORT,
        "chosenFrom": "train",
        "trainCount": len(train),
    }


def band_of(margin, thresholds):
    if margin >= thresholds["high"]:
        return "high"
    return "medium" if margin >= thresholds["medium"] else "low"


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
    members = json.loads(sample[0])
    rows = {"train": [], "test": []}
    for member in members:
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
        predicted, _, margin, _ = rank(prompts, matrix, vector)
        rows[member["split"]].append(
            (family(member["category"]), family(predicted), margin, predicted)
        )
    pairs = rows["test"]
    if not pairs:
        raise HTTPException(422, "At least one held-out test sample is required")
    metrics = {}
    for label in sorted({label for pair in pairs for label in pair[:2]}):
        tp = sum(row[0] == row[1] == label for row in pairs)
        predicted = sum(row[1] == label for row in pairs)
        actual = sum(row[0] == label for row in pairs)
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
    thresholds = choose_thresholds([(r[2], r[0] == r[1]) for r in rows["train"]])
    held_out = {band: {"photos": 0, "correct": 0} for band in ("high", "medium", "low")}
    confusion = {}
    risky = {"total": 0, "suggestedClutter": 0, "inHighBand": 0}
    for label, predicted, margin, group in pairs:
        band = band_of(margin, thresholds)
        held_out[band]["photos"] += 1
        held_out[band]["correct"] += label == predicted
        confusion.setdefault(label, {}).setdefault(predicted, 0)
        confusion[label][predicted] += 1
        if label in ("personal", "documents"):
            risky["total"] += 1
            risky["suggestedClutter"] += group in CLUTTER
            risky["inHighBand"] += group in CLUTTER and band == "high"
    sources = c.execute(
        "SELECT r.name,COUNT(DISTINCT f.content_id) AS photos FROM file_instances f JOIN roots r ON r.id=f.root_id WHERE f.content_id IN (SELECT value FROM json_each(?)) GROUP BY r.id ORDER BY r.name",
        (json.dumps([member["contentId"] for member in members]),),
    ).fetchall()
    report = {
        "testCount": len(pairs),
        "trainCount": len(rows["train"]),
        "accuracy": sum(row[0] == row[1] for row in pairs) / len(pairs),
        "macroF1": sum(v["f1"] for v in metrics.values()) / len(metrics),
        "categories": metrics,
        "confusion": confusion,
        "thresholds": thresholds,
        "heldOutBands": held_out,
        "personalAndDocumentsAsClutter": risky,
        "sources": [dict(row) for row in sources],
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
    return {
        "id": evaluation,
        "sampleId": request.sampleId,
        "promptVersion": request.promptVersion,
        **report,
    }


def overview(c):
    """What the Models screen needs to walk an index from filled to scored."""
    row = c.execute(
        "SELECT id FROM embedding_indexes WHERE role='primary' AND status='ready'"
    ).fetchone()
    index = get_index(c, row[0]) if row else active_index(c)
    if not index:
        return None
    packs = c.execute(
        "SELECT version,prompts FROM prompt_packs WHERE index_id=? ORDER BY rowid DESC",
        (index["id"],),
    ).fetchall()
    samples = c.execute(
        "SELECT * FROM comparison_samples ORDER BY created_at DESC,rowid DESC LIMIT 20"
    ).fetchall()
    evaluations = c.execute(
        "SELECT * FROM evaluations WHERE index_id=? ORDER BY created_at DESC,rowid DESC LIMIT 20",
        (index["id"],),
    ).fetchall()
    return {
        "indexId": index["id"],
        "fingerprint": index["fingerprint"],
        "role": index["role"],
        "missingVectors": c.execute(
            "SELECT COUNT(DISTINCT p.content_id) FROM photos p JOIN file_instances f ON f.content_id=p.content_id WHERE f.state='available' AND NOT EXISTS(SELECT 1 FROM embeddings e WHERE e.content_id=p.content_id AND e.index_id=?)",
            (index["id"],),
        ).fetchone()[0],
        "scored": c.execute(
            "SELECT COUNT(DISTINCT s.content_id) FROM content_scores s JOIN scoring_runs r ON r.id=s.run_id WHERE r.index_id=?",
            (index["id"],),
        ).fetchone()[0],
        "promptPacks": [
            {"version": pack["version"], "prompts": len(json.loads(pack["prompts"]))}
            for pack in packs
        ],
        "samples": [
            {
                "id": sample["id"],
                "createdAt": sample["created_at"],
                **sample_summary(json.loads(sample["members"])),
            }
            for sample in samples
        ],
        "evaluations": [
            {
                "id": row["id"],
                "sampleId": row["sample_id"],
                "promptVersion": row["prompt_version"],
                "createdAt": row["created_at"],
                **json.loads(row["metrics"]),
            }
            for row in evaluations
        ],
    }


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
