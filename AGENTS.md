# Project Agent Guide

This repository is the full Podcast Speaker AI Investment Mentor project.

Current workspace layout:

- `legacy_segment_poc/`: completed Phase 1 transcription POC. Treat as frozen reference except for explicitly requested bug fixes.
- `semantic_analysis_poc/`: active Phase 2 Semantic Extraction POC workspace.

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
Phase 1 — Transcription POC
→ Phase 2 — Semantic Extraction POC
→ Phase 3 — Model/API Selection
→ Phase 4 — 3 to 20 Episode Pilot
→ Phase 5 — Full Corpus Processing
→ Phase 6 — Cross-Episode Knowledge
→ Phase 7 — Mentor MVP
→ Phase 8 — Advanced Version
```

Current phase:

```text
Phase 2 — Semantic Extraction POC
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
- Phase 2 semantic extraction should be LLM-based, whole-episode-aware, evidence-grounded, hierarchical, and multi-tag.
- Do not use deterministic keyword segmentation, fixed episode-position weighting, fixed-duration windows, or single-label classification as the main semantic method.
- Level 3 is optional. Do not force a full episode synthesis, central theme, or unified narrative when Level 1 recommends `light_consolidation` or `bypass`.
- Terminology corrections are annotations/context and must not destructively rewrite raw transcript evidence.

## Simplicity And Organization Rules

- Keep the repository easy to read before making it more complete.
- Start from `README.md`; split details into separate docs only when the README becomes too long or the topic is independently useful.
- Prefer fewer, clearer modules over many tiny modules. Split a module only when a step has a distinct responsibility, reusable implementation, or likely replacement path.
- POC folders are sandbox/playground areas. Do not couple future production architecture to POC internals unless the user explicitly promotes a component.
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
- `legacy_segment_poc/poc_docs/local-transcription-poc.md`: Phase 1 transcription POC results.
- `legacy_segment_poc/poc_docs/data-source-notes.md`: RSS, SoundOn, Apple lookup, and manifest notes.
- `legacy_segment_poc/poc_docs/troubleshooting.md`: Phase 1 troubleshooting and known failure modes.
- `semantic_analysis_poc/README.md`: active Phase 2 workspace overview.

For Phase 2 operation rules, also read `semantic_analysis_poc/AGENTS.md`. For Phase 1 reference rules, read `legacy_segment_poc/AGENTS.md`.
