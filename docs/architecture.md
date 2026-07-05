# Architecture

This document describes the long-term architecture for the Podcast Speaker AI Investment Mentor.

Current boundary:

- Phase 1 Transcription POC is complete and preserved in `legacy_segment_poc/`.
- Phase 2 Semantic Extraction POC is implemented in `semantic_analysis_poc/` for the current pilot episodes.
- `legacy_mentor_rag_poc/` is a working deterministic case-only RAG baseline.
- Offline Level 3/Level 4 principle extraction is implemented as a POC in `semantic_analysis_poc/`.
- `mentor_agent/` is the implemented and validated first formal runtime for principle-plus-case Reasoning Mentor.

## Target Flow

```text
Apple lookup / RSS
    ↓
Episode manifest
    ↓
MP3 + metadata
    ↓
Raw ASR
    ↓
Merged utterances
    ↓
Whole-episode normalized transcript package
    ↓
Level 1: Episode Semantic Map
    ↓
Level 2: Topic-thread Reasoning Records
    ↓
Level 3: Episode-Level Provisional Principles
    ↓
Level 4: Cross-Episode Decision Principles
    ↓
Principle-plus-case Reasoning Mentor
```

## Phase 1 Verified

Verified in the completed transcription POC:

- Apple lookup fallback can recover the active RSS feed after an old feed URL returns 404.
- RSS ingestion produces a 674-row episode manifest.
- EP674 MP3 was downloaded and validated with size, SHA-256, and ffprobe metadata.
- EP674 full `large-v3-turbo` transcription completed locally.
- EP674 raw segment audit completed.
- EP674 deterministic merged utterances were generated.
- EP674 Traditional Chinese normalized merged transcript exists.
- EP674 timestamp and source-segment integrity checks passed.
- EP674 manual review package was generated.

Phase 1 is now legacy/reference, frozen except for bug fixes or explicitly requested reproducibility checks.

## Phase 2 Semantic Extraction

Phase 2 uses the normalized whole-episode transcript as LLM input. The LLM is responsible for semantic understanding. Deterministic code handles transcript preparation, source mapping, API orchestration, schema validation, retry handling, metadata, storage, and reproducibility.

Target hierarchy:

1. Episode Semantic Map
2. Reasoning Records / Cases
3. Episode-Level Provisional Principles
4. Cross-Episode Decision Principles

Topic threads may be long, overlapping, or non-contiguous. The primary method should not be deterministic keyword segmentation, fixed episode-position weighting, fixed-duration windows, or single-label classification.

Level 3 in the new architecture should be principle-oriented and strategy-dependent:

- `principle_extraction`: identify episode-local recurring decision patterns.
- `light_principle_grouping`: group overlapping local patterns without inventing a central theme.
- `bypass`: skip principle extraction when Level 2 cases are already the useful output.

Do not force every episode into one unified narrative. Level 2 cases may be the canonical useful output for loosely structured episodes.

## Principle Extraction Boundary

Level 3/4 extraction is offline knowledge construction:

```text
Level 2 reasoning records
    ↓
Level 3 episode-local provisional principles
    ↓
Level 4 cross-episode decision principles
```

Level 2 cases remain the source of truth. Principles are abstractions and must not be counted as additional independent evidence.

## Mentor Agent Boundary

`legacy_mentor_rag_poc/` is a completed case-only baseline:

```text
query
    ↓
retrieve Level 2 reasoning records
    ↓
domain filtering and context assembly
    ↓
LLM answer with citations
```

The formal `mentor_agent/` uses offline principles and cases:

```text
query / scenario analysis
    ↓
retrieve Level 4 principles
    ↓
retrieve supporting, conflicting, and similar Level 2 cases
    ↓
online applicability check
    ↓
conditional answer with citations
```

## Future

- Stronger Level 4 evidence before enabling `principle_guided`.
- Full corpus expansion planning and implementation.
- More robust online applicability evaluation.
- Conditional speaker model construction.
- Current information tools, only after historical reasoning is stable.

## Boundaries

Raw evidence must remain separate from derived layers. Transcript text is not trusted for downstream investment reasoning until manual review checks entity names, tickers, numbers, negation, sentiment, and reasoning fidelity.
