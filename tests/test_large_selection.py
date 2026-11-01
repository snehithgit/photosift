"""Generated metadata correctness exercise, not a hardware throughput benchmark."""

from conftest import assert_ok

from server.db import now
from server.review import process_bulk


def test_50000_metadata_selection_cross_page_freeze_conflict_recovery_and_undo(
    workspace,
):
    client, db, _, root = workspace
    identifiers = [f"{i + 100:064x}" for i in range(50000)]
    with db.write() as c:
        c.executemany(
            "INSERT INTO contents VALUES (?,10,?)",
            [(identifier, now()) for identifier in identifiers],
        )
        c.executemany(
            "INSERT INTO photos(content_id,filename,format,folder) VALUES (?,?,'JPEG','generated')",
            [(identifier, f"{i}.jpg") for i, identifier in enumerate(identifiers)],
        )
        c.executemany(
            "INSERT INTO decisions(content_id) VALUES (?)",
            [(identifier,) for identifier in identifiers],
        )
        c.executemany(
            "INSERT INTO group_assignments(content_id,ai_group) VALUES (?,'screenshots')",
            [(identifier,) for identifier in identifiers],
        )
        c.executemany(
            "INSERT INTO file_instances(root_id,path,content_id,state,folder) VALUES (?,?,?,'available','generated')",
            [
                (root, f"generated/{i}.jpg", identifier)
                for i, identifier in enumerate(identifiers)
            ],
        )
    page = assert_ok(
        client.get(
            "/api/photos",
            params={"root": root, "group": "screenshots", "limit": 180, "by": "folder"},
        )
    )
    assert page["total"] == 50000 and len(page["items"]) == 180
    assert page["groups"][0]["count"] == 50000
    frozen = assert_ok(
        client.post(
            "/api/selections",
            json={
                "query": {"root": root, "group": "screenshots", "by": "folder"},
                "queryRevision": page["revision"],
                "queryFingerprint": page["queryFingerprint"],
                "exclusions": [identifiers[0]],
            },
        ),
        201,
    )
    assert frozen["count"] == 49999
    # A correction beyond the loaded page must survive the bulk operation.
    assert_ok(
        client.patch(
            "/api/photos/" + identifiers[-1],
            json={"expectedVersion": 0, "decision": "later"},
        )
    )
    job = assert_ok(
        client.post(
            "/api/bulk",
            json={"selectionId": frozen["selectionId"], "decision": "clutter"},
        ),
        202,
    )
    assert process_bulk(db, batch_size=777)
    from server.db import Database

    reopened = Database(db.path)
    while process_bulk(reopened, batch_size=777):
        pass
    result = assert_ok(client.get("/api/jobs/" + job["id"]))
    assert (result["total"], result["applied"], result["conflicts"]) == (
        49999,
        49998,
        1,
    )
    assert result["conflictItems"][0]["contentId"] == identifiers[-1]
    assert (
        assert_ok(client.get("/api/photos", params={"group": "screenshots"}))["total"]
        == 1
    )
    undo_job = assert_ok(client.post("/api/history/" + job["actionId"] + "/undo"), 202)
    from server.review import process_undo

    assert process_undo(db, batch_size=777)
    reopened = Database(db.path)
    while process_undo(reopened, batch_size=777):
        pass
    reversed_action = assert_ok(client.get("/api/jobs/" + undo_job["id"]))
    assert reversed_action["applied"] == 49998 and reversed_action["conflicts"] == 0
    assert (
        assert_ok(client.get("/api/photos", params={"group": "screenshots"}))["total"]
        == 49999
    )
