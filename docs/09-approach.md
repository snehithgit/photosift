# 09 · Approach (fresh project)

Status: design, no application code yet. Updated 1 November 2026. PhotoSift is a fresh project, not an extension of the previous repository. This summary and the canonical [02 Pipeline](02-pipeline.md) replace the early conflicting drafts.

## Idea

Automatically group a huge local photo library so the owner inspects samples and exceptions, then makes bulk Keep / Clutter / Review later decisions. Zima remembers everything; the laptop performs heavy decoding and AI work. Originals remain read-only; export creates independent verified copies.

## Goals

1. Separate likely clutter from useful/personal content through explainable group suggestions.
2. Suggest scenes/events and eventually trips without excluding shared personal photos.
3. Gather document-like images into a useful section; OCR is outside initial scope.
4. Support same-index similar-photo and text search without a second full embedding pass.

## Pipeline

```text
ZIMA: choose scope → inventory + SHA-256 → durable jobs
  ↓ laptop pulls a leased job
LAPTOP: verify original RO → decode/EXIF → previews/quality/pHash/YuNet
  → ONE primary encoder: SigLIP 2 OR MobileCLIP2
  → D3 Nano when evidence/protection checks need assessment
  ↓ per-image results acknowledged by Zima
ZIMA: evidence + protection gate → content/triage groups → events/similarity
  → representative samples + borderline items → frozen bulk decisions
  → frozen export plan → explicit plan-bound approval → verified copies
  → Immich indexes only approved non-sensitive Organized output
```

D3 work is conditional, not assumed to be 10% of images. Model size, thresholds, precision, batching and worker limits come from measurements. MobileCLIP2 comparison/provisional indexes never mix with SigLIP vectors.

## Independent labels

Estimated origin (including Unknown), content/subcategory, importance, sensitivity, triage suggestion and user decision are separate. Missing EXIF or sharing-app origin alone does not mean junk. A face is protection evidence, not proof of sentimental value; no detected face is not proof that a photo is unimportant. Important family photos stay Personal, not Sensitive.

Content groups include Personal, Documents, Food, Memes/graphics, Screenshots, Other and Unclassified. Cards expose All / Undecided / Decided. Changing content/group is a real manual correction, not a contradictory display-only override.

## Stack and boundaries

| Part | Direction |
|---|---|
| Zima | FastAPI, SQLite WAL on local disk, durable scheduler/executor, React/TypeScript/Vite |
| Laptop | Pull agent, read-only root mappings, Python preparation and embedding adapters, local Ollama for D3 |
| Models | One primary SigLIP 2/MobileCLIP2 index; YuNet face signal; explicit D3 decision adapter |
| Vector search | Versioned SQLite BLOBs and bounded in-memory per-index search; ANN only if measured need |
| Export | Copy-only with journal, manifests, identity/hash verification and safe rollback; no hardlinks to originals |
| UI | Library / Analyze / Sort & Review / Export; scope persistence, accessible preview and sticky bulk actions |

No Postgres, Redis, OCR, large theme clustering or Immich API integration is necessary for the first cleanup path. Heavy decoding is not duplicated on Zima during the initial laptop-assisted batch.

## Build order

1. Foundation: roots, SHA-256, SQLite, leases, protection policy/Unknown states and safe selection/approval contracts.
2. Laptop preparation: metadata/cache/quality/pHash/YuNet, browsable grid and offline recovery.
3. Compare embedding backends and select a primary; store provenance and candidate scores.
4. D3 adapter, evidence reconciliation and guarded triage.
5. Efficient group-level review, corrections, exclusions/history and large generated-data tests.
6. Similarity/events/search, then verified export.
7. Optional learning, complex albums/trips and integrations after the core is measured.

Protection and scope ownership precede bulk Clutter suggestions. No phase claims perfect detection or a guaranteed speed. Full input/output/recovery details are in [02](02-pipeline.md), model-index rules in [03](03-models.md), and review/export behavior in [04](04-review-and-export.md).
