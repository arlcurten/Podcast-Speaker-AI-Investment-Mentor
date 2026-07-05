from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import ROOT, read_yaml, write_json
from .runtime import MentorRuntime
from .validation_runner import token_latency


def private_conflict_validation_dir() -> Path:
    root = os.environ.get("PIPELINE_DATA_ROOT")
    if not root:
        raise RuntimeError("PIPELINE_DATA_ROOT is required for private conflict validation outputs.")
    return Path(root) / "mentor_agent" / "evaluations" / "conflict_handling"


def run_conflict_validation(
    config_path: Path = ROOT / "config" / "conflict_validation_questions.yaml",
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    cfg = read_yaml(config_path)
    runtime = MentorRuntime(use_llm=use_llm)
    output_dir = private_conflict_validation_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in cfg.get("questions", []):
        result = runtime.ask(str(item["query"]))
        rows.append({"question": item, "new_result": result, "review": review_conflict_result(item, result)})
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": os.environ.get("MENTOR_MODEL_ID") or runtime.config["model"].get("model_id"),
        "question_count": len(rows),
        "rows": rows,
        "summary": summarize_conflicts(rows),
    }
    write_json(output_dir / "conflict_validation_full_traces.json", payload)
    (output_dir / "conflict_validation_report.md").write_text(render_conflict_markdown(payload), encoding="utf-8")
    return {"output_dir": str(output_dir), "summary": payload["summary"]}


def review_conflict_result(question: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    assessment = result.get("applicability_assessment", {})
    cases = result.get("retrieved_cases", [])
    answer = result.get("answer", "")
    category = question.get("category")
    roles = [case.get("evidence_role_candidate") for case in cases]
    conflict_items = assessment.get("conflicting_evidence") or []
    context_items = assessment.get("resolved_context_differences") or []
    exceptions = assessment.get("exceptions_triggered") or []
    unresolved = assessment.get("unresolved_conflicts") or []
    checks = {
        "conflict_retrieval_quality": grade(
            bool(set(roles) & {"potential_conflict", "context_difference", "exception"}),
            na=category == "no_conflict_control",
        ),
        "true_conflict_identified": grade(bool(conflict_items or context_items or unresolved), na=category not in {"weak_or_partial_conflict", "principle_case_tension"}),
        "false_conflict_avoided": grade(not conflict_items, na=category not in {"context_difference", "exception_not_conflict", "no_conflict_control"}),
        "context_difference_identified": grade(bool(context_items), na=category != "context_difference"),
        "exception_not_mislabeled": grade(bool(exceptions) and not unresolved, na=category != "exception_not_conflict"),
        "unresolved_conflict_preserved": grade(bool(unresolved) or assessment.get("missing_information"), na=category != "unresolved_missing_context"),
        "route_adjusted_correctly": grade(result.get("selected_route") in {"case_only", "insufficient_evidence"}, na=category != "unresolved_missing_context"),
        "confidence_adjusted_correctly": grade(assessment.get("confidence") in {"low", "medium"}, na=not (conflict_items or unresolved)),
        "answer_explains_tradeoff": grade(any(term in answer for term in ["取決", "條件", "如果", "差異", "兩種", "不能直接"])),
        "both_sides_cited": grade(citation_count(answer) >= 2, na=not (conflict_items or context_items or unresolved)),
        "no_overgeneralization": grade(not any(term in answer for term in ["永遠", "一定都", "必然"])),
    }
    return {
        "checks": checks,
        "candidate_roles": role_counts(roles),
        "token_latency": token_latency(result),
        "reason": short_conflict_reason(checks),
    }


def grade(ok: bool, *, na: bool = False) -> str:
    if na:
        return "not_applicable"
    return "pass" if ok else "partial"


def citation_count(answer: str) -> int:
    return answer.count("EP")


def role_counts(roles: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for role in roles:
        out[str(role)] = out.get(str(role), 0) + 1
    return out


def short_conflict_reason(checks: dict[str, str]) -> str:
    partials = [key for key, value in checks.items() if value == "partial"]
    return "OK" if not partials else "Needs review: " + ", ".join(partials)


def summarize_conflicts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    route_counts: dict[str, int] = {}
    grade_counts: dict[str, int] = {}
    role_counts_total: dict[str, int] = {}
    total_tokens = 0
    total_cost = 0.0
    total_latency = 0.0
    conflict_count = 0
    context_difference_count = 0
    unresolved_count = 0
    for row in rows:
        result = row["new_result"]
        route = result.get("selected_route")
        route_counts[route] = route_counts.get(route, 0) + 1
        assessment = result.get("applicability_assessment", {})
        conflict_count += len(assessment.get("conflicting_evidence") or [])
        context_difference_count += len(assessment.get("resolved_context_differences") or [])
        unresolved_count += len(assessment.get("unresolved_conflicts") or [])
        tl = row["review"]["token_latency"]
        total_tokens += tl["total_tokens"]
        total_cost += tl["total_cost"]
        total_latency += tl["total_latency_seconds"]
        for role, count in row["review"]["candidate_roles"].items():
            role_counts_total[role] = role_counts_total.get(role, 0) + count
        for grade_value in row["review"]["checks"].values():
            grade_counts[grade_value] = grade_counts.get(grade_value, 0) + 1
    return {
        "route_counts": route_counts,
        "candidate_role_counts": role_counts_total,
        "grade_counts": grade_counts,
        "conflicting_evidence_items": conflict_count,
        "resolved_context_difference_items": context_difference_count,
        "unresolved_conflict_items": unresolved_count,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "total_latency_seconds": total_latency,
    }


def render_conflict_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Mentor Agent Conflict Handling Validation", "", "## Summary", ""]
    lines.append("```json")
    lines.append(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    lines.append("```")
    for row in payload["rows"]:
        q = row["question"]
        result = row["new_result"]
        assessment = result.get("applicability_assessment", {})
        lines.extend(
            [
                "",
                f"## {q['id']}",
                "",
                f"- Category: `{q.get('category')}`",
                f"- Query: {q.get('query')}",
                f"- Final route: `{result.get('selected_route')}`",
                f"- Candidate roles: `{row['review']['candidate_roles']}`",
                f"- Conflicting evidence: `{assessment.get('conflicting_evidence', [])}`",
                f"- Context differences: `{assessment.get('resolved_context_differences', [])}`",
                f"- Exceptions: `{assessment.get('exceptions_triggered', [])}`",
                f"- Unresolved conflicts: `{assessment.get('unresolved_conflicts', [])}`",
                f"- Confidence: `{assessment.get('confidence')}`",
                f"- Citations valid: `{result.get('guards', {}).get('checks', {}).get('citations', {}).get('ok')}`",
                f"- Token/latency: `{row['review']['token_latency']}`",
                f"- Review: {row['review']['reason']}",
            ]
        )
    return "\n".join(lines) + "\n"
