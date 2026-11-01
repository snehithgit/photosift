# PhotoSift · Handoff for the next agent

Updated 2026-11-01. Read this first, then the docs it points to.

## 1. What PhotoSift is

A local, self-hosted **photo-decluttering workspace** for a library of ~50,000+ personal photos.
It groups photos (screenshots, memes/forwards, documents, duplicates, bad shots, events…),
lets the owner review **groups in bulk** (sample + borderline + protected rows), and exports
the approved result as **independent verified copies** for Immich. It is **not a gallery**.

- **Zima (ZimaOS server)** owns all state: FastAPI + SQLite WAL, background worker, UI, export.
- **Windows laptop (RTX 5060, 8 GB)** is a replaceable **pull agent** for heavy work: decode, EXIF,
  thumbnails, pHash, YuNet faces, ONE primary embedding model (SigLIP 2 **or** MobileCLIP2),
  and D3 Nano (via local Ollama) only for unclear/sensitive photos.
- Originals are **read-only everywhere**. Nothing ever deletes, moves, renames or edits a source file.

## 2. Where things are

```
C:\Users\snehith\Documents\code\photosift\
  README.md              index of docs
  HANDOFF.md             this file
  docs\
    02-pipeline.md       CANONICAL processing contract (stages, leases, recovery, build order)
    01-architecture.md   Zima vs laptop, SQLite, contracts, memory budget
    03-models.md         model roles and rules (one primary index, no silent switching)
    04-review-and-export.md  review rules, protection policy, copy-only export
    09-approach.md       short summary of the approach
    10-frontend.md       full frontend spec (screens, selection model, states, mock data contract)
    06-open-questions.md things still unknown — do not guess these
    07-model-shortlist.md, 08-prior-art.md  research notes (not guarantees)
  web\                   the finished MOCK web app (frontend only, fake data)
```

The folder is now a git repository. The original documentation and finished mock were committed
first as `9e088c6`. `origin` is `https://github.com/snehithgit/photosift.git`.
The Python backend is in `server/`, the Zima executor in `worker/`, the laptop executor in `agent/`, and pytest tests in `tests/`.
See [server/README.md](server/README.md) for commands, API contracts and explicit phase boundaries.

## 3. The mock web app (`web/`) — finished and verified

Runs fully in the browser on a seeded fake dataset of 50,000 photos. No backend.

**Stack:** React 19 · TypeScript 5.9 · Vite 7 · TanStack Router / Query / Virtual · Zustand · Radix Dialog ·
lucide-react · Tailwind 4 import + hand-written CSS with theme tokens. Package manager: pnpm 11.25.

**Commands** (Node 26 LTS is installed; pnpm is run through npx):
```
cd C:\Users\snehith\Documents\code\photosift\web
npx pnpm@11.25.0 install
npx pnpm@11.25.0 dev          # http://localhost:3000
npx pnpm@11.25.0 typecheck
npx pnpm@11.25.0 test         # 9 vitest tests
npx pnpm@11.25.0 build
```

**Source map**
| File | Holds |
|---|---|
| `src/domain.ts` | Types (`Photo`, `Evidence`, `GroupStats`, `MockEvent`…), the seeded dataset, realistic sizes/evidence, events and trips, stats and filters |
| `src/store.ts` | Zustand store: decisions, bulk actions with conflicts, version-aware undo, event edits, history; saves a diff to localStorage |
| `src/analyze.ts` | Simulated pipeline stages (pause / cancel / retry / laptop offline) |
| `src/export-store.ts` | Frozen export plans and runs, saved and addressable by URL |
| `src/theme.ts` | Theme (dark / light / system), density, thumbnail size, auto-advance, setup status |
| `src/screens.tsx` | Review overview, group page, full grid (virtualised, group-by, arrow keys), photo detail |
| `src/screens-workflow.tsx` | Analyze, Library, Duplicates, Events, Documents, Search, Teach, Export, History, Settings, Setup |
| `src/components.tsx` | `PhotoTile`, `ActionBar`, `BandChip`, `EvidenceRow`, `EmptyState` |
| `src/router.tsx` | Routes, app shell, status popover, activity drawer, shortcuts, toasts |
| `src/styles.css` | Theme tokens (`--bg`, `--surface*`, `--text*`, `--line*`, `--blue*`, `--red-strong`…); no hard-coded colours |

**Routes:** `/review`, `/review/group/:id`, `/review/group/:id/all`, `/photo/:id`, `/library`, `/analyze`,
`/duplicates`, `/events`, `/events/:id`, `/documents`, `/search`, `/teach`, `/export`, `/export/plan/:id`,
`/export/run/:id`, `/history`, `/settings/:section`, `/setup`. All filters live in the URL.

**Verified behaviour (keep it working):** counts always add up (all = undecided + decided; High+Medium+Low = undecided);
bulk actions act on *all matching* minus protected minus exclusions and the action bar states the exact count first;
conflicts are never overwritten; undo is version-aware and survives reload (last 5 steps); Back/Forward restore filters;
detail opens at the top and Esc returns to the same scroll spot; light and dark themes are clean; phone width has no
horizontal scroll and hides bulk actions and export approval.

## 4. Product decisions already made (do not reverse)

1. **Not a gallery.** Library shows no photo grid (folder stats, "What's here" per group, problem lists only).
   Reviewed photos leave every working view; a "Show reviewed" switch is the only way back.
2. **Clutter = "set aside", never delete.** The word *delete* never appears for photos.
3. **Protection before suggestions:** faces, documents, sensitive, protected folders, age rule. Protected photos are
   excluded from bulk actions by default and never appear in Sample/Borderline rows.
4. **Personal photos and documents are never suggested as clutter.** High confidence in a personal group means
   "confidently personal".
5. **Sensitive IDs** stay blurred everywhere until revealed and export only to `Private/Sensitive/`.
6. **Exact copies** = one content (SHA-256) with several paths. Bursts and possible matches are different files and
   always stay within one group. Possible matches are unticked by default.
7. **Copy-only export** with a frozen plan, plan-bound approval, collision renaming "(2)", checksum verification,
   journal, manifest, rollback of only verified outputs. **No hardlinks.**
8. **One primary embedding index.** Never mix vectors from different models. No silent model or protocol switching.
9. **D3 Nano** is reached through the laptop's Ollama at `/v1/systemone`. Its request/response schema is **unknown** —
   ask the owner; do not assume `/api/chat` or invent fields.
10. **No OCR** in the first version.

## 5. Build phases (follow `docs/02-pipeline.md`)

Build in phases; each phase ends with tests and a runnable result. Do not start a phase before the previous one passes.

**Phase 1 — Foundation (implemented; see section 7)**
- Create `server/` (Python 3.12, FastAPI, Pydantic, SQLite WAL on local disk) and `worker/`.
- Schema (additive migrations, never edit an applied one): library roots, scan runs, file instances (path, size,
  mtime, state), contents (SHA-256, size), cached media, stage jobs with leases (owner, token, expiry, attempt),
  stage results keyed by content + stage + full model/prep fingerprint, decisions with versions, audit log,
  group assignments (AI vs manual), events and event edits, export plans / runs / operations journal.
- Read-only inventory scanner: walk a root without following symlinks, stream SHA-256, incremental by size+mtime,
  mark files missing only after a complete reachable scan, keep history, record unsupported/unreadable files.
- REST API shaped like the mock's needs (the types in `web/src/domain.ts` are the contract): group stats,
  paged photo lists with the same filters (`band`, `origin`, `format`, `q`, `protectedOnly`, `faces`, `duplicate`,
  `decisions=all`, `reviewed`, `sort`, `by`), photo detail, frozen "select all matching" → bulk job → result with
  conflicts, single decisions, group moves, undo, history, events + rename/confirm/merge/split, duplicates,
  library tree/stats, analyze stage status, export plans/runs/manifest, settings.
- Server-side grouping for the grid (the mock only groups within one 180-photo page).

**Phase 2 — Connect the frontend**

Frontend integration is **deferred at the owner's request**. An attempted integration was
fully reverted; `web/` is exactly the finished original mock. Do not redesign or reconnect it
while continuing backend work. Current backend progress and remaining gates are in section 8.
- Replace the Zustand mock data with TanStack Query calls to the API, keeping a `VITE_MOCK=1` mode that still runs
  on the fake dataset. Keep every verified behaviour from section 3.

**Later phases** (in `docs/02-pipeline.md`): laptop pull agent and preparation (EXIF, thumbnails, pHash, YuNet),
embedding comparison and primary index, D3 Nano adapter, protection gate and triage, events, verified export.

## 6. Rules for working

- Never write to, move, rename or delete anything under a library root. Tests must prove the source is untouched.
- Keep SQLite write transactions short; never hold one during file IO, network waits or GPU work.
- No performance numbers without measuring on the real hardware. Report what was measured and how.
- Unknowns are listed in `docs/06-open-questions.md` (D3 Nano schema, Zima hardware, library size and share,
  Immich setup). Ask the owner instead of guessing.
- Report honestly: say what was tested, what passed, and what was not verified.

## 7. Phase 1 implementation notes

Historical Phase 1 snapshot; section 8 supersedes its implementation-status boundaries.

- Python 3.12/FastAPI/Pydantic, four immutable additive SQLite WAL migrations; concurrent
  API/worker startup is tested. Current installed versions are pinned in `requirements.lock`.
- Read-only throttled SHA-256 inventory, content/path separation, incremental and verify modes,
  file history, unsupported/unreadable/missing states, scan leases/recovery, subtree boundaries,
  symlink/junction refusal and no missing reconciliation on partial/disconnected scans.
- REST endpoints for the requested screens: group stats, filtered/paged photos and globally
  grouped grids, details, frozen/versioned selections, durable idempotent bulk decisions,
  optimistic single corrections, version-aware undo (large reversals are durable jobs), audit,
  editable events, paged duplicate sets, library stats/tree/problems, analyze/status and settings.
- Select-all requires `queryRevision` **and** `queryFingerprint` from the loaded photo query;
  it returns a frozen exact count. Bulk/undo outcomes and conflicts survive process restart.
  Clients use `reviewVersion` for single corrections; `decisionVersion` is still returned.
- Export **contracts**: frozen plans, private-sensitive mapping, exclusions, collision renaming,
  measured free space, plan-bound approval, persisted unavailable runs, pending operations and
  explicitly unverified manifests. The real verified copier/rollback is still the later exporter
  phase, **not** implemented. No copy success is simulated and no output files are written.
- Stage dispatch foundation has authenticated claims/renewals and stale-lease rejection.
  Actual result acceptance returns 501 until stage-specific validated adapters are built.
  Scanner-created preparation jobs remain explicitly unconfigured. D3's schema stays unknown;
  no OCR, inference, fallback model, semantic search or real media generation was added.
- Newly inventoried photos have zero/unknown metadata, no fake thumbnails, and Awaiting analysis.
  Protection age is unset and folder rules empty until explicitly configured. Positive sensitivity
  or importance holds bulk membership independently of the mock's limited protection-reason enum.
- Tests include source bytes/tree/mtime/mode invariance, Windows reparse refusal, workflow
  conflicts/recovery, real API/worker process smoke and a 50,000-row **metadata correctness**
  exercise. Symlink creation is skipped on this Windows account without that privilege;
  the separate junction safety test passes. Final Phase 1 run: **31 passed, 1 skipped**;
  Ruff checks and formatting pass. One Starlette/httpx test-client deprecation warning remains.
- No real library/share, Zima deployment, GPU inference, D3 schema or actual copy IO was tested.
  No real-hardware performance is claimed. Frontend source was not changed.

## 8. Backend continuation (1 November 2026)

The owner requested remaining backend work while keeping the restored frontend unchanged.
They explicitly said to **keep primary embedding selection pending**. They asked us to check
the docs for D3; a full docs search confirmed that the `/v1/systemone` payload schema is still
absent and explicitly marked unknown. No engine/protocol/model was silently substituted.

Implemented and tested beyond Phase 1:

- Laptop preparation pull agent (`python -m agent`): mapped read-only roots, source version/hash
  verification, oriented Pillow decode, EXIF/camera/GPS, bounded metadata-free WebP cache,
  pHash/dHash and quality signals. Optional checksum-pinned YuNet; missing/failed detector
  remains Unknown. No OCR. See [agent guide](agent/README.md).
- Full preparation fingerprint registration/activation, authenticated claims/renewals/failures,
  source-instance-bound typed results, idempotent replay, request-size bounds, atomic cache
  publication and matching cache/metadata reactivation. Accepted per-image work survives restart.
- Isolated embedding index registry, typed normalized vectors, explicit queueing of prepared
  contents, frozen comparison labels and event-group splits, prompt packs, held-out category
  metrics and explicit backfill-gated primary promotion. **Native encoder execution and real
  comparison/throughput remain pending**; test vectors are arithmetic fixtures, not model results.
- Conservative prompt scoring with explicit report/threshold provenance and bounded batches,
  independent content grouping and user decisions, Unknown protection holds, no personal/document
  Clutter suggestions; unresolved checks queue an explicitly unconfigured D3 assessment.
- Same-primary-index/time/pHash neighbor suggestions are unticked and stay within an effective
  group. Manual group moves remove stale duplicate membership. Time-only event suggestions
  preserve existing event edits and do not infer places/trips. Date-only/unknown time is excluded.
- **Real verified export executor** in `worker/exporter.py`: source/output checksums, independent
  copies, fsync, no-overwrite atomic publication, journaled ownership, publication-crash recovery,
  pause/resume/retry, atomic manifests, and safe rollback preserving changed/unowned outputs.
  Approval now queues actual copies; run the Zima worker to execute them. Old unavailable runs
  need explicit resume. Original files are never changed; interrupted temporary files are
  preserved rather than recursively cleaned up. Windows tested; Linux/SMB still unverified.
- Migrations `005`–`008` extend the original four without editing them. Dependencies/commands
  are in the backend/agent guides. The backend additions retained from the reverted frontend
  attempt (document filters, related photos, exact-set pagination, scope-correct events and
  serving a built UI) were regression tested; `web/` itself remains untouched.

Still pending: actual primary checkpoint/runtime comparison and selection; native image/text
encoder execution and new semantic-text query encoding; the real D3 SystemOne fixture/adapter;
real YuNet weights/accuracy, GPU/VRAM/throughput, Zima/share/Linux deployment checks. Optional
personal-classifier training and Immich API integration are later scope. Do not call these done.

Verification for this continuation: **59 passed, 1 skipped** on Windows/Python 3.12.10;
Ruff checks/formatting and `pip check` pass. The skip is Windows symbolic-link creation
privilege; junction refusal is tested. The live smoke now runs API, Zima worker and laptop
preparation agent as separate real HTTP processes. All copy/preparation tests used temporary
fixture libraries. One Starlette/httpx test-client deprecation warning remains. `web/` has
no changes compared with the restored committed frontend.
