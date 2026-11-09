Propose packet-local conditional behavioral families from ALL supplied Profile evidence.
Input uses compact_profile_discovery.v2: lossless compact_profile.v1 tables plus exact
source_annotation_subset.v1 references. Read serialization_legend before interpreting
columns, shared values, missing-versus-null cells, quote indices and grounding references.
Annotation subset references resolve ordered IDs against the same episode's SHA-bound source
catalog; the header comes from that source or an explicit inline envelope. Do not infer issues.
Each sources entry is identified by its source.episode_id. Expand references conceptually without
tools; no semantic fields are omitted and repeated table references add no evidence weight.
The packet is a transport bound, not a semantic segmentation or an importance ranking.
Read every supplied observation, interpretation, source warning and known relationship.
Keep rare, non-investment, non-host and inferred items; do not force a stable personality,
investment relevance, fixed family count or minimum frequency. Zero families is valid.
Use local family IDs F1, F2, ... . An item may have multiple justified memberships.
Account for EVERY supplied evidence_id exactly once as assigned or retained_without_family,
including reference-only items. Non-host items cannot support or contradict a host pattern.
Preserve evidence_kind and speaker_attribution. Inferred tendencies are interpretations of
their basis observations, not additional observations. Hypotheses are navigation only:
account for every full hypothesis_key (artifact SHA plus local ID), including different
versions of the same local hypothesis. Never treat a hypothesis as independent evidence.
Owned IDs specify transport ownership only. Repeated reference context adds no evidential
weight. Keep known basis, support, counterevidence, typed statement/action comparisons and
recovered comparison caveats visible; do not resolve them by omission or invented facts.
Primary quotes and anchors remain the evidence. Supplementary context and editorial notes
retain their original model/reviewer authority, not the speaker's voice, primary coverage
or inference basis. Preserve exact source limitations, holds, uncertainty and chronology.
Do not invent motives, diagnoses, missing events, causes, outcomes or reasoning steps.
Publication dates, episode counts, repeated narratives and profits do not establish
independent events. No independence count or source acceptance is being requested.
Families remain unreviewed grouping proposals. Cross-packet reconciliation and global
semantic counter review are NOT performed by this call. Return only the supplied schema.
