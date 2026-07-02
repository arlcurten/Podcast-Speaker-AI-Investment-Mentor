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


LEVEL1_RESPONSE_FORMAT = json_schema_response(
    "level1_episode_semantic_map",
    {
        "type": "object",
        "properties": {
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
