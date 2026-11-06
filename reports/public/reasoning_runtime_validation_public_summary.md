# Reasoning Runtime Validation Public Summary

> Historical aggregate for the archived baseline, not a result of the rebuild.


This is a public-safe aggregate summary of a focused Mentor Agent reasoning validation run.
The complete live traces, model responses, detailed review notes, and legacy comparison outputs are kept in the private repository.

## Scope

- Runtime: `mentor_agent`
- Model: `qwen/qwen3-235b-a22b-2507`
- Corpus scope: four semantic episodes, 38 Level 2 reasoning records, one eligible Level 4 principle
- Validation set: 10 curated questions
- Legacy comparison subset: 5 questions
- No new transcript, semantic extraction, or corpus expansion was run

Question categories covered:

- principle-applicable scenario
- principle-related but limited current-data scenario
- case-only investment behavior
- conflicting or conditional evidence
- exception-triggering scenario
- missing user information
- insufficient evidence
- current-market buy/sell request
- non-investment domain mismatch
- overgeneralization risk

## Aggregate Results

| Metric | Result |
| --- | ---: |
| `principle_assisted` routes | 4 |
| `case_only` routes | 5 |
| `insufficient_evidence` routes | 1 |
| Review checks passed | 101 |
| Review checks partial | 10 |
| Review checks failed | 0 |
| Not applicable checks | 19 |
| Live model tokens | 55,999 |
| Live model latency | 213.5 seconds |
| Reported API cost metadata | USD 0.01033857 |
| Parse retries | 0 observed in the final run |

## Findings

- The runtime can use the Level 4 principle when the query is about the data-versus-narrative decision pattern.
- The runtime can downgrade to case-only routing when the principle context is weak or unrelated.
- The insufficient-evidence path now avoids requiring citations when no source-backed answer should be produced.
- Current-market questions are guarded: the runtime can provide a historical reasoning frame, but it does not claim live market awareness.
- The final validation pass found no failing review checks, but several checks remain partial because the answers still require caveats around conditionality, principle rejection, or conflict presentation.

## Fixes Applied During Validation

- Added a focused validation question set and runner.
- Added safer route grading for mixed expectations such as case-only or insufficient-evidence.
- Added a context gate so the current Level 4 principle is not over-applied to broad market questions.
- Improved current-market guard wording detection.
- Improved insufficient-evidence handling for uncovered companies or scenarios.
- Added citation and no-evidence guard tests.
- Reduced prompt/output size without removing conditions, exceptions, conflicts, or citation linkage.

## Token Usage Notes

The main token cost comes from two structured LLM stages:

1. Applicability assessment receives the compact principle/case context.
2. Answer generation receives the assessment plus compact source evidence and citation metadata.

The final prompt still duplicates some context across these stages by design, because the answer generator needs enough evidence to remain grounded. Compact case serialization and output limits reduced the final validation run from an earlier 63,528-token run to 55,999 tokens.

## Limitations

- This is not a full benchmark.
- The report does not include full model responses, private traces, transcript excerpts, or detailed review notes.
- Citation validation checks existence and membership, not full semantic entailment.
- Only one eligible Level 4 principle is currently available, so principle-assisted routing is intentionally conservative.
- The system has no live market data, news tools, portfolio context, persistent memory, vector database, or production UI.

## Public / Private Boundary

This summary is an aggregate public derivative. The canonical validation traces and detailed reports remain private.
