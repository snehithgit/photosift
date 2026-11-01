import base64
import gc
import hashlib
import io
import platform
import threading
from pathlib import Path

from PIL import Image, ImageOps

from server.db import fingerprint


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class Encoder:
    def __init__(self, settings):
        self.settings = settings
        self.gpu_lock = threading.RLock()
        self.model = None
        self.processor = None
        self.index_config = None
        self.state = "unloaded"
        self.error = None

    def status(self):
        return {
            "state": self.state,
            "loaded": self.model is not None,
            "model": self.settings.modelId,
            "revision": self.settings.modelRevision,
            "error": self.error,
            "indexConfig": self.index_config,
            "fingerprint": fingerprint(self.index_config)
            if self.index_config
            else None,
        }

    def load(self):
        with self.gpu_lock:
            if self.model is not None:
                return self.status()
            self.state, self.error = "loading", None
            try:
                import torch
                import transformers
                from transformers import AutoModel, AutoProcessor

                if not torch.cuda.is_available():
                    raise RuntimeError("CUDA is unavailable; CPU fallback is disabled")
                directory = Path(self.settings.modelDirectory)
                if (
                    not self.settings.fileHashes
                    or "model.safetensors" not in self.settings.fileHashes
                ):
                    raise RuntimeError(
                        "Run Setup-Laptop.ps1 to download and verify the pinned checkpoint"
                    )
                for name, sha in self.settings.fileHashes.items():
                    path = directory / name
                    if (
                        Path(name).name != name
                        or not path.is_file()
                        or sha256_file(path) != sha
                    ):
                        raise RuntimeError(
                            "Pinned model file is missing or changed: " + name
                        )
                processor = AutoProcessor.from_pretrained(
                    directory, local_files_only=True, trust_remote_code=False
                )
                model = AutoModel.from_pretrained(
                    directory,
                    local_files_only=True,
                    trust_remote_code=False,
                    dtype=torch.float16,
                    device_map={"": "cuda:0"},
                    attn_implementation="sdpa",
                ).eval()
                dimension = model.config.vision_config.hidden_size
                if dimension != model.config.text_config.hidden_size:
                    raise RuntimeError(
                        "This adapter requires matching SigLIP image/text dimensions"
                    )
                config = {
                    "backend": "siglip2",
                    "checkpoint": self.settings.modelId,
                    "revision": self.settings.modelRevision,
                    "dimension": dimension,
                    "preprocessing": {
                        "image": processor.image_processor.to_dict(),
                        "tokenizerFiles": {
                            name: sha
                            for name, sha in self.settings.fileHashes.items()
                            if name != "model.safetensors"
                        },
                        "padding": "max_length",
                        "maxLength": model.config.text_config.max_position_embeddings,
                        "truncation": True,
                        "exifTranspose": True,
                    },
                    "precision": "float16",
                    "normalization": "l2",
                    "runtime": f"python={platform.python_version()};torch={torch.__version__};transformers={transformers.__version__};cuda={torch.version.cuda};device=cuda;attention=sdpa",
                }
                self.processor, self.index_config, self.model = processor, config, model
                self.state = "ready"
                return self.status()
            except Exception as exc:
                self.state, self.error = "failed", str(exc)
                self.unload(keep_error=True)
                raise

    def unload(self, keep_error=False):
        with self.gpu_lock:
            self.model, self.processor = None, None
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            if not keep_error:
                self.state, self.error = "unloaded", None
            return self.status()

    @staticmethod
    def features(output):
        import torch

        value = output if isinstance(output, torch.Tensor) else output.pooler_output
        if (
            value.ndim != 2
            or not torch.isfinite(value).all()
            or (value.float().norm(dim=-1) == 0).any()
        ):
            raise RuntimeError("Encoder returned invalid features")
        return torch.nn.functional.normalize(value.float(), dim=-1).cpu().tolist()

    def encode(self, texts=None, images=None):
        with self.gpu_lock:
            self.load()
            import torch

            if texts is not None:
                inputs = self.processor(
                    text=texts,
                    padding="max_length",
                    truncation=True,
                    max_length=self.model.config.text_config.max_position_embeddings,
                    return_tensors="pt",
                ).to("cuda:0")
                with torch.inference_mode():
                    return self.features(self.model.get_text_features(**inputs))
            vectors = []
            # Decode and encode one original at a time to keep GPU/RAM use bounded.
            for encoded in images:
                data = base64.b64decode(encoded, validate=True)
                if len(data) > 8 * 1024 * 1024:
                    raise ValueError("Image exceeds 8 MiB service input bound")
                try:
                    with Image.open(io.BytesIO(data)) as original:
                        if (
                            original.width * original.height
                            > self.settings.maxImagePixels
                        ):
                            raise ValueError("Image exceeds the configured pixel bound")
                        image = ImageOps.exif_transpose(original).convert("RGB")
                except (OSError, Image.DecompressionBombError) as exc:
                    raise ValueError("Invalid or oversized image") from exc
                inputs = self.processor(images=[image], return_tensors="pt").to(
                    "cuda:0", dtype=torch.float16
                )
                with torch.inference_mode():
                    vectors.extend(
                        self.features(self.model.get_image_features(**inputs))
                    )
            return vectors
