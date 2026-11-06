from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import PROJECT_ROOT, ROOT, read_yaml, write_json
from .runtime import MentorRuntime


def private_validation_dir() -> Path:
    root = os.environ.get("PIPELINE_DATA_ROOT")
    if not root:
        raise RuntimeError("PIPELINE_DATA_ROOT is required for private validation outputs.")
    return Path(root) / "mentor_agent" / "evaluations" / "reasoning_quality"


def run_reasoning_validation(
    config_path: Path = ROOT / "config" / "reasoning_validation_questions.yaml",
    *,
    use_llm: bool = True,
    run_legacy: bool = True,
) -> dict[str, Any]:
    cfg = read_yaml(config_path)
    runtime = MentorRuntime(use_llm=use_llm)
    output_dir = private_validation_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for item in cfg.get("questions", []):
        query = str(item["query"])
        result = runtime.ask(query)
        review = review_result(item, result)
        rows.append({"question": item, "new_result": result, "review": review})

    legacy_ids = set(cfg.get("legacy_comparison_ids") or [])
    comparisons = []
    if run_legacy:
        for row in rows:
            if row["question"].get("id") not in legacy_ids:
                continue
            legacy = run_legacy_answer(row["question"]["query"])
            comparisons.append(compare_legacy_new(row["question"], legacy, row["new_result"]))

    summary = summarize(rows, comparisons)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": os.environ.get("MENTOR_MODEL_ID") or runtime.config["model"].get("model_id"),
        "question_count": len(rows),
        "rows": rows,
        "legacy_comparisons": comparisons,
        "summary": summary,
    }
    write_json(output_dir / "reasoning_validation_full_traces.json", payload)
    (output_dir / "reasoning_validation_report.md").write_text(render_markdown(payload), encoding="utf-8")
    return {"output_dir": str(output_dir), "summary": summary}


def review_result(question: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    assessment = result.get("applicability_assessment", {})
    guards = result.get("guards", {})
    answer = result.get("answer", "")
    route = result.get("selected_route")
    expected = str(question.get("expected_behavior", ""))
    checks = {
        "route_correct": grade_route(route, expected),
        "principle_use_correct": grade_principle_use(result, expected),
        "principle_rejection_correct": "pass" if result.get("rejected_principles") or route != "principle_assisted" else "partial",
        "case_support_sufficient": "pass" if len(result.get("retrieved_cases", [])) >= 2 or route == "insufficient_evidence" else "partial",
        "conflicts_retained": "pass" if assessment.get("conflicting_evidence") else ("not_applicable" if question.get("category") != "conflicting_evidence" else "partial"),
        "exceptions_retained": "pass" if assessment.get("exceptions_triggered") else ("not_applicable" if question.get("category") != "exception_trigger" else "partial"),
        "missing_information_identified": "pass" if assessment.get("missing_information") else ("not_applicable" if question.get("category") not in {"missing_information", "current_market", "insufficient_evidence"} else "fail"),
        "current_data_limitation_correct": "pass" if guards.get("checks", {}).get("current_market", {}).get("ok") else ("not_applicable" if not result.get("query_analysis", {}).get("current_data_requirement") else "fail"),
        "speaker_claims_grounded": "pass" if guards.get("checks", {}).get("unsupported_speaker_claim", {}).get("ok") else "fail",
        "citations_valid": "pass" if guards.get("checks", {}).get("citations", {}).get("ok") else "fail",
        "answer_is_conditional": "pass" if any(term in answer for term in ["如果", "條件", "不能", "無法", "取決", "需要"]) else "partial",
        "answer_is_not_simple_record_summary": "pass" if "主要證據" not in answer[:120] else "partial",
        "answer_is_not_overgeneralized": grade_overgeneralization(answer),
    }
    return {
        "checks": checks,
        "reason": short_reason(question, result, checks),
        "token_latency": token_latency(result),
    }


def grade_route(route: str, expected: str) -> str:
    if expected == "principle_assisted":
        return "pass" if route == "principle_assisted" else "fail"
    if "non_investment" in expected:
        return "pass" if route in {"case_only", "insufficient_evidence"} else "fail"
    if "case_only" in expected and route in {"case_only", "principle_assisted"}:
        return "pass" if route == "case_only" else "partial"
    if "case_only" in expected and "insufficient" in expected:
        return "pass" if route in {"case_only", "insufficient_evidence"} else "fail"
    if "insufficient" in expected:
        return "pass" if route == "insufficient_evidence" else "fail"
    if "no_current" in expected:
        return "pass" if route in {"case_only", "insufficient_evidence", "principle_assisted"} else "fail"
    return "not_applicable"


def grade_principle_use(result: dict[str, Any], expected: str) -> str:
    route = result.get("selected_route")
    if "reject" in expected or "current_data_warning" in expected:
        return "pass" if result.get("applicability_assessment", {}).get("current_data_needed") or route != "principle_assisted" else "partial"
    if route == "principle_assisted":
        return "pass" if result.get("selected_principles") else "fail"
    return "not_applicable"


def grade_overgeneralization(answer: str) -> str:
    if any(phrase in answer for phrase in ["不能說", "不應說", "不是永遠", "不能把"]):
        return "pass"
    return "pass" if not any(term in answer for term in ["永遠", "一定都", "必然"]) else "fail"


def run_legacy_answer(query: str) -> dict[str, Any]:
    env = os.environ.copy()
    proc = subprocess.run(
        [sys.executable, "main.py", "ask", query, "--debug"],
        cwd=PROJECT_ROOT / "legacy" / "legacy_mentor_rag_poc",
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def compare_legacy_new(question: dict[str, Any], legacy: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": question.get("id"),
        "query": question.get("query"),
        "legacy_returncode": legacy["returncode"],
        "legacy_answer_chars": len(legacy.get("stdout", "")),
        "new_route": new.get("selected_route"),
        "new_principles": new.get("selected_principles", []),
        "new_missing_information": new.get("applicability_assessment", {}).get("missing_information", []),
        "new_exceptions": new.get("applicability_assessment", {}).get("exceptions_triggered", []),
        "new_conflicts": new.get("applicability_assessment", {}).get("conflicting_evidence", []),
        "new_citations_valid": new.get("guards", {}).get("checks", {}).get("citations", {}).get("ok"),
        "observation": "New runtime exposes route/applicability diagnostics; legacy output is retained as case-only baseline.",
    }


def token_latency(result: dict[str, Any]) -> dict[str, Any]:
    stages = result.get("model_metadata", {}).get("stages", [])
    total_tokens = 0
    total_cost = 0.0
    total_latency = 0.0
    prompt_chars = 0
    by_stage = []
    for stage in stages:
        usage = stage.get("usage") or {}
        total_tokens += int(usage.get("total_tokens") or 0)
        total_cost += float(usage.get("cost") or 0.0)
        total_latency += float(stage.get("latency_seconds") or 0.0)
        prompt_chars += int(stage.get("prompt_chars") or 0)
        by_stage.append(
            {
                "stage": stage.get("stage"),
                "tokens": usage.get("total_tokens"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "cost": usage.get("cost"),
                "latency_seconds": stage.get("latency_seconds"),
                "prompt_chars": stage.get("prompt_chars"),
                "retries": stage.get("retries"),
            }
        )
    return {"total_tokens": total_tokens, "total_cost": total_cost, "total_latency_seconds": total_latency, "prompt_chars": prompt_chars, "by_stage": by_stage}


def summarize(rows: list[dict[str, Any]], comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    route_counts: dict[str, int] = {}
    grade_counts: dict[str, int] = {}
    total_tokens = 0
    total_cost = 0.0
    total_latency = 0.0
    for row in rows:
        route = row["new_result"].get("selected_route")
        route_counts[route] = route_counts.get(route, 0) + 1
        tl = row["review"]["token_latency"]
        total_tokens += tl["total_tokens"]
        total_cost += tl["total_cost"]
        total_latency += tl["total_latency_seconds"]
        for grade in row["review"]["checks"].values():
            grade_counts[grade] = grade_counts.get(grade, 0) + 1
    return {
        "route_counts": route_counts,
        "grade_counts": grade_counts,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "total_latency_seconds": total_latency,
        "legacy_comparison_count": len(comparisons),
    }


def short_reason(question: dict[str, Any], result: dict[str, Any], checks: dict[str, str]) -> str:
    failures = [k for k, v in checks.items() if v == "fail"]
    partials = [k for k, v in checks.items() if v == "partial"]
    if failures:
        return f"Needs review: {', '.join(failures)}."
    if partials:
        return f"Usable with caveats: {', '.join(partials)}."
    return f"Route {result.get('selected_route')} looks acceptable for {question.get('category')}."


def render_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Mentor Agent Reasoning Validation", "", "## Summary", ""]
    lines.append("```json")
    lines.append(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    lines.append("```")
    for row in payload["rows"]:
        q = row["question"]
        result = row["new_result"]
        review = row["review"]
        lines.extend(
            [
                "",
                f"## {q['id']}",
                "",
                f"- Category: `{q.get('category')}`",
                f"- Query: {q.get('query')}",
                f"- Final route: `{result.get('selected_route')}`",
                f"- Selected principles: `{result.get('selected_principles')}`",
                f"- Rejected principles: `{result.get('rejected_principles')}`",
                f"- Missing information: `{result.get('applicability_assessment', {}).get('missing_information', [])}`",
                f"- Exceptions: `{result.get('applicability_assessment', {}).get('exceptions_triggered', [])}`",
                f"- Conflicts: `{result.get('applicability_assessment', {}).get('conflicting_evidence', [])}`",
                f"- Citations valid: `{result.get('guards', {}).get('checks', {}).get('citations', {}).get('ok')}`",
                f"- Token/latency: `{review['token_latency']}`",
                f"- Review: {review['reason']}",
            ]
        )
    lines.extend(["", "## Legacy Comparisons", ""])
    for row in payload["legacy_comparisons"]:
        lines.extend(
            [
                f"### {row['id']}",
                f"- Query: {row['query']}",
                f"- Legacy return code: `{row['legacy_returncode']}`",
                f"- New route: `{row['new_route']}`",
                f"- New missing information: `{row['new_missing_information']}`",
                f"- Observation: {row['observation']}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"
