"""Independent profile proposals, not knowledge-layer artifacts or approvals."""
from __future__ import annotations

import copy

from knowledge.schemas import TEXT, STRINGS, IDS, OPTIONAL_TEXT, array, enum, obj

VERSION = "profile.v1"
EPISODE_CONTEXT = "episode_context.v1"
DIMENSION = obj(value=enum("low", "medium", "high", "unknown"), reason=TEXT)
CATEGORIES = array(enum("investment_decision_behavior", "general_decision_preference", "values_and_beliefs",
                        "emotional_response_or_possible_bias", "other"), 1)
QUOTE = obj(segment_id={"type": "integer", "minimum": 0}, start={"type": "number", "minimum": 0},
            end={"type": "number", "minimum": 0}, text=TEXT)
NOTE = obj(merged_ids=IDS, note={**TEXT, "maxLength": 600})
TRADEOFF = {"anyOf": [obj(considerations=STRINGS, preferred_option=OPTIONAL_TEXT,
                         accepted_cost=OPTIONAL_TEXT, switch_conditions=STRINGS), {"type": "null"}]}
ACTION = {"anyOf": [obj(kind=enum("reported_action", "reported_inaction", "hypothetical", "unknown"),
                       description=OPTIONAL_TEXT), {"type": "null"}]}
OUTCOME = {"anyOf": [obj(description=OPTIONAL_TEXT, known_at=OPTIONAL_TEXT,
                        verification=enum("self_reported", "unknown")), {"type": "null"}]}
ITEM = obj(
    evidence_id=TEXT, evidence_kind=enum("direct_statement", "reported_action", "inferred_tendency"),
    speaker_attribution=enum("host", "quoted_other", "guest", "listener", "unknown"),
    categories=CATEGORIES, domains=array(TEXT, 1), merged_ids=IDS, quotes=array(QUOTE, 1),
    source_observation=TEXT, interpretation=OPTIONAL_TEXT, basis_evidence_ids=STRINGS,
    decision_context=obj(mode=enum("literal_report", "hypothetical", "anecdotal", "quoted", "unknown"),
                         description=TEXT, event_reference=OPTIONAL_TEXT, event_time=OPTIONAL_TEXT),
    investment_relevance=obj(status=enum("explicit", "hypothesized", "unknown", "not_claimed"), explanation=TEXT),
    tradeoff=TRADEOFF, action=ACTION, outcome=OUTCOME, proposed_salience=DIMENSION,
    conditions=STRINGS, exceptions=STRINGS, uncertainty=STRINGS, alternative_explanations=STRINGS,
    supplementary_notes=array(NOTE),
    proposed_confidence=obj(evidence_fidelity=DIMENSION, attribution_certainty=DIMENSION, source_clarity=DIMENSION))
HYPOTHESIS = obj(
    hypothesis_id=TEXT, label=TEXT, conditional_description=TEXT,
    hypothesis_type=enum("self_described_preference", "observed_reported_pattern", "low_frequency_signal",
                         "contextual_tendency", "statement_action_gap"),
    categories=CATEGORIES, domains=array(TEXT, 1), supporting_evidence=array(TEXT, 1), counterevidence=STRINGS,
    conditions=STRINGS, exceptions=STRINGS, switch_conditions=STRINGS, alternative_explanations=STRINGS,
    falsification_conditions=STRINGS, limitations=STRINGS,
    proposed_salience=DIMENSION,
    statement_action_relationships=array(obj(statement_id=TEXT, action_id=TEXT,
        relationship=enum("agreement", "apparent_mismatch", "context_resolved_difference", "unresolved"), reason=TEXT)),
    proposed_confidence=obj(evidence_fidelity=DIMENSION, attribution_certainty=DIMENSION,
                            context_consistency=DIMENSION, generalization_support=DIMENSION))
PROPOSALS = {
    "evidence": obj(episode_id=TEXT, summary=TEXT, items=array(ITEM), unselected_regions=array(NOTE)),
    "synthesis": obj(summary=TEXT, hypotheses=array(HYPOTHESIS), evidence_accounting=array(obj(
        evidence_id=TEXT, disposition=enum("in_hypotheses", "retained_without_hypothesis"),
        hypothesis_ids=STRINGS, reason=TEXT))),
}

PROMPTS = {
    "evidence": """Extract provisional behavioral evidence from this entire episode, independently of knowledge L1-L4.
Read all supplied raw rows and mapped transcript regions, including non-investment material. Use broad multi-label
categories: decision behavior, general preferences, values, reported feelings or possible biases, and other material.
There is no required trait, bias, role, frequency or item quota. Zero items is valid. Preserve isolated and uncertain
observations; do not demand an investment connection. Account for unselected regions with anchored reasons. Regions
may support an item while containing an omitted fragment; this is not certification of complete semantic extraction.
Use evidence IDs <episode_id>:E1, E2, ... . Separate direct_statement, reported_action and inferred_tendency items.
source_observation is only what is expressed/reported, without invented motives, causes, events, context or outcomes.
interpretation and supplementary_notes are explicitly analyst commentary, never speaker quotations or new evidence.
An inferred tendency needs an interpretation and existing direct/action basis_evidence_ids with matching source regions.
For direct/action items basis_evidence_ids must be empty. Distinguish the host from guests, listeners and quoted people.
Quotes must reproduce a whole raw segment's text, ID, start and end exactly. Cite the corresponding merged IDs.
Do not correct raw quotations or turn terminology suggestions into evidence. Retain source limitations and uncertainty.
Do not infer clinical conditions, private motives or sensitive attributes; minimize incidental third-party information.
Text alone cannot establish vocal emotion or sincerity. Reported actions/results are not verified brokerage history.
Keep event_time and event_reference null when unspecified; publication time is not event time. Never create event IDs
from episode IDs. Hypothetical, anecdotal, quoted and unknown contexts are valid. Cross-domain relevance is not assumed.
Keep tradeoff, action and outcome null when unspecified. Alternatives need not be binary. Preserve accepted costs,
reported inaction and switch conditions when stated. Salience is proposed importance, not frequency or confidence.
Confidence dimensions are proposals requiring review, not computed certification. Return only the supplied schema.""",
    "synthesis": """Synthesize provisional conditional speaker-profile hypotheses from ALL supplied profile evidence.
No knowledge L1-L4 input is supplied or permitted. Preserve the distinction between direct statements, reported actions
and analyst interpretations. Inferred items are not independent observations of their own supporting evidence.
Use hypothesis IDs PROFILE:H1, H2, ... . Do not force stable traits, bias labels, investing relevance or a coherent persona.
Zero hypotheses is valid. Keep low-frequency evidence even when no hypothesis is justified. Account for every evidence
ID exactly once, with the exact hypothesis IDs using it as support or counterevidence, or retained_without_hypothesis.
Only host-attributed evidence can support a host hypothesis. Preserve conflicts, conditions, exceptions, alternative
explanations, uncertain chronology and falsifiers. Compare stated preferences and reported actions explicitly when
supported, without assuming outcomes establish causes. Do not supply missing events, private motives or diagnoses.
Non-investment evidence stands on its own; transfer to investing must remain an explicit hypothesis unless supported.
Publication dates, separate episodes, repeated narratives, high salience and profits do not prove independent events.
One observation may support a narrow self-description or low-frequency signal, not established recurring personality.
Retain proposed salience independently of frequency/confidence, and describe uncertain evolution in limitations.
Confidence dimensions are proposed judgments, not approvals. All output remains provisional, with global-profile and
dynamic-retrieval usage undecided. Source notes and interpretations are commentary, not additional speaker evidence.
Return only the supplied schema.""",
}


def prompt(stage: str, commentary_scope=None) -> str:
    base = PROMPTS[stage]
    if commentary_scope == EPISODE_CONTEXT:
        base += "\ncommentary_scope: episode_context.v1. Supplementary notes are model_analyst_not_speaker commentary."
        if stage == "evidence":
            base += (" Each supplementary note may cite any existing merged IDs from this SAME episode, including"
                     " regions fully disjoint from the item's primary anchors. Note IDs must be nonempty and unique."
                     " Keep primary merged_ids and exact quotes limited to the observation's own evidence."
                     " Notes do not supply inferred-tendency basis anchors or account for unselected regions.")
        base += (" Supplementary context is never additional independent evidence or primary source coverage."
                 " Preserve source warnings; do not fill missing facts, motives, context or reasoning.")
    return base


def schema(stage: str) -> dict:
    return copy.deepcopy(PROPOSALS[stage])


def validate(stage: str, content: dict) -> None:
    from jsonschema import Draft202012Validator

    error = next(Draft202012Validator(schema(stage)).iter_errors(content), None)
    if error is not None:
        raise ValueError(f"Profile {stage} schema invalid ({error.validator}).")
