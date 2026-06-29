# Semantic Analysis POC

This is the active Phase 2 workspace for the Podcast Speaker AI Investment Mentor project.

Phase 1 transcription is complete and preserved separately in `../legacy_segment_poc/`. This POC starts from Phase 1 outputs and prepares for LLM-based semantic extraction.

No LLM call, API provider integration, RAG, vector database, Mentor Agent, or cloud workflow is implemented yet.

## Purpose

Test whether a whole normalized episode transcript can be converted into evidence-grounded semantic records that preserve:

- investment reasoning
- assumptions and conditions
- risk and uncertainty
- exceptions and counterexamples
- opinion changes
- decisions and position logic
- speaker behavior and teaching style

## Intended Flow

```text
Phase 1 normalized transcript
  -> LLM semantic extraction
  -> Level 1: Episode Semantic Map
  -> Level 2: Topic-thread Reasoning Records
  -> Level 3: Episode Synthesis
```

Topic threads may be long, overlapping, or non-contiguous. The main semantic method should not be deterministic keyword segmentation, fixed episode-position weighting, fixed-duration windows, or single-label classification.

## Folder Map

```text
semantic_analysis_poc/
├── AGENTS.md
├── README.md
├── config/
├── data/
│   └── phase1_inputs/
```

- `config/`: Phase 2 input references and future run settings.
- `data/phase1_inputs/`: small copied Phase 1 evidence inputs needed by this POC.

Do not create extra folders until the implementation actually needs them. When Phase 2 starts implementation, prefer the same simple style as `../legacy_segment_poc/`:

```text
main.py
modules/
config/
data/
reports/
```

Only add a folder when it has real content and a clear role. For example, create `modules/` when there is reusable code, `reports/` when there is a human-readable output, and `data/outputs/` only after the first generated semantic extraction artifact exists.

## Phase 1 Input Copy

```text
data/phase1_inputs/EP674/raw_large-v3-turbo_transcript.json
data/phase1_inputs/EP674/raw_large-v3-turbo_run_metadata.json
data/phase1_inputs/EP674/derived_large-v3-turbo_merged_transcript.json
data/phase1_inputs/EP674/derived_large-v3-turbo_normalized_merged_transcript_zh_tw.json
data/phase1_inputs/EP674/merge_integrity.json
data/phase1_inputs/EP674/audio_metadata.json
data/phase1_inputs/EP674/download_metadata.json
```

The MP3, benchmark artifacts, old deterministic topic outputs, and temporary data are not copied.

Shared terminology remains at:

```text
../docs/terminology.tsv
```

## Current Status

Minimal workspace and input boundary only. The next task should define the smallest useful schema/prompt strategy before implementing any API call or adding more structure.
