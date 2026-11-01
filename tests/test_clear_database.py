"""Clear database starts the library over without touching originals or the owner's setup."""

import test_preparation as preparation_fixtures
from test_assessment import D3
from test_embedding_dispatch import GATEWAY_CONFIG, GATEWAY_FINGERPRINT
from test_embedding_dispatch import gateway as embedding_gateway

from agent.__main__ import execute, execute_embedding
from agent.assess import contract
from agent.embed import index_config
from server import clear
from worker.__main__ import tick

prepared_workspace = preparation_fixtures.prepared_workspace
CONFIRM = {"confirm": "CLEAR"}


def filled(workspace):
    """A library with a prepared and embedded photo, a decision and saved setup."""
    client, db, source, root, config, claim = workspace
    execute(client, claim, {root: str(source)}, config)
    active = client.post(
        "/api/pipeline/embedding/configure", json=index_config(GATEWAY_CONFIG)
    ).json()
    target = {**active, "gatewayFingerprint": GATEWAY_FINGERPRINT}
    job = client.post(
        "/api/agent/claim",
        json={
            "owner": "laptop",
            "stage": "embed",
            "fingerprint": target["fingerprint"],
        },
    ).json()
    execute_embedding(client, job, {root: str(source)}, embedding_gateway()[0], target)
    client.post("/api/pipeline/assessment/configure", json=contract(D3))
    photo = client.get("/api/photos/" + claim["sha256"]).json()
    decided = client.patch(
        "/api/photos/" + claim["sha256"],
        json={"expectedVersion": photo["reviewVersion"], "decision": "keep"},
    )
    assert decided.status_code == 200, decided.text
    return client, db, source, root, config, claim


def count(db, table):
    with db.read() as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_every_table_is_a_deliberate_choice(prepared_workspace):
    _, db, *_ = prepared_workspace
    with db.read() as c:
        tables = {
            row[0]
            for row in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == set(clear.ERASED) | set(clear.KEPT)
    assert not set(clear.ERASED) & set(clear.KEPT)


def test_clear_erases_the_library_and_keeps_setup_and_originals(prepared_workspace):
    client, db, source, root, _, claim = filled(prepared_workspace)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
    cache = db.path.parent / "cache"
    assert cache.exists() and count(db, "embeddings") == 1
    with db.write() as c:
        c.execute(
            "INSERT INTO comparison_samples VALUES ('saved','[]','2026-01-01T00:00:00')"
        )
    client.post("/api/analyze/pause")

    response = client.post("/api/database/clear", json=CONFIRM)
    assert response.status_code == 200, response.text
    assert response.json()["cleared"] is True

    for table in clear.ERASED:
        assert count(db, table) == 0, table
    assert not cache.exists()
    assert client.get("/api/photos/" + claim["sha256"]).status_code == 404
    assert client.get("/api/photos").json()["total"] == 0
    # Setup stays: libraries, labels, the index and both model contracts.
    assert [r["id"] for r in client.get("/api/roots").json()] == [root]
    assert count(db, "comparison_samples") == 1
    assert count(db, "embedding_indexes") == 1
    assert client.get("/api/pipeline/embedding").json()
    assert client.get("/api/pipeline/assessment").json()
    stages = client.get("/api/stages").json()
    assert stages["preparationConfigured"] is True
    assert stages["control"]["state"] == "running"
    assert {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()
    } == before


def test_nothing_runs_until_the_owner_scans_again(prepared_workspace):
    client, db, _, root, config, claim = filled(prepared_workspace)
    client.post("/api/database/clear", json=CONFIRM)
    assert not tick(db)
    assert count(db, "stage_jobs") == 0
    assert (
        client.post(
            f"/api/roots/{root}/scans", json={"folder": "", "verify": False}
        ).status_code
        == 202
    )
    assert tick(db)
    # The same photo comes back undecided, and queues under the kept contract.
    photo = client.get("/api/photos/" + claim["sha256"]).json()
    assert photo["decision"] == "undecided"
    job = client.post(
        "/api/agent/claim",
        json={
            "owner": "laptop",
            "stage": "prepare",
            "fingerprint": claim["fingerprint"],
        },
    ).json()
    assert job and job["sha256"] == claim["sha256"]
    assert execute(client, job, {}, config)["accepted"] is True
    assert count(db, "stage_jobs") >= 3


def test_needs_the_typed_word(prepared_workspace):
    client, _, _, _, _, claim = filled(prepared_workspace)
    for body in ({}, {"confirm": "clear"}, {"confirm": "yes"}):
        assert client.post("/api/database/clear", json=body).status_code == 422
    assert client.get("/api/photos/" + claim["sha256"]).status_code == 200


def test_refused_while_work_is_running(prepared_workspace):
    client, _, _, root, _, claim = filled(prepared_workspace)
    client.post(f"/api/roots/{root}/scans", json={"folder": "", "verify": False})
    response = client.post("/api/database/clear", json=CONFIRM)
    assert response.status_code == 409
    assert "scan" in response.json()["detail"].lower()
    assert client.get("/api/photos/" + claim["sha256"]).status_code == 200


def test_adding_a_library_does_not_scan_it(prepared_workspace, tmp_path):
    client, db, *_ = prepared_workspace
    tick(db)
    folder = tmp_path / "second"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(b"not read until asked")
    before = count(db, "scan_runs")
    added = client.post("/api/roots", json={"name": "Second", "path": str(folder)})
    assert added.status_code == 201, added.text
    assert count(db, "scan_runs") == before
    assert not tick(db)


def test_browse_stays_inside_the_allowed_places(
    prepared_workspace, tmp_path, monkeypatch
):
    client, *_ = prepared_workspace
    place = tmp_path / "library"
    (place / "Family" / "2019").mkdir(parents=True)
    (place / ".hidden").mkdir()
    (place / "photo.jpg").write_bytes(b"a file, not a folder")
    outside = tmp_path / "private"
    outside.mkdir()
    monkeypatch.setenv("PHOTOSIFT_BROWSE_ROOTS", str(place))

    start = client.get("/api/browse").json()
    assert [f["path"] for f in start["folders"]] == [str(place)]
    top = client.get("/api/browse", params={"path": str(place)}).json()
    assert [f["name"] for f in top["folders"]] == ["Family"]
    assert top["parent"] is None
    inner = client.get("/api/browse", params={"path": str(place / "Family")}).json()
    assert inner["parent"] == str(place)
    assert [f["name"] for f in inner["folders"]] == ["2019"]
    for blocked in (str(outside), str(place / ".." / "private"), str(tmp_path)):
        assert client.get("/api/browse", params={"path": blocked}).status_code == 403
    assert (
        client.get("/api/browse", params={"path": str(place / "missing")}).status_code
        == 422
    )
