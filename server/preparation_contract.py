from typing import Literal

from pydantic import Field, model_validator

from server.contracts import Contract, Face


class PreparationConfig(Contract):
    contract: Literal["photosift-prepare-v1"] = "photosift-prepare-v1"
    implementation: Literal["photosift-pillow-opencv-v1"] = "photosift-pillow-opencv-v1"
    pillowVersion: str = Field(min_length=1, max_length=100)
    opencvVersion: str = Field(min_length=1, max_length=100)
    numpyVersion: str = Field(min_length=1, max_length=100)
    runtime: str = Field(min_length=1, max_length=200)
    thumbnailEdge: int = Field(default=256, ge=64, le=512)
    previewEdge: int = Field(default=1600, ge=512, le=2048)
    webpQuality: int = Field(default=85, ge=1, le=100)
    maxSourceBytes: int = Field(
        default=256 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024
    )
    maxPixels: int = Field(default=80_000_000, ge=1_000_000, le=120_000_000)
    faceModelSha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    faceThreshold: float = Field(default=0.9, gt=0, le=1)
    faceNmsThreshold: float = Field(default=0.3, gt=0, le=1)
    faceInputEdge: int = Field(default=640, ge=320, le=2048)


class GPS(Contract):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class PreparationResult(Contract):
    width: int = Field(gt=0, le=120_000)
    height: int = Field(gt=0, le=120_000)
    format: str = Field(min_length=1, max_length=30)
    captureDate: str | None = None
    captureDateSource: Literal["exif", "filename", "file_time", "unknown"] = "unknown"
    camera: str | None = Field(default=None, max_length=200)
    gps: GPS | None = None
    faceStatus: Literal["done", "not_checked", "failed"]
    faceError: str | None = Field(default=None, max_length=1000)
    faces: list[Face] = Field(default_factory=list, max_length=1000)
    pHash: str = Field(pattern=r"^[a-f0-9]{16}$")
    dHash: str = Field(pattern=r"^[a-f0-9]{16}$")
    sharpness: float = Field(ge=0, allow_inf_nan=False)
    meanLuminance: float = Field(ge=0, le=255, allow_inf_nan=False)
    darkFraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    brightFraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    thumbnail: str = Field(max_length=3_000_000)
    preview: str = Field(max_length=12_000_000)

    @model_validator(mode="after")
    def consistent(self):
        if self.captureDate:
            from datetime import datetime

            datetime.fromisoformat(self.captureDate)
            if self.captureDateSource == "unknown":
                raise ValueError("A capture date requires provenance")
        elif self.captureDateSource != "unknown":
            raise ValueError("Date provenance requires a date")
        if self.faceStatus != "done" and self.faces:
            raise ValueError(
                "Failed or unchecked faces cannot be treated as detections"
            )
        for face in self.faces:
            x, y, w, h = face.box
            if (
                not all(0 <= v <= 1 for v in face.box)
                or w <= 0
                or h <= 0
                or x + w > 1.00001
                or y + h > 1.00001
            ):
                raise ValueError(
                    "Face boxes must be normalized within the oriented frame"
                )
        return self
