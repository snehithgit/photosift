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

Done on the owner's request: the frontend now reads and writes only the API, and the mock
dataset is gone (no `VITE_MOCK` mode). `web/src/styles.css` is byte-identical to the finished
design and JSX structure was kept; an earlier attempt that changed the layout was reverted.
Current backend progress and remaining gates are in section 8.

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
They initially said to **keep primary embedding selection pending** (superseded by the
later runtime setup request in section 9). They asked us to check
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

## 9. Laptop inference setup (1 November 2026)

The owner later asked to select an embedding model now and prepare laptop services with
start/stop scripts and URLs for Zima. The selected loading default is pinned
`google/siglip2-so400m-patch16-384` (revision
`dd658faac399427308559e2c3ac1e99cbe43845d`), native Transformers/PyTorch CUDA FP16.
This supersedes the earlier request to leave the loading choice pending. It does not
claim labelled library accuracy or automatically promote a primary backend index.

See [laptop guide](laptop/README.md) for setup, lifecycle scripts, bearer authentication,
model request format, exact fingerprint and LAN URLs. Separate `.venv-laptop` leaves the
backend environment independent. `Start-PhotoSift.ps1` manages the model gateway on 8787
and its owned loopback Ollama on 11435; `Stop-PhotoSift.ps1` stops only owned processes.
The frontend remains unchanged. Zima services and the preparation pull agent are separate.

D3's model is locally installed, but the SystemOne schema is still absent; the gateway
reports it unconfigured and returns 501. No D3 decision protocol was invented. Backend
dispatch to the new embedding gateway, labelled comparison, real-library backfill,
primary promotion and Zima connectivity still require follow-up.

Verification: pinned weights match upstream SHA-256. Actual CUDA matrix execution and
live HTTP inference passed on RTX 5060 Laptop GPU using PyTorch 2.14.1+cu130 / Transformers
5.22.1. Generated pixels plus three text prompts returned finite, L2-normalized 1152-D
vectors with the same validated backend index fingerprint. Full pytest: **64 passed,
1 skipped** (Windows symlink privilege); the latest five laptop tests also pass independently.
Ruff, dependency checks and PowerShell syntax checks pass. Actual repeated start/stop
and restart passed with the same fingerprint; existing shared Ollama was preserved.
Live missing-token 401 and D3-unconfigured 501 checks passed. No library classification accuracy or
sustained performance was measured. These results supersede the earlier statement that
native image/text execution had not been tested.

## 10. Embedding jobs connected

Leased image embedding jobs now run from Zima through the agent to the local gateway; see the
[agent guide](agent/README.md). This supersedes the follow-up above for backend dispatch, and
the launcher now also runs the agent once `agent.local.json` exists. Verified on the real GPU
with 240 generated photos on a scratch database, including a model service killed mid-job. The
owner's library has not been run. Labelled comparison, prompt encoding, promotion and Zima
connectivity remain, so photos stay Unclassified. Full pytest: **91 passed, 1 skipped**.

## 11. From a filled index to groups

Prompt encoding (`--encode-prompts`), Teach labels saved as a frozen sample, the comparison report,
explicit promotion and bounded scoring now work end to end; see the [agent guide](agent/README.md).
Run on the real GPU and through the real screens, on a scratch database with **323 generated
images, none a real photo and none showing a real person**. 200 were labelled through Teach from
their filenames: 92 train, 108 held out, 111 moments, no moment on both sides.

| Held-out result (108 images) | |
|---|---|
| Matched, at family level | 87% (94 of 108); macro F1 0.86 |
| High band (margin at least 0.0114) | 85 of 87 matched |
| Medium band | Empty: no margin on these labels reached 80% |
| Low band | 9 of 21 matched |
| Personal or documents ranked as a clutter type | 2 of 49, none in High |

On the 118 scored images never labelled: High 77 of 82, Low 14 of 36, Other 9 of 23 and Screenshots
11 of 20 matched. All 308 undecided images left Unclassified with evidence, 228 High and 80 Low.
No decision among 15 set beforehand changed, and no source file changed. Every photo stayed
`needs_review`: sensitivity is unknown without D3, so none became a clutter suggestion. These figures
say nothing about real photos, whose people and clutter look different. The owner's library has not
been run. Full pytest: **102 passed, 1 skipped**.

Margins are measured against the best category of another family, and people, events and places
count as one family because Teach has one Personal label. Scoring leaves reviewed photos out,
because bumping their review version would block undoing them; they stay in their group until
reset and scored again. Cut-offs are one pair per run; the per-group sliders under Settings,
Thresholds are not used, and now say so.

Known issue, measured on the scratch run: promotion bumps every photo's review version, so undoing
a decision made before promotion is refused as a conflict. The decision stays as it was; only that
undo is lost. To use this on the live server, restart it and rebuild `web/dist`: both still run the
code from before this work.

## 12. D3 Nano assessment connected

Earlier notes calling the `/v1/systemone` schema unknown are superseded: Ollama documents it at
<https://docs.ollama.com/api/systemone>, and it was confirmed against the installed model on Ollama
0.40.13. The gateway forwards to the owned Ollama, `agent/assess.py` holds the questions, and
`server/assessments.py` holds the cut-offs. `--configure-assessment` activates it. The fingerprint
covers model digest, Ollama version, questions, state and image steps, and migration `010` adds
`photos.people`, which lets D3 stand in for a face check that never ran.

Measured on the real GPU on a scratch database: median 1.4 s per photo, with SigLIP2 and D3 together
using 5.3 GB of 8 GB. Of 24 generated fake IDs, cards and records, 20 scored sensitive, 4 stayed
Unknown and none was cleared; the lowest score was 0.14. None of 34 plain generated images was
flagged. On a read-only pass over the owner's 15 real photos, 6 webcam shots came back as people and
worth keeping, and the screenshots as not sensitive, with one held on importance and one on people.
These images say little about real IDs. Full pytest at that point: 119 passed, 1 skipped.

## 13. Running from Zima and the web app

The image is built by GitHub Actions and runs on ZimaOS, with the GPU laptop pulling work over the
LAN. The agent downloads each original over its lease and checks the hash, so no share is needed.
Teach labels add up across rounds. Settings has a folder picker and Clear database, and a library
is scanned only when the owner clicks Scan now.

Settings, Laptop agent has Turn on models and Encode prompts. They queue a fixed request in
`agent_commands` (migration `011`, `server/commands.py`) that the agent pulls every few seconds, so
Zima still never calls the laptop. A claim now also honours Pause, because configuring a model can
queue work after a pause. `laptop/control.py` is a small Start/Stop window, and
`Add-PhotoSiftShortcut.ps1` puts it on the desktop. Nothing runs at Windows startup.

Tried on a scratch server with the real models: both buttons worked and 6 photos went through every
stage. Start in the window was clicked with services already running. Stop and a start from
stopped were not run through the window, because the laptop was in use. Full pytest: see
`CLAUDE_HANDOFF.md`.

Prepare can also run on Zima (`worker/preparer.py`, Settings, Prepare on Zima). The worker prepares
exactly when the active contract is its own, so the contract alone decides who runs it. Zima's
owner names start with `zima-` and never count as laptop contact. Preparing a photo again now keeps
D3's answers. The base image pins an exact Python, because its version is in the contract.

Laptop speed on the real library: about 115 photos a minute over the network. Known gap: an altered
copy of a reviewed photo arrives as a new photo with no link to the original; only byte-identical
copies are recognised. `suggest-neighbors` has no caller.

Fixed after the first real run on Zima: a GPS value of 0/0 no longer fails the photo; View under
Failures now lists failed jobs (it only opened the scan's unreadable files); Retry and the counts
ignore failures of a replaced contract (migration `012`); Start again after Cancel really restarts;
`/api/stages` skips the per-photo work when it covers the whole library. On that run, 13 of Zima's
15 failures were `.jpg` files of 0 to 122 bytes, not photos.

`stage_results` no longer keeps prepare's base64 thumbnail and preview, which duplicated the cache:
new rows hold a digest, and the worker slims older rows in small batches. Zima's database was
9.28 GB before this. Settings, Libraries, Free disk space runs VACUUM, only while Analyze is paused,
because it holds up every write. `SQLITE_TMPDIR=/data` keeps its temporary copy off the container's
own disk.

Known and not fixed: Prepare's total also counts jobs finished under an earlier contract, so it read
51,726 rather than about 47,833 after moving to Zima. The owner chose to leave it.

## 14. Removed originals and Analyze failures (22 November 2026)

The live Analyze page still showed 14 preparation failures after the owner deleted those originals.
Each error was now file-not-found; the last completed inventory had not reconciled the removals.
A read-only incremental rescan completed through the normal API, with 52,599 seen paths and no
traversal errors. It reconciled 272 missing paths in the existing inventory (not 272 failed
contents; exact copies and unsupported files also have inventory paths).

Stage counts and the job list now require an available supported source in the requested root/folder;
Library's Unreadable list also excludes missing paths with historical failed jobs.
Retry also leaves failed jobs alone when no available copy remains. Missing file records, job errors,
photo decisions and version history are retained. A remaining exact copy keeps the content active;
restoring the file makes its retained failure visible and retryable again. Offline/partial scans
continue to preserve current availability. No migrations, frontend layout or source-file changes.

Verification: 187 passed, 1 skipped (Windows symlink privilege); Ruff and formatting pass. New
regressions cover confirmed removal, history retention, restoration, exact-copy scope boundaries,
and offline/partial scans. GitHub's backend/frontend checks and container smoke/publish passed.
Both Zima services were updated through the existing ZimaOS management UI to the verified image
`ghcr.io/snehithgit/photosift:sha-39636ee`, preserving the volumes and read-only library mount.
The live `/api/stages` reports Prepare done (51,706 / 51,706), `/api/pipeline/jobs?state=failed`
reports zero, and Library Unreadable reports zero. The real Analyze page was refreshed and
visually verified to say **No failures**; laptop D3 assessment resumed after the update.
No source files were changed and no database/history records were deleted. Future external
source removals still need a successful Library **Scan now**; this does not add a file watcher.

## 15. Similar-search paging and category changes (22 November 2026)

The owner asked to select all More-like-this matches and change their category, then pointed
out that the search reported 500 results while displaying only 120 with no page controls.
Search now has Previous/Next controls above and below the grid, URL-backed pages, visible
ranges and persistent selection across pages. The existing closest-500 similarity bound
is explicit; Select all fetches that bounded ranked set, not the whole library. Membership
resets on a changed seed/query/library/review filter; late select-all responses are discarded.

A category picker applies the exact selected IDs and the review versions loaded at selection
through a durable, batched Zima job. Additive migration `013` adds bulk-job `group_id`; no
applied migration was edited. Manual category edits preserve decisions/sensitivity/importance,
remove stale duplicate grouping when the effective category changes, and support one version-aware
Undo. Concurrent edits produce conflicts. Search shows the number of protected selected photos;
category correction does not set Keep/Later/Clutter, and decision actions exclude protected photos
unless the owner opts in. No category change was tested on the owner's real photos.

The live browser check also exposed TanStack's JSON quoting of numeric query strings: Next
generated `page="2"`, which the screens read as invalid. Router search parsing/stringifying
now preserves plain URLSearchParams values (`page=2`, `reviewed=1`); real-router tests cover
this and quoted/numeric text searches. Live preview verified page 2 with all 500 still selected.

Older repeatedly quoted numeric flags are repaired during navigation without decoding free text.

Local verification: 190 pytest passed, 1 skipped; 16 frontend tests passed; TypeScript and
production build passed. New tests traverse all five result pages, edit a frozen 500-photo
fixture, undo it, retain later edits, preserve source bytes/mtimes, and discard stale selections.
Existing `styles.css` is unchanged. GitHub's tests and image smoke/publish passed; both Zima
services now run `ghcr.io/snehithgit/photosift:sha-055af1d`. The live OpenAPI exposes the category
job route and the production frontend has the new controls. Browser verification on the owner's
exact search link traversed all five pages through 481–500, retained 500 selections, then cleared
the temporary selection. No category changes were applied to the owner's photos. The actual bulk
change, Undo and conflict behavior were exercised on generated fixtures only. A live screenshot
of the controls is in ignored `.data/verification/search-pagination.jpg`.

## 16. Initial quoted grid links (22 November 2026)

The owner reported Documents-grid Next still showing the same photos from an older, repeatedly
quoted `protectedOnly`/`page` URL. The existing Chrome SPA was still running the older frontend
from before the router fix; a full navigation/reload was also necessary. `useUrlSearch` still
read `location.searchStr` directly, so it now normalizes known numeric flags defensively rather
than depending only on router canonicalization. Free-text search is preserved. A real
RouterProvider/hook regression opens a repeatedly quoted page 2/protected link, verifies page 2
and protected=1, then Next=3 and Previous=2. No backend/schema/style changes.
Local verification: 17 frontend tests, TypeScript and production build pass; the stylesheet
and backend are unchanged.
GitHub checks and image smoke/publish passed; both Zima services were updated to
`ghcr.io/snehithgit/photosift:sha-574ea1a`. A fresh browser with the exact legacy link verified
page 2, Next to page 3 with a different API-matched first photo, and Previous back to page 2.
The original Chrome tab was then fully navigated to the clean Documents link and Next was
clicked there too: it now displays different photos on page 3 of 42, with `protectedOnly=1`.
No review/category changes were made. Ignored `.data/verification/documents-pagination.jpg`
contains the pagination bar only, without capturing document contents.

