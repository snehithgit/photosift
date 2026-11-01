import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from worker.__main__ import tick


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"first image")
    (source / "b.png").write_bytes(b"second image")
    (source / "copies").mkdir()
    (source / "copies" / "a-copy.jpg").write_bytes(b"first image")
    (source / "notes.txt").write_text("unsupported source", encoding="utf-8")
    app = create_app(
        tmp_path / "data" / "state.sqlite3", agent_token="test-agent-token"
    )
    client = TestClient(app)
    response = client.post(
        "/api/roots", json={"name": "Test library", "path": str(source)}
    )
    assert response.status_code == 201, response.text
    root = response.json()["id"]
    response = client.post(f"/api/roots/{root}/scans", json={})
    assert response.status_code == 202, response.text
    assert tick(app.state.db)
    return client, app.state.db, source, root


def assert_ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()
