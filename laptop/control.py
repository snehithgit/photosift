"""A small window to start and stop PhotoSift on this laptop. It starts nothing by itself, and
closing it leaves the services as they are."""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from laptop import services

REFRESH_SECONDS = 3


def last_line(path):
    try:
        with Path(path).open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 4096))
            lines = log.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    lines = [line.strip() for line in lines if line.strip()]
    return lines[-1][:160] if lines else ""


def server_url():
    try:
        settings = json.loads(services.AGENT_CONFIG.read_text(encoding="utf-8"))
        return settings["serverUrl"].rstrip("/")
    except (OSError, ValueError, KeyError):
        return None


def model_states():
    try:
        settings = services.load_settings()
    except (OSError, ValueError):
        return "Not set up; run Setup-Laptop.ps1", "Not set up"
    try:
        health = services.fetch(f"http://127.0.0.1:{settings.port}/health")
        return health["embedding"]["state"].capitalize(), health["d3"][
            "state"
        ].capitalize()
    except (OSError, ValueError, KeyError):
        return "Stopped", "Stopped"


def snapshot():
    """What the window shows. Reads only: it changes no service."""
    try:
        records = services.read_records()
    except (OSError, ValueError):
        records = {}  # Being rewritten; the next refresh reads it.
    agent = "Stopped"
    if services.agent_command() is None:
        agent = "Not set up; see agent/README.md"
    elif "agent" in records and services.owned(records["agent"]):
        agent = "Running"
    models, d3 = model_states()
    url = server_url()
    zima = "Not set up; see agent/README.md"
    if url:
        try:
            services.fetch(url + "/api/status")
            zima = "Connected to " + url
        except (OSError, ValueError):
            zima = "Can't reach " + url
    return {
        "Agent": agent,
        "SigLIP 2": models,
        "D3 Nano": d3,
        "Zima": zima,
        "Last message": last_line(services.SERVICE_DIRECTORY / "agent.log"),
    }


def run_services(action):
    """Run Start or Stop as its own process, with its output in a log rather than a pipe, so a
    service it leaves running can never hold this window up."""
    log = services.SERVICE_DIRECTORY / "control.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    # The window runs without a console; the services are started as they are from a script.
    python = Path(sys.executable).with_name(
        "python.exe" if os.name == "nt" else Path(sys.executable).name
    )
    with log.open("ab") as output:
        code = subprocess.run(
            [str(python), "-m", "laptop.services", action],
            cwd=services.PROJECT,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ).returncode
    if action == "stop":
        return "Stopped. The GPU is free." if code == 0 else last_line(log)
    if code == 0:
        return "Started."
    if code == services.NOT_RUNNING:
        return "Started, but something isn't ready yet; see above."
    return last_line(log) or "Start failed; see .data/services logs."


def main():
    # Only the window needs Tk, so importing this module works without it.
    import tkinter
    from tkinter import ttk

    window = tkinter.Tk()
    window.title("PhotoSift Agent")
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=16)
    frame.grid()
    updates = queue.Queue()
    values = {}
    for row, name in enumerate(
        ("Agent", "SigLIP 2", "D3 Nano", "Zima", "Last message")
    ):
        ttk.Label(frame, text=name + ":").grid(
            row=row, column=0, sticky="nw", padx=(0, 12), pady=2
        )
        values[name] = tkinter.StringVar(value="…")
        ttk.Label(frame, textvariable=values[name], width=56, wraplength=330).grid(
            row=row, column=1, sticky="w", pady=2
        )
    buttons = ttk.Frame(frame)
    buttons.grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 6))
    result = tkinter.StringVar(
        value="Start runs the models and the agent. Stop frees the GPU."
    )
    ttk.Label(frame, textvariable=result, wraplength=480).grid(
        row=6, column=0, columnspan=2, sticky="w"
    )

    def act(action, doing):
        start_button["state"] = "disabled"
        if action == "stop":
            stop_button["state"] = "disabled"
        result.set(doing)

        def work():
            updates.put(("done", run_services(action)))

        threading.Thread(target=work, daemon=True).start()

    start_button = ttk.Button(
        buttons,
        text="Start",
        command=lambda: act("start", "Starting; the models take a minute to load…"),
    )
    stop_button = ttk.Button(
        buttons, text="Stop", command=lambda: act("stop", "Stopping…")
    )
    open_button = ttk.Button(
        buttons,
        text="Open PhotoSift",
        command=lambda: webbrowser.open(server_url() or ""),
    )
    open_button["state"] = "normal" if server_url() else "disabled"
    start_button.grid(row=0, column=0, padx=(0, 6))
    stop_button.grid(row=0, column=1, padx=(0, 6))
    open_button.grid(row=0, column=2)

    def watch():
        while True:
            updates.put(("state", snapshot()))
            time.sleep(REFRESH_SECONDS)

    def drain():
        while not updates.empty():
            kind, value = updates.get()
            if kind == "state":
                for name, text in value.items():
                    values[name].set(text)
            else:
                result.set(value)
                start_button["state"] = stop_button["state"] = "normal"
        window.after(200, drain)

    threading.Thread(target=watch, daemon=True).start()
    drain()
    window.mainloop()


if __name__ == "__main__":
    main()
