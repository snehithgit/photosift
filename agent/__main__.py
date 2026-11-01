"""Explicitly configured laptop pull loop; all originals are read-only."""

import argparse
import json
import os
import threading
import time
from pathlib import Path

import httpx

from agent.prepare import configuration, prepare
from server.db import fingerprint
from server.preparation_contract import PreparationConfig


def execute(client, claim, root_mappings, config, model_path=None):
    lease = {key: claim[key] for key in ("owner", "token", "attempt")}
    stop, errors = threading.Event(), []

    def heartbeat():
        while not stop.wait(20):
            try:
                response = client.post(
                    "/api/agent/jobs/" + claim["jobId"] + "/renew", json=lease
                )
                response.raise_for_status()
            except Exception as exc:
                errors.append(exc)
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        if claim["rootId"] not in root_mappings:
            raise ValueError("No local read-only mapping for the claimed root ID")
        if claim["fingerprint"] != fingerprint(config.model_dump()):
            raise ValueError("Claim fingerprint does not match this executor")
        root = Path(root_mappings[claim["rootId"]])
        if not root.is_absolute():
            raise ValueError("Root mapping must be absolute")
        result = prepare(root, claim, config, model_path)
        if errors:
            raise RuntimeError("Lease renewal failed") from errors[0]
        response = client.post(
            "/api/agent/jobs/" + claim["jobId"] + "/result",
            json={
                **lease,
                "sha256": claim["sha256"],
                "fingerprint": claim["fingerprint"],
                "payload": result.model_dump(),
            },
        )
        response.raise_for_status()
        return response.json()
    finally:
        stop.set()
        thread.join(timeout=35)


def main():
    parser = argparse.ArgumentParser(
        description="PhotoSift laptop preparation pull agent"
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--print-preparation-config", action="store_true")
    parser.add_argument(
        "--yunet",
        type=Path,
        help="Explicit YuNet ONNX path; its checksum enters the fingerprint",
    )
    parser.add_argument(
        "--configure-preparation",
        action="store_true",
        help="Explicitly activate this exact preparation contract on Zima",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.print_preparation_config:
        print(json.dumps(configuration(args.yunet).model_dump(), indent=2))
        return
    if not args.config:
        parser.error("--config is required")
    settings = json.loads(args.config.read_text(encoding="utf-8"))
    config = PreparationConfig.model_validate(settings["preparation"])
    actual = configuration(
        args.yunet,
        **{
            key: value
            for key, value in config.model_dump().items()
            if key
            not in (
                "pillowVersion",
                "opencvVersion",
                "numpyVersion",
                "runtime",
                "faceModelSha256",
            )
        },
    )
    if actual != config:
        parser.error(
            "Installed runtime/model differs from the exact configured fingerprint"
        )
    token = os.environ.get(settings.get("tokenEnv", "PHOTOSIFT_AGENT_TOKEN"))
    if not token:
        parser.error("Configured token environment variable is unset")
    with httpx.Client(
        base_url=settings["serverUrl"],
        headers={"Authorization": "Bearer " + token},
        timeout=45,
    ) as client:
        if args.configure_preparation:
            response = client.post(
                "/api/pipeline/preparation/configure", json=config.model_dump()
            )
            response.raise_for_status()
        digest = fingerprint(config.model_dump())
        while True:
            try:
                response = client.post(
                    "/api/agent/claim",
                    json={
                        "owner": settings["owner"],
                        "stage": "prepare",
                        "fingerprint": digest,
                    },
                )
                response.raise_for_status()
                claim = response.json()
                if claim:
                    try:
                        execute(
                            client, claim, settings["rootMappings"], config, args.yunet
                        )
                    except Exception as exc:
                        report = {
                            key: claim[key] for key in ("owner", "token", "attempt")
                        }
                        report.update({"reason": str(exc)[:1000], "state": "failed"})
                        client.post(
                            "/api/agent/jobs/" + claim["jobId"] + "/fail", json=report
                        )
                        print("Preparation failed:", str(exc))
                elif args.once:
                    break
                else:
                    time.sleep(2)
            except httpx.HTTPError as exc:
                if args.once:
                    raise
                print(
                    "Server unavailable; accepted results remain on Zima:",
                    type(exc).__name__,
                )
                time.sleep(5)


if __name__ == "__main__":
    main()
