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
from filelock import FileLock, Timeout

from laptop.config import PROJECT, SERVICE_DIRECTORY, load_settings, write_json

STATE = SERVICE_DIRECTORY / "processes.json"
AGENT_CONFIG = PROJECT / "agent.local.json"
NOT_RUNNING = 3


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
    # A test bind on one address misses a listener on 0.0.0.0 or :: on Windows, so ask the OS.
    for connection in psutil.net_connections(kind="tcp"):
        if connection.status == psutil.CONN_LISTEN and connection.laddr.port == port:
            raise RuntimeError(
                f"Port {port} is already in use by process {connection.pid}; no external process will be stopped"
            )


def stop_process(record):
    process = owned(record)
    if process is None:
        return
    try:
        children = process.children(recursive=True)
    except psutil.NoSuchProcess:
        return
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
            raise RuntimeError(
                "Service exited or Stop was run; inspect .data/services/*.log"
            )
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


def agent_command():
    folder = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    python = PROJECT / ".venv" / folder
    if not AGENT_CONFIG.is_file() or not python.is_file():
        return None
    return [str(python), "-u", "-m", "agent", "--config", str(AGENT_CONFIG)]


def read_records():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def locked():
    # Held only while reading or changing records, never during a model load, so Status and
    # Stop keep working while Start waits.
    return FileLock(str(SERVICE_DIRECTORY / "manager.lock"), timeout=10)


def report(settings, records):
    running = True
    for name in ("ollama", "models", "agent"):
        if name not in records:
            if name == "agent" and agent_command() is None:
                print("agent: not set up; see agent/README.md")
                continue
            print(name + ": stopped")
            running = False
        elif owned(records[name]):
            print(name + ": running (owned)")
        else:
            print(name + ": stopped / identity changed")
            running = False
    try:
        health = fetch(f"http://127.0.0.1:{settings.port}/health")
        state = health["embedding"]["state"]
        d3_state = health["d3"]["state"]
    except (OSError, ValueError, KeyError):
        print("Embedding endpoint unavailable")
        return NOT_RUNNING
    print("Embedding:", state)
    print("D3 Nano:", d3_state)
    base = f"http://{advertised(settings)}:{settings.port}"
    endpoints = {
        "baseUrl": base,
        "embeddings": base + "/v1/embeddings",
        "models": base + "/v1/models",
        "health": base + "/health",
        "d3": {"url": base + "/v1/systemone", "ready": d3_state == "ready"},
        "tokenFile": settings.tokenFile,
    }
    write_json(SERVICE_DIRECTORY / "endpoints.json", endpoints)
    print(json.dumps(endpoints, indent=2))
    return 0 if running and state == "ready" else NOT_RUNNING


def start(settings):
    env = dict(os.environ)
    env["PHOTOSIFT_LAPTOP_CONFIG"] = str(SERVICE_DIRECTORY / "laptop.json")
    started = []
    try:
        with locked():
            records = {n: r for n, r in read_records().items() if owned(r)}
            write_json(STATE, records)
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
                started.append("ollama")
            ollama = records["ollama"]
        wait_ready(
            f"http://127.0.0.1:{settings.ollamaPort}/api/version",
            lambda v: "version" in v,
            ollama,
            60,
        )
        with locked():
            records = read_records()
            if "models" not in records:
                port_free(settings.port)
                launch("models", [sys.executable, "-m", "laptop"], env, records)
                started.append("models")
            models = records["models"]
        wait_ready(
            f"http://127.0.0.1:{settings.port}/health",
            lambda v: (
                v.get("service") == "photosift-laptop"
                and v["embedding"]["state"] == "ready"
            ),
            models,
        )
        with locked():
            records = read_records()
            command = agent_command()
            if command and "agent" not in records:
                launch("agent", command, env, records)
                started.append("agent")
                # A bad config exits at once; show that now, not at the next Status.
                time.sleep(3)
            return report(settings, records)
    except Exception:
        with locked():
            records = read_records()
            for name in started:
                # Only what this run launched is undone. A failed agent keeps the loaded model.
                if name in records:
                    stop_process(records.pop(name))
            write_json(STATE, records)
        raise


def stop():
    with locked():
        records = read_records()
        for record in reversed(list(records.values())):
            stop_process(record)
        write_json(STATE, {})
    print("PhotoSift-owned services stopped. Shared Ollama instances were left alone.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status"])
    args = parser.parse_args(argv)
    SERVICE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    try:
        if args.action == "stop":
            return stop()
        settings = load_settings()
        if args.action == "status":
            with locked():
                return report(settings, read_records())
        return start(settings)
    except Timeout:
        print("Another Start or Stop is changing services; try again.", file=sys.stderr)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
