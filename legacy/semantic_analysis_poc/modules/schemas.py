from __future__ import annotations

from typing import Any


def json_schema_response(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": False,
            "schema": schema,
        },
    }


EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "start": {"type": "number"},
        "end": {"type": "number"},
        "merged_ids": {"type": "array", "items": {"type": "integer"}},
        "source_segment_id_ranges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_id": {"type": "integer"},
                    "end_id": {"type": "integer"},
                },
                "required": ["start_id", "end_id"],
                "additionalProperties": False,
            },
        },
        "source_segment_ids": {"type": "array", "items": {"type": "integer"}},
        "quote": {"type": "string"},
    },
    "required": ["start", "end", "merged_ids", "source_segment_id_ranges", "source_segment_ids"],
    "additionalProperties": True,
}


CONFIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidence_strength": {"type": "string", "enum": ["low", "medium", "high"]},
        "evidence_independence": {"type": "string", "enum": ["low", "medium", "high"]},
        "speaker_specificity": {"type": "string", "enum": ["low", "medium", "high"]},
        "condition_clarity": {"type": "string", "enum": ["low", "medium", "high"]},
        "contradiction_severity": {"type": "string", "enum": ["none", "minor", "major"]},
        "cross_episode_consistency": {"type": "string", "enum": ["not_applicable", "low", "medium", "high"]},
        "time_period_diversity": {"type": "string", "enum": ["not_applicable", "low", "medium", "high"]},
        "overall": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": [
        "evidence_strength",
        "evidence_independence",
        "speaker_specificity",
        "condition_clarity",
        "contradiction_severity",
        "cross_episode_consistency",
        "time_period_diversity",
        "overall",
    ],
    "additionalProperties": True,
}


DECISION_TRADEOFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "option_a": {"type": "string"},
        "option_b": {"type": "string"},
        "preferred_option": {"type": "string"},
        "reason_for_preference": {"type": "string"},
        "cost_of_preference": {"type": "string"},
        "switch_conditions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "option_a",
        "option_b",
        "preferred_option",
        "reason_for_preference",
        "cost_of_preference",
        "switch_conditions",
    ],
    "additionalProperties": True,
}


LEVEL1_RESPONSE_FORMAT = json_schema_response(
    "level1_episode_semantic_map",
    {
        "type": "object",
            "properties": {
            "schema_version": {"type": "string"},
            "level": {"type": "integer"},
            "episode_id": {"type": "string"},
            "episode_structure_type": {
                "type": "string",
                "enum": ["single_theme", "multi_theme", "conversational", "qa_heavy", "mixed"],
            },
            "episode_structure_confidence": {"type": "number"},
            "episode_structure_reason": {"type": "string"},
            "main_topic_threads": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "transcript_region_relationships": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "non_contiguous_topic_regions": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "important_reasoning_regions": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "recommended_level3_strategy": {
                "type": "string",
                "enum": ["full_synthesis", "light_consolidation", "bypass"],
            },
            "level3_strategy_reason": {"type": "string"},
            "source_evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
        },
        "required": [
            "level",
            "episode_id",
            "episode_structure_type",
            "episode_structure_confidence",
            "main_topic_threads",
            "transcript_region_relationships",
            "non_contiguous_topic_regions",
            "important_reasoning_regions",
            "recommended_level3_strategy",
            "level3_strategy_reason",
            "source_evidence",
        ],
        "additionalProperties": True,
    },
)


LEVEL2_RESPONSE_FORMAT = json_schema_response(
    "level2_reasoning_records",
    {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string"},
            "level": {"type": "integer"},
            "episode_id": {"type": "string"},
            "reasoning_records": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "reasoning_record_id": {"type": "string"},
                        "topic_thread_id": {"type": "string"},
                        "title": {"type": "string"},
                        "claims": {"type": "array", "items": {"type": "string"}},
                        "observations": {"type": "array", "items": {"type": "string"}},
                        "reasoning_steps": {"type": "array", "items": {"type": "string"}},
                        "assumptions": {"type": "array", "items": {"type": "string"}},
                        "conditions": {"type": "array", "items": {"type": "string"}},
                        "risks": {"type": "array", "items": {"type": "string"}},
                        "exceptions": {"type": "array", "items": {"type": "string"}},
                        "uncertainty": {"type": "array", "items": {"type": "string"}},
                        "decisions_or_preferences": {"type": "array", "items": {"type": "string"}},
                        "actions_or_avoided_actions": {"type": "array", "items": {"type": "string"}},
                        "view_change_conditions": {"type": "array", "items": {"type": "string"}},
                        "speaker_behavior": {"type": "array", "items": {"type": "string"}},
                        "teaching_or_explanation_style": {"type": "array", "items": {"type": "string"}},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "source_evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
                        "confidence": {"type": "number"},
                    },
                    "required": ["reasoning_record_id", "topic_thread_id", "title", "source_evidence"],
                    "additionalProperties": True,
                },
            },
        },
        "required": ["level", "episode_id", "reasoning_records"],
        "additionalProperties": True,
    },
)


LEVEL3_RESPONSE_FORMAT = json_schema_response(
    "level3_optional_consolidation",
    {
        "type": "object",
        "properties": {
            "level": {"type": "integer"},
            "episode_id": {"type": "string"},
            "selected_strategy": {
                "type": "string",
                "enum": ["full_synthesis", "light_consolidation", "bypass"],
            },
            "strategy_reason": {"type": "string"},
            "source_level1_map_id": {"type": "string"},
            "source_reasoning_record_ids": {"type": "array", "items": {"type": "string"}},
            "full_synthesis": {"type": ["object", "null"], "additionalProperties": True},
            "light_consolidation": {"type": ["object", "null"], "additionalProperties": True},
            "bypass": {"type": ["object", "null"], "additionalProperties": True},
        },
        "required": [
            "level",
            "episode_id",
            "selected_strategy",
            "strategy_reason",
            "source_level1_map_id",
            "source_reasoning_record_ids",
            "full_synthesis",
            "light_consolidation",
            "bypass",
        ],
        "additionalProperties": True,
    },
)


LEVEL3_PRINCIPLES_RESPONSE_FORMAT = json_schema_response(
    "level3_episode_principles",
    {
        "type": "object",
        "properties": {
            "level": {"type": "integer"},
            "episode_id": {"type": "string"},
            "selected_strategy": {
                "type": "string",
                "enum": ["principle_extraction", "light_principle_grouping", "bypass"],
            },
            "strategy_reason": {"type": "string"},
            "principles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "principle_id": {"type": "string"},
                        "episode_id": {"type": "string"},
                        "name": {"type": "string"},
                        "domains": {"type": "array", "items": {"type": "string"}},
                        "decision_context": {"type": "string"},
                        "core_preference": {"type": "string"},
                        "reasoning_steps": {"type": "array", "items": {"type": "string"}},
                        "preferred_actions": {"type": "array", "items": {"type": "string"}},
                        "conditions": {"type": "array", "items": {"type": "string"}},
                        "exceptions": {"type": "array", "items": {"type": "string"}},
                        "supporting_records": {"type": "array", "items": {"type": "string"}},
                        "counterexamples": {"type": "array", "items": {"type": "string"}},
                        "contradictions": {"type": "array", "items": {"type": "string"}},
                        "principle_type": {
                            "type": "string",
                            "enum": ["recurring_principle", "case_derived_hypothesis", "conditional_pattern", "exception_pattern"],
                        },
                        "decision_tradeoff": DECISION_TRADEOFF_SCHEMA,
                        "confidence": CONFIDENCE_SCHEMA,
                        "generalization_scope": {"type": "string", "enum": ["episode_local"]},
                        "speaker_specificity": {"type": "string", "enum": ["specific", "partially_specific", "too_generic"]},
                        "genericness_status": {"type": "string", "enum": ["specific", "partially_specific", "too_generic"]},
                        "genericness_reasons": {"type": "array", "items": {"type": "string"}},
                        "distinctive_behavior": {"type": "array", "items": {"type": "string"}},
                        "eligible_for_level4": {"type": "boolean"},
                        "eligible_for_online_reasoning": {"type": "string", "enum": ["false", "limited", "principle_assisted_only"]},
                        "eligibility_reasons": {"type": "array", "items": {"type": "string"}},
                        "runtime_mode_recommendation": {
                            "type": "string",
                            "enum": ["principle_guided", "principle_assisted", "case_only_fallback", "not_eligible"],
                        },
                        "provenance_stats": {"type": "object", "additionalProperties": True},
                        "provisional": {"type": "boolean"},
                        "extraction_strategy": {
                            "type": "string",
                            "enum": ["principle_extraction", "light_principle_grouping", "bypass"],
                        },
                        "evidence_summary": {"type": "string"},
                    },
                    "required": [
                        "principle_id",
                        "episode_id",
                        "name",
                        "domains",
                        "decision_context",
                        "core_preference",
                        "reasoning_steps",
                        "preferred_actions",
                        "conditions",
                        "exceptions",
                        "supporting_records",
                        "counterexamples",
                        "contradictions",
                        "principle_type",
                        "decision_tradeoff",
                        "confidence",
                        "generalization_scope",
                        "speaker_specificity",
                        "genericness_status",
                        "genericness_reasons",
                        "distinctive_behavior",
                        "eligible_for_level4",
                        "eligible_for_online_reasoning",
                        "eligibility_reasons",
                        "runtime_mode_recommendation",
                        "provenance_stats",
                        "provisional",
                        "extraction_strategy",
                        "evidence_summary",
                    ],
                    "additionalProperties": True,
                },
            },
        },
        "required": ["schema_version", "level", "episode_id", "selected_strategy", "strategy_reason", "principles"],
        "additionalProperties": True,
    },
)


LEVEL4_PRINCIPLES_RESPONSE_FORMAT = json_schema_response(
    "level4_cross_episode_principles",
    {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string"},
            "level": {"type": "integer"},
            "principles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "principle_id": {"type": "string"},
                        "name": {"type": "string"},
                        "domains": {"type": "array", "items": {"type": "string"}},
                        "decision_context": {"type": "string"},
                        "core_preference": {"type": "string"},
                        "reasoning_steps": {"type": "array", "items": {"type": "string"}},
                        "preferred_actions": {"type": "array", "items": {"type": "string"}},
                        "conditions": {"type": "array", "items": {"type": "string"}},
                        "exceptions": {"type": "array", "items": {"type": "string"}},
                        "supporting_records": {"type": "array", "items": {"type": "string"}},
                        "supporting_episode_ids": {"type": "array", "items": {"type": "string"}},
                        "supporting_episode_count": {"type": "integer"},
                        "counterexamples": {"type": "array", "items": {"type": "string"}},
                        "contradictions": {"type": "array", "items": {"type": "string"}},
                        "principle_evolution": {"type": "array", "items": {"type": "string"}},
                        "principle_type": {
                            "type": "string",
                            "enum": ["recurring_principle", "case_derived_hypothesis", "conditional_pattern", "exception_pattern"],
                        },
                        "decision_tradeoff": DECISION_TRADEOFF_SCHEMA,
                        "confidence": CONFIDENCE_SCHEMA,
                        "generalization_scope": {"type": "string", "enum": ["cross_episode_candidate"]},
                        "speaker_specificity": {"type": "string", "enum": ["specific", "partially_specific", "too_generic"]},
                        "genericness_status": {"type": "string", "enum": ["specific", "partially_specific", "too_generic"]},
                        "genericness_reasons": {"type": "array", "items": {"type": "string"}},
                        "distinctive_behavior": {"type": "array", "items": {"type": "string"}},
                        "eligible_for_level4": {"type": "boolean"},
                        "eligible_for_online_reasoning": {"type": "string", "enum": ["false", "limited", "principle_assisted_only", "true"]},
                        "eligibility_reasons": {"type": "array", "items": {"type": "string"}},
                        "runtime_mode_recommendation": {
                            "type": "string",
                            "enum": ["principle_guided", "principle_assisted", "case_only_fallback", "not_eligible"],
                        },
                        "provenance_stats": {"type": "object", "additionalProperties": True},
                        "derived_from": {"type": "array", "items": {"type": "string"}},
                        "evidence_summary": {"type": "string"},
                    },
                    "required": [
                        "principle_id",
                        "name",
                        "domains",
                        "decision_context",
                        "core_preference",
                        "reasoning_steps",
                        "preferred_actions",
                        "conditions",
                        "exceptions",
                        "supporting_records",
                        "supporting_episode_ids",
                        "supporting_episode_count",
                        "counterexamples",
                        "contradictions",
                        "principle_evolution",
                        "principle_type",
                        "decision_tradeoff",
                        "confidence",
                        "generalization_scope",
                        "speaker_specificity",
                        "genericness_status",
                        "genericness_reasons",
                        "distinctive_behavior",
                        "eligible_for_level4",
                        "eligible_for_online_reasoning",
                        "eligibility_reasons",
                        "runtime_mode_recommendation",
                        "provenance_stats",
                        "derived_from",
                        "evidence_summary",
                    ],
                    "additionalProperties": True,
                },
            },
        },
        "required": ["schema_version", "level", "principles"],
        "additionalProperties": True,
    },
)
