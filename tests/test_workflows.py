from conftest import assert_ok
from fastapi.testclient import TestClient
from test_review import listing

from server.app import create_app
from worker.scanner import queue_scan, scan


def test_events_edit_split_merge_and_conflicts(workspace):
    client, db, _, _ = workspace
    ids = [p["contentId"] for p in listing(client)["items"]]
    event = assert_ok(
        client.post("/api/events", json={"name": "Manual event", "contentIds": ids}),
        201,
    )
    assert event["photoCount"] == 2 and event["status"] == "confirmed"
    assert len(assert_ok(client.get("/api/events"))["items"]) == 1
    assert (
        client.patch(
            "/api/events/" + event["id"], json={"expectedVersion": 0, "name": "Stale"}
        ).status_code
        == 409
    )
    renamed = assert_ok(
        client.patch(
            "/api/events/" + event["id"],
            json={
                "expectedVersion": event["version"],
                "name": "Renamed",
                "status": "suggested",
            },
        )
    )
    assert renamed["name"] == "Renamed"
    split = assert_ok(
        client.post(
            "/api/events/" + event["id"] + "/split",
            json={
                "expectedVersion": renamed["version"],
                "contentIds": [ids[0]],
                "name": "Part two",
            },
        )
    )
    assert split["photoCount"] == 1
    original = assert_ok(client.get("/api/events/" + event["id"]))
    merged = assert_ok(
        client.post(
            "/api/events/" + split["id"] + "/merge",
            json={
                "expectedVersion": split["version"],
                "intoId": original["id"],
                "targetVersion": original["version"],
            },
        )
    )
    assert merged["photoCount"] == 2
    assert client.get("/api/events/" + split["id"]).status_code == 404
    p = assert_ok(client.get("/api/photos/" + ids[0]))
    result = assert_ok(
        client.patch(
            "/api/photos/" + ids[0],
            json={"expectedVersion": p["reviewVersion"], "removeFromEvent": True},
        )
    )
    assert result["photo"].get("eventId") is None
    assert_ok(client.post("/api/history/" + result["actionId"] + "/undo"))
    assert assert_ok(client.get("/api/photos/" + ids[0]))["eventId"] == event["id"]
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM event_edits").fetchone()[0] >= 5


def test_inventory_api_status_analyze_settings_and_search(workspace):
    client, db, _, root = workspace
    for path in (
        "/api/status",
        "/api/roots",
        "/api/folders?root=" + root,
        "/api/library/stats?root=" + root,
        "/api/library/files",
        "/api/stages",
        "/api/settings",
        "/api/history",
        "/api/export/plans",
        "/api/export/runs",
    ):
        assert_ok(client.get(path))
    inventory = assert_ok(
        client.get("/api/library/files", params={"root": root, "state": "unsupported"})
    )
    assert inventory["total"] == 1 and inventory["items"][0]["path"] == "notes.txt"
    assert (
        assert_ok(client.get("/api/library/stats", params={"root": root}))["files"] == 4
    )
    tree = assert_ok(client.get("/api/folders", params={"root": root}))
    assert tree[0]["files"] == 4
    stage = assert_ok(client.get("/api/stages"))
    assert stage["items"][0]["state"] == "done"
    assert all(s["ratePerMin"] is None for s in stage["items"])
    for operation in ("pause", "resume", "cancel", "retry"):
        assert_ok(client.post("/api/analyze/" + operation))
    saved = assert_ok(
        client.patch(
            "/api/settings/appearance",
            json={
                "expectedVersion": 0,
                "value": {"theme": "light", "autoAdvance": True},
            },
        )
    )
    assert saved["value"]["theme"] == "light"
    assert (
        client.patch(
            "/api/settings/appearance",
            json={"expectedVersion": 0, "value": {"theme": "dark"}},
        ).status_code
        == 409
    )
    assert (
        client.patch(
            "/api/settings/appearance",
            json={"expectedVersion": 1, "value": {"theme": "invented"}},
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/settings/models",
            json={"expectedVersion": 0, "value": {"d3Schema": {}}},
        ).status_code
        == 501
    )
    search = assert_ok(client.get("/api/search", params={"q": "copy"}))
    assert search["total"] == 1 and search["mode"] == "filename-and-path"
    assert client.get("/api/search", params={"like": "anything"}).status_code == 503


def test_exact_duplicates_do_not_decide_content(workspace):
    client, _, _, _ = workspace
    sets = assert_ok(client.get("/api/duplicates"))["items"]
    assert len(sets) == 1
    assert sets[0]["kind"] == "exact" and sets[0]["extraCopies"] == 1
    assert len(sets[0]["contentIds"]) == 1
    assert listing(client)["total"] == 2


def make_exportable(client, db):
    for p in listing(client)["items"]:
        assert_ok(
            client.patch(
                "/api/photos/" + p["contentId"],
                json={"expectedVersion": p["reviewVersion"], "decision": "keep"},
            )
        )
    with db.write() as c:
        c.execute("UPDATE photos SET sensitive='no',importance='no'")


def test_frozen_export_plan_approval_manifest_and_no_source_or_destination_writes(
    workspace, tmp_path
):
    client, db, source, root = workspace
    make_exportable(client, db)
    destination = tmp_path / "exports"
    destination.mkdir()
    source_before = {str(p): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    plan = assert_ok(
        client.post(
            "/api/export/plans",
            json={"query": {"root": root}, "destination": str(destination)},
        ),
        201,
    )
    assert plan["totals"]["photos"] == 2
    assert plan["totals"]["bytes"] == len(b"first image") + len(b"second image")
    assert plan["totals"]["freeBytes"] > 0
    assert all("Organized" in row["destination"] for row in plan["rows"])
    assert (
        client.post(
            "/api/export/plans/" + plan["id"] + "/approve",
            json={"revision": 1, "fingerprint": "wrong"},
        ).status_code
        == 409
    )
    run = assert_ok(
        client.post(
            "/api/export/plans/" + plan["id"] + "/approve",
            json={"revision": 1, "fingerprint": plan["fingerprint"]},
        ),
        202,
    )
    assert run["state"] == "queued" and run["copied"] == run["verified"] == 0
    assert_ok(client.get("/api/export/runs/" + run["id"]))
    manifest = assert_ok(client.get("/api/export/runs/" + run["id"] + "/manifest"))
    assert manifest["verified"] is False
    assert all(row["state"] == "pending" for row in manifest["entries"])
    csv = client.get("/api/export/runs/" + run["id"] + "/manifest?format=csv")
    assert csv.status_code == 200 and "contentId,source,destination,sha256" in csv.text
    assert client.post("/api/export/runs/" + run["id"] + "/rollback").status_code == 200
    reloaded = TestClient(create_app(db.path))
    assert (
        assert_ok(reloaded.get("/api/export/plans/" + plan["id"]))["rows"]
        == plan["rows"]
    )
    assert list(destination.iterdir()) == []
    assert {
        str(p): p.read_bytes() for p in source.rglob("*") if p.is_file()
    } == source_before


def test_export_sensitive_private_only_unknown_excluded_stale_plan(workspace, tmp_path):
    client, db, source, root = workspace
    make_exportable(client, db)
    with db.write() as c:
        c.execute("UPDATE photos SET document_type='IDs'")
    destination = tmp_path / "exports"
    destination.mkdir()
    inputs = {"query": {}, "destination": str(destination)}
    excluded = assert_ok(client.post("/api/export/plans", json=inputs), 201)
    assert excluded["totals"]["photos"] == 0 and excluded["totals"]["exclusions"] == 2
    plan = assert_ok(
        client.post("/api/export/plans", json={**inputs, "includeSensitive": True}), 201
    )
    assert all(
        "Private/Sensitive/" in row["destination"].replace("\\", "/")
        for row in plan["rows"]
    )
    p = listing(client, decisions="all")["items"][0]
    assert_ok(
        client.patch(
            "/api/photos/" + p["contentId"],
            json={"expectedVersion": p["reviewVersion"], "decision": "later"},
        )
    )
    stale = assert_ok(client.get("/api/export/plans/" + plan["id"]))
    assert stale["staleDecisions"] == 1 and stale["rows"] == plan["rows"]
    assert (
        client.post(
            "/api/export/plans/" + plan["id"] + "/approve",
            json={"revision": 1, "fingerprint": plan["fingerprint"]},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/export/plans", json={**inputs, "destination": str(source)}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/export/plans",
            json={**inputs, "destination": str(source / "nested-output")},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/export/plans", json={**inputs, "destination": str(db.path.parent)}
        ).status_code
        == 422
    )


def test_export_collision_renaming_and_copy_dedup(workspace, tmp_path):
    client, db, source, root = workspace
    (source / "copies" / "b.png").write_bytes(b"third unique image")
    scan(db, queue_scan(db, root))
    make_exportable(client, db)
    destination = tmp_path / "exports"
    destination.mkdir()
    plan = assert_ok(
        client.post(
            "/api/export/plans", json={"query": {}, "destination": str(destination)}
        ),
        201,
    )
    assert plan["totals"]["photos"] == 3  # four supported paths, three contents
    assert plan["totals"]["collisions"] == 1
    assert any("(2).png" in row["destination"] for row in plan["rows"])
    assert len({row["destination"].casefold() for row in plan["rows"]}) == 3
    # Once frozen, this destination can never subsequently be registered as a source.
    assert (
        client.post(
            "/api/roots", json={"name": "output", "path": str(destination)}
        ).status_code
        == 422
    )


def test_lease_authentication_expiry_reclaim_and_unconfigured_schema(workspace):
    client, db, _, _ = workspace
    body = {
        "owner": "laptop",
        "stage": "prepare",
        "fingerprint": "test-preparation-v1",
        "leaseSeconds": 10,
    }
    headers = {"Authorization": "Bearer test-agent-token"}
    assert client.post("/api/agent/claim", json=body).status_code == 401
    with db.write() as c:
        c.execute("UPDATE stage_jobs SET fingerprint='test-preparation-v1'")
    first = assert_ok(client.post("/api/agent/claim", json=body, headers=headers))
    assert first["attempt"] == 1 and "relativePath" in first and "rootId" in first
    assert "root_path" not in first
    renewal = {k: first[k] for k in ("owner", "token", "attempt")}
    assert_ok(
        client.post(
            "/api/agent/jobs/" + first["jobId"] + "/renew",
            json=renewal,
            headers=headers,
        )
    )
    with db.write() as c:
        c.execute("UPDATE stage_jobs SET expires=0 WHERE id=?", (first["jobId"],))
    assert (
        client.post(
            "/api/agent/jobs/" + first["jobId"] + "/renew",
            json=renewal,
            headers=headers,
        ).status_code
        == 409
    )
    second = assert_ok(client.post("/api/agent/claim", json=body, headers=headers))
    # Claim order may select the other queued content; reclaim after making it ineligible.
    if second["jobId"] != first["jobId"]:
        second = assert_ok(client.post("/api/agent/claim", json=body, headers=headers))
    assert (
        second["jobId"] == first["jobId"]
        and second["token"] != first["token"]
        and second["attempt"] == 2
    )
    invalid = {
        **renewal,
        "fingerprint": first["fingerprint"],
        "sha256": first["sha256"],
        "payload": {},
    }
    assert (
        client.post(
            "/api/agent/jobs/" + first["jobId"] + "/result",
            json=invalid,
            headers=headers,
        ).status_code
        == 409
    )
    unimplemented = {**invalid, **{k: second[k] for k in ("owner", "token", "attempt")}}
    assert (
        client.post(
            "/api/agent/jobs/" + first["jobId"] + "/result",
            json=unimplemented,
            headers=headers,
        ).status_code
        == 501
    )
    assert (
        client.post(
            "/api/agent/claim",
            json={**body, "fingerprint": "prepare-contract-v1:unconfigured"},
            headers=headers,
        ).status_code
        == 409
    )
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM stage_results").fetchone()[0] == 0


def test_one_primary_embedding_index_enforced(workspace):
    import sqlite3

    import pytest

    _, db, _, _ = workspace
    with db.write() as c:
        c.execute(
            "INSERT INTO embedding_indexes VALUES ('a','siglip-fingerprint','{}','primary','ready')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        with db.write() as c:
            c.execute(
                "INSERT INTO embedding_indexes VALUES ('b','clip-fingerprint','{}','primary','ready')"
            )
