# Conflict Handling Validation Public Summary

This is a public-safe aggregate summary of a focused `mentor_agent` conflict-handling validation run.
The full traces, complete model responses, detailed review notes, and private evidence remain in the private repository.

## Scope

- Runtime: `mentor_agent`
- Model: `qwen/qwen3-235b-a22b-2507`
- Corpus scope: four semantic episodes, 38 Level 2 reasoning records, one eligible Level 4 principle
- Validation set: 6 conflict-focused questions
- No new transcript, semantic extraction, corpus expansion, vector database, or current-data tool was run

Question categories covered:

- weak or partial conflict
- apparent conflict resolved by context difference
- exception rather than conflict
- principle and case tension
- unresolved missing-context scenario
- no-conflict control

## Aggregate Results

| Metric | Result |
| --- | ---: |
| `principle_assisted` routes | 4 |
| `case_only` routes | 2 |
| Candidate `potential_conflict` roles | 24 |
| Candidate `exception` roles | 6 |
| Candidate `supporting` roles | 4 |
| Candidate `neutral` roles | 2 |
| Structured conflicting-evidence items | 6 |
| Resolved context-difference items | 10 |
| Unresolved conflict items | 2 |
| Review checks passed | 35 |
| Review checks partial | 1 |
| Review checks failed | 0 |
| Not applicable checks | 30 |
| Live model tokens | 52,675 |
| Live model latency | 214.9 seconds |
| Reported API cost metadata | USD 0.008777914 |

## Findings

- Retrieval now preserves candidate evidence roles instead of returning only relevance-ranked cases.
- The Applicability Assessment now separates `conflicting_evidence`, `resolved_context_differences`, `exceptions_triggered`, and `unresolved_conflicts`.
- Pure exception candidates are no longer kept as conflicts after structured post-processing.
- The no-conflict control no longer produces false conflict items.
- The remaining partial result is a real limitation: the current four-episode corpus supports weak or partial conflict around market timing triggers, but not enough to fully resolve when to switch from waiting to buying.

## Public / Private Boundary

This public summary is aggregate-only. It excludes full answers, transcript excerpts, source evidence text, private traces, and internal review notes.
