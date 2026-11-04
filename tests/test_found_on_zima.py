"""Bugs the first real run on Zima showed: odd files, stale failures, Cancel and slow polling."""

import io
from pathlib import Path

import pytest
import test_preparation as preparation_fixtures
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from agent.__main__ import execute, report_failure
from agent.prepare import gps_value, prepare
from server import pipeline
from server.db import fingerprint
from server.queries import BASE
from worker.__main__ import tick

prepared_workspace = preparation_fixtures.prepared_workspace


def with_gps(latitude, longitude, reference="N"):
    exif = Image.Exif()
    gps = exif.get_ifd(34853)
    gps[1], gps[2] = reference, latitude
    gps[3], gps[4] = "E", longitude
    output = io.BytesIO()
    Image.new("RGB", (32, 32), (9, 90, 180)).save(output, "JPEG", exif=exif)
    return output.getvalue()


def degrees(value):
    return (IFDRational(value, 1), IFDRational(0, 1), IFDRational(0, 1))


NO_FIX = (IFDRational(0, 0), IFDRational(0, 0), IFDRational(0, 0))


def read_gps(data):
    with Image.open(io.BytesIO(data)) as image:
        return gps_value(image.getexif())


def test_a_real_location_is_still_read():
    assert read_gps(with_gps(degrees(17), degrees(78))) == {"lat": 17.0, "lon": 78.0}
    assert read_gps(with_gps(degrees(17), degrees(78), "S"))["lat"] == -17.0


@pytest.mark.parametrize(
    "latitude,longitude",
    [(NO_FIX, NO_FIX), (degrees(91), degrees(78)), (degrees(17), degrees(181))],
)
def test_an_impossible_location_is_no_location(latitude, longitude):
    assert read_gps(with_gps(latitude, longitude)) is None


def test_short_gps_values_are_no_location():
    class Short:
        def get_ifd(self, tag):
            return {1: "N", 2: (1.0,), 3: "E", 4: (1.0,)}

    assert gps_value(Short()) is None


def test_a_photo_with_no_gps_fix_prepares_under_the_same_contract(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    (source / "nofix.jpg").write_bytes(with_gps(NO_FIX, NO_FIX))
    client.post(f"/api/roots/{root}/scans", json={})
    tick(db)
    execute(client, claim, {root: str(source)}, config)
    job = client.post(
        "/api/agent/claim",
        json={
            "owner": "laptop",
            "stage": "prepare",
            "fingerprint": claim["fingerprint"],
        },
    ).json()
    assert job["relativePath"] == "nofix.jpg"
    assert execute(client, job, {root: str(source)}, config)["accepted"] is True
    assert client.get("/api/photos/" + job["sha256"]).json().get("gps") is None
    # This must not change the contract, or the whole library would be prepared again.
    assert fingerprint(config.model_dump()) == claim["fingerprint"]


def test_a_tiny_fake_jpg_says_what_it_is(prepared_workspace):
    _, _, source, _, config, claim = prepared_workspace
    with pytest.raises(ValueError, match=r"Not a readable image \(72 bytes\)"):
        prepare(source, {**claim, "relativePath": "x.jpg"}, config, data=b"x" * 72)


def test_an_overlong_camera_name_is_cut(prepared_workspace):
    _, _, source, _, config, claim = prepared_workspace
    exif = Image.Exif()
    exif[271] = "M" * 500
    output = io.BytesIO()
    Image.new("RGB", (32, 32)).save(output, "JPEG", exif=exif)
    result = prepare(source, claim, config, data=output.getvalue())
    assert len(result.camera) == 200


def failed_job(client, config, root, source, claim):
    report_failure(client, claim, ValueError("Not a readable image (72 bytes)"))
    return client.get("/api/pipeline/jobs", params={"state": "failed"}).json()


def test_the_failure_list_names_the_file_and_reason(prepared_workspace):
    client, _, source, root, config, claim = prepared_workspace
    listed = failed_job(client, config, root, source, claim)
    assert listed["total"] == 1
    row = listed["items"][0]
    assert row["filename"] == "IMG_20200203.jpg"
    assert row["size"] > 0
    assert row["error"].startswith("Not a readable image")


def test_changing_contract_retires_its_failures_and_retry_skips_them(
    prepared_workspace,
):
    client, db, source, root, config, claim = prepared_workspace
    failed_job(client, config, root, source, claim)
    other = {**config.model_dump(), "runtime": "other"}
    client.post("/api/pipeline/preparation/configure", json=other)
    stage = client.get("/api/stages").json()["items"][1]
    assert not stage["counts"].get("failed")
    # A failure from before this fix: it can't be claimed, so Retry must not owe it.
    with db.write() as c:
        c.execute(
            "UPDATE stage_jobs SET state='failed',error='old' WHERE fingerprint=?",
            (claim["fingerprint"],),
        )
    client.post("/api/analyze/retry")
    with db.read() as c:
        row = c.execute(
            "SELECT state FROM stage_jobs WHERE fingerprint=?", (claim["fingerprint"],)
        ).fetchone()
    assert row["state"] == "failed"


def test_the_migration_retires_only_failures_of_replaced_contracts(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    other = {**config.model_dump(), "runtime": "other"}
    digest = client.post("/api/pipeline/preparation/configure", json=other).json()[
        "fingerprint"
    ]
    with db.write() as c:
        c.execute(
            "UPDATE stage_jobs SET state='failed',error='old' WHERE fingerprint IN (?,?)",
            (claim["fingerprint"], digest),
        )
        migration = Path(pipeline.__file__).parent / "migrations"
        c.execute((migration / "012_supersede_failed_prepare.sql").read_text())
    with db.read() as c:
        states = dict(
            c.execute("SELECT fingerprint,state FROM stage_jobs WHERE stage='prepare'")
        )
    assert states[claim["fingerprint"]] == "skipped"
    assert states[digest] == "failed"


def test_start_again_after_cancel_really_starts_again(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    (source / "second.jpg").write_bytes(with_gps(degrees(1), degrees(1)))
    scan = client.post(f"/api/roots/{root}/scans", json={"folder": ""}).json()
    client.post("/api/analyze/cancel")
    body = {
        "owner": "laptop",
        "stage": "prepare",
        "fingerprint": claim["fingerprint"],
    }
    assert client.post("/api/agent/claim", json=body).json() is None
    assert client.get("/api/scans/" + scan["id"]).json()["state"] == "cancelled"

    client.post("/api/analyze/resume")
    assert client.get("/api/stages").json()["control"]["state"] == "running"
    assert client.get("/api/scans/" + scan["id"]).json()["state"] == "queued"
    assert tick(db)
    assert client.post("/api/agent/claim", json=body).json()


def test_start_again_leaves_an_older_cancelled_scan_alone(prepared_workspace):
    client, db, _, root, _, _ = prepared_workspace
    old = client.post(f"/api/roots/{root}/scans", json={"folder": ""}).json()
    client.post("/api/analyze/cancel")
    client.post("/api/analyze/resume")
    assert tick(db)
    client.post(f"/api/roots/{root}/scans", json={"folder": ""}).json()
    assert tick(db)
    client.post("/api/analyze/pause")
    client.post("/api/analyze/resume")
    assert client.get("/api/scans/" + old["id"]).json()["state"] == "done"
    assert not tick(db)


def test_quick_counts_match_the_full_query(prepared_workspace):
    client, db, source, root, config, claim = prepared_workspace
    execute(client, claim, {root: str(source)}, config)
    (source / "second.jpg").write_bytes(with_gps(degrees(1), degrees(1)))
    client.post(f"/api/roots/{root}/scans", json={})
    tick(db)
    quick = client.get("/api/stages").json()["items"]
    scoped = client.get("/api/stages", params={"root": root}).json()["items"]
    with db.read() as c:
        full = c.execute(
            BASE
            + "SELECT j.stage,j.state,COUNT(*) n FROM stage_jobs j JOIN base b ON b.content_id=j.content_id GROUP BY j.stage,j.state"
        ).fetchall()
    assert [s["counts"] for s in quick[1:]] == [s["counts"] for s in scoped[1:]]
    assert quick[1]["counts"] == {
        row["state"]: row["n"] for row in full if row["stage"] == "prepare"
    }


def test_a_photo_with_no_files_left_is_not_counted(prepared_workspace):
    client, db, *_ = prepared_workspace
    with db.write() as c:
        c.execute("UPDATE stage_jobs SET source_instance=NULL")
        c.execute("DELETE FROM file_versions")
        c.execute("DELETE FROM file_instances")
    assert client.get("/api/stages").json()["items"][1]["counts"] == {}
