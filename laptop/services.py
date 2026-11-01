"""Manage only the processes launched by PhotoSift, with PID-reuse protection."""

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil
from filelock import FileLock

from laptop.config import PROJECT, SERVICE_DIRECTORY, load_settings, write_json

STATE = SERVICE_DIRECTORY / "processes.json"


def identity(process):
    return {
        "pid": process.pid,
        "created": process.create_time(),
        "command": hashlib.sha256(json.dumps(process.cmdline()).encode()).hexdigest(),
    }


def owned(record):
    try:
        process = psutil.Process(record["pid"])
        return process if identity(process) == record else None
    except (psutil.Error, KeyError):
        return None


def fetch(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=3) as response:
        return json.load(response)


def port_free(port):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(
                f"Port {port} is already in use; no external process will be stopped"
            ) from exc


def stop_process(record):
    process = owned(record)
    if process is None:
        return
    children = process.children(recursive=True)
    # Recheck the owner before terminating the captured tree.
    if owned(record) is None:
        return
    for item in [*reversed(children), process]:
        try:
            item.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs([*children, process], timeout=8)
    for item in alive:
        try:
            item.kill()
        except psutil.NoSuchProcess:
            pass


def launch(name, command, env, records):
    log = SERVICE_DIRECTORY / (name + ".log")
    with log.open("ab") as output:
        process = subprocess.Popen(
            command,
            cwd=PROJECT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    try:
        records[name] = identity(psutil.Process(process.pid))
    except psutil.Error as exc:
        raise RuntimeError(f"{name} exited; see {log}") from exc
    write_json(STATE, records)


def wait_ready(url, predicate, record, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if owned(record) is None:
            raise RuntimeError("Service exited; inspect .data/services/*.log")
        try:
            value = fetch(url)
            if predicate(value):
                return
            if value.get("embedding", {}).get("state") == "failed":
                raise RuntimeError(value["embedding"]["error"])
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("Service readiness timed out; inspect .data/services/*.log")


def advertised(settings):
    if settings.advertiseHost:
        return settings.advertiseHost
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 80))
            return probe.getsockname()[0]
        except OSError:
            return socket.gethostbyname(socket.gethostname())


def report(settings, records):
    for name, record in records.items():
        print(
            name
            + ": "
            + ("running (owned)" if owned(record) else "stopped / identity changed")
        )
    base = f"http://{advertised(settings)}:{settings.port}"
    endpoints = {
        "baseUrl": base,
        "embeddings": base + "/v1/embeddings",
        "models": base + "/v1/models",
        "health": base + "/health",
        "d3": {
            "url": base + "/v1/systemone",
            "ready": False,
            "reason": "SystemOne request/response schema is unknown",
        },
        "tokenFile": settings.tokenFile,
    }
    write_json(SERVICE_DIRECTORY / "endpoints.json", endpoints)
    print(json.dumps(endpoints, indent=2))
    try:
        health = fetch(f"http://127.0.0.1:{settings.port}/health")
        print("Embedding:", health["embedding"]["state"])
    except (OSError, ValueError, KeyError):
        print("Embedding endpoint unavailable")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status"])
    args = parser.parse_args()
    SERVICE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with FileLock(str(SERVICE_DIRECTORY / "manager.lock"), timeout=10):
        records = json.loads(STATE.read_text()) if STATE.exists() else {}
        if args.action == "stop":
            for record in reversed(list(records.values())):
                stop_process(record)
            write_json(STATE, {})
            print(
                "PhotoSift-owned services stopped. Shared Ollama instances were left alone."
            )
            return
        settings = load_settings()
        if args.action == "status":
            report(settings, records)
            return
        records = {name: record for name, record in records.items() if owned(record)}
        old = set(records)
        env = dict(os.environ)
        env["PHOTOSIFT_LAPTOP_CONFIG"] = str(SERVICE_DIRECTORY / "laptop.json")
        try:
            if "ollama" not in records:
                executable = settings.ollamaExecutable or shutil.which("ollama")
                fallback = (
                    Path(os.environ.get("LOCALAPPDATA", ""))
                    / "Programs/Ollama/ollama.exe"
                )
                if not executable and fallback.is_file():
                    executable = str(fallback)
                if not executable:
                    raise RuntimeError(
                        "Install Ollama first or set ollamaExecutable in laptop.json"
                    )
                port_free(settings.ollamaPort)
                ollama_env = {
                    **env,
                    "OLLAMA_HOST": f"127.0.0.1:{settings.ollamaPort}",
                    "OLLAMA_MAX_LOADED_MODELS": "1",
                    "OLLAMA_NUM_PARALLEL": "1",
                    "OLLAMA_KEEP_ALIVE": "0",
                    "OLLAMA_NO_CLOUD": "1",
                }
                launch("ollama", [executable, "serve"], ollama_env, records)
            wait_ready(
                f"http://127.0.0.1:{settings.ollamaPort}/api/version",
                lambda v: "version" in v,
                records["ollama"],
                60,
            )
            if "models" not in records:
                port_free(settings.port)
                launch("models", [sys.executable, "-m", "laptop"], env, records)
            wait_ready(
                f"http://127.0.0.1:{settings.port}/health",
                lambda v: (
                    v.get("service") == "photosift-laptop"
                    and v["embedding"]["state"] == "ready"
                ),
                records["models"],
            )
            report(settings, records)
        except Exception:
            for name in list(records):
                if name not in old:
                    stop_process(records.pop(name))
            write_json(STATE, records)
            raise


if __name__ == "__main__":
    main()
