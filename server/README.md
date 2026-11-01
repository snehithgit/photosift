# PhotoSift Phase 1 backend

Python 3.12, FastAPI/Pydantic, SQLite WAL on local disk, and a separate Zima worker.
The React mock is unchanged. This backend starts empty; it never loads the mock dataset.

## Run (PowerShell, from the repository root)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python -m pip install --no-deps -e .
.\.venv\Scripts\python -m server
```

In a second terminal, from the same directory:

```powershell
.\.venv\Scripts\python -m worker
```

API and interactive OpenAPI documentation: <http://127.0.0.1:8000/docs>.
`python -m worker --once` drains available work and exits. It does not wait for unexpired
leases from a stopped process. Normal worker mode waits for those leases to expire.

The default database is `.data/photosift.sqlite3`. Set `PHOTOSIFT_DB` to the **same absolute
local-disk filename in both terminals** to change it. Keep the whole app-data directory
outside every library root and export destination. Never place the WAL database on SMB/NFS.
Mount source roots read-only at the OS/share level on both Zima and the future laptop agent.
OS-managed access timestamps may change on writable mounts; use RO/noatime where supported.

The default hashing cap is **32 MiB/s per worker process**, configurable with
`--hash-mib-per-second 16`, for example. This is an IO limit, not measured throughput.
Hashing uses 1 MiB blocks and refreshes scan leases during large reads. Interrupted scans
restart their traversal and reuse completed unchanged file hashes; an interrupted individual
file is hashed again. Verify scans deliberately rehash files.

Linux equivalents use `.venv/bin/python` instead of `.venv\Scripts\python`.
Actual Zima deployment, shares, hardware and model integration have not been verified.

## Register a root and inventory it

This example asks for a real source path; it does not assume where your library lives.

```powershell
$api = 'http://127.0.0.1:8000/api'
$sourcePath = Read-Host 'Existing read-only source folder'
$library = Invoke-RestMethod "$api/roots" -Method Post -ContentType 'application/json' -Body (@{path=$sourcePath; name='My library'} | ConvertTo-Json)
$scan = Invoke-RestMethod "$api/roots/$($library.id)/scans" -Method Post -ContentType 'application/json' -Body '{}'
Invoke-RestMethod "$api/scans/$($scan.id)"
Invoke-RestMethod "$api/photos?root=$($library.id)&limit=24"
```

Use `{"verify":true}` for a full rehash, or `{"folder":"relative/subfolder"}` for a frozen
subtree. Folder parameters use `/`, never absolute paths or `..`. Overlapping roots,
app data, export destinations and source symlinks/reparse points are rejected. File
extensions indicate preparation eligibility only; Phase 1 does not decode images.

Exact SHA-256 copies share one photo/decision/job and retain every path. Unsupported bytes
are inventoried and hashed but do not become analyzed photos. Unreadable entries remain
explicit. Missing reconciliation occurs only after a complete, reachable traversal; partial
and disconnected scans never mark unseen files missing. Changed-path content versions remain
in history; superseded contents without a current path leave working lists.

## Review API

| Operation | Endpoint |
|---|---|
| Root/folder inventory | `GET /api/roots`, `/api/folders?root=`, `/api/library/stats`, `/api/library/files?state=` |
| Scan | `POST /api/roots/{root}/scans`, `GET /api/scans/{id}` |
| Group cards/review rows | `GET /api/groups`, `/api/groups/{id}/review` (stratified sample, borderline, protected) |
| Paged grid/detail | `GET /api/photos`, `/api/photos/{sha256}` |
| Frozen selection | `POST /api/selections` |
| Durable bulk | `POST /api/bulk`, `GET /api/jobs/{id}`, `/api/jobs/{id}/items?state=conflict` |
| Decision/group/event correction | `PATCH /api/photos/{sha256}` |
| Audit/undo | `GET /api/history`, `POST /api/history/{actionId}/undo` |
| Events | `GET/POST /api/events`, `GET/PATCH /api/events/{id}`, `POST …/merge`, `POST …/split` |
| Exact and possible sets | `GET /api/duplicates` (pages whole sets) |
| Analyze/status | `GET /api/status`, `/api/stages`, `POST /api/analyze/{pause,resume,cancel,retry}` |
| Filename/path search | `GET /api/search?q=` |
| Settings | `GET /api/settings`, `GET/PATCH /api/settings/{section}` |
| Export contracts | `GET/POST /api/export/plans`, `GET …/plans/{id}`, `POST …/plans/{id}/approve`, `GET …/runs`, `GET …/runs/{id}`, `GET …/runs/{id}/manifest?format=json` (or `csv`) |

Photo fields match `web/src/domain.ts`; `reviewVersion` and `blurRequired` are additional
server guards. No thumbnail/preview URL is fabricated before preparation. Missing face,
importance and sensitivity checks remain Unknown. Sensitive IDs always require reveal;
unresolved sensitivity also requires reveal in this conservative foundation.
Events use empty date/place strings when metadata is unknown, rather than fabricated dates.

Photo filters: `root`, root-relative `folder`, `group`, `event`, `band`, `origin`, `format`,
`q`, `protectedOnly`, `faces`, `duplicate`, `decisions`, `reviewed`, `sort`, `by`, `page`,
`limit` (max 500), `revision`. Omitted root means an explicit all-library query. Clients
must keep their selected root in every request; no reset/widening is performed by the API.
Boolean query flags accept `1`/`0` or `true`/`false`. The default working view is undecided;
`decisions=all` or `reviewed=1` includes reviewed items. Sort is `default|margin|date|size|name`.
`by=folder|month|event` sorts/groups **before paging**. Headers contain full-query counts
and page offsets even when a group crosses pages. Multiple paths use one deterministic
representative inside the requested scope. Band counts always describe undecided items.

### Freeze → confirm → process

1. Load `/api/photos` with the desired scope/filters. Retain `revision` and
   `queryFingerprint` from that response. Paging does not change the fingerprint;
   changing scope, filters, sort or grouping does.
2. Post to `/api/selections` with `query`, `queryRevision`, `queryFingerprint`, optional
   `exclusions`, optional explicit `contentIds`, and `includeProtected` (default false).
   The server freezes all matching membership and review/policy versions, and returns
   `selectionId`, exact `count`, excluded/protected counts and its frozen fingerprint.
   A filter mismatch, stale revision or out-of-scope ID returns 409.
3. Display that exact count before posting `{"selectionId":"…","decision":"clutter"}`
   to `/api/bulk`. Repeating the same request returns the same durable job. Reusing the
   selection for a different decision is a conflict; freeze another selection instead.
4. The worker applies bounded transactions (100 items by default). Poll `/api/jobs/{id}`.
   Each changed photo is audited; newer review edits, source changes and policy changes
   are conflicts, never overwritten. The response includes at most 100 conflict previews;
   `/items` pages the complete outcomes. Closing the browser or restarting the worker is safe.

Single corrections require `expectedVersion` from `reviewVersion`, plus exactly one of
`decision`, `groupId`, or `removeFromEvent:true`. A protected-photo Clutter decision also
requires `overrideProtected:true`. Manual group overrides take precedence over AI grouping.
Manual Keep, faces, documents, protected folders, reliable age rules, positive importance
and sensitivity exclude bulk membership by default. Importance and sensitivity remain their
own axes; their protection may have an empty `protected.reasons` list because the mock's
reason enum has no importance/sensitivity value. No Clutter suggestion is published for
personal/documents/protected/unresolved-check content. A manual bulk choice is a user decision.

Undo skips items changed since the original action and reports conflicts. Up to 100 changes
finish in the request; larger reversals return HTTP 202 with a durable undo job, polled through
the same jobs endpoint. Undo and bulk restart from committed item outcomes. Event rename/
confirmation edits are audited; event merge reversal is not automatic when the former event
has been archived (affected undo items report conflicts).

Protection age defaults to unset, folders to none, models to unconfigured. Set folder rules
explicitly through `PATCH /api/settings/protection` with `expectedVersion` and
`value:{protectedFolders:[{root,folder}],ageBefore:null}`. Appearance/setup settings are
validated and versioned. Categories/thresholds/export-defaults can be saved as preferences;
they do not activate an inference policy or exporter. Model changes require later adapters.

## Export foundation boundary

Plans freeze reviewed content IDs, one available source instance/hash/size, output mapping,
decision/group/evidence snapshots, exclusions, measured free space and collision renaming
`(2)`. Sources, app data and outputs cannot overlap. Unknown sensitivity is excluded by
default; explicitly included sensitive/unknown content goes only to `Private/Sensitive/`.
Review-later and Clutter are opt-in and outside `Organized/`. Exact copies produce one entry.

Approval requires the exact displayed plan `revision` and `fingerprint`. A stale decision or
source snapshot blocks approval. Frozen rows never change when later reviews change.
Approval persists a run and pending operations journal, but reports `state: unavailable`,
`executorAvailable:false`, `copied:0`, `verified:0`. Manifests clearly label pending entries;
they are **not verification certificates**. No output directories/files are created by Phase 1.
Copy/pause/resume/rollback execution returns 501 until the later checksum-verified exporter
phase. There is no hardlink, source-write or pretend-copy code path.

## Worker foundation boundary

Stage jobs persist owner/token/attempt/expiry and full requested fingerprint. A configured
`PHOTOSIFT_AGENT_TOKEN` is required for `/api/agent/*` bearer access; ordinary app review
routes assume the single-owner trusted local deployment. The default listener is loopback.
Do not expose it to an untrusted network without an authenticated proxy/private boundary.

Claims return root IDs and relative paths, not executable server filesystem paths. Expired
claims can be reclaimed; stale tokens cannot renew/submit. Scanner-created preparation jobs
are explicitly `prepare-contract-v1:unconfigured` and cannot be claimed as a working model.
Result storage and accepted-result replay primitives exist, but actual result acceptance
returns 501 until a stage-specific validated adapter exists. No generic arbitrary JSON is
accepted as an AI result. The D3 `/v1/systemone` schema is unknown and remains unimplemented.
Semantic search, media generation, AI grouping/triage, laptop preparation, model evaluation
and verified copying are later phases. No OCR or fallback model/protocol is present.

## Verify

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\ruff check server worker tests
.\.venv\Scripts\ruff format --check server worker tests
```

Tests cover source bytes/names/sizes/modes/mtimes and read-only open flags, incremental/verify
hashing, exact identity, history/replacements, reachability/missing reconciliation, Windows
junction refusal, boundaries, migrations/concurrent startup, filters/counts/global grouping,
selection conflicts/idempotency/restart, decisions/undo, event edits, duplicate sets, settings,
leases, private export mapping/collisions/approval/manifest, and 50,000 generated metadata
rows through selection/bulk/restart/undo. Symbolic-link creation may be skipped where the OS
does not grant that privilege; the Windows junction test still exercises reparse handling.
These are correctness tests, not real-library/GPU/SMB/Zima performance measurements.

Final Phase 1 verification on Windows/Python 3.12.10: **31 passed, 1 skipped**. The skip
is symbolic-link creation privilege; the junction test passes. Ruff checks/format pass.
The installed Starlette emits one deprecation warning for its supported httpx test client.

Migrations are append-only SQL files in `server/migrations/`. An applied file's hash is
checked at startup; changing one fails startup. API and worker serialize first-time migrations.
Add a new migration for every schema change. Back up the database using SQLite's backup API
or a coordinated stopped-service backup; do not copy only the database while ignoring its WAL.
