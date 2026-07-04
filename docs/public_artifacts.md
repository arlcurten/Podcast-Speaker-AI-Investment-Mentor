# Public Artifacts

This public repository is not the complete project dataset. It is a curated showcase containing source code, schemas, prompts, public documentation, synthetic examples, redacted real examples, and aggregate reports.

The private sibling repository is the canonical source of truth for complete source-derived data:

```text
AI-Investment-Mentor-private/
```

Do not commit complete transcripts, full Level 1-4 outputs, full model response traces, private human review records, private experiment notes, or internal know-how to this public repository.

## Artifact Classes

| Class | Public repo policy |
| --- | --- |
| Synthetic fixtures | Allowed when clearly marked and not derived from real podcast transcript text. |
| Redacted real examples | Allowed only after manual selection and redaction. They must not reconstruct a full episode. |
| Aggregate reports | Allowed when they summarize metrics or architecture outcomes without long transcript excerpts or full model responses. |
| Complete source-derived artifacts | Private only. |
| Credentials or private URLs | Never allowed. |

## Adding A Public Artifact

1. Generate or preserve the complete artifact in the private repo first.
2. Register the canonical artifact in the private manifest.
3. Decide whether a public derivative has clear portfolio, README, interview, architecture, or test value.
4. Create a synthetic, redacted, summarized, or aggregated derivative.
5. Manually check transcript exposure, sensitive content, and private notes.
6. Place it under `sample_outputs/` or `reports/public/`.
7. Record the relationship in this file and in the private manifest `public_derivatives` field.
8. Commit the private metadata separately from the public derivative.

Do not create a `private/` directory in the public repo. If an artifact is uncertain, keep it private.

## Current Public Artifact Index

| Public artifact | Type | Private source | Source manifest ID | Episodes | Review status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `sample_outputs/synthetic/level2_reasoning_records_synthetic_sample.json` | synthetic_fixture | none | none | synthetic | safe_by_construction | Schema-shaped example, not derived from real transcript text. |
| `reports/public/pipeline_status_public_summary.md` | aggregate_summary | `pipeline_data/semantic_analysis/outputs`, `pipeline_data/semantic_analysis/reports`, `pipeline_data/legacy_mentor_rag/evaluations` | `phase2_semantic_outputs`, `phase2_private_reports`, `legacy_mentor_rag_evaluations` | EP57, EP450, EP670, EP674 | reviewed_for_public_summary | Aggregate project status only. No transcript excerpts or full model responses. |
| `reports/public/reasoning_runtime_validation_public_summary.md` | aggregate_summary | `pipeline_data/mentor_agent/evaluations/reasoning_quality` | `mentor_agent_reasoning_quality_validation` | EP57, EP450, EP670, EP674 | reviewed_for_public_summary | Aggregate runtime validation metrics only. No full answers, transcript excerpts, private traces, or review notes. |
