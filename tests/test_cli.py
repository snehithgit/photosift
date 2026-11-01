"""Run the installed API and worker as real processes against temporary data."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from PIL import Image

from agent.prepare import configuration


def test_live_api_and_worker_cli(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (80, 40), "blue").save(source / "photo.jpg")
    original = (source / "photo.jpg").read_bytes()
    environment = {
        **os.environ,
        "PHOTOSIFT_DB": str(tmp_path / "app" / "state.sqlite3"),
        "PHOTOSIFT_AGENT_TOKEN": "live-test-token",
    }
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    repository = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=repository,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=flags,
    )
    base = f"http://127.0.0.1:{port}"

    def request(path, value=None):
        req = Request(
            base + path,
            data=json.dumps(value).encode() if value is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    try:
        for _ in range(100):
            try:
                status = request("/api/status")
                break
            except (URLError, ConnectionError):
                if process.poll() is not None:
                    raise AssertionError(process.stdout.read())
                time.sleep(0.1)
        else:
            raise AssertionError("API startup timeout")
        assert status["phase"] == 1
        assert request("/openapi.json")["info"]["title"] == "PhotoSift foundation"
        root = request("/api/roots", {"name": "CLI test", "path": str(source)})
        scan = request("/api/roots/" + root["id"] + "/scans", {})
        result = subprocess.run(
            [sys.executable, "-m", "worker", "--once"],
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=flags,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert request("/api/scans/" + scan["id"])["state"] == "done"
        assert request("/api/photos")["total"] == 1
        settings = tmp_path / "agent.json"
        settings.write_text(
            json.dumps(
                {
                    "serverUrl": base,
                    "owner": "cli-laptop",
                    "rootMappings": {root["id"]: str(source)},
                    "preparation": configuration().model_dump(),
                }
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent",
                "--config",
                str(settings),
                "--configure-preparation",
                "--once",
            ],
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=flags,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        photos = request("/api/photos")
        assert photos["items"][0]["width"] == 80 and photos["items"][0]["height"] == 40
        assert photos["items"][0]["thumbUrl"]
        assert request("/api/status")["laptop"]["state"] == "online"
        assert (source / "photo.jpg").read_bytes() == original
    finally:
        process.terminate()
        process.communicate(timeout=10)
