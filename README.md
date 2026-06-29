# Podcast Speaker AI Investment Mentor

This project explores how to build an AI Investment Mentor from historical Gooaye 股癌 podcast episodes.

The goal is not automated trading or order placement. The long-term goal is to preserve and model how the speaker reasons about markets, industries, companies, risk, uncertainty, exceptions, position changes, and behavior across cases.

## Quick Overview

Current status:

- Phase 1 Transcription POC is complete and preserved as a stable reference in `legacy_segment_poc/`.
- Phase 2 Semantic Extraction POC is the active workspace in `semantic_analysis_poc/`.
- Phase 2 starts from whole-episode normalized transcript input and will use LLM-based, evidence-grounded semantic extraction.
- RAG, vector database, full Mentor Agent, fine-tuning, and cloud batch processing are not implemented yet.

Start here:

- `AGENTS.md`: project-wide agent rules.
- `semantic_analysis_poc/README.md`: active Phase 2 workspace.
- `legacy_segment_poc/README.md`: completed Phase 1 transcription POC reference.
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
[active]  Phase 2: Whole-episode LLM semantic extraction POC
[future]  Phase 3: API/model selection
[future]  Phase 4: 3 to 20 episode pilot
[future]  Phase 5: Full corpus processing
[future]  Phase 6: Cross-episode knowledge
[future]  Phase 7: Mentor MVP
[future]  Phase 8: Advanced version
```

Every derived layer should remain traceable to episode, timestamp, and source segment IDs. Raw artifacts should not be overwritten by normalized, merged, corrected, or semantic extraction outputs.

Phase 2 design direction:

- Use the whole normalized episode transcript as LLM input.
- Supply terminology corrections as annotations, not destructive transcript rewrites.
- Produce a three-level hierarchy: Episode Semantic Map, Topic-thread Reasoning Records, and Episode Synthesis.
- Preserve uncertainty, assumptions, risks, exceptions, counterexamples, opinion changes, decisions, and speaker behavior.
- Do not use deterministic keyword segmentation/classification as the primary semantic method.

## Repository Hierarchy

```text
Podcast-Speaker-AI-Investment-Mentor/
├── AGENTS.md
├── README.md
├── docs/
│   ├── architecture.md
│   ├── data-model.md
│   ├── cloud-processing-plan.md
│   ├── future-improvements.md
│   └── terminology.tsv
├── legacy_segment_poc/
│   └── ...
└── semantic_analysis_poc/
    ├── AGENTS.md
    ├── README.md
    ├── config/
    ├── data/
    ├── prompts/
    ├── schemas/
    ├── src/
    ├── outputs/
    ├── tests/
    └── tmp/
```

## How To Use This Project

For active Phase 2 work, start in `semantic_analysis_poc/`.

For Phase 1 reference only:

```text
legacy_segment_poc/
```

Do not rerun Phase 1 transcription, GPU benchmark, RSS discovery, or episode download unless explicitly needed.

## More Detail

- Architecture: `docs/architecture.md`
- Data model: `docs/data-model.md`
- Cloud plan: `docs/cloud-processing-plan.md`
- Future improvements: `docs/future-improvements.md`
- Terminology: `docs/terminology.tsv`
- Phase 1 POC details: `legacy_segment_poc/poc_docs/local-transcription-poc.md`
- Phase 1 troubleshooting: `legacy_segment_poc/poc_docs/troubleshooting.md`
- Phase 2 POC: `semantic_analysis_poc/README.md`
