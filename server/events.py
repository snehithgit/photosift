from fastapi import HTTPException

from server.db import action, audit, bump, now, packed, uid
from server.queries import BASE, where


def get_event(c, identifier):
    row = c.execute(
        "SELECT e.*,MIN(p.capture_date) AS start,MAX(p.capture_date) AS end,COUNT(m.content_id) AS photoCount,MIN(m.content_id) AS coverId FROM events e LEFT JOIN event_members m ON m.event_id=e.id LEFT JOIN photos p ON p.content_id=m.content_id WHERE e.id=? AND e.archived=0 GROUP BY e.id",
        (identifier,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Unknown event")
    result = dict(row)
    # Empty strings preserve the mock's string contract without inventing metadata.
    for key in ("start", "end", "place", "coverId"):
        result[key] = result[key] or ""
    if row["parent_trip_id"]:
        result["tripId"] = row["parent_trip_id"]
        result["parentTripId"] = row["parent_trip_id"]
    return result


def list_events(c, query):
    condition, params = where(query)
    rows = c.execute(
        BASE
        + "SELECT e.id,e.name,e.place,e.status,e.version,e.parent_trip_id,MIN(b.capture_date) start,MAX(b.capture_date) end,COUNT(*) photoCount,MIN(b.content_id) coverId FROM base b JOIN events e ON e.id=b.event_id WHERE "
        + condition
        + " AND e.archived=0 GROUP BY e.id ORDER BY start DESC,e.id",
        params,
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for key in ("start", "end", "place", "coverId"):
            item[key] = item[key] or ""
        if item["parent_trip_id"]:
            item["tripId"] = item["parent_trip_id"]
        result.append(item)
    return result


def check_version(c, identifier, expected):
    event = get_event(c, identifier)
    if event["version"] != expected:
        raise HTTPException(409, "Event changed; reload")
    return event


def record(c, identifier, action_id, before):
    c.execute("UPDATE events SET version=version+1 WHERE id=?", (identifier,))
    after = dict(c.execute("SELECT * FROM events WHERE id=?", (identifier,)).fetchone())
    c.execute(
        "INSERT INTO event_edits(event_id,action_id,before_json,after_json,at) VALUES (?,?,?,?,?)",
        (identifier, action_id, packed(before), packed(after), now()),
    )
    audit(c, action_id, None, "event_edit", before, after)
    bump(c)


def create_event(c, request):
    identifiers = set(request.contentIds)
    for identifier in identifiers:
        if not c.execute(
            "SELECT 1 FROM photos WHERE content_id=?", (identifier,)
        ).fetchone():
            raise HTTPException(404, "Unknown event member")
        if c.execute(
            "SELECT 1 FROM event_members WHERE content_id=?", (identifier,)
        ).fetchone():
            raise HTTPException(
                409, "Use explicit merge/split for existing event members"
            )
    identifier, action_id = uid(), action(c, "Create event", request.name)
    c.execute(
        "INSERT INTO events(id,name,status) VALUES (?,?,'confirmed')",
        (identifier, request.name),
    )
    for content_id in identifiers:
        c.execute(
            "INSERT INTO event_members(content_id,event_id) VALUES (?,?)",
            (content_id, identifier),
        )
        version = (
            c.execute(
                "SELECT review_version FROM photos WHERE content_id=?", (content_id,)
            ).fetchone()[0]
            + 1
        )
        c.execute(
            "UPDATE photos SET review_version=? WHERE content_id=?",
            (version, content_id),
        )
        audit(c, action_id, content_id, "event", None, identifier, version)
    record(c, identifier, action_id, {})
    return get_event(c, identifier)


def patch_event(c, identifier, request):
    before = check_version(c, identifier, request.expectedVersion)
    action_id = action(c, "Edit event", identifier)
    if request.name is not None:
        c.execute("UPDATE events SET name=? WHERE id=?", (request.name, identifier))
    if request.status is not None:
        c.execute("UPDATE events SET status=? WHERE id=?", (request.status, identifier))
    record(c, identifier, action_id, before)
    return get_event(c, identifier)


def move_members(c, source, target, identifiers, action_id):
    for identifier in identifiers:
        c.execute(
            "UPDATE event_members SET event_id=? WHERE content_id=?",
            (target, identifier),
        )
        c.execute(
            "UPDATE photos SET review_version=review_version+1 WHERE content_id=?",
            (identifier,),
        )
        version = c.execute(
            "SELECT review_version FROM photos WHERE content_id=?", (identifier,)
        ).fetchone()[0]
        audit(c, action_id, identifier, "event", source, target, version)


def merge(c, identifier, request):
    if identifier == request.intoId:
        raise HTTPException(422, "Choose a different event")
    before = check_version(c, identifier, request.expectedVersion)
    target = check_version(c, request.intoId, request.targetVersion)
    action_id = action(c, "Merge events", f"{identifier} → {request.intoId}")
    identifiers = [
        r[0]
        for r in c.execute(
            "SELECT content_id FROM event_members WHERE event_id=?", (identifier,)
        )
    ]
    move_members(c, identifier, request.intoId, identifiers, action_id)
    c.execute("UPDATE events SET archived=1 WHERE id=?", (identifier,))
    record(c, identifier, action_id, before)
    record(c, request.intoId, action_id, target)
    return get_event(c, request.intoId)


def split(c, identifier, request):
    before = check_version(c, identifier, request.expectedVersion)
    members = {
        r[0]
        for r in c.execute(
            "SELECT content_id FROM event_members WHERE event_id=?", (identifier,)
        )
    }
    selected = set(request.contentIds)
    if not selected < members:
        raise HTTPException(
            409, "Split must select a nonempty proper subset of event members"
        )
    new_id, action_id = uid(), action(c, "Split event", identifier)
    c.execute(
        "INSERT INTO events(id,name,status) VALUES (?,?,?)",
        (new_id, request.name, before["status"]),
    )
    move_members(c, identifier, new_id, selected, action_id)
    record(c, identifier, action_id, before)
    record(c, new_id, action_id, {})
    return get_event(c, new_id)
