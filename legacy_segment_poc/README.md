# Gooaye Mentor Local POC

This folder is a sandbox/playground for the Local POC. It is intentionally separate from the future production project.

The POC validates one episode, EP674, through ingestion, audio validation, transcription, normalization, deterministic merging, minimal topic review, and human review preparation.

## Folder Hierarchy

```text
gooaye_mentor_poc/
├── AGENTS.md
├── README.md
├── main.py
├── modules/
│   ├── source/
│   ├── transcript/
│   ├── segmentation/
│   ├── build_reports/
│   └── legacy/
├── config/
├── poc_docs/
├── data/
│   ├── rss_sources/
│   ├── audio/
│   ├── transcripts/
│   ├── topic_segments/
│   ├── evaluation/
│   └── legacy/
├── reports/
└── requirements.txt
```

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

The raw transcript is immutable evidence. Normalized, merged, topic, classification, and routing outputs are derived artifacts and can be regenerated.

## Main Entry Point

Run commands from this directory:

```bash
python3 main.py merge --episode EP674 --configuration large-v3-turbo
python3 main.py normalize --episode EP674 --configuration large-v3-turbo
python3 main.py topic-review-poc --episode EP674 --configuration large-v3-turbo
python3 -m py_compile main.py modules/*.py modules/*/*.py
```

## Important Files

```text
data/rss_sources/episodes.jsonl
data/rss_sources/rss_ingestion_metadata.json
data/rss_sources/rss_snapshot_*.xml
```

RSS snapshot, episode manifest, and feed provenance.

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

This POC does not implement production RAG, embeddings, vector database, Mentor Agent, fine-tuning, cloud orchestration, or multi-episode batch processing.

## More Detail

- POC results: `poc_docs/local-transcription-poc.md`
- Data source notes: `poc_docs/data-source-notes.md`
- Troubleshooting: `poc_docs/troubleshooting.md`
- Project-wide docs: `../docs/`
