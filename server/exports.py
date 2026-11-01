"""Frozen copy plans and durable run/journal contracts, without a Phase 7 copier."""

import csv
import io
import json
import re
import shutil
from pathlib import Path

from fastapi import HTTPException

from server.db import action, audit, fingerprint, now, packed, uid
from server.paths import checked_path, overlap
from server.queries import matched, photo


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:150]
    if not value or value.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *[f"COM{i}" for i in range(1, 10)],
        *[f"LPT{i}" for i in range(1, 10)],
    }:
        return "_" + value
    return value


def create_plan(db, request):
    identifier = uid()
    destination = Path(request.destination).expanduser()
    if not destination.is_absolute():
        raise HTTPException(422, "Destination must be an existing absolute directory")
    try:
        destination = checked_path(destination).resolve()
        if not destination.is_dir():
            raise ValueError("Destination must already exist")
        with db.read() as c:
            roots = [r[0] for r in c.execute("SELECT path FROM roots")]
            if any(overlap(destination, r) for r in roots) or overlap(
                destination, db.path.parent
            ):
                raise ValueError(
                    "Export destination must be separate from all sources and app data"
                )
            # Explicit reviewed export decisions supersede the working-view decision default.
            query = request.query.model_copy(
                update={"decisions": "all", "reviewed": True}
            )
            candidates = matched(c, query)
            entries, exclusions = [], 0
            for row in candidates:
                p = photo(c, row)
                if (
                    p["decision"] not in request.decisions
                    or request.groups
                    and p["groupId"] not in request.groups
                ):
                    exclusions += 1
                    continue
                if p["sensitive"] != "no" and not request.includeSensitive:
                    exclusions += 1
                    continue
                source = c.execute(
                    "SELECT f.*,r.path AS root_path FROM file_instances f JOIN roots r ON r.id=f.root_id WHERE f.content_id=? AND state='available' ORDER BY f.id LIMIT 1",
                    (p["contentId"],),
                ).fetchone()
                if not source:
                    exclusions += 1
                    continue
                date = p["captureDate"] or "Unknown"
                if p["sensitive"] != "no":
                    folder = Path("Private/Sensitive")
                elif p["decision"] == "later":
                    folder = Path("Review-Later")
                elif p["decision"] == "clutter":
                    folder = Path("Clutter") / safe_name(p["groupId"])
                elif p["groupId"] == "documents":
                    folder = Path("Organized/Documents") / safe_name(
                        p.get("documentType") or "Unclassified"
                    )
                elif p["groupId"] in ("screenshots", "memes"):
                    folder = Path("Organized") / (
                        "Screenshots"
                        if p["groupId"] == "screenshots"
                        else "Memes-and-Graphics"
                    )
                elif request.template == "original":
                    folder = Path("Organized/Photos") / Path(source["path"]).parent
                else:
                    event = (
                        c.execute(
                            "SELECT name FROM events WHERE id=?", (p["eventId"],)
                        ).fetchone()
                        if p.get("eventId")
                        else None
                    )
                    album = (
                        event[0]
                        if request.template == "year-event" and event
                        else date[:7]
                    )
                    folder = (
                        Path("Organized/Photos")
                        / safe_name(date[:4])
                        / safe_name(album)
                    )
                entries.append(
                    {
                        "contentId": p["contentId"],
                        "instanceId": source["id"],
                        "source": str(Path(source["root_path"]) / source["path"]),
                        "relative": folder / safe_name(Path(source["path"]).name),
                        "size": p["bytes"],
                        "snapshot": {
                            "decision": p["decision"],
                            "decisionVersion": p["decisionVersion"],
                            "reviewVersion": p["reviewVersion"],
                            "groupId": p["groupId"],
                            "sensitive": p["sensitive"],
                            "paths": p["paths"],
                            "evidence": p["evidence"],
                        },
                    }
                )
        # Capacity/path probes occur outside any database transaction.
        free = shutil.disk_usage(destination).free
        run_root = destination / f"PhotoSift-{identifier}"
        collisions, used = 0, set()
        for entry in entries:
            path = run_root / entry.pop("relative")
            candidate, number = path, 1
            while str(candidate).casefold() in used or candidate.exists():
                number += 1
                candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
            if candidate != path:
                collisions += 1
            used.add(str(candidate).casefold())
            entry["destination"] = str(candidate)
        totals = dict(
            photos=len(entries),
            bytes=sum(e["size"] for e in entries),
            freeBytes=free,
            collisions=collisions,
            exclusions=exclusions,
        )
        digest = fingerprint(
            {"entries": entries, "inputs": request.model_dump(), "revision": 1}
        )
        with db.write() as c:
            # Recheck source registrations since destination probes left the read snapshot.
            if any(
                overlap(destination, r[0]) for r in c.execute("SELECT path FROM roots")
            ):
                raise ValueError("Destination overlaps a registered source")
            c.execute(
                "INSERT INTO export_plans(id,created_at,inputs,totals,fingerprint) VALUES (?,?,?,?,?)",
                (
                    identifier,
                    now(),
                    packed(request.model_dump()),
                    packed(totals),
                    digest,
                ),
            )
            c.executemany(
                "INSERT INTO export_entries VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        identifier,
                        e["contentId"],
                        e["instanceId"],
                        e["source"],
                        e["destination"],
                        e["contentId"],
                        e["size"],
                        packed(e["snapshot"]),
                    )
                    for e in entries
                ],
            )
        return identifier
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


def plan(c, identifier, page=1, limit=180, q=""):
    row = c.execute("SELECT * FROM export_plans WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown export plan")
    stale = c.execute(
        "SELECT COUNT(*) FROM export_entries e JOIN photos p ON p.content_id=e.content_id WHERE e.plan_id=? AND p.review_version!=json_extract(e.snapshot,'$.reviewVersion')",
        (identifier,),
    ).fetchone()[0]
    matching = "plan_id=? AND (?='' OR instr(lower(source),lower(?))>0 OR instr(lower(destination),lower(?))>0)"
    scope = (identifier, q, q, q)
    rows_total = c.execute(
        "SELECT COUNT(*) FROM export_entries WHERE " + matching, scope
    ).fetchone()[0]
    rows = [
        dict(r)
        for r in c.execute(
            "SELECT content_id AS contentId,source,destination,sha256,size,snapshot FROM export_entries WHERE "
            + matching
            + " ORDER BY destination LIMIT ? OFFSET ?",
            (*scope, limit, (page - 1) * limit),
        )
    ]
    for item in rows:
        item["snapshot"] = json.loads(item["snapshot"])
    return {
        "id": identifier,
        "revision": row["revision"],
        "createdAt": row["created_at"],
        "inputs": json.loads(row["inputs"]),
        "totals": json.loads(row["totals"]),
        "fingerprint": row["fingerprint"],
        "state": row["state"],
        "staleDecisions": stale,
        "rows": rows,
        "rowsTotal": rows_total,
        "page": page,
        "limit": limit,
        "executorAvailable": True,
    }


def approve(c, identifier, request):
    p = plan(c, identifier)
    if p["revision"] != request.revision or p["fingerprint"] != request.fingerprint:
        raise HTTPException(409, "Approval must match the exact displayed plan")
    previous = c.execute(
        "SELECT id FROM export_runs WHERE plan_id=?", (identifier,)
    ).fetchone()
    if previous:
        return run(c, previous[0])
    if p["state"] != "preview":
        raise HTTPException(409, "Plan no longer accepts approval")
    if p["staleDecisions"]:
        raise HTTPException(409, "Decisions changed; regenerate and inspect a new plan")
    if p["totals"]["bytes"] > p["totals"]["freeBytes"]:
        raise HTTPException(409, "Insufficient capacity at plan creation; regenerate")
    c.execute("UPDATE export_plans SET state='approved' WHERE id=?", (identifier,))
    c.execute(
        "INSERT INTO export_runs(id,plan_id,state,created_at,error) VALUES (?,?,'queued',?,NULL)",
        (identifier, identifier, now()),
    )
    c.execute(
        "INSERT INTO export_operations(run_id,content_id,destination,state) SELECT ?,content_id,destination,'pending' FROM export_entries WHERE plan_id=?",
        (identifier, identifier),
    )
    action_id = action(c, "Approved frozen copy plan", identifier)
    audit(
        c,
        action_id,
        None,
        "export",
        None,
        {
            "planId": identifier,
            "revision": request.revision,
            "fingerprint": request.fingerprint,
        },
    )
    return run(c, identifier)


def run(c, identifier):
    row = c.execute("SELECT * FROM export_runs WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown export run")
    counts = {
        r["state"]: r["n"]
        for r in c.execute(
            "SELECT state,COUNT(*) n FROM export_operations WHERE run_id=? GROUP BY state",
            (identifier,),
        )
    }
    return {
        **dict(row),
        "planId": row["plan_id"],
        "total": sum(counts.values()),
        "copied": counts.get("copied", 0) + counts.get("verified", 0),
        "verified": counts.get("verified", 0),
        "failed": counts.get("failed", 0),
        "executorAvailable": True,
    }


def manifest(c, identifier, format):
    r = run(c, identifier)
    rows = [
        dict(row)
        for row in c.execute(
            "SELECT e.content_id AS contentId,e.source,e.destination,e.sha256,e.size,e.snapshot,o.state,o.output_hash AS outputHash FROM export_entries e JOIN export_operations o ON o.content_id=e.content_id AND o.run_id=? WHERE e.plan_id=? ORDER BY e.destination",
            (identifier, r["planId"]),
        )
    ]
    if format == "csv":
        stream = io.StringIO()
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "contentId",
                "source",
                "destination",
                "sha256",
                "size",
                "snapshot",
                "state",
                "outputHash",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue()
    return {
        "runId": identifier,
        "state": r["state"],
        "verified": bool(rows) and all(row["state"] == "verified" for row in rows),
        "entries": [{**row, "snapshot": json.loads(row["snapshot"])} for row in rows],
    }
