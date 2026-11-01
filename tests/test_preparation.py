import hashlib
import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from agent.__main__ import execute
from agent.prepare import configuration, prepare
from server.app import create_app
from worker.__main__ import tick
from worker.scanner import queue_scan, scan


@pytest.fixture
def prepared_workspace(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    image = Image.new("RGB", (80, 40), (80, 130, 190))
    exif = Image.Exif()
    exif[274] = 6
    exif[271] = "Test Camera"
    exif[306] = "2020:02:03 12:34:56"
    path = source / "IMG_20200203.jpg"
    image.save(path, exif=exif)
    app = create_app(tmp_path / "app" / "state.sqlite3", agent_token="test")
    client = TestClient(app, headers={"Authorization": "Bearer test"})
    root = client.post(
        "/api/roots", json={"name": "Library", "path": str(source)}
    ).json()["id"]
    client.post("/api/roots/" + root + "/scans", json={})
    tick(app.state.db)
    config = configuration()
    response = client.post(
        "/api/pipeline/preparation/configure", json=config.model_dump()
    )
    assert response.status_code == 200, response.text
    digest = response.json()["fingerprint"]
    response = client.post(
        "/api/agent/claim",
        json={"owner": "laptop", "stage": "prepare", "fingerprint": digest},
    )
    assert response.status_code == 200, response.text
    return client, app.state.db, source, root, config, response.json()


def test_real_decode_and_cache_per_image_ack_replay(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
    ack = execute(client, claim, {root: str(source)}, config)
    assert ack["accepted"] is True
    photo = client.get("/api/photos/" + claim["sha256"]).json()
    assert (photo["width"], photo["height"]) == (40, 80)
    assert photo["captureDate"] == "2020-02-03T12:34:56"
    assert photo["captureDateSource"] == "exif"
    assert photo["faces"] == "not_checked"
    assert photo["sensitive"] == photo["importance"] == "unknown"
    assert photo["triage"] == "needs_review"
    assert photo["thumbUrl"] and photo["previewUrl"]
    assert client.get(photo["thumbUrl"]).status_code == 403
    cache = client.get(photo["thumbUrl"] + "?reveal=true")
    assert cache.status_code == 200
    with Image.open(io.BytesIO(cache.content)) as image:
        assert image.format == "WEBP" and max(image.size) <= 256
        assert not image.getexif()
    with db.read() as c:
        saved = c.execute(
            "SELECT payload FROM stage_results WHERE job_id=?", (claim["jobId"],)
        ).fetchone()[0]
    import json

    body = {key: claim[key] for key in ("owner", "token", "attempt", "fingerprint")}
    body.update({"sha256": claim["sha256"], "payload": json.loads(saved)})
    assert (
        client.post("/api/agent/jobs/" + claim["jobId"] + "/result", json=body).json()
        == ack
    )
    assert {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()
    } == before
    assert (
        client.post(
            "/api/agent/claim",
            json={
                "owner": "laptop",
                "stage": "prepare",
                "fingerprint": claim["fingerprint"],
            },
        ).json()
        is None
    )


def result_body(source, config, claim):
    body = {key: claim[key] for key in ("owner", "token", "attempt", "fingerprint")}
    return {
        **body,
        "sha256": claim["sha256"],
        "payload": prepare(source, claim, config).model_dump(),
    }


def test_reject_stale_source_after_preparation(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    body = result_body(source, config, claim)
    Image.new("RGB", (30, 30), "white").save(source / claim["relativePath"])
    scan(db, queue_scan(db, root))
    response = client.post("/api/agent/jobs/" + claim["jobId"] + "/result", json=body)
    assert response.status_code == 409, response.text
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM stage_results").fetchone()[0] == 0


def test_payload_checks_unknown_faces_and_expired_leases(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    body = result_body(source, config, claim)
    body["payload"]["faceStatus"] = "done"
    assert (
        client.post(
            "/api/agent/jobs/" + claim["jobId"] + "/result", json=body
        ).status_code
        == 422
    )
    body["payload"]["faceStatus"] = "not_checked"
    body["payload"]["thumbnail"] = "not valid base64"
    assert (
        client.post(
            "/api/agent/jobs/" + claim["jobId"] + "/result", json=body
        ).status_code
        == 422
    )
    with db.write() as c:
        c.execute("UPDATE stage_jobs SET expires=0 WHERE id=?", (claim["jobId"],))
    assert (
        client.post(
            "/api/agent/jobs/" + claim["jobId"] + "/result", json=body
        ).status_code
        == 409
    )


def test_hash_mismatch_and_config_change(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    bad = {**claim, "sha256": "0" * 64}
    with pytest.raises(ValueError, match="hash/version"):
        prepare(source, bad, config)
    client.post(
        "/api/pipeline/preparation/configure",
        json=config.model_copy(update={"webpQuality": 80}).model_dump(),
    )
    assert (
        client.post(
            "/api/agent/jobs/" + claim["jobId"] + "/renew",
            json={key: claim[key] for key in ("owner", "token", "attempt")},
        ).status_code
        == 409
    )


def test_face_detector_failure_remains_unknown(prepared_workspace, tmp_path):
    _, _, source, _, config, claim = prepared_workspace
    weights = tmp_path / "test.onnx"
    weights.write_bytes(b"fixture-only")
    config = config.model_copy(
        update={"faceModelSha256": hashlib.sha256(weights.read_bytes()).hexdigest()}
    )
    import cv2

    with patch(
        "agent.prepare.cv2.FaceDetectorYN.create",
        side_effect=cv2.error("fixture failure"),
    ):
        result = prepare(source, claim, config, weights)
    assert result.faceStatus == "failed" and result.faces == [] and result.faceError


def test_reactivate_exact_preparation_does_not_mix_metadata_or_cache_versions(
    prepared_workspace,
):
    client, _, source, root, config, claim = prepared_workspace
    execute(client, claim, {root: str(source)}, config)
    original = client.get("/api/photos/" + claim["sha256"]).json()
    second_config = config.model_copy(update={"webpQuality": 60})
    registration = client.post(
        "/api/pipeline/preparation/configure", json=second_config.model_dump()
    ).json()
    second_claim = client.post(
        "/api/agent/claim",
        json={
            "owner": "laptop",
            "stage": "prepare",
            "fingerprint": registration["fingerprint"],
        },
    ).json()
    execute(client, second_claim, {root: str(source)}, second_config)
    second = client.get("/api/photos/" + claim["sha256"]).json()
    assert second["thumbUrl"] != original["thumbUrl"]
    client.post("/api/pipeline/preparation/configure", json=config.model_dump())
    restored = client.get("/api/photos/" + claim["sha256"]).json()
    assert restored["thumbUrl"] == original["thumbUrl"]
    assert (
        restored["width"] == original["width"]
        and restored["faces"] == original["faces"]
    )


def test_stage_failures_are_visible_and_retry_is_explicit(prepared_workspace):
    client, _, _, root, _, claim = prepared_workspace
    body = {key: claim[key] for key in ("owner", "token", "attempt")}
    response = client.post(
        "/api/agent/jobs/" + claim["jobId"] + "/fail",
        json={**body, "state": "failed", "reason": "Explicit decoder failure fixture"},
    )
    assert response.status_code == 200, response.text
    response = client.get(
        "/api/pipeline/jobs",
        params={"root": root, "stage": "prepare", "state": "failed"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert (
        result["total"] == 1
        and result["items"][0]["error"] == "Explicit decoder failure fixture"
    )
    assert "token" not in result["items"][0]
    assert (
        client.get("/api/pipeline/jobs", params={"folder": "../escape"}).status_code
        == 422
    )
    assert client.post("/api/analyze/retry").status_code == 200
    assert (
        client.get(
            "/api/pipeline/jobs", params={"root": root, "state": "failed"}
        ).json()["total"]
        == 0
    )


def test_request_body_is_bounded_before_json_parsing(prepared_workspace):
    client, _, _, _, _, claim = prepared_workspace
    response = client.post(
        "/api/agent/jobs/" + claim["jobId"] + "/result",
        content=b"x" * (16 * 1024 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_prior_positive_face_evidence_survives_repreparation(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    execute(client, claim, {root: str(source)}, config)
    with db.write() as c:
        c.execute(
            "UPDATE photos SET faces=? WHERE content_id=?",
            ('[{"box":[0.1,0.1,0.2,0.2]}]', claim["sha256"]),
        )
    second = config.model_copy(update={"webpQuality": 65})
    registration = client.post(
        "/api/pipeline/preparation/configure", json=second.model_dump()
    ).json()
    next_claim = client.post(
        "/api/agent/claim",
        json={
            "owner": "laptop",
            "stage": "prepare",
            "fingerprint": registration["fingerprint"],
        },
    ).json()
    execute(client, next_claim, {root: str(source)}, second)
    photo = client.get("/api/photos/" + claim["sha256"]).json()
    assert photo["faces"] == "not_checked"
    assert "faces" in photo["protected"]["reasons"]
    assert photo["triage"] != "clutter_candidate"
