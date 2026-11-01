# Laptop agent

This is a pull executor for preparation and embedding jobs. Zima owns the database, cache, jobs, reviews and export. The
laptop keeps only configuration and local model files; it has no durable queue database.

Install with Python 3.12 in the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[agent]"
.\.venv\Scripts\python -m agent --print-preparation-config
```

The last command prints the exact installed Pillow/OpenCV/NumPy/Python fingerprint and
processing bounds. Use its output as `preparation` in a local config. Set `serverUrl` to
your actual private Zima URL and `owner` to a stable agent name. For any library not listed in
`rootMappings`, the agent downloads each leased original from the server and checks its hash, so
no file share is needed. Add a mapping only to read a library from a local **read-only** path:

```json
{
  "serverUrl": "http://YOUR-ZIMA-HOST:8000",
  "owner": "gpu-laptop",
  "tokenEnv": "PHOTOSIFT_AGENT_TOKEN",
  "rootMappings": {},
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

## Embedding jobs

With an `embedding` section in the config, the same loop also fills an embedding index once
nothing is left to prepare. It reads the original the same read-only way, decodes it, applies
EXIF orientation, scales the longest edge to at most 1536px, and sends lossless PNG pixels to
the model gateway on this laptop, which applies the checkpoint's own preprocessing. Paths never
reach the gateway, and Zima never needs to reach it, so no firewall change is needed.

```json
"embedding": {
  "gatewayUrl": "http://127.0.0.1:8787",
  "tokenFile": "C:/path/to/.data/services/model-api.token"
}
```

`tokenFile` can also replace `tokenEnv` at the top level. The server reads
`agent-api.token` next to its database when `PHOTOSIFT_AGENT_TOKEN` is unset.

When the laptop also runs the server, one command writes all of this, including a new token
and a mapping for each library the server knows. Run it again after adding a library:

```powershell
.\.venv\Scripts\python -m agent --config agent.local.json --init-local http://127.0.0.1:8000
```

Choosing the index is explicit. With the model service running, this registers the gateway's
exact contract plus the agent's input steps, queues every prepared photo, and drains the work:

```powershell
.\Start-PhotoSift.ps1
.\.venv\Scripts\python -m agent --config agent.local.json --configure-preparation --configure-embedding --once
```

After that, `Start-PhotoSift.ps1` runs the agent in the background and newly prepared photos
queue by themselves. The agent claims embedding work only while the model's fingerprint matches
the active index. A different model, revision, preprocessing or runtime stops the claims with a
message in `.data/services/agent.log`; nothing is mixed, and only `--configure-embedding`
starts a new index. It stays an evaluation index: filling one never promotes it, and photos
stay Unclassified until a labelled comparison and explicit promotion.

| What happened | What the agent does |
|---|---|
| Model service down, loading, unloaded or GPU busy | Hands the job back and waits; the photo is not failed |
| Library folder not reachable | Hands the job back and waits |
| Gateway refuses the image | Marks that photo failed; Analyze can retry it |
| Gateway keeps returning errors while healthy | Marks the photo failed on attempt 5 so it stops blocking the queue |
| Agent killed mid-job | The lease expires within two minutes and the job runs again |

## From a filled index to groups

Nothing here starts by itself, and filling an index still changes no photo.

1. **Encode the prompts.** Zima can't reach the gateway, so this runs on the laptop. It refuses
   unless the gateway's model matches the active index's exact fingerprint, and saves the pack
   under that index. A saved pack version is frozen; running it again keeps the first vectors.

   ```powershell
   .\.venv\Scripts\python -m agent --config agent.local.json --encode-prompts
   ```

2. **Label under Teach.** Finishing saves a frozen comparison sample. The server keeps photos of
   one moment (an event, an exact or possible match set, a shooting session, the same date or a
   near-identical pHash) on one side of the train/test split.
3. **In Settings, Models:** Run comparison shows the report. Make primary asks first and needs
   every photo to have a vector. Group photos then scores undecided photos, 100 at a time, with
   the cut-offs from that report. Keep and Clutter decisions don't change. A photo becomes a
   clutter suggestion only once D3 has also cleared it.

The report's cut-offs come from the train split only, and its figures from the held-out split.
High needs 95% and Medium 80% of that band's own train photos right, with at least 5 photos.
A target the labels don't prove leaves that band empty rather than guessing.

## D3 Nano assessment

Once nothing is left to prepare or embed, the agent sends the same oriented 1536px image to D3
Nano through the gateway and asks whether it shows sensitive content, is worth keeping, shows
people or text, and what kind of content it is. Turn it on once, with the model service running:

```powershell
.\.venv\Scripts\python -m agent --config agent.local.json --configure-assessment --once
```

This queues every prepared photo, and newly prepared photos queue by themselves after that. A
different model file, Ollama version or question wording stops the claims with a message in
`.data/services/agent.log` until you run it again; nothing is mixed. Outages and refused images
behave as in the embedding table above. Zima turns the answers into yes, no or unknown, and
unknown keeps the photo held; see the [backend guide](../server/README.md).
