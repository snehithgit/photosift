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


def test_live_api_and_worker_cli(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "photo.jpg").write_bytes(b"live CLI source")
    environment = {
        **os.environ,
        "PHOTOSIFT_DB": str(tmp_path / "app" / "state.sqlite3"),
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
        assert (source / "photo.jpg").read_bytes() == b"live CLI source"
    finally:
        process.terminate()
        process.communicate(timeout=10)
