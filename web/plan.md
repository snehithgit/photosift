# PhotoSift implementation plan

## Product scope

Build the supplied PhotoSift specification as a browser-only mock application on one deterministic seeded dataset of approximately 50,000 fake photos. There is no backend or real file access. Every screen, count, selection, decision, undo, simulated job, and export preview derives from that shared in-memory dataset. The app must make action scope and reasons explicit, keep suggestions visually distinct from decisions, treat unknown checks as unknown, default-protect sensitive or otherwise protected photos, and never use “delete” for photos.

The main delivery is the trust-critical photo review workflow: Library, Analyze, review overview, group review, full grid, and photo detail. Also include the specified duplicates/events/documents/search/Teach/export/history/settings/setup experiences as browser simulations, plus keyboard, loading/error/offline/conflict states. Implement one top navigation (Library · Analyze · Sort & review · Export), a visible scope bar and status, URL-persisted routes and query state, no marketing banner, responsive layouts, and no percentage-of-junk scores. Use the user-provided calm visual direction: dark neutral chrome, photo-led surfaces, blue primary actions, semantic green/amber/red/grey, compact density, group rail, reason lines, band chips, three review rows, and sticky action bar.

## Architecture and implementation

Use React, TypeScript, and Vite, keeping the experience client-only. The delivered app uses TanStack Router for URL-driven routes and filters, TanStack Virtual for large grids, Zustand for review decisions/selection/jobs/undo, Lucide icons, authored CSS variables and responsive styles, and Vitest/Testing Library for domain checks. Screen simulations are local; there is no network mock API or backend. React Query, Radix UI and the Tailwind Vite plugin are present in the starter dependency manifest but are not required by the delivered screens. No managed database, server, external account, or real-photo mutation.

Generate the single seeded dataset once per session. Model group membership, document IDs/sensitive items, exact duplicate sets, events, paths, protection, confidence bands, decisions, and evidence in the fixture types. Compute all group aggregates from the same records. Add development-only invariant checks for `all = undecided + decided`, band totals equalling undecided, consistent header/chip/rail/overview values, actual Borderline list counts, and no nonzero “clear for now” claims. Use derived selector/query functions for paging, filtering, sorting, counts, and full-match selection. Simulate delayed responses, offline laptop/source states, 23 unreadable files, 4 concurrent conflicts, collision-bearing export plans, background progress, and undo from captured prior decisions.

Selection is query-revision-bound and defaults to all matching undecided items across pages, with protected photos excluded. Tile clicks add/remove exclusions, which persist across paging. Any query change with exclusions asks before clearing them. Protected inclusion requires an explicit warned toggle. The sticky action sentence and action result use the same computed eligible set; excluded and protected items cannot be changed accidentally. Undo restores exactly the prior values for items this action changed, while conflicts remain untouched. Large actions report progress and exact applied/conflict counts.

Group pages show a left rail, consistent undecided/all counts, High/Medium/Low chips, concise reason, Sample/Borderline/Protected rows, Show all, and a safe sticky action bar. Most-clearable-first sorts by High-band undecided count. Personal groups prioritize Keep; clutter-type groups prioritize Set aside as clutter. Fully decided groups remain visible. Full-grid selection supports click, range, modifier toggles, page selection, all-matching with exact count, exclusions, clear, thumbnail sizes, and concise accessible badges. Photo detail keeps its source-list order and scroll return state; it supports loaded-image-gated keyboard decisions, group moves with aggregate updates, protection and evidence details, face overlay, comparisons, and blurred sensitive images with explicit Reveal.

Implement Analyze, Library, Duplicates, Events, Documents, Search, Teach, Export, History, Setup, and Settings using the same dataset and shared design system. Export is a frozen, plan-bound preview with decisions/groups, destination template, example path, totals, free space, collisions, exclusions, source-to-destination mapping, approval reset on edits/back, simulated copy/verification progress, and rollback simulation. Library source-offline tiles are ghosts, not missing. Include phone/tablet breakpoints, a 375px no-horizontal-scroll layout, touch targets, accessible control names/focus, reduced motion, and light/dark/system tokens.

## Project structure

- `src/main.tsx`: React bootstrap, global styles and route registration.
- `src/router.tsx`: route tree, shared shell, navigation, page titles, keyboard shortcuts and status/undo/conflict toasts.
- `src/screens.tsx`: review overview/group/full-grid/detail, Library, Analyze, Duplicates, Events, Documents, Search, Teach, Export, History, Settings and Setup simulations.
- `src/components.tsx`: photo tiles, evidence/reason rows, confidence chips, action bar and shared accessible controls.
- `src/domain.ts`: typed fake-photo model, deterministic 50,000-photo generator, group aggregates and filtering/sorting selectors.
- `src/store.ts`: session-local decisions, selection/exclusions, conflict simulation, background jobs and undo.
- `src/styles.css`: CSS-variable design tokens, dark/light themes and responsive breakpoints.
- `src/domain.test.ts`: seeded-data, safety-boundary, inventory and action/undo tests.
- `public/manus-routes.json`: static page-route manifest, kept in sync with the route tree.
- `public/manus-routes.json`: static page-route manifest, kept in sync with source.

## Visual direction

- **Design movement:** Calm, compact photo-review console, retaining the supplied dark neutral, photo-first direction.
- **Core principles:** scope before action; evidence before confidence; safe defaults before speed; dense chrome with readable content.
- **Color philosophy:** graphite surfaces recede so thumbnails carry colour; blue marks the primary action; green, amber, red, and grey carry consistent semantic meaning.
- **Layout paradigm:** persistent workflow header and scope strip, collapsible group rail, broad review canvas, and selection-aware bottom action bar; stack these regions on narrow screens.
- **Signature elements:** slim group-count rail; concise “Why” evidence line; three confidence/protection review bands with a persistent exact-scope action sentence.
- **Interaction philosophy:** reversible decisions, exact scope described before commit, visible exclusions/protections, explicit confirmations for scope changes, and no silent overwrites.
- **Animation:** restrained short fades and progress transitions only; honor reduced-motion preferences; never animate layout in a way that shifts keyboard focus or photo selection.
- **Typography system:** one sans-serif family, regular/medium weights, compact 13px control text and 16px section headings, with larger photo-detail metadata hierarchy.
- **Brand essence:** Photo triage for one owner who needs to review a large library without surrendering control; calm, candid, careful.
- **Brand voice:** plain, exact, action-first; examples: “All 3,912 undecided in Screenshots, excluding 38 protected and 2 you excluded.” “Set aside 3,910 · Undo”.
- **Wordmark & logo:** a compact “PhotoSift” wordmark paired with a simple outlined photo-frame mark and a small sifting/slit motif; no decorative imagery.
- **Signature brand color:** clear, restrained blue for the selected item and one primary action.

## Build and serving workflow

1. After plan approval, create `TODO.md` with the full independently deliverable outcomes and acceptance clauses from the attached specification.
2. Register host-managed TypeScript diagnostics for the empty Sandbox project; configure dependencies and package-manager/lifecycle policy before install.
3. Create the reusable domain types, deterministic dataset, selectors, mock API and development invariants; then build the shell, route manifest and token system.
4. Implement review selection/action/undo and group count consistency, followed by virtual grid and detail, then Analyze/Library and remaining simulated screens.
5. Start the app on the already configured port 3000, verify the route manifest and diagnostics, and run available checks. Capture the user-requested screenshots for acceptance cases 2, 6 and 10; report only cases actually verified.
6. Prepare a project logo URL in `app.config.ts` before a checkpoint. If saving a deliverable checkpoint, preserve canonical `main` and confirm the recorded SHA. Do not publish without a successful explicit publication flow; auto-publish is currently off.

## Constraints

The dataset is in-memory and is regenerated from a stable seed; edits persist for the active browser session only. The mock export does not move real files. Preview runs on the configured port 3000. The app is deliberately local-first and requires no account, server or database.
