# Cross-Episode Level 4 Decision Principles Review

- Principle count: `1`
- Validation ok: `True`
- Review summary: `{'needs_revision': 1}`
- Cost USD: `0.00212042`

## Validation Warnings


## Candidate Principles

### L4-principle-001 — Prefer Data-Driven Structural Explanations Over Speculative Narratives in Market Analysis

- Codex review: `needs_revision`
- Review reasons: Evidence summary mentions possible tension, but contradictions field is empty.
- Confidence: `{'evidence_strength': 'high', 'evidence_independence': 'high', 'speaker_specificity': 'high', 'condition_clarity': 'high', 'contradiction_severity': 'minor', 'cross_episode_consistency': 'high', 'time_period_diversity': 'high', 'overall': 'medium'}`
- Runtime mode: `principle_assisted`
- Eligible online: `principle_assisted_only`
- Genericness: `specific` —
- Provenance stats: `{'supporting_record_count': 4, 'supporting_episode_ids': ['EP450', 'EP670', 'EP674'], 'supporting_episode_count': 3, 'unique_level1_thread_count': 4, 'unique_level1_threads': ['EP450:EP450-thread-003', 'EP670:EP670-thread-008', 'EP674:EP674-thread-002', 'EP674:EP674-thread-003'], 'distinct_decision_context_count': 4, 'source_region_count': 12, 'independent_source_context_count': 4, 'overlapping_or_duplicate_source_regions': 1}`
- Supporting episodes: EP450, EP670, EP674
- Decision context: Interpreting market movements or company-specific events when multiple causal explanations are possible.
- Core preference: Favor mechanical, structural, or data-grounded explanations over emotionally charged or media-amplified narratives.
- Decision trade-off: `{'option_a': 'Accept dominant media or emotional narrative (e.g., war fears, consumer outrage, earnings hype).', 'option_b': 'Demand data confirmation and prefer structural or mechanical explanations.', 'preferred_option': 'option_b', 'reason_for_preference': 'Narratives are often unrepresentative or lag reality; data and structure provide earlier, more reliable signals.', 'cost_of_preference': 'Risk of missing early momentum if narrative precedes data confirmation.', 'switch_conditions': ['If data confirms narrative intensity (e.g., oil spike, sales drop, regulatory action).', 'If market enters a regime where sentiment drives prices independently of fundamentals.']}`
- Conditions: Applies when observable data is available to test narratives.; More relevant in developed or transparent markets where data proxies are reliable.
- Exceptions: Major geopolitical events with direct supply chain impact may override structural explanations (EP674-reasoning-002 view_change_conditions).; Narratives can become self-fulfilling if widely adopted (EP670-reasoning-002).
- Evidence summary: This principle recurs across three episodes in distinct but comparable decision contexts: market corrections (EP674), information filtering (EP670), and earnings expectations (EP450). In each case, the speaker rejects a popular or emotionally salient narrative in favor of a data-grounded or structural explanation. The reasoning pattern—testing narratives against observable proxies—is consistent. No contradictions exist. The speaker repeatedly accepts the cost of potential delay in favor of accuracy. This is not generic skepticism but a specific, disciplined approach to narrative filtering grounded in observable data. It is distinctive in its reliance on cross-market indicators (e.g., oil prices) and regional industry sentiment as validation tools.

Supporting records:
- `EP674-reasoning-002` (EP674): Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears
- `EP674-reasoning-005` (EP674): Apple's Price Hike May Not Deter Core Customers Despite Online Backlash
- `EP670-reasoning-007` (EP670): Information overload as market noise
- `EP450-reasoning-003` (EP450): AMD財報後下跌合理：市場過度高估AI營收

Derived from:
- `EP674-principle-001`: Prefer Simplicity in Ownership and Market Explanations
- `EP674-reasoning-002`: Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears
- `EP674-reasoning-005`: Apple's Price Hike May Not Deter Core Customers Despite Online Backlash
- `EP670-reasoning-007`: Information overload as market noise
- `EP450-reasoning-003`: AMD財報後下跌合理：市場過度高估AI營收

## Rejected Level 4 Candidates

### L4-principle-002 — Prioritize Early Positioning in Emerging Narratives Over Valuation Debates

- Rejection reason: Level 4 requires at least two supporting episodes; retain as Level 3/local hypothesis only.
- Supporting episodes: EP670
- Runtime mode: `not_eligible`
- Confidence: `{'evidence_strength': 'low', 'evidence_independence': 'low', 'speaker_specificity': 'high', 'condition_clarity': 'high', 'contradiction_severity': 'none', 'cross_episode_consistency': 'low', 'time_period_diversity': 'low', 'overall': 'low'}`

## Level 2 -> Level 3 -> Level 4 Examples

### Example: Prefer Data-Driven Structural Explanations Over Speculative Narratives in Market Analysis

Level 2 cases:
- `EP674-reasoning-002` (EP674): Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears
- `EP674-reasoning-005` (EP674): Apple's Price Hike May Not Deter Core Customers Despite Online Backlash
- `EP670-reasoning-007` (EP670): Information overload as market noise
- `EP450-reasoning-003` (EP450): AMD財報後下跌合理：市場過度高估AI營收

Level 3 abstraction:
- `EP674-principle-001`: Prefer Simplicity in Ownership and Market Explanations — Simple, transparent structures and data-aligned narratives over complex or speculative ones.

Level 4 abstraction:
- Favor mechanical, structural, or data-grounded explanations over emotionally charged or media-amplified narratives.
- Preserved details: conditions=['Applies when observable data is available to test narratives.', 'More relevant in developed or transparent markets where data proxies are reliable.']; exceptions=['Major geopolitical events with direct supply chain impact may override structural explanations (EP674-reasoning-002 view_change_conditions).', 'Narratives can become self-fulfilling if widely adopted (EP670-reasoning-002).']
- Not generalized: No unsupported universal claim added.
