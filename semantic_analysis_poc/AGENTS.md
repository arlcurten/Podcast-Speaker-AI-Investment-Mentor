# Semantic Analysis POC Agent Guide

This directory is the active Phase 2 Semantic Extraction POC workspace.

Phase 1 transcription work is complete and preserved at `../legacy_segment_poc/`. Do not modify that legacy area unless the user explicitly requests a Phase 1 bug fix or inspection.

## Scope

Phase 2 will evaluate whole-episode LLM-based semantic extraction from normalized transcripts.

Target hierarchy:

1. Episode Semantic Map
2. Topic-thread Reasoning Records
3. Optional Episode Consolidation

## Rules

- Do not implement API calls, provider integration, model comparison, RAG, embeddings, or Mentor Agent unless explicitly requested.
- Do not use deterministic keyword segmentation/classification as the main semantic method.
- Level 1 is a semantic map, not fixed segmentation. It must identify episode structure and recommend `full_synthesis`, `light_consolidation`, or `bypass` for Level 3.
- Level 2 is the primary knowledge layer. It may be the final useful episode-level output for conversational or multi-topic episodes.
- Level 3 is optional. Do not force a full synthesis, central theme, or unified narrative when the episode structure does not support it.
- Deterministic code should only prepare transcripts, preserve mappings, orchestrate API calls, validate schemas, handle retries, store metadata, and support reproducibility.
- Every future output must retain episode ID, timestamps, source segment IDs, and evidence links.
- Terminology corrections from `../docs/terminology.tsv` are annotations/context, not destructive transcript rewrites.
- OpenRouter credentials must come from `OPENROUTER_API_KEY` or another explicitly configured environment variable. Never write API keys into tracked files or logs.
- The model ID must come from `PHASE2_MODEL_ID` or `config/provider.yaml`; do not hard-code a default model choice.
- Keep this workspace minimal until the Phase 2 design stabilizes.
- Do not pre-create placeholder folders. Follow the simple `main.py` + `modules/` + `config/` + `data/` + `reports/` style from `../legacy_segment_poc/` only when those pieces are actually needed.
- Prefer one small module per real pipeline step. Do not split prompts, schemas, API code, validation, and reporting into separate trees before there is working content.
- When validation is implemented, Level 3 required fields must depend on the selected strategy. Do not require full synthesis fields for `light_consolidation` or `bypass`.

## Local Inputs

Small Phase 1 input copies for EP674 live under:

```text
data/phase1_inputs/EP674/
```

These are copied evidence artifacts, not regenerated Phase 1 outputs.
