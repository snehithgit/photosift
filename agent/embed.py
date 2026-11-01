"""One leased original to one vector, through the model gateway on this laptop."""

import base64
import copy
import io
import time
from pathlib import Path

import httpx
import PIL
from PIL import Image, ImageOps

from agent.prepare import download_verified, read_verified

MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_PIXELS = 80_000_000
BUSY_TRIES = 5
BUSY_WAIT_SECONDS = 2
TEXT_BATCH = 8
# Big originals exceed the gateway's input bound, so every photo takes this same path. Being
# lossless and well above the model's 384px, it leaves the checkpoint's own preprocessing to
# the gateway. It changes the pixels the model sees, so it is part of the index fingerprint.
AGENT_INPUT = {
    "contract": "photosift-embed-input-v1",
    "orientation": "exif",
    "mode": "RGB",
    "maxEdge": 1536,
    "resample": "lanczos",
    "format": "png",
    "pillowVersion": PIL.__version__,
}

# Settings > Categories and prompts shows these; a test keeps the two the same. A new wording
# needs a new version: a saved pack is frozen, and reports and scores point at its version.
PROMPT_VERSION = "photosift-prompts-v1"
PROMPTS = {
    "screenshots": [
        "a screenshot of a phone user interface",
        "a screenshot of a chat conversation",
        "a screen capture of a mobile app",
    ],
    "memes": [
        "a meme with overlaid text",
        "a good morning greeting card",
        "a forwarded image with a caption",
    ],
    "documents": [
        "a scanned document",
        "a close-up photo of a paper receipt",
        "an ID card",
        "handwritten notes",
    ],
    "events": ["people at a family gathering", "a celebration with friends"],
    "places": ["a landscape photograph", "a travel photo of a place"],
    "people": ["a portrait of a person", "a family photo"],
    "badshots": ["an accidental photo of the floor", "a very blurry photo"],
    "other": ["a photo of an everyday object", "a photo of food on a table"],
}


class Unavailable(Exception):
    """The laptop can't do this right now. The photo is fine: hand the job back and wait."""


class GatewayError(Unavailable):
    """The gateway is up but this request failed; repeated failures may be the photo's."""


def index_config(gateway_config):
    config = copy.deepcopy(gateway_config)
    config["preprocessing"]["agentInput"] = dict(AGENT_INPUT)
    return config


def model_input(root, claim):
    _, data = read_verified(root, claim, MAX_SOURCE_BYTES)
    return input_image(data)


def claimed_image(client, claim, root_mappings):
    """The model's image for a job: from a mapped folder, otherwise from the server."""
    if claim["rootId"] not in root_mappings:
        return input_image(download_verified(client, claim, MAX_SOURCE_BYTES))
    root = Path(root_mappings[claim["rootId"]])
    if not root.is_absolute():
        raise ValueError("Root mapping must be absolute")
    if not root.is_dir():
        raise Unavailable("Library folder is not reachable")
    return model_input(root, claim)


def input_image(data):
    with Image.open(io.BytesIO(data)) as original:
        if original.width * original.height > MAX_PIXELS:
            raise ValueError("Image exceeds configured decode pixel bound")
        image = ImageOps.exif_transpose(original).convert("RGB")
    del data
    edge = AGENT_INPUT["maxEdge"]
    image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", compress_level=1)
    return buffer.getvalue()


class Gateway:
    def __init__(self, url, token, transport=None):
        self.client = httpx.Client(
            base_url=url,
            headers={"Authorization": "Bearer " + token},
            timeout=120,
            trust_env=False,
            transport=transport,
        )

    def health(self):
        try:
            response = self.client.get("/health", timeout=5)
            response.raise_for_status()
            value = response.json()["embedding"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise Unavailable("Model service is not reachable") from exc
        if value.get("state") != "ready" or not value.get("indexConfig"):
            raise Unavailable("Model service is " + str(value.get("state")))
        return value

    def d3(self):
        try:
            response = self.client.get("/health", timeout=10)
            response.raise_for_status()
            value = response.json()["d3"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise Unavailable("Model service is not reachable") from exc
        if value.get("state") != "ready":
            raise Unavailable("D3 Nano is " + str(value.get("state")))
        return value

    def send(self, path, body):
        for _ in range(BUSY_TRIES):
            try:
                response = self.client.post(path, json=body)
            except httpx.HTTPError as exc:
                raise Unavailable("Model service is not reachable") from exc
            if response.status_code != 429:
                return response
            time.sleep(BUSY_WAIT_SECONDS)
        raise Unavailable("GPU stayed busy")

    def assess(self, image, state, questions):
        response = self.send(
            "/v1/systemone", {"state": state, "images": [image], "questions": questions}
        )
        if response.status_code in (400, 413, 422):
            raise ValueError("D3 refused this image")
        if response.status_code == 404:
            raise Unavailable("D3 Nano is not installed")
        if response.status_code != 200:
            raise GatewayError(f"D3 returned {response.status_code}")
        return response.json()

    def post(self, body, expected_fingerprint):
        response = self.send("/v1/embeddings", body)
        if response.status_code == 422:
            kind = "image" if "images" in body else "prompt"
            raise ValueError("Model service refused this " + kind)
        if response.status_code != 200:
            raise GatewayError(f"Model service returned {response.status_code}")
        value = response.json()
        if value.get("fingerprint") != expected_fingerprint:
            raise Unavailable("Model service contract changed")
        return value.get("data", [])

    def encode(self, image, expected_fingerprint):
        body = {"images": [base64.b64encode(image).decode("ascii")]}
        data = self.post(body, expected_fingerprint)
        if len(data) != 1:
            raise GatewayError("Model service returned the wrong number of vectors")
        return data[0]["embedding"]

    def encode_texts(self, texts, expected_fingerprint):
        vectors = []
        for start in range(0, len(texts), TEXT_BATCH):
            batch = texts[start : start + TEXT_BATCH]
            data = self.post({"input": batch}, expected_fingerprint)
            if len(data) != len(batch):
                raise GatewayError("Model service returned the wrong number of vectors")
            vectors.extend(item["embedding"] for item in data)
        return vectors


def embed_payload(client, claim, root_mappings, gateway, target):
    if claim["fingerprint"] != target["fingerprint"]:
        raise ValueError("Claim fingerprint does not match this executor")
    image = claimed_image(client, claim, root_mappings)
    vector = gateway.encode(image, target["gatewayFingerprint"])
    return {"indexId": target["indexId"], "vector": vector}


def prompt_pack(gateway, target):
    """The starting prompts, encoded by the same text tower as the index's image vectors."""
    rows = [(category, text) for category, texts in PROMPTS.items() for text in texts]
    vectors = gateway.encode_texts(
        [text for _, text in rows], target["gatewayFingerprint"]
    )
    return {
        "version": PROMPT_VERSION,
        "indexFingerprint": target["fingerprint"],
        "prompts": [
            {"category": category, "text": text, "vector": vector}
            for (category, text), vector in zip(rows, vectors, strict=True)
        ],
    }
