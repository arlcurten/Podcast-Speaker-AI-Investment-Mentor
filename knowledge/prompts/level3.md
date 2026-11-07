Examine one episode's Level 2 cases for provisional decision patterns. This is
not an episode summary or a permanent speaker policy. Use principle_extraction
only for genuinely recurring decision logic across independent source contexts;
light_principle_grouping for limited/heterogeneous patterns; bypass when no
reusable pattern is supported. Bypass returns an empty principles array.
Use IDs `<episode_id>:P01` etc. supporting_records must be exact host-attributed
Level 2 record IDs from this episode. Counterexamples and contradictions must
also resolve to this episode's records. Single-case abstractions are explicitly
case_derived_hypothesis, never eligible recurring policy. Repeated records from
one discussion are not independent support. Explain the accepted cost, competing
options, preference, switch conditions, falsification and distinctive behavior.
Preserve conditions, uncertainty and exceptions; search for conflicts rather
than cherry-picking support. If the evidence cannot supply a trade-off, bypass
or return a cautious hypothesis, not generic advice. proposed_assessment is a
qualitative proposal; deterministic code constrains confidence and eligibility.

Provide one context_relationships entry for every unordered pair of supporting
record IDs. Explain from actual case contexts whether they refer to the same
event/retelling, distinct events/decision scenarios, or unknown independence.
Different record IDs, threads, episode dates or wording are not sufficient proof.
Use unknown/insufficient_information when ambiguous; general statements cannot
establish independent events. Known overlap/same-thread/same-event evidence is
conservatively grouped by code. A multi-record case_derived_hypothesis is still
ineligible; never rename it recurring just to pass a gate.
