# Project Agent Guide

This repository is the full Podcast Speaker AI Investment Mentor project. The active implementation today is the Local POC in `gooaye_mentor_poc/`.

## Project Purpose

The long-term goal is to build an AI Investment Mentor from historical Gooaye 股癌 podcast content. The system should learn patterns in:

- market and industry analysis logic
- decision process and reasoning
- risk attitude
- uncertainty handling
- exception conditions
- view revision
- teaching style
- cross-case behavioral patterns

The goal is not automated trading and not direct order placement.

## Project Phases

```text
Data acquisition
→ transcription
→ transcript quality validation
→ topic segmentation
→ reasoning-case extraction
→ behavioral pattern modeling
→ hierarchical retrieval
→ Mentor Agent
```

Current phase:

```text
Local transcription POC / transcript validation
```

## Global Invariants

- Raw data is never overwritten by derived artifacts.
- Every derived layer must trace back to episode, timestamp, and source IDs.
- Preserve audio SHA-256, model, config, package versions, and run metadata.
- Do not treat historical market commentary as current investment advice.
- Do not encode the speaker model as a fixed unconditional personality.
- Do not build a full Agent, fine-tuning workflow, RAG, embeddings, or vector database unless explicitly requested.
- Separate planning estimates from measured results.
- Prefer project-relative paths for repository artifacts.

## Documentation Index

- `docs/architecture.md`: full long-term architecture.
- `docs/data-model.md`: project data layers and invariants.
- `docs/cloud-processing-plan.md`: future cloud transcription plan.
- `docs/future-improvements.md`: deferred optimization ideas.
- `docs/terminology.tsv`: shared terminology table for preserved terms, aliases, and corrections.
- `gooaye_mentor_poc/docs/local-transcription-poc.md`: Local POC transcription results.
- `gooaye_mentor_poc/docs/data-source-notes.md`: RSS, SoundOn, Apple lookup, and manifest notes.
- `gooaye_mentor_poc/docs/troubleshooting.md`: POC troubleshooting and known failure modes.

For Local POC operation rules, also read `gooaye_mentor_poc/AGENTS.md`.
