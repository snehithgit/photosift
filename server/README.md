# PhotoSift backend

Python 3.12, FastAPI/Pydantic, SQLite WAL on local disk, a Zima background worker and a
separate laptop preparation pull agent. The frontend in `web/` reads and writes only this
API; it holds no mock data. No real photo library is seeded or assumed by the backend.

## Run

From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python -m pip install --no-deps -e .
.\.venv\Scripts\python -m server
```

In another terminal from the same directory:

```powershell
.\.venv\Scripts\python -m worker
```

Open <http://127.0.0.1:8000/docs> for the API. For the app, either build the frontend once
(`pnpm run build` in `web/`) and open <http://127.0.0.1:8000>, or run `pnpm dev` in `web/`
and open <http://localhost:3000>, which proxies `/api` here (`PHOTOSIFT_API` overrides the
target). Scans, bulk actions, large undos and exports wait until the worker is running.
Unknown `/api` paths remain JSON 404s. `python -m worker --once` drains ready scans,
review/undo jobs and approved exports. It does not wait for unexpired leases of stopped
processes.

Default app data is `.data/photosift.sqlite3`. To change it, set `PHOTOSIFT_DB` to the same
absolute **local-disk** filename on server and Zima worker. Do not place SQLite/WAL on SMB
or NFS. Keep app data, cache, source roots and export destinations separate. Source roots
must be mounted read-only on both machines. OS-managed atime may change on writable mounts;
use RO/noatime where available. Linux commands use `.venv/bin/python`.

The worker's default SHA-256 read cap is 32 MiB/s per process, configurable through
`--hash-mib-per-second`. This is a resource limit, not a measured throughput claim.
Configure the laptop independently using [agent/README.md](../agent/README.md).

## Implemented capabilities

| Area | Current behavior |
|---|---|
| Inventory | Read-only streaming SHA-256, exact content/path separation, incremental/verify scans, history, errors, lease recovery, no missing reconciliation on incomplete/offline scans |
| Review | Group counts, global server-side grid grouping, bounded pages, loaded-query fingerprints, frozen selections, durable bulk jobs, conflicts, version-aware undo, audit |
| Preparation | Mapped read-only laptop source reads, hash/version checks, oriented first-frame decode, EXIF, thumbnails/previews, pHash/dHash, quality signals, optional checksum-pinned YuNet |
| Dispatch/cache | Authenticated claims, renewals, source-version binding, typed per-image results, duplicate acknowledgement, bounded atomic cache publication, explicit failures |
| Embedding | One explicitly active index filled by leased laptop jobs, queued automatically after preparation; isolated eval index registry, full fingerprints, normalized/dimension-checked vector ingestion, prompt packs encoded on the laptop, labelled samples with server-side event-group splits, held-out comparison reports with cut-offs chosen on the train split, explicit backfill-gated primary promotion |
| Suggestions | Bounded prompt-scoring batches with provenance and conservative protection gate; same-index pHash/time neighbor suggestions; deterministic time-neighbor event suggestions; no final decision writes |
| Search | Filename/path search; example-image similarity using an explicitly promoted primary index. New semantic text encoding is pending a real encoder adapter |
| Export | Frozen copy plans, plan-bound approval, independent checksum-verified copies, exclusive publication, journal, recovery, pause/resume/retry, atomic manifests and identity/hash-checked rollback |
| Settings/events | Optimistic settings, manual group overrides, event rename/confirm/merge/split, preserved manual event memberships/edits |

No index is promoted by default. Photos stay Unclassified until the owner encodes prompts, labels
a sample, reads the report, promotes and runs grouping; see the [agent guide](../agent/README.md).
D3 Nano runs only after `--configure-assessment`. Its answers become evidence and holds, and never
a Keep or Clutter decision:

| Axis | Yes at or above | No at or below | Between |
|---|---|---|---|
| Sensitive | 0.5 | 0.02 | Unknown: held, and out of `Organized/` |
| Worth keeping | 0.5 | 0.2 | Unknown: held |
| People | 0.5 (protects the photo) | 0.1 (stands in for an unrun face check) | Unknown: held |

A clear "no" on sensitive lets a photo show unblurred and reach `Organized/`. The answers are model
signals, and their accuracy on real IDs and private records has not been measured. Each result
keeps D3's raw answers, its duration, and the model digest, Ollama version and question version.
OCR, optional personal-classifier training and Immich API integration are not implemented.

## Inventory and review API

| Operation | Endpoint |
|---|---|
| Libraries/folders | `GET/POST /api/roots`, `GET /api/folders?root=` |
| Inventory/problems | `GET /api/library/stats`, `/api/library/files?state=` |
| Scan | `POST /api/roots/{root}/scans`, `GET /api/scans/{id}` |
| Cards/sample review | `GET /api/groups`, `/api/groups/{id}/review` |
| Photos | `GET /api/photos`, `/api/photos/{sha256}`, `/api/photos/{sha256}/related` |
| Selection/bulk | `POST /api/selections`, `POST /api/bulk`, `GET /api/jobs/{id}`, `/api/jobs/{id}/items` |
| Single review/undo | `PATCH /api/photos/{sha256}`, `GET /api/history`, `POST /api/history/{actionId}/undo` |
| Events | `GET/POST /api/events`, `GET/PATCH /api/events/{id}`, `POST …/merge`, `POST …/split` |
| Duplicates | `GET /api/duplicates?kind=all|exact|possible|burst` (pages whole sets) |
| Pipeline/status | `GET /api/status`, `/api/stages`, `POST /api/analyze/{pause,resume,cancel,retry}` |
| Settings | `GET /api/settings`, `GET/PATCH /api/settings/{section}` |

Register an existing actual root using `POST /api/roots` with `{name,path}`, then queue a scan
with `{folder:"",verify:false}`. Run the Zima worker. Folder paths are root-relative POSIX
paths; absolute paths, `..`, symlinks and reparse points are rejected. Unsupported files are
hashed/inventoried but not passed off as analyzed photos. Exact copies are one content and
one decision with every path retained. Replaced content stays in history, not active views.

Photo filters include `root`, `folder`, `group`, `event`, `documentType`, `band`, `origin`,
`format`, `q`, `protectedOnly`, `faces`, `duplicate`, `decisions`, `reviewed`, `sort`, `by`,
`page`, `limit` (max 500), `revision`. Omitted root means an explicit all-library request.
Working views default to undecided; `reviewed=1` or `decisions=all` includes reviewed content.
Grid grouping happens before paging and headers report full-query counts. Protected photos
never enter Sample/Borderline. Sensitive or unchecked cached media requires `?reveal=true`.

Freeze selections using the photo page's `revision` and `queryFingerprint`, the same `query`,
optional exclusions/explicit IDs and explicit `includeProtected`. Confirm the returned exact
count, then post `{selectionId,decision}` to `/api/bulk`. Repeated identical requests reuse the
same durable job. Different decisions require a new frozen selection. Stale review/source/
policy versions produce per-item conflicts. Job conflict previews are capped at 100; `/items`
pages all outcomes. Single edits require `expectedVersion` from `reviewVersion` and exactly
one of `decision`, `groupId`, `removeFromEvent:true`. Protected-photo Clutter overrides require
`overrideProtected:true`. Large undo actions return a durable job with HTTP 202.

Manual Keep, faces, documents, folders, reliable configured age, positive importance and
sensitivity hold default bulk membership. Importance and sensitivity remain separate axes.
Unknown required checks hold automatic Clutter suggestions. Personal/document groups are
never suggested as clutter. Only explicit user decisions change Keep/Clutter/Later.

## Preparation and embedding infrastructure

`POST /api/pipeline/preparation/configure` explicitly registers a full `PreparationConfig` and
queues unique contents. The agent prints this config from its installed runtime. Changing
preparation invalidates old claims; reactivating an existing fingerprint restores its matching
metadata/cache without mixing versions. No detector means Unknown, not an empty face result.
All writes remain on Zima. Request payloads are capped before JSON/base64 parsing.

| Model operation | Endpoint |
|---|---|
| Register/list eval spaces | `POST/GET /api/indexes` |
| Choose the index the laptop fills | `POST /api/pipeline/embedding/configure`, `GET /api/pipeline/embedding` |
| Download a leased job's original | Authenticated `POST /api/agent/jobs/{id}/source` |
| Queue prepared content | `POST /api/indexes/{id}/queue` with explicit `contentIds` |
| Frozen labelled sample | `POST /api/comparison/samples` |
| Versioned prompt vectors | `POST /api/indexes/{id}/prompts` |
| Held-out category metrics | `POST /api/indexes/{id}/evaluate` |
| Explicit primary selection | `POST /api/indexes/{id}/promote` |
| Activate D3 assessment | `POST /api/pipeline/assessment/configure`, `GET /api/pipeline/assessment` |
| Per-image GPU results | Authenticated `POST /api/agent/jobs/{id}/result` |
| Hand a job back without failing it | Authenticated `POST /api/agent/jobs/{id}/release` |
| Stage failures/status | `GET /api/pipeline/jobs?root=&stage=&state=failed` |
| Prompt scoring | `POST /api/pipeline/score` |
| Neighbor/event suggestions | `POST /api/pipeline/suggest-neighbors`, `/api/pipeline/suggest-events` |

Index fingerprints include backend, immutable revision, dimension, full preprocessing,
precision, normalization and runtime. Vectors must already be finite L2-normalized float32
storage inputs of the exact dimension. Model spaces are never mixed. Registration does not
promote an index. Promotion requires its comparison report and backfill of current available
contents; it invalidates old automatic triage/selection policy while preserving user decisions.

Comparison samples keep exact content IDs, content labels and train/test event/similarity-group
splits. Duplicate contents and cross-split event leakage are rejected. Evaluate both candidates
on the **same sample ID**; synthetic test metrics are not real model-quality measurements.
Scoring requires the primary index, matching prompt/report versions and explicit margin
thresholds. It processes at most the requested page size; follow `nextPage` for another batch.
Scores/margins are ranking signals, not probabilities. It never resolves sensitivity/importance
from embeddings. Unresolved content queues a D3 assessment and stays held.

Neighbor suggestions require explicit pHash, similarity, time and neighbor bounds, remain
within one effective group and are unticked. Time-event suggestions require explicit gap/count
parameters, exclude unknown/date-only times and never replace existing event edits. Camera
origin is not an eligibility condition. They do not infer places, home locations or trips.

## Verified export

Create `/api/export/plans` from an explicit scope and reviewed decisions, destination and
layout. Sensitive/unknown content is excluded unless explicitly included, in which case it
maps only to `Private/Sensitive/`. Later and Clutter are opt-in outside `Organized/`. Exact
copies yield one plan entry. Sources/app data/outputs cannot overlap. Sources, SHA-256, sizes,
decision/group snapshots, collision renames and observed free capacity are frozen.

Approve using the exact displayed plan `revision` and `fingerprint`. Stale snapshots block
approval. Approval queues real work; **running the Zima worker can create the approved copies**.
Old Phase 1 unavailable runs require an explicit resume and do not start automatically.

The worker streams an independent copy, checks the frozen source and output hashes, fsyncs
the temporary output, journals its identity, and publishes without overwrite. Windows uses
exclusive rename; Linux uses `renameat2(RENAME_NOREPLACE)` and directory fsync. No hardlinks or
unsafe fallback rename are used. Only Windows has been exercised in this environment.

| Export operation | Endpoint |
|---|---|
| Plan list/detail | `GET /api/export/plans`, `/api/export/plans/{id}` |
| Approval | `POST /api/export/plans/{id}/approve` |
| Run/status | `GET /api/export/runs`, `/api/export/runs/{id}` |
| Controls | `POST /api/export/runs/{id}/{pause,resume,retry,rollback}` |
| Operation journal | `GET /api/export/runs/{id}/journal` |
| Manifest | `GET /api/export/runs/{id}/manifest?format=json|csv` |

Recovered publication requires matching journaled identity and checksum. Unowned collisions
are preserved. Rollback checks output identity/mtime/size/hash and removes only unchanged,
verified run-owned outputs; changed or ambiguous files remain as explicit conflicts. A rollback
after a publication crash recovers ownership without copying pending photos. Empty directories,
manifests and interrupted temporary files are preserved; cleanup never recursively removes a
run directory. Completed runs publish atomic JSON/CSV manifests. Rollback publishes separate
rollback manifests so the original completion record remains available.

## Verification and remaining gates

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\ruff check server worker agent tests
.\.venv\Scripts\ruff format --check server worker agent tests
```

Tests cover source immutability, Windows junction refusal, inventory/review regression, 50,000
generated metadata actions, real process smoke, actual generated-image decode/EXIF/cache,
fingerprints, stale/replayed results, isolated vector arithmetic, comparisons, conservative
triage, neighbor/event suggestion rules, actual verified copy/rollback and publication-crash
recovery. Symbolic-link creation may be skipped on Windows without that privilege.

Current verification: **119 passed, 1 skipped** on Windows/Python 3.12.10. Ruff and dependency
checks pass. The live smoke exercises API, Zima worker and laptop preparation agent as separate
processes over HTTP. One Starlette/httpx test-client deprecation warning remains.

Embedding jobs also ran against the real RTX 5060 service on 240 generated 12 MP JPEGs, on a
scratch database and including a killed model service mid-job. About 1.5 s per photo for
preparation and embedding together; real photos may differ.

D3 Nano ran on the real GPU on 58 generated images and a read-only pass over 15 real photos, on a
scratch database, at a median of 1.4 s per photo. It flagged or held all 24 fake IDs, cards and
records, and flagged none of 34 plain images. Generated images say little about real documents.

No real library/share/Zima deployment, real model accuracy, real YuNet accuracy, Linux rename path
or SMB export behavior has been verified. Optional learning and Immich APIs are later work.

Migrations `001`–`010` are append-only and hash-checked. Never edit an applied migration.
Back up SQLite with its backup API or coordinated stopped services; do not omit a live WAL.
