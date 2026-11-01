import base64
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import psutil
from fastapi.testclient import TestClient

from laptop.app import create_app
from laptop.config import Settings
from laptop.engine import Encoder
from laptop.services import identity, owned, stop_process


class FakeEncoder:
    def __init__(self):
        self.loaded = False

    def load(self):
        self.loaded = True
        return self.status()

    def unload(self):
        self.loaded = False
        return self.status()

    def status(self):
        return {
            "state": "ready" if self.loaded else "unloaded",
            "fingerprint": "fixture",
        }

    def encode(self, texts=None, images=None):
        self.load()
        return [[1.0, 0.0] for _ in (texts or images)]


def test_model_service_contract(tmp_path):
    token = tmp_path / "token"
    token.write_text("x" * 48)
    settings = Settings(tokenFile=str(token), loadOnStart=False)
    encoder = FakeEncoder()
    with TestClient(create_app(settings, encoder)) as client:
        auth = {"Authorization": "Bearer " + token.read_text()}
        assert client.get("/health").json()["d3"]["state"] == "protocol_unconfigured"
        assert (
            client.post("/v1/embeddings", json={"input": "a family photo"}).status_code
            == 401
        )
        response = client.post(
            "/v1/embeddings",
            headers=auth,
            json={"input": ["a family photo", "a receipt"]},
        )
        assert response.status_code == 200
        assert response.json()["fingerprint"] == "fixture"
        assert len(response.json()["data"]) == 2
        for invalid in (
            {"input": ""},
            {"input": []},
            {"input": "text", "images": ["a"]},
            {"images": []},
            {"path": "C:/original.jpg"},
        ):
            assert (
                client.post("/v1/embeddings", headers=auth, json=invalid).status_code
                == 422
            )
        assert (
            client.post(
                "/v1/embeddings",
                headers=auth,
                json={"input": "text", "model": "different"},
            ).status_code
            == 409
        )
        assert client.post("/v1/systemone", headers=auth, json={}).status_code == 501
        assert (
            client.post("/v1/model/unload", headers=auth).json()["state"] == "unloaded"
        )


def test_stop_requires_exact_owned_process():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        record = identity(psutil.Process(process.pid))
        changed = {**record, "created": record["created"] - 1}
        assert owned(changed) is None
        stop_process(changed)
        assert process.poll() is None
        stop_process(record)
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_encoder_input_is_not_a_source_path(tmp_path):
    # Remote clients supply bytes; the service has no original-file path input.
    from pydantic import ValidationError

    from laptop.app import EmbeddingRequest

    source = tmp_path / "original.jpg"
    source.write_bytes(b"original fixture")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        EmbeddingRequest.model_validate(
            {
                "images": [base64.b64encode(source.read_bytes()).decode()],
                "sourcePath": str(source),
            }
        )
        raise AssertionError("Source path accepted")
    except ValidationError:
        pass
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert json.loads(Settings().model_dump_json())["modelId"].startswith(
        "google/siglip2-"
    )


def test_encoder_refuses_cpu_fallback(monkeypatch):
    import pytest

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
    )
    monkeypatch.setitem(
        sys.modules, "transformers", SimpleNamespace(AutoModel=None, AutoProcessor=None)
    )
    encoder = Encoder(Settings())
    with pytest.raises(RuntimeError, match="CPU fallback is disabled"):
        encoder.load()
    assert encoder.state == "failed" and encoder.model is None


def test_encoder_refuses_changed_pinned_weights(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True, empty_cache=lambda: None)
        ),
    )
    monkeypatch.setitem(
        sys.modules, "transformers", SimpleNamespace(AutoModel=None, AutoProcessor=None)
    )
    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"original weights")
    settings = Settings(
        modelDirectory=str(tmp_path),
        fileHashes={
            "model.safetensors": hashlib.sha256(weights.read_bytes()).hexdigest()
        },
    )
    weights.write_bytes(b"changed weights")
    encoder = Encoder(settings)
    with pytest.raises(RuntimeError, match="missing or changed"):
        encoder.load()
    assert encoder.state == "failed" and encoder.model is None
