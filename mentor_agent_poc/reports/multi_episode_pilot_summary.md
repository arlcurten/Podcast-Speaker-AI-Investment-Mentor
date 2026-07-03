# Multi-Episode Mentor Retrieval Pilot

Scope: EP674 plus three added episodes. This validates that the existing Phase 1 -> Phase 2 -> Mentor retrieval flow can operate across multiple episodes without adding production features.

## Episodes

| Episode | Reason selected | Phase 1 | Phase 2 | Structure | Level 3 | Records | Phase 2 cost |
|---|---|---|---|---|---|---:|---:|
| EP674 | Existing reviewed baseline, conversational / multi-topic | complete | complete | conversational | light_consolidation | 8 | $0.014338 |
| EP670 | Newer AI / US-market / technology-heavy context | complete | complete | mixed | light_consolidation | 10 | $0.011143 |
| EP450 | Older macro / rate / market-regime context | complete | complete | qa_heavy | light_consolidation | 8 | $0.018407 |
| EP57 | Early TSMC / central-bank / Taiwan-market context | complete | complete | qa_heavy | bypass | 12 | $0.012651 |

Total Phase 2 cost for the four loaded episodes: `$0.056540`.

## Cross-Episode Retrieval

Command:

```bash
cd mentor_agent_poc
python3 main.py eval-cross
```

Output:

- `reports/mentor_cross_episode_eval_report.md`
- `reports/mentor_cross_episode_eval_results.json`

Summary:

- 8 cross-episode questions were run.
- All questions returned retrieved evidence.
- Citation resolution passed for all generated answers.
- Retrieval can pull evidence from multiple episodes when the query contains shared concepts such as risk, uncertainty, technical weakness, TSMC, AI, or market context.
- The current default retrieval path is `hybrid_dense` with deterministic domain / investment-relevance gating.

## Known Weaknesses

- Some broad questions can still retrieve adjacent but imperfect records because local dense similarity is not full semantic understanding.
- The domain gate removes obvious career/business/personal-only mismatches for investment queries, but it intentionally keeps mixed records such as brokerage/cost-structure discussions when they contain investment-adjacent evidence.
- Domain-gate rejection counts in reports are candidate-level events across queries, not unique records permanently removed from the knowledge base.
- Automated review currently checks citation validity and obvious current-market guardrails, not deep semantic relevance.
- Cross-episode behavioral conclusions should remain tentative until more episodes are added and human-reviewed.

## MVP Readiness

The agent now demonstrates meaningful multi-episode retrieval for a portfolio/interview MVP:

```text
question
  -> retrieve records across EP674 / EP670 / EP450 / EP57
  -> assemble compact evidence
  -> answer with episode/time/record citations
```

It is ready to add 2-4 more episodes next, but not ready for a full corpus, production RAG, live market tools, or a formal behavioral model.
