"""Download an explicitly pinned encoder and record its local checksums."""

import secrets
from pathlib import Path

from huggingface_hub import get_hf_file_metadata, hf_hub_url, snapshot_download

from laptop.config import DEFAULT_CONFIG, Settings, load_settings, write_json
from laptop.engine import sha256_file


def main():
    settings = load_settings() if DEFAULT_CONFIG.exists() else Settings()
    snapshot_download(
        settings.modelId,
        revision=settings.modelRevision,
        local_dir=settings.modelDirectory,
        allow_patterns=["*.json", "*.model", "*.safetensors"],
    )
    directory = Path(settings.modelDirectory)
    settings.fileHashes = {
        p.name: sha256_file(p)
        for p in sorted(directory.iterdir())
        if p.is_file() and p.suffix in {".json", ".model", ".safetensors"}
    }
    metadata = get_hf_file_metadata(
        hf_hub_url(
            settings.modelId, "model.safetensors", revision=settings.modelRevision
        )
    )
    if settings.fileHashes.get("model.safetensors") != metadata.etag:
        raise RuntimeError(
            "Downloaded weights do not match the pinned upstream SHA-256"
        )
    token = Path(settings.tokenFile)
    token.parent.mkdir(parents=True, exist_ok=True)
    if not token.exists():
        token.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    write_json(DEFAULT_CONFIG, settings.model_dump())
    print("Pinned model downloaded and checksummed. Configuration:", DEFAULT_CONFIG)


if __name__ == "__main__":
    main()
