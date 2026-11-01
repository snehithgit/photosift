# 01 · Architecture

Status: fresh-project design. Canonical flow: [02 Pipeline](02-pipeline.md). This revision replaces the earlier Postgres/push-service drafts.

## Zima owns durable state

- Python 3.12 / FastAPI, React + TypeScript + Vite.
- SQLite WAL on local disk: file instances, content identity, stage jobs/leases, model registry, results, reviews, audit, group memberships and export journal.
- Separate local background executor for scan/hash, scoring/grouping, long bulk actions and verified export.
- Read-only source mount; independent writable app data and export locations.
- Versioned vector BLOBs associated with an index ID; search over a bounded in-memory matrix initially. Evaluate an ANN index only if measured latency requires it.
- Cached thumbnails/previews returned by the laptop, so existing images remain browsable offline.

## Laptop agent executes compatible jobs

The Windows agent pulls leased jobs from Zima and posts per-image results. It downloads each leased original from Zima and checks its hash, or reads it from a configured read-only path when one is mapped. It performs full decoding, EXIF extraction, quality/pHash/YuNet and embedding inference. It invokes local Ollama for D3 assessments. Zima does not need to push background inference HTTP requests into the laptop.

There is no durable laptop database. Temporary buffers/cache are disposable. Accepted results live on Zima; expired claims can be retried. An active stage still needs a bounded batch/checkpoint and resource budget.

Support one primary image/text encoder: SigLIP 2 OR MobileCLIP2. Other indexes remain isolated. On 8 GB VRAM, serialize/unload model stages if measurements require it. CPU execution on Zima is optional and explicit, not a silent fallback.

## Contracts

- Content identity: SHA-256, separate from root/path instances.
- Stage identity: content hash + stage + complete model/preparation/scoring fingerprint.
- Claim identity: job + attempt + owner/token + expiry; reject stale/duplicate conflicting submissions.
- UI identity: URL scope + loaded query revision + detail ID; selection cannot use a previous page after filters change.
- Export authority: exact plan ID/revision/state; independent verified copies, no hardlinked originals.
- Local agent access uses an authenticated configured worker identity; transport is LAN-only or explicitly secured private networking. Do not expose database/model ports publicly.

Keep SQLite transactions short around claims/results; never hold a write transaction during image IO, network waits or GPU work. Long operations belong to durable jobs. See the canonical pipeline for bulk conflict/recovery semantics.

## Memory is a measured budget

Raw vector bytes = content count × dimension × bytes per component. For 100,000 vectors of dimension 1,152: approximately 439 MiB float32 or 220 MiB float16, before IDs, runtime copies, indexes, model weights and additional embedding spaces. Search may need a different compute precision than stored vectors. Record both; do not infer total app RAM from BLOB size.

## Planned package responsibilities

- `server/`: API, validated contracts and durable data access.
- `worker/`: Zima scheduler/executor and recovery.
- `ai-service/`: laptop pull agent, preparation and model adapters; not an externally required push API.
- `web/`: Library / Analyze / Sort & Review / Export.
- `config/`: explicit models, mappings, prompt packs and protection/scoring policies.

These directories are a proposed organization, not code already present. No Redis, Postgres or additional framework is required by this first design.
