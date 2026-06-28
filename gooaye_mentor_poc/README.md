# Gooaye Mentor Local POC

This folder is the Local POC for the Gooaye 股癌 Podcast Speaker AI Investment Mentor project.

Full project root:

```text
/home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor
```

Local POC root:

```text
/home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor/gooaye_mentor_poc
```

Current scope: one-episode EP674 ingestion, audio validation, raw transcription, normalization, deterministic merge, minimal topic/classification/routing POC, and human review preparation.

Out of scope unless explicitly requested: RAG, embeddings, vector database, Mentor Agent, fine-tuning, cloud orchestration, new episode download, or new GPU benchmark.

## What To Review Now

Use this single file:

```text
reports/EP674_human_review.md
```

It contains 10 selected topic segments and a compact review form. The goal is not to check every ASR word. Judge whether topic boundaries and classification preserve useful investment reasoning.

## File Map

| Role | Current path | Commit? | Can regenerate? | Notes |
|---|---|---:|---:|---|
| POC rules | `AGENTS.md` | yes | no | Local agent instructions. |
| Scripts | `scripts/*.py` | yes | no | Reusable pipeline code only. |
| Config | `config/*.yaml`, `config/*.txt`, `config/*.tsv` | yes | no | Canonical settings, terminology, thresholds. |
| RSS source metadata | `data/rss/`, `data/manifests/`, `data/environment_inspection.json` | mixed | yes/partly | Source evidence for ingestion; XML/manifest files may be ignored if bulky. |
| Audio source evidence | `data/audio/EP674/audio_metadata.json`, `download_metadata.json` | usually yes | partly | MP3 itself is ignored; metadata records size/hash/ffprobe. |
| Raw transcript | `data/transcripts/EP674/large-v3-turbo/transcript.json` | no by default | expensive | Immutable ASR output. Do not overwrite. |
| Normalized transcript | `data/transcripts/EP674/large-v3-turbo/normalized_*.json` | no by default | yes | Derived OpenCC + glossary layer. |
| Merged transcript | `data/transcripts/EP674/large-v3-turbo/merged_transcript.json` | no by default | yes | Derived deterministic readability layer. |
| Current topic POC | `data/topic_segments/EP674/topic_segments.json` | optional | yes | Canonical machine-readable topic/classification output for this POC. |
| Routing output | `data/topic_segments/EP674/routing.jsonl` | optional | yes | JSONL is only useful if a downstream streaming/indexing consumer appears. |
| Human review | `reports/EP674_human_review.md` | yes | yes | Main review artifact for the user. |
| Historical reports | `reports/local_poc_report.md`, `reports/clean_gpu_benchmark.md` | optional | partly | Useful references, not day-to-day source of truth. |
| Benchmark/tmp outputs | `data/benchmarks/`, review clips, `__pycache__/` | no | yes | Keep ignored; move future scratch into `tmp/`. |

## Rebuild Current Derived Outputs

Run from this directory:

```bash
python scripts/merge_transcript_segments.py --episode EP674 --configuration large-v3-turbo
python scripts/validate_merged_transcript.py --episode EP674 --configuration large-v3-turbo
python scripts/normalize_transcript.py --episode EP674 --configuration large-v3-turbo
python scripts/build_topic_segments.py --episode EP674 --configuration large-v3-turbo
python3 -m py_compile scripts/*.py
```

This sequence does not regenerate the raw transcript, download audio, or run GPU work.

## Important Source Inputs

- Episode manifest: `data/manifests/episodes.jsonl`
- Audio metadata/hash: `data/audio/EP674/audio_metadata.json`
- Raw transcript: `data/transcripts/EP674/large-v3-turbo/transcript.json`
- Known raw transcript facts:
  - segment count: `2493`
  - segment hash: `83e80be92c616a63a56bbfa842566029e24e4be62730d4a1ba716cfe5fa6f89f`

## Generated Files That Can Be Deleted And Rebuilt

Safe to delete only if you are intentionally cleaning derived outputs:

- `data/transcripts/EP674/large-v3-turbo/merged_transcript.*`
- `data/transcripts/EP674/large-v3-turbo/normalized_*.json`
- `data/evaluation/EP674_merge_integrity.json`
- `data/evaluation/EP674_topic_segmentation_summary.json`
- `data/topic_segments/EP674/`
- `reports/EP674_merge_integrity.md`
- `reports/EP674_topic_segmentation_review.md`

Do not delete raw transcripts, MP3 files, or benchmark artifacts without explicit user confirmation.

## Documentation

- Project-wide rules: `../AGENTS.md`
- Local rules: `AGENTS.md`
- Architecture: `../docs/architecture.md`
- Data model: `../docs/data-model.md`
- Future improvements: `../docs/future-improvements.md`
- POC details: `../docs/poc/local-transcription-poc.md`
- Troubleshooting: `../docs/poc/troubleshooting.md`
