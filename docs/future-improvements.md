# Future Improvements

These items are deferred optimization ideas. They are not current blockers for Phase 2 workspace setup.

## Highest Priority

1. Define schemas for Episode Semantic Map, Topic-thread Reasoning Records, and Optional Episode Consolidation.
2. Compare a small number of long-context API models for quality, cost, stability, and structured output reliability.
3. Build a small human-labeled gold set for semantic extraction review.
4. Calibrate evidence-link quality and missing-context errors.
5. Measure token usage and cost on 3 to 20 diverse episodes.

## Classification And Segmentation

- Deterministic topic segmentation/classification from Phase 1 should remain reference-only, not the main Phase 2 semantic method.
- Improve semantic handling of long, overlapping, or non-contiguous topic threads.
- Better detection of companies, industries, tickers, numbers, conditions, risks, exceptions, counterexamples, and opinion changes.
- Confidence calibration for LLM extraction output.
- Duration- and evidence-weighted evaluation instead of segment-count-only metrics.
- Advertisement and show-admin handling if it interferes with semantic extraction.

## Transcript And Normalization

- OpenCC/package version reproducibility in locked environments.
- Glossary audit reports and before/after change statistics.
- Better Traditional Chinese terminology handling for Taiwan finance, industry nicknames, and company aliases.
- Review-package presentation improvements, including adjacent source context before/after selected evidence.

## Retrieval And Mentor Evaluation

- Retrieval-noise evaluation before building any vector database.
- Tests for whether retrieved segments preserve reasoning, uncertainty, risk attitude, and counterexamples.
- Explicit separation between historical market commentary and current investment advice.
- Later evaluation of how topic/reasoning cases should feed a Mentor Agent.

## Infrastructure

- Consolidate generated outputs into fewer canonical paths.
- Move scratch/debug/benchmark artifacts under a single ignored `tmp/` or clearly ignored benchmark area.
- Add minimal automated tests for merge integrity and path conventions.
