# Future Improvements

These items are deferred optimization ideas. They are not current blockers for the Local POC cleanup or the EP674 human review.

## Highest Priority

1. Build a small human-labeled gold dataset from selected EP674 review segments.
2. Calibrate topic-boundary quality against human labels.
3. Improve semantic topic segmentation beyond deterministic keyword/continuity heuristics.
4. Improve classification recall for implicit market context and speaker reasoning.
5. Tune routing thresholds with duration-weighted review results.

## Classification And Segmentation

- Add a `trading_strategy` / 操作策略 content type based on EP674 human review feedback.
- Improve boundary detection for explicit transition cues such as "接下來我們來..." and QA section starts.
- Episode-adaptive thresholds for shows with different structures.
- Better handling of mixed segments that combine personal chat, market reasoning, and Q&A.
- Improved detection of companies, industries, tickers, numbers, conditions, risks, exceptions, and opinion changes.
- Confidence calibration so low-confidence and near-threshold segments are more meaningful.
- Advertisement and show-admin detection with deduplication.
- Duration-based precision/recall evaluation instead of segment-count-only metrics.

## Transcript And Normalization

- OpenCC/package version reproducibility in locked environments.
- Glossary audit reports and before/after change statistics.
- Better Traditional Chinese terminology handling for Taiwan finance, industry nicknames, and company aliases.
- Review-package boundary presentation improvements, including adjacent context before/after a selected segment.

## Retrieval And Mentor Evaluation

- Retrieval-noise evaluation before building any vector database.
- Tests for whether retrieved segments preserve reasoning, uncertainty, risk attitude, and counterexamples.
- Explicit separation between historical market commentary and current investment advice.
- Later evaluation of how topic/reasoning cases should feed a Mentor Agent.

## Infrastructure

- Consolidate generated outputs into fewer canonical paths.
- Move scratch/debug/benchmark artifacts under a single ignored `tmp/` or clearly ignored benchmark area.
- Add minimal automated tests for merge integrity and path conventions.
