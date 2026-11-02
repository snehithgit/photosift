"""Explicitly configured laptop pull loop; all originals are read-only."""

import argparse
import json
import os
import secrets
import threading
import time
from pathlib import Path

import httpx

from agent.assess import assess_payload, contract
from agent.embed import (
    Gateway,
    GatewayError,
    Unavailable,
    embed_payload,
    index_config,
    prompt_pack,
)
from agent.prepare import configuration, download_verified, prepare
from server.db import fingerprint
from server.preparation_contract import PreparationConfig

# Beyond this, a job the gateway keeps refusing while healthy is reported instead of retried.
MAX_EMBED_ATTEMPTS = 5


def lease_of(claim):
    return {key: claim[key] for key in ("owner", "token", "attempt")}


def leased(client, claim, work):
    lease = lease_of(claim)
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
        payload = work()
        if errors:
            raise RuntimeError("Lease renewal failed") from errors[0]
        response = client.post(
            "/api/agent/jobs/" + claim["jobId"] + "/result",
            json={
                **lease,
                "sha256": claim["sha256"],
                "fingerprint": claim["fingerprint"],
                "payload": payload,
            },
        )
        response.raise_for_status()
        return response.json()
    finally:
        stop.set()
        thread.join(timeout=35)


def execute(client, claim, root_mappings, config, model_path=None):
    def work():
        if claim["fingerprint"] != fingerprint(config.model_dump()):
            raise ValueError("Claim fingerprint does not match this executor")
        if claim["rootId"] not in root_mappings:
            data = download_verified(client, claim, config.maxSourceBytes)
            return prepare(None, claim, config, model_path, data).model_dump()
        root = Path(root_mappings[claim["rootId"]])
        if not root.is_absolute():
            raise ValueError("Root mapping must be absolute")
        return prepare(root, claim, config, model_path).model_dump()

    return leased(client, claim, work)


def report_failure(client, claim, exc):
    client.post(
        "/api/agent/jobs/" + claim["jobId"] + "/fail",
        json={**lease_of(claim), "reason": str(exc)[:1000], "state": "failed"},
    )


def run_on_gpu(client, claim, label, work):
    """Run one model job. Raises Unavailable after handing the job back for a later try."""
    try:
        return leased(client, claim, work)
    except Unavailable as exc:
        if isinstance(exc, GatewayError) and claim["attempt"] >= MAX_EMBED_ATTEMPTS:
            report_failure(client, claim, exc)
            print(label, "failed:", str(exc))
            return None
        client.post(
            "/api/agent/jobs/" + claim["jobId"] + "/release",
            json={**lease_of(claim), "reason": str(exc)[:1000]},
        )
        raise
    except Exception as exc:
        report_failure(client, claim, exc)
        print(label, "failed:", str(exc))
        return None


def execute_embedding(client, claim, root_mappings, gateway, target):
    return run_on_gpu(
        client,
        claim,
        "Embedding",
        lambda: embed_payload(client, claim, root_mappings, gateway, target),
    )


def execute_assessment(client, claim, root_mappings, gateway, target):
    return run_on_gpu(
        client,
        claim,
        "Assessment",
        lambda: assess_payload(client, claim, root_mappings, gateway, target),
    )


class Notes:
    """Say a waiting reason once, not on every poll."""

    def __init__(self):
        self.last = None

    def say(self, text):
        if text != self.last:
            print(text, flush=True)
        self.last = text


def embedding_target(client, gateway, notes):
    """The active index when this laptop's exact model matches it, otherwise None."""
    try:
        health = gateway.health()
    except Unavailable as exc:
        notes.say("Embedding waiting: " + str(exc))
        return None
    active = client.get("/api/pipeline/embedding")
    active.raise_for_status()
    active = active.json()
    if not active:
        notes.say(
            "Embedding waiting: no index is active; click Turn on models under Settings, Laptop agent"
        )
        return None
    if active["fingerprint"] != fingerprint(index_config(health["indexConfig"])):
        notes.say(
            "Embedding waiting: this laptop's model differs from the active index. Nothing is mixed; click Turn on models to fill a new index"
        )
        return None
    notes.say("Embedding ready: index " + active["indexId"])
    return {**active, "gatewayFingerprint": health["fingerprint"]}


def assessment_target(client, gateway, notes):
    """The active assessment when this laptop's exact D3 setup matches it, otherwise None."""
    try:
        d3 = gateway.d3()
    except Unavailable as exc:
        notes.say("Assessment waiting: " + str(exc))
        return None
    active = client.get("/api/pipeline/assessment")
    active.raise_for_status()
    active = active.json()
    if not active:
        notes.say(
            "Assessment waiting: not active; click Turn on models under Settings, Laptop agent"
        )
        return None
    if active["config"] != contract(d3):
        notes.say(
            "Assessment waiting: this laptop's D3 setup differs from the active one; click Turn on models to assess again"
        )
        return None
    notes.say("Assessment ready")
    return active


def encode_prompts(client, gateway):
    notes = Notes()
    target = embedding_target(client, gateway, notes)
    if not target:
        raise ValueError(notes.last)
    response = client.post(
        "/api/indexes/" + target["indexId"] + "/prompts",
        json=prompt_pack(gateway, target),
    )
    response.raise_for_status()
    saved = response.json()
    outcome = "saved" if saved["created"] else "already saved; vectors kept"
    return f"Prompt pack {saved['version']}: {saved['prompts']} prompts {outcome}"


def turn_on_models(client, gateway, config):
    """Activate this laptop's exact contracts. Repeating it with the same models changes nothing."""
    # Ask the models first, so a model service that is down changes nothing on Zima.
    health = gateway.health()
    d3 = gateway.d3()

    def configure(stage, body):
        response = client.post(f"/api/pipeline/{stage}/configure", json=body)
        response.raise_for_status()
        return response.json()["queued"]

    prepare = configure("preparation", config.model_dump())
    embed = configure("embedding", index_config(health["indexConfig"]))
    assess = configure("assessment", contract(d3))
    return f"Preparation, SigLIP 2 and D3 Nano are on. Newly queued: {prepare:,} to prepare, {embed:,} to embed, {assess:,} to assess."


def run_command(client, gateway, config):
    """Do one request from the web app, if there is one. Its reason goes back to the web app."""
    response = client.post("/api/agent/commands/claim")
    if response.status_code == 404:
        return (
            False  # A server older than this agent; it can't have asked for anything.
        )
    response.raise_for_status()
    command = response.json()
    if not command:
        return False
    print("Web app asked for:", command["kind"], flush=True)
    try:
        if not gateway:
            raise ValueError("agent.local.json has no embedding section")
        if command["kind"] == "encode-prompts":
            state, message = "done", encode_prompts(client, gateway)
        elif command["kind"] == "turn-on-models":
            state, message = "done", turn_on_models(client, gateway, config)
        else:
            raise ValueError("This agent doesn't know that request; update it")
    except Exception as exc:
        state, message = "failed", str(exc) or type(exc).__name__
    print(message, flush=True)
    response = client.post(
        "/api/agent/commands/" + command["id"] + "/finish",
        json={"state": state, "message": message[:1000]},
    )
    response.raise_for_status()
    return True


def resolve(config_path, value):
    path = Path(value)
    return path if path.is_absolute() else config_path.resolve().parent / path


def init_local(path, server_url):
    """Write a config for a laptop that runs the server itself, so every path is shared."""
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    url = existing.get("serverUrl", server_url)
    with httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
        response = client.get("/api/roots")
        response.raise_for_status()
        roots = response.json()
    data = Path(os.environ.get("PHOTOSIFT_DB", ".data/photosift.sqlite3")).resolve()
    token = data.parent / "agent-api.token"
    token.parent.mkdir(parents=True, exist_ok=True)
    if not token.exists():
        with token.open("x", encoding="utf-8") as output:
            output.write(secrets.token_urlsafe(48))
    settings = {
        "serverUrl": url,
        "owner": "gpu-laptop",
        "tokenFile": str(token),
        "preparation": configuration().model_dump(),
        "embedding": {
            "gatewayUrl": "http://127.0.0.1:8787",
            "tokenFile": str(data.parent / "services" / "model-api.token"),
        },
        **existing,
    }
    settings["rootMappings"] = {
        **{root["id"]: root["path"] for root in roots},
        **existing.get("rootMappings", {}),
    }
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"Wrote {path} with {len(settings['rootMappings'])} library mapping(s).")


def main():
    parser = argparse.ArgumentParser(description="PhotoSift laptop pull agent")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--print-preparation-config", action="store_true")
    parser.add_argument(
        "--init-local",
        metavar="SERVER_URL",
        help="Create or extend --config for a server running on this same machine",
    )
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
    parser.add_argument(
        "--configure-embedding",
        action="store_true",
        help="Explicitly fill an index for this laptop's exact model; never promotes it",
    )
    parser.add_argument(
        "--configure-assessment",
        action="store_true",
        help="Explicitly activate D3 Nano assessment for this laptop's exact setup",
    )
    parser.add_argument(
        "--encode-prompts",
        action="store_true",
        help="Encode the prompt pack under the active index, save it, and exit",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.print_preparation_config:
        print(json.dumps(configuration(args.yunet).model_dump(), indent=2))
        return
    if not args.config:
        parser.error("--config is required")
    if args.init_local:
        init_local(args.config, args.init_local)
        return
    settings = json.loads(args.config.read_text(encoding="utf-8"))
    settings.setdefault("rootMappings", {})
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
    if settings.get("tokenFile"):
        token = (
            resolve(args.config, settings["tokenFile"])
            .read_text(encoding="utf-8")
            .strip()
        )
    else:
        token = os.environ.get(settings.get("tokenEnv", "PHOTOSIFT_AGENT_TOKEN"))
    if not token:
        parser.error("Configured token environment variable is unset")
    gateway = None
    if settings.get("embedding"):
        gateway = Gateway(
            settings["embedding"]["gatewayUrl"],
            resolve(args.config, settings["embedding"]["tokenFile"])
            .read_text(encoding="utf-8")
            .strip(),
        )
    elif args.configure_embedding or args.encode_prompts or args.configure_assessment:
        parser.error("Model options need an embedding section in the config")
    notes, assess_notes = Notes(), Notes()
    with httpx.Client(
        base_url=settings["serverUrl"],
        headers={"Authorization": "Bearer " + token},
        timeout=45,
        trust_env=False,
    ) as client:
        if args.configure_preparation:
            response = client.post(
                "/api/pipeline/preparation/configure", json=config.model_dump()
            )
            response.raise_for_status()
        if args.configure_embedding:
            try:
                health = gateway.health()
            except Unavailable as exc:
                parser.error(f"{exc}. Run Start-PhotoSift.ps1 first")
            response = client.post(
                "/api/pipeline/embedding/configure",
                json=index_config(health["indexConfig"]),
            )
            response.raise_for_status()
        if args.configure_assessment:
            try:
                d3 = gateway.d3()
            except Unavailable as exc:
                parser.error(f"{exc}. Run Start-PhotoSift.ps1 first")
            response = client.post(
                "/api/pipeline/assessment/configure", json=contract(d3)
            )
            response.raise_for_status()
        if args.encode_prompts:
            try:
                print(encode_prompts(client, gateway))
            except (ValueError, Unavailable) as exc:
                parser.error(f"Prompts were not encoded: {exc}")
            return
        digest = fingerprint(config.model_dump())
        next_command = 0
        server_down = False

        def claim_next(stage, stage_fingerprint):
            response = client.post(
                "/api/agent/claim",
                json={
                    "owner": settings["owner"],
                    "stage": stage,
                    "fingerprint": stage_fingerprint,
                },
            )
            response.raise_for_status()
            return response.json()

        while True:
            try:
                # Checked between photos, and not on every one of them.
                if time.monotonic() >= next_command:
                    next_command = time.monotonic() + 3
                    run_command(client, gateway, config)
                claim = claim_next("prepare", digest)
                if server_down:
                    print("Server reachable again", flush=True)
                    server_down = False
                if claim:
                    try:
                        execute(
                            client, claim, settings["rootMappings"], config, args.yunet
                        )
                    except Exception as exc:
                        report_failure(client, claim, exc)
                        print("Preparation failed:", str(exc))
                    continue
                target = gateway and embedding_target(client, gateway, notes)
                claim = target and claim_next("embed", target["fingerprint"])
                if claim:
                    try:
                        execute_embedding(
                            client, claim, settings["rootMappings"], gateway, target
                        )
                    except Unavailable as exc:
                        notes.say("Embedding waiting: " + str(exc))
                        if args.once:
                            break
                        time.sleep(5)
                    continue
                target = gateway and assessment_target(client, gateway, assess_notes)
                claim = target and claim_next("assess", target["fingerprint"])
                if claim:
                    try:
                        execute_assessment(
                            client, claim, settings["rootMappings"], gateway, target
                        )
                    except Unavailable as exc:
                        assess_notes.say("Assessment waiting: " + str(exc))
                        if args.once:
                            break
                        time.sleep(5)
                    continue
                if args.once:
                    break
                time.sleep(2)
            except httpx.HTTPError as exc:
                if args.once:
                    raise
                server_down = True
                print(
                    "Server unavailable; accepted results remain on Zima:",
                    str(exc)[:200] or type(exc).__name__,
                    flush=True,
                )
                time.sleep(5)


if __name__ == "__main__":
    main()
