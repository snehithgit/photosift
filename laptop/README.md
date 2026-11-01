# Laptop model service

Selected loading default: **google/siglip2-so400m-patch16-384**, revision
`dd658faac399427308559e2c3ac1e99cbe43845d`, native Transformers/PyTorch CUDA,
FP16, 384px images, normalized 1152-dimensional image and text vectors.
This is a quality-first choice for photo/category matching and multilingual text,
based on Google's published model results. It is not a claim of measured PhotoSift
accuracy. MobileCLIP2-S2 remains a smaller alternative for comparison. Only one
configured encoder is served; vectors from different models cannot be mixed.

References: [Google SigLIP2 results](https://google-research.github.io/big_vision/big_vision/configs/proj/image_text/README_siglip2.html),
[official checkpoint](https://huggingface.co/google/siglip2-so400m-patch16-384),
[Apple MobileCLIP2](https://github.com/apple-aiml-research/ml-mobileclip).

## Start and stop

From the project root in PowerShell:

```powershell
.\Setup-Laptop.ps1          # one-time dependencies + pinned model download/checksums
.\Start-PhotoSift.ps1        # background services; waits for actual model readiness
.\Status-PhotoSift.ps1
.\.venv-laptop\Scripts\python -m laptop.probe  # generated image + text HTTP smoke
.\Stop-PhotoSift.ps1
```

Double-click `Start-PhotoSift.cmd` / `Stop-PhotoSift.cmd` for the same actions.
Setup needs Python 3.12, NVIDIA drivers supporting CUDA 13.0, internet access and
installed Ollama. It uses `.venv-laptop`, separately from the backend environment.
PyTorch/torchvision are pinned to their CUDA 13.0 wheels from the official PyTorch
index; the remaining dependencies are in `requirements-laptop.lock`.

Start launches a dedicated Ollama on **127.0.0.1:11435** and the model gateway on
**0.0.0.0:8787**. Existing Ollama on 11434 is left alone. Only one request can use
the gateway GPU slot; image batches run one image at a time. External GPU programs
can still consume VRAM. No CPU fallback, alternate model or remote inference is used.
Start/stop are repeatable and identify owned processes by PID, creation time and
command hash. Failed starts roll back newly launched processes only.

Status exits 0 when everything is running and ready, and 3 when something is not. Status
and Stop keep working while Start waits for the model; Stop cancels that Start. Nothing
restarts a service that dies, but a running agent waits and carries on once Start is run again.

This launcher runs the laptop model services, and the pull agent once `agent.local.json`
exists (`.data/services/agent.log`). Zima continues to own the API, worker, SQLite and
library state. It does not start or modify the frontend, configure a library or activate an
embedding index; the [agent guide](../agent/README.md) has those explicit steps.

## URLs for other machines

Embedding jobs do not need these: the agent runs on this laptop and calls the gateway
locally. They are for a client on another machine. The scripts print the detected laptop address and write `.data/services/endpoints.json`.
On the tested Wi-Fi this is `192.168.68.65` (DHCP may change it):

- `http://192.168.68.65:8787/v1/embeddings`
- `http://192.168.68.65:8787/v1/models`
- `http://192.168.68.65:8787/health`

All `/v1/*` routes require `Authorization: Bearer <key>`. The generated key is in
`.data/services/model-api.token`; copy it privately to Zima's model-client configuration.
It is never printed by start/status and is excluded from Git with all `.data/` files.
Keep the service on your trusted home LAN; plain HTTP does not encrypt this token or images.
If Windows Firewall blocks Zima, run `Enable-PhotoSiftLAN.ps1` explicitly in an
Administrator PowerShell. It permits TCP 8787 only for the Private profile and local
subnet, so it does not apply while the network is set to Public; the script warns about
that. No firewall change is made automatically. Reserve the laptop IP in your router
or set `advertiseHost` in `.data/services/laptop.json` to a stable hostname/IP.

Embedding requests are JSON, either `{"input":["a family photo","a receipt"]}`
or `{"images":["<base64 image bytes>"]}`. These are alternative inputs. Maximum
8 inputs, 4096 characters per text, 8 MiB decoded image, 16 MiB whole request,
80 million pixels. No file paths or remote image URLs are accepted. Response includes
OpenAI-style `data[].embedding`, model, dimension and a fingerprint. `/health` includes
the exact `embedding.indexConfig` for explicit backend registration. Image preprocessing,
tokenizer hashes, revision, precision and runtime enter the fingerprint. Image and text
features share this same index contract. Files are checksummed again on each model load.
`POST /v1/model/load` and `/v1/model/unload` explicitly control VRAM residency.

## D3 Nano

The local tag is `hf.co/ggml-org/vllm-d3-nano-GGUF:Q8_0`. Ollama is its runtime;
SigLIP2 runs through Transformers, not Ollama. D3 weights were already installed on this
laptop. Both models stay loaded together: 5.3 GB of the 8 GB, measured with nothing else on
the GPU. Other GPU programs can still run it out of memory.

`POST /v1/systemone` forwards to the owned Ollama's documented
[decision API](https://docs.ollama.com/api/systemone), with the model fixed by this service.
The body is `{"state": "...", "images": ["<base64>"], "questions": {...}}`, one image per
request. Each question has a `type` of `choice`, `noul` or `score`, `instructions`, and
`criteria`. Answers come back per question name: `noul` is the probability of yes, and `choice`
carries `choice`, `probabilities` and `confidence`. They are not a chat reply, and `/api/chat`
is not used. `/health` reports `d3.state` with the exact model digest and Ollama version.

Logs and configuration: `.data/services/{models,ollama,agent}.log`, `laptop.json`,
`processes.json`. No original photo paths are configured or written by this service.
Actual library accuracy, sustained throughput and remote Zima connectivity remain unverified. Primary-index promotion still requires an explicit labelled report
and complete backfill; selecting the loading default does not bypass that protection.
