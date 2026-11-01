"""Fields the connected web client relies on; additive to the Phase 1 contracts."""

import io

from conftest import assert_ok
from PIL import Image

from server.db import bump, uid


def _prepared_thumbnail(db, tmp_path, content_id):
    cache = db.path.parent / "cache" / content_id
    cache.mkdir(parents=True)
    path = cache / "thumb.webp"
    Image.new("RGB", (64, 48), (200, 40, 40)).save(path, "WEBP")
    identifier = uid()
    with db.write() as c:
        c.execute(
            "INSERT INTO cached_media VALUES (?,?,?,?,?,?,?)",
            (identifier, content_id, "fp", "thumb", str(path), 10, "x"),
        )
    return identifier


def test_photo_list_reports_protected_count_and_groups_carry_covers(workspace):
    client, db, _, _ = workspace
    current = assert_ok(client.get("/api/photos"))
    assert current["protectedCount"] == 0
    first = current["items"][0]
    assert_ok(
        client.patch(
            "/api/photos/" + first["contentId"],
            json={"expectedVersion": first["reviewVersion"], "decision": "keep"},
        )
    )
    # A kept photo is protected, and only appears when reviewed photos are requested.
    assert assert_ok(client.get("/api/photos?reviewed=1"))["protectedCount"] == 1
    groups = assert_ok(client.get("/api/groups"))["items"]
    unclassified = next(g for g in groups if g["id"] == "unclassified")
    assert unclassified["coverIds"] == [p["contentId"] for p in unclassified["covers"]]
    assert all(not g["covers"] for g in groups if g["all"] == 0)


def test_review_rows_carry_selection_fingerprint_and_borderline_count(workspace):
    client, _, _, _ = workspace
    review = assert_ok(client.get("/api/groups/unclassified/review"))
    assert review["borderlineCount"] == review["total"] == 2
    selection = assert_ok(
        client.post(
            "/api/selections",
            json={
                "query": {"group": "unclassified"},
                "queryRevision": review["revision"],
                "queryFingerprint": review["queryFingerprint"],
            },
        ),
        201,
    )
    assert selection["count"] == 2


def test_hide_set_aside_removes_only_clutter(workspace):
    client, _, _, _ = workspace
    first, second = assert_ok(client.get("/api/photos"))["items"]
    for photo, decision in ((first, "clutter"), (second, "keep")):
        assert_ok(
            client.patch(
                "/api/photos/" + photo["contentId"],
                json={
                    "expectedVersion": photo["reviewVersion"],
                    "decision": decision,
                },
            )
        )
    assert assert_ok(client.get("/api/photos?reviewed=1"))["total"] == 2
    shown = assert_ok(client.get("/api/photos?reviewed=1&hideSetAside=1"))
    assert [p["contentId"] for p in shown["items"]] == [second["contentId"]]


def test_placeholder_is_tiny_and_never_the_restricted_image(workspace, tmp_path):
    client, db, _, _ = workspace
    photo = assert_ok(client.get("/api/photos"))["items"][0]
    media = _prepared_thumbnail(db, tmp_path, photo["contentId"])
    assert photo["blurRequired"]
    assert client.get(f"/api/media/{media}").status_code == 403
    tiny = client.get(f"/api/media/{media}?placeholder=true")
    assert tiny.status_code == 200
    assert max(Image.open(io.BytesIO(tiny.content)).size) <= 12
    full = client.get(f"/api/media/{media}?reveal=true")
    assert Image.open(io.BytesIO(full.content)).size == (64, 48)
    assert full.headers["content-type"] == "image/webp"


def test_stage_totals_ignore_superseded_jobs_and_failed_files_are_listed(workspace):
    client, db, _, root = workspace
    photo = assert_ok(client.get("/api/photos"))["items"][0]
    before = assert_ok(client.get("/api/stages"))["items"][1]["total"]
    with db.write() as c:
        for state, fingerprint in (("skipped", "old"), ("failed", "new")):
            c.execute(
                "INSERT INTO stage_jobs(id,content_id,stage,fingerprint,state,error) VALUES (?,?,?,?,?,?)",
                (uid(), photo["contentId"], "prepare", fingerprint, state, "bad"),
            )
    stage = assert_ok(client.get("/api/stages"))["items"][1]
    assert stage["total"] == before + 1 and stage["counts"]["skipped"] == 1
    listed = assert_ok(client.get(f"/api/library/files?root={root}&state=unreadable"))[
        "items"
    ]
    assert [row["content_id"] for row in listed] == [photo["contentId"]]
    assert listed[0]["stageError"] == "bad"


def test_group_counts_follow_every_change_despite_reuse(workspace):
    client, _, _, _ = workspace

    def undecided():
        groups = assert_ok(client.get("/api/groups"))["items"]
        return next(g for g in groups if g["id"] == "unclassified")["undecided"]

    first = assert_ok(client.get("/api/photos"))["items"][0]
    assert undecided() == undecided() == 2
    assert_ok(
        client.patch(
            "/api/photos/" + first["contentId"],
            json={"expectedVersion": first["reviewVersion"], "decision": "later"},
        )
    )
    assert undecided() == 1


def test_covers_skip_sensitive_photos_and_prefer_undecided(workspace):
    client, db, _, _ = workspace
    first, second = assert_ok(client.get("/api/photos"))["items"]
    with db.write() as c:
        c.execute(
            "UPDATE photos SET sensitive='yes' WHERE content_id=?",
            (first["contentId"],),
        )
        bump(c)
    group = next(
        g
        for g in assert_ok(client.get("/api/groups"))["items"]
        if g["id"] == "unclassified"
    )
    assert group["coverIds"] == [second["contentId"]]


def test_protected_folders_still_protect_with_the_shared_lookup(workspace):
    client, _, _, root = workspace
    assert assert_ok(client.get("/api/photos"))["protectedCount"] == 0
    current = assert_ok(client.get("/api/settings/protection"))
    assert_ok(
        client.patch(
            "/api/settings/protection",
            json={
                "expectedVersion": current["version"],
                "value": {
                    "protectedFolders": [{"root": root, "folder": ""}],
                    "ageBefore": None,
                },
            },
        )
    )
    photos = assert_ok(client.get("/api/photos"))
    assert photos["protectedCount"] == photos["total"] > 0


def test_adding_a_missing_folder_gives_a_plain_error(workspace, tmp_path):
    client, _, _, _ = workspace
    response = client.post(
        "/api/roots", json={"path": str(tmp_path / "gone"), "name": "x"}
    )
    assert response.status_code == 422
    assert "doesn’t exist" in response.json()["detail"]


def test_duplicates_carry_photos_and_similar_excludes_exact_copies(workspace):
    client, _, _, _ = workspace
    exact = assert_ok(client.get("/api/duplicates?kind=exact"))["items"]
    assert len(exact) == 1 and len(exact[0]["paths"]) == 2
    assert exact[0]["photos"][0]["contentId"] == exact[0]["contentIds"][0]
    assert assert_ok(client.get("/api/duplicates?kind=similar"))["items"] == []


def test_library_stats_report_date_range(workspace):
    client, _, _, root = workspace
    stats = assert_ok(client.get(f"/api/library/stats?root={root}"))
    assert stats["dateRange"] == {"first": None, "last": None}
