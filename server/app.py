import io
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from server import (
    assessments,
    clear,
    commands,
    embeddings,
    events,
    exports,
    leases,
    pipeline,
    review,
    suggestions,
)
from server import duplicates as duplicate_queries
from server.contracts import (
    AppearanceSettings,
    ApprovalInput,
    BulkInput,
    ClaimInput,
    ClearInput,
    DuplicateQuery,
    EventInput,
    EventPatch,
    ExportInput,
    FailureInput,
    GroupId,
    InventoryQuery,
    LeaseInput,
    MergeInput,
    PhotoPatch,
    PhotoQuery,
    ProtectionSettings,
    ReleaseInput,
    ResultInput,
    RootInput,
    ScanInput,
    SearchQuery,
    SelectionInput,
    SettingsInput,
    SplitInput,
)
from server.db import Database, action, audit, bump, now, packed, revision, uid
from server.paths import checked_path, clean_folder, overlap
from server.preparation_contract import PreparationConfig
from server.queries import (
    BASE,
    CURRENT_PHOTO,
    detail,
    group_stats,
    list_photos,
    photo,
    query_fingerprint,
    rows_by_id,
    validate_scope,
    where,
)
from worker.scanner import queue_scan


def create_app(db_path=None, *, agent_token=None):
    app = FastAPI(title="PhotoSift foundation", version="0.1.0")
    from server.http_limits import BoundedBody

    app.add_middleware(BoundedBody)
    db = Database(db_path or os.environ.get("PHOTOSIFT_DB", ".data/photosift.sqlite3"))
    app.state.db = db
    configured_token = agent_token or os.environ.get("PHOTOSIFT_AGENT_TOKEN")
    token_file = db.path.parent / "agent-api.token"
    # Whole-library counts take seconds at 50k photos and the overview polls them. Every change
    # bumps the durable revision (the worker's too), so an entry can only be reused while exact.
    summaries = {}

    def per_revision(c, name, query, compute):
        key = (name, revision(c), query.model_dump_json())
        if key not in summaries:
            if len(summaries) >= 32:
                summaries.clear()
            summaries[key] = compute()
        return summaries[key]

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request, exception):
        return JSONResponse(
            status_code=409,
            content={"detail": "Conflicting durable state; reload and retry"},
        )

    def agent(authorization: Annotated[str | None, Header()] = None):
        token = configured_token
        if not token and token_file.is_file():
            token = token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise HTTPException(503, "Agent access is not configured")
        if not authorization or not secrets.compare_digest(
            authorization, "Bearer " + token
        ):
            raise HTTPException(401, "Worker authentication required")

    @app.get("/api/status")
    def status():
        with db.read() as c:
            roots = [dict(r) for r in c.execute("SELECT * FROM roots")]
            busy = c.execute(
                "SELECT COUNT(*) FROM bulk_jobs WHERE state!='done'"
            ).fetchone()[0]
            contact = c.execute(
                "SELECT MAX(last_contact) FROM agent_contacts"
            ).fetchone()[0]
        for root in roots:
            try:
                root["available"] = checked_path(root["path"]).is_dir()
            except (OSError, ValueError):
                root["available"] = False
        return {
            "laptop": {
                "state": "online"
                if contact and time.time() - contact < 60
                else "offline",
                "lastContact": datetime.fromtimestamp(contact, timezone.utc).isoformat()
                if contact
                else None,
            },
            "sources": roots,
            "activeBulkJobs": busy,
            "phase": 1,
            "inferenceAvailable": False,
            "exportExecutorAvailable": True,
            "preparationAvailable": True,
        }

    @app.get("/api/roots")
    def roots():
        with db.read() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT r.*,COUNT(f.id) AS files,COALESCE(SUM(f.size),0) AS bytes FROM roots r LEFT JOIN file_instances f ON f.root_id=r.id GROUP BY r.id ORDER BY r.name"
                )
            ]

    @app.get("/api/browse")
    def browse(path: str | None = None):
        """Folders a library can be chosen from. The app has no login, so only these places."""
        setting = os.environ.get("PHOTOSIFT_BROWSE_ROOTS")
        places = [Path(p) for p in setting.split(os.pathsep) if p] if setting else []
        places = places or [Path.home()]
        if path is None:
            return {
                "path": None,
                "parent": None,
                "folders": [
                    {"name": str(place), "path": str(place)}
                    for place in places
                    if place.is_dir()
                ],
            }
        try:
            target = checked_path(path)
            place = next(
                (p for p in places if target.resolve().is_relative_to(p.resolve())),
                None,
            )
            if place is None:
                raise HTTPException(403, "That folder can’t be browsed")
            with os.scandir(target) as entries:
                names = sorted(
                    (
                        entry.name
                        for entry in entries
                        if entry.is_dir(follow_symlinks=False)
                        and not entry.name.startswith((".", "$"))
                    ),
                    key=str.lower,
                )
        except (OSError, ValueError) as exc:
            raise HTTPException(422, "That folder can’t be opened") from exc
        return {
            "path": str(target),
            "parent": None
            if target.resolve() == place.resolve()
            else str(target.parent),
            "folders": [
                {"name": name, "path": str(target / name)} for name in names[:1000]
            ],
        }

    @app.post("/api/roots", status_code=201)
    def add_root(request: RootInput):
        try:
            path = Path(request.path).expanduser()
            if not path.is_absolute():
                raise ValueError("Library path must be absolute")
            if not path.is_dir():
                raise ValueError("That folder doesn’t exist or can’t be opened")
            path = checked_path(path).resolve()
            if overlap(path, db.path.parent):
                raise ValueError("Library and app data must be separate")
            with db.write() as c:
                if any(
                    overlap(path, r[0]) for r in c.execute("SELECT path FROM roots")
                ):
                    raise ValueError("Library roots must not overlap")
                for row in c.execute("SELECT inputs FROM export_plans"):
                    if overlap(path, json.loads(row[0])["destination"]):
                        raise ValueError("Library overlaps a frozen export destination")
                identifier = uid()
                c.execute(
                    "INSERT INTO roots VALUES (?,?,?,?)",
                    (identifier, str(path), request.name, now()),
                )
                bump(c)
            return {"id": identifier, "path": str(path), "name": request.name}
        except (OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/roots/{root_id}/scans", status_code=202)
    def scan_root(root_id: str, request: ScanInput):
        try:
            return {
                "id": queue_scan(db, root_id, request.folder, request.verify),
                "state": "queued",
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/scans/{scan_id}")
    def scan_status(scan_id: str):
        with db.read() as c:
            row = c.execute("SELECT * FROM scan_runs WHERE id=?", (scan_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Unknown scan")
            return dict(row)

    @app.get("/api/folders")
    def folders(root: str):
        with db.read() as c:
            if not c.execute("SELECT 1 FROM roots WHERE id=?", (root,)).fetchone():
                raise HTTPException(404, "Unknown library root")
            rows = c.execute(
                "SELECT path,state,size FROM file_instances WHERE root_id=?", (root,)
            ).fetchall()
            protection = json.loads(
                c.execute(
                    "SELECT value FROM settings WHERE section='protection'"
                ).fetchone()[0]
            )
        tree = {
            "": dict(
                folder="", files=0, bytes=0, unsupported=0, missing=0, unreadable=0
            )
        }
        for row in rows:
            parent = Path(row["path"]).parent
            ancestors = [
                "",
                *[
                    str(p).replace("\\", "/")
                    for p in [parent, *parent.parents]
                    if str(p) != "."
                ],
            ]
            for folder in ancestors:
                node = tree.setdefault(
                    folder,
                    dict(
                        folder=folder,
                        files=0,
                        bytes=0,
                        unsupported=0,
                        missing=0,
                        unreadable=0,
                    ),
                )
                node["files"] += 1
                node["bytes"] += row["size"] or 0
                if row["state"] in ("unsupported", "missing", "unreadable"):
                    node[row["state"]] += 1
        for node in tree.values():
            node["protected"] = any(
                p["root"] == root
                and (
                    not p["folder"]
                    or node["folder"] == p["folder"]
                    or node["folder"].startswith(p["folder"] + "/")
                )
                for p in protection["protectedFolders"]
            )
        return sorted(tree.values(), key=lambda n: n["folder"])

    @app.get("/api/library/stats")
    def library_stats(query: Annotated[PhotoQuery, Query()]):
        with db.read() as c:
            validate_scope(c, query)
            return per_revision(
                c, "library", query, lambda: compute_library_stats(c, query)
            )

    def compute_library_stats(c, query):
        clauses, params = ["1=1"], []
        if query.root:
            clauses.append("root_id=?")
            params.append(query.root)
        if query.folder:
            clauses.append("substr(path,1,?)=?")
            params.extend([len(query.folder) + 1, query.folder + "/"])
        condition = " AND ".join(clauses)
        rows = c.execute(
            "SELECT state,COUNT(*) AS files,COALESCE(SUM(size),0) AS bytes FROM file_instances WHERE "
            + condition
            + " GROUP BY state",
            params,
        ).fetchall()
        scope, scope_params = where(
            query.model_copy(update={"decisions": "all", "reviewed": True})
        )
        summary = c.execute(
            BASE
            + "SELECT MIN(capture_date) AS first,MAX(capture_date) AS last FROM base b WHERE "
            + scope,
            scope_params,
        ).fetchone()
        formats = c.execute(
            BASE
            + "SELECT format,COUNT(*) AS count FROM base b WHERE "
            + scope
            + " GROUP BY format",
            scope_params,
        ).fetchall()
        return {
            "files": sum(r["files"] for r in rows),
            "bytes": sum(r["bytes"] for r in rows),
            "states": [dict(r) for r in rows],
            "formats": [dict(r) for r in formats],
            "dateRange": {"first": summary["first"], "last": summary["last"]},
            "groups": per_revision(c, "groups", query, lambda: group_stats(c, query)),
            "revision": revision(c),
        }

    @app.get("/api/library/files")
    def inventory_files(query: Annotated[InventoryQuery, Query()]):
        with db.read() as c:
            validate_scope(c, query)
            clauses, params = ["1=1"], []
            if query.root:
                clauses.append("root_id=?")
                params.append(query.root)
            if query.folder:
                clauses.append("substr(path,1,?)=?")
                params.extend([len(query.folder) + 1, query.folder + "/"])
            if query.state == "unreadable":
                # A file can pass the scan and still fail to decode during preparation.
                clauses.append(
                    "(state='unreadable' OR EXISTS(SELECT 1 FROM stage_jobs j WHERE j.content_id=file_instances.content_id AND j.state='failed'))"
                )
            elif query.state:
                clauses.append("state=?")
                params.append(query.state)
            condition = " AND ".join(clauses)
            total = c.execute(
                "SELECT COUNT(*) FROM file_instances WHERE " + condition, params
            ).fetchone()[0]
            rows = c.execute(
                "SELECT *,(SELECT j.error FROM stage_jobs j WHERE j.content_id=file_instances.content_id AND j.state='failed' LIMIT 1) AS stageError FROM file_instances WHERE "
                + condition
                + " ORDER BY root_id,path LIMIT ? OFFSET ?",
                [*params, query.limit, (query.page - 1) * query.limit],
            ).fetchall()
            return {
                "items": [dict(r) for r in rows],
                "total": total,
                "page": query.page,
            }

    @app.get("/api/groups")
    def groups(query: Annotated[PhotoQuery, Query()]):
        with db.read() as c:
            validate_scope(c, query)
            items = per_revision(c, "groups", query, lambda: group_stats(c, query))
            return {"items": items, "revision": revision(c)}

    @app.get("/api/photos")
    def photos(query: Annotated[PhotoQuery, Query()]):
        with db.read() as c:
            return list_photos(c, query)

    @app.get("/api/groups/{group_id}/review")
    def group_review(group_id: GroupId, query: Annotated[PhotoQuery, Query()]):
        if query.group and query.group != group_id:
            raise HTTPException(409, "Group does not match the requested scope")
        query = query.model_copy(update={"group": group_id})
        with db.read() as c:
            validate_scope(c, query)
            condition, params = where(query)

            def pick(sql):
                return rows_by_id(c, [r[0] for r in c.execute(BASE + sql, params)])

            sample = pick(
                ", ranked AS (SELECT b.content_id,ROW_NUMBER() OVER (PARTITION BY band,folder ORDER BY random()) sample_rank FROM base b WHERE "
                + condition
                + " AND is_protected=0) SELECT content_id FROM ranked ORDER BY sample_rank,random() LIMIT 24"
            )
            borderline = pick(
                "SELECT b.content_id FROM base b WHERE "
                + condition
                + " AND is_protected=0 AND band='low' ORDER BY CASE WHEN instr(evidence,'small_margin')>0 THEN 0 ELSE 1 END,content_id LIMIT 24"
            )
            protected = pick(
                "SELECT b.content_id FROM base b WHERE "
                + condition
                + " AND is_protected=1 ORDER BY content_id LIMIT 24"
            )
            counts = c.execute(
                BASE
                + "SELECT COUNT(*) total,COALESCE(SUM(is_protected),0) protected,COALESCE(SUM(is_protected=0 AND band='low'),0) borderline FROM base b WHERE "
                + condition,
                params,
            ).fetchone()
            return {
                "borderlineCount": counts["borderline"],
                "queryFingerprint": query_fingerprint(query, revision(c)),
                "sample": [photo(c, r) for r in sample],
                "borderline": [photo(c, r) for r in borderline],
                "protected": [photo(c, r) for r in protected],
                "total": counts["total"],
                "protectedCount": counts["protected"],
                "revision": revision(c),
            }

    @app.get("/api/photos/{content_id}")
    def photo_detail(content_id: str):
        with db.read() as c:
            p = detail(c, content_id)
            p["history"] = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM audit_log WHERE content_id=? ORDER BY id DESC LIMIT 100",
                    (content_id,),
                )
            ]
            return p

    @app.patch("/api/photos/{content_id}")
    def patch_photo(content_id: str, request: PhotoPatch):
        with db.write() as c:
            return review.patch_photo(c, content_id, request)

    @app.post("/api/selections", status_code=201)
    def freeze_selection(request: SelectionInput):
        with db.write() as c:
            return review.freeze(c, request)

    @app.post("/api/bulk", status_code=202)
    def bulk(request: BulkInput):
        with db.write() as c:
            return review.enqueue_bulk(c, request)

    @app.get("/api/jobs/{identifier}")
    def job(identifier: str):
        with db.read() as c:
            if c.execute(
                "SELECT 1 FROM undo_jobs WHERE id=?", (identifier,)
            ).fetchone():
                return review.undo_job(c, identifier)
            return review.job(c, identifier)

    @app.get("/api/history")
    def history(page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=500)):
        with db.read() as c:
            rows = c.execute(
                "SELECT a.*,COUNT(l.id) AS count,EXISTS(SELECT 1 FROM audit_log x WHERE x.action_id=a.id AND x.kind IN ('decision','group','event')) AND NOT EXISTS(SELECT 1 FROM actions u WHERE u.undo_of=a.id) AS undoable FROM actions a LEFT JOIN audit_log l ON l.action_id=a.id GROUP BY a.id ORDER BY a.created_at DESC LIMIT ? OFFSET ?",
                (limit, (page - 1) * limit),
            ).fetchall()
            return {
                "items": [dict(r) for r in rows],
                "total": c.execute("SELECT COUNT(*) FROM actions").fetchone()[0],
            }

    @app.get("/api/jobs/{identifier}/items")
    def job_items(
        identifier: str,
        page: int = Query(1, ge=1),
        limit: int = Query(100, ge=1, le=500),
        state: str | None = None,
    ):
        with db.read() as c:
            condition, params = "i.job_id=?", [identifier]
            if state:
                condition += " AND i.state=?"
                params.append(state)
            if c.execute(
                "SELECT 1 FROM undo_jobs WHERE id=?", (identifier,)
            ).fetchone():
                table = "undo_items i JOIN audit_log a ON a.id=i.audit_id"
                fields = "a.content_id AS contentId,i.state,i.reason"
                order = "a.content_id"
            elif c.execute(
                "SELECT 1 FROM bulk_jobs WHERE id=?", (identifier,)
            ).fetchone():
                table, fields, order = (
                    "bulk_items i",
                    "i.content_id AS contentId,i.state,i.reason",
                    "i.content_id",
                )
            else:
                raise HTTPException(404, "Unknown durable job")
            total = c.execute(
                "SELECT COUNT(*) FROM " + table + " WHERE " + condition, params
            ).fetchone()[0]
            rows = c.execute(
                "SELECT "
                + fields
                + " FROM "
                + table
                + " WHERE "
                + condition
                + " ORDER BY "
                + order
                + " LIMIT ? OFFSET ?",
                [*params, limit, (page - 1) * limit],
            ).fetchall()
            return {"items": [dict(r) for r in rows], "total": total, "page": page}

    @app.post("/api/history/{identifier}/undo")
    def undo(identifier: str):
        with db.write() as c:
            result = review.undo(c, identifier)
        return JSONResponse(
            status_code=200 if result["state"] == "done" else 202, content=result
        )

    @app.get("/api/events")
    def event_list(query: Annotated[PhotoQuery, Query()]):
        with db.read() as c:
            validate_scope(c, query)
            return {"items": events.list_events(c, query), "revision": revision(c)}

    @app.post("/api/events", status_code=201)
    def create_event(request: EventInput):
        with db.write() as c:
            return events.create_event(c, request)

    @app.get("/api/events/{identifier}")
    def event_detail(identifier: str):
        with db.read() as c:
            return events.get_event(c, identifier)

    @app.patch("/api/events/{identifier}")
    def edit_event(identifier: str, request: EventPatch):
        with db.write() as c:
            return events.patch_event(c, identifier, request)

    @app.post("/api/events/{identifier}/merge")
    def merge_event(identifier: str, request: MergeInput):
        with db.write() as c:
            return events.merge(c, identifier, request)

    @app.post("/api/events/{identifier}/split")
    def split_event(identifier: str, request: SplitInput):
        with db.write() as c:
            return events.split(c, identifier, request)

    @app.get("/api/duplicates")
    def duplicates(query: Annotated[DuplicateQuery, Query()]):
        with db.read() as c:
            return duplicate_queries.list_sets(c, query)

    @app.get("/api/stages")
    def stages(query: Annotated[PhotoQuery, Query()]):
        with db.read() as c:
            validate_scope(c, query)
            condition, params = where(
                query.model_copy(update={"decisions": "all", "reviewed": True})
            )
            # Every open tab polls this. Across the whole library it needs none of the per-photo
            # work in BASE, which took 3 seconds on 48,000 photos on an N150.
            whole_library = condition == CURRENT_PHOTO
            stage_rows = c.execute(
                "SELECT j.stage,j.state,COUNT(*) n FROM stage_jobs j WHERE EXISTS(SELECT 1 FROM file_instances current WHERE current.content_id=j.content_id) GROUP BY j.stage,j.state"
                if whole_library
                else BASE
                + "SELECT j.stage,j.state,COUNT(*) n FROM stage_jobs j JOIN base b ON b.content_id=j.content_id WHERE "
                + condition
                + " GROUP BY j.stage,j.state",
                params,
            ).fetchall()
            scans = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM scan_runs WHERE (? IS NULL OR root_id=?) AND (?='' OR folder=?) ORDER BY started_at DESC LIMIT 100",
                    (query.root, query.root, query.folder, query.folder),
                )
            ]
            control = c.execute(
                "SELECT value FROM settings WHERE section='analyze'"
            ).fetchone()
            control = json.loads(control[0]) if control else {"state": "running"}
            indexes = [
                {"id": row["id"], "role": row["role"], "status": row["status"]}
                for row in c.execute("SELECT id,role,status FROM embedding_indexes")
            ]
            primary = c.execute(
                "SELECT id FROM embedding_indexes WHERE role='primary' AND status='ready'"
            ).fetchone()
            prep_configured = bool(
                c.execute(
                    "SELECT 1 FROM stage_specs WHERE stage='prepare' AND active=1"
                ).fetchone()
            )
            runner = pipeline.preparation_runner(c)
            embed_configured = embeddings.active_index(c) is not None
            assess_configured = assessments.active_spec(c) is not None
            grouped = (
                c.execute(
                    BASE
                    + "SELECT COUNT(*) AS total,COALESCE(SUM(b.group_id!='unclassified'),0) AS done FROM base b WHERE "
                    + condition
                    + " AND b.decision='undecided'",
                    params,
                ).fetchone()
                if primary
                else None
            )
            inventory_counts = {
                r["state"]: r["n"]
                for r in c.execute(
                    "SELECT state,COUNT(*) n FROM file_instances WHERE (? IS NULL OR root_id=?) AND (?='' OR substr(path,1,?)=?) GROUP BY state",
                    (
                        query.root,
                        query.root,
                        query.folder,
                        len(query.folder) + 1,
                        query.folder + "/",
                    ),
                )
            }
        result = []
        for stage in ("inventory", "prepare", "embed", "assess", "group"):
            # Skipped jobs were superseded or cancelled; they are not work still owed.
            counts = {r["state"]: r["n"] for r in stage_rows if r["stage"] == stage}
            total = sum(n for state, n in counts.items() if state != "skipped")
            done = counts.get("done", 0)
            state = (
                "done"
                if total and done == total
                else "waiting_laptop"
                if stage in ("prepare", "embed", "assess")
                else "model_unavailable"
            )
            here = stage == "prepare" and runner == "server"
            if here and state == "waiting_laptop":
                state = "waiting"
            if control["state"] in ("paused", "cancelled"):
                state = "paused"
            if counts.get("failed"):
                state = "failed"
            elif counts.get("running"):
                state = "running"
            elif (
                stage == "prepare"
                and not prep_configured
                or stage == "assess"
                and not assess_configured
                or stage == "embed"
                and not embed_configured
                and not total
            ):
                state = "model_unavailable"
            if stage == "group" and primary:
                # Grouping has no jobs: it runs when the owner asks, over photos still undecided.
                total, done = grouped["total"], grouped["done"]
                state = "done" if done == total else "paused"
            if stage == "inventory":
                counts = inventory_counts
                total = sum(counts.values())
                done = total - counts.get("missing", 0)
                latest_by_root = {}
                for scan_run in scans:
                    latest_by_root.setdefault(scan_run["root_id"], scan_run)
                latest_states = {s["state"] for s in latest_by_root.values()}
                state = (
                    "running"
                    if latest_states & {"running", "queued"}
                    else "failed"
                    if latest_states & {"partial", "unavailable"}
                    else "paused"
                    if latest_states & {"paused", "cancelled"}
                    else "done"
                )
            result.append(
                {
                    "name": stage,
                    "done": done,
                    "total": total,
                    "ratePerMin": None,
                    "location": "laptop"
                    if stage in ("prepare", "embed", "assess") and not here
                    else "zima",
                    "state": state,
                    "counts": counts,
                }
            )
        return {
            "items": result,
            "scans": scans,
            "control": control,
            "primaryIndex": primary[0] if primary else None,
            "indexes": indexes,
            "preparationConfigured": prep_configured,
            "preparationRunner": runner,
            "d3": {
                "endpoint": "/v1/systemone",
                "schema": "ollama-systemone",
                "state": "configured" if assess_configured else "unconfigured",
            },
        }

    @app.post("/api/analyze/{operation}")
    def analyze_control(operation: str):
        if operation not in ("pause", "resume", "cancel", "retry"):
            raise HTTPException(404, "Unknown analyze operation")
        with db.write() as c:
            state = {
                "pause": "paused",
                "resume": "running",
                "cancel": "cancelled",
                "retry": "running",
            }[operation]
            c.execute(
                "INSERT INTO settings(section,value) VALUES ('analyze',?) ON CONFLICT(section) DO UPDATE SET value=excluded.value,version=version+1",
                (packed({"state": state}),),
            )
            if operation == "pause":
                c.execute(
                    "UPDATE stage_jobs SET state='paused',token=NULL,expires=NULL WHERE state IN ('queued','running')"
                )
                c.execute(
                    "UPDATE scan_runs SET state='paused',token=NULL,expires=NULL WHERE state IN ('queued','running')"
                )
            elif operation == "cancel":
                c.execute(
                    "UPDATE stage_jobs SET state='skipped',error='Cancelled by owner',token=NULL,expires=NULL WHERE state IN ('queued','running','paused')"
                )
                c.execute(
                    "UPDATE scan_runs SET state='cancelled',token=NULL,expires=NULL WHERE state IN ('queued','running','paused')"
                )
            elif operation == "resume":
                # Also "Start again" after Cancel: what Cancel set aside is owed again.
                c.execute(
                    "UPDATE stage_jobs SET state='queued',error=NULL WHERE state='paused' OR state='skipped' AND error='Cancelled by owner' AND fingerprint IN (SELECT fingerprint FROM stage_specs WHERE active=1)"
                )
                c.execute(
                    "UPDATE scan_runs SET state='queued',error=NULL WHERE state='paused' OR state='cancelled' AND NOT EXISTS(SELECT 1 FROM scan_runs newer WHERE newer.root_id=scan_runs.root_id AND newer.started_at>scan_runs.started_at)"
                )
            else:
                # A job of a replaced contract can never be claimed, so it isn't retried.
                c.execute(
                    "UPDATE stage_jobs SET state='queued',error=NULL,token=NULL,expires=NULL WHERE state='failed' AND fingerprint IN (SELECT fingerprint FROM stage_specs WHERE active=1)"
                )
                failed_scans = c.execute(
                    "SELECT * FROM scan_runs s WHERE state IN ('partial','unavailable') AND NOT EXISTS(SELECT 1 FROM scan_runs newer WHERE newer.root_id=s.root_id AND newer.started_at>s.started_at) AND NOT EXISTS(SELECT 1 FROM scan_runs active WHERE active.root_id=s.root_id AND active.state IN ('queued','running','paused'))"
                ).fetchall()
                for failed in failed_scans:
                    c.execute(
                        "INSERT INTO scan_runs(id,root_id,folder,verify,state,started_at) VALUES (?,?,?,?,'queued',?)",
                        (
                            uid(),
                            failed["root_id"],
                            failed["folder"],
                            failed["verify"],
                            now(),
                        ),
                    )
            action_id = action(c, "Analyze " + operation, "stage jobs")
            audit(c, action_id, None, "analyze", None, {"state": state})
        return {"state": state}

    @app.get("/api/search")
    def search(query: Annotated[SearchQuery, Query()]):
        if query.like:
            with db.read() as c:
                return embeddings.similar(c, query.like, query)
        with db.read() as c:
            return {
                **list_photos(c, query),
                "mode": "filename-and-path",
                "semanticAvailable": False,
            }

    @app.get("/api/settings")
    def all_settings():
        with db.read() as c:
            return {
                r["section"]: {"value": json.loads(r["value"]), "version": r["version"]}
                for r in c.execute("SELECT * FROM settings")
            }

    @app.get("/api/settings/{section}")
    def get_settings(section: str):
        with db.read() as c:
            row = c.execute(
                "SELECT * FROM settings WHERE section=?", (section,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "Unknown settings section")
            return {"value": json.loads(row["value"]), "version": row["version"]}

    @app.patch("/api/settings/{section}")
    def patch_settings(section: str, request: SettingsInput):
        from pydantic import ValidationError

        if section not in (
            "protection",
            "appearance",
            "setup",
            "export-defaults",
            "categories",
            "thresholds",
        ):
            raise HTTPException(
                501, "This configuration needs its later validated adapter"
            )
        try:
            value = request.value
            if section == "protection":
                value = ProtectionSettings.model_validate(value).model_dump()
                if value["ageBefore"]:
                    from datetime import date

                    date.fromisoformat(value["ageBefore"])
                for folder in value["protectedFolders"]:
                    if set(folder) != {"root", "folder"}:
                        raise ValueError("Protected folders require root and folder")
                    folder["folder"] = clean_folder(folder["folder"])
            elif section == "appearance":
                value = AppearanceSettings.model_validate(value).model_dump()
            elif section == "setup" and (
                set(value) != {"complete"} or not isinstance(value["complete"], bool)
            ):
                raise ValueError("Setup requires complete: boolean")
        except (ValidationError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        with db.write() as c:
            previous = c.execute(
                "SELECT * FROM settings WHERE section=?", (section,)
            ).fetchone()
            if request.expectedVersion != (previous["version"] if previous else 0):
                raise HTTPException(409, "Settings changed; reload")
            if section == "protection":
                for folder in value["protectedFolders"]:
                    if not c.execute(
                        "SELECT 1 FROM roots WHERE id=?", (folder["root"],)
                    ).fetchone():
                        raise HTTPException(422, "Unknown protected library root")
                c.execute("UPDATE meta SET value=value+1 WHERE key='policyRevision'")
                bump(c)
            c.execute(
                "INSERT INTO settings VALUES (?,?,1) ON CONFLICT(section) DO UPDATE SET value=excluded.value,version=version+1",
                (section, packed(value)),
            )
            action_id = action(c, "Update settings", section)
            audit(
                c,
                action_id,
                None,
                "settings",
                json.loads(previous["value"]) if previous else None,
                value,
            )
            return {"value": value, "version": request.expectedVersion + 1}

    @app.get("/api/export/plans")
    def export_plans(page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=500)):
        with db.read() as c:
            return {
                "items": [
                    dict(r)
                    for r in c.execute(
                        "SELECT id,revision,created_at,state,totals FROM export_plans ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (limit, (page - 1) * limit),
                    )
                ]
            }

    @app.post("/api/export/plans", status_code=201)
    def export_plan(request: ExportInput):
        identifier = exports.create_plan(db, request)
        with db.read() as c:
            return exports.plan(c, identifier)

    @app.get("/api/export/plans/{identifier}")
    def get_plan(
        identifier: str,
        page: int = Query(1, ge=1),
        limit: int = Query(180, ge=1, le=500),
        q: str = Query("", max_length=1000),
    ):
        with db.read() as c:
            return exports.plan(c, identifier, page, limit, q)

    @app.post("/api/export/plans/{identifier}/approve", status_code=202)
    def approve_plan(identifier: str, request: ApprovalInput):
        with db.write() as c:
            return exports.approve(c, identifier, request)

    @app.get("/api/export/runs")
    def export_runs():
        with db.read() as c:
            return {
                "items": [
                    exports.run(c, r[0])
                    for r in c.execute(
                        "SELECT id FROM export_runs ORDER BY created_at DESC LIMIT 100"
                    )
                ]
            }

    @app.get("/api/export/runs/{identifier}")
    def export_run(identifier: str):
        with db.read() as c:
            return exports.run(c, identifier)

    @app.get("/api/export/runs/{identifier}/manifest")
    def manifest(identifier: str, format: str = "json"):
        if format not in ("json", "csv"):
            raise HTTPException(422, "Manifest format must be json or csv")
        with db.read() as c:
            value = exports.manifest(c, identifier, format)
        return (
            Response(
                value,
                media_type="text/csv",
                headers={
                    "Content-Disposition": f'attachment; filename="{identifier}-manifest.csv"'
                },
            )
            if format == "csv"
            else value
        )

    @app.post("/api/export/runs/{identifier}/{operation}")
    def export_operation(identifier: str, operation: str):
        with db.write() as c:
            current = exports.run(c, identifier)
            if operation == "pause" and current["state"] in ("queued", "running"):
                state = "paused"
            elif operation in ("resume", "retry") and current["state"] in (
                "paused",
                "failed",
                "unavailable",
            ):
                state = "queued"
                c.execute(
                    "UPDATE export_operations SET state=CASE WHEN output_hash IS NOT NULL AND temporary_identity IS NOT NULL THEN 'ready' ELSE 'pending' END,error=NULL WHERE run_id=? AND state='failed'",
                    (identifier,),
                )
            elif operation == "rollback" and current["state"] in (
                "done",
                "failed",
                "paused",
                "queued",
                "running",
            ):
                state = "rollback-requested"
            else:
                raise HTTPException(
                    409, "Export control does not apply to the current run state"
                )
            c.execute(
                "UPDATE export_runs SET state=?,error=NULL WHERE id=?",
                (state, identifier),
            )
            action_id = action(c, "Export " + operation, identifier)
            audit(c, action_id, None, "export_control", current["state"], state)
            return exports.run(c, identifier)

    @app.get("/api/export/runs/{identifier}/journal")
    def export_journal(
        identifier: str,
        page: int = Query(1, ge=1),
        limit: int = Query(100, ge=1, le=500),
    ):
        with db.read() as c:
            exports.run(c, identifier)
            return {
                "items": [
                    dict(row)
                    for row in c.execute(
                        "SELECT * FROM export_journal WHERE run_id=? ORDER BY id LIMIT ? OFFSET ?",
                        (identifier, limit, (page - 1) * limit),
                    )
                ],
                "total": c.execute(
                    "SELECT COUNT(*) FROM export_journal WHERE run_id=?", (identifier,)
                ).fetchone()[0],
            }

    @app.get("/api/media/{identifier}")
    def media(identifier: str, reveal: bool = False, placeholder: bool = False):
        with db.read() as c:
            row = c.execute(
                "SELECT * FROM cached_media WHERE id=?", (identifier,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "Media has not been prepared")
            p = detail(c, row["content_id"])
            if p["blurRequired"] and not reveal and not placeholder:
                raise HTTPException(
                    403, "Explicit reveal required for restricted media"
                )
            path = Path(row["path"]).resolve()
        if not path.is_relative_to(db.path.parent.resolve() / "cache"):
            raise HTTPException(403, "Invalid cached media boundary")
        if placeholder and not reveal:
            # A blurred tile needs colours, not detail: 12 pixels cannot expose a restricted photo.
            from PIL import Image

            with Image.open(path) as image:
                image.thumbnail((12, 12))
                output = io.BytesIO()
                image.convert("RGB").save(output, "WEBP", quality=40)
            return Response(
                output.getvalue(),
                media_type="image/webp",
                headers={"Cache-Control": "no-store"},
            )
        # Cache files carry no extension to infer a type from; the agent always writes WebP.
        return FileResponse(
            path, media_type="image/webp", headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/agent/claim", dependencies=[Depends(agent)])
    def claim(request: ClaimInput):
        with db.write() as c:
            return leases.claim(c, request)

    @app.get("/api/laptop/commands")
    def laptop_commands():
        with db.read() as c:
            return commands.latest(c)

    @app.post("/api/laptop/commands")
    def ask_laptop(request: commands.CommandRequest):
        with db.write() as c:
            return commands.ask(c, request.kind)

    @app.post("/api/agent/commands/claim", dependencies=[Depends(agent)])
    def claim_command():
        with db.write() as c:
            return commands.claim(c)

    @app.post("/api/agent/commands/{identifier}/finish", dependencies=[Depends(agent)])
    def finish_command(identifier: str, request: commands.CommandResult):
        with db.write() as c:
            return commands.finish(c, identifier, request)

    @app.post("/api/agent/jobs/{identifier}/renew", dependencies=[Depends(agent)])
    def renew(identifier: str, request: LeaseInput):
        with db.write() as c:
            return leases.renew(c, identifier, request)

    @app.post("/api/agent/jobs/{identifier}/source", dependencies=[Depends(agent)])
    def job_source(identifier: str, request: LeaseInput):
        # Only the original of a job this agent currently holds, read-only. The agent checks
        # its hash, so a laptop with no path to the library needs no file share.
        with db.read() as c:
            job = leases.valid_lease(c, identifier, request)
            source = c.execute(
                "SELECT f.*,r.path AS root_path FROM file_instances f JOIN roots r ON r.id=f.root_id WHERE f.id=?",
                (job["source_instance"],),
            ).fetchone()
        if (
            not source
            or source["state"] != "available"
            or (source["size"], source["mtime_ns"])
            != (job["source_size"], job["source_mtime_ns"])
        ):
            raise HTTPException(409, "Source superseded; rescan required")
        try:
            path = checked_path(source["root_path"], source["path"])
            found = path.stat()
        except (OSError, ValueError) as exc:
            raise HTTPException(409, "Source is not readable on the server") from exc
        if (found.st_size, found.st_mtime_ns) != (source["size"], source["mtime_ns"]):
            raise HTTPException(409, "Source changed; rescan required")
        return FileResponse(
            path,
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/agent/jobs/{identifier}/release", dependencies=[Depends(agent)])
    def release(identifier: str, request: ReleaseInput):
        with db.write() as c:
            return leases.release(c, identifier, request)

    @app.post("/api/agent/jobs/{identifier}/result", dependencies=[Depends(agent)])
    def result(identifier: str, request: ResultInput):
        with db.read() as c:
            job = c.execute(
                "SELECT stage FROM stage_jobs WHERE id=?", (identifier,)
            ).fetchone()
        if job and job["stage"] == "assess":
            with db.write() as c:
                return assessments.accept(c, identifier, request)
        if job and job["stage"] == "embed":
            from pydantic import ValidationError

            try:
                with db.write() as c:
                    return embeddings.accept(c, identifier, request)
            except ValidationError as exc:
                raise HTTPException(422, str(exc)) from exc
        return pipeline.submit_preparation(db, identifier, request)

    @app.get("/api/indexes")
    def embedding_indexes():
        with db.read() as c:
            return {
                "items": [
                    embeddings.get_index(c, row[0])
                    for row in c.execute("SELECT id FROM embedding_indexes")
                ]
            }

    @app.post("/api/indexes")
    def register_index(request: embeddings.IndexConfig):
        with db.write() as c:
            return embeddings.register(c, request)

    @app.post("/api/indexes/{identifier}/queue")
    def queue_embeddings(identifier: str, request: embeddings.QueueEmbeddings):
        with db.write() as c:
            return embeddings.queue(c, identifier, request)

    @app.get("/api/comparison")
    def comparison():
        with db.read() as c:
            return embeddings.overview(c)

    @app.post("/api/database/clear")
    def clear_database(request: ClearInput):
        return clear.clear(db)

    @app.get("/api/database/space")
    def database_space():
        return clear.space(db)

    @app.post("/api/database/compact")
    def compact_database():
        return clear.compact(db)

    @app.post("/api/comparison/labels")
    def comparison_labels(request: embeddings.ComparisonSample):
        with db.write() as c:
            return embeddings.add_labels(c, request)

    @app.post("/api/comparison/samples")
    def comparison_sample(request: embeddings.ComparisonSample):
        with db.write() as c:
            return embeddings.save_sample(c, request)

    @app.post("/api/indexes/{identifier}/prompts")
    def index_prompts(identifier: str, request: embeddings.PromptPack):
        with db.write() as c:
            return embeddings.save_prompts(c, identifier, request)

    @app.post("/api/indexes/{identifier}/evaluate")
    def evaluate_index(identifier: str, request: embeddings.EvaluationRequest):
        with db.write() as c:
            return embeddings.evaluate(c, identifier, request)

    @app.post("/api/indexes/{identifier}/promote")
    def promote_index(identifier: str, request: embeddings.PromoteIndex):
        with db.write() as c:
            return embeddings.promote(c, identifier, request)

    @app.post("/api/pipeline/preparation/configure")
    def configure_preparation(request: PreparationConfig):
        with db.write() as c:
            return pipeline.configure_preparation(c, request)

    @app.get("/api/pipeline/preparation")
    def preparation_place():
        with db.read() as c:
            return {"runner": pipeline.preparation_runner(c)}

    @app.post("/api/pipeline/preparation/run-here")
    def prepare_here():
        config = pipeline.local_preparation()
        if not config:
            raise HTTPException(503, "This server can't prepare photos itself")
        with db.write() as c:
            return pipeline.configure_preparation(c, config)

    @app.get("/api/pipeline/assessment")
    def active_assessment():
        with db.read() as c:
            return assessments.active_spec(c)

    @app.post("/api/pipeline/assessment/configure")
    def configure_assessment(request: assessments.AssessmentConfig):
        with db.write() as c:
            return assessments.configure(c, request)

    @app.get("/api/pipeline/embedding")
    def active_embedding():
        with db.read() as c:
            index = embeddings.active_index(c)
        if not index:
            return None
        return {"indexId": index["id"], "fingerprint": index["fingerprint"]}

    @app.post("/api/pipeline/embedding/configure")
    def configure_embedding(request: embeddings.IndexConfig):
        with db.write() as c:
            return embeddings.configure(c, request)

    @app.post("/api/agent/jobs/{identifier}/fail", dependencies=[Depends(agent)])
    def fail_stage(identifier: str, request: FailureInput):
        with db.write() as c:
            return leases.fail(c, identifier, request)

    @app.get("/api/photos/{content_id}/related")
    def related_photos(content_id: str, query: Annotated[PhotoQuery, Query()]):
        with db.read() as c:
            validate_scope(c, query)
            p = detail(c, content_id)
            condition, params = where(query)
            rows = c.execute(
                BASE
                + "SELECT b.* FROM base b WHERE "
                + condition
                + " AND content_id!=? AND ((duplicate_set IS NOT NULL AND duplicate_set=? AND group_id=?) OR (event_id IS NOT NULL AND event_id=?)) ORDER BY content_id LIMIT 24",
                [
                    *params,
                    content_id,
                    p.get("duplicateSetId"),
                    p["groupId"],
                    p.get("eventId"),
                ],
            ).fetchall()
            return {"items": [photo(c, row) for row in rows]}

    # Serve the built client from the same origin; unknown API paths must remain JSON 404s.
    @app.post("/api/pipeline/score")
    def score_content(request: suggestions.ScoringRequest):
        with db.write() as c:
            return suggestions.score(c, request)

    @app.post("/api/pipeline/suggest-neighbors")
    def suggest_neighbors(request: suggestions.NeighborRequest):
        with db.write() as c:
            return suggestions.neighbors(c, request)

    @app.post("/api/pipeline/suggest-events")
    def suggest_events(request: suggestions.EventSuggestionRequest):
        with db.write() as c:
            return suggestions.events(c, request)

    @app.get("/api/pipeline/jobs")
    def pipeline_jobs(
        root: str | None = None,
        folder: str = "",
        stage: str | None = None,
        state: str | None = None,
        page: int = Query(1, ge=1),
        limit: int = Query(100, ge=1, le=500),
    ):
        try:
            scope = PhotoQuery(root=root, folder=folder, decisions="all")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with db.read() as c:
            validate_scope(c, scope)
            condition, params = where(scope)
            if stage:
                condition += " AND j.stage=?"
                params.append(stage)
            if state:
                condition += " AND j.state=?"
                params.append(state)
            source = (
                " FROM stage_jobs j JOIN base b ON b.content_id=j.content_id WHERE "
                + condition
            )
            total = c.execute(BASE + "SELECT COUNT(*)" + source, params).fetchone()[0]
            rows = c.execute(
                BASE
                + "SELECT j.id,j.content_id AS contentId,b.filename,b.size,j.stage,j.fingerprint,j.state,j.error,j.attempt,j.owner,j.expires"
                + source
                + " ORDER BY j.id LIMIT ? OFFSET ?",
                [*params, limit, (page - 1) * limit],
            ).fetchall()
            return {"items": [dict(row) for row in rows], "total": total, "page": page}

    distribution = Path(__file__).resolve().parents[1] / "web" / "dist"
    if (distribution / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=distribution / "assets"),
            name="frontend-assets",
        )

    @app.get("/{client_path:path}", include_in_schema=False)
    def frontend(client_path: str):
        if client_path == "api" or client_path.startswith("api/"):
            raise HTTPException(404, "Unknown API endpoint")
        if (
            client_path == "photosift-mark.svg"
            and (distribution / client_path).is_file()
        ):
            return FileResponse(distribution / client_path)
        if not (distribution / "index.html").is_file():
            raise HTTPException(
                404, "Build the frontend first, or use the Vite development server"
            )
        return FileResponse(
            distribution / "index.html", headers={"Cache-Control": "no-store"}
        )

    return app
