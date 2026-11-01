# PhotoSift

> Status: the frontend is connected to the real backend, with mock data removed and its
> look unchanged. Inventory, review, laptop preparation, events and verified-copy export
> work end to end. Run it with the [backend guide](server/README.md).
> The laptop now fills an embedding index from leased jobs, but filling it does not group
> anything. Real photos stay in Unclassified until prompts are encoded, labels are compared
> and an index is explicitly promoted. D3 Nano is connected through Ollama's decision API and
> tested on generated images, not real private records. No model accuracy is claimed.

PhotoSift is a local AI photo-decluttering app for huge personal libraries. It groups
personal photos, documents, food, screenshots and memes so you can make bulk
**Keep / Clutter** decisions, find similar photos, get album suggestions, and export
organised folders for Immich.

## Core principles

1. **Originals are never modified.** The library is mounted read-only everywhere.
   Every decision is metadata; cache/database writes stay in app data, and approved export writes independent output copies.
2. **Everything runs locally.** No cloud services, no telemetry.
3. **Zima owns the state.** App, database, job queue, UI and library live on Zima.
4. **The GPU laptop is a stateless, disposable worker.** It pulls jobs, returns
   results, and can go offline at any moment without losing work.
5. **No silent switching.** Models, checkpoints and protocols are explicit config;
   vectors from different model spaces are never mixed.
6. **AI suggests, you decide.** Nothing is marked Clutter without passing protection
   rules, and every suggestion shows why it was made.

> **Continuing the work?** Start with [HANDOFF.md](HANDOFF.md): current state, decisions and the next task.

## Documents

| Doc | What it covers |
|-----|----------------|
| [01 Architecture](docs/01-architecture.md) | Zima / laptop split, data flow, storage |
| [02 Pipeline](docs/02-pipeline.md) | **Canonical pipeline:** stage inputs, outputs, models, leases and recovery |
| [03 Models](docs/03-models.md) | SigLIP 2, MobileCLIP2, D3 Nano: roles and rules |
| [04 Review & export](docs/04-review-and-export.md) | Triage UI, protection rules, Immich export |
| [05 Ideas backlog](docs/05-ideas.md) | Features worth considering, roughly prioritised |
| [06 Open questions](docs/06-open-questions.md) | Things to decide or find out before building |
| [07 Model shortlist](docs/07-model-shortlist.md) | Models chosen per goal, dropped models, task-specific models reviewed |
| [08 Prior art](docs/08-prior-art.md) | What to borrow from Facet, cull and FolioSort |
| [09 Approach](docs/09-approach.md) | **Start here:** summary of the fresh-project approach and build order |
| [10 Frontend](docs/10-frontend.md) | Complete frontend spec: screens, selection model, states, mock data contract |

> PhotoSift is a **fresh project**: it does not extend or reuse any existing repository.
> The [canonical pipeline](docs/02-pipeline.md) now defines the working design: SQLite WAL on Zima, a laptop pull agent, one primary embedding index, protection before Clutter suggestions, and copy-only export.
> Docs 07–08 are research/background notes, not measured product guarantees. Preparation and
> verified copying are implemented, and embedding jobs run on the real GPU service; labelled evaluation and D3 still need validation.
