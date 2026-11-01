import base64
import hashlib
import io
import os
import platform
import re
import stat
from datetime import datetime
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
import PIL
from PIL import Image, ImageOps

from server.paths import checked_path
from server.preparation_contract import PreparationConfig, PreparationResult


def configuration(model_path=None, **overrides):
    digest = (
        hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
        if model_path
        else None
    )
    return PreparationConfig(
        pillowVersion=PIL.__version__,
        opencvVersion=cv2.__version__,
        numpyVersion=np.__version__,
        runtime=platform.python_version()
        + "-"
        + platform.system()
        + "-"
        + platform.machine(),
        faceModelSha256=digest,
        **overrides,
    )


def gps_value(exif):
    try:
        gps = exif.get_ifd(34853)

        def degrees(values):
            return float(values[0]) + float(values[1]) / 60 + float(values[2]) / 3600

        return {
            "lat": degrees(gps[2]) * (-1 if gps[1] == "S" else 1),
            "lon": degrees(gps[4]) * (-1 if gps[3] == "W" else 1),
        }
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def read_verified(root, claim, max_bytes):
    # Only a mapped root and a validated relative path are used. No server absolute path.
    path = checked_path(root, claim["relativePath"])
    before = path.stat()
    if before.st_size != claim["size"] or before.st_mtime_ns != claim["mtimeNs"]:
        raise ValueError("Source changed; inventory refresh required")
    if before.st_size > max_bytes:
        raise ValueError("Source exceeds configured preparation memory bound")
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Mapped source is not a regular file")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Source identity changed before preparation")
        data = source.read(max_bytes + 1)
    after = checked_path(root, claim["relativePath"]).stat()
    if (
        len(data) > max_bytes
        or (before.st_size, before.st_mtime_ns, before.st_ino)
        != (after.st_size, after.st_mtime_ns, after.st_ino)
        or hashlib.sha256(data).hexdigest() != claim["sha256"]
    ):
        raise ValueError("Source hash/version mismatch; inventory refresh required")
    return path, data


def download_verified(client, claim, max_bytes):
    """Fetch the leased original from the server, for a laptop with no path to the library."""
    if claim["size"] > max_bytes:
        raise ValueError("Source exceeds configured preparation memory bound")
    data = bytearray()
    with client.stream(
        "POST",
        "/api/agent/jobs/" + claim["jobId"] + "/source",
        json={key: claim[key] for key in ("owner", "token", "attempt")},
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > claim["size"]:
                raise ValueError("Source hash/version mismatch; rescan required")
    if (
        len(data) != claim["size"]
        or hashlib.sha256(data).hexdigest() != claim["sha256"]
    ):
        raise ValueError("Source hash/version mismatch; rescan required")
    return bytes(data)


def prepare(root, claim, config: PreparationConfig, model_path=None, data=None):
    if data is None:
        path, data = read_verified(root, claim, config.maxSourceBytes)
    else:
        path = PurePosixPath(claim["relativePath"])
    with Image.open(io.BytesIO(data)) as original:
        if original.width * original.height > config.maxPixels:
            raise ValueError("Image exceeds configured decode pixel bound")
        exif = original.getexif()
        capture, provenance = None, "unknown"
        try:
            raw_date = exif.get_ifd(34665).get(36867) or exif.get(306)
            if raw_date:
                capture = datetime.strptime(
                    str(raw_date), "%Y:%m:%d %H:%M:%S"
                ).isoformat()
                provenance = "exif"
        except (ValueError, TypeError, KeyError):
            pass
        if capture is None:
            match = re.search(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)", path.stem)
            if match:
                try:
                    capture = (
                        datetime(*(int(value) for value in match.groups()))
                        .date()
                        .isoformat()
                    )
                    provenance = "filename"
                except ValueError:
                    pass
        camera = (
            " ".join(str(exif.get(tag, "")).strip() for tag in (271, 272)).strip()
            or None
        )
        gps = gps_value(exif)
        original.seek(0)
        oriented = ImageOps.exif_transpose(original).convert("RGB")
        format_name = original.format or "Unknown"
    # Image memory is bounded by maxPixels; source bytes are released before derived arrays.
    del data
    assessment = oriented.copy()
    assessment.thumbnail(
        (config.previewEdge, config.previewEdge), Image.Resampling.LANCZOS
    )
    pixels = np.asarray(assessment)
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    coefficients = cv2.dct(small)[:8, :8].flatten()
    median = np.median(coefficients[1:])
    p_hash = "".join("1" if value > median else "0" for value in coefficients)
    difference = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    d_hash = "".join(
        "1" if value else "0"
        for value in (difference[:, 1:] > difference[:, :-1]).flatten()
    )
    face_status, face_error, faces = "not_checked", None, []
    if config.faceModelSha256:
        if (
            not model_path
            or hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
            != config.faceModelSha256
        ):
            raise ValueError("Exact configured YuNet model is unavailable; no fallback")
        try:
            face_image = oriented.copy()
            face_image.thumbnail((config.faceInputEdge, config.faceInputEdge))
            bgr = cv2.cvtColor(np.asarray(face_image), cv2.COLOR_RGB2BGR)
            height, width = bgr.shape[:2]
            detector = cv2.FaceDetectorYN.create(
                str(model_path),
                "",
                (width, height),
                config.faceThreshold,
                config.faceNmsThreshold,
                5000,
            )
            _, detections = detector.detect(bgr)
            for box in detections if detections is not None else []:
                x, y, w, h = map(float, box[:4])
                left, top = max(0, x / width), max(0, y / height)
                right, bottom = min(1, (x + w) / width), min(1, (y + h) / height)
                if right > left and bottom > top:
                    faces.append({"box": [left, top, right - left, bottom - top]})
            face_status = "done"
        except cv2.error as exc:
            face_status, face_error = "failed", str(exc)[:1000]

    def webp(image, edge):
        copy = image.copy()
        copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        copy.save(buffer, "WEBP", quality=config.webpQuality, exif=b"")
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    return PreparationResult(
        width=oriented.width,
        height=oriented.height,
        format=format_name,
        captureDate=capture,
        captureDateSource=provenance,
        camera=camera,
        gps=gps,
        faceStatus=face_status,
        faceError=face_error,
        faces=faces,
        pHash=f"{int(p_hash, 2):016x}",
        dHash=f"{int(d_hash, 2):016x}",
        sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        meanLuminance=float(gray.mean()),
        darkFraction=float(np.mean(gray < 8)),
        brightFraction=float(np.mean(gray > 247)),
        thumbnail=webp(oriented, config.thumbnailEdge),
        preview=webp(assessment, config.previewEdge),
    )
