# PhotoSift outcomes

Implementation status: the browser-only mock app is implemented; each outcome below records its delivery and verification status. Lower-priority workflows remain partial/open.

## Shared fake dataset, model and count invariants — Complete
- Generate one deterministic seeded dataset of approximately 50,000 fake photos in memory and use the same dataset throughout the app; every number on every screen is computed from it, never hard-coded.
- Each photo has: id, filename, paths[] (1–3 locations), thumbUrl, width, height, bytes, captureDate, captureDateSource (exif | filename | file_time | unknown), origin (camera | screenshot | forwarded | downloaded | edited | unknown), groupId, groupSource (ai | manual), band (high | medium | low), decision (undecided | keep | clutter | later), protected null or { reasons: [faces | document | folder | age | manual_keep] }, sensitive (yes | no | unknown), faces ([boxes] or "not_checked"), evidence ([{source: rule | siglip2 | yunet | d3nano, text, strength: strong | weak | small_margin | not_checked}]), and optional duplicateSetId/eventId.
- Dataset content matches group membership: Screenshots contains screenshot filenames; Events contains camera photos of people and places; Documents contains document scans; passport/ID scans live only in Documents > IDs and are sensitive. Events contains no screenshots or passport scans.
- Add a development-only check that logs an error if any invariant fails: group.all = undecided + decided; high + medium + low = undecided; All chip, header counts and left-rail counts read the same numbers; Borderline count equals the number of photos actually listed in that row’s full list; no row says “clear for now” while showing a non-zero count.

## App shell, visual design and URL state — Partially verified
- Keep only one top navigation: Library · Analyze · Sort and review · Export. Remove Home / Timeline / Clean / Find / Protect navigation and remove the marketing banner “ACT 03 · Make the next pass feel lighter.”
- Show photos on the first screen, including on phone. Use calm, compact, dark-by-default neutral UI chrome; photos are the colour; one blue primary-action accent; semantic green/amber/red/grey; square photo tiles with restrained radius; clear badges and readable reason lines. Provide light and system theme options.
- Show a status pill and a scope bar with library root/folder and photo count. Include accessible shortcuts and action-first sentence-case copy. Never use “delete” for photos; say “set aside as clutter.” Never show percentage-of-junk numbers or scores as percentages; use counts and “closer / further” or “small margin.”
- Group, band, sort, filters and page live in the URL. Reload, Back and Forward restore exactly the same view; route titles identify the current content; top navigation highlights the current workflow step. Each route sets its own title, e.g. “Screenshots · Sort and review · PhotoSift.”
- All visible navigation and controls have accessible names. Provide keyboard focus, WCAG AA contrast, screen-reader tile names, reduced-motion support, and touch targets of at least 44px on touch devices.

## Review overview, group counts and recommendation ordering — Complete
- Review overview shows groups with name/icon/covers, undecided/all counts, progress, High/Medium/Low bands, protected count, and a one-line reason. Sections include Personal, Information, From elsewhere, Cleanup, and Other groups from the specification.
- Default order “Most clearable first” truly sorts by the number of High-band undecided photos, so Memes and Screenshots precede Events when their counts are higher. Other supported sorts include size, name, and least reviewed.
- Group rail displays “undecided / all” (e.g. “3,950 / 4,812”). Fully decided groups stay visible as “All 702 decided · View.” Header, chips, overview cards and rail use consistent dataset-derived counts.
- A group page contains the left rail; name/counts; band chips; sort/filter menus; a group reason line; Sample, Borderline and Protected rows with Show all; and a sticky action bar. Documents reason line does not mention OCR; use e.g. “document prompt score · flat lighting · no faces.” Add bottom padding so the sticky bar never covers the final tile row.
- Sample is stratified across bands/folders and can be reshuffled. Borderline is least-certain-first and its count equals its actual listed population. Protected rows show the held-back items and reasons.

## Selection model, bulk decisions, conflicts and undo — Partially verified
- Default group-page selection is all undecided photos matching the current group + band filter + filters, across every page, not just visible tiles. Selection is tied to a query revision.
- Protected photos are excluded by default. Provide a warned “Include N protected” toggle. Tile click toggles a persistent manual exclusion with red outline and “Excluded” label. Exclusions persist while scrolling and paging.
- The action-bar sentence exactly states the selection, e.g. “All 3,912 undecided in Screenshots (High + Medium), excluding 38 protected and 2 you excluded”. The sentence and resulting mutation use the same eligible set.
- Changing group, band or filters with exclusions asks “Clear your 2 exclusions?”; do not silently carry exclusions or selection to a different query. Disable actions while results are loading; ignore stale responses.
- Keep, Review later and Set aside as clutter update only selected photos minus exclusions minus protected unless explicitly included. Excluded photos must not change. Update header, chips, rail and overview immediately.
- After action show exact result and Undo, e.g. “Set aside 3,910 · Undo”; Undo restores exactly the previous decisions. For actions over 1,000 photos simulate a short background job with progress. Simulate four conflicts (“Applied 3,906 · 4 conflicts · View”) and never overwrite items changed elsewhere; make conflicts inspectable.
- In Personal groups (Events, Places, People), Keep is the primary button and Set aside as clutter is quieter/secondary. In clutter-type groups (Screenshots, Memes, Duplicates, Bad shots), Set aside as clutter may be primary.

## Full grid and photo detail — Partially verified
- “Show all <group>” opens `/review/group/:id/all`. Full grid uses paged mock API data and virtualized rendering suitable for groups of 8,000+; supports thumbnail sizes S/M/L.
- Selection supports click; Shift-click visible-page range; Ctrl/Cmd-click add/remove; Select page; Select all matching with exact count across every page; Clear. Exclusions remain stored and counted.
- Tile badges (maximum three) identify protected, exact copy, possible duplicate, decision made, and not analysed; accessible tooltips/text accompany icons. Full-grid suggestion badges are small and appear only when useful; do not put an AI suggestion badge on every tile.
- Enter, double-click or the “i” button opens `/photo/:id?from=<list>`. Detail has large preview, face-box toggle (F), related exact copies/possible matches/same-event strip, and controls in this order: Keep / Clutter / Later / Reset with K/C/L/U; current decision separate from AI suggestion; protection banner only if protected; group picker; evidence; file metadata.
- Changing group actually moves the photo, shows “You changed this,” and updates counts for both groups. Evidence has one row per signal, tagged Rule / SigLIP 2 / YuNet / D3 Nano; weak clue, small margin and Not checked yet appear explicitly where applicable.
- File details show filename, size, resolution, capture date and its source, and every path. Sensitive photos open blurred until user clicks Reveal.
- Left/right arrows move within the list from which detail was opened. Esc returns to the same scroll position. Decision keys act only once the image is loaded and visible.

## Analyze and pipeline simulations — In progress
- `/analyze` has one row each for Inventory, Prepare, Embed, Assess and Group, with progress bar, done/total, simulated rate, execution location (Zima/laptop), and state.
- A dev menu control toggles laptop offline/online. GPU stages show “Waiting for laptop” while offline; browsing/review continues.
- Simulate a failures list of 23 unreadable files with reason, View and Retry. Include “Ready to review” chips linking directly to usable groups. Simulate slow responses/newest-wins so older data cannot overwrite newer data.

## Library and special browse screens — In progress
- `/library` has a folder tree with photo/unsupported/missing counts, scan status and scan/verify controls, file inventory list/grid and tabs for exact duplicates, unsupported, missing and errors.
- Offline source uses grey ghost tiles and “Source unavailable”; scans pause and offline files are not falsely marked missing.
- Duplicates/bursts show one card per set, exact-copy keeper suggestions and reasons; possible matches are unticked and labeled Possible match; best burst photo is suggested with reason. Bulk apply is limited to exact-copy sets.
- Events show vertical year/month timeline and event cards with cover, name, date range, place, photo count and Suggested/Confirmed status. Event actions are Rename, Merge with…, Split here, Remove photo and Confirm. Trips are parent cards with day-events.
- Documents has Papers, Receipts and bills, IDs, Notes and whiteboards, Slides. IDs are Sensitive and blurred until revealed; export routes them to Private/Sensitive/ only.
- Search supports text queries and “more like this/these,” ranked results, “closer / further” scores, current-folder/all-libraries scope, saved searches offline, and an explicit laptop-required message for new text queries offline. Results can be selected and acted on like grids.
- Teach labels one photo at a time using 1 Personal, 2 Document, 3 Screenshot, 4 Meme/greeting, 5 Junk/bad shot, 6 Other, 0 Skip. Show progress; result page reports measured precision by group and band and what suggestions changed. Labels remain distinct from Keep/Clutter decisions.

## Export, history, setup and settings — Open
- `/export` is a four-step flow: choose scope/decisions/groups; choose folder layout and destination with live example path; preview a frozen plan with photo count, bytes, destination free space, collisions, exclusions and searchable/paged source→destination rows; approve and run.
- Approval names its exact plan (e.g. “Copy 31,204 photos (212 GB) · Plan #18”). Going back or changing anything clears approval. Show stale-decision warning with Regenerate. Simulate export collisions.
- Run shows copied/verified/failed progress, pause/resume, log, manifest, and Rollback that removes only verified files created by that run. Export is mock-only and never changes real files. Include Immich read-only external-library hint.
- History is an action timeline with action, scope in words, count, time, and version-aware Undo that skips items changed later and says how many were skipped.
- Setup is a five-step re-openable wizard: add library and quick scan/count; connect laptop with pairing code/read-only mapping/status; choose measured/recommended embedding/D3 models; protect folders/age; optional Teach sample labels. Steps may be skipped and completed later; incomplete status appears in Settings.
- Settings sections: Libraries; Laptop agent; Models; Categories and prompts with versions; Protection rules and affected-photo preview; Thresholds; Export defaults; Appearance; Keyboard and auto-advance.

## Responsive behavior, interaction states and acceptance outcomes — Partially verified
- At widths ≥1280px show left rail + main + optional right detail panel; 768–1279px use a collapsible rail and full-width main; under 768px use bottom four-step navigation, stacked detail and pinned single-photo decisions. Hide Export approval and large bulk actions on phone (<768px), with “Use a larger screen for bulk actions.”
- At 375px there is no horizontal page scroll and photos are visible on the first screen. Add responsive layouts and touch targets as above.
- Design first-load skeletons; laptop-offline state; source-offline ghost tiles; partly analysed/Awaiting analysis; Not checked yet evidence; useful empty groups; errors with retry; stale response protection; Changed elsewhere · Reload conflicts; progress toasts and safe tab close/resume for long jobs.
- Acceptance outcomes: (1) On every group page, header/chips/rail/overview numbers match and high+medium+low equals undecided. (2) Excluding two tiles then clicking Review later leaves those two decisions unchanged, updates counts, and Undo restores everything. (3) Action-bar sentence always matches what the action changes and its count is verifiable after action. (4) Protected photos are untouched unless Include protected is enabled. (5) Show all opens the grid and Select all matching covers the full count across pages. (6) Opening a photo, pressing K, then → moves to the next photo in that list; Esc returns to the same scroll spot. (7) Changing a photo’s group moves it and updates both group counts. (8) Set band=high and reload remains on High; Back/Forward work. (9) Events never shows screenshots or passport scans; passport is in Documents > IDs and opens blurred. (10) At 375px there is no horizontal scroll, photos appear on the first screen, and no large bulk action bar. (11) Only one nav bar exists; there is no marketing banner or percentage-of-junk number.
- Before reporting completion, provide which acceptance cases pass and screenshots for cases 2, 6 and 10. Report only actually verified results.
