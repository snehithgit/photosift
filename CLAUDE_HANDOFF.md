# PhotoSift handoff for Claude

Snapshot: 1 November 2026, Asia/Calcutta.
Workspace: `C:\Users\snehith\Documents\code\photosift`.
Repository: https://github.com/snehithgit/photosift, branch `main`.
Latest implementation commit: `7f027b2` (pushed). Working tree was clean before this handoff.

## Read before changing code

1. This snapshot, then `HANDOFF.md`, especially sections 4, 8 and 9.
2. `docs/02-pipeline.md` — canonical processing contract and build order.
3. `docs/10-frontend.md` and `docs/04-review-and-export.md`.
4. `docs/06-open-questions.md` — ask about actual unknowns instead of inventing answers.
5. `server/README.md`, `agent/README.md`, `laptop/README.md` for implementation commands.

Some older paragraphs describe previous milestones. In particular, statements saying that
the exporter is unimplemented, native embeddings are untested, or loading-model selection
is still pending are superseded by HANDOFF sections 8–11 and this snapshot. Leased image jobs,
prompt encoding, labelled comparison and scoring now run, measured only on generated images.

## User instructions that must survive this handoff

- **The frontend's look must not change.** An earlier integration attempt damaged the layout
  and was reverted. On the user's request the frontend was then connected to the real backend
  with mock data removed, keeping JSX structure and class names and leaving `web/src/styles.css`
  byte-identical. Change data wiring, not layout, unless the user asks for a design change.
- Continue backend/model work when requested. Do not claim the entire product is finished.
- The user initially left embedding selection pending, then explicitly asked to choose now.
  The current loading default is SigLIP2 SO400M/16-384. Do not keep treating selection as blocked.
- Originals stay read-only. No source deletion, moves, renaming, overwriting or hardlinks.
- Zima owns durable state, SQLite WAL, review decisions, jobs, cache and exports. The laptop
  is replaceable compute, not a second durable owner.
- Not a gallery. Reviewed photos leave working views. Set aside, never delete.
- Protection before suggestions. Personal photos and documents are never suggested as clutter.
- Sensitive IDs require blurred display and export only under `Private/Sensitive/`.
- Exact copies are one content with many paths. Possible matches stay unticked and within
  the effective group. One primary embedding space; never mix model vectors.
- Frozen, explicitly approved, verified independent-copy export. No OCR.
- D3 uses Ollama's documented `/v1/systemone` decision API (HANDOFF section 12). Do not
  substitute `/api/chat`, invent fields or claim its accuracy on real private records.

## Completed implementation

### Frontend — connected to the backend, look unchanged

React 19/TypeScript/Vite 7, TanStack Router/Query/Virtual and Zustand. `web/src/api.ts` holds
every request; `web/src/store.ts` holds UI state and actions; `web/src/domain.ts` holds types.
Screens load one server page at a time, never the whole library. Verified on a generated
fixture folder (scan, preparation, bulk, conflict, undo, event edits, settings, export, rollback,
source files unchanged) and on a generated 50,000-row database. Real-library speed is not measured.

Known gaps shown honestly in the UI: no promoted index means no grouping or similar-photo
search, and text search stays file-name only either way; nothing yet triggers event
suggestions; `sensitive` stays `unknown` until D3 clears a photo, so those photos display normally
but export only through the "Sensitive and not-yet-checked" option into `Private/Sensitive/`.

### Backend — `server/` and `worker/`

- Python 3.12, FastAPI/Pydantic, SQLite WAL; additive, hash-checked migrations `001`–`010`.
  Never edit applied migrations.
- Streaming read-only SHA-256 inventory, exact content/path separation, incremental/verify
  scans, history/errors, lease recovery and safe missing-file reconciliation rules.
- Group stats, globally paged/filtered photo lists, server-side grid grouping, photo detail,
  related photos, decisions, history, events, duplicate sets, library/analyze status, settings.
- Frozen select-all-matching selections and query fingerprints; durable bulk jobs, conflict
  reporting, review versions, version-aware undo and audit.
- Preparation contract activation, authenticated stage claims/renewals/failures, typed results,
  stale/source-version rejection, idempotent replay, atomic cache and matching reactivation.
- Isolated embedding index registry and normalized-vector acceptance; frozen labelled samples,
  event-group train/test separation, prompt packs, comparison reports and backfill-gated
  explicit primary promotion. This infrastructure is not a completed real-library evaluation.
- Conservative prompt/margin scoring with explicit calibration provenance and bounded batches.
  Unknown protection holds remain. Unticked same-group/time/pHash neighbor suggestions;
  time-based event suggestions preserve manual edits and do not infer trips or places.
- Real export executor: frozen plans/approvals, collision handling, independent copies,
  source/output checksums, fsync, no-overwrite publication, operation journals, crash recovery,
  pause/resume/retry, JSON/CSV manifests and ownership-checked rollback. Changed/unowned outputs
  survive rollback. Interrupted temporary files are preserved. Windows exercised; Linux/SMB unverified.

### Preparation pull agent — `agent/`

Mapped read-only source roots, hash/version checks, oriented first-frame decode, EXIF metadata,
bounded metadata-free WebP previews/thumbnails, pHash/dHash and quality signals. Optional
checksum-pinned YuNet; missing/error detector remains Unknown. Leases renew during work;
results submit independently. Real API + worker + preparation-agent subprocess smoke passed.
Actual Zima URL, root mappings, token and real YuNet weights are not configured/validated.

### Laptop inference service — `laptop/`

Selected model: `google/siglip2-so400m-patch16-384`.
Pinned revision: `dd658faac399427308559e2c3ac1e99cbe43845d`.
Upstream weight SHA-256: `422750e06216f053d85d4176fb6a9d4c5e71ded2d1b7c7e0e6c2efd909a1e427`.
Weights are downloaded locally and verified. Native Transformers/PyTorch CUDA, FP16,
384px preprocessing, L2-normalized 1152-D image/text vectors in one shared space.
This is a practical quality-first default, not a measured claim of best PhotoSift accuracy.
MobileCLIP2-S2 remains a smaller comparison candidate, not a second active primary.

- Separate GPU environment `.venv-laptop`: PyTorch `2.14.1+cu130`, torchvision `0.29.1`,
  Transformers `5.22.1`. Backend `.venv` remains separate. Exact dependencies are locked.
- Authenticated gateway on `0.0.0.0:8787`: text OR base64-image embeddings, model listing,
  explicit load/unload, public health with readiness and exact backend `indexConfig`.
- Revision, preprocessing, tokenizer hashes, precision and runtime enter the index fingerprint.
  Checkpoint files are verified again on load. No CPU fallback or silent model switching.
- One concurrent GPU request, bounded requests/batches; images process one at a time.
  No source-file paths or remote image URLs are accepted by the gateway.
- Dedicated owned Ollama on `127.0.0.1:11435`; shared existing Ollama on `11434` is preserved.
  Lifecycle tracks PID + creation time + command hash and stops only owned processes.
- D3 weights already installed: `hf.co/ggml-org/vllm-d3-nano-GGUF:Q8_0`. D3 runs through
  Ollama; embeddings run through Transformers. Both stay loaded: 5.3 GB of 8 GB measured.
  The gateway's `/v1/systemone` forwards to the owned Ollama; health reports the exact digest.

Current laptop LAN address is `192.168.68.65` (may change with DHCP):

- `http://192.168.68.65:8787/v1/embeddings`
- `http://192.168.68.65:8787/v1/models`
- `http://192.168.68.65:8787/health`

All `/v1/*` requests require `Authorization: Bearer <key>`; key is in the ignored local
`.data/services/model-api.token`. Never paste/commit the key. Local config, endpoints, logs
and process state are under `.data/services/`; model files under `.data/models/`.
The model service was rechecked during this handoff: owned Ollama and gateway running,
embedding state **ready**. This does not verify access from Zima or Windows Firewall.

## Commands

Run from the repository root in PowerShell. Laptop setup is already completed here:

```powershell
.\Setup-Laptop.ps1            # repeatable install/download; normally only needed for setup
.\Start-PhotoSift.ps1          # background model services; waits for actual readiness
.\Status-PhotoSift.ps1
.\.venv-laptop\Scripts\python -m laptop.probe
.\Stop-PhotoSift.ps1
```

Double-click alternatives: `Start-PhotoSift.cmd`, `Stop-PhotoSift.cmd`, or the PhotoSift Agent
window (`laptop/control.py`; `Add-PhotoSiftShortcut.ps1` makes its desktop shortcut). Nothing runs
at Windows startup: the owner asked for that explicitly.
Optional `Enable-PhotoSiftLAN.ps1` must be explicitly run as Administrator if needed;
it permits port 8787 only on Private profile/local subnet. It has not been applied automatically.
The launcher starts model services and, once `agent.local.json` exists, the pull agent. It does
not start Zima/backend/frontend. The firewall rule is not needed for embedding jobs.

Backend local development, in separate terminals:

```powershell
.\.venv\Scripts\python -m server
.\.venv\Scripts\python -m worker
```

API docs: `http://127.0.0.1:8000/docs`. Default DB: `.data/photosift.sqlite3` on local disk.
For a fresh backend environment use Python 3.12, `pip install -r requirements.lock`, then
`pip install --no-deps -e .`. See `agent/README.md` for actual server/root/token configuration
before starting `python -m agent`; do not invent or use placeholder deployment values.

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check server worker agent laptop tests
.\.venv\Scripts\python -m pip check
.\.venv-laptop\Scripts\python -m pip check
git diff --exit-code -- web
```

## Verified results and limits

- Latest full pytest: **174 passed, 1 skipped**. Skip: Windows symbolic-link privilege;
  separate Windows junction refusal passes. One Starlette/httpx deprecation warning remains.
- Source immutability tests check source bytes/tree/mtime/mode. Preparation/copy tests used
  temporary fixture libraries, not the owner's real originals.
- Ruff, dependency consistency and PowerShell syntax checks passed.
- Actual CUDA matrix execution passed on RTX 5060 Laptop GPU, 8 GB VRAM.
- Actual live HTTP probe passed for three text vectors and one generated-image vector:
  finite, L2-normalized, dimension 1152, same validated backend index fingerprint.
- Probe fingerprint: `69d377adea73fde5ca68585b17b81f96ebaa0992703ae4727d22ba849e0621ec`.
  Restart reproduced it. Compare the actual health contract, do not hardcode it into dispatch.
- Actual repeated start/stop/restart passed; shared Ollama preserved. Live missing-token 401
  passed. These are inference/service checks, not accuracy benchmarks.
- No sustained throughput or real-library classification accuracy was measured. No real
  Zima/share deployment, Linux export path, SMB export, real YuNet accuracy or D3 accuracy on
  real IDs and private records verified.

## Remaining work / suggested next backend step

1. Done: leased image embedding jobs (`agent/embed.py`, `server/embeddings.py`). The index
   fingerprint is the gateway's health contract plus the agent's input steps. Exercised on the
   real GPU with generated photos only, not the owner's library. `stage_results` for preparation
   still stores every thumbnail and preview as base64, duplicating the cache in SQLite.
2. Done for prompt packs (HANDOFF section 11). New semantic-text queries remain; use the same
   index contract instead of creating a competing index.
3. Pipeline done and measured on generated images only (HANDOFF section 11). The owner still has
   to label real photos under Teach, read that report and promote. Never auto-promote, and never
   present the generated figures as a quality report for real photos.
4. Done: D3 assessment (HANDOFF section 12). Its cut-offs in `server/assessments.py` come from
   generated images only; recheck them on real IDs and records the owner chooses to test.
5. Configure and test actual Zima access, read-only library mappings, tokens and optional YuNet.
   Validate on real deployment surfaces before claiming end-to-end readiness.
6. Optional personal-classifier learning and Immich API integration are later scope.

Commit history: `9e088c6` baseline mock/docs; `f6798e6` Phase 1 backend;
`c63eec1` preparation/comparison/export continuation; `7f027b2` GPU model service and scripts.
