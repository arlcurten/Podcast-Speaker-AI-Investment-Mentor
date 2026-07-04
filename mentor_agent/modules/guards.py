from __future__ import annotations

import re
from typing import Any

from .retrieval import RetrievalResult


def validate_citations(answer: str, cases: list[RetrievalResult]) -> dict[str, Any]:
    retrieved_ids = {case.item_id for case in cases}
    answer_ids = set(re.findall(r"EP\d+-reasoning-\d+", answer))
    invalid = sorted(answer_ids - retrieved_ids)
    missing_citation = not bool(answer_ids) and bool(cases)
    return {
        "ok": not invalid and not missing_citation,
        "retrieved_record_ids": sorted(retrieved_ids),
        "answer_record_ids": sorted(answer_ids),
        "invalid_record_ids": invalid,
        "warnings": (["answer has no reasoning-record citation"] if missing_citation else [])
        + ([f"answer cites non-retrieved records: {invalid}"] if invalid else []),
    }


def current_market_guard(query_analysis: dict[str, Any], answer: str) -> dict[str, Any]:
    required = bool(query_analysis.get("current_data_requirement"))
    if not required:
        return {"ok": True, "required": False, "warnings": []}
    limitation_terms = ["沒有即時", "無法驗證", "不能判斷現在", "不具備即時", "current"]
    ok = any(term in answer for term in limitation_terms)
    return {
        "ok": ok,
        "required": True,
        "warnings": [] if ok else ["current-market question does not clearly state current-data limitation"],
    }


def unsupported_speaker_claim_guard(answer: str, cases: list[RetrievalResult]) -> dict[str, Any]:
    if not cases and any(term in answer for term in ["他會", "他通常", "講者"]):
        return {"ok": False, "warnings": ["speaker-specific claim with no retrieved cases"]}
    return {"ok": True, "warnings": []}


def principle_overreach_guard(assessment: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    if assessment.get("selected_route") == "principle_assisted":
        if not assessment.get("applicable_principles"):
            warnings.append("principle_assisted route without applicable principles")
        if assessment.get("principle_overreach_risk") in {"high", "major"}:
            warnings.append("applicability assessment reports high principle overreach risk")
    return {"ok": not warnings, "warnings": warnings}


def run_guards(answer: str, query_analysis: dict[str, Any], cases: list[RetrievalResult], assessment: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "citations": validate_citations(answer, cases),
        "current_market": current_market_guard(query_analysis, answer),
        "unsupported_speaker_claim": unsupported_speaker_claim_guard(answer, cases),
        "principle_overreach": principle_overreach_guard(assessment),
    }
    warnings = []
    for check in checks.values():
        warnings.extend(check.get("warnings", []))
    return {"ok": all(check.get("ok", False) for check in checks.values()), "checks": checks, "warnings": warnings}

