from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from modules.common import (
    ROOT,
    OpenAICompatibleClient,
    display_path,
    estimate_cost,
    parse_json_object,
    private_pipeline_path,
    private_report_path,
    read_json,
    read_yaml,
    resolve_env_value,
    write_json,
)
from modules.prompts import level3_principles_prompt, level4_principles_prompt
from modules.schemas import LEVEL3_PRINCIPLES_RESPONSE_FORMAT, LEVEL4_PRINCIPLES_RESPONSE_FORMAT


DEFAULT_EPISODES = ["EP674", "EP670", "EP450", "EP57"]
LEVEL3_STRATEGIES = {"principle_extraction", "light_principle_grouping", "bypass"}
CONFIDENCE_VALUES = {"low", "medium", "high"}
CONFIDENCE_KEYS = {
    "evidence_strength",
    "evidence_independence",
    "speaker_specificity",
    "condition_clarity",
    "contradiction_severity",
    "cross_episode_consistency",
    "time_period_diversity",
    "overall",
}
BROAD_WORDS = ("always", "never", "任何情況", "必然", "一定", "所有市場", "固定")
GENERIC_PHRASES = (
    "注意風險",
    "投資要看數據",
    "估值會改變",
    "不要追高",
    "保持理性",
    "看數據",
    "control risk",
    "be rational",
    "avoid chasing",
)


def load_provider(path: str, model_id: str | None) -> dict[str, Any]:
    provider = read_yaml(ROOT / path)
    provider["model_id"] = model_id or resolve_env_value(provider.get("model_id")) or os.environ.get("PHASE2_MODEL_ID")
    return provider


def output_dir(episode_id: str) -> Path:
    return private_pipeline_path(Path("data") / "outputs" / episode_id)


def cross_episode_dir() -> Path:
    return private_pipeline_path(Path("data") / "outputs" / "cross_episode")


def available_episodes() -> list[str]:
    root = private_pipeline_path(Path("data") / "outputs")
    if not root.exists():
        return []
    found = [
        path.name
        for path in root.iterdir()
        if path.is_dir() and (path / "level2_reasoning_records.json").exists()
    ]
    return [ep for ep in DEFAULT_EPISODES if ep in found] + sorted(ep for ep in found if ep not in DEFAULT_EPISODES)


def load_level1(episode_id: str) -> dict[str, Any]:
    return read_json(output_dir(episode_id) / "level1_episode_semantic_map.json")


def load_level2(episode_id: str) -> dict[str, Any]:
    return read_json(output_dir(episode_id) / "level2_reasoning_records.json")


def load_level3_principles(episode_id: str) -> dict[str, Any]:
    return read_json(output_dir(episode_id) / "level3_principles.json")


def run_chat_json(
    client: OpenAICompatibleClient,
    messages: list[dict[str, str]],
    response_format: dict[str, Any],
    output_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = client.chat_json(messages, response_format=response_format)
    try:
        data = parse_json_object(result.content)
    except ValueError as exc:
        write_json(
            output_path.with_name(f"{output_path.stem}_failure.json"),
            {
                "error": str(exc),
                "usage": result.usage,
                "latency_seconds": result.latency_seconds,
                "retries": result.retries,
                "raw_content": result.content,
            },
        )
        raise
    write_json(output_path, data)
    return data, {
        "usage": result.usage,
        "latency_seconds": result.latency_seconds,
        "retries": result.retries,
    }


def extract_level3(args: argparse.Namespace) -> int:
    episodes = available_episodes() if args.all_configured else [args.episode]
    provider = load_provider(args.provider, args.model_id)
    client = OpenAICompatibleClient(provider)
    summaries = []
    for episode_id in episodes:
        level1 = load_level1(episode_id)
        level2 = load_level2(episode_id)
        path = output_dir(episode_id) / "level3_principles.json"
        data, stage = run_chat_json(
            client,
            level3_principles_prompt(episode_id, level1, level2),
            LEVEL3_PRINCIPLES_RESPONSE_FORMAT,
            path,
        )
        data = enrich_level3_principles(data, level1, level2)
        write_json(path, data)
        validation = validate_level3_principles(data, level2)
        write_json(output_dir(episode_id) / "level3_principles_validation.json", validation)
        metadata = {
            "episode_id": episode_id,
            "stage": "level3_principles",
            "provider": provider.get("provider"),
            "base_url": provider.get("base_url"),
            "model": provider.get("model_id"),
            "temperature": provider.get("temperature"),
            "max_output_tokens": provider.get("max_output_tokens"),
            "usage": stage["usage"],
            "latency_seconds": stage["latency_seconds"],
            "retries": stage["retries"],
            **estimate_cost(provider, stage["usage"]),
            "validation": validation,
        }
        write_json(output_dir(episode_id) / "level3_principles_metadata.json", metadata)
        write_level3_report(private_report_path(f"{episode_id}_level3_principles_review.md"), data, level2, validation, metadata)
        summaries.append(level3_summary(data, validation, metadata))
    print(json.dumps({"episodes": summaries}, ensure_ascii=False, indent=2))
    return 0


def extract_level4(args: argparse.Namespace) -> int:
    episodes = available_episodes()
    level3_outputs = [load_level3_principles(ep) for ep in episodes]
    level2_outputs = {ep: load_level2(ep) for ep in episodes}
    provider = load_provider(args.provider, args.model_id)
    client = OpenAICompatibleClient(provider)
    path = cross_episode_dir() / "level4_principles.json"
    data, stage = run_chat_json(
        client,
        level4_principles_prompt(level3_outputs, level2_outputs),
        LEVEL4_PRINCIPLES_RESPONSE_FORMAT,
        path,
    )
    data = enrich_level4_principles(data, level3_outputs, level2_outputs)
    write_json(path, data)
    validation = validate_level4_principles(data, level3_outputs, level2_outputs)
    write_json(cross_episode_dir() / "level4_principles_validation.json", validation)
    review = review_level4_principles(data, level2_outputs)
    write_json(cross_episode_dir() / "level4_principles_codex_review.json", review)
    metadata = {
        "stage": "level4_principles",
        "episodes": episodes,
        "provider": provider.get("provider"),
        "base_url": provider.get("base_url"),
        "model": provider.get("model_id"),
        "temperature": provider.get("temperature"),
        "max_output_tokens": provider.get("max_output_tokens"),
        "usage": stage["usage"],
        "latency_seconds": stage["latency_seconds"],
        "retries": stage["retries"],
        **estimate_cost(provider, stage["usage"]),
        "validation": validation,
        "codex_review": review,
    }
    write_json(cross_episode_dir() / "level4_principles_metadata.json", metadata)
    write_level4_report(private_report_path("cross_episode_level4_principles_review.md"), data, level3_outputs, level2_outputs, validation, review, metadata)
    print(json.dumps(level4_summary(data, validation, metadata), ensure_ascii=False, indent=2))
    return 0


def validate_principles(_: argparse.Namespace) -> int:
    episodes = available_episodes()
    level3_results = {}
    for ep in episodes:
        if (output_dir(ep) / "level3_principles.json").exists():
            v = validate_level3_principles(load_level3_principles(ep), load_level2(ep))
            write_json(output_dir(ep) / "level3_principles_validation.json", v)
            level3_results[ep] = v
    level4_result = None
    if (cross_episode_dir() / "level4_principles.json").exists():
        level3_outputs = [load_level3_principles(ep) for ep in episodes if (output_dir(ep) / "level3_principles.json").exists()]
        level2_outputs = {ep: load_level2(ep) for ep in episodes}
        level4 = read_json(cross_episode_dir() / "level4_principles.json")
        level4_result = validate_level4_principles(level4, level3_outputs, level2_outputs)
        write_json(cross_episode_dir() / "level4_principles_validation.json", level4_result)
    print(json.dumps({"level3": level3_results, "level4": level4_result}, ensure_ascii=False, indent=2))
    return 0


def report_principles(_: argparse.Namespace) -> int:
    episodes = [ep for ep in available_episodes() if (output_dir(ep) / "level3_principles.json").exists()]
    level3_outputs = [load_level3_principles(ep) for ep in episodes]
    level2_outputs = {ep: load_level2(ep) for ep in episodes}
    level4_path = cross_episode_dir() / "level4_principles.json"
    if not level4_path.exists():
        raise SystemExit("Missing Level 4 output. Run extract-level4 first.")
    level4 = read_json(level4_path)
    validation = validate_level4_principles(level4, level3_outputs, level2_outputs)
    review = review_level4_principles(level4, level2_outputs)
    write_json(cross_episode_dir() / "level4_principles_codex_review.json", review)
    metadata = read_json(cross_episode_dir() / "level4_principles_metadata.json") if (cross_episode_dir() / "level4_principles_metadata.json").exists() else {}
    report_path = private_report_path("cross_episode_level4_principles_review.md")
    write_level4_report(report_path, level4, level3_outputs, level2_outputs, validation, review, metadata)
    write_confidence_audit_report(private_report_path("principle_confidence_audit.md"), level3_outputs, level4, validation, review)
    write_principle_quality_report(private_report_path("principle_quality_summary.md"), level3_outputs, level4, validation, review)
    print(json.dumps({"report": display_path(report_path)}, ensure_ascii=False, indent=2))
    return 0


def enrich_level3_principles(data: dict[str, Any], level1: dict[str, Any], level2: dict[str, Any]) -> dict[str, Any]:
    data["schema_version"] = "principles.v2"
    data["level"] = 3
    episode_id = level2.get("episode_id")
    data["episode_id"] = episode_id
    data.setdefault("selected_strategy", "bypass")
    data.setdefault("strategy_reason", "")
    record_map = {r.get("reasoning_record_id"): r for r in level2.get("reasoning_records", [])}
    for idx, principle in enumerate(data.get("principles") or [], 1):
        principle.setdefault("principle_id", f"{episode_id}-principle-{idx:03d}")
        principle["episode_id"] = episode_id
        refs = [rid for rid in principle.get("supporting_records", []) if rid in record_map]
        principle["supporting_records"] = refs
        principle["provisional"] = True
        principle["generalization_scope"] = "episode_local"
        principle["extraction_strategy"] = data.get("selected_strategy")
        principle["provenance_stats"] = level3_provenance_stats(principle, record_map)
        principle["genericness_status"], principle["genericness_reasons"] = assess_genericness(principle)
        principle["speaker_specificity"] = principle.get("genericness_status", "partially_specific")
        principle["principle_type"] = infer_level3_principle_type(principle)
        principle["decision_tradeoff"] = normalize_tradeoff(principle.get("decision_tradeoff"))
        principle["distinctive_behavior"] = normalize_string_list(principle.get("distinctive_behavior"))
        principle["confidence"] = constrained_level3_confidence(principle)
        apply_level3_eligibility(principle)
    if data.get("selected_strategy") == "bypass":
        data["principles"] = []
    return data


def enrich_level4_principles(
    data: dict[str, Any],
    level3_outputs: list[dict[str, Any]],
    level2_outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    data["schema_version"] = "principles.v2"
    data["level"] = 4
    record_map = {
        record.get("reasoning_record_id"): (episode_id, record)
        for episode_id, level2 in level2_outputs.items()
        for record in level2.get("reasoning_records", [])
    }
    level3_ids = {
        p.get("principle_id")
        for level3 in level3_outputs
        for p in level3.get("principles", [])
    }
    enriched = []
    rejected = []
    for idx, principle in enumerate(data.get("principles") or [], 1):
        principle.setdefault("principle_id", f"L4-principle-{idx:03d}")
        principle["generalization_scope"] = "cross_episode_candidate"
        refs = [rid for rid in principle.get("supporting_records", []) if rid in record_map]
        principle["supporting_records"] = refs
        episode_ids = sorted({record_map[rid][0] for rid in refs})
        principle["supporting_episode_ids"] = episode_ids
        principle["supporting_episode_count"] = len(episode_ids)
        principle["derived_from"] = [
            source_id
            for source_id in principle.get("derived_from", [])
            if source_id in level3_ids or source_id in record_map
        ]
        principle["provenance_stats"] = level4_provenance_stats(principle, record_map)
        principle["genericness_status"], principle["genericness_reasons"] = assess_genericness(principle)
        principle["speaker_specificity"] = principle.get("genericness_status", "partially_specific")
        principle["principle_type"] = infer_level4_principle_type(principle)
        principle["decision_tradeoff"] = normalize_tradeoff(principle.get("decision_tradeoff"))
        principle["distinctive_behavior"] = normalize_string_list(principle.get("distinctive_behavior"))
        principle["confidence"] = constrained_level4_confidence(principle)
        apply_level4_eligibility(principle)
        if principle["provenance_stats"].get("supporting_episode_count", 0) < 2:
            rejected.append(
                {
                    "principle": principle,
                    "rejection_reason": "Level 4 requires at least two supporting episodes; retain as Level 3/local hypothesis only.",
                }
            )
        else:
            enriched.append(principle)
    data["principles"] = enriched
    data["rejected_principle_candidates"] = rejected
    return data


def level3_provenance_stats(principle: dict[str, Any], record_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    refs = [rid for rid in principle.get("supporting_records", []) if rid in record_map]
    records = [record_map[rid] for rid in refs]
    thread_ids = sorted({r.get("topic_thread_id") for r in records if r.get("topic_thread_id")})
    contexts = sorted({str(r.get("title", "")).strip() for r in records if r.get("title")})
    regions = evidence_regions(records)
    return {
        "supporting_record_count": len(refs),
        "supporting_episode_count": 1 if refs else 0,
        "unique_level1_thread_count": len(thread_ids),
        "unique_level1_threads": thread_ids,
        "distinct_decision_context_count": len(contexts),
        "source_region_count": len(regions),
        "independent_source_context_count": min(len(thread_ids) or len(contexts), len(contexts)),
        "overlapping_or_duplicate_source_regions": overlapping_regions(regions),
    }


def level4_provenance_stats(principle: dict[str, Any], record_map: dict[str, tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    refs = [rid for rid in principle.get("supporting_records", []) if rid in record_map]
    episode_ids = sorted({record_map[rid][0] for rid in refs})
    thread_keys = sorted({f"{record_map[rid][0]}:{record_map[rid][1].get('topic_thread_id')}" for rid in refs if record_map[rid][1].get("topic_thread_id")})
    contexts = sorted({str(record_map[rid][1].get("title", "")).strip() for rid in refs if record_map[rid][1].get("title")})
    regions = evidence_regions([record_map[rid][1] for rid in refs])
    return {
        "supporting_record_count": len(refs),
        "supporting_episode_ids": episode_ids,
        "supporting_episode_count": len(episode_ids),
        "unique_level1_thread_count": len(thread_keys),
        "unique_level1_threads": thread_keys,
        "distinct_decision_context_count": len(contexts),
        "source_region_count": len(regions),
        "independent_source_context_count": min(len(thread_keys) or len(contexts), len(contexts)),
        "overlapping_or_duplicate_source_regions": overlapping_regions(regions),
    }


def evidence_regions(records: list[dict[str, Any]]) -> list[tuple[float, float]]:
    regions: list[tuple[float, float]] = []
    for record in records:
        for evidence in record.get("source_evidence", []):
            start = evidence.get("start")
            end = evidence.get("end")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)):
                regions.append((float(start), float(end)))
    return sorted(regions)


def overlapping_regions(regions: list[tuple[float, float]]) -> int:
    count = 0
    for idx, (start, end) in enumerate(regions):
        for other_start, other_end in regions[idx + 1:]:
            if other_start < end and other_end > start:
                count += 1
    return count


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value:
        return [str(value)]
    return []


def normalize_tradeoff(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    return {
        "option_a": str(value.get("option_a") or ""),
        "option_b": str(value.get("option_b") or ""),
        "preferred_option": str(value.get("preferred_option") or ""),
        "reason_for_preference": str(value.get("reason_for_preference") or ""),
        "cost_of_preference": str(value.get("cost_of_preference") or ""),
        "switch_conditions": normalize_string_list(value.get("switch_conditions")),
    }


def assess_genericness(principle: dict[str, Any]) -> tuple[str, list[str]]:
    text = " ".join(
        str(principle.get(key, ""))
        for key in ("name", "decision_context", "core_preference", "evidence_summary")
    ).lower()
    reasons: list[str] = []
    if any(phrase.lower() in text for phrase in GENERIC_PHRASES):
        reasons.append("Contains generic investment-advice phrasing.")
    tradeoff = normalize_tradeoff(principle.get("decision_tradeoff"))
    if not (tradeoff["option_a"] and tradeoff["option_b"] and tradeoff["cost_of_preference"]):
        reasons.append("Decision trade-off is incomplete.")
    if not principle.get("conditions"):
        reasons.append("Conditions are missing.")
    if not principle.get("distinctive_behavior"):
        reasons.append("Distinctive speaker behavior is missing.")
    if len(reasons) >= 3:
        return "too_generic", reasons
    if reasons:
        return "partially_specific", reasons
    return "specific", []


def infer_level3_principle_type(principle: dict[str, Any]) -> str:
    stats = principle.get("provenance_stats", {})
    if stats.get("supporting_record_count", 0) <= 1:
        return "case_derived_hypothesis"
    if principle.get("counterexamples") or principle.get("exceptions"):
        return "conditional_pattern"
    return principle.get("principle_type") if principle.get("principle_type") in {"recurring_principle", "conditional_pattern", "exception_pattern"} else "recurring_principle"


def infer_level4_principle_type(principle: dict[str, Any]) -> str:
    if principle.get("counterexamples") or principle.get("exceptions"):
        return "conditional_pattern"
    return principle.get("principle_type") if principle.get("principle_type") in {"recurring_principle", "conditional_pattern", "exception_pattern"} else "recurring_principle"


def constrained_level3_confidence(principle: dict[str, Any]) -> dict[str, str]:
    stats = principle.get("provenance_stats", {})
    support = int(stats.get("supporting_record_count") or 0)
    threads = int(stats.get("unique_level1_thread_count") or 0)
    contexts = int(stats.get("distinct_decision_context_count") or 0)
    has_conditions = bool(principle.get("conditions"))
    contradiction_severity = "minor" if principle.get("contradictions") else "none"
    evidence_strength = "high" if support >= 3 and contexts >= 2 and has_conditions else "medium" if support >= 2 and has_conditions else "low"
    evidence_independence = "high" if threads >= 3 else "medium" if threads >= 2 or contexts >= 2 else "low"
    condition_clarity = "high" if len(principle.get("conditions") or []) >= 2 and normalize_tradeoff(principle.get("decision_tradeoff"))["switch_conditions"] else "medium" if has_conditions else "low"
    speaker_specificity = "high" if principle.get("genericness_status") == "specific" else "medium" if principle.get("genericness_status") == "partially_specific" else "low"
    overall = "medium" if evidence_strength in {"medium", "high"} and evidence_independence != "low" and speaker_specificity != "low" and condition_clarity != "low" and contradiction_severity != "major" else "low"
    return {
        "evidence_strength": evidence_strength,
        "evidence_independence": evidence_independence,
        "speaker_specificity": speaker_specificity,
        "condition_clarity": condition_clarity,
        "contradiction_severity": contradiction_severity,
        "cross_episode_consistency": "not_applicable",
        "time_period_diversity": "not_applicable",
        "overall": overall,
    }


def constrained_level4_confidence(principle: dict[str, Any]) -> dict[str, str]:
    stats = principle.get("provenance_stats", {})
    support = int(stats.get("supporting_record_count") or 0)
    episodes = int(stats.get("supporting_episode_count") or 0)
    contexts = int(stats.get("distinct_decision_context_count") or 0)
    has_conditions = bool(principle.get("conditions"))
    summary_lower = str(principle.get("evidence_summary", "")).lower()
    contradiction_severity = "minor" if principle.get("contradictions") or any(term in summary_lower for term in ("contradict", "contradiction", "contradictory", "tension")) else "none"
    evidence_strength = "high" if support >= 4 and episodes >= 3 and contexts >= 3 else "medium" if support >= 2 and episodes >= 2 else "low"
    evidence_independence = "high" if episodes >= 3 and contexts >= 3 else "medium" if episodes >= 2 and contexts >= 2 else "low"
    condition_clarity = "high" if len(principle.get("conditions") or []) >= 2 and normalize_tradeoff(principle.get("decision_tradeoff"))["switch_conditions"] else "medium" if has_conditions else "low"
    speaker_specificity = "high" if principle.get("genericness_status") == "specific" else "medium" if principle.get("genericness_status") == "partially_specific" else "low"
    cross_episode = "high" if episodes >= 3 else "medium" if episodes >= 2 else "low"
    time_period = "high" if episodes >= 3 else "medium" if episodes >= 2 else "low"
    overall = "high" if evidence_strength == "high" and evidence_independence == "high" and speaker_specificity == "high" and condition_clarity != "low" and contradiction_severity == "none" else "medium" if evidence_strength != "low" and evidence_independence != "low" and speaker_specificity != "low" and condition_clarity != "low" else "low"
    return {
        "evidence_strength": evidence_strength,
        "evidence_independence": evidence_independence,
        "speaker_specificity": speaker_specificity,
        "condition_clarity": condition_clarity,
        "contradiction_severity": contradiction_severity,
        "cross_episode_consistency": cross_episode,
        "time_period_diversity": time_period,
        "overall": overall,
    }


def apply_level3_eligibility(principle: dict[str, Any]) -> None:
    stats = principle.get("provenance_stats", {})
    reasons: list[str] = []
    eligible_level4 = True
    if principle.get("principle_type") == "case_derived_hypothesis":
        eligible_level4 = False
        reasons.append("Single-record Level 3 output is a hypothesis, not a recurring principle.")
    if principle.get("genericness_status") == "too_generic":
        eligible_level4 = False
        reasons.append("Principle is too generic for Level 4 promotion.")
    if int(stats.get("unique_level1_thread_count") or 0) < 2:
        eligible_level4 = False
        reasons.append("Insufficient independent topic-thread evidence within the episode.")
    if principle["confidence"]["overall"] == "low":
        eligible_level4 = False
        reasons.append("Overall Level 3 confidence is low.")
    principle["eligible_for_level4"] = eligible_level4
    principle["eligible_for_online_reasoning"] = "principle_assisted_only" if eligible_level4 else "limited" if principle.get("principle_type") != "case_derived_hypothesis" else "false"
    principle["runtime_mode_recommendation"] = "principle_assisted" if eligible_level4 else "case_only_fallback" if principle["eligible_for_online_reasoning"] == "limited" else "not_eligible"
    principle["eligibility_reasons"] = reasons or ["Episode-local principle may be used only as assisted context, not a confirmed policy."]


def apply_level4_eligibility(principle: dict[str, Any]) -> None:
    stats = principle.get("provenance_stats", {})
    reasons: list[str] = []
    eligible = True
    if int(stats.get("supporting_episode_count") or 0) < 2:
        eligible = False
        reasons.append("Level 4 requires at least two supporting episodes.")
    if principle.get("genericness_status") == "too_generic":
        eligible = False
        reasons.append("Principle is too generic for online reasoning.")
    if principle["confidence"]["overall"] == "low":
        eligible = False
        reasons.append("Overall confidence is low.")
    if principle["confidence"]["condition_clarity"] == "low":
        eligible = False
        reasons.append("Conditions or switch conditions are insufficient.")
    principle["eligible_for_level4"] = eligible
    if not eligible:
        principle["eligible_for_online_reasoning"] = "false"
        principle["runtime_mode_recommendation"] = "not_eligible"
    elif principle["confidence"]["overall"] == "high" and principle.get("genericness_status") == "specific":
        principle["eligible_for_online_reasoning"] = "true"
        principle["runtime_mode_recommendation"] = "principle_guided"
    else:
        principle["eligible_for_online_reasoning"] = "principle_assisted_only"
        principle["runtime_mode_recommendation"] = "principle_assisted"
    principle["eligibility_reasons"] = reasons or ["Use as an assisted reasoning framework with direct Level 2 case support."]


def valid_confidence(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if not CONFIDENCE_KEYS.issubset(value):
        return False
    for key in (
        "evidence_strength",
        "evidence_independence",
        "speaker_specificity",
        "condition_clarity",
        "overall",
    ):
        if value.get(key) not in CONFIDENCE_VALUES:
            return False
    if value.get("contradiction_severity") not in {"none", "minor", "major"}:
        return False
    if value.get("cross_episode_consistency") not in {"not_applicable", "low", "medium", "high"}:
        return False
    if value.get("time_period_diversity") not in {"not_applicable", "low", "medium", "high"}:
        return False
    return True


def confidence_overall(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("overall")
    if isinstance(value, str):
        return f"legacy_flat:{value}"
    return None


def validate_level3_principles(data: dict[str, Any], level2: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    episode_id = level2.get("episode_id")
    record_ids = {r.get("reasoning_record_id") for r in level2.get("reasoning_records", [])}
    if data.get("schema_version") != "principles.v2":
        errors.append("schema_version must be principles.v2")
    if data.get("episode_id") != episode_id:
        errors.append("episode_id mismatch")
    if data.get("selected_strategy") not in LEVEL3_STRATEGIES:
        errors.append("selected_strategy is invalid")
    principles = data.get("principles")
    if not isinstance(principles, list):
        errors.append("principles must be a list")
        principles = []
    if data.get("selected_strategy") == "bypass" and principles:
        warnings.append("bypass selected but principles are present")
    seen: set[str] = set()
    names: list[str] = []
    for idx, principle in enumerate(principles):
        pid = principle.get("principle_id")
        if not pid:
            errors.append(f"principles[{idx}] missing principle_id")
        elif pid in seen:
            errors.append(f"duplicate principle_id: {pid}")
        else:
            seen.add(pid)
        if principle.get("episode_id") != episode_id:
            errors.append(f"{pid} episode_id mismatch")
        if principle.get("provisional") is not True:
            errors.append(f"{pid} provisional must be true")
        if principle.get("extraction_strategy") != data.get("selected_strategy"):
            warnings.append(f"{pid} extraction_strategy differs from selected_strategy")
        confidence = principle.get("confidence")
        if not valid_confidence(confidence):
            errors.append(f"{pid} confidence must be a principles.v2 confidence object")
        refs = principle.get("supporting_records")
        if not isinstance(refs, list) or not refs:
            warnings.append(f"{pid} missing supporting_records")
            refs = []
        bad = [x for x in refs if x not in record_ids]
        if bad:
            errors.append(f"{pid} has invalid supporting_records: {bad[:10]}")
        for key in ("counterexamples", "contradictions"):
            vals = principle.get(key) or []
            bad_vals = [x for x in vals if x not in record_ids]
            if bad_vals:
                warnings.append(f"{pid} has {key} not matching Level 2 IDs: {bad_vals[:10]}")
        if principle.get("generalization_scope") != "episode_local":
            errors.append(f"{pid} generalization_scope must be episode_local")
        if isinstance(confidence, dict) and confidence.get("overall") == "high":
            errors.append(f"{pid} Level 3 overall confidence cannot be high")
        if len(refs) == 1 and principle.get("principle_type") != "case_derived_hypothesis":
            errors.append(f"{pid} single-record Level 3 output must be case_derived_hypothesis")
        if len(refs) == 1 and principle.get("eligible_for_level4"):
            errors.append(f"{pid} single-record hypothesis cannot be eligible_for_level4")
        if principle.get("genericness_status") == "too_generic" and principle.get("runtime_mode_recommendation") != "not_eligible":
            errors.append(f"{pid} too_generic principle must be not_eligible")
        if not principle.get("conditions"):
            warnings.append(f"{pid} missing conditions")
        if not isinstance(principle.get("decision_tradeoff"), dict):
            errors.append(f"{pid} missing decision_tradeoff")
        if not isinstance(principle.get("provenance_stats"), dict):
            errors.append(f"{pid} missing provenance_stats")
        text_blob = " ".join(str(principle.get(k, "")) for k in ("name", "core_preference", "evidence_summary"))
        if any(word in text_blob for word in BROAD_WORDS):
            warnings.append(f"{pid} may use suspiciously broad wording")
        names.append(str(principle.get("name", "")).strip().lower())
    duplicates = [name for name, count in Counter(names).items() if name and count > 1]
    if duplicates:
        warnings.append(f"duplicate principle names: {duplicates[:10]}")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "principle_count": len(principles),
        "strategy": data.get("selected_strategy"),
        "confidence_distribution": dict(Counter(confidence_overall(p.get("confidence")) for p in principles)),
        "principle_type_distribution": dict(Counter(p.get("principle_type") for p in principles)),
        "runtime_mode_distribution": dict(Counter(p.get("runtime_mode_recommendation") for p in principles)),
    }


def validate_level4_principles(
    data: dict[str, Any],
    level3_outputs: list[dict[str, Any]],
    level2_outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    all_record_ids = {
        record.get("reasoning_record_id"): episode_id
        for episode_id, level2 in level2_outputs.items()
        for record in level2.get("reasoning_records", [])
    }
    all_level3_ids = {
        principle.get("principle_id")
        for level3 in level3_outputs
        for principle in level3.get("principles", [])
    }
    if data.get("schema_version") != "principles.v2":
        errors.append("schema_version must be principles.v2")
    principles = data.get("principles")
    if not isinstance(principles, list):
        errors.append("principles must be a list")
        principles = []
    seen: set[str] = set()
    for idx, principle in enumerate(principles):
        pid = principle.get("principle_id")
        if not pid:
            errors.append(f"principles[{idx}] missing principle_id")
        elif pid in seen:
            errors.append(f"duplicate principle_id: {pid}")
        else:
            seen.add(pid)
        supporting_records = principle.get("supporting_records") or []
        bad_records = [x for x in supporting_records if x not in all_record_ids]
        if bad_records:
            errors.append(f"{pid} has invalid supporting_records: {bad_records[:10]}")
        computed_eps = sorted({all_record_ids[x] for x in supporting_records if x in all_record_ids})
        declared_eps = sorted(principle.get("supporting_episode_ids") or [])
        if declared_eps != computed_eps:
            warnings.append(f"{pid} supporting_episode_ids differs from records: declared={declared_eps}, computed={computed_eps}")
        if principle.get("supporting_episode_count") != len(set(declared_eps)):
            warnings.append(f"{pid} supporting_episode_count mismatch")
        derived = principle.get("derived_from") or []
        bad_derived = [x for x in derived if x not in all_level3_ids and x not in all_record_ids]
        if bad_derived:
            warnings.append(f"{pid} derived_from has unknown IDs: {bad_derived[:10]}")
        confidence = principle.get("confidence")
        if not valid_confidence(confidence):
            errors.append(f"{pid} confidence must be a principles.v2 confidence object")
        if principle.get("generalization_scope") != "cross_episode_candidate":
            errors.append(f"{pid} generalization_scope must be cross_episode_candidate")
        if len(set(declared_eps)) < 2:
            errors.append(f"{pid} Level 4 requires at least two supporting episodes")
        if isinstance(confidence, dict) and confidence.get("overall") == "high" and len(set(declared_eps)) < 3:
            errors.append(f"{pid} high overall confidence requires at least three supporting episodes")
        if not principle.get("conditions"):
            warnings.append(f"{pid} missing conditions")
        if not isinstance(principle.get("decision_tradeoff"), dict):
            errors.append(f"{pid} missing decision_tradeoff")
        if not isinstance(principle.get("provenance_stats"), dict):
            errors.append(f"{pid} missing provenance_stats")
        if principle.get("genericness_status") == "too_generic" and principle.get("runtime_mode_recommendation") != "not_eligible":
            errors.append(f"{pid} too_generic principle must be not_eligible")
        if principle.get("eligible_for_online_reasoning") in {"true", "principle_assisted_only"} and not principle.get("supporting_records"):
            errors.append(f"{pid} online-eligible principle must have supporting records")
        if not principle.get("evidence_summary"):
            warnings.append(f"{pid} missing evidence_summary")
        text_blob = " ".join(str(principle.get(k, "")) for k in ("name", "core_preference", "evidence_summary"))
        if any(word in text_blob for word in BROAD_WORDS):
            warnings.append(f"{pid} may use suspiciously broad wording")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "principle_count": len(principles),
        "confidence_distribution": dict(Counter(confidence_overall(p.get("confidence")) for p in principles)),
        "runtime_mode_distribution": dict(Counter(p.get("runtime_mode_recommendation") for p in principles)),
        "genericness_distribution": dict(Counter(p.get("genericness_status") for p in principles)),
    }


def review_level4_principles(data: dict[str, Any], level2_outputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    record_map = {
        record.get("reasoning_record_id"): record
        for level2 in level2_outputs.values()
        for record in level2.get("reasoning_records", [])
    }
    reviews = []
    for principle in data.get("principles", []):
        pid = principle.get("principle_id")
        supporting_records = principle.get("supporting_records") or []
        episodes = set(principle.get("supporting_episode_ids") or [])
        reasons: list[str] = []
        label = "useful"
        if len(episodes) <= 1:
            label = "needs_revision"
            reasons.append("Only one supporting episode; keep as low-confidence or Level 3 only.")
        if len(supporting_records) < 2:
            label = "unsupported"
            reasons.append("Too few supporting records for cross-episode principle.")
        if not principle.get("conditions"):
            label = "needs_revision" if label == "useful" else label
            reasons.append("Conditions are missing or too thin.")
        if not principle.get("exceptions") and not principle.get("counterexamples"):
            reasons.append("No explicit exceptions or counterexamples; check for overgeneralization.")
        summary = str(principle.get("evidence_summary", ""))
        if "indirectly support" in summary or "indirectly supports" in summary:
            label = "needs_revision" if label == "useful" else label
            reasons.append("At least one cross-episode support link is indirect; verify before treating as stable.")
        summary_lower = summary.lower()
        if any(term in summary_lower for term in ("contradict", "contradiction", "contradictory", "tension")) and not principle.get("contradictions"):
            label = "needs_revision" if label == "useful" else label
            reasons.append("Evidence summary mentions possible tension, but contradictions field is empty.")
        combined_titles = " ".join(str(record_map.get(rid, {}).get("title", "")) for rid in supporting_records)
        if combined_titles and len(set(combined_titles.split())) < 8:
            label = "too_generic" if label == "useful" else label
            reasons.append("Supporting records may be too semantically narrow.")
        reviews.append(
            {
                "principle_id": pid,
                "classification": label,
                "reasons": reasons or ["Looks useful for a future applicability check."],
            }
        )
    return {"items": reviews, "summary": dict(Counter(item["classification"] for item in reviews))}


def level3_summary(data: dict[str, Any], validation: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    principles = data.get("principles", [])
    return {
        "episode_id": data.get("episode_id"),
        "strategy": data.get("selected_strategy"),
        "principles": len(principles),
        "supporting_record_count": sum(len(p.get("supporting_records") or []) for p in principles),
        "counterexample_count": sum(len(p.get("counterexamples") or []) for p in principles),
        "contradiction_count": sum(len(p.get("contradictions") or []) for p in principles),
        "confidence_distribution": validation.get("confidence_distribution"),
        "principle_type_distribution": validation.get("principle_type_distribution"),
        "runtime_mode_distribution": validation.get("runtime_mode_distribution"),
        "warnings": len(validation.get("warnings", [])),
        "errors": len(validation.get("errors", [])),
        "latency_seconds": metadata.get("latency_seconds"),
        "tokens": (metadata.get("usage") or {}).get("total_tokens"),
        "cost": (metadata.get("usage") or {}).get("cost") or metadata.get("estimated_cost_usd"),
    }


def level4_summary(data: dict[str, Any], validation: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "principles": len(data.get("principles", [])),
        "principle_names": [p.get("name") for p in data.get("principles", [])],
        "confidence_distribution": validation.get("confidence_distribution"),
        "runtime_mode_distribution": validation.get("runtime_mode_distribution"),
        "genericness_distribution": validation.get("genericness_distribution"),
        "warnings": len(validation.get("warnings", [])),
        "errors": len(validation.get("errors", [])),
        "latency_seconds": metadata.get("latency_seconds"),
        "tokens": (metadata.get("usage") or {}).get("total_tokens"),
        "cost": (metadata.get("usage") or {}).get("cost") or metadata.get("estimated_cost_usd"),
    }


def write_level3_report(path: Path, data: dict[str, Any], level2: dict[str, Any], validation: dict[str, Any], metadata: dict[str, Any]) -> None:
    record_map = {r.get("reasoning_record_id"): r for r in level2.get("reasoning_records", [])}
    lines = [
        f"# {data.get('episode_id')} Level 3 Provisional Principles Review",
        "",
        f"- Strategy: `{data.get('selected_strategy')}`",
        f"- Strategy reason: {data.get('strategy_reason')}",
        f"- Principle count: `{len(data.get('principles', []))}`",
        f"- Validation ok: `{validation.get('ok')}`",
        f"- Cost USD: `{(metadata.get('usage') or {}).get('cost') or metadata.get('estimated_cost_usd')}`",
        "",
        "## Validation Warnings",
        "",
        *[f"- {w}" for w in validation.get("warnings", [])],
        "",
        "## Principles",
        "",
    ]
    for principle in data.get("principles", []):
        lines.extend(
            [
                f"### {principle.get('principle_id')} — {principle.get('name')}",
                "",
                f"- Confidence: `{principle.get('confidence')}`",
                f"- Principle type: `{principle.get('principle_type')}`",
                f"- Runtime mode: `{principle.get('runtime_mode_recommendation')}`",
                f"- Eligible for Level 4: `{principle.get('eligible_for_level4')}`",
                f"- Genericness: `{principle.get('genericness_status')}` — {'; '.join(principle.get('genericness_reasons') or [])}",
                f"- Provenance stats: `{principle.get('provenance_stats')}`",
                f"- Domains: {', '.join(principle.get('domains') or [])}",
                f"- Decision context: {principle.get('decision_context')}",
                f"- Core preference: {principle.get('core_preference')}",
                f"- Decision trade-off: `{principle.get('decision_tradeoff')}`",
                f"- Conditions: {'; '.join(principle.get('conditions') or [])}",
                f"- Exceptions: {'; '.join(principle.get('exceptions') or [])}",
                f"- Evidence summary: {principle.get('evidence_summary')}",
                "",
                "Supporting records:",
            ]
        )
        for rid in principle.get("supporting_records") or []:
            rec = record_map.get(rid, {})
            lines.append(f"- `{rid}`: {rec.get('title', 'unknown')}")
        if principle.get("counterexamples"):
            lines.append(f"- Counterexamples: {', '.join(principle.get('counterexamples'))}")
        if principle.get("contradictions"):
            lines.append(f"- Contradictions: {', '.join(principle.get('contradictions'))}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_level4_report(
    path: Path,
    data: dict[str, Any],
    level3_outputs: list[dict[str, Any]],
    level2_outputs: dict[str, dict[str, Any]],
    validation: dict[str, Any],
    review: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    record_map = {
        record.get("reasoning_record_id"): (episode_id, record)
        for episode_id, level2 in level2_outputs.items()
        for record in level2.get("reasoning_records", [])
    }
    level3_map = {
        principle.get("principle_id"): principle
        for level3 in level3_outputs
        for principle in level3.get("principles", [])
    }
    review_map = {item["principle_id"]: item for item in review.get("items", [])}
    lines = [
        "# Cross-Episode Level 4 Decision Principles Review",
        "",
        f"- Principle count: `{len(data.get('principles', []))}`",
        f"- Validation ok: `{validation.get('ok')}`",
        f"- Review summary: `{review.get('summary')}`",
        f"- Cost USD: `{(metadata.get('usage') or {}).get('cost') or metadata.get('estimated_cost_usd')}`",
        "",
        "## Validation Warnings",
        "",
        *[f"- {w}" for w in validation.get("warnings", [])],
        "",
        "## Candidate Principles",
        "",
    ]
    for principle in data.get("principles", []):
        pid = principle.get("principle_id")
        item_review = review_map.get(pid, {})
        lines.extend(
            [
                f"### {pid} — {principle.get('name')}",
                "",
                f"- Codex review: `{item_review.get('classification')}`",
                f"- Review reasons: {'; '.join(item_review.get('reasons', []))}",
                f"- Confidence: `{principle.get('confidence')}`",
                f"- Runtime mode: `{principle.get('runtime_mode_recommendation')}`",
                f"- Eligible online: `{principle.get('eligible_for_online_reasoning')}`",
                f"- Genericness: `{principle.get('genericness_status')}` — {'; '.join(principle.get('genericness_reasons') or [])}",
                f"- Provenance stats: `{principle.get('provenance_stats')}`",
                f"- Supporting episodes: {', '.join(principle.get('supporting_episode_ids') or [])}",
                f"- Decision context: {principle.get('decision_context')}",
                f"- Core preference: {principle.get('core_preference')}",
                f"- Decision trade-off: `{principle.get('decision_tradeoff')}`",
                f"- Conditions: {'; '.join(principle.get('conditions') or [])}",
                f"- Exceptions: {'; '.join(principle.get('exceptions') or [])}",
                f"- Evidence summary: {principle.get('evidence_summary')}",
                "",
                "Supporting records:",
            ]
        )
        for rid in principle.get("supporting_records") or []:
            ep, rec = record_map.get(rid, ("unknown", {}))
            lines.append(f"- `{rid}` ({ep}): {rec.get('title', 'unknown')}")
        if principle.get("derived_from"):
            lines.append("")
            lines.append("Derived from:")
            for source_id in principle.get("derived_from") or []:
                label = level3_map.get(source_id, {}).get("name") or record_map.get(source_id, ("", {}))[1].get("title") or "unknown"
                lines.append(f"- `{source_id}`: {label}")
        if principle.get("counterexamples"):
            lines.append(f"- Counterexamples: {', '.join(principle.get('counterexamples'))}")
        if principle.get("contradictions"):
            lines.append(f"- Contradictions: {'; '.join(principle.get('contradictions'))}")
        lines.append("")
    rejected = data.get("rejected_principle_candidates") or []
    if rejected:
        lines.extend(["## Rejected Level 4 Candidates", ""])
        for item in rejected:
            principle = item.get("principle", {})
            lines.extend(
                [
                    f"### {principle.get('principle_id')} — {principle.get('name')}",
                    "",
                    f"- Rejection reason: {item.get('rejection_reason')}",
                    f"- Supporting episodes: {', '.join(principle.get('supporting_episode_ids') or [])}",
                    f"- Runtime mode: `{principle.get('runtime_mode_recommendation')}`",
                    f"- Confidence: `{principle.get('confidence')}`",
                    "",
                ]
            )
    lines.extend(["## Level 2 -> Level 3 -> Level 4 Examples", ""])
    for principle in data.get("principles", [])[:5]:
        lines.extend(example_lines(principle, level3_map, record_map))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def example_lines(principle: dict[str, Any], level3_map: dict[str, Any], record_map: dict[str, tuple[str, dict[str, Any]]]) -> list[str]:
    lines = [f"### Example: {principle.get('name')}", ""]
    lines.append("Level 2 cases:")
    for rid in (principle.get("supporting_records") or [])[:4]:
        ep, record = record_map.get(rid, ("unknown", {}))
        lines.append(f"- `{rid}` ({ep}): {record.get('title', 'unknown')}")
    lines.append("")
    lines.append("Level 3 abstraction:")
    for source_id in principle.get("derived_from") or []:
        if source_id in level3_map:
            p = level3_map[source_id]
            lines.append(f"- `{source_id}`: {p.get('name')} — {p.get('core_preference')}")
    lines.append("")
    lines.append("Level 4 abstraction:")
    lines.append(f"- {principle.get('core_preference')}")
    lines.append(f"- Preserved details: conditions={principle.get('conditions')}; exceptions={principle.get('exceptions')}")
    lines.append(f"- Not generalized: {principle.get('contradictions') or 'No unsupported universal claim added.'}")
    lines.append("")
    return lines


def write_confidence_audit_report(
    path: Path,
    level3_outputs: list[dict[str, Any]],
    level4: dict[str, Any],
    level4_validation: dict[str, Any],
    level4_review: dict[str, Any],
) -> None:
    lines = [
        "# Principle Confidence Audit",
        "",
        "## Previous Implementation",
        "",
        "- `confidence` was a flat `low|medium|high` string emitted by the LLM.",
        "- Deterministic code validated only the enum value and supporting-record references.",
        "- It did not override high confidence, compute thread independence, or compute runtime eligibility.",
        "",
        "## New Implementation",
        "",
        "- `confidence` is a deterministic multi-dimensional object after post-processing.",
        "- Level 3 `overall` is constrained to `low` or `medium`; `high` is not allowed for episode-local principles.",
        "- Level 4 requires at least two supporting episodes.",
        "- Single-record Level 3 outputs become `case_derived_hypothesis` and are not eligible for Level 4.",
        "- Runtime eligibility is separate from extraction confidence.",
        "",
        "## Level 3 Summary",
        "",
    ]
    for level3 in level3_outputs:
        principles = level3.get("principles", [])
        lines.extend(
            [
                f"### {level3.get('episode_id')}",
                "",
                f"- Strategy: `{level3.get('selected_strategy')}`",
                f"- Principle count: `{len(principles)}`",
                f"- Overall confidence: `{dict(Counter((p.get('confidence') or {}).get('overall') for p in principles))}`",
                f"- Principle types: `{dict(Counter(p.get('principle_type') for p in principles))}`",
                f"- Runtime modes: `{dict(Counter(p.get('runtime_mode_recommendation') for p in principles))}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Level 4 Summary",
            "",
            f"- Candidate count: `{len(level4.get('principles', []))}`",
            f"- Rejected candidate count: `{len(level4.get('rejected_principle_candidates', []))}`",
            f"- Validation ok: `{level4_validation.get('ok')}`",
            f"- Confidence: `{level4_validation.get('confidence_distribution')}`",
            f"- Runtime modes: `{level4_validation.get('runtime_mode_distribution')}`",
            f"- Codex review: `{level4_review.get('summary')}`",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_principle_quality_report(
    path: Path,
    level3_outputs: list[dict[str, Any]],
    level4: dict[str, Any],
    level4_validation: dict[str, Any],
    level4_review: dict[str, Any],
) -> None:
    single_record = []
    recurring = []
    for level3 in level3_outputs:
        for principle in level3.get("principles", []):
            if principle.get("principle_type") == "case_derived_hypothesis":
                single_record.append(principle)
            else:
                recurring.append(principle)
    lines = [
        "# Principle Quality Summary",
        "",
        "## Before / After",
        "",
        "- Old flat confidence allowed high Level 3 values from the LLM.",
        "- New Level 3 overall confidence is deterministic and cannot be high.",
        "- Old Level 4 had 2 candidates; new validated Level 4 has "
        f"{len(level4.get('principles', []))} candidate and {len(level4.get('rejected_principle_candidates', []))} rejected candidate.",
        "",
        "## Single-Record Hypotheses",
        "",
    ]
    for principle in single_record:
        lines.append(f"- `{principle.get('principle_id')}`: {principle.get('name')} -> `{principle.get('runtime_mode_recommendation')}`")
    lines.extend(["", "## Recurring / Conditional Episode-Local Patterns", ""])
    for principle in recurring:
        lines.append(f"- `{principle.get('principle_id')}`: {principle.get('name')} -> `{principle.get('runtime_mode_recommendation')}` / confidence `{(principle.get('confidence') or {}).get('overall')}`")
    lines.extend(["", "## Level 4 Runtime Eligibility", ""])
    for principle in level4.get("principles", []):
        lines.extend(
            [
                f"### {principle.get('principle_id')} — {principle.get('name')}",
                "",
                f"- Runtime mode: `{principle.get('runtime_mode_recommendation')}`",
                f"- Eligible online: `{principle.get('eligible_for_online_reasoning')}`",
                f"- Confidence: `{principle.get('confidence')}`",
                f"- Genericness: `{principle.get('genericness_status')}`",
                f"- Review: `{next((item for item in level4_review.get('items', []) if item.get('principle_id') == principle.get('principle_id')), {})}`",
                "",
            ]
        )
    if level4.get("rejected_principle_candidates"):
        lines.extend(["## Rejected Level 4 Candidates", ""])
        for item in level4.get("rejected_principle_candidates", []):
            principle = item.get("principle", {})
            lines.append(f"- `{principle.get('principle_id')}`: {principle.get('name')} — {item.get('rejection_reason')}")
    lines.extend(["", "## Validation", "", f"- Level 4 validation: `{level4_validation}`", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline Level 3/4 principle extraction.")
    parser.add_argument("--provider", default="config/provider.yaml")
    parser.add_argument("--model-id", help="Override PHASE2_MODEL_ID/provider model_id.")
    parser.add_argument("--episode", default="EP674")
    parser.add_argument("--all-configured", action="store_true")
    return parser


def main() -> int:
    command = Path(sys.argv[0]).name
    parser = build_parser()
    args = parser.parse_args()
    if command == "extract-level3":
        return extract_level3(args)
    if command == "extract-level4":
        return extract_level4(args)
    if command == "validate-principles":
        return validate_principles(args)
    if command == "report-principles":
        return report_principles(args)
    raise SystemExit(f"Unsupported principles command: {command}")


if __name__ == "__main__":
    raise SystemExit(main())
