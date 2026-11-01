# 04 · Review and export

Status: design contract, not implementation. See [02 Pipeline](02-pipeline.md) stages 6–9 for the authoritative flow.

## Automatic grouping; explicit bulk decisions

Content groups: Personal, Documents, Food, Memes/graphics, Screenshots, Other and Unclassified, with similarity/event views later. Estimated origin, importance, sensitivity, triage suggestion and final user decision are independent fields.

Each group shows All / Undecided / Decided counts, a representative/stratified sample, borderline items and protection counts. A decided collection must remain discoverable. Samples do not guarantee correctness of unseen items.

- Keep root/folder/filter/page/detail context through navigation and reload.
- Group changes preserve selected library/folder; Reset all scope is explicit.
- Provide Select page, shift-select, Select all matching across pages and explicit exclusions.
- Selection is tied to the loaded query revision; disable stale-page actions while filters load.
- Freeze selected membership, relevant versions and exclusions at confirmation. Never silently widen scope or overwrite a newer manual decision.
- Persist bulk jobs, per-item audit/conflicts and clear recovery. Version-aware Undo cannot erase later edits.
- K Keep / C Clutter / L Later / U Reset, arrows and Escape only operate on a loaded visible matching detail. Accessible focus containment/restore and readable text are required.
- Correction changes content/subcategory directly, with manual values taking precedence. Changing Food to Other actually changes its group.

## Protection policy

Protection is in place before Clutter suggestions are offered. User-protected folders, manual Keep, people/face evidence, importance, sensitivity and configured age protection block or hold automatic candidates according to a versioned policy.

No EXIF, forwarded origin, blur, no detected face or small size alone proves junk. Detection/assessment failures remain Unknown. YuNet detects faces, not identities or sentimental value. Shared personal photos may join events.

Important family photos stay Personal with protection flags. Sensitive means potentially private IDs/financial/medical content, not simply “worth keeping.” Users can explicitly override suggestions/protections through a clearly scoped audited action; the UI shows affected protected counts instead of silently including them.

Visual near-duplicates are possible matches and remain unticked. Exact copies are multiple paths for one content; deduplicating an export does not label the content itself Clutter.

## Copy-only export

A plan freezes approved scope, chosen decisions, source content/size, actual directory mapping, exclusions and intended outputs. Show bytes and available capacity. Multiple exports are distinct runs; never imply “already exported” items are automatically skipped unless that mode is deliberately implemented.

Approval belongs to one displayed plan ID/revision/state. Back/Forward or a changed plan clears confirmation. Later review edits do not silently mutate a frozen plan; show its creation snapshot and offer explicit regeneration.

Use independent checksum-verified copies, not hardlinks to originals. Never overwrite. Journal ownership and output identity, publish verified temporary files atomically, and create manifests. Retry and rollback are explicit; rollback removes only unchanged verified outputs this run created. Preserve ambiguous outputs for manual recovery.

Output roots:

```text
PhotoSift-<run-id>/
  Organized/             # approved non-sensitive Keep content only
    Photos/<Year>/<Event-or-Month>/
    Documents/<subgroup>/
    Food/
    Screenshots/
    Memes-and-Graphics/
  Private/Sensitive/     # optional explicit private output
  Review-Later/          # optional
  Clutter/<bucket>/      # optional set-aside copies, never original deletion
  manifest.csv
  manifest.json
```

Immich indexes only Organized as a read-only external library. Private/Sensitive, Review-Later and Clutter must be outside that indexing mount. Content in these sections is not automatically exported without a clear option/approval.

Immich API albums and XMP metadata are later optional integration stages. Write metadata only to exported copies or sidecars alongside exported copies, never to source originals/folders. OCR is outside the initial scope; document grouping does not require it.
