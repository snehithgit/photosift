import pytest

from server.suggestions import triage


@pytest.mark.parametrize("group", ["people", "places", "events", "documents"])
def test_personal_and_documents_never_suggest_clutter(group):
    assert (
        triage(
            {
                "decision": "undecided",
                "protected": None,
                "sensitive": "no",
                "importance": "no",
                "faces": [],
                "groupId": group,
            },
            "high",
        )
        != "clutter_candidate"
    )


@pytest.mark.parametrize("hold", ["faces", "sensitive", "importance", "protected"])
def test_unknown_required_checks_hold_suggestions(hold):
    photo = {
        "decision": "undecided",
        "protected": None,
        "sensitive": "no",
        "importance": "no",
        "faces": [],
        "groupId": "screenshots",
    }
    photo[hold] = (
        {"reasons": ["folder"]}
        if hold == "protected"
        else "not_checked"
        if hold == "faces"
        else "unknown"
    )
    assert triage(photo, "high") == "needs_review"


def test_time_neighbor_events_preserve_manual_edits_and_decisions(workspace):
    client, db, _, root = workspace
    with db.write() as c:
        c.execute(
            "UPDATE photos SET capture_date='2020-02-03T12:00:00',capture_source='exif',origin='forwarded'"
        )
        c.execute("UPDATE group_assignments SET ai_group='people'")
    config = {"query": {"root": root}, "maxGapSeconds": 120, "minPhotos": 2}
    response = client.post("/api/pipeline/suggest-events", json=config)
    assert response.status_code == 200, response.text
    assert len(response.json()["events"]) == 1
    identifier = response.json()["events"][0]["id"]
    event = client.get("/api/events/" + identifier).json()
    assert event["status"] == "suggested" and event["photoCount"] == 2
    response = client.patch(
        "/api/events/" + identifier,
        json={
            "expectedVersion": event["version"],
            "name": "My confirmed occasion",
            "status": "confirmed",
        },
    )
    assert response.status_code == 200, response.text
    response = client.post("/api/pipeline/suggest-events", json=config)
    assert response.status_code == 200 and response.json()["events"] == []
    assert (
        client.get("/api/events/" + identifier).json()["name"]
        == "My confirmed occasion"
    )
    with db.read() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM decisions WHERE decision!='undecided'"
            ).fetchone()[0]
            == 0
        )


def test_date_only_clues_do_not_manufacture_bursts(workspace):
    client, db, _, root = workspace
    with db.write() as c:
        c.execute(
            "UPDATE photos SET capture_date='2020-02-03',capture_source='filename'"
        )
        c.execute("UPDATE group_assignments SET ai_group='people'")
    response = client.post(
        "/api/pipeline/suggest-events",
        json={"query": {"root": root}, "maxGapSeconds": 120, "minPhotos": 2},
    )
    assert response.status_code == 200 and response.json()["events"] == []


def test_neighbor_suggestions_use_same_index_and_keep_distinct_images_unticked(
    workspace,
):
    import json

    import numpy as np

    from server.db import now

    client, db, _, root = workspace
    index = client.post(
        "/api/indexes",
        json={
            "backend": "siglip2",
            "checkpoint": "fixture-only",
            "revision": "a" * 40,
            "dimension": 16,
            "preprocessing": {"fixture": True},
            "precision": "float32",
            "normalization": "l2",
            "runtime": "fixture-v1",
        },
    ).json()
    vector = np.asarray([1.0] + [0.0] * 15, dtype="<f4").tobytes()
    with db.write() as c:
        c.execute(
            "UPDATE embedding_indexes SET role='primary',status='ready' WHERE id=?",
            (index["id"],),
        )
        c.execute(
            "INSERT INTO stage_specs VALUES ('fixture-prep','prepare','{}',1,?)",
            (now(),),
        )
        contents = [row[0] for row in c.execute("SELECT content_id FROM photos")]
        for content in contents:
            c.execute(
                "INSERT INTO embeddings VALUES (?,?,?)", (content, index["id"], vector)
            )
            c.execute(
                "INSERT INTO preparation_outputs VALUES (?,'fixture-prep',?,?)",
                (content, json.dumps({"pHash": "0" * 16}), now()),
            )
        c.execute(
            "UPDATE photos SET capture_date='2020-02-03T12:00:00',capture_source='exif'"
        )
    response = client.post(
        "/api/pipeline/suggest-neighbors",
        json={
            "query": {"root": root},
            "indexId": index["id"],
            "maxGapSeconds": 30,
            "minSimilarity": 0.99,
            "maxHamming": 1,
        },
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["sets"]) == 1
    assert response.json()["sets"][0]["preselected"] is False
    p = client.get("/api/photos/" + contents[0]).json()
    moved = client.patch(
        "/api/photos/" + contents[0],
        json={"expectedVersion": p["reviewVersion"], "groupId": "people"},
    )
    assert moved.status_code == 200, moved.text
    assert "duplicateSetId" not in moved.json()["photo"]
    with db.read() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM decisions WHERE decision!='undecided'"
            ).fetchone()[0]
            == 0
        )
