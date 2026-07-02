from __future__ import annotations

from typing import Any


STRUCTURE_TYPES = {"single_theme", "multi_theme", "conversational", "qa_heavy", "mixed"}
LEVEL3_STRATEGIES = {"full_synthesis", "light_consolidation", "bypass"}


def _evidence_items(obj: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if "source_evidence" in obj and isinstance(obj["source_evidence"], list):
            items.extend([x for x in obj["source_evidence"] if isinstance(x, dict)])
        for v in obj.values():
            items.extend(_evidence_items(v))
    elif isinstance(obj, list):
        for v in obj:
            items.extend(_evidence_items(v))
    return items


def _validate_evidence_refs(
    data: dict[str, Any],
    valid_merged_ids: set[int],
    valid_source_ids: set[int],
    episode_duration_seconds: float | None = None,
) -> list[str]:
    warnings: list[str] = []
    for idx, ev in enumerate(_evidence_items(data)):
        for key in ("start", "end"):
            if key in ev and not isinstance(ev[key], (int, float)):
                warnings.append(f"evidence[{idx}].{key} should be numeric")
            elif key in ev and episode_duration_seconds is not None:
                if ev[key] < 0 or ev[key] > episode_duration_seconds + 1:
                    warnings.append(f"evidence[{idx}].{key} is outside episode duration: {ev[key]}")
        merged_ids = ev.get("merged_ids", [])
        source_ids = ev.get("source_segment_ids", [])
        if not isinstance(merged_ids, list):
            warnings.append(f"evidence[{idx}].merged_ids should be a list")
        else:
            bad = [x for x in merged_ids if x not in valid_merged_ids]
            if bad:
                warnings.append(f"evidence[{idx}].merged_ids has invalid IDs: {bad[:10]}")
        if not isinstance(source_ids, list):
            warnings.append(f"evidence[{idx}].source_segment_ids should be a list")
        else:
            bad = [x for x in source_ids if x not in valid_source_ids]
            if bad:
                warnings.append(f"evidence[{idx}].source_segment_ids has invalid IDs: {bad[:10]}")
    return warnings


def validate_level1(
    data: dict[str, Any],
    episode_id: str,
    valid_merged_ids: set[int],
    valid_source_ids: set[int],
    episode_duration_seconds: float | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if data.get("episode_id") != episode_id:
        errors.append("episode_id mismatch")
    if data.get("episode_structure_type") not in STRUCTURE_TYPES:
        errors.append("episode_structure_type is invalid")
    if not isinstance(data.get("episode_structure_confidence"), (int, float)):
        errors.append("episode_structure_confidence must be numeric")
    if data.get("recommended_level3_strategy") not in LEVEL3_STRATEGIES:
        errors.append("recommended_level3_strategy is invalid")
    if not data.get("level3_strategy_reason"):
        errors.append("level3_strategy_reason is required")
    if not isinstance(data.get("main_topic_threads"), list):
        errors.append("main_topic_threads must be a list")
    warnings.extend(_validate_evidence_refs(data, valid_merged_ids, valid_source_ids, episode_duration_seconds))
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def validate_level2(
    data: dict[str, Any],
    episode_id: str,
    valid_merged_ids: set[int],
    valid_source_ids: set[int],
    level1_thread_ids: set[str],
    episode_duration_seconds: float | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if data.get("episode_id") != episode_id:
        errors.append("episode_id mismatch")
    records = data.get("reasoning_records")
    if not isinstance(records, list):
        errors.append("reasoning_records must be a list")
        records = []
    seen: set[str] = set()
    for i, record in enumerate(records):
        rid = record.get("reasoning_record_id")
        if not rid:
            errors.append(f"reasoning_records[{i}] missing reasoning_record_id")
        elif rid in seen:
            errors.append(f"duplicate reasoning_record_id: {rid}")
        else:
            seen.add(rid)
        evidence = record.get("source_evidence")
        if not isinstance(evidence, list) or not evidence:
            warnings.append(f"reasoning_records[{i}] missing source_evidence")
        thread_id = record.get("topic_thread_id")
        if thread_id and thread_id not in level1_thread_ids:
            warnings.append(f"reasoning_records[{i}] references unknown topic_thread_id: {thread_id}")
    warnings.extend(_validate_evidence_refs(data, valid_merged_ids, valid_source_ids, episode_duration_seconds))
    return {"ok": not errors, "errors": errors, "warnings": warnings, "record_count": len(records)}


def validate_level3(data: dict[str, Any], episode_id: str, level1_strategy: str, level2_ids: set[str]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if data.get("episode_id") != episode_id:
        errors.append("episode_id mismatch")
    strategy = data.get("selected_strategy")
    if strategy not in LEVEL3_STRATEGIES:
        errors.append("selected_strategy is invalid")
    if strategy != level1_strategy:
        warnings.append("selected_strategy differs from Level 1 recommendation")
    if not data.get("strategy_reason"):
        errors.append("strategy_reason is required")
    ids = data.get("source_reasoning_record_ids")
    if not isinstance(ids, list):
        errors.append("source_reasoning_record_ids must be a list")
    else:
        bad = [x for x in ids if x not in level2_ids]
        if bad:
            warnings.append(f"source_reasoning_record_ids has invalid IDs: {bad[:10]}")
    for key in LEVEL3_STRATEGIES:
        value = data.get(key)
        if key == strategy and value is None:
            warnings.append(f"{key} object is missing for selected strategy")
        if key != strategy and value is not None:
            warnings.append(f"{key} should be null when selected strategy is {strategy}")
    if strategy == "bypass":
        bypass = data.get("bypass") or {}
        if bypass.get("canonical_output") != "level2_reasoning_records":
            warnings.append("bypass.canonical_output should be level2_reasoning_records")
    return {"ok": not errors, "errors": errors, "warnings": warnings}
