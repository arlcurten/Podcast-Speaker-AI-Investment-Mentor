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

## Simplicity And Organization Rules

- Keep the repository easy to read before making it more complete.
- Start from `README.md`; split details into separate docs only when the README becomes too long or the topic is independently useful.
- Prefer fewer, clearer modules over many tiny modules. Split a module only when a step has a distinct responsibility, reusable implementation, or likely replacement path.
- The Local POC is a sandbox/playground. Do not couple future production architecture to POC internals unless the user explicitly promotes a component.
- Prefer one canonical artifact per stage. Avoid producing JSON, JSONL, CSV, and Markdown versions of the same result unless each has a real consumer.
- Merge small configuration or terminology files when one shared source of truth is clearer. Split them later if maintenance pressure appears.
- Temporary experiments may be created while investigating. Once they are no longer useful, either remove them or move useful references into `legacy`/`tmp` rather than leaving them in the active path.
- Do not keep obsolete scripts in the main workflow. If they remain useful as references, move them under a clearly named legacy location.
- Keep human-facing reports concise and reviewable; do not generate a separate report for every intermediate JSON file.

## Documentation Index

- `docs/architecture.md`: full long-term architecture.
- `docs/data-model.md`: project data layers and invariants.
- `docs/cloud-processing-plan.md`: future cloud transcription plan.
- `docs/future-improvements.md`: deferred optimization ideas.
- `docs/terminology.tsv`: shared terminology table for preserved terms, aliases, and corrections.
- `gooaye_mentor_poc/poc_docs/local-transcription-poc.md`: Local POC transcription results.
- `gooaye_mentor_poc/poc_docs/data-source-notes.md`: RSS, SoundOn, Apple lookup, and manifest notes.
- `gooaye_mentor_poc/poc_docs/troubleshooting.md`: POC troubleshooting and known failure modes.

For Local POC operation rules, also read `gooaye_mentor_poc/AGENTS.md`.
