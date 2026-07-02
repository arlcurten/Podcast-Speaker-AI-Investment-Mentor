# Architecture

This document describes the long-term architecture for the Podcast Speaker AI Investment Mentor.

Current boundary:

- Phase 1 Transcription POC is complete and preserved in `legacy_segment_poc/`.
- Phase 2 Semantic Extraction POC is active in `semantic_analysis_poc/`.
- Later retrieval and Mentor Agent phases are future work.

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
Level 3: Optional Episode Consolidation
    ↓
Cross-episode decision cases and behavioral patterns
    ↓
Hierarchical RAG / Mentor Agent
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

## Phase 2 Active Direction

Phase 2 should use the normalized whole-episode transcript as LLM input. The LLM is responsible for semantic understanding. Deterministic code should only handle transcript preparation, source mapping, API orchestration, schema validation, retry handling, metadata, storage, and reproducibility.

Target hierarchy:

1. Episode Semantic Map
2. Topic-thread Reasoning Records
3. Optional Episode Consolidation

Topic threads may be long, overlapping, or non-contiguous. The primary method should not be deterministic keyword segmentation, fixed episode-position weighting, fixed-duration windows, or single-label classification.

Level 3 is strategy-dependent:

- `full_synthesis`: use only when the episode has a clear central theme.
- `light_consolidation`: use for multi-topic, conversational, mixed, or loosely structured episodes.
- `bypass`: use when Level 2 reasoning records are already complete and largely independent.

Do not force every episode into one unified narrative. Level 2 may be the canonical useful output.

## Future

- API/model selection.
- 3 to 20 episode pilot.
- Full corpus processing.
- Cross-episode decision cases and behavioral patterns.
- Conditional speaker model construction.
- Hierarchical retrieval.
- Mentor Agent MVP.

## Boundaries

Raw evidence must remain separate from derived layers. Transcript text is not trusted for downstream investment reasoning until manual review checks entity names, tickers, numbers, negation, sentiment, and reasoning fidelity.
