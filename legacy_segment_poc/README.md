# Gooaye Mentor Local POC

This folder is the completed Phase 1 transcription reference plus the reusable transcription utilities for the next full-corpus expansion.

The POC validates one episode, EP674, through ingestion, audio validation, transcription, normalization, deterministic merging, minimal topic review, and human review preparation.

The new batch commands wrap the existing Phase 1 ingestion/download/faster-whisper code for RunPod-ready transcription. They do not change the completed EP674 reference artifacts.

## Folder Hierarchy

```text
legacy_segment_poc/
├── AGENTS.md
├── README.md
├── main.py
├── modules/
│   ├── batch/
│   ├── source/
│   ├── transcript/
│   ├── segmentation/
│   ├── build_reports/
│   └── legacy/
├── config/
├── poc_docs/
└── requirements.txt
```

Complete audio, transcripts, and batch outputs are private artifacts and should live under the configured private data roots, not in this public source tree.

## POC Flow

```text
RSS / manifest
  -> EP674 MP3 + audio metadata
  -> raw faster-whisper transcript
  -> deterministic merged utterances + integrity validation
  -> Traditional Chinese normalization
  -> minimal topic/classification/routing POC
  -> concise human review file
```

Batch transcription flow:

```text
episode list
  -> exact episode metadata resolution
  -> audio download or reuse
  -> faster-whisper raw transcript
  -> output validation
  -> checkpoint / resume / retry
  -> private batch summary
```

The raw transcript is immutable evidence. Normalized, merged, topic, classification, and routing outputs are derived artifacts and can be regenerated.

## Main Entry Point

Run commands from this directory:

```bash
python3 main.py batch-plan --config config/transcription_batch.example.yaml --dry-run
python3 main.py batch-status --config config/transcription_batch.example.yaml
python3 main.py merge --episode EP674 --configuration large-v3-turbo
python3 main.py normalize --episode EP674 --configuration large-v3-turbo
python3 main.py topic-review-poc --episode EP674 --configuration large-v3-turbo
python3 -m py_compile main.py modules/*.py modules/*/*.py
```

Batch transcription is RunPod-ready but should not be executed locally or on GPU without explicit approval. See `../docs/runpod_transcription.md`.

## Important Files

```text
data/rss_sources/episodes.jsonl
data/rss_sources/rss_ingestion_metadata.json
data/rss_sources/rss_snapshot_*.xml
```

RSS snapshot, episode manifest, and feed provenance.

For new batch runs, equivalent complete manifests and outputs should be read from or written to the private repository through `PODCAST_DATA_ROOT` and `PIPELINE_DATA_ROOT`.

```text
data/transcripts/EP674/raw_large-v3-turbo_transcript.json
```

Canonical raw ASR transcript. Do not overwrite.

```text
data/transcripts/EP674/derived_large-v3-turbo_merged_transcript.json
data/transcripts/EP674/derived_large-v3-turbo_normalized_merged_transcript_zh_tw.json
data/topic_segments/EP674/topic_segments.json
```

Current derived transcript and human-review reference outputs.

```text
data/legacy/
```

Historical benchmark and comparison artifacts. These are not part of the active POC workflow.

```text
reports/EP674_human_review.md
```

Current human review file.

```text
../docs/terminology.tsv
```

Shared terminology table for transcription prompts, normalization aliases, and human-reviewed corrections.

## Not In Scope Here

This folder does not implement production RAG, embeddings, vector database, Mentor Agent, fine-tuning, semantic extraction, or distributed cloud orchestration. RunPod batch transcription is single-GPU faster-whisper execution only.

## More Detail

- POC results: `poc_docs/local-transcription-poc.md`
- Data source notes: `poc_docs/data-source-notes.md`
- Troubleshooting: `poc_docs/troubleshooting.md`
- Project-wide docs: `../docs/`
