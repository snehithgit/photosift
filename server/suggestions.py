"""Conservative, explicit scoring and neighbor suggestions; never final decisions."""

import json
from datetime import datetime

import numpy as np
from fastapi import HTTPException
from pydantic import Field

from server.assessments import UNCONFIGURED, active_spec, queue_active, triage
from server.contracts import Contract, PhotoQuery
from server.db import bump, fingerprint, now, packed, uid
from server.embeddings import get_index, rank
from server.queries import detail, matched


class ScoringRequest(Contract):
    query: PhotoQuery
    indexId: str
    promptVersion: str
    evaluationId: str
    highMargin: float = Field(gt=0, le=2, allow_inf_nan=False)
    mediumMargin: float = Field(gt=0, le=2, allow_inf_nan=False)


class NeighborRequest(Contract):
    query: PhotoQuery
    indexId: str
    maxGapSeconds: float = Field(gt=0, le=7 * 86400, allow_inf_nan=False)
    minSimilarity: float = Field(ge=-1, le=1, allow_inf_nan=False)
    maxHamming: int = Field(ge=0, le=64)
    maxNeighbors: int = Field(default=50, ge=1, le=100)


class EventSuggestionRequest(Contract):
    query: PhotoQuery
    maxGapSeconds: float = Field(gt=0, le=7 * 86400, allow_inf_nan=False)
    minPhotos: int = Field(ge=2, le=10000)


def score(c, request):
    index = get_index(c, request.indexId)
    if index["role"] != "primary" or index["status"] != "ready":
        raise HTTPException(
            409, "Scoring requires the explicitly promoted primary index"
        )
    if request.mediumMargin >= request.highMargin:
        raise HTTPException(422, "Medium margin must be below the high margin")
    if not c.execute(
        "SELECT 1 FROM evaluations WHERE id=? AND index_id=? AND prompt_version=?",
        (request.evaluationId, request.indexId, request.promptVersion),
    ).fetchone():
        raise HTTPException(
            409, "Threshold policy must reference this exact held-out comparison report"
        )
    pack = c.execute(
        "SELECT prompts FROM prompt_packs WHERE index_id=? AND version=?",
        (request.indexId, request.promptVersion),
    ).fetchone()
    if not pack:
        raise HTTPException(404, "Unknown prompt pack")
    prompts = json.loads(pack[0])
    matrix = np.asarray([prompt["vector"] for prompt in prompts], dtype=np.float32)
    identifier = uid()
    c.execute(
        "INSERT INTO scoring_runs VALUES (?,?,?,?,?)",
        (
            identifier,
            request.indexId,
            request.promptVersion,
            packed(request.model_dump()),
            now(),
        ),
    )
    scored = changed = missing = 0
    members = matched(c, request.query)
    start = (request.query.page - 1) * request.query.limit
    for member in members[start : start + request.query.limit]:
        vector = c.execute(
            "SELECT vector FROM embeddings WHERE content_id=? AND index_id=?",
            (member["content_id"], request.indexId),
        ).fetchone()
        if not vector:
            missing += 1
            continue
        array = np.frombuffer(vector[0], dtype="<f4")
        if array.size != index["config"]["dimension"]:
            raise HTTPException(409, "Stored vector space is inconsistent")
        group, rival, margin, classes = rank(prompts, matrix, array)
        band = (
            "high"
            if margin >= request.highMargin
            else "medium"
            if margin >= request.mediumMargin
            else "low"
        )
        c.execute(
            "UPDATE group_assignments SET ai_group=? WHERE content_id=?",
            (group, member["content_id"]),
        )
        p = detail(c, member["content_id"])
        if p["groupId"] != member["group_id"]:
            c.execute(
                "UPDATE photos SET duplicate_set=NULL WHERE content_id=?",
                (member["content_id"],),
            )
        evidence = [
            row
            for row in p["evidence"]
            if row["source"] not in ("siglip2", "mobileclip2")
        ]
        evidence.append(
            {
                "source": index["config"]["backend"],
                "text": f"Prompt ranking: {group}; competing category {rival}; margin {margin:.4f} (ranking signal, not a probability)",
                "strength": "small_margin" if band == "low" else "strong",
            }
        )
        verdict = (band, triage(p, band), packed(evidence))
        # Scoring again to the same result must not disturb selections or undo.
        if verdict != (member["band"], member["triage"], member["evidence"]):
            c.execute(
                "UPDATE photos SET band=?,triage=?,evidence=?,review_version=review_version+1 WHERE content_id=?",
                (*verdict, member["content_id"]),
            )
            changed += 1
        c.execute(
            "INSERT INTO content_scores VALUES (?,?,?,?)",
            (identifier, member["content_id"], packed(classes), margin),
        )
        if (
            p["faces"] == "not_checked"
            or p["importance"] == "unknown"
            or p["sensitive"] == "unknown"
            or band == "low"
        ) and not queue_active(c, member["content_id"]):
            c.execute(
                "INSERT OR IGNORE INTO stage_jobs(id,content_id,stage,fingerprint) VALUES (?,?,'assess',?)",
                (uid(), member["content_id"], UNCONFIGURED),
            )
        scored += 1
    if changed:
        bump(c)
    return {
        "id": identifier,
        "scored": scored,
        "changed": changed,
        "withoutVector": missing,
        "decisionsChanged": 0,
        "assessmentConfigured": active_spec(c) is not None,
        "total": len(members),
        "nextPage": request.query.page + 1
        if start + request.query.limit < len(members)
        else None,
    }


def capture_key(photo):
    # Date-only filename clues cannot establish seconds-level event/burst proximity.
    if (
        not photo["captureDate"]
        or "T" not in photo["captureDate"]
        or photo["captureDateSource"] == "unknown"
    ):
        return None
    try:
        value = datetime.fromisoformat(photo["captureDate"])
        return ("aware" if value.tzinfo else "naive", value)
    except ValueError:
        return None


def neighbors(c, request):
    index = get_index(c, request.indexId)
    if index["role"] != "primary" or index["status"] != "ready":
        raise HTTPException(409, "Similarity suggestions require the primary index")
    buckets = {}
    for member in matched(c, request.query):
        p = detail(c, member["content_id"])
        date = capture_key(p)
        prep = c.execute(
            "SELECT p.payload FROM preparation_outputs p JOIN stage_specs s ON s.fingerprint=p.fingerprint WHERE p.content_id=? AND s.active=1",
            (p["contentId"],),
        ).fetchone()
        vector = c.execute(
            "SELECT vector FROM embeddings WHERE content_id=? AND index_id=?",
            (p["contentId"], request.indexId),
        ).fetchone()
        if date and prep and vector:
            buckets.setdefault((p["groupId"], date[0]), []).append(
                (
                    date[1],
                    p["contentId"],
                    int(json.loads(prep[0])["pHash"], 16),
                    np.frombuffer(vector[0], dtype="<f4"),
                )
            )
    sets = []
    for (group, _), items in buckets.items():
        items.sort(key=lambda row: (row[0], row[1]))
        # Compare only a bounded preceding neighborhood, not every pair in the library.
        parent = {item[1]: item[1] for item in items}

        def find(value):
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        for i, item in enumerate(items):
            for candidate in items[max(0, i - request.maxNeighbors) : i]:
                if (item[0] - candidate[0]).total_seconds() > request.maxGapSeconds:
                    continue
                if (item[2] ^ candidate[2]).bit_count() <= request.maxHamming and float(
                    np.dot(item[3], candidate[3])
                ) >= request.minSimilarity:
                    parent[find(item[1])] = find(candidate[1])
        grouped = {}
        for item in items:
            grouped.setdefault(find(item[1]), []).append(item[1])
        for members in grouped.values():
            if len(members) < 2:
                continue
            identifier = (
                "possible-"
                + fingerprint(
                    {
                        "group": group,
                        "members": sorted(members),
                        "config": request.model_dump(),
                    }
                )[:24]
            )
            for content in members:
                c.execute(
                    "UPDATE photos SET duplicate_set=?,review_version=review_version+1 WHERE content_id=?",
                    (identifier, content),
                )
            sets.append({"id": identifier, "contentIds": members, "preselected": False})
    run = uid()
    c.execute(
        "INSERT INTO suggestion_runs VALUES (?,'neighbors',?,?,?)",
        (run, packed(request.model_dump()), packed(sets), now()),
    )
    if sets:
        bump(c)
    return {"id": run, "sets": sets, "decisionsChanged": 0}


def events(c, request):
    buckets = {}
    for member in matched(c, request.query):
        p = detail(c, member["content_id"])
        date = capture_key(p)
        if (
            p["groupId"] not in ("people", "places", "events")
            or not date
            or p.get("eventId")
        ):
            continue
        buckets.setdefault(date[0], []).append((date[1], p["contentId"]))
    created = []
    for items in buckets.values():
        items.sort()
        groups = []
        for item in items:
            if (
                not groups
                or (item[0] - groups[-1][-1][0]).total_seconds() > request.maxGapSeconds
            ):
                groups.append([])
            groups[-1].append(item)
        for group in groups:
            if len(group) < request.minPhotos:
                continue
            identifier = (
                "suggested-" + fingerprint([member[1] for member in group])[:24]
            )
            c.execute(
                "INSERT OR IGNORE INTO events(id,name,status) VALUES (?,?,'suggested')",
                (
                    identifier,
                    "Time-neighbor suggestion · " + group[0][0].date().isoformat(),
                ),
            )
            for _, content in group:
                c.execute(
                    "INSERT OR IGNORE INTO event_members(content_id,event_id,source) VALUES (?,?,'ai')",
                    (content, identifier),
                )
                c.execute(
                    "UPDATE photos SET review_version=review_version+1 WHERE content_id=?",
                    (content,),
                )
            created.append(
                {
                    "id": identifier,
                    "photos": len(group),
                    "reason": "Time proximity only; place and event meaning are unconfirmed",
                }
            )
    run = uid()
    c.execute(
        "INSERT INTO suggestion_runs VALUES (?,'events',?,?,?)",
        (run, packed(request.model_dump()), packed(created), now()),
    )
    if created:
        bump(c)
    return {"id": run, "events": created, "decisionsChanged": 0}
