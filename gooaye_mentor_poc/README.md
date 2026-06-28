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
│   ├── review/
│   ├── benchmark/
│   ├── reports/
│   └── maintenance/
├── config/
├── docs/
├── data/
├── reports/
└── requirements.txt
```

## POC Flow

```text
RSS / manifest
  -> EP674 MP3 + audio metadata
  -> raw faster-whisper transcript
  -> Traditional Chinese normalization
  -> deterministic merged utterances
  -> merge integrity validation
  -> minimal topic/classification/routing POC
  -> concise human review file
```

The raw transcript is immutable evidence. Normalized, merged, topic, classification, and routing outputs are derived artifacts and can be regenerated.

## Main Entry Point

Run commands from this directory:

```bash
python3 main.py validate-merge --episode EP674 --configuration large-v3-turbo
python3 main.py normalize --episode EP674 --configuration large-v3-turbo
python3 main.py topic-review-poc --episode EP674 --configuration large-v3-turbo
python3 -m py_compile main.py modules/*.py modules/*/*.py
```

## Important Files

```text
data/transcripts/EP674/large-v3-turbo/transcript.json
```

Canonical raw ASR transcript. Do not overwrite.

```text
data/transcripts/EP674/large-v3-turbo/normalized_*.json
data/transcripts/EP674/large-v3-turbo/merged_transcript.json
data/topic_segments/EP674/topic_segments.json
```

Current derived transcript and topic POC outputs.

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

- POC results: `docs/local-transcription-poc.md`
- Data source notes: `docs/data-source-notes.md`
- Troubleshooting: `docs/troubleshooting.md`
- Project-wide docs: `../docs/`
