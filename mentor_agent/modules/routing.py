from __future__ import annotations

from typing import Any

from .retrieval import RetrievalResult


def candidate_route(
    query_analysis: dict[str, Any],
    principle_candidates: list[RetrievalResult],
    case_candidates: list[RetrievalResult],
    config: dict[str, Any],
) -> dict[str, Any]:
    route_cfg = config.get("routing") or {}
    min_principle_cases = int(route_cfg.get("min_cases_for_principle_assisted", 2))
    min_case_only = int(route_cfg.get("min_cases_for_case_only", 1))
    current_gap = bool(query_analysis.get("current_data_requirement"))

    if principle_candidates and len(case_candidates) >= min_principle_cases:
        route = "principle_assisted"
        reasons = ["eligible Level 4 principle found", "sufficient Level 2 cases available"]
    elif len(case_candidates) >= min_case_only:
        route = "case_only"
        reasons = ["no sufficiently eligible principle", "Level 2 cases available"]
    else:
        route = "insufficient_evidence"
        reasons = ["insufficient Level 2 evidence"]
    if current_gap:
        reasons.append("current data required; answer must be historical/framework-only")
    return {"candidate_route": route, "route_reasons": reasons}


def final_route_from_assessment(assessment: dict[str, Any], fallback: str) -> str:
    selected = assessment.get("selected_route")
    if selected in {"principle_assisted", "case_only", "insufficient_evidence"}:
        return str(selected)
    return fallback

