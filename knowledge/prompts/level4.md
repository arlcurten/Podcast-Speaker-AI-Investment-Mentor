Identify a small, not fixed-count set of revisable cross-episode decision
patterns in Traditional Chinese. Input includes eligible Level 3 abstractions
and actual Level 2 cases, including non-supporting cases. Cases alone are the
evidence authority. A principle plus its source cases is not independent evidence.
Use IDs `CROSS:P01` etc; require at least two independently supporting episodes
and contexts. derived_from may reference eligible Level 3 IDs or supporting
Level 2 IDs only. Single-case Level 3 hypotheses are not eligible inputs; their
Level 2 cases can still contribute when genuinely independent cross-episode
evidence exists. Empty output is valid and preferable to forced principles.
For every merge ask: same decision preference, or only similar subject words?
Require comparable context, shared trade-off, reasoning sequence, action tendency
and compatible conditions. Preserve explicit differences, contradictions,
counterexamples and uncertain evolution in historical order. What is distinctive
to this speaker? What cost do they repeatedly accept? What would falsify the
pattern? Generic 'watch risk/use data' advice is insufficient. Never merge solely
on risk/data/valuation keywords. proposed_assessment is qualitative, not measured
independence; code calculates episode/context counts and constrains runtime use.

Provide context_relationships for every unordered supporting-case pair as in
Level 3. Compare the underlying decision/event, not the episode containing its
retelling. Different publication dates alone are never an independence basis.
Use unknown when the source cannot establish independence. General statements
or two retellings of one trade must not produce an eligible recurring principle.

Inspect upstream_counterevidence, including ineligible L3 abstractions. For
every upstream principle whose supporting_records overlap this candidate,
give a counterevidence_dispositions entry for each counterexample/contradiction
link (source_principle_id, record_id, kind). Account for these links even when
derived_from names only cases, not that abstraction. Each disposition needs a
source-grounded reason: retained (include in the corresponding L4 evidence
list), context_excluded (explain the actual scope/condition difference; do not
include in that evidence list), or unresolved (retain the link; blocks runtime
eligibility). Do not mechanically call every scope difference a contradiction,
and never erase a known conflict to preserve the principle. Cases remain the
only evidence; disposition links are not additional independent observations.
