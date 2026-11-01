"""A laptop with no path to the library gets originals from the server, under its lease."""

import io
import os

import httpx
import test_preparation as preparation_fixtures
from fastapi.testclient import TestClient
from PIL import Image
from test_assessment import D3
from test_assessment import gateway as assessment_gateway
from test_embedding_dispatch import GATEWAY_CONFIG, GATEWAY_FINGERPRINT
from test_embedding_dispatch import gateway as embedding_gateway

from agent.__main__ import execute, execute_assessment, execute_embedding
from agent.assess import contract
from agent.embed import index_config
from agent.prepare import prepare

prepared_workspace = preparation_fixtures.prepared_workspace


def lease(claim):
    return {key: claim[key] for key in ("owner", "token", "attempt")}


def url(claim):
    return "/api/agent/jobs/" + claim["jobId"] + "/source"


def claim_stage(client, stage, digest):
    return client.post(
        "/api/agent/claim",
        json={"owner": "laptop", "stage": stage, "fingerprint": digest},
    ).json()


def test_all_stages_run_with_no_mapping(prepared_workspace):
    client, db, source, _, config, claim = prepared_workspace
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
    assert execute(client, claim, {}, config)["accepted"] is True
    photo = client.get("/api/photos/" + claim["sha256"]).json()
    assert (photo["width"], photo["height"]) == (40, 80)
    assert photo["captureDate"] == "2020-02-03T12:34:56"

    active = client.post(
        "/api/pipeline/embedding/configure", json=index_config(GATEWAY_CONFIG)
    ).json()
    target = {**active, "gatewayFingerprint": GATEWAY_FINGERPRINT}
    job = claim_stage(client, "embed", target["fingerprint"])
    model, received = embedding_gateway()
    assert execute_embedding(client, job, {}, model, target)["accepted"] is True
    assert list(received[0]) == ["images"]

    target = client.post("/api/pipeline/assessment/configure", json=contract(D3)).json()
    job = claim_stage(client, "assess", target["fingerprint"])
    ack = execute_assessment(client, job, {}, assessment_gateway()[0], target)
    assert ack["accepted"] is True
    assert {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()
    } == before


def test_filename_date_works_without_a_local_path(prepared_workspace):
    _, _, _, _, config, _ = prepared_workspace
    # No EXIF here, so the date can only come from the claim's relative path.
    buffer = io.BytesIO()
    Image.new("RGB", (80, 40), "blue").save(buffer, "JPEG")
    claim = {"relativePath": "nested/IMG_20190305.jpg"}
    result = prepare(None, claim, config, data=buffer.getvalue())
    assert result.captureDate == "2019-03-05"
    assert result.captureDateSource == "filename"


def test_download_needs_token_and_current_lease(prepared_workspace):
    client, db, _, _, _, claim = prepared_workspace
    outsider = TestClient(client.app)
    assert outsider.post(url(claim), json=lease(claim)).status_code == 401
    stale = {**lease(claim), "token": "stale"}
    assert client.post(url(claim), json=stale).status_code == 409
    other = {**lease(claim), "owner": "someone-else"}
    assert client.post(url(claim), json=other).status_code == 409
    assert client.post(url(claim), json=lease(claim)).status_code == 200
    # A finished job's lease is over, so its original can't be fetched again.
    with db.write() as c:
        c.execute("UPDATE stage_jobs SET state='done' WHERE id=?", (claim["jobId"],))
    assert client.post(url(claim), json=lease(claim)).status_code == 409


def test_changed_source_is_refused(prepared_workspace):
    client, db, source, _, config, claim = prepared_workspace
    path = source / "IMG_20200203.jpg"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    assert client.post(url(claim), json=lease(claim)).status_code == 409


def test_wrong_bytes_are_refused_by_the_agent(prepared_workspace):
    client, _, _, _, _, claim = prepared_workspace
    from agent.prepare import download_verified

    def tampered(request):
        return httpx.Response(200, content=b"not the original")

    fake = httpx.Client(
        base_url="http://zima.test", transport=httpx.MockTransport(tampered)
    )
    try:
        download_verified(fake, claim, 1024 * 1024)
        raise AssertionError("Wrong bytes were accepted")
    except ValueError as exc:
        assert "mismatch" in str(exc)
