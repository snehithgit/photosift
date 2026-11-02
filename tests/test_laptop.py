import base64
import hashlib
import json
import socket
import subprocess
import sys
from types import SimpleNamespace

import psutil
from fastapi.testclient import TestClient

from laptop import control, services
from laptop.app import create_app
from laptop.config import Settings
from laptop.engine import Encoder
from laptop.services import identity, owned, port_free, stop_process


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
        assert client.get("/health").json()["d3"]["state"] in (
            "ready",
            "unavailable",
            "model_missing",
        )
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
        assert client.post("/v1/systemone", headers=auth, json={}).status_code == 422
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


def test_port_check_sees_listeners_on_any_address():
    import pytest

    for family, address in (
        (socket.AF_INET, "127.0.0.1"),
        (socket.AF_INET, "0.0.0.0"),
        (socket.AF_INET6, "::"),
    ):
        with socket.socket(family) as listener:
            listener.bind((address, 0))
            listener.listen()
            port = listener.getsockname()[1]
            with pytest.raises(RuntimeError, match="already in use"):
                port_free(port)
        port_free(port)


def isolate_services(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "SERVICE_DIRECTORY", tmp_path)
    monkeypatch.setattr(services, "STATE", tmp_path / "processes.json")
    monkeypatch.setattr(services, "AGENT_CONFIG", tmp_path / "agent.local.json")
    token = tmp_path / "token"
    token.write_text("x" * 48)
    monkeypatch.setattr(
        services,
        "load_settings",
        lambda: Settings(
            tokenFile=str(token), port=free_port(), ollamaPort=free_port()
        ),
    )


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_window_reports_stopped_services_and_changes_nothing(tmp_path, monkeypatch):
    isolate_services(tmp_path, monkeypatch)
    shown = control.snapshot()
    assert shown["SigLIP 2"] == shown["D3 Nano"] == "Stopped"
    assert shown["Agent"].startswith(("Stopped", "Not set up"))
    assert shown["Zima"].startswith("Not set up")
    assert not services.STATE.exists()


def test_window_shows_the_agents_last_line_and_a_bad_address(tmp_path, monkeypatch):
    isolate_services(tmp_path, monkeypatch)
    services.AGENT_CONFIG.write_text(
        json.dumps({"serverUrl": f"http://127.0.0.1:{free_port()}/"})
    )
    (tmp_path / "agent.log").write_text("first\nEmbedding ready\n\n")
    shown = control.snapshot()
    assert shown["Last message"] == "Embedding ready"
    assert shown["Zima"].startswith("Can't reach http://127.0.0.1:")


def test_window_passes_start_and_stop_to_the_services(tmp_path, monkeypatch):
    isolate_services(tmp_path, monkeypatch)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[1:])
        kwargs["stdout"].write(b"Install Ollama first\n")
        return SimpleNamespace(returncode=returns.pop(0))

    returns = [0, services.NOT_RUNNING, 1, 0]
    monkeypatch.setattr(control.subprocess, "run", fake_run)
    assert control.run_services("start") == "Started."
    assert "isn't ready" in control.run_services("start")
    assert control.run_services("start") == "Install Ollama first"
    assert control.run_services("stop").startswith("Stopped")
    assert calls[-1] == ["-m", "laptop.services", "stop"]


def test_nothing_is_set_to_run_at_windows_startup():
    for name in ("Add-PhotoSiftShortcut.ps1", "Setup-Laptop.ps1"):
        script = (services.PROJECT / name).read_text(encoding="utf-8").lower()
        for startup in ("startup", "schtasks", "scheduledtask", "currentversion\\run"):
            assert startup not in script


def test_status_exit_code_says_stopped(tmp_path, monkeypatch, capsys):
    isolate_services(tmp_path, monkeypatch)
    assert services.main(["status"]) == services.NOT_RUNNING
    output = capsys.readouterr().out
    assert "models: stopped" in output
    assert "baseUrl" not in output, "A stopped service must not advertise URLs"
    assert not (tmp_path / "endpoints.json").exists()
    assert services.main(["stop"]) == 0


def test_status_and_stop_work_while_start_waits(tmp_path, monkeypatch, capsys):
    isolate_services(tmp_path, monkeypatch)
    monkeypatch.setattr(services.shutil, "which", lambda name: sys.executable)
    launched = []

    def fake_launch(name, command, env, records):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        )
        launched.append(process)
        records[name] = identity(psutil.Process(process.pid))
        services.write_json(services.STATE, records)

    def fake_wait(url, predicate, record, timeout=300):
        # What a user's Status and Stop do during a long model load.
        assert services.main(["status"]) == services.NOT_RUNNING
        assert services.main(["stop"]) == 0
        real_wait(url, predicate, record, timeout)

    real_wait = services.wait_ready
    monkeypatch.setattr(services, "launch", fake_launch)
    monkeypatch.setattr(services, "wait_ready", fake_wait)
    try:
        assert services.main(["start"]) == 1
        assert "Stop was run" in capsys.readouterr().err
        assert services.read_records() == {}
        for process in launched:
            process.wait(timeout=10)
    finally:
        for process in launched:
            if process.poll() is None:
                process.kill()
                process.wait()


def test_agent_starts_only_when_set_up(tmp_path, monkeypatch):
    isolate_services(tmp_path, monkeypatch)
    assert services.agent_command() is None
    (tmp_path / "agent.local.json").write_text("{}")
    python = services.PROJECT / ".venv" / "Scripts" / "python.exe"
    if python.is_file():
        command = services.agent_command()
        assert command[-3:] == ["agent", "--config", str(tmp_path / "agent.local.json")]


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
