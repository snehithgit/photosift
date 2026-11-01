from conftest import assert_ok
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import now
from server.review import process_bulk


def listing(client, **params):
    return assert_ok(client.get("/api/photos", params=params))


def select(client, **extra):
    current = listing(client)
    return assert_ok(
        client.post(
            "/api/selections",
            json={
                "query": {},
                "queryRevision": current["revision"],
                "queryFingerprint": current["queryFingerprint"],
                **extra,
            },
        ),
        201,
    )


def test_photo_contract_counts_filters_and_review_visibility(workspace):
    client, db, _, root = workspace
    current = listing(client, root=root)
    assert current["total"] == 2
    p = current["items"][0]
    assert p["faces"] == "not_checked"
    assert p["sensitive"] == p["importance"] == "unknown"
    assert p["triage"] == "awaiting_analysis"
    assert p["thumbUrl"] == "" and p["blurRequired"]
    assert "camera" not in p and "gps" not in p and "eventId" not in p
    assert p["captureDate"] is None and p["protected"] is None
    assert listing(client, faces="1")["total"] == 0
    assert listing(client, q="COPY")["total"] == 1
    assert listing(client, format="PNG")["total"] == 1
    assert listing(client, band="high")["total"] == 0
    assert listing(client, origin="camera")["total"] == 0
    assert listing(client, protectedOnly="1")["total"] == 0
    result = assert_ok(
        client.patch(
            "/api/photos/" + p["contentId"],
            json={"expectedVersion": p["reviewVersion"], "decision": "keep"},
        )
    )
    assert result["photo"]["decisionVersion"] == 1
    assert listing(client)["total"] == 1
    assert listing(client, reviewed="1")["total"] == 2
    assert listing(client, decisions="keep")["total"] == 1
    assert (
        client.patch(
            "/api/photos/" + p["contentId"],
            json={"expectedVersion": 0, "decision": "later"},
        ).status_code
        == 409
    )
    stats = assert_ok(client.get("/api/groups"))["items"]
    for group in stats:
        assert group["all"] == group["undecided"] + group["decided"]
        assert sum(group["bands"].values()) == group["undecided"]
    assert sum(g["all"] for g in stats) == 2


def test_frozen_bulk_conflicts_idempotency_and_restart(workspace):
    client, db, _, _ = workspace
    current = listing(client)
    selected = select(client)
    ids = [p["contentId"] for p in current["items"]]
    assert_ok(
        client.patch(
            "/api/photos/" + ids[0], json={"expectedVersion": 0, "decision": "later"}
        )
    )
    job = assert_ok(
        client.post(
            "/api/bulk",
            json={"selectionId": selected["selectionId"], "decision": "clutter"},
        ),
        202,
    )
    retry = assert_ok(
        client.post(
            "/api/bulk",
            json={"selectionId": selected["selectionId"], "decision": "clutter"},
        ),
        202,
    )
    assert retry["id"] == job["id"]
    assert (
        client.post(
            "/api/bulk",
            json={"selectionId": selected["selectionId"], "decision": "keep"},
        ).status_code
        == 409
    )
    assert client.post("/api/history/" + job["actionId"] + "/undo").status_code == 409
    assert process_bulk(db, batch_size=1)
    reopened = create_app(db.path).state.db
    while process_bulk(reopened, batch_size=1):
        pass
    result = assert_ok(client.get("/api/jobs/" + job["id"]))
    assert (result["applied"], result["conflicts"], result["state"]) == (1, 1, "done")
    assert result["conflictItems"][0]["contentId"] == ids[0]
    undo = assert_ok(client.post("/api/history/" + job["actionId"] + "/undo"))
    assert undo["applied"] == 1
    assert listing(client)["total"] == 1
    assert client.post("/api/history/" + job["actionId"] + "/undo").status_code == 409


def test_version_aware_undo_skips_later_edits_and_survives_reload(workspace):
    client, db, _, _ = workspace
    selected = select(client)
    job = assert_ok(
        client.post(
            "/api/bulk",
            json={"selectionId": selected["selectionId"], "decision": "later"},
        ),
        202,
    )
    while process_bulk(db):
        pass
    p = listing(client, decisions="all")["items"][0]
    assert_ok(
        client.patch(
            "/api/photos/" + p["contentId"],
            json={"expectedVersion": p["reviewVersion"], "groupId": "people"},
        )
    )
    reloaded = TestClient(create_app(db.path))
    result = assert_ok(reloaded.post("/api/history/" + job["actionId"] + "/undo"))
    assert (result["applied"], result["conflicts"]) == (1, 1)
    assert result["conflictIds"] == [p["contentId"]]


def test_stale_queries_and_selection_cannot_expand_scope(workspace):
    client, db, _, root = workspace
    previous = listing(client)
    old = previous["revision"]
    p = listing(client)["items"][0]
    assert_ok(
        client.patch(
            "/api/photos/" + p["contentId"],
            json={"expectedVersion": 0, "decision": "later"},
        )
    )
    assert (
        client.post(
            "/api/selections",
            json={
                "query": {},
                "queryRevision": old,
                "queryFingerprint": previous["queryFingerprint"],
            },
        ).status_code
        == 409
    )
    assert client.get("/api/photos", params={"revision": old}).status_code == 409
    current = listing(client)
    assert (
        client.post(
            "/api/selections",
            json={
                "query": {"root": root, "folder": "copies"},
                "queryRevision": current["revision"],
                "queryFingerprint": current["queryFingerprint"],
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/selections",
            json={
                "query": {},
                "queryRevision": current["revision"],
                "queryFingerprint": current["queryFingerprint"],
                "exclusions": ["unknown"],
            },
        ).status_code
        == 409
    )


def test_protection_before_suggestions_and_bulk_policy_conflict(workspace):
    client, db, _, root = workspace
    selection = select(client)
    assert_ok(
        client.patch(
            "/api/settings/protection",
            json={
                "expectedVersion": 0,
                "value": {
                    "protectedFolders": [{"root": root, "folder": ""}],
                    "ageBefore": None,
                },
            },
        )
    )
    assert select(client)["count"] == 0
    assert select(client, includeProtected=True)["count"] == 2
    job = assert_ok(
        client.post(
            "/api/bulk",
            json={"selectionId": selection["selectionId"], "decision": "clutter"},
        ),
        202,
    )
    while process_bulk(db):
        pass
    assert assert_ok(client.get("/api/jobs/" + job["id"]))["conflicts"] == 2
    p = listing(client)["items"][0]
    assert (
        client.patch(
            "/api/photos/" + p["contentId"],
            json={"expectedVersion": 0, "decision": "clutter"},
        ).status_code
        == 409
    )
    assert_ok(
        client.patch(
            "/api/photos/" + p["contentId"],
            json={
                "expectedVersion": 0,
                "decision": "clutter",
                "overrideProtected": True,
            },
        )
    )
    with db.write() as c:
        c.execute(
            "UPDATE photos SET triage='clutter_candidate',sensitive='no',importance='no',faces='[]'"
        )
        c.execute("UPDATE group_assignments SET ai_group='people'")
    assert all(
        p["triage"] != "clutter_candidate"
        for p in listing(client, decisions="all")["items"]
    )


def test_grid_groups_use_full_query_before_paging(workspace):
    client, db, _, root = workspace
    with db.write() as c:
        for i in range(8):
            content = f"{i:064x}"
            c.execute("INSERT INTO contents VALUES (?,?,?)", (content, 10, now()))
            c.execute(
                "INSERT INTO photos(content_id,filename,format,folder,capture_date) VALUES (?,?,'JPEG',?,?)",
                (
                    content,
                    f"{i}.jpg",
                    "A" if i < 5 else "B",
                    "2024-01-01" if i < 5 else "2024-02-01",
                ),
            )
            c.execute("INSERT INTO decisions(content_id) VALUES (?)", (content,))
            c.execute(
                "INSERT INTO group_assignments(content_id) VALUES (?)", (content,)
            )
            c.execute(
                "INSERT INTO file_instances(root_id,path,content_id,state,folder) VALUES (?,?,?,'available',?)",
                (
                    root,
                    f"{'A' if i < 5 else 'B'}/{i}.jpg",
                    content,
                    "A" if i < 5 else "B",
                ),
            )
    first = listing(client, by="folder", limit=3)
    assert first["groups"][0]["key"] == "."
    # Folder A spans pages but each header reports all five members, never the page slice.
    page = listing(client, by="folder", limit=3, page=2)
    group = next(g for g in page["groups"] if g["key"] == "A")
    assert group["count"] == 5
    months = listing(client, by="month", limit=2)
    assert all(g["count"] in (2, 3, 5) for g in months["groups"])
    assert months["groups"][0]["key"] == "2024-02"


def test_group_sample_and_borderline_never_include_protected(workspace):
    client, db, _, root = workspace
    ids = [p["contentId"] for p in listing(client)["items"]]
    with db.write() as c:
        c.execute(
            "UPDATE photos SET protection='[\"faces\"]' WHERE content_id=?", (ids[0],)
        )
    rows = assert_ok(
        client.get("/api/groups/unclassified/review", params={"root": root})
    )
    assert [p["contentId"] for p in rows["sample"]] == [ids[1]]
    assert [p["contentId"] for p in rows["borderline"]] == [ids[1]]
    assert [p["contentId"] for p in rows["protected"]] == [ids[0]]
    assert rows["total"] == 2 and rows["protectedCount"] == 1


def test_group_move_reversible_and_manual_keep_protects(workspace):
    client, _, _, _ = workspace
    p = listing(client)["items"][0]
    change = assert_ok(
        client.patch(
            "/api/photos/" + p["contentId"],
            json={"expectedVersion": 0, "groupId": "documents"},
        )
    )
    assert change["photo"]["groupSource"] == "manual"
    assert "document" in change["photo"]["protected"]["reasons"]
    assert_ok(client.post("/api/history/" + change["actionId"] + "/undo"))
    p = assert_ok(client.get("/api/photos/" + p["contentId"]))
    assert p["groupId"] == "unclassified" and p["groupSource"] == "ai"
    change = assert_ok(
        client.patch(
            "/api/photos/" + p["contentId"],
            json={"expectedVersion": p["reviewVersion"], "decision": "keep"},
        )
    )
    assert "manual_keep" in change["photo"]["protected"]["reasons"]
