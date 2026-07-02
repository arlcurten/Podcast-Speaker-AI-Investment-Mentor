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
  -> Level 3: Optional Episode Consolidation
```

Topic threads may be long, overlapping, or non-contiguous. The main semantic method should not be deterministic keyword segmentation, fixed episode-position weighting, fixed-duration windows, or single-label classification.

Level 3 is optional. Do not force every episode into a unified theme or narrative. Level 1 must recommend one of these Level 3 strategies:

- `full_synthesis`: use only when the episode has a clear central theme.
- `light_consolidation`: use for multi-topic, conversational, mixed, or loosely structured episodes.
- `bypass`: use when Level 2 reasoning records are already complete and mostly independent.

Level 2 is allowed to be the final useful semantic output.

## Semantic Levels

Level 1 asks: what topics appear in this episode, and how are they related?

It should identify main topic threads, interrupted topics that resume later, Q&A that expands earlier reasoning, non-contiguous regions belonging to the same topic, important reasoning regions, and overall episode structure. It must include:

- `episode_structure_type`: `single_theme`, `multi_theme`, `conversational`, `qa_heavy`, or `mixed`
- `episode_structure_confidence`
- `recommended_level3_strategy`: `full_synthesis`, `light_consolidation`, or `bypass`
- `level3_strategy_reason`

Level 2 asks: what reasoning, conditions, risks, exceptions, and behavioral signals exist inside each important topic?

Reasoning records may come from one continuous region or multiple non-contiguous transcript regions. Each record should preserve claims, observations, causes, assumptions, conditions, risks, uncertainty, exceptions, decisions or preferences, avoided actions, view-change conditions, speaker behavior, teaching style, tags, and source evidence.

Level 3 asks: should this episode receive full synthesis, light consolidation, or no consolidation?

- `full_synthesis`: consolidate major views, cases, decision principles, risks, exceptions, repeated arguments, corrections, contradictions, opinion changes, and overall stance.
- `light_consolidation`: produce a topic inventory, merge duplicate or substantially overlapping reasoning records, identify cross-topic links, corrections, contradictions, and records useful for cross-episode analysis. Do not invent an episode-level central message.
- `bypass`: preserve Level 2 records as canonical output and produce only minimal metadata explaining why consolidation was skipped.

The LLM must be explicitly told that many episodes have no central theme, the speaker may move between unrelated topics, and it must not invent coherence or force a narrative.

## Folder Map

```text
semantic_analysis_poc/
├── AGENTS.md
├── README.md
├── main.py
├── config/
├── modules/
├── data/
│   └── phase1_inputs/
```

- `main.py`: minimal Phase 2 command dispatcher.
- `modules/`: current reusable Phase 2 execution code.
- `config/`: Phase 2 input references, provider settings, and semantic design contract.
- `data/phase1_inputs/`: small copied Phase 1 evidence inputs needed by this POC.
- `config/semantic_design.yaml`: current semantic level, prompt, and validation contract.

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

## OpenRouter Setup

OpenRouter registration and credits are external prerequisites. This repository does not store API keys.

Preferred local setup is a repository-root `.env` file copied from `../.env.example`:

```bash
cd ..
cp .env.example .env
```

Then fill in local values:

```text
OPENROUTER_API_KEY=...
PHASE2_MODEL_ID=...
```

The Phase 2 runner loads only the repository-root `.env` file and does not override variables already exported in the shell. You can also set the same values explicitly:

```bash
export OPENROUTER_API_KEY="..."
export PHASE2_MODEL_ID="..."
```

`PHASE2_MODEL_ID` may also be configured as `model_id` in `config/provider.yaml`, but keep credentials in the environment. Do not commit `.env` or credential files.

Dry-run / preflight without a paid API call:

```bash
cd semantic_analysis_poc
python3 main.py run-episode --episode EP674 --dry-run
```

Authenticated OpenRouter key check without sending transcript text:

```bash
cd semantic_analysis_poc
export OPENROUTER_API_KEY="..."
python3 main.py preflight-auth
```

Real execution after credentials and model are configured:

```bash
cd semantic_analysis_poc
export OPENROUTER_API_KEY="..."
export PHASE2_MODEL_ID="..."
python3 main.py run-episode --episode EP674
```

Expected outputs after real execution:

```text
data/outputs/EP674/level1_episode_semantic_map.json
data/outputs/EP674/level2_reasoning_records.json
data/outputs/EP674/level3_optional_consolidation.json
data/outputs/EP674/run_metadata.json
reports/EP674_phase2_human_review.md
```

## Current Status

Minimal executable workflow. Run one real episode only:

```bash
cd semantic_analysis_poc
python3 main.py run-episode --episode EP674 --dry-run
```
