# 08 · Prior art: Facet, cull, FolioSort

> Research notes, 2026-11-01. Facet and cull were studied from their source and config;
> FolioSort is closed source, so only its public README was studied.

| | Facet | cull | FolioSort |
|---|---|---|---|
| Focus | Photo scoring/culling for photographers | Cleaning messy image folders | Organising photos where they live |
| Source | MIT code (some default models non-commercial) | MIT | **Proprietary**, source not published |
| Stack | FastAPI + SQLite + Angular, PyTorch | Python, open_clip, imagehash, Gradio | Swift/SwiftUI (macOS), .NET WPF (Windows) |
| Junk detection | Zero-shot on stored embeddings, with a "not junk" comparison class | Zero-shot CLIP, one threshold | Screenshots, blur, duplicates (models not named) |
| Touches originals? | Reads only; syncs ratings to Immich via API | Moves files (dry run by default) | Copy mode by default, but writes metadata into files |

## Facet: what to borrow

### Junk sweep (`models/junk_classifier.py`)
- **No extra model pass:** pure zero-shot over the embedding already stored per photo.
- **Several prompts per kind, best match wins** (`pooling: "max"`).
- **A "not junk" comparison class**, including **hard negatives**: real photos that look like documents.
- **Decision rule:** flag only if the best junk score ≥ `min_confidence` **and**
  (best junk − not junk) ≥ `min_margin`.
- **Thresholds differ by backend:** OpenCLIP 0.2 / 0.06; transformers (SigLIP) 0.1 / 0.02.
  → PhotoSift must store thresholds per embedding index.
- **Clean photos are stored as `NOT_JUNK`**, so later runs only process new photos.

Prompts (from `config/scoring_config.default.json`):

| Kind | Prompts |
|---|---|
| screenshot | "a screenshot of a phone user interface", "a screenshot of a computer screen", "a screen capture of a mobile app", "a screenshot of a web page", "a screenshot of a chat conversation" |
| document | "a scanned document", "a page of printed text", "a scanned paper document", "a photo of a printed page", "a page from a book" |
| receipt | "a close-up photo of a paper receipt", "a thermal paper cash register receipt", "a scanned invoice", "a utility bill with payment amounts" |
| meme | "a meme with overlaid text", "an internet meme", "a funny image with a caption", "a reaction image with bold text" |
| slide | "a presentation slide", "a powerpoint slide", "a lecture slide with bullet points", "a slide with a chart and text" |
| **not junk** | "a natural photograph", "a candid photo of people", "a landscape photograph", "a portrait of a person", "a photo of a real scene", "an ordinary snapshot", "a menu with prices in a restaurant or shop", "a close-up photo of a product package" |

### Grouping defaults (starting values, to tune on our library)

| Group | Setting |
|---|---|
| Burst | within 0.8 min, ≥70% similarity; rapid burst 0.4 s |
| Sequence | gap ≤3 s, hash distance ≤10, ≥3 frames |
| Scene | gap 20 min, adaptive (k = 6), 2–60 photos |
| Duplicate | ≥90% similarity |
| Similar group | ≥0.85 similarity, 2–50 photos |

Our "events" (a day, a trip) are bigger than Facet's scenes, so PhotoSift needs a second, larger gap level on top.

### Best frame in a burst (`processing/burst_score.py`)
A fixed weighted blend: overall 0.4, aesthetic 0.25, sharpness 0.2, not blinking 0.15
(eyes and expression available, weight 0 by default).

### Models and licences (`docs/THIRD_PARTY_MODELS.md`)
- Embeddings: `google/siglip2-so400m-patch16-naflex` on 16/24 GB profiles; OpenCLIP ViT-L-14 on 8 GB
  (Facet runs many models at once; SigLIP 2 alone may fit our 8 GB, to be tested).
- A **licence mode switch** swaps non-commercial models for permissive ones:
  YuNet + SFace replace InsightFace; Q-ReAlign replaces pyiqa scorers.
  → PhotoSift should keep a licence entry for every model.

### Immich integration (`docs/IMMICH.md`)
- REST API only; Immich sees the photos as a **read-only external library**.
- `path_map` translates between Facet's and Immich's paths; a wrong map is the most common failure.
- Pushes ratings, favourites, optional rejections and a top-picks album. Dry run first; needs Immich 3.0+.
- A lighter alternative (or complement) to copying files for export.

## cull: what to borrow
- Simple CLIP (ViT-B-32 default, ViT-L-14 recommended), 21 categories, 2–4 prompts each, threshold 0.3.
- `imagehash.dhash` for duplicates; dry run by default; custom categories in YAML.
- **Extra categories we hadn't planned:** wallpaper, downloaded/stock photo, text_heavy, low_quality, NSFW.
- Weaknesses: no comparison class, no margin, and it moves files.

## FolioSort: product ideas to borrow (no code available)
- **"Possible match" safety pattern:** exact duplicates are pre-selected, visual matches are shown *unticked*.
- **Best shot with a reason** ("eyes open", "eyes in focus"), confirmed by the user.
- **Screenshots kept out of the photo timeline** (receipts, memes, UI captures).
- **Blurriest-first view** with an adjustable sharpness slider.
- **Dates:** EXIF capture date first; fill missing dates from file or folder names; fix a camera whose clock was wrong.
- **Live Photos kept together** (HEIC + MOV).
- **Events** named by place + time, with an adjustable grouping interval.
- **Ghost tiles** for unplugged drives: the index stays browsable when the source is offline.
- **Export folder templates** with tokens, e.g. `{YYYY}/{MM}/{City}`, plus presets.
- **Google Takeout repair:** reads `.json` sidecars to recover the real date, GPS and people.
- **Checksum-verified copies** (XXHash64 / SHA-256).
- Later idea: a **read-only MCP server** so an AI assistant can search the library.
- **Don't copy:** FolioSort writes metadata *into* files. PhotoSift never touches originals;
  metadata goes only to exported copies or XMP sidecars.

## What none of them do (PhotoSift's edge)
- **Metadata rules for junk:** missing camera EXIF, WhatsApp/Screenshot filenames, phone-screen sizes.
- **A personal classifier** that learns from our Keep/Clutter decisions.
- **Bulk decisions on whole groups**, with sample and edge-case review.
- **Forwards repeated across chat folders** as a junk signal.

## Sources
- [Facet repository](https://github.com/ncoevoet/facet)
- [Facet junk_classifier.py](https://raw.githubusercontent.com/ncoevoet/facet/HEAD/models/junk_classifier.py)
- [Facet scoring_config.default.json](https://raw.githubusercontent.com/ncoevoet/facet/HEAD/config/scoring_config.default.json)
- [Facet THIRD_PARTY_MODELS.md](https://raw.githubusercontent.com/ncoevoet/facet/HEAD/docs/THIRD_PARTY_MODELS.md)
- [Facet IMMICH.md](https://raw.githubusercontent.com/ncoevoet/facet/HEAD/docs/IMMICH.md)
- [Facet burst_score.py](https://raw.githubusercontent.com/ncoevoet/facet/HEAD/processing/burst_score.py)
- [cull repository](https://github.com/Yogendra96/cull)
- [cull categories.py](https://raw.githubusercontent.com/Yogendra96/cull/main/cull/categories.py)
- [FolioSort repository](https://github.com/tiagotrindade/FolioSort)
