# 10 · Frontend specification

> Status: design, not implemented. Updated 1 November 2026.
> Purpose: the complete frontend idea, detailed enough to build a **mock web app** that runs on
> fake data (no backend), then connect it to the real API later.
> Processing rules live in [02 Pipeline](02-pipeline.md); review/export rules in [04](04-review-and-export.md).

---

## 1. Why the frontend matters most

The backend only produces *suggestions*. The frontend is where 50,000 photos become a few dozen
confident bulk decisions. If the screen is confusing, every good model result is wasted.

### The three-question rule

Every screen must answer, without the user thinking:

1. **Which photos am I acting on?** Scope and selection in plain words.
2. **Why does PhotoSift think this?** A reason line at every level: group, tile, detail.
3. **What happens when I click?** The action bar says it *before* the click:
   "Set aside 3,912 photos in Screenshots, excluding 38 protected".

If a screen can't answer all three, it isn't finished.

### Principles

- **Groups first, photos second.** Review happens on groups; single photos are for exceptions.
- **Clutter means "set aside", never "delete".** The word *delete* doesn't appear anywhere in the app.
- **AI suggests, you decide.** Suggestions and decisions always look different.
- **Unknown is visible.** "Not checked yet" is shown, never hidden or treated as "no".
- **Nothing silent:** no silent scope widening, no silent overwrite, no silent model switch.
- **Calm and dense:** neutral UI, photos are the colour.

---

## 2. Users and devices

| | |
|---|---|
| User | One owner (single-user first version) |
| Main device | Desktop/laptop browser, keyboard + mouse |
| Secondary | Tablet: touch review |
| Phone | Browse and light review; no large bulk actions or export approval |
| Network | LAN to Zima; laptop may be offline at any time |

---

## 3. App shell and navigation

```
┌───────────────────────────────────────────────────────────────────────────┐
│ PhotoSift   Library   Analyze   Sort & review   Export        ● Laptop  ⚙ │  ← top bar
├───────────────────────────────────────────────────────────────────────────┤
│ 📁 Scope: /library/Phone-2019-2024 · 48,210 photos            [Change]    │  ← scope bar
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│                          screen content                                   │
│                                                                           │
├───────────────────────────────────────────────────────────────────────────┤
│ (sticky action bar, only when something is selected)                      │
└───────────────────────────────────────────────────────────────────────────┘
```

### Top bar
- **Four steps, in workflow order:** Library → Analyze → Sort & review → Export.
- **Status pill** (right): Laptop online / offline / busy, and Source available / unavailable.
  Click → status popover (agent version, current job, GPU memory, last contact).
- **Activity icon:** opens the Activity drawer (running jobs, bulk actions, exports).
- **Settings icon** (⚙): secondary; technical config lives here, never in the main flow.
- **Help:** `?` opens a keyboard shortcuts sheet.

### Scope bar
- Always shows the current **library root / folder** and its photo count.
- Changing scope never resets filters silently: it asks "Keep filters / Reset filters".
- "All libraries" is an explicit choice, never a default after a reset.

### Routes (all state in the URL)

| Route | Screen |
|---|---|
| `/setup` | First-run wizard |
| `/library?root=&folder=` | Library |
| `/analyze?root=&folder=` | Analyze |
| `/review?root=&folder=` | Review overview (group list) |
| `/review/group/:groupId?band=&filter=&page=` | Group page |
| `/review/group/:groupId/all?...` | Full grid of a group |
| `/photo/:contentId?from=<list-token>` | Photo detail |
| `/duplicates` · `/events` · `/events/:id` · `/documents` | Special views |
| `/search?q=` · `/search?like=:contentId` | Search |
| `/teach` | Labelling / calibration mode |
| `/export` · `/export/plan/:planId` · `/export/run/:runId` | Export |
| `/history` | Decision and action history |
| `/settings/:section` | Settings |

Back, Forward and reload always restore the same screen, filters, page and scroll position.

---

## 4. Screens

### 4.1 First-run setup (`/setup`)

A five-step wizard, shown once (re-openable from Settings):

1. **Add a library:** pick a root on Zima (e.g. `/library`); show photo count after a quick scan.
2. **Connect the laptop:** shows a pairing code; status turns green when the agent connects.
   Show the read-only path mapping it will use.
3. **Choose models:** primary embedding (SigLIP 2 / MobileCLIP2) with its benchmark summary,
   D3 Nano connection test result. "Recommended" badge from measurements, never a guess.
4. **Protect folders:** tick folders that must never get Clutter suggestions (e.g. `Family/`, `Wedding/`).
   Set the age protection (e.g. "photos older than 2015").
5. **Teach PhotoSift (optional):** label ~200 sample photos to calibrate confidence bands (see 4.9).

Each step can be skipped and finished later; the app shows "Setup incomplete" in Settings.

### 4.2 Library (`/library`)

Job: choose what to work on and see what's there.

- **Left:** folder tree with counts (photos, unsupported, missing). Protected folders show a shield.
- **Main:** for the selected folder:
  - Summary cards: total photos, total size, formats, date range, unsupported, missing.
  - Scan controls: **Scan now**, **Verify scan** (full re-hash), last scan time and result.
  - Inventory grid / list toggle (thumbnails once the laptop has prepared them; placeholders before).
- **Tabs below:** All files · Exact duplicates · Unsupported · Missing · Errors.
- **Exact duplicates tab:** one row per content, all its paths listed; no decision here, just facts.
- **Offline source:** tiles shown greyed ("ghost") with "Source unavailable"; nothing is marked missing.
- **Primary action:** "Analyze this folder" → goes to Analyze with the same scope.

### 4.3 Analyze (`/analyze`)

Job: watch the AI work and know what's ready.

```
Scope: /library/Phone-2019-2024                      [Pause] [Resume] [Cancel]

Inventory    ████████████████████  48,210 / 48,210  done
Prepare      ███████████████░░░░░  36,900 / 48,210  ~140/min   laptop
Embed        ███████████░░░░░░░░░  27,400 / 48,210  ~310/min   laptop
Assess (D3)  ██░░░░░░░░░░░░░░░░░░     410 /  2,130  queued     laptop
Group        updates live

Failures: 23 unreadable files  [View]      Paused: none
Ready to review: Screenshots (3,950) · Memes (6,100) · Duplicates (2,300)  [Review results →]
```

- One row per stage: done / total, **measured** rate, where it runs, state
  (running, waiting for laptop, paused, model unavailable, failed).
- **Model summary** card: primary index name + version, D3 Nano status. Read-only here; change in Settings.
- **Failures** list with reason and Retry per reason.
- **"Ready to review"** chips appear as groups become usable → direct link into those groups.
- Laptop offline → GPU stages show "Waiting for laptop", everything else continues.
- Counts never go stale: completed stages show final numbers, not "pending".

### 4.4 Review overview (`/review`)

Job: see all groups and pick where to work.

- **Group cards** in a grid or list, each showing:
  - Name, icon, cover thumbnails (4).
  - **Undecided / All** counts and a small progress bar of decided.
  - Band split: High / Medium / Low.
  - Protected count.
  - One-line reason ("No camera EXIF · phone screen size · screenshot score").
- **Default order: "Most clearable first"**: groups with the most High-confidence clutter at the top.
  Other sorts: size, name, least reviewed.
- Group set (hierarchy):

| Section | Groups |
|---|---|
| Personal | People and family · Portraits · Events · Places · Pets · Objects |
| Information | Documents · Screenshots |
| From elsewhere | Memes and greetings · Graphics and downloads · Forwards |
| Cleanup | Duplicates and bursts · Bad shots |
| Other | Food · Other · Unclassified · Awaiting analysis |

- A group with everything decided still shows ("All 702 decided · View") instead of disappearing.

### 4.5 Group page (`/review/group/:id`): the main screen

```
┌──────────────┬────────────────────────────────────────────────────────────┐
│ Groups       │ Screenshots   3,950 undecided · 862 decided                │
│ undecided/all│ [High 3,410] [Medium 470] [Low 70]        Sort ▾  Filter ▾ │
│              │ Why: no camera EXIF · phone screen size · filename · score │
│ Personal     │                                                            │
│ Events       │ Sample · 24 random of 3,950            [Shuffle] [Show all]│
│ Documents    │ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢                                            │
│▶Screenshots  │ Borderline · least certain first (70)          [Show all]  │
│ Memes        │ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢                                            │
│ Duplicates   │ Protected · faces or possible document (38)    [Show all]  │
│ Bad shots    │ ▢ ▢ ▢ ▢ ▢ ▢ ▢ ▢                                            │
│ Unclassified │                                                            │
├──────────────┴────────────────────────────────────────────────────────────┤
│ All 3,912 undecided in Screenshots (High + Medium), excluding 38 protected │
│                               [Keep] [Review later] [Set aside as clutter] │
└───────────────────────────────────────────────────────────────────────────┘
```

- **Left rail:** group list with undecided/all counts; collapsible on small screens.
- **Header:** name, counts, **band chips** (they act as filters), sort and filter menus, reason line.
- **Three rows instead of thousands of photos:**
  1. **Sample:** random, stratified across bands and folders. "Shuffle" draws a new sample.
  2. **Borderline:** lowest margin first; this is where mistakes hide.
  3. **Protected:** automatically held back (faces, possible document, protected folder, age).
- **Click a tile:** toggles exclusion from the bulk action (red outline, "Excluded").
  **Double-click / Enter:** opens Photo detail.
- **Filters:** band, origin (camera/screenshot/forwarded/downloaded/unknown), decision state,
  folder, date range, has faces, possible duplicate, format.
- **Action bar** (sticky): describes the exact selection in words; three actions:
  Keep · Review later · Set aside as clutter. After an action: toast "Set aside 3,912 · Undo".

### 4.6 Full grid (`/review/group/:id/all`)

- Virtualised grid (only visible rows rendered), server-side filtering and paging.
- Thumbnail size slider (S / M / L).
- **Selection:**
  - Click = select one · Shift-click = range on the visible page · Ctrl/Cmd-click = add/remove.
  - **Select page** · **Select all matching** (across every page, shows the exact count) · **Clear**.
  - Exclusions on top of "all matching" are kept and counted.
- **Tile badges** (max 3, icon only, tooltip on hover):
  shield = protected · copy = exact copy · layers = possible duplicate ·
  check/x/clock = Keep/Clutter/Later decided · question = not analysed yet.
- Group-by toggle: none · folder · month · event.

### 4.7 Photo detail (`/photo/:id`)

Two columns: **photo left, answers right** (stacked on phone).

- **Top bar:** back (same scroll position), source list name, "12 of 70", ← →.
  ← → move through **the list it was opened from**, never a different list.
- **Left:** large oriented preview; face boxes toggle; zoom (click / scroll);
  strip of exact copies, possible matches and same-event photos → click to compare side by side.
- **Right, in this order:**
  1. **Decision buttons** Keep / Clutter / Later / Reset with keys shown; current decision and
     AI suggestion shown separately.
  2. **Protection banner** (only when it applies) with the reason.
  3. **Group picker:** changing it really moves the photo; label "You changed this".
  4. **Why PhotoSift thinks this:** one row per signal, tagged by source
     (Rule · SigLIP 2 · YuNet · D3 Nano), weak clues marked weak, close scores marked "small margin",
     missing checks shown as "Not checked yet".
  5. **File:** name, size, resolution, capture date **and its source** (EXIF / filename / file time),
     camera, GPS (if any), **every path** this content exists at.
  6. **History:** decisions and corrections for this photo, with time.
- **Sensitive photos** open blurred; "Reveal" needs a click; never shown in grid tooltips.
- **Changed elsewhere** (another tab/bulk job): notice "Changed elsewhere · Reload", no overwrite.
- Auto-advance after a decision is an opt-in setting (off by default).

### 4.8 Special views

**Duplicates and bursts (`/duplicates`)**
- One card per set, side by side.
- **Exact copies:** pre-ticked as extra copies; the kept one is marked "Keeper" with reason
  (e.g. "highest resolution", "in Family/ folder").
- **Possible matches:** shown **unticked**, labelled "Possible match" with the similarity reason.
- **Bursts:** best shot suggested with reason ("sharpest", "eyes open"), user confirms.
- Bulk: "Apply suggested keepers to all exact-copy sets" (exact copies only).

**Events (`/events`)**
- Vertical timeline grouped by year → month; each event card: cover, name, date range,
  place, photo count, "Suggested" or "Confirmed" badge.
- Event page: photos in time order with scene breaks; actions: **Rename · Merge with… · Split here ·
  Remove photo · Confirm**.
- Trips shown as a parent card containing day-events ("Goa trip · 4 days · 612 photos").

**Documents (`/documents`)**
- Subgroup tabs: Papers · Receipts and bills · IDs · Notes and whiteboards · Slides.
- IDs tab is **Sensitive**: thumbnails blurred until revealed; export goes to `Private/Sensitive/` only.
- Same Sample / Borderline / Protected layout as any group.

**Search (`/search`)**
- One search box: text ("people eating together", "photographed paperwork").
- Results ranked; scores shown as "closer / further", never as percentages.
- "More like this" from any photo; "More like these" from a selection (uses examples).
- Scope chip: search within current folder or all libraries.
- Laptop offline + new text query → "Needs the laptop for new text searches; saved searches still work".
- Results can be selected and acted on like any grid.

### 4.9 Teach PhotoSift (`/teach`)

Fast labelling to calibrate confidence and train the personal classifier.

- One photo at a time, large; answer with number keys:
  `1` Personal · `2` Document · `3` Screenshot · `4` Meme/greeting · `5` Junk/bad shot · `6` Other ·
  `0` Skip.
- Progress: "142 of 200 · about 3 minutes left".
- Result page: measured precision per group and band ("Screenshots High band: 98% correct on your
  labels"), and what changed in the suggestions.
- Labels are content labels, separate from Keep/Clutter decisions.

### 4.10 Export (`/export`)

Four steps on one page, top to bottom:

1. **Choose what to export:** scope, decisions (Keep ✓, Review later ☐, Clutter ☐, Sensitive ☐),
   groups to include.
2. **Choose folder layout:** template picker with live example path:
   `Photos/{Year}/{Event or Month}/` · `Documents/{Subgroup}/` …; destination root.
3. **Preview plan** (frozen snapshot):
   - Totals: photos, bytes, **free space at destination**, collisions, exclusions.
   - Table: source → destination for every file (searchable, paged).
   - Warnings: "12 newer decisions since this plan was made · Regenerate".
4. **Approve and run:** button shows the exact plan: "Copy 31,204 photos (212 GB) · Plan #18".
   Approval belongs to this plan only; going back or changing anything clears it.

- **Run page** (`/export/run/:id`): progress (copied, verified, failed), pause/resume,
  log, manifest download, **Rollback** (removes only verified files this run created).
- **History:** previous plans and runs with status.
- Immich hint: "Point Immich at `Organized/` as a read-only external library".

### 4.11 History (`/history`)

- Timeline of actions: bulk decisions, single decisions, corrections, exports.
- Each entry: what, scope (in words), count, time, **Undo** (version-aware: skips items changed later
  and says how many it skipped).

### 4.12 Settings (`/settings`)

| Section | Contents |
|---|---|
| Libraries | Roots, scan schedule, protected folders, age protection |
| Laptop agent | Pairing, path mappings, status, GPU memory, batch size |
| Models | Primary embedding index, comparison results, D3 Nano endpoint test |
| Categories and prompts | Prompt packs per group (view/edit), version history |
| Protection rules | Faces, documents, sensitive, age; preview "this rule affects N photos" |
| Thresholds | Band cut-offs per group (from Teach results) |
| Export defaults | Destination, layout template, verification |
| Appearance | Theme (dark default / light / system), density, thumbnail size |
| Keyboard | Shortcut list, auto-advance toggle |

---

## 5. Selection model (the most important interaction)

- A selection always belongs to **one query revision** (scope + group + filters + sort).
- Changing filters while a selection exists → "Clear selection?" (never carries it to new results).
- While results load, selection actions are disabled ("Loading…") so you can't act on an old page.
- **Select all matching** stores the query, not thousands of IDs; the server freezes membership
  at confirmation and returns the exact count.
- Exclusions are stored with the selection and shown in the action bar.
- Protected items are **excluded by default**; including them needs an explicit
  "Include 38 protected" toggle with a warning.
- Bulk actions run as background jobs: toast with progress → "Applied 3,912 · 4 conflicts · Undo".
  Conflicts (newer manual decisions) are listed, never overwritten.

---

## 6. Keyboard

| Where | Key | Action |
|---|---|---|
| Everywhere | `?` | Shortcut sheet |
| Everywhere | `/` | Focus search |
| Everywhere | `g` then `l` / `a` / `r` / `e` | Go to Library / Analyze / Review / Export |
| Grid | arrows | Move focus |
| Grid | `Space` | Toggle select / exclude |
| Grid | `Shift`+arrows | Extend selection |
| Grid | `Ctrl/Cmd+A` | Select all matching (confirm count) |
| Grid | `Enter` | Open detail |
| Detail | `K` `C` `L` `U` | Keep · Clutter · Later · Reset |
| Detail | `←` `→` | Previous / next |
| Detail | `F` · `G` · `S` | Face boxes · group picker · compare similar |
| Any | `Ctrl/Cmd+Z` | Undo last action |
| Any | `Esc` | Close / back |

Decision keys work only when the photo is loaded and visible.

---

## 7. States (designed, not left to chance)

| State | What the user sees |
|---|---|
| First load | Skeleton tiles and cards, never a blank page |
| Laptop offline | Status pill grey + small banner; review/browse keep working; GPU stages "Waiting for laptop" |
| Source offline | Ghost tiles; scans paused; nothing marked missing |
| Partly analysed | Groups fill in live; "Awaiting analysis" count visible |
| Not checked | Evidence row "Not checked yet" (never "No") |
| Empty group | Invitation: "All screenshots reviewed · View decided" |
| Error | What failed, how many, Retry; errors are never cleared by background refresh |
| Stale response | Older responses ignored; actions disabled until fresh data arrives |
| Conflict | "Changed elsewhere · Reload" |
| Long job | Toast with progress; safe to close the tab; resumes |

---

## 8. Words (copy rules)

- Sentence case everywhere; verbs first on buttons ("Set aside as clutter", "Approve plan").
- **Never say "delete"** for user photos. Clutter = "set aside".
- Weak clues say "weak clue"; scores say "closer / further" or "small margin", never "92% junk".
- Counts are exact ("3,912"), with "about" only for time estimates.
- Errors: what happened + what to do ("Couldn't read 23 files. Retry or view list").
- Empty states invite ("All screenshots reviewed").

---

## 9. Visual design

- **Theme:** dark by default (photos look better), light and system available.
- **Neutral greys** for all UI chrome; photos are the only strong colour.
- **One accent colour** (blue) for the main action per screen.
- **Colour = meaning only:**

| Meaning | Colour |
|---|---|
| Keep / High confidence | Green |
| Medium / protected / warning | Amber |
| Clutter / excluded / error | Red |
| Low / unknown / not checked | Grey |
| Selected / primary action | Blue |

- **Typography:** one sans family, two weights (regular, medium); 13px UI text, 16px headings.
- **Tiles:** square crops in grids, original aspect in detail; 4px radius; badges top-right.
- **Density:** compact (default for desktop) and comfortable (tablet).
- **Icons:** one outline icon set (Tabler or Lucide).

---

## 10. Responsive

| Width | Layout |
|---|---|
| ≥ 1280 px | Left rail + main + optional right detail panel |
| 768–1279 px | Collapsible rail, full-width main |
| < 768 px | Bottom tab bar (4 steps), stacked detail, decision buttons pinned at bottom; Export approval and large bulk actions hidden ("Use a larger screen") |

No horizontal page scroll at any width. Touch targets ≥ 44 px on touch devices.

---

## 11. Accessibility

- Full keyboard use; visible focus ring; focus trapped in dialogs and restored on close.
- Contrast WCAG AA in both themes; badges never rely on colour alone (icon + tooltip text).
- Screen-reader labels for tiles ("Screenshot, 12 Apr 2023, protected, undecided").
- Respect reduced motion.

---

## 12. Performance

- Never load the whole library into the browser: server-side filter, sort and paging.
- Virtualised grids; thumbnails ~256 px WebP, previews ~1600 px, loaded lazily.
- Group counts come from the server as aggregates.
- Fetching with request cancellation and "newest wins" so older responses never overwrite newer.
- Targets to measure (not promises): grid scroll at 60 fps with 50k items; detail open < 300 ms on LAN.

---

## 13. Tech stack

| Concern | Choice |
|---|---|
| Framework | React + TypeScript + Vite |
| Routing | TanStack Router (typed URL search params for filters) |
| Server data | TanStack Query (cancellation, stale-while-revalidate, newest wins) |
| Virtual grid | TanStack Virtual |
| UI primitives | Radix UI (dialogs, menus, popovers, tooltips; accessible by default) |
| Styling | Tailwind CSS with design tokens as CSS variables (light/dark) |
| Icons | Tabler or Lucide |
| Local UI state | Zustand (selection, exclusions, UI prefs) |
| Mock backend | MSW (Mock Service Worker) + generated fixtures |
| Tests | Vitest + Testing Library; Playwright for key flows |

### Folder layout

```
web/
  src/
    app/            shell, routes, providers
    screens/        setup, library, analyze, review, group, grid, photo,
                    duplicates, events, documents, search, teach, export, history, settings
    components/     PhotoTile, GroupCard, ReasonLine, EvidenceRow, BandChip, Badge,
                    ActionBar, ScopeBar, StatusPill, StageProgress, PlanTable,
                    UndoToast, EmptyState, ConfirmDialog, FaceBoxes, CompareView
    data/           api client, query hooks, types
    mocks/          MSW handlers, fixture generator
    state/          selection store, preferences
    styles/         tokens.css, tailwind config
```

---

## 14. Core components

| Component | Purpose | Key props / states |
|---|---|---|
| `PhotoTile` | One thumbnail | selected, excluded, decision, badges, protected, loading, sensitive-blur |
| `GroupCard` | Group summary | counts (all/undecided/decided), bands, protected, reason, covers |
| `ReasonLine` | One-line "why" | list of short clues |
| `EvidenceRow` | One signal in detail | source tag, text, strength (strong/weak/small margin/not checked) |
| `BandChip` | High/Medium/Low filter | count, active |
| `ActionBar` | Sticky bulk actions | scope sentence, counts, exclusions, protected toggle, actions, busy |
| `ScopeBar` | Current root/folder | path, count, change |
| `StatusPill` | Laptop / source state | online, offline, busy, unavailable |
| `StageProgress` | One pipeline stage | done/total, rate, location, state |
| `PlanTable` | Export mapping | paged rows, search, collisions |
| `UndoToast` | Action feedback | message, progress, undo, conflicts |
| `CompareView` | Side-by-side photos | 2–4 photos, keeper marker |
| `FaceBoxes` | Overlay | boxes, visible |
| `EmptyState` | Invitation | title, body, action |

---

## 15. Mock data contract (for the mock web app)

The mock app runs fully in the browser with MSW and a seeded generator (~50,000 fake photos).
Thumbnails can be placeholder images coloured by group.

```ts
type Band = "high" | "medium" | "low";
type Decision = "undecided" | "keep" | "clutter" | "later";
type Origin = "camera" | "screenshot" | "forwarded" | "downloaded" | "edited" | "unknown";
type Tri = "yes" | "no" | "unknown";

interface Photo {
  contentId: string;            // sha256
  paths: string[];              // every location
  thumbUrl: string;
  previewUrl: string;
  width: number; height: number; bytes: number; format: string;
  captureDate: string | null;
  captureDateSource: "exif" | "filename" | "file_time" | "unknown";
  camera?: string; gps?: { lat: number; lon: number };
  origin: Origin;
  groupId: string;              // effective group
  groupSource: "ai" | "manual";
  band: Band;
  triage: "likely_keep" | "clutter_candidate" | "needs_review" | "awaiting_analysis";
  decision: Decision;
  protected: { reasons: ("faces" | "document" | "folder" | "age" | "manual_keep")[] } | null;
  sensitive: Tri;
  importance: Tri;
  faces: { box: [number, number, number, number] }[] | "not_checked";
  evidence: Evidence[];
  duplicateSetId?: string; eventId?: string;
}

interface Evidence {
  source: "rule" | "siglip2" | "mobileclip2" | "yunet" | "d3nano";
  text: string;                 // "No camera EXIF"
  strength: "strong" | "weak" | "small_margin" | "not_checked";
}

interface Group {
  id: string; name: string; section: string; icon: string;
  counts: { all: number; undecided: number; decided: number; protected: number };
  bands: Record<Band, number>;
  reason: string;
  coverIds: string[];
}

interface Stage {
  name: "inventory" | "prepare" | "embed" | "assess" | "group";
  done: number; total: number; ratePerMin: number | null;
  location: "zima" | "laptop";
  state: "running" | "waiting_laptop" | "paused" | "model_unavailable" | "failed" | "done";
}

interface Event {
  id: string; name: string; start: string; end: string;
  place?: string; photoCount: number; coverId: string;
  status: "suggested" | "confirmed"; parentTripId?: string;
}

interface ExportPlan {
  id: string; revision: number; createdAt: string;
  totals: { photos: number; bytes: number; freeBytes: number; collisions: number; exclusions: number };
  staleDecisions: number;
  rows: { contentId: string; source: string; destination: string }[]; // paged
}
```

Mock API endpoints (MSW):

```
GET  /api/status                       laptop + source status
GET  /api/roots, /api/folders?root=    library tree with counts
GET  /api/stages?scope=                analyze progress (simulated ticking)
GET  /api/groups?scope=                group cards
GET  /api/photos?scope=&group=&band=&filter=&sort=&cursor=   paged
GET  /api/photos/:id                   detail
POST /api/selections                   freeze "all matching" → { selectionId, count }
POST /api/bulk                         { selectionId, decision } → job
GET  /api/jobs/:id                     progress, applied, conflicts
PATCH /api/photos/:id                  { decision } | { groupId }
GET  /api/duplicates, /api/events, /api/events/:id
GET  /api/search?q=  |  ?like=
POST /api/export/plans                 → plan;   POST /api/export/plans/:id/approve
GET  /api/export/runs/:id              simulated progress
GET  /api/history                      POST /api/history/:id/undo
```

Simulations to include in the mock: laptop going offline/online, a stage failing with 23 errors,
slow responses (to test "newest wins"), a bulk job with 4 conflicts, and an export with collisions.

---

## 16. Mock web app: build order and done checklist

| Step | Build | Done when |
|---|---|---|
| 1 | Shell, routing, tokens, theme, status pill, scope bar | navigation and URL state survive reload |
| 2 | Mock data generator + MSW handlers | 50k fake photos served in pages |
| 3 | Review overview + group page + action bar | bulk "set aside" with exclusions and undo works |
| 4 | Full grid + selection model | select all matching, exclusions, stale-guard all work |
| 5 | Photo detail | evidence, group correction, keys, ← → within list |
| 6 | Analyze | stages tick, laptop offline state, failures, "ready to review" |
| 7 | Duplicates, Events, Documents | keeper/possible-match rules, merge/split, blurred IDs |
| 8 | Search + Teach | text/like search, 1–6 labelling with results page |
| 9 | Export | plan preview, plan-bound approval, run, rollback |
| 10 | Library, History, Settings, Setup wizard | full app flow from first run to export |

Checklist for every screen: answers the three questions · keyboard works · dark and light ·
loading/empty/error/offline states · no horizontal scroll at 375 px · URL restores the view.
