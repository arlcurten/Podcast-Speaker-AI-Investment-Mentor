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
    estimate_cost,
    parse_json_object,
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
BROAD_WORDS = ("always", "never", "任何情況", "必然", "一定", "所有市場", "固定")


def load_provider(path: str, model_id: str | None) -> dict[str, Any]:
    provider = read_yaml(ROOT / path)
    provider["model_id"] = model_id or resolve_env_value(provider.get("model_id")) or os.environ.get("PHASE2_MODEL_ID")
    return provider


def output_dir(episode_id: str) -> Path:
    return ROOT / "data" / "outputs" / episode_id


def cross_episode_dir() -> Path:
    return ROOT / "data" / "outputs" / "cross_episode"


def available_episodes() -> list[str]:
    root = ROOT / "data" / "outputs"
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
        write_level3_report(ROOT / "reports" / f"{episode_id}_level3_principles_review.md", data, level2, validation, metadata)
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
    write_level4_report(ROOT / "reports" / "cross_episode_level4_principles_review.md", data, level3_outputs, level2_outputs, validation, review, metadata)
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
    write_level4_report(ROOT / "reports" / "cross_episode_level4_principles_review.md", level4, level3_outputs, level2_outputs, validation, review, metadata)
    print(json.dumps({"report": "reports/cross_episode_level4_principles_review.md"}, ensure_ascii=False, indent=2))
    return 0


def validate_level3_principles(data: dict[str, Any], level2: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    episode_id = level2.get("episode_id")
    record_ids = {r.get("reasoning_record_id") for r in level2.get("reasoning_records", [])}
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
        if principle.get("confidence") not in CONFIDENCE_VALUES:
            errors.append(f"{pid} confidence is invalid")
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
        if len(refs) == 1:
            warnings.append(f"{pid} is based on one supporting record; keep confidence low or explain narrow scope")
        if principle.get("confidence") == "high" and len(set(refs)) < 3:
            warnings.append(f"{pid} high confidence has limited supporting record coverage")
        if not principle.get("conditions"):
            warnings.append(f"{pid} missing conditions")
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
        "confidence_distribution": dict(Counter(p.get("confidence") for p in principles)),
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
        if principle.get("confidence") not in CONFIDENCE_VALUES:
            errors.append(f"{pid} confidence is invalid")
        if len(set(declared_eps)) <= 1 and principle.get("confidence") != "low":
            warnings.append(f"{pid} has one supporting episode but confidence is not low")
        if principle.get("confidence") == "high" and len(set(declared_eps)) < 3:
            warnings.append(f"{pid} high confidence has fewer than 3 supporting episodes")
        if not principle.get("conditions"):
            warnings.append(f"{pid} missing conditions")
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
        "confidence_distribution": dict(Counter(p.get("confidence") for p in principles)),
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
        "warnings": len(validation.get("warnings", [])),
        "latency_seconds": metadata.get("latency_seconds"),
        "tokens": (metadata.get("usage") or {}).get("total_tokens"),
        "cost": (metadata.get("usage") or {}).get("cost") or metadata.get("estimated_cost_usd"),
    }


def level4_summary(data: dict[str, Any], validation: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "principles": len(data.get("principles", [])),
        "principle_names": [p.get("name") for p in data.get("principles", [])],
        "confidence_distribution": validation.get("confidence_distribution"),
        "warnings": len(validation.get("warnings", [])),
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
                f"- Domains: {', '.join(principle.get('domains') or [])}",
                f"- Decision context: {principle.get('decision_context')}",
                f"- Core preference: {principle.get('core_preference')}",
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
                f"- Supporting episodes: {', '.join(principle.get('supporting_episode_ids') or [])}",
                f"- Decision context: {principle.get('decision_context')}",
                f"- Core preference: {principle.get('core_preference')}",
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
