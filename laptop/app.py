import os
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from laptop.config import load_settings
from laptop.engine import Encoder
from server.http_limits import BoundedBody


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None
    input: str | list[str] | None = None
    images: list[str] | None = Field(default=None, min_length=1, max_length=8)

    @model_validator(mode="after")
    def valid_input(self):
        if (self.input is None) == (self.images is None):
            raise ValueError("Specify text input OR base64 images")
        if self.input is not None:
            texts = [self.input] if isinstance(self.input, str) else self.input
            if (
                not texts
                or len(texts) > 8
                or any(not value.strip() or len(value) > 4096 for value in texts)
            ):
                raise ValueError(
                    "Use 1–8 nonempty text strings, at most 4096 characters each"
                )
        return self


def create_app(settings=None, encoder=None):
    settings = settings or load_settings(os.environ.get("PHOTOSIFT_LAPTOP_CONFIG"))
    token = Path(settings.tokenFile).read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise RuntimeError("A strong local model-service token is required")
    engine = encoder or Encoder(settings)
    slot = threading.BoundedSemaphore(1)

    def background_load():
        try:
            engine.load()
        except Exception:
            pass  # Detailed failure is exposed through health; no fallback model starts.

    @asynccontextmanager
    async def lifespan(app):
        if settings.loadOnStart:
            threading.Thread(target=background_load, daemon=True).start()
        yield
        engine.unload()

    app = FastAPI(title="PhotoSift laptop models", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BoundedBody, limit=16 * 1024 * 1024)
    app.state.engine = engine

    def authorize(authorization: str | None = Header(default=None)):
        if not authorization or not secrets.compare_digest(
            authorization, "Bearer " + token
        ):
            raise HTTPException(401, "Model service bearer token required")

    @app.get("/health")
    def health():
        return {
            "service": "photosift-laptop",
            "embedding": engine.status(),
            "d3": {
                "model": settings.d3Model,
                "state": "protocol_unconfigured",
                "endpoint": "/v1/systemone",
            },
            "maxConcurrentGpuRequests": 1,
        }

    @app.get("/v1/models", dependencies=[Depends(authorize)])
    def models():
        return {
            "object": "list",
            "data": [{"id": settings.modelId, "object": "model", **engine.status()}],
        }

    @app.post("/v1/embeddings", dependencies=[Depends(authorize)])
    def embeddings(request: EmbeddingRequest):
        if request.model and request.model != settings.modelId:
            raise HTTPException(
                409,
                "Only the explicitly configured encoder is served; no model switching",
            )
        if not slot.acquire(blocking=False):
            raise HTTPException(429, "GPU busy; retry this request later")
        try:
            texts = [request.input] if isinstance(request.input, str) else request.input
            if len(texts or request.images or []) > settings.maxBatch:
                raise HTTPException(422, "Batch exceeds the configured bound")
            values = engine.encode(texts=texts, images=request.images)
            status = engine.status()
            return {
                "object": "list",
                "model": settings.modelId,
                "fingerprint": status["fingerprint"],
                "dimension": len(values[0]),
                "data": [
                    {"object": "embedding", "index": index, "embedding": value}
                    for index, value in enumerate(values)
                ],
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        finally:
            slot.release()

    @app.post("/v1/model/load", dependencies=[Depends(authorize)])
    def load_model():
        if not slot.acquire(blocking=False):
            raise HTTPException(429, "GPU busy")
        try:
            return engine.load()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        finally:
            slot.release()

    @app.post("/v1/model/unload", dependencies=[Depends(authorize)])
    def unload_model():
        if not slot.acquire(blocking=False):
            raise HTTPException(429, "GPU busy")
        try:
            return engine.unload()
        finally:
            slot.release()

    @app.post("/v1/systemone", dependencies=[Depends(authorize)])
    def d3_assessment():
        raise HTTPException(
            501,
            "D3 weights/runtime are prepared, but the SystemOne payload schema is not provided. No substitute decision protocol is used.",
        )

    return app
