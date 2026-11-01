import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

PROJECT = Path(__file__).resolve().parents[1]
SERVICE_DIRECTORY = PROJECT / ".data" / "services"
DEFAULT_CONFIG = SERVICE_DIRECTORY / "laptop.json"
MODEL_ID = "google/siglip2-so400m-patch16-384"
MODEL_REVISION = "dd658faac399427308559e2c3ac1e99cbe43845d"
D3_MODEL = "hf.co/ggml-org/vllm-d3-nano-GGUF:Q8_0"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bindHost: str = "0.0.0.0"
    port: int = Field(default=8787, ge=1024, le=65535)
    advertiseHost: str | None = None
    modelId: str = MODEL_ID
    modelRevision: str = Field(default=MODEL_REVISION, pattern=r"^[a-f0-9]{40}$")
    modelDirectory: str = str(
        PROJECT / ".data" / "models" / "siglip2-so400m-patch16-384"
    )
    fileHashes: dict[str, str] = Field(default_factory=dict)
    tokenFile: str = str(SERVICE_DIRECTORY / "model-api.token")
    ollamaPort: int = Field(default=11435, ge=1024, le=65535)
    ollamaExecutable: str | None = None
    d3Model: str = D3_MODEL
    loadOnStart: bool = True
    maxImagePixels: int = Field(default=80_000_000, ge=1, le=120_000_000)
    maxBatch: int = Field(default=8, ge=1, le=8)


def load_settings(path=None):
    return Settings.model_validate_json(
        Path(
            path or os.environ.get("PHOTOSIFT_LAPTOP_CONFIG") or DEFAULT_CONFIG
        ).read_text(encoding="utf-8")
    )


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)
