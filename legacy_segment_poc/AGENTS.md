# Local POC Agent Guide

This directory is the Local POC for the full Podcast Speaker AI Investment Mentor project. Root project rules live in `../AGENTS.md`; this file adds POC-specific constraints.

Full project root:

```text
/home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor
```

Local POC directory:

```text
/home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc
```

Work in the Local POC directory unless the user explicitly asks for changes at the full project root.

## Local POC Scope

This POC verifies the podcast ingestion and transcription pipeline for Gooaye 股癌:

- RSS discovery and ingestion
- episode manifest generation
- selected MP3 download
- audio validation
- local transcription benchmark
- manual transcript review preparation

Do not implement these unless explicitly requested: RAG, embeddings, AI Agent, personality modeling, fine-tuning, full content classification, full cloud batch, or automated investment analysis.

## Current Environment

Verified from `data/environment_inspection.json`:

- WSL2 Linux
- CPU: 12th Gen Intel(R) Core(TM) i7-12700H
- RAM visible to WSL at inspection: 7.59 GB total
- GPU: NVIDIA GeForce RTX 3050 Laptop GPU
- VRAM: 4096 MB
- CUDA available according to Torch and `nvidia-smi`
- ffmpeg and ffprobe available
- recommended local backend: faster-whisper CUDA

Treat this as an inspection snapshot, not a permanent guarantee. Re-run `python3 main.py inspect-env` when environment drift matters.

## GPU Rules

- `large-v3-turbo` completed one full EP674 transcription locally.
- `large-v3` CUDA tests on this 4 GB GPU hit out-of-memory in tested partial configurations; full-episode local `large-v3` is conditional/no-go.
- CPU int8 `large-v3` is too slow for production-scale work and should only be used for smoke tests.
- If a game or unrelated process is using the GPU, do not start benchmarks or transcription.
- Runtime measured under GPU contention is not a clean performance baseline.
- Before any GPU work, run `nvidia-smi` and record whether other GPU processes are present.
- Do not start a new full transcription, CUDA benchmark, or model comparison without explicit user approval.

## RSS And Source Rules

Current active RSS:

```text
https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml
```

The old SoundOn feed returned HTTP 404 during this POC. RSS URLs are not permanent. If a configured feed fails, use Apple Podcasts lookup or the existing discovery path, verify the channel title, and preserve provenance:

- requested URL
- resolved URL
- discovery method
- fetch timestamp
- channel title
- Apple collection ID when available

Do not treat an Apple Podcasts show page URL as an RSS feed.

## Audio Validation Rules

SoundOn RSS `<enclosure length>` may be an invalid placeholder. EP674 had `rss_enclosure_length = 1`.

Do not fail a download solely because RSS enclosure length is unreasonable. Validate in this order:

1. HTTP Content-Length
2. actual file size
3. ffprobe parse/decode success
4. SHA-256

Store these separately: `rss_enclosure_length`, `rss_length_valid`, `http_content_length`, `actual_file_size`, `sha256`.

## Download Rules

- Download only the episode the user explicitly requested.
- Do not auto-download the full catalog.
- Use streaming download and a `.part` temporary file.
- Use timeouts, bounded retries, and backoff.
- Do not load a complete MP3 into RAM.
- Validate Content-Type, final size, SHA-256, and ffprobe output.
- Avoid re-downloading completed files unless the user asks.
- Do not rename a partial file to `episode.mp3` before validation completes.

## Transcript Rules

- Raw model output must be preserved.
- Do not overwrite raw transcripts.
- Merged, normalized, corrected, classified, or topic-segmented outputs are derived artifacts.
- Do not silently rewrite raw transcript text with an LLM.
- Preserve source segment IDs, timestamps, audio SHA-256, model, device, compute type, parameters, and package versions.
- EP674 `large-v3-turbo` transcript has not completed human quality review.
- Fluent-looking transcript text is not proof of correctness for entities, tickers, numbers, negation, or market direction.

## Testing Rules

Default to CPU-safe validation:

```bash
python3 -m py_compile main.py modules/*.py modules/*/*.py
```

Do not start GPU tests, cloud resources, or multi-episode batches without explicit user approval. Do not hide errors; scripts should use non-zero exit codes for failures.

## Large And Generated Files

Do not commit by default:

- MP3, WAV, M4A, and `.part` files
- model caches and downloaded weights
- transcript outputs unless intentionally versioned
- large benchmark artifacts
- `__pycache__` and `*.pyc`
- secrets, credentials, and `.env`
- generated RSS snapshots and manifests unless intentionally versioned

## POC Simplification Rules

- Keep `main.py` as the small active workflow surface. Only expose commands that are still part of the current POC path.
- Mark important active commands with `#!!` when the user has identified them as core steps. These commands may be improved or combined with validation, but should not be removed casually.
- Keep modules coarse enough to be understandable: source acquisition, transcript processing, segmentation/review, report building, and legacy/reference utilities are usually enough.
- Avoid splitting a step into separate audit, validate, convert, and report commands when the check naturally belongs at the end of the producing step.
- Prefer this pattern:
  - `download` also validates audio metadata.
  - `transcribe` also writes the basic segment audit.
  - `merge` also validates raw-to-merged mapping.
  - `normalize` reads the shared terminology table.
- Keep one current canonical output per stage when possible. Do not accumulate timestamped variants unless the user asks for an experiment history.
- If a script or output was only for a completed or failed experiment, remove it if it has no reference value. If it may be useful later, move it to `modules/legacy/` or an ignored `tmp/` location.
- Do not create a new config file when an existing shared table can represent the same information clearly. Current shared terminology lives at `../docs/terminology.tsv`.
- Generated test/debug data should not sit beside active source-of-truth artifacts once the investigation is over.

## Module Layout

- `main.py`: single POC entrypoint and command dispatcher.
- `modules/source/`: environment, RSS, manifest, download, and audio inspection.
- `modules/transcript/`: transcription, automatic audit, merge with validation, and normalization.
- `modules/segmentation/`: topic segmentation, classification, routing, and review package helpers.
- `modules/build_reports/`: report generation from existing artifacts.
- `modules/legacy/`: old benchmark, comparison, and migration repair utilities kept for reference.

## Documentation Index

- `README.md`: Local POC file map and entry points.
- `reports/EP674_human_review.md`: current concise human review package.
- `reports/legacy/pending_gpu_benchmarks.md`: historical local GPU benchmark notes.
- `poc_docs/local-transcription-poc.md`: EP674 transcription POC results.
- `poc_docs/data-source-notes.md`: RSS, SoundOn, Apple Podcasts, manifest, and enclosure-length notes.
- `poc_docs/troubleshooting.md`: symptom-driven POC fixes.
- `../docs/architecture.md`: project-wide architecture.
- `../docs/data-model.md`: project-wide data model.
- `../docs/cloud-processing-plan.md`: future cloud pilot strategy.
