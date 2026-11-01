# Laptop preparation agent

This is a pull executor. Zima owns the database, cache, jobs, reviews and export. The
laptop keeps only configuration and local model files; it has no durable queue database.
The frontend remains the restored mock and is not connected to these endpoints.

Install with Python 3.12 in the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[agent]"
.\.venv\Scripts\python -m agent --print-preparation-config
```

The last command prints the exact installed Pillow/OpenCV/NumPy/Python fingerprint and
processing bounds. Use its output as `preparation` in a local config. Set `serverUrl` to
your actual private Zima URL, `owner` to a stable agent name, and `rootMappings` to the
actual server root IDs and **read-only** paths accessible from the laptop:

```json
{
  "serverUrl": "http://YOUR-ZIMA-HOST:8000",
  "owner": "gpu-laptop",
  "tokenEnv": "PHOTOSIFT_AGENT_TOKEN",
  "rootMappings": { "YOUR-REGISTERED-ROOT-ID": "YOUR-ABSOLUTE-READ-ONLY-PATH" },
  "preparation": {}
}
```

The placeholders and empty preparation object must be replaced. Save as
`agent.local.json` (gitignored). The token stays in the named environment variable, not
the config file. Configure the same `PHOTOSIFT_AGENT_TOKEN` on the server before startup.
Do not expose the single-owner app to an untrusted network; use private networking or an
authenticated proxy. The default server listener is loopback.

```powershell
# Explicitly activate this exact preparation contract, then pull work:
.\.venv\Scripts\python -m agent --config agent.local.json --configure-preparation

# Later starts reuse the registered contract without changing it:
.\.venv\Scripts\python -m agent --config agent.local.json
```

`--once` drains available compatible jobs and exits. Unfinished jobs become eligible when
their leases expire. The agent renews leases during work, submits each image independently,
and reports failures. Accepted-result replay is idempotent. Source changes are rejected.

Preparation reads the mapped original through a read-only handle, checks size/mtime/hash,
decodes the oriented first frame, extracts available EXIF date/camera/GPS, computes pHash,
dHash, Laplacian variance and luminance/clipping signals, and makes bounded WebP thumbnails
and previews without embedded EXIF/GPS. Decoder errors are explicit; HEIF/RAW support is
not claimed simply because the inventory recognizes a file extension. No OCR runs.

YuNet is optional and explicit. Supply a compatible local ONNX model:

```powershell
.\.venv\Scripts\python -m agent --print-preparation-config --yunet C:\path\to\yunet.onnx
.\.venv\Scripts\python -m agent --config agent.local.json --yunet C:\path\to\yunet.onnx --configure-preparation
```

Use that printed config in `agent.local.json` and pass the same model at every start.
Its SHA-256, thresholds and input size enter the fingerprint. Missing/different weights
are an error, never a fallback. Detector errors or no configured detector remain Unknown;
zero detections do not imply no people or disposable content. Actual YuNet weights and face
accuracy have not been validated on this library.

Image/text encoder execution remains pending exact checkpoint/runtime selection and a
labelled comparison. Zima already supports isolated eval indexes, validated embedding
results and comparison/promotion endpoints; no model is downloaded or promoted automatically.
D3 remains unconfigured: project docs name `/v1/systemone` but do not contain its payload
schema. No `/api/chat` or engine substitution has been added.
