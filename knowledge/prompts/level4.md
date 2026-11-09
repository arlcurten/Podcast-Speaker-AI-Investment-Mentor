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

Carry forward reviewed_annotations in case evidence, including their method and
limitations. Corrections remain episode-local; interpretive cautions about spoken
slips, inconsistent arithmetic or attribution are not replacement facts. Preserve
unresolved numeric/wording uncertainty rather than selecting a convenient value.
Audio-model agreement is not independent evidence or proof of human listening.
Annotation anchors and notes never add supporting cases or independent contexts.
An [Agent clarification] remains reviewer commentary across abstraction levels.
Never promote it into source context, speaker preference, causal explanation or
independent support. Carry relevant limitations forward without filling the gaps.

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

Consolidate comparable decision processes, not winning stories. Do not prefer a
profitable case over a conflicting case simply because its reported outcome is
better, and do not use self-reported success as independent validation. Preserve
source uncertainty about results and causal explanations. Check both cases'
available information at decision time; avoid hindsight. Remove incidental names
only when doing so preserves horizon, position size, risk constraints and other
decision-changing conditions. If those conditions differ, keep scoped variants
or explain the difference rather than flattening them into one universal tip.
