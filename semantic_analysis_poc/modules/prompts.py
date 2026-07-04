from __future__ import annotations

import json
from typing import Any

from modules.evidence import compact_id_refs


SYSTEM_PROMPT = """You extract investment-reasoning semantics from Gooaye podcast transcripts.

Rules:
- Use the whole episode context.
- Many episodes may have no central theme.
- The speaker may move between unrelated topics.
- Do not invent coherence.
- Do not force a narrative.
- Do not force fixed-duration topic segments.
- Do not use fixed episode-position weighting.
- Do not force single-label classification.
- Topic threads may be long, overlapping, or non-contiguous.
- Level 2 is allowed to be the final useful episode-level output.
- Every high-level statement must preserve source evidence.
- Keep outputs concise enough to be reviewable.
- Do not enumerate long raw source ID lists.
- Evidence format:
  - Use merged_ids for merged transcript references.
  - Use source_segment_id_ranges for continuous raw segment IDs.
  - Use source_segment_ids only for isolated raw IDs.
  - The full raw mapping must be reconstructable from Phase 1 data; do not ask the output to list every raw segment ID.
- Preserve uncertainty, assumptions, risks, exceptions, counterexamples, opinion changes, decisions, preferences, avoided actions, speaker behavior, and teaching style.
- Return only valid JSON.
"""


def transcript_payload(segments: list[dict[str, Any]]) -> str:
    compact = [
        {
            "merged_id": s.get("merged_id", s.get("id")),
            "start": s["start"],
            "end": s["end"],
            **compact_id_refs([int(x) for x in s.get("source_segment_ids", [])]),
            "text": s.get("normalized_text_zh_tw") or s.get("text") or s.get("raw_text", ""),
        }
        for s in segments
    ]
    return json.dumps(compact, ensure_ascii=False)


def level1_prompt(episode_id: str, segments: list[dict[str, Any]], terminology: str) -> list[dict[str, str]]:
    user = f"""Episode: {episode_id}

Terminology table:
{terminology}

Normalized merged transcript segments as JSON:
{transcript_payload(segments)}

Create Level 1: Episode Semantic Map.

Keep Level 1 compact:
- Return at most 8 main_topic_threads.
- Return at most 8 important_reasoning_regions.
- Each thread should use at most 3 region_refs.
- Each evidence item should use concise quotes and compact evidence references as described in the system rules.

Return JSON with this shape:
{{
  "level": 1,
  "episode_id": "...",
  "episode_structure_type": "single_theme|multi_theme|conversational|qa_heavy|mixed",
  "episode_structure_confidence": 0.0,
  "episode_structure_reason": "...",
  "main_topic_threads": [
    {{
      "topic_thread_id": "EP674-thread-001",
      "title": "...",
      "summary": "...",
      "region_refs": [{{"start": 0.0, "end": 0.0, "merged_ids": [0], "source_segment_id_ranges": [{{"start_id": 0, "end_id": 3}}], "source_segment_ids": []}}],
      "importance_reason": "...",
      "is_non_contiguous": false,
      "continues_or_expands": []
    }}
  ],
  "transcript_region_relationships": [],
  "non_contiguous_topic_regions": [],
  "important_reasoning_regions": [],
  "recommended_level3_strategy": "full_synthesis|light_consolidation|bypass",
  "level3_strategy_reason": "...",
  "source_evidence": []
}}
"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def level2_prompt(
    episode_id: str,
    segments: list[dict[str, Any]],
    level1: dict[str, Any],
    terminology: str,
) -> list[dict[str, str]]:
    user = f"""Episode: {episode_id}

Terminology table:
{terminology}

Level 1 semantic map:
{json.dumps(level1, ensure_ascii=False)}

Normalized merged transcript segments as JSON:
{transcript_payload(segments)}

Create Level 2: Reasoning Records for important topic threads.

Keep Level 2 compact:
- Return at most 12 reasoning_records.
- Prefer complete, high-value reasoning records over many small records.
- Each record should use at most 4 source_evidence items.
- Each evidence item should use concise quotes and compact evidence references as described in the system rules.

Return JSON with this shape:
{{
  "level": 2,
  "episode_id": "...",
  "reasoning_records": [
    {{
      "reasoning_record_id": "EP674-reasoning-001",
      "topic_thread_id": "...",
      "title": "...",
      "claims": [],
      "observations": [],
      "reasoning_steps": [],
      "assumptions": [],
      "conditions": [],
      "risks": [],
      "exceptions": [],
      "uncertainty": [],
      "decisions_or_preferences": [],
      "actions_or_avoided_actions": [],
      "view_change_conditions": [],
      "speaker_behavior": [],
      "teaching_or_explanation_style": [],
      "tags": [],
      "source_evidence": [
        {{"start": 0.0, "end": 0.0, "merged_ids": [0], "source_segment_id_ranges": [{{"start_id": 0, "end_id": 3}}], "source_segment_ids": [], "quote": "..."}}
      ],
      "confidence": 0.0
    }}
  ]
}}
"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def level3_prompt(
    episode_id: str,
    level1: dict[str, Any],
    level2: dict[str, Any],
) -> list[dict[str, str]]:
    strategy = level1.get("recommended_level3_strategy")
    user = f"""Episode: {episode_id}

Level 1 semantic map:
{json.dumps(level1, ensure_ascii=False)}

Level 2 reasoning records:
{json.dumps(level2, ensure_ascii=False)}

Create Level 3: Optional Episode Consolidation using selected strategy: {strategy}

Rules by strategy:
- full_synthesis: use only when the episode has a clear central theme. Consolidate major views, cases, decision principles, risks, exceptions, repeated arguments, corrections, contradictions, opinion changes, and overall stance.
- light_consolidation: produce topic inventory, merge duplicate or substantially overlapping reasoning records, identify cross-topic links, corrections, contradictions, and cross-episode candidate records. Do not invent an episode-level central message.
- bypass: preserve Level 2 as canonical semantic output. Produce only minimal metadata explaining why consolidation was skipped. Do not generate artificial synthesis.

Return JSON with this shape:
{{
  "level": 3,
  "episode_id": "...",
  "selected_strategy": "full_synthesis|light_consolidation|bypass",
  "strategy_reason": "...",
  "source_level1_map_id": "level1",
  "source_reasoning_record_ids": [],
  "full_synthesis": null,
  "light_consolidation": null,
  "bypass": null
}}

Only populate the object matching selected_strategy; keep the other two null.
"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


PRINCIPLE_SYSTEM_PROMPT = """You extract provisional decision principles from already-extracted podcast reasoning records.

Rules:
- Work from Level 2 reasoning records, not from generic investment knowledge.
- Principles are abstractions, not independent evidence.
- Do not upgrade one-off comments into stable speaker principles.
- Preserve conditions, exceptions, uncertainty, risks, contradictions, and counterexamples.
- Prefer cautious, evidence-linked wording over broad advice.
- Return bypass when evidence is unrelated, too weak, or only one narrow case exists.
- Do not invent speaker preferences.
- Do not hide conflicts.
- Use only valid reasoning record IDs as evidence references.
- Return only valid JSON.
"""


def level3_principles_prompt(
    episode_id: str,
    level1: dict[str, Any],
    level2: dict[str, Any],
) -> list[dict[str, str]]:
    records = level2.get("reasoning_records", [])
    user = f"""Episode: {episode_id}

Level 1 semantic map:
{json.dumps(level1, ensure_ascii=False)}

Level 2 reasoning records:
{json.dumps(level2, ensure_ascii=False)}

Create Level 3: Episode-Level Provisional Principles.

Choose exactly one selected_strategy:
- principle_extraction: multiple Level 2 records in this episode show a recurring decision pattern with enough evidence.
- light_principle_grouping: related patterns exist, but evidence is limited or heterogeneous. Produce cautious low/medium-confidence principles.
- bypass: records are unrelated, evidence is too weak, only one narrow special case exists, or no reusable reasoning pattern is supported.

Requirements:
- Inspect all {len(records)} Level 2 records.
- Identify recurring decision logic, not just repeated topics.
- Distinguish reusable principles from isolated opinions.
- Preserve context, conditions, uncertainty, exceptions, contradictions, counterexamples, and speaker behavior.
- Avoid generic investment advice.
- Avoid inventing speaker preferences.
- Every principle must reference supporting_records from this episode's Level 2 IDs.
- counterexamples and contradictions must reference valid Level 2 IDs when possible.
- provisional must always be true.
- Use high confidence sparingly.
- Explain why each principle is provisional in evidence_summary.

Return JSON with this shape:
{{
  "level": 3,
  "episode_id": "{episode_id}",
  "selected_strategy": "principle_extraction|light_principle_grouping|bypass",
  "strategy_reason": "...",
  "principles": [
    {{
      "principle_id": "{episode_id}-principle-001",
      "episode_id": "{episode_id}",
      "name": "...",
      "domains": ["investment", "market", "portfolio"],
      "decision_context": "...",
      "core_preference": "...",
      "reasoning_steps": [],
      "preferred_actions": [],
      "conditions": [],
      "exceptions": [],
      "supporting_records": ["{episode_id}-reasoning-001"],
      "counterexamples": [],
      "contradictions": [],
      "confidence": "low|medium|high",
      "provisional": true,
      "extraction_strategy": "principle_extraction|light_principle_grouping|bypass",
      "evidence_summary": "..."
    }}
  ]
}}

If selected_strategy is bypass, return an empty principles array and explain why.
"""
    return [{"role": "system", "content": PRINCIPLE_SYSTEM_PROMPT}, {"role": "user", "content": user}]


def level4_principles_prompt(
    level3_outputs: list[dict[str, Any]],
    level2_outputs: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    compact_level2 = {
        episode_id: {
            "episode_id": data.get("episode_id"),
            "reasoning_records": [
                {
                    "reasoning_record_id": record.get("reasoning_record_id"),
                    "title": record.get("title"),
                    "claims": record.get("claims", []),
                    "reasoning_steps": record.get("reasoning_steps", []),
                    "conditions": record.get("conditions", []),
                    "risks": record.get("risks", []),
                    "exceptions": record.get("exceptions", []),
                    "uncertainty": record.get("uncertainty", []),
                    "decisions_or_preferences": record.get("decisions_or_preferences", []),
                    "actions_or_avoided_actions": record.get("actions_or_avoided_actions", []),
                    "view_change_conditions": record.get("view_change_conditions", []),
                    "speaker_behavior": record.get("speaker_behavior", []),
                    "tags": record.get("tags", []),
                }
                for record in data.get("reasoning_records", [])
            ],
        }
        for episode_id, data in level2_outputs.items()
    }
    user = f"""Level 3 episode-level provisional principles:
{json.dumps(level3_outputs, ensure_ascii=False)}

Level 2 reasoning record summaries:
{json.dumps(compact_level2, ensure_ascii=False)}

Create Level 4: Cross-Episode Decision Principles.

Identify recurring decision preferences across episodes. Do not merge principles solely because they share keywords.
Ask explicitly: does this represent the same decision preference, or only a superficially similar topic?

Requirements:
- Source records remain the actual evidence.
- Level 3 and Level 4 principles must not be counted as independent evidence.
- derived_from may reference Level 3 principle IDs and/or Level 2 record IDs.
- supporting_records must be Level 2 reasoning record IDs.
- supporting_episode_count must be computed from unique supporting_episode_ids.
- Output a Level 4 principle only when it has support from at least two different episodes.
- If a pattern is strong within one episode only, do not output it as Level 4; it remains a Level 3 provisional principle.
- Preserve conditions, exceptions, conflicts, counterexamples, changes over time, and confidence.
- Confidence should consider independent episode coverage, time/context diversity, semantic consistency, condition consistency, directness of evidence, contradictions, and abstraction distance.
- Use high confidence very sparingly because this POC only has four episodes.
- If no cross-episode pattern is sufficiently supported, return an empty principles array.
- It is acceptable to produce only a few principles.

Return JSON with this shape:
{{
  "level": 4,
  "principles": [
    {{
      "principle_id": "L4-principle-001",
      "name": "...",
      "domains": [],
      "decision_context": "...",
      "core_preference": "...",
      "reasoning_steps": [],
      "preferred_actions": [],
      "conditions": [],
      "exceptions": [],
      "supporting_records": [],
      "supporting_episode_ids": [],
      "supporting_episode_count": 0,
      "counterexamples": [],
      "contradictions": [],
      "principle_evolution": [],
      "confidence": "low|medium|high",
      "derived_from": [],
      "evidence_summary": "..."
    }}
  ]
}}
"""
    return [{"role": "system", "content": PRINCIPLE_SYSTEM_PROMPT}, {"role": "user", "content": user}]
