# 05 · Ideas backlog

Roughly prioritised. None of these are commitments.

## High value

1. **Learn from your decisions.** After a few hundred Keep/Clutter clicks, train a
   tiny logistic-regression classifier on the primary index's vectors. Measure training latency and held-out quality; retraining creates versioned suggestions and never overwrites manual decisions. Tied to one index.
2. **Active learning.** Show the photos the classifier is least sure about first,
   so each click teaches it the most.
3. **Prompt presets as data.** Category prompts live in the database, not in code.
   Adding "college notes" or "wedding" is a new row; re-scoring can reuse existing vectors without another image-model pass; measure the latency.
4. **Explainable suggestions.** Every suggestion lists its signals (heuristics,
   scores, assessment rationale).
5. **Protection rules and a Sensitive bucket** (see [04](04-review-and-export.md)).

## Medium

6. **Space-saved counter** for motivation ("38 GB marked as clutter").
7. **Best-shot picker** for bursts: sharpness, eyes open, exposure, resolution.
8. **OCR for documents and screenshots** → searchable receipts, tickets, notes.
9. **Undo and decision history:** use version-aware reversal without overwriting later edits; export has a separate verified rollback.
10. **Session mode:** "15 minutes of triage", ending with a summary.

## Later

11. **Video support:** keyframes → averaged vector; short forwards are big clutter.
12. **Face-aware protection** (local face detection only), never auto-clutter known people.
13. **Smart album names** from event time + place + representative image themes.
14. **Scheduled incremental runs:** new imports processed overnight when the laptop is online.
15. **Library health report:** duplicate GB, screenshot share, oldest unreviewed folders.
