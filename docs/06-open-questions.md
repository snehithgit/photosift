# 06 · Open questions

The canonical processing contract is now [02 Pipeline](02-pipeline.md). Separate established context from measurements still needed.

## Established direction

- Fresh project; Zima owns SQLite WAL, app state and export; laptop agent pulls leased work.
- GPU laptop context: RTX 5060 Laptop GPU, 8 GB VRAM; verify available memory during benchmark.
- Existing local D3 Nano was served by laptop Ollama using `/v1/systemone`. Test a fresh adapter fixture; do not assume `/api/chat` or unsupported output fields.
- Export is independent copy-only. Hardlinks to originals are excluded.
- First design targets one user; OCR is outside initial scope.

## Measurements and deployment checks

## Models
- Updated owner direction (1 November 2026): choose a loading default now. Selected
  `google/siglip2-so400m-patch16-384` on the laptop; see [runtime guide](../laptop/README.md).
  Labelled PhotoSift evaluation, backfill and explicit primary-index promotion remain pending.
- A docs-wide check found no SystemOne payload fixture; only its endpoint/model tag and an
  explicit unknown-schema requirement. Do not infer a standard Ollama or llama.cpp protocol.
- [ ] **`/v1/systemone` decision protocol:** the exact request/response schema and
      decision fields. The D3 Nano adapter must match it exactly.
- [ ] Available laptop GPU memory and measured throughput for each exact checkpoint/precision/batch size.
- [ ] Which MobileCLIP2 variants are realistic on Zima's CPU for incremental imports?
- [ ] Who builds the labelled comparison sample, and which categories does it need?

## Hardware & library
- [ ] Zima model / CPU / RAM; is Docker already running there?
- [ ] Library size (number of photos, total GB) and formats (HEIC, RAW, video share).
- [ ] How is the library shared to the laptop: SMB or NFS? Can the share be read-only?
- [ ] Is Immich already running on Zima, and does it use an External Library today?

## Product
- [ ] Final category list for the first version.
- [ ] Default protection rules (age threshold, people, GPS).
- [ ] Export destination capacity, ownership, actual folder mapping and private/gallery indexing boundaries.
- [ ] Single user only, or does anyone else in the household review too?
