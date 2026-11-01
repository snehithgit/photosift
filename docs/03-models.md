# 03 · Models

Planned model roles, not implemented adapters. Two embedding backends are supported by the design, but **only one is primary** at a time. [02 Pipeline](02-pipeline.md) is the canonical processing/recovery contract.

## Hard rules

- Never compare or mix vectors from different model spaces.
- Do not run both embedding models over every image by default.
- Never silently change a model, checkpoint or protocol.
- D3 Nano and DINOv3 are different models; never confuse their names or capabilities.
- Every index stores the exact model/checkpoint, preprocessing config and embedding version.

## A. SigLIP 2 — semantic embeddings

Used for:
- image embeddings;
- matching images against category descriptions (zero-shot);
- text-to-image search;
- image-to-image similarity;
- content grouping and similar-scene suggestions;
- features combined with timestamps/GPS for album suggestions.

**Size:** benchmark on the laptop's GPU before choosing. Candidates: base/384,
large/384, so400m/384. Pick on comparison-mode metrics plus throughput and VRAM.

## B. Apple MobileCLIP2 — efficient alternative

Evaluate for:
- efficient image embeddings and text/image matching;
- similar-photo retrieval;
- a lower-resource processing profile;
- **CPU processing of small incremental imports on Zima.**

It is not tied to Apple hardware; it runs in a normal Python/PyTorch runtime.

## Embedding backend design

```
EmbeddingBackend
  id, checkpoint, revision, dim
  preprocess_config   resolution, resize/crop, interpolation, mean/std
  embed_images(batch) → L2-normalised vectors
  embed_texts(list)   → L2-normalised vectors
  ├── SigLIP2Backend
  └── MobileCLIP2Backend
```

### Index registry
Each set of vectors belongs to one `embedding_index` record:

| Field | Purpose |
|-------|---------|
| backend | `siglip2` / `mobileclip2` |
| checkpoint + revision | exact weights used |
| preprocess_config | full preprocessing, so results can be reproduced |
| dim | vector size |
| embedding_version | bumped on any change to the above |
| role | `primary`, `eval` or `provisional` |
| status | building / ready / retired |

- SQLite vector BLOB rows carry an index ID and validated dimension/dtype; separate in-memory search matrices belong to each index. No pgvector/Postgres dependency.
- Fingerprint precision, normalization and runtime as well as checkpoint/preprocessing. Measure total RAM including runtime copies and search overhead.
- Anything derived from vectors is tied to its index: prompt vectors, text queries,
  the Keep/Clutter classifier, clusters and similarity results.
- Code-level guard: a similarity call between two different indexes is an error.

### Primary selection
- The primary index is named explicitly in config.
- Switching: create a new index → backfill → flip the setting. The old index is kept
  until deleted by hand. There is no automatic switching.

### Optional Zima CPU incremental imports (later, after measurement)
- **If MobileCLIP2 is primary:** Zima may embed small new imports straight into the primary index.
- **If SigLIP 2 is primary:** Zima's MobileCLIP2 vectors go into a separate
  `provisional` index, used only for "recent imports" search. Those assets stay
  "awaiting primary embedding" until the laptop backfills them.

### Comparison mode
- One fixed, labelled sample (500–1,000 images), the same for both backends.
- Labels: category, Keep/Clutter, known duplicate pairs, 30–50 text queries with
  expected matches.
- Metrics per backend:
  - zero-shot category macro-F1;
  - category metrics and Keep/Clutter precision/recall of a linear probe, with false-Clutter rate on personal/protected examples;
  - held-out events/near-duplicate groups, preventing related shots from leaking across training/test splits;
  - duplicate recall@k;
  - text-search recall@10;
  - throughput (img/s) and VRAM on the laptop, plus img/s on Zima's CPU.
- Results are stored per run. Comparison indexes have role `eval` and are only
  promoted to primary by explicit choice.

## C. D3 Nano — vision assessment

Keep the existing local model:

`hf.co/ggml-org/vllm-d3-nano-GGUF:Q8_0`

Used for:
- ambiguous content;
- images needing closer inspection;
- content type versus potential importance;
- people/text presence;
- extra assessment of uncertain grouping candidates.

### Protocol
- It uses Ollama's documented **decision API at `/v1/systemone`**
  ([reference](https://docs.ollama.com/api/systemone)).
- Do **not** assume this model supports Ollama `/api/chat`.
- Other vision models can be added later, each with its own explicitly configured
  compatible protocol.

```
VisionAssessor
  config: { model, protocol, endpoint }    ← set together, never inferred
  ├── SystemOneAdapter   (D3 Nano)
  └── <other adapter>    (only if another model is explicitly configured)
```

- **Agent capability check:** verify the configured runtime/model/protocol contract before claiming assessment work. If unavailable, that stage waits visibly; Zima browsing/review still starts and works. No fallback model.
- D3 adapter fields must match an actual tested request/response fixture. Unsupported sensitivity/explanation fields stay Unknown; do not invent them from category scores.
- GPU model residency is bounded; schedule/unload stages if concurrent encoders and D3 exceed measured laptop memory.
- **Provenance per result:** model ID, protocol, protocol version, prompt/decision version.
