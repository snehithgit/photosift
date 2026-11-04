import json
from pathlib import Path

from fastapi import HTTPException

from server.contracts import Photo, PhotoQuery
from server.db import fingerprint, revision

GROUPS = [
    ("screenshots", "Screenshots", "Information", "▧"),
    ("memes", "Memes and greetings", "From elsewhere", "▣"),
    ("events", "Events", "Personal", "◎"),
    ("documents", "Documents", "Information", "▤"),
    ("duplicates", "Duplicates and bursts", "Cleanup", "◫"),
    ("badshots", "Bad shots", "Cleanup", "◌"),
    ("places", "Places", "Personal", "⌖"),
    ("people", "People and family", "Personal", "◉"),
    ("other", "Other", "Other", "◍"),
    ("unclassified", "Unclassified", "Other", "？"),
]

# Effective protections are computed before filtering/counting, so folder/Keep changes cannot
# leave stale protected flags in selections. Unknown assessment is a suggestion hold, not 'No'.
# The protected-folder list is read once per statement; without folders no file lookup runs.
BASE = """
WITH protected_folder AS MATERIALIZED (
 SELECT json_extract(pf.value,'$.root') AS root,json_extract(pf.value,'$.folder') AS folder
 FROM json_each((SELECT json_extract(value,'$.protectedFolders') FROM settings WHERE section='protection')) pf
),
base AS (
 SELECT p.*,c.size,d.decision,d.version AS decision_version,
 COALESCE(g.manual_group,g.ai_group) AS group_id,
 CASE WHEN g.manual_group IS NULL THEN 'ai' ELSE 'manual' END AS group_source,
 em.event_id,
 (SELECT COUNT(*) FROM file_instances f WHERE f.content_id=c.id AND f.state='available') AS copies,
 CASE WHEN d.decision='keep' OR json_array_length(p.protection)>0 OR p.sensitive='yes' OR p.importance='yes'
 OR p.faces!='"not_checked"' AND json_array_length(p.faces)>0
 OR COALESCE(g.manual_group,g.ai_group)='documents' OR p.document_type='IDs'
 OR EXISTS(SELECT 1 FROM protected_folder pf JOIN file_instances f ON f.content_id=c.id AND f.root_id=pf.root
 WHERE pf.folder='' OR f.path=pf.folder OR substr(f.path,1,length(pf.folder)+1)=pf.folder||'/')
 OR p.capture_source IN ('exif','filename') AND p.capture_date < (SELECT json_extract(value,'$.ageBefore') FROM settings WHERE section='protection')
 THEN 1 ELSE 0 END AS is_protected,
 CASE WHEN EXISTS(SELECT 1 FROM file_instances f WHERE f.content_id=c.id AND state='available') THEN 'available'
 WHEN EXISTS(SELECT 1 FROM file_instances f WHERE f.content_id=c.id AND state='unreadable') THEN 'unreadable'
 WHEN EXISTS(SELECT 1 FROM file_instances f WHERE f.content_id=c.id AND state='unsupported') THEN 'unsupported' ELSE 'missing' END AS file_state
 FROM photos p JOIN contents c ON c.id=p.content_id JOIN decisions d ON d.content_id=c.id
 JOIN group_assignments g ON g.content_id=c.id LEFT JOIN event_members em ON em.content_id=c.id
)
"""


CURRENT_PHOTO = (
    "EXISTS(SELECT 1 FROM file_instances current WHERE current.content_id=b.content_id)"
)


def where(query: PhotoQuery):
    # Superseded content stays in history/results, but is no longer a current photo.
    clauses, params = [CURRENT_PHOTO], []
    if query.root:
        clause = "EXISTS(SELECT 1 FROM file_instances f WHERE f.content_id=b.content_id AND f.root_id=?"
        params.append(query.root)
        if query.folder:
            clause += " AND substr(f.path,1,?)=?"
            params.extend([len(query.folder) + 1, query.folder + "/"])
        clauses.append(clause + ")")
    for field, column, skip in [
        ("group", "group_id", None),
        ("event", "event_id", None),
        ("band", "band", "all"),
        ("origin", "origin", "all"),
    ]:
        value = getattr(query, field)
        if value is not None and value != skip:
            clauses.append(f"b.{column}=?")
            params.append(value)
    if query.format != "all":
        clauses.append("lower(b.format)=lower(?)")
        params.append(query.format)
    if query.documentType:
        # A document not yet sorted into a subgroup is listed under Papers, never lost.
        clauses.append("COALESCE(b.document_type,'Papers')=?")
        params.append(query.documentType)
    decision = "all" if query.reviewed else query.decisions
    if decision != "all":
        clauses.append("b.decision=?")
        params.append(decision)
    if query.hideSetAside:
        clauses.append("b.decision!='clutter'")
    if query.unlabelled:
        clauses.append(
            "b.content_id NOT IN (SELECT json_extract(m.value,'$.contentId') FROM json_each((SELECT members FROM comparison_samples ORDER BY created_at DESC,rowid DESC LIMIT 1)) m)"
        )
    if query.q:
        clauses.append(
            "(instr(lower(b.filename),lower(?))>0 OR EXISTS(SELECT 1 FROM file_instances f JOIN roots r ON r.id=f.root_id WHERE f.content_id=b.content_id AND instr(lower(r.path||'/'||f.path),lower(?))>0))"
        )
        params.extend([query.q, query.q])
    if query.protectedOnly:
        clauses.append("b.is_protected=1")
    if query.faces:
        clauses.append("b.faces!='\"not_checked\"' AND json_array_length(b.faces)>0")
    if query.duplicate:
        clauses.append("b.duplicate_set IS NOT NULL")
    return " AND ".join(clauses), params


def validate_scope(c, query):
    if (
        query.root
        and not c.execute("SELECT 1 FROM roots WHERE id=?", (query.root,)).fetchone()
    ):
        raise HTTPException(404, "Unknown library root")
    if query.revision is not None and query.revision != revision(c):
        raise HTTPException(409, "Query revision changed; reload results")


def matched(c, query):
    validate_scope(c, query)
    condition, params = where(query)
    return c.execute(
        BASE + "SELECT b.* FROM base b WHERE " + condition + " ORDER BY b.content_id",
        params,
    ).fetchall()


def query_fingerprint(query, current_revision):
    return fingerprint(
        {
            "query": {
                key: getattr(query, key)
                for key in PhotoQuery.model_fields
                if key not in ("page", "limit", "revision")
            },
            "revision": current_revision,
        }
    )


def photo(c, row):
    row = dict(row)
    instances = c.execute(
        "SELECT f.*,r.path AS root_path FROM file_instances f JOIN roots r ON r.id=f.root_id WHERE f.content_id=? ORDER BY CASE f.state WHEN 'available' THEN 0 ELSE 1 END,f.root_id,f.path",
        (row["content_id"],),
    ).fetchall()
    paths = [str(Path(f["root_path"]) / f["path"]) for f in instances]
    protection = set(json.loads(row["protection"]))
    faces = json.loads(row["faces"])
    if faces != "not_checked" and faces:
        protection.add("faces")
    if row["group_id"] == "documents" or row["document_type"] == "IDs":
        protection.add("document")
    if row["decision"] == "keep":
        protection.add("manual_keep")
    settings = json.loads(
        c.execute("SELECT value FROM settings WHERE section='protection'").fetchone()[0]
    )
    for protected in settings["protectedFolders"]:
        if any(
            f["root_id"] == protected["root"]
            and (
                not protected["folder"]
                or f["path"].startswith(protected["folder"] + "/")
            )
            for f in instances
        ):
            protection.add("folder")
    if (
        settings["ageBefore"]
        and row["capture_source"] in ("exif", "filename")
        and row["capture_date"]
        and row["capture_date"] < settings["ageBefore"]
    ):
        protection.add("age")
    sensitive = "yes" if row["document_type"] == "IDs" else row["sensitive"]
    triage = row["triage"]
    if triage == "clutter_candidate" and (
        protection
        or row["group_id"] in ("people", "events", "places", "documents")
        or sensitive != "no"
        or row["importance"] != "no"
        or (faces == "not_checked" and row["people"] != "no")
    ):
        triage = "needs_review"
    media = {
        m["kind"]: m["id"]
        for m in c.execute(
            "SELECT id,kind FROM cached_media WHERE content_id=? ORDER BY (fingerprint=(SELECT fingerprint FROM stage_specs WHERE stage='prepare' AND active=1)),rowid",
            (row["content_id"],),
        )
    }
    result = Photo.model_validate(
        dict(
            contentId=row["content_id"],
            filename=Path(instances[0]["path"]).name if instances else row["filename"],
            paths=paths,
            thumbUrl=f"/api/media/{media['thumb']}" if "thumb" in media else "",
            previewUrl=f"/api/media/{media['preview']}" if "preview" in media else "",
            width=row["width"],
            height=row["height"],
            bytes=row["size"],
            format=row["format"],
            captureDate=row["capture_date"],
            captureDateSource=row["capture_source"],
            camera=row["camera"],
            gps=json.loads(row["gps"]) if row["gps"] else None,
            origin=row["origin"],
            groupId=row["group_id"],
            groupSource=row["group_source"],
            band=row["band"],
            triage=triage,
            decision=row["decision"],
            decisionVersion=row["decision_version"],
            reviewVersion=row["review_version"],
            fileState=row["file_state"],
            protected={"reasons": sorted(protection)}
            if protection or row["is_protected"]
            else None,
            sensitive=sensitive,
            importance=row["importance"],
            people=row["people"],
            faces=faces,
            evidence=json.loads(row["evidence"]),
            duplicateSetId=row["duplicate_set"],
            eventId=row["event_id"],
            documentType=row["document_type"],
            blurRequired=sensitive != "no",
        )
    ).model_dump()
    # Match TypeScript's optional properties; required nullable axes remain explicit nulls.
    for optional in ("camera", "gps", "duplicateSetId", "eventId", "documentType"):
        if result[optional] is None:
            result.pop(optional)
    return result


def rows_by_id(c, ids):
    """Full rows for one page, in the given order.

    Sorting on `SELECT b.*` would compute every per-photo subquery for every match
    before LIMIT applies, so callers pick and order ids first and load rows here.
    """
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    rows = {
        row["content_id"]: row
        for row in c.execute(
            BASE + f"SELECT b.* FROM base b WHERE b.content_id IN ({marks})", list(ids)
        )
    }
    return [rows[identifier] for identifier in ids if identifier in rows]


def detail(c, content_id):
    row = c.execute(
        BASE + "SELECT * FROM base WHERE content_id=?", (content_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Unknown photo")
    return photo(c, row)


def list_photos(c, query):
    validate_scope(c, query)
    condition, params = where(query)
    sorts = {
        "default": "CASE b.band WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END",
        "date": "b.capture_date DESC",
        "name": "b.filename COLLATE NOCASE",
        "size": "b.size DESC",
        # Content IDs are SHA-256, so this order is an unbiased sample of the library.
        "content": "b.content_id",
        "margin": "CASE WHEN instr(b.evidence,'small_margin')>0 THEN 0 ELSE 1 END,b.band",
    }
    # Group membership/order is computed over the ENTIRE query before page boundaries.
    # A content with many paths appears once, using a deterministic path INSIDE this scope.
    folder_scope = ""
    if query.root:
        # Parameters are injected through SQLite quote(), never through caller-controlled SQL.
        root_literal = c.execute("SELECT quote(?)", (query.root,)).fetchone()[0]
        folder_scope = " AND f.root_id=" + root_literal
        if query.folder:
            folder_literal = c.execute(
                "SELECT quote(?)", (query.folder + "/",)
            ).fetchone()[0]
            folder_scope += (
                f" AND substr(f.path,1,{len(query.folder) + 1})=" + folder_literal
            )
    folder_key = (
        "COALESCE((SELECT f.folder FROM file_instances f WHERE f.content_id=b.content_id"
        + folder_scope
        + " ORDER BY CASE f.state WHEN 'available' THEN 0 ELSE 1 END,f.root_id,f.path LIMIT 1),b.folder)"
    )
    keys = {
        "none": "''",
        "folder": folder_key,
        "month": "COALESCE(substr(b.capture_date,1,7),'Unknown date')",
        "event": "COALESCE(b.event_id,'Not in an event')",
    }
    key = keys[query.by]
    unknown_last = (
        "b.capture_date IS NULL,"
        if query.by == "month"
        else "b.event_id IS NULL,"
        if query.by == "event"
        else ""
    )
    order = (
        unknown_last
        + (
            key + (" DESC," if query.by == "month" else ",")
            if query.by != "none"
            else ""
        )
        + sorts[query.sort]
        + ",b.content_id"
    )
    totals = c.execute(
        BASE
        + "SELECT COUNT(*) AS total,COALESCE(SUM(is_protected),0) AS protected FROM base b WHERE "
        + condition,
        params,
    ).fetchone()
    total = totals["total"]
    keyed = c.execute(
        BASE
        + "SELECT b.content_id,"
        + key
        + " AS grid_key FROM base b WHERE "
        + condition
        + " ORDER BY "
        + order
        + " LIMIT ? OFFSET ?",
        [*params, query.limit, (query.page - 1) * query.limit],
    ).fetchall()
    rows = rows_by_id(c, [row["content_id"] for row in keyed])
    groups = (
        c.execute(
            BASE
            + "SELECT "
            + key
            + " AS key,COUNT(*) AS count FROM base b WHERE "
            + condition
            + " GROUP BY "
            + key,
            params,
        ).fetchall()
        if query.by != "none"
        else []
    )
    counts = {r["key"]: r["count"] for r in groups}
    sections = []
    for index, row in enumerate(keyed):
        if not sections or sections[-1]["key"] != row["grid_key"]:
            sections.append(
                {
                    "key": row["grid_key"],
                    "count": counts.get(row["grid_key"], total),
                    "pageOffset": index,
                    "contentIds": [],
                }
            )
        sections[-1]["contentIds"].append(row["content_id"])
    return {
        "items": [photo(c, r) for r in rows],
        "total": total,
        "protectedCount": totals["protected"],
        "page": query.page,
        "limit": query.limit,
        "revision": revision(c),
        "queryFingerprint": query_fingerprint(query, revision(c)),
        "groups": sections if query.by != "none" else [],
        "nextPage": query.page + 1 if query.page * query.limit < total else None,
    }


def group_stats(c, query):
    query = query.model_copy(
        update={"decisions": "all", "reviewed": False, "revision": None, "band": "all"}
    )
    condition, params = where(query)
    rows = c.execute(
        BASE
        + "SELECT group_id,COUNT(*) AS all_count,SUM(decision='undecided') AS undecided,SUM(is_protected) AS protected,SUM(decision='undecided' AND band='high') AS high,SUM(decision='undecided' AND band='medium') AS medium,SUM(decision='undecided' AND band='low') AS low FROM base b WHERE "
        + condition
        + " GROUP BY group_id",
        params,
    ).fetchall()
    counts = {r["group_id"]: r for r in rows}
    # Covers show what is left to review and never a sensitive image.
    cover_ids = {}
    for row in c.execute(
        BASE
        + "SELECT content_id,group_id FROM (SELECT b.content_id,b.group_id,ROW_NUMBER() OVER (PARTITION BY b.group_id ORDER BY b.decision!='undecided',b.content_id) AS position FROM base b WHERE "
        + condition
        + " AND b.sensitive!='yes') WHERE position<=4 ORDER BY group_id,position",
        params,
    ):
        cover_ids.setdefault(row["group_id"], []).append(row["content_id"])
    result = []
    for identifier, name, section, icon in GROUPS:
        r = counts.get(identifier)
        all_count, undecided = (r["all_count"], r["undecided"]) if r else (0, 0)
        covers = rows_by_id(c, cover_ids.get(identifier, []))
        result.append(
            dict(
                id=identifier,
                name=name,
                section=section,
                icon=icon,
                reason="Awaiting analysis"
                if identifier == "unclassified"
                else "Review evidence for each photo",
                count=all_count,
                undecidedTarget=undecided,
                all=all_count,
                undecided=undecided,
                decided=all_count - undecided,
                protected=r["protected"] if r else 0,
                bands={b: r[b] if r else 0 for b in ("high", "medium", "low")},
                coverIds=[row["content_id"] for row in covers],
                # Group cards draw these directly, so one request is enough for the overview.
                covers=[photo(c, row) for row in covers],
            )
        )
    return result
