"""From a filled index to groups. Vectors are arithmetic fixtures, not model results."""

import json
import math
import re
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from agent.embed import PROMPTS, Gateway, Unavailable, prompt_pack
from server.app import create_app
from server.embeddings import choose_thresholds, rank
from worker.__main__ import tick

DIMENSION = 16
PEOPLE, SCREENSHOTS, MEMES = 0, 1, 2


def axis(index):
    return [float(i == index) for i in range(DIMENSION)]


def leaning(index, other, margin):
    """A unit vector whose score for `index` beats the one for `other` by `margin`."""
    # cos(t) - sin(t) = margin
    angle = math.acos(margin / math.sqrt(2)) - math.pi / 4
    vector = np.zeros(DIMENSION, dtype="<f4")
    vector[index], vector[other] = math.cos(angle), math.sin(angle)
    return vector / np.linalg.norm(vector)


@pytest.fixture
def library(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for number in range(24):
        (source / f"photo{number:02}.jpg").write_bytes(b"photo %d" % number)
    app = create_app(tmp_path / "data" / "state.sqlite3", agent_token="test")
    client = TestClient(app, headers={"Authorization": "Bearer test"})
    root = client.post(
        "/api/roots", json={"name": "Fixture library", "path": str(source)}
    ).json()["id"]
    client.post(f"/api/roots/{root}/scans", json={})
    assert tick(app.state.db)
    config = {
        "backend": "siglip2",
        "checkpoint": "test-fixture-only",
        "revision": "a" * 40,
        "dimension": DIMENSION,
        "preprocessing": {"testOnly": True},
        "precision": "float32",
        "normalization": "l2",
        "runtime": "fixture-v1",
    }
    index = client.post("/api/indexes", json=config).json()
    assert (
        client.post("/api/pipeline/embedding/configure", json=config).status_code == 200
    )
    with app.state.db.read() as c:
        contents = [
            row[0]
            for row in c.execute("SELECT content_id FROM photos ORDER BY filename")
        ]
    return client, app.state.db, index, contents


def fill(db, index, vectors):
    with db.write() as c:
        for content, vector in vectors.items():
            c.execute(
                "INSERT INTO embeddings VALUES (?,?,?)",
                (content, index["id"], np.asarray(vector, dtype="<f4").tobytes()),
            )


def save_pack(client, index, version="fixture-v1"):
    response = client.post(
        f"/api/indexes/{index['id']}/prompts",
        json={
            "version": version,
            "indexFingerprint": index["fingerprint"],
            "prompts": [
                {"category": "people", "text": "family", "vector": axis(PEOPLE)},
                {"category": "screenshots", "text": "ui", "vector": axis(SCREENSHOTS)},
                {"category": "memes", "text": "meme", "vector": axis(MEMES)},
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def set_dates(db, dated):
    with db.write() as c:
        for content, date in dated.items():
            c.execute(
                "UPDATE photos SET capture_date=?,capture_source='exif' WHERE content_id=?",
                (date, content),
            )


def members_of(db, sample):
    with db.read() as c:
        return json.loads(
            c.execute(
                "SELECT members FROM comparison_samples WHERE id=?", (sample,)
            ).fetchone()[0]
        )


def test_labels_without_splits_keep_each_moment_on_one_side(library):
    client, db, _, contents = library
    # A burst, then photos hours apart: only the burst must share a side.
    burst = contents[:6]
    set_dates(db, {c: f"2024-05-01T10:00:{i:02}" for i, c in enumerate(burst)})
    set_dates(
        db,
        {c: f"2024-05-{i + 2:02}T10:00:00" for i, c in enumerate(contents[6:12])},
    )
    labels = [{"contentId": c, "category": "personal"} for c in contents[:12]]
    labels += [{"contentId": c, "category": "screenshots"} for c in contents[12:]]
    saved = client.post("/api/comparison/samples", json={"members": labels})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["members"] == 24 and body["train"] and body["test"]
    assert body["categories"] == {"personal": 12, "screenshots": 12}
    frozen = members_of(db, body["id"])
    assert len({m["split"] for m in frozen if m["contentId"] in burst}) == 1
    assert len({m["eventGroup"] for m in frozen if m["contentId"] in burst}) == 1
    for kind in ("personal", "screenshots"):
        assert {m["split"] for m in frozen if m["category"] == kind} == {
            "train",
            "test",
        }
    # Labelling order doesn't decide the split.
    again = client.post("/api/comparison/samples", json={"members": labels[::-1]})
    assert {m["contentId"]: m["split"] for m in frozen} == {
        m["contentId"]: m["split"] for m in members_of(db, again.json()["id"])
    }


def test_existing_event_is_the_split_group_and_events_stay_apart(library):
    client, db, _, contents = library
    with db.write() as c:
        for name, members in (("one", contents[:2]), ("two", contents[2:4])):
            c.execute(
                "INSERT INTO events(id,name,status) VALUES (?,?,'confirmed')",
                (name, name),
            )
            for content in members:
                c.execute(
                    "INSERT INTO event_members(content_id,event_id,source) VALUES (?,?,'ai')",
                    (content, name),
                )
    # The same minute would chain them into one moment; the two events must still stay apart.
    set_dates(db, {c: "2024-05-01T10:00:00" for c in contents[:4]})
    saved = client.post(
        "/api/comparison/samples",
        json={
            "members": [{"contentId": c, "category": "personal"} for c in contents[:4]]
        },
    )
    assert saved.status_code == 200, saved.text
    groups = {
        m["contentId"]: m["eventGroup"] for m in members_of(db, saved.json()["id"])
    }
    assert groups[contents[0]] == groups[contents[1]] == "one"
    assert groups[contents[2]] == groups[contents[3]] == "two"


def test_sample_rejects_half_given_splits_and_a_single_moment(library):
    client, db, _, contents = library
    half = [
        {
            "contentId": contents[0],
            "category": "people",
            "eventGroup": "a",
            "split": "test",
        },
        {"contentId": contents[1], "category": "people"},
    ]
    assert (
        client.post("/api/comparison/samples", json={"members": half}).status_code
        == 422
    )
    set_dates(db, {c: "2024-05-01T10:00:00" for c in contents[:3]})
    together = [{"contentId": c, "category": "people"} for c in contents[:3]]
    refused = client.post("/api/comparison/samples", json={"members": together})
    assert refused.status_code == 422 and "two separate moments" in refused.text
    unknown = [{"contentId": "nope", "category": "people"}, *together]
    assert (
        client.post("/api/comparison/samples", json={"members": unknown}).status_code
        == 404
    )


def test_prompt_pack_is_bound_to_the_index_and_frozen_per_version(library):
    client, _, index, _ = library
    body = {
        "version": "v1",
        "indexFingerprint": "another-model",
        "prompts": [
            {"category": "people", "text": "a", "vector": axis(0)},
            {"category": "memes", "text": "b", "vector": axis(1)},
        ],
    }
    url = f"/api/indexes/{index['id']}/prompts"
    assert client.post(url, json=body).status_code == 409
    body["indexFingerprint"] = index["fingerprint"]
    first = client.post(url, json=body)
    assert first.status_code == 200 and first.json()["created"] is True
    # The same wording encoded again is a no-op: the first vectors stay in force.
    body["prompts"][0]["vector"] = axis(2)
    again = client.post(url, json=body)
    assert again.status_code == 200 and again.json()["created"] is False
    body["prompts"][0]["text"] = "changed"
    assert client.post(url, json=body).status_code == 409
    siblings = {
        "version": "v2",
        "indexFingerprint": index["fingerprint"],
        "prompts": [
            {"category": "people", "text": "a", "vector": axis(0)},
            {"category": "places", "text": "b", "vector": axis(1)},
        ],
    }
    assert client.post(url, json=siblings).status_code == 422


def test_thresholds_hold_each_band_to_its_own_target():
    top = [(0.5 - i * 0.01, True) for i in range(10)]
    middle = [(0.2 - i * 0.01, i != 0) for i in range(6)]
    bottom = [(0.1 - i * 0.01, False) for i in range(5)]
    chosen = choose_thresholds(top + middle + bottom)
    assert chosen["highProven"] and chosen["mediumProven"]
    assert chosen["high"] == pytest.approx(
        0.41
    )  # the wrong photo at 0.2 stops High here
    # 5 of the 6 photos in the band are right. Counting High's photos too would have let two
    # of the wrong ones below in, at 0.09.
    assert chosen["medium"] == pytest.approx(0.15)
    # A band that is mostly wrong stays empty, however good the photos above it are.
    carried = choose_thresholds(top + bottom)
    assert carried["highProven"] and not carried["mediumProven"]
    assert carried["medium"] == pytest.approx(carried["high"] - 1e-6)
    # Too few labels, or too wrong, proves nothing, and nothing can reach High.
    for unproven in ([(0.5, True)] * 4, [(0.5, False)] * 20, []):
        result = choose_thresholds(unproven)
        assert not result["highProven"] and not result["mediumProven"]
        assert result["high"] == 2.0 and result["medium"] < 2.0


def labelled_library(library):
    """Six train and six held-out moments per kind, with one personal photo ranked as a meme."""
    client, db, index, contents = library
    members, vectors = [], {}
    for number, content in enumerate(contents):
        split = "train" if number % 2 == 0 else "test"
        personal = number < 12
        members.append(
            {
                "contentId": content,
                "category": "personal" if personal else "screenshots",
                "eventGroup": f"moment-{number}",
                "split": split,
            }
        )
        margin = 0.2 + number * 0.02
        vectors[content] = (
            leaning(PEOPLE, SCREENSHOTS, margin)
            if personal
            else leaning(SCREENSHOTS, PEOPLE, margin)
        )
    wrong = contents[1]
    vectors[wrong] = leaning(MEMES, PEOPLE, 0.9)
    fill(db, index, vectors)
    save_pack(client, index)
    sample = client.post("/api/comparison/samples", json={"members": members}).json()
    report = client.post(
        f"/api/indexes/{index['id']}/evaluate",
        json={"sampleId": sample["id"], "promptVersion": "fixture-v1"},
    )
    assert report.status_code == 200, report.text
    return client, db, index, contents, report.json(), wrong


def test_report_measures_held_out_photos_with_cut_offs_from_train(library):
    client, db, index, contents, report, _ = labelled_library(library)
    assert report["testCount"] == 12 and report["trainCount"] == 12
    assert report["sources"] == [{"name": "Fixture library", "photos": 24}]
    assert report["accuracy"] == pytest.approx(11 / 12)
    # A "people" ranking is right for a Personal label: they are one family.
    assert report["categories"]["personal"]["support"] == 6
    assert report["categories"]["personal"]["recall"] == pytest.approx(5 / 6)
    assert report["confusion"]["personal"] == {"personal": 5, "memes": 1}
    assert report["personalAndDocumentsAsClutter"] == {
        "total": 6,
        "suggestedClutter": 1,
        "inHighBand": 1,
    }
    assert report["thresholds"]["chosenFrom"] == "train"
    assert report["thresholds"]["highProven"]
    assert sum(band["photos"] for band in report["heldOutBands"].values()) == 12
    assert report["performanceMeasured"] is False
    overview = client.get("/api/comparison").json()
    assert overview["evaluations"][0]["id"] == report["id"]
    assert overview["samples"][0]["test"] == 12
    assert overview["promptPacks"] == [{"version": "fixture-v1", "prompts": 3}]
    # Every step so far leaves the index and the photos as they were.
    assert overview["role"] == "eval" and overview["scored"] == 0
    photo = client.get("/api/photos/" + contents[0]).json()
    assert photo["groupId"] == "unclassified"
    assert not [row for row in photo["evidence"] if row["source"] == "siglip2"]


def test_scoring_groups_photos_without_touching_decisions(library):
    client, db, index, contents, report, wrong = labelled_library(library)
    kept, cluttered = contents[3], contents[20]
    for content, decision in ((kept, "keep"), (cluttered, "clutter")):
        version = client.get("/api/photos/" + content).json()["reviewVersion"]
        assert (
            client.patch(
                "/api/photos/" + content,
                json={"expectedVersion": version, "decision": decision},
            ).status_code
            == 200
        )
    body = {
        "indexId": index["id"],
        "promptVersion": report["promptVersion"],
        "evaluationId": report["id"],
        "highMargin": report["thresholds"]["high"],
        "mediumMargin": report["thresholds"]["medium"],
    }
    refused = client.post("/api/pipeline/score", json={"query": {}, **body})
    assert refused.status_code == 409  # not promoted yet
    promoted = client.post(
        f"/api/indexes/{index['id']}/promote", json={"evaluationId": report["id"]}
    )
    assert promoted.status_code == 200, promoted.text
    assert client.get("/api/photos/" + kept).json()["decision"] == "keep"

    page, scored = 1, 0
    while page:
        result = client.post(
            "/api/pipeline/score", json={"query": {"page": page, "limit": 5}, **body}
        ).json()
        assert result["decisionsChanged"] == 0 and result["scored"] <= 5
        scored += result["scored"]
        page = result["nextPage"]
    assert scored == 22  # the two reviewed photos stay out of an undecided run

    undecided = client.get("/api/photos", params={"limit": 100}).json()["items"]
    assert not [p for p in undecided if p["groupId"] == "unclassified"]
    assert {p["band"] for p in undecided} >= {"high"}
    for photo in undecided:
        assert photo["decision"] == "undecided"
        assert photo["sensitive"] == "unknown"
        assert photo["triage"] == "needs_review"  # no D3, so never a clutter suggestion
        assert any(
            row["source"] == "siglip2" and "not a probability" in row["text"]
            for row in photo["evidence"]
        )
    # A personal photo ranked as a meme with a large margin is still not a clutter suggestion.
    risky = client.get("/api/photos/" + wrong).json()
    assert risky["groupId"] == "memes" and risky["triage"] != "clutter_candidate"
    assert client.get("/api/photos/" + kept).json()["decision"] == "keep"
    assert client.get("/api/photos/" + cluttered).json()["decision"] == "clutter"

    # The same run again changes nothing, so selections and undo aren't disturbed.
    before = {p["contentId"]: p["reviewVersion"] for p in undecided}
    again = client.post(
        "/api/pipeline/score", json={"query": {"limit": 100}, **body}
    ).json()
    assert again["scored"] == 22 and again["changed"] == 0
    after = client.get("/api/photos", params={"limit": 100}).json()["items"]
    assert before == {p["contentId"]: p["reviewVersion"] for p in after}
    assert client.get("/api/comparison").json()["role"] == "primary"
    stage = next(
        item
        for item in client.get("/api/stages").json()["items"]
        if item["name"] == "group"
    )
    assert (stage["done"], stage["total"], stage["state"]) == (22, 22, "done")


def test_margin_is_measured_against_another_family():
    prompts = [{"category": name} for name in ("people", "places", "memes")]
    matrix = np.asarray([axis(PEOPLE), axis(PEOPLE), axis(SCREENSHOTS)], np.float32)
    group, rival, margin, _ = rank(prompts, matrix, leaning(PEOPLE, SCREENSHOTS, 0.5))
    assert group in ("people", "places") and rival == "memes"
    assert margin == pytest.approx(0.5, abs=1e-5)


def text_gateway(encode):
    received = []

    def handle(request):
        body = json.loads(request.content)
        received.append(body)
        return encode(body)

    return (
        Gateway("http://gateway.test", "token", transport=httpx.MockTransport(handle)),
        received,
    )


def test_prompt_pack_encodes_in_bounded_batches_under_the_exact_contract():
    def encode(body):
        return httpx.Response(
            200,
            json={
                "fingerprint": "gateway",
                "data": [
                    {"embedding": axis(i % DIMENSION)}
                    for i in range(len(body["input"]))
                ],
            },
        )

    gateway, received = text_gateway(encode)
    target = {"gatewayFingerprint": "gateway", "fingerprint": "index"}
    pack = prompt_pack(gateway, target)
    assert pack["indexFingerprint"] == "index"
    assert all(1 <= len(body["input"]) <= 8 for body in received)
    assert [p["text"] for p in pack["prompts"]] == [
        text for texts in PROMPTS.values() for text in texts
    ]
    with pytest.raises(Unavailable):
        prompt_pack(gateway, {**target, "gatewayFingerprint": "a-different-model"})


def test_prompt_pack_never_pairs_short_replies_with_the_wrong_text():
    gateway, _ = text_gateway(
        lambda body: httpx.Response(
            200, json={"fingerprint": "gateway", "data": [{"embedding": axis(0)}]}
        )
    )
    with pytest.raises(Unavailable):
        prompt_pack(gateway, {"gatewayFingerprint": "gateway", "fingerprint": "index"})


def test_settings_screen_shows_the_prompts_that_are_encoded():
    source = (
        Path(__file__).parent.parent / "web" / "src" / "screens-workflow.tsx"
    ).read_text(encoding="utf-8")
    block = re.search(r"const PROMPTS[^=]*= \{(.*?)\n\};", source, re.DOTALL).group(1)
    shown = {
        name: re.findall(r"'([^']+)'", texts)
        for name, texts in re.findall(r"(\w+): \[([^\]]*)\]", block)
    }
    assert shown == PROMPTS
