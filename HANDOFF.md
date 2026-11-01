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

The folder is **not a git repository yet**. Run `git init` and make a first commit before changing anything.

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

## 5. Next task: the backend (follow `docs/02-pipeline.md`)

Build in phases; each phase ends with tests and a runnable result. Do not start a phase before the previous one passes.

**Phase 1 — Foundation (start here)**
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
