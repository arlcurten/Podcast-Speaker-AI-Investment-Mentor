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
