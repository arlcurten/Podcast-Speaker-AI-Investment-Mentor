# Podcast Speaker AI Investment Mentor

This project explores how to build an AI Investment Mentor from historical Gooaye 股癌 podcast episodes.

The goal is not automated trading or order placement. The long-term goal is to preserve and model how the speaker reasons about markets, industries, companies, risk, uncertainty, exceptions, position changes, and behavior across cases.

## Quick Overview

Current status:

- Phase 1 Transcription POC is complete and preserved as a stable reference in `legacy_segment_poc/`.
- Phase 2 Semantic Extraction POC is implemented in `semantic_analysis_poc/` for the current pilot episodes.
- `legacy_mentor_rag_poc/` is a working legacy case-only RAG baseline over Level 2 reasoning records.
- Offline Level 3/Level 4 principle extraction is implemented in `semantic_analysis_poc/`.
- `mentor_agent/` is the implemented and validated formal principle-plus-case AI Investment Mentor MVP runtime.
- Vector database, current market tools, portfolio integration, fine-tuning, UI, deployment, and full corpus processing are not implemented yet.

Start here:

- `AGENTS.md`: project-wide agent rules.
- `semantic_analysis_poc/README.md`: offline semantic extraction workspace.
- `legacy_mentor_rag_poc/README.md`: deterministic case-only RAG baseline.
- `mentor_agent/README.md`: formal principle-plus-case Mentor runtime.
- `legacy_segment_poc/README.md`: completed Phase 1 transcription POC reference.
- `docs/reasoning_mentor_architecture.md`: planned four-level reasoning architecture.
- `docs/private_data_setup.md`: how to connect the public source repo to the sibling private data repo.
- `docs/public_artifacts.md`: public/private artifact policy and public derivative index.
- `docs/terminology.tsv`: shared terminology table for preserved terms, aliases, and corrections.
- `docs/future-improvements.md`: deferred optimization ideas.

## Project Flow

```text
[done]    Phase 1: RSS / Apple lookup
[done]    Phase 1: Episode manifest
[done]    Phase 1: EP674 MP3 audio + metadata
[done]    Phase 1: Raw ASR transcript
[done]    Phase 1: Traditional Chinese normalization
[done]    Phase 1: Deterministic merged utterances and integrity validation
[done]    Phase 2: Whole-episode LLM semantic extraction POC for pilot episodes
[done]    Offline Level 3/4 principle extraction POC
[done]    Legacy case-only Mentor RAG baseline over Level 2 records
[done]    Formal Mentor Agent MVP runtime: principle-assisted / case-only grounded answering
[future]  Full corpus expansion planning and implementation
[future]  Full principle-guided reasoning after stronger Level 4 evidence
[future]  Phase 8: Advanced version
```

Every derived layer should remain traceable to episode, timestamp, and source segment IDs. Raw artifacts should not be overwritten by normalized, merged, corrected, or semantic extraction outputs.

Current semantic design direction:

- Use the whole normalized episode transcript as LLM input.
- Supply terminology corrections as annotations, not destructive transcript rewrites.
- Produce Level 1 Episode Semantic Maps and Level 2 Reasoning Records / Cases.
- Level 3 extracts episode-local provisional principles from Level 2 records.
- Level 4 compares principles and cases across episodes.
- Principle confidence is multi-dimensional and deterministically constrained; Level 3 high overall confidence is not allowed.
- Future online routing must use runtime eligibility and cite Level 2 source records, not principles alone.
- Preserve uncertainty, assumptions, risks, exceptions, counterexamples, opinion changes, decisions, and speaker behavior.
- Do not use deterministic keyword segmentation/classification as the primary semantic method.
- Do not invent long-term speaker principles without cross-episode evidence.

## Repository Hierarchy

```text
Podcast-Speaker-AI-Investment-Mentor/
├── AGENTS.md
├── README.md
├── docs/
│   ├── architecture.md
│   ├── data-model.md
│   ├── private_data_setup.md
│   ├── cloud-processing-plan.md
│   ├── future-improvements.md
│   └── terminology.tsv
├── legacy_segment_poc/
│   └── ...
├── semantic_analysis_poc/
│   └── ...
├── legacy_mentor_rag_poc/
│   └── ...
└── mentor_agent/
    ├── README.md
    ├── main.py
    ├── config/
    ├── modules/
    ├── prompts/
    └── ...
```

## How To Use This Project

For transcription and normalization reference, start in `legacy_segment_poc/`.

For offline semantic extraction, start in `semantic_analysis_poc/`.

For the working case-only RAG demo, start in:

```text
legacy_mentor_rag_poc/
```

For the formal principle-plus-case Mentor runtime, start in:

```text
mentor_agent/
```

Do not rerun Phase 1 transcription, GPU benchmark, RSS discovery, or episode download unless explicitly needed.

## Public / Private Data Boundary

This public repository is a curated showcase: code, schemas, prompts, configs, public documentation, synthetic examples, redacted examples, and aggregate reports.

The private sibling repository is the canonical source of truth for complete data, complete outputs, complete reports, internal documents, experiment records, and daily runtime artifacts:

```text
projects/
├── Podcast-Speaker-AI-Investment-Mentor/
└── AI-Investment-Mentor-private/
```

Set these local environment variables when running commands that need private data:

```bash
PODCAST_DATA_ROOT=/absolute/path/to/AI-Investment-Mentor-private/shared_data
PIPELINE_DATA_ROOT=/absolute/path/to/AI-Investment-Mentor-private/pipeline_data
PRIVATE_DOCS_ROOT=/absolute/path/to/AI-Investment-Mentor-private/private_docs
```

See `docs/private_data_setup.md`.

Public samples are not production data. They must be synthetic, redacted, summarized, or aggregated, and their relationship to private source artifacts is tracked in `docs/public_artifacts.md`.

## More Detail

- Architecture: `docs/architecture.md`
- Reasoning Mentor architecture: `docs/reasoning_mentor_architecture.md`
- Data model: `docs/data-model.md`
- Cloud plan: `docs/cloud-processing-plan.md`
- Future improvements: `docs/future-improvements.md`
- Private data setup: `docs/private_data_setup.md`
- Public artifacts: `docs/public_artifacts.md`
- Terminology: `docs/terminology.tsv`
- Phase 1 POC details: `legacy_segment_poc/poc_docs/local-transcription-poc.md`
- Phase 1 troubleshooting: `legacy_segment_poc/poc_docs/troubleshooting.md`
- Phase 2 POC: `semantic_analysis_poc/README.md`
- Legacy case-only RAG baseline: `legacy_mentor_rag_poc/README.md`
- Formal Mentor Agent runtime: `mentor_agent/README.md`
