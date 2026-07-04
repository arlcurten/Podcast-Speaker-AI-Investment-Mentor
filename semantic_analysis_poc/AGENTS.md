# Semantic Analysis POC Agent Guide

This directory is the Phase 2 Semantic Extraction POC workspace.

Phase 1 transcription work is complete and preserved at `../legacy_segment_poc/`. Do not modify that legacy area unless the user explicitly requests a Phase 1 bug fix or inspection.

## Scope

Phase 2 evaluates whole-episode LLM-based semantic extraction from normalized transcripts.

Current hierarchy:

1. Episode Semantic Map
2. Reasoning Records / Cases
3. Episode-Level Provisional Principles
4. Cross-Episode Candidate Decision Principles

## Rules

- Do not implement API calls, provider integration, model comparison, RAG, embeddings, or Mentor Agent unless explicitly requested.
- Do not use deterministic keyword segmentation/classification as the main semantic method.
- Level 1 is a semantic map, not fixed segmentation.
- Level 2 is the primary historical case and evidence layer.
- Level 3 is episode-local and provisional. Do not force a full synthesis, central theme, or unified narrative when the episode structure does not support it.
- Level 4 must be cross-episode, evidence-linked, and revisable.
- Deterministic code should only prepare transcripts, preserve mappings, orchestrate API calls, validate schemas, handle retries, store metadata, and support reproducibility.
- Every output must retain episode ID, timestamps, source segment IDs, and evidence links.
- Terminology corrections from `../docs/terminology.tsv` are annotations/context, not destructive transcript rewrites.
- OpenRouter credentials must come from `OPENROUTER_API_KEY` or another explicitly configured environment variable. Never write API keys into tracked files or logs.
- The model ID must come from `PHASE2_MODEL_ID` or `config/provider.yaml`; do not hard-code a default model choice.
- Keep this workspace minimal until the Phase 2 design stabilizes.
- Do not pre-create placeholder folders. Follow the simple `main.py` + `modules/` + `config/` + `data/` + `reports/` style from `../legacy_segment_poc/` only when those pieces are actually needed.
- Prefer one small module per real pipeline step. Do not split prompts, schemas, API code, validation, and reporting into separate trees before there is working content.
- Level 3/4 validation must preserve evidence links, counterexamples, conflicts, multi-dimensional confidence, and runtime eligibility.

## Private Inputs

Phase 1 transcript inputs and generated Phase 2 outputs are private source-derived artifacts. They should live under the sibling private repository and be located through:

```text
PIPELINE_DATA_ROOT=/absolute/path/to/consultant-ai-agent-private/pipeline_data
```

Do not commit raw transcripts, normalized full transcripts, complete model outputs, model comparison traces, or private review reports to the public repository.
