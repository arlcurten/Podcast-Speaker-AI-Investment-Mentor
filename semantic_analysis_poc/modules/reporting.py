from __future__ import annotations

from pathlib import Path
from typing import Any


def fmt_seconds(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.1f}s"
    return "unknown"


def write_review_report(
    path: Path,
    episode_id: str,
    level1: dict[str, Any],
    level2: dict[str, Any],
    level3: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    records = level2.get("reasoning_records", [])
    strategy = level3.get("selected_strategy")
    lines: list[str] = [
        f"# {episode_id} Phase 2 Human Review",
        "",
        "This report is for reviewing whether the semantic extraction preserved useful investment reasoning. It is not a transcript word-error review.",
        "",
        "## Episode Structure",
        "",
        f"- Structure type: `{level1.get('episode_structure_type')}`",
        f"- Structure confidence: `{level1.get('episode_structure_confidence')}`",
        f"- Level 3 strategy: `{level1.get('recommended_level3_strategy')}`",
        f"- Strategy reason: {level1.get('level3_strategy_reason')}",
        "",
        "Review questions:",
        "",
        "- Is the detected episode structure correct?",
        "- Did the model find the main topics?",
        "- Did it connect interrupted or resumed topic threads correctly?",
        "- Did it preserve useful Q&A content?",
        "- Did Level 3 add useful information or over-compress the episode?",
        "- Did the model invent unsupported conclusions?",
        "- Were terminology corrections reasonable?",
        "",
        "## Level 1 Topic Threads",
        "",
    ]
    for thread in level1.get("main_topic_threads", [])[:20]:
        regions = thread.get("region_refs", [])
        region_text = ", ".join(
            f"{fmt_seconds(r.get('start'))}-{fmt_seconds(r.get('end'))}" for r in regions[:4]
        )
        lines.extend([
            f"### {thread.get('topic_thread_id')} — {thread.get('title')}",
            "",
            f"- Regions: {region_text}",
            f"- Non-contiguous: `{thread.get('is_non_contiguous')}`",
            f"- Summary: {thread.get('summary')}",
            f"- Why important: {thread.get('importance_reason')}",
            "",
        ])
    lines.extend(["## Level 2 Reasoning Records", ""])
    for record in records:
        evidence = record.get("source_evidence", [])
        region_text = ", ".join(
            f"{fmt_seconds(e.get('start'))}-{fmt_seconds(e.get('end'))}" for e in evidence[:4]
        )
        lines.extend([
            f"### {record.get('reasoning_record_id')} — {record.get('title')}",
            "",
            f"- Topic thread: `{record.get('topic_thread_id')}`",
            f"- Evidence regions: {region_text}",
            f"- Confidence: `{record.get('confidence')}`",
            f"- Tags: {', '.join(record.get('tags', [])) if isinstance(record.get('tags'), list) else record.get('tags')}",
            "",
            "**Claims**",
            "",
            *[f"- {x}" for x in record.get("claims", [])[:5]],
            "",
            "**Reasoning / Conditions / Risks**",
            "",
            *[f"- Reasoning: {x}" for x in record.get("reasoning_steps", [])[:5]],
            *[f"- Condition: {x}" for x in record.get("conditions", [])[:5]],
            *[f"- Risk: {x}" for x in record.get("risks", [])[:5]],
            *[f"- Uncertainty: {x}" for x in record.get("uncertainty", [])[:5]],
            *[f"- Exception: {x}" for x in record.get("exceptions", [])[:5]],
            "",
            "**Review form**",
            "",
            "- Correct / partly correct / incorrect:",
            "- Missing context:",
            "- Unsupported conclusion:",
            "- Terminology issue:",
            "- Notes:",
            "",
        ])
    lines.extend([
        "## Level 3 Optional Consolidation",
        "",
        f"- Selected strategy: `{strategy}`",
        f"- Strategy reason: {level3.get('strategy_reason')}",
        "",
        "Review:",
        "",
        "- Did Level 3 add useful information?",
        "- Did it over-compress the episode?",
        "- Should it have used full_synthesis, light_consolidation, or bypass?",
        "",
        "```json",
        str(level3.get(strategy) if strategy else None),
        "```",
        "",
        "## Run Metadata",
        "",
        f"- Provider/model: `{metadata.get('provider')}` / `{metadata.get('model')}`",
        f"- Total latency seconds: `{metadata.get('total_latency_seconds')}`",
        f"- Total retries: `{metadata.get('total_retries')}`",
        f"- Estimated cost USD: `{metadata.get('estimated_cost_usd')}`",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
