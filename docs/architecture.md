# Architecture

This document describes the long-term architecture for the Podcast Speaker AI Investment Mentor. The current implementation is still in Local POC / transcript validation.

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
Topic segments
    ↓
Decision / reasoning cases
    ↓
Behavioral patterns
    ↓
Speaker model
    ↓
Hierarchical RAG / Mentor Agent
```

## Verified

Verified in the Local POC:

- Apple lookup fallback can recover the active RSS feed after an old feed URL returns 404.
- RSS ingestion produces a 674-row episode manifest.
- EP674 MP3 was downloaded and validated with size, SHA-256, and ffprobe metadata.
- EP674 full `large-v3-turbo` transcription completed locally.
- EP674 raw segment audit completed.
- EP674 deterministic merged utterances were generated.
- EP674 manual review package and clips were generated.

## Pending

- Human listening review of transcript quality.
- Clean same-clip GPU benchmark for `large-v3-turbo`.
- Same-clip `large-v3` feasibility comparison on local 4 GB GPU.
- Content classification feasibility.
- Decision on 20-episode cloud pilot.

## Future

- Topic segmentation.
- Decision/reasoning case extraction.
- Behavioral pattern modeling.
- Speaker model construction.
- Hierarchical retrieval.
- Mentor Agent MVP.

## Boundaries

Raw evidence must remain separate from derived layers. Transcript text is not trusted for downstream investment reasoning until manual review checks entity names, tickers, numbers, negation, sentiment, and reasoning fidelity.
