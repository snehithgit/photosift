"""Live GPU/HTTP smoke using generated pixels, without reading any originals."""

import base64
import io
import json
import math
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

from PIL import Image

from laptop.config import load_settings
from server.db import fingerprint
from server.embeddings import IndexConfig


def main():
    settings = load_settings()
    token = Path(settings.tokenFile).read_text().strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{settings.port}"
    with opener.open(base + "/health", timeout=10) as response:
        health = json.load(response)
    if health["embedding"]["state"] != "ready":
        raise RuntimeError(
            "Embedding service is not ready; wait for Start-PhotoSift.ps1 to finish"
        )
    for path, headers, expected in [
        ("/v1/models", {}, 401),
        ("/v1/systemone", {"Authorization": "Bearer " + token}, 501),
    ]:
        req = urllib.request.Request(
            base + path,
            headers=headers,
            method="POST" if "systemone" in path else "GET",
        )
        try:
            opener.open(req, timeout=10)
            raise AssertionError("Unexpected success: " + path)
        except HTTPError as exc:
            assert exc.code == expected
    contract = IndexConfig.model_validate(health["embedding"]["indexConfig"])

    def request(payload):
        req = urllib.request.Request(
            base + "/v1/embeddings",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token,
            },
        )
        with opener.open(req, timeout=120) as response:
            return json.load(response)

    text = request({"input": ["a red square", "a family photo", "a receipt"]})
    buffer = io.BytesIO()
    Image.new("RGB", (384, 384), "red").save(buffer, format="PNG")
    image = request({"images": [base64.b64encode(buffer.getvalue()).decode()]})
    assert text["fingerprint"] == image["fingerprint"]
    assert text["fingerprint"] == fingerprint(contract.model_dump())
    assert len(text["data"]) == 3 and len(image["data"]) == 1
    for result in [text, image]:
        for row in result["data"]:
            vector = row["embedding"]
            assert len(vector) == result["dimension"] == 1152
            assert all(math.isfinite(value) for value in vector)
            assert abs(math.sqrt(sum(value * value for value in vector)) - 1) < 1e-5
    print(
        json.dumps(
            {
                "passed": True,
                "model": text["model"],
                "dimension": text["dimension"],
                "fingerprint": text["fingerprint"],
                "textVectors": 3,
                "imageVectors": 1,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
