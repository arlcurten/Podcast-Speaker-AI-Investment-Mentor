"""Model proposal schemas. Provenance and runtime eligibility are computed, never voted on."""
from __future__ import annotations

import copy

VERSION = "knowledge.v2"
TEXT = {"type": "string", "minLength": 1, "pattern": "\\S"}
STRINGS = {"type": "array", "items": TEXT}
IDS = {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1, "uniqueItems": True}
DOMAINS = ["investment", "market", "industry", "portfolio", "behavioral_investing", "business", "career", "personal", "other"]


def enum(*values):
    return {"type": "string", "enum": list(values)}


def obj(**properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(item, minimum=0):
    return {"type": "array", "items": item, "minItems": minimum}


DOMAIN_TAGS = array(enum(*DOMAINS), 1)
LINK = obj(record_id=TEXT, explanation=TEXT)
OPTIONAL_TEXT = {"anyOf": [TEXT, {"type": "null"}]}
DECISION_CONTEXT = obj(
    kind=enum("historical_event", "hypothetical_scenario", "general_statement", "unknown"),
    description=TEXT, event_reference=OPTIONAL_TEXT, event_time=OPTIONAL_TEXT)
CONTEXT_RELATION = obj(
    record_ids={**array(TEXT, 2), "maxItems": 2, "uniqueItems": True},
    relationship=enum("same_context", "independent", "unknown"),
    basis=enum("same_event", "retelling", "distinct_events", "distinct_decision_scenarios", "insufficient_information"),
    explanation=TEXT)
COUNTEREVIDENCE_DISPOSITION = obj(
    source_principle_id=TEXT, record_id=TEXT, kind=enum("counterexample", "contradiction"),
    disposition=enum("retained", "context_excluded", "unresolved"), reason=TEXT)
TRADEOFF = obj(option_a=TEXT, option_b=TEXT, preferred_option=TEXT, reason_for_preference=TEXT,
               cost_of_preference=TEXT, switch_conditions=STRINGS)
ASSESSMENT = obj(evidence_strength=enum("low", "medium", "high"),
                 speaker_specificity=enum("low", "medium", "high"),
                 condition_clarity=enum("low", "medium", "high"),
                 contradiction_severity=enum("none", "minor", "major"),
                 rationale=TEXT)
PRINCIPLE = obj(principle_id=TEXT, name=TEXT, domains=DOMAIN_TAGS, decision_context=TEXT,
                core_preference=TEXT, reasoning_steps=array(TEXT, 1), preferred_actions=STRINGS,
                conditions=STRINGS, exceptions=STRINGS, supporting_records=array(TEXT, 1),
                counterexamples=array(LINK), contradictions=array(LINK),
                principle_type=enum("recurring_principle", "case_derived_hypothesis", "conditional_pattern", "exception_pattern"),
                distinctive_behavior=TEXT, decision_tradeoff=TRADEOFF,
                falsification_conditions=STRINGS, evidence_summary=TEXT,
                proposed_assessment=ASSESSMENT, context_relationships=array(CONTEXT_RELATION))
L3_PRINCIPLE = obj(**PRINCIPLE["properties"], episode_id=TEXT)
L4_PRINCIPLE = obj(**PRINCIPLE["properties"], derived_from=STRINGS,
                   shared_decision_logic=TEXT, context_differences=STRINGS,
                   principle_evolution=STRINGS, counterevidence_dispositions=array(COUNTEREVIDENCE_DISPOSITION))

PROPOSALS = {
    "level1": obj(episode_id=TEXT, episode_summary=TEXT,
                  threads=array(obj(thread_id=TEXT, title=TEXT, summary=TEXT, domains=DOMAIN_TAGS,
                                    merged_ids=IDS, related_threads=array(obj(thread_id=TEXT, relationship=TEXT)))),
                  excluded_regions=array(obj(merged_ids=IDS, category=enum("advertisement", "logistics", "non_reasoning", "unintelligible"), reason=TEXT))),
    "level2": obj(episode_id=TEXT, thread_dispositions=array(obj(
        thread_id=TEXT, status=enum("covered", "no_reusable_reasoning", "insufficient_source"),
        record_ids=STRINGS, explanation=TEXT,
        unextracted_regions=array(obj(merged_ids=IDS, reason=TEXT)))), cases=array(obj(
        record_id=TEXT, title=TEXT, thread_ids=array(TEXT, 1),
        speaker_attribution=enum("host", "listener", "guest", "unknown"),
        claims=array(TEXT, 1), reasoning_steps=STRINGS, assumptions=STRINGS, conditions=STRINGS,
        risks=STRINGS, exceptions=STRINGS, uncertainty=STRINGS, contradictions=STRINGS,
        preferred_actions=STRINGS, decision_preferences=STRINGS, view_change_conditions=STRINGS,
        speaker_behavior=STRINGS, tags=STRINGS, domains=DOMAIN_TAGS, merged_ids=IDS,
        decision_context=DECISION_CONTEXT))),
    "level3": obj(episode_id=TEXT, strategy=enum("principle_extraction", "light_principle_grouping", "bypass"),
                  strategy_reason=TEXT, principles=array(L3_PRINCIPLE)),
    "level4": obj(consolidation_reason=TEXT, principles=array(L4_PRINCIPLE)),
}


def schema(stage: str, *, candidate: bool = False) -> dict:
    result = copy.deepcopy(PROPOSALS[stage])
    if candidate:
        from .candidates import proposal_schema
        result = proposal_schema(stage, result)
    return result


def validate_proposal(stage: str, payload: dict, *, candidate: bool = False) -> None:
    from jsonschema import Draft202012Validator

    errors = list(Draft202012Validator(schema(stage, candidate=candidate)).iter_errors(payload))
    if errors:
        # Do not echo model content, source text or paths into public console logs.
        first = errors[0]
        raise ValueError(f"{stage} proposal schema invalid at {'.'.join(map(str, first.path)) or 'root'} ({first.validator})")
