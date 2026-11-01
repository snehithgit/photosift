"""D3 Nano through Ollama's documented decision API on the PhotoSift-owned local daemon."""

import json
import time
import urllib.error
import urllib.request

# SigLIP2 and D3 measured 5.3 GB together on the 8 GB RTX 5060, so D3 can stay loaded between
# photos. The daemon's default of unloading after each request stays for anything else.
KEEP_ALIVE = "5m"


class D3Error(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class D3Runtime:
    def __init__(self, settings):
        self.base = f"http://127.0.0.1:{settings.ollamaPort}"
        self.model = settings.d3Model
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, body=None, timeout=3):
        request = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=timeout) as response:
            return json.load(response)

    def status(self):
        value = {
            "model": self.model,
            "protocol": "ollama-systemone",
            "endpoint": "/v1/systemone",
        }
        try:
            tags = self.call("/api/tags")["models"]
            digest = next(m["digest"] for m in tags if m["name"] == self.model)
            capabilities = self.call("/api/show", {"model": self.model})["capabilities"]
            version = self.call("/api/version")["version"]
        except StopIteration:
            return {**value, "state": "model_missing"}
        except (OSError, ValueError, KeyError):
            return {**value, "state": "unavailable"}
        if not {"decision", "vision"} <= set(capabilities):
            return {**value, "state": "unsupported"}
        return {
            **value,
            "state": "ready",
            "digest": digest,
            "runtime": "ollama=" + version,
        }

    def assess(self, state, images, questions):
        body = {
            "model": self.model,
            "state": state,
            "images": images,
            "questions": questions,
            "keep_alive": KEEP_ALIVE,
        }
        started = time.monotonic()
        try:
            value = self.call("/v1/systemone", body, timeout=120)
        except urllib.error.HTTPError as exc:
            raise D3Error(exc.code, "D3 refused this request") from exc
        except (OSError, ValueError) as exc:
            raise D3Error(503, "D3 runtime is not reachable") from exc
        return {
            "model": value.get("model"),
            "answers": value.get("answers"),
            "usage": value.get("usage"),
            "durationMs": (time.monotonic() - started) * 1000,
        }
