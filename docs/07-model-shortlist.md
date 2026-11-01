# 07 · Model shortlist

> Research notes, 2026-11-01. Nothing here has been benchmarked on our library yet.

## Goals this shortlist serves

1. Remove junk from the library.
2. Group events / same timeline.
3. Put document-like images in one section. **No OCR needed.**

## Core stack: 3 models + rules

```
Rules (EXIF, filenames, blur, pHash, time gaps)   ← free, every image
SigLIP 2 (or MobileCLIP2)                          ← every image, once
YuNet faces                                        ← every image, CPU
D3 Nano                                            ← only uncertain/important images
+ a small personal classifier trained on our own Keep/Clutter labels
```

## By goal

| Goal | Use | Notes |
|---|---|---|
| Main embedding | **SigLIP 2**: compare `base-patch16-384`, `so400m-patch14-384` and `so400m-patch16-naflex` | Beats SigLIP 1 at every size, biggest gains at base. NaFlex keeps the native aspect ratio, which may help tall screenshots and documents. Facet uses the naflex so400m. |
| Lighter alternative | **MobileCLIP2**: S2 on Zima's CPU, S4 on the laptop | Apple reports S4 ≈ SigLIP-SO400M accuracy at about half the parameters. Weights use the `apple-amlr` licence, so read the terms. |
| Junk: blur, dark, pocket shots | **No model**: Laplacian blur, brightness, resolution, plus SigLIP prompts | Consider testing `nurhikam/BlurOrBokeh`: plain blur scores wrongly flag portraits with a soft background. |
| Junk: forwards, memes, screenshots | **No model**: no camera EXIF, WhatsApp/Screenshot filenames, phone-screen sizes, the same image repeated across chat folders, plus SigLIP zero-shot | Neither Facet nor cull uses EXIF; this is our edge. |
| Near-duplicates / bursts | **pHash/dHash** + SigLIP similarity + time window | **DINOv2-small** in reserve if SigLIP merges distinct burst shots. (DINOv2 ≠ DINOv3 ≠ D3 Nano.) |
| Protect people | **YuNet** (OpenCV Zoo) | Faces present → never suggest Clutter. Avoid InsightFace/SCRFD weights (non-commercial research only). |
| Documents section | **SigLIP zero-shot** + D3 Nano for uncertain ones | Optional later step: `microsoft/dit-base-finetuned-rvlcdip` to sort document *types* inside the section; it cannot tell photos from documents. |
| Uncertain / important | **D3 Nano** (keep) | If it disappoints, compare explicitly with Qwen2.5-VL-3B (strongest small) or SmolVLM2-2.2B (lightest). Never switch silently. |
| Events / timeline | **No model**: capture-time gaps → GPS → SigLIP similarity to merge/split | Capture date: EXIF first, then filename date. Forwards without EXIF stay out of events. |

## The "specific model" we should build: a personal classifier

Downloaded task-specific models are mostly weak (see below). The strongest one is ours:

1. SigLIP 2 embeds every photo once.
2. Label a few hundred examples per group: meme/greeting, screenshot, document, junk, personal.
3. Train a tiny classifier (logistic regression) on the embeddings. It takes seconds on CPU and needs no extra GPU pass.
4. Retrain as Keep/Clutter decisions accumulate. Tie it to one embedding index.

## Dropped or deferred

| Model | Decision | Why |
|---|---|---|
| Florence-2 base-ft | Drop from core (optional extra) | Its main value is OCR, which we don't need; captions and detection overlap with SigLIP, YuNet and D3 Nano. |
| aesthetic-predictor-v2.5 | Skip | Built on **SigLIP 1** so400m (a second embedding pass) and AGPL-3.0. |
| Q-Align / pyiqa scorers | Skip | Heavy, batch size 1; pyiqa is non-commercial. Photographer-grade aesthetics aren't our goal. |

## Task-specific models found (and why not)

| Task | Model | Verdict |
|---|---|---|
| Meme or not | `Hrishikesh332/autotrain-meme-classification` | ❌ ~330 training images, claims 100% accuracy; about 3.5 years old |
| Screenshot | `al-css/Screenshots_detection_to_classification`, `hur03/capturemate-image-classifier-v1-5class` | ⚠️ No published accuracy; metadata rules do this better |
| Blur | `WT-MM/vit-base-blur` | ❌ "Blurry" means unfinished AI-generated images, not camera shake |
| Blur vs bokeh | `nurhikam/BlurOrBokeh` | ⚠️ Worth testing; sparse model card |
| Document type | `microsoft/dit-base-finetuned-rvlcdip` | ✅ Solid, but only sorts document types |

## Sources
- [SigLIP 2 paper](https://arxiv.org/pdf/2502.14786)
- [SigLIP 2 overview](https://www.emergentmind.com/topics/siglip-siglip2)
- [MobileCLIP2-S2 card](https://huggingface-gw-kz.just-ai.com/apple/MobileCLIP2-S2)
- [MobileCLIP2 OpenCLIP card](https://huggingface.co/timm/MobileCLIP2-S0-OpenCLIP/blob/main/README.md)
- [MobileCLIP2 licence](https://huggingface.co/apple/mobileclip2_coca_dfn2b_s13b_context77/blob/main/README.md)
- [Near-duplicate detection paper](https://arxiv.org/pdf/2203.07167)
- [Embedding dedup guide](https://mixpeek.com/guides/how-to-find-duplicate-photos-including-edited-copies)
- [Face model licences](https://fullstackcv.github.io/playbook/face/)
- [SmolVLM](https://huggingface.co/blog/smolvlm)
- [Local VLMs (Roboflow)](https://blog.roboflow.com/local-vision-language-models/)
- [aesthetic-predictor-v2.5](https://github.com/discus0434/aesthetic-predictor-v2-5)
- [pyiqa Q-Align](https://iqa-pytorch.readthedocs.io/en/stable/_modules/pyiqa/archs/qalign_arch.html)
- [Meme classifier](https://huggingface.co/Hrishikesh332/autotrain-meme-classification-42897109437)
- [Screenshot classifier](https://huggingface.co/al-css/Screenshots_detection_to_classification/tree/main)
- [capturemate classifier](https://huggingface.co/hur03/capturemate-image-classifier-v1-5class/discussions)
- [vit-base-blur](https://huggingface.co/WT-MM/vit-base-blur/blob/main/README.md)
- [BlurOrBokeh](https://huggingface.co/nurhikam/BlurOrBokeh/blob/main/README.md?code=true)
- [DiT RVL-CDIP](https://huggingface.co/microsoft/dit-base-finetuned-rvlcdip/raw/bba47a64376ad3bc4727ba63c0cc26395f1a88d4/README.md)
