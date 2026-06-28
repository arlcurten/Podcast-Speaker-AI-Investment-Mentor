# Gooaye Podcast Speaker AI Mentor Local POC

This folder is the Local POC for the Gooaye 股癌 Podcast Speaker AI Investment Mentor project.

Full project root:

```text
/home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor
```

Local POC directory:

```text
/home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc
```

The current phase covers RSS discovery, RSS ingestion, episode manifest generation, selected MP3 download, audio validation, local transcription benchmarking, segment audit, deterministic merged utterances, and manual transcript review preparation.

It intentionally does not build RAG, embeddings, vector databases, AI agents, speaker personality models, fine-tuning, full cloud batch processing, or production content classification.

## Current POC Episode

EP674 is the completed Local POC episode. It has downloaded audio, ffprobe validation, SHA-256 metadata, a full `large-v3-turbo` transcript, segment audit, deterministic merged transcript, and manual review package.

EP672 is only a manifest/discovery sanity-check example from earlier work. Do not treat EP672 as the completed transcription POC unless new artifacts are explicitly generated for it.

## Quick Start

```bash
cd /home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install ffmpeg if needed:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

## Common CPU-Safe Commands

Inspect the local machine:

```bash
python scripts/inspect_environment.py
```

Fetch RSS and build manifests:

```bash
python scripts/fetch_rss.py
```

The original requested SoundOn UUID returned HTTP 404 during this POC. The current feed was resolved via Apple Podcasts lookup for collection ID `1500839292`:

```bash
python scripts/fetch_rss.py \
  --rss-url https://feeds.soundon.fm/podcasts/4f2a74ec-cc7a-4284-be4b-74b882da701c \
  --apple-fallback
```

Download one explicitly selected episode from the manifest:

```bash
python scripts/download_episode.py --episode EP674
```

Inspect the downloaded audio:

```bash
python scripts/inspect_audio.py --episode EP674
```

Build the local report:

```bash
python scripts/build_poc_report.py
```

Run the default CPU-safe validation:

```bash
python3 -m py_compile scripts/*.py
```

## GPU Work

Do not start transcription, CUDA benchmarks, or model comparisons unless the user explicitly approves. Before any GPU work, check:

```bash
nvidia-smi
```

The EP674 `large-v3-turbo` full run completed locally, but its runtime is not a clean baseline if other GPU workload was present. Local `large-v3` CUDA tests on the 4 GB RTX 3050 hit out-of-memory in tested partial configurations.

A fixed benchmark clip has been prepared at `data/benchmarks/EP674/benchmark_clip.mp3`, but the clean benchmark was not started because repeated `nvidia-smi` samples showed non-idle GPU utilization. See [reports/clean_gpu_benchmark.md](reports/clean_gpu_benchmark.md).

## Segment Audit And Manual Review

Raw ASR output is preserved. Existing EP674 artifacts include raw transcript, deterministic merged transcript, segment audit, manual review CSV, and review clips.

To regenerate the audit and merged layer without changing raw transcript text:

```bash
python scripts/audit_transcript_segments.py --episode EP674 --configuration large-v3-turbo
python scripts/merge_transcript_segments.py --episode EP674 --configuration large-v3-turbo
```

The merged transcript is not topic segmentation. It only joins adjacent raw ASR segments using fixed gap, duration, and length limits, preserves source segment IDs, and does not rewrite text.

Prepare review windows:

```bash
python scripts/prepare_review_package.py --episode EP674 --configuration large-v3-turbo --create-clips
```

Then open `data/evaluation/manual_review.csv` and fill one or more rows per observed issue:

- `expected_text`: what you hear in the audio.
- `transcribed_text`: the ASR text being judged.
- `error_category`: one of Chinese substitution, English company-name error, Ticker error, Financial terminology error, Number error, Negation error, Missing words, Hallucination, Repetition, Segmentation, Timestamp drift, Other.
- `severity`: minor, moderate, or critical.
- `semantic_impact`: none, low, changes_entity, changes_number, changes_sentiment, changes_reasoning, or unknown.
- `notes`: brief explanation and any uncertainty.

Do not mark transcript quality as accepted until sampled windows have been checked against the source audio.

## Documentation

Coding agents should read [AGENTS.md](AGENTS.md) before making changes.

Detailed notes:

- [../docs/architecture.md](../docs/architecture.md): project-wide pipeline and completion state.
- [../docs/data-model.md](../docs/data-model.md): project-wide raw and derived data layers.
- [../docs/cloud-processing-plan.md](../docs/cloud-processing-plan.md): future RunPod/Vast.ai/cloud pilot plan.
- [../docs/poc/data-source-notes.md](../docs/poc/data-source-notes.md): RSS, SoundOn, Apple Podcasts, manifest, and enclosure-length behavior.
- [../docs/poc/local-transcription-poc.md](../docs/poc/local-transcription-poc.md): EP674 transcription results and benchmark limitations.
- [../docs/poc/troubleshooting.md](../docs/poc/troubleshooting.md): symptoms, causes, checks, and recommended handling.
- [reports/manual_review_guide.md](reports/manual_review_guide.md): how to perform the EP674 manual listening review.

## Data Not To Commit

Do not commit MP3 files, `.part` files, model caches, transcript outputs, `__pycache__`, secrets, credentials, or large generated benchmark artifacts unless the user explicitly decides to version them. See `.gitignore`.

## Cleanup

Remove local audio and transcripts:

```bash
rm -rf data/audio data/transcripts
```

Remove common model caches:

```bash
rm -rf ~/.cache/huggingface ~/.cache/torch ~/.cache/ctranslate2
```

## Known Limits

- Manual transcript quality review is required; scripts do not fabricate listening judgments.
- CPU transcription with large-v3 can be very slow. Use partial-audio benchmarks if full episodes are not practical.
- Cloud GPU timing in the report is an extrapolation unless a real cloud pilot is executed.
- `terminology.txt` is only used as an initial prompt. Raw transcripts are not force-rewritten.
- Troubleshooting details are in [../docs/poc/troubleshooting.md](../docs/poc/troubleshooting.md).
