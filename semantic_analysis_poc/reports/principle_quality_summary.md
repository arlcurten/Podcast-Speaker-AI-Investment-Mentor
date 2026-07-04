# Principle Quality Summary

## Before / After

- Old flat confidence allowed high Level 3 values from the LLM.
- New Level 3 overall confidence is deterministic and cannot be high.
- Old Level 4 had 2 candidates; new validated Level 4 has 1 candidate and 1 rejected candidate.

## Single-Record Hypotheses

- `EP674-principle-002`: Delay Judgment on Sentiment-Driven Events Until Empirical Data Confirms Impact -> `not_eligible`
- `EP674-principle-003`: Wealth Effect Drives Reinvestment, Not Consumption, in Bull Markets -> `not_eligible`
- `EP670-principle-003`: Capital Preservation During High-Outflow Life Phases -> `not_eligible`
- `EP450-principle-002`: Reject over-optimistic revenue expectations even for strategically positioned competitors -> `not_eligible`
- `EP450-principle-003`: Accept neurodivergent traits as spectrum-based; prefer self-regulation over medicalization for mild cases -> `not_eligible`
- `EP450-principle-004`: Prefer low-cost brokerage access but diversify to mitigate service gaps -> `not_eligible`
- `EP450-principle-005`: Avoid informal commerce models exposed to regulatory and counterfeit risks -> `not_eligible`
- `EP450-principle-006`: Resist subjective speech suppression; defend platform moderation based on criminal conviction -> `not_eligible`

## Recurring / Conditional Episode-Local Patterns

- `EP674-principle-001`: Prefer Simplicity in Ownership and Market Explanations -> `principle_assisted` / confidence `medium`
- `EP670-principle-001`: Narrative Anticipation Over Valuation Correctness -> `principle_assisted` / confidence `medium`
- `EP670-principle-002`: Directional Focus Over Data Frequency -> `principle_assisted` / confidence `medium`
- `EP450-principle-001`: Prefer cost-effective AI infrastructure if it enables new use cases despite durability risks -> `principle_assisted` / confidence `medium`

## Level 4 Runtime Eligibility

### L4-principle-001 — Prefer Data-Driven Structural Explanations Over Speculative Narratives in Market Analysis

- Runtime mode: `principle_assisted`
- Eligible online: `principle_assisted_only`
- Confidence: `{'evidence_strength': 'high', 'evidence_independence': 'high', 'speaker_specificity': 'high', 'condition_clarity': 'high', 'contradiction_severity': 'minor', 'cross_episode_consistency': 'high', 'time_period_diversity': 'high', 'overall': 'medium'}`
- Genericness: `specific`
- Review: `{'principle_id': 'L4-principle-001', 'classification': 'needs_revision', 'reasons': ['Evidence summary mentions possible tension, but contradictions field is empty.']}`

## Rejected Level 4 Candidates

- `L4-principle-002`: Prioritize Early Positioning in Emerging Narratives Over Valuation Debates — Level 4 requires at least two supporting episodes; retain as Level 3/local hypothesis only.

## Validation

- Level 4 validation: `{'ok': True, 'errors': [], 'warnings': [], 'principle_count': 1, 'confidence_distribution': {'medium': 1}, 'runtime_mode_distribution': {'principle_assisted': 1}, 'genericness_distribution': {'specific': 1}}`
