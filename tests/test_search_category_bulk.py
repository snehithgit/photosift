import hashlib
import math

import numpy as np

from server import review
from server.db import now


def test_all_ranked_search_pages_can_be_categorized_and_undone(workspace):
    client, db, source, root = workspace
    originals = {
        p.relative_to(source): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in source.rglob("*")
        if p.is_file()
    }
    index = client.post(
        "/api/indexes",
        json={
            "backend": "siglip2",
            "checkpoint": "arithmetic-fixture-only",
            "revision": "a" * 40,
            "dimension": 16,
            "preprocessing": {"fixture": True},
            "precision": "float32",
            "normalization": "l2",
            "runtime": "test",
        },
    ).json()
    members = [
        hashlib.sha256(f"search-fixture-{i}".encode()).hexdigest() for i in range(507)
    ]
    with db.write() as c:
        # Test vectors only: this does not represent a real quality evaluation/promotion.
        c.execute(
            "UPDATE embedding_indexes SET role='primary',status='ready' WHERE id=?",
            (index["id"],),
        )
        for position, content in enumerate(members):
            c.execute("INSERT INTO contents VALUES (?,?,?)", (content, 1, now()))
            c.execute(
                "INSERT INTO photos(content_id,filename,format,folder,sensitive,importance) VALUES (?,?,'JPEG','','yes','yes')",
                (content, f"{position}.jpg"),
            )
            c.execute(
                "INSERT INTO group_assignments(content_id) VALUES (?)", (content,)
            )
            c.execute("INSERT INTO decisions(content_id) VALUES (?)", (content,))
            c.execute(
                "INSERT INTO file_instances(root_id,path,content_id,state) VALUES (?,?,?,'available')",
                (root, f"{position}.jpg", content),
            )
            score = 1.0 if position == 0 else 1.0 / (position + 1)
            vector = np.asarray(
                [score, math.sqrt(1 - score * score), *([0] * 14)], dtype="<f4"
            )
            c.execute(
                "INSERT INTO embeddings VALUES (?,?,?)",
                (content, index["id"], vector.tobytes()),
            )
    params = {"like": members[0], "root": root, "limit": 120}
    pages = [
        client.get("/api/search", params={**params, "page": page}).json()
        for page in range(1, 6)
    ]
    assert [len(page["items"]) for page in pages] == [120, 120, 120, 120, 20]
    selected = [photo for page in pages for photo in page["items"]]
    ids = [photo["contentId"] for photo in selected]
    assert len(set(ids)) == 500 and members[0] not in ids
    assert all(page["total"] == 500 for page in pages)
    all_matches = client.get("/api/search", params={**params, "limit": 500}).json()[
        "items"
    ]
    assert [p["contentId"] for p in all_matches] == ids
    body = {
        "groupId": "documents",
        "items": [
            {"contentId": p["contentId"], "expectedVersion": p["reviewVersion"]}
            for p in all_matches
        ],
    }
    queued = client.post("/api/bulk/groups", json=body)
    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert job["total"] == 500 and job["progress"] == 0
    for _ in range(5):
        assert review.process_bulk(db, batch_size=100)
    assert not review.process_bulk(db)
    done = client.get("/api/jobs/" + job["id"]).json()
    assert (done["state"], done["applied"], done["conflicts"]) == ("done", 500, 0)
    with db.read() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM group_assignments WHERE manual_group='documents'"
            ).fetchone()[0]
            == 500
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM decisions WHERE decision!='undecided'"
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM photos WHERE sensitive='yes' AND importance='yes'"
            ).fetchone()[0]
            == 507
        )
        assert (
            c.execute(
                "SELECT manual_group FROM group_assignments WHERE content_id=?",
                (members[0],),
            ).fetchone()[0]
            is None
        )
    undo = client.post("/api/history/" + job["actionId"] + "/undo")
    assert undo.status_code == 202, undo.text
    while review.process_undo(db):
        pass
    with db.read() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM group_assignments WHERE manual_group IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
    assert originals == {
        p.relative_to(source): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in source.rglob("*")
        if p.is_file()
    }


def test_category_job_uses_selected_versions_and_undo_preserves_later_edits(workspace):
    client, db, _, _ = workspace
    photos = client.get("/api/photos").json()["items"]
    selected = [
        {"contentId": p["contentId"], "expectedVersion": p["reviewVersion"]}
        for p in photos
    ]
    job = client.post(
        "/api/bulk/groups", json={"groupId": "memes", "items": selected}
    ).json()
    changed = photos[0]
    client.patch(
        "/api/photos/" + changed["contentId"],
        json={"expectedVersion": changed["reviewVersion"], "groupId": "people"},
    )
    review.process_bulk(db)
    result = client.get("/api/jobs/" + job["id"]).json()
    assert result["applied"] == 1 and result["conflicts"] == 1
    assert (
        client.get("/api/photos/" + changed["contentId"]).json()["groupId"] == "people"
    )
    other = client.get("/api/photos/" + photos[1]["contentId"]).json()
    client.patch(
        "/api/photos/" + other["contentId"],
        json={"expectedVersion": other["reviewVersion"], "decision": "keep"},
    )
    client.post("/api/history/" + job["actionId"] + "/undo")
    while review.process_undo(db):
        pass
    assert client.get("/api/photos/" + other["contentId"]).json()["decision"] == "keep"
    assert client.get("/api/photos/" + other["contentId"]).json()["groupId"] == "memes"


def test_category_bulk_validates_membership_and_cannot_repurpose_a_frozen_selection(
    workspace,
):
    client, db, _, _ = workspace
    photo = client.get("/api/photos").json()["items"][0]
    item = {"contentId": photo["contentId"], "expectedVersion": photo["reviewVersion"]}
    for items in ([], [item, item], [item] * 501):
        assert (
            client.post(
                "/api/bulk/groups", json={"groupId": "people", "items": items}
            ).status_code
            == 422
        )
    assert (
        client.post(
            "/api/bulk/groups", json={"groupId": "invalid", "items": [item]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/bulk/groups",
            json={
                "groupId": "people",
                "items": [{"contentId": "missing", "expectedVersion": 0}],
            },
        ).status_code
        == 409
    )
    started = client.post(
        "/api/bulk/groups", json={"groupId": "people", "items": [item]}
    ).json()
    with db.read() as c:
        selection = c.execute(
            "SELECT selection_id FROM bulk_jobs WHERE id=?", (started["id"],)
        ).fetchone()[0]
    assert (
        client.post(
            "/api/bulk", json={"selectionId": selection, "groupId": "people"}
        ).json()["id"]
        == started["id"]
    )
    assert (
        client.post(
            "/api/bulk", json={"selectionId": selection, "decision": "clutter"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/bulk",
            json={"selectionId": selection, "groupId": "people", "decision": "keep"},
        ).status_code
        == 422
    )
