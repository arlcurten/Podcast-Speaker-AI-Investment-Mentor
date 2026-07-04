# Semantic Analysis POC

This is the offline semantic analysis POC workspace for the Podcast Speaker AI Investment Mentor project.

Phase 1 transcription is complete and preserved separately in `../legacy_segment_poc/`. This POC starts from Phase 1 outputs and prepares for LLM-based semantic extraction.

Phase 2 semantic extraction has been run on the current pilot episodes. The current primary pilot model is:

```text
qwen/qwen3-235b-a22b-2507
```

RAG, vector database, Mentor Agent, full corpus processing, and cloud workflow are not implemented here. The case-only Mentor baseline lives in `../legacy_mentor_rag_poc/`; the new principle-plus-case Mentor skeleton lives in `../reasoning_mentor_poc/`.

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
  -> Level 2: Reasoning Records / Cases
  -> future Level 3: Episode-Level Provisional Principles
  -> future Level 4: Cross-Episode Decision Principles
  -> small pilot summary
```

Topic threads may be long, overlapping, or non-contiguous. The main semantic method should not be deterministic keyword segmentation, fixed episode-position weighting, fixed-duration windows, or single-label classification.

Future Level 3 is principle-oriented and optional. Do not force every episode into a unified theme or narrative. A future Level 3 pass may use:

- `principle_extraction`: extract episode-local recurring decision patterns.
- `light_principle_grouping`: group related local patterns without inventing a central thesis.
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

Future Level 3 asks: are there episode-local decision principles worth extracting?

- `principle_extraction`: identify provisional principles supported by multiple local records.
- `light_principle_grouping`: preserve useful groupings without over-compressing a loose episode.
- `bypass`: preserve Level 2 records as canonical output and record why principle extraction was skipped.

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
- `data/outputs/`: ignored generated semantic outputs.
- `reports/`: ignored generated human-readable review and pilot reports.

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

Run one episode after credentials and model are configured:

```bash
cd semantic_analysis_poc
export OPENROUTER_API_KEY="..."
export PHASE2_MODEL_ID="..."
python3 main.py run-episode --episode EP674
```

Run the small pilot over all currently available Phase 1 inputs:

```bash
cd semantic_analysis_poc
python3 main.py pilot --model-id qwen/qwen3-235b-a22b-2507 --max-episodes 5
```

Expected outputs after real execution:

```text
data/outputs/EP674/level1_episode_semantic_map.json
data/outputs/EP674/level2_reasoning_records.json
data/outputs/EP674/level3_optional_consolidation.json
data/outputs/EP674/run_metadata.json
reports/EP674_phase2_human_review.md
```

Pilot summary:

```text
reports/phase4_pilot_summary.md
```

## Current Status

Current available Phase 1 inputs include the pilot episodes that have already been processed. Add more normalized Phase 1 transcript inputs under `data/phase1_inputs/<EPXXX>/` before running a broader pilot.

```bash
cd semantic_analysis_poc
python3 main.py pilot --model-id qwen/qwen3-235b-a22b-2507 --report-only
```
