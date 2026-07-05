from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .common import ROOT, read_yaml
from .guards import run_guards
from .model import ChatResult, OpenAICompatibleClient
from .query import QueryAnalysis, analyze_query_deterministic
from .retrieval import RetrievalResult, annotate_case_roles, make_case_search, make_principle_search
from .routing import candidate_route, final_route_from_assessment_with_config
from .stores import CaseStore, PrincipleStore, ReasoningRecord


class MentorRuntime:
    def __init__(self, config_path: Path = ROOT / "config" / "agent.yaml", *, use_llm: bool = True):
        self.config_path = config_path
        self.config = read_yaml(config_path)
        self.case_store = CaseStore(config_path)
        self.principle_store = PrincipleStore(config_path)
        self.case_search = make_case_search(self.case_store.records, self.config.get("retrieval") or {})
        self.principle_search = make_principle_search(self.principle_store.eligible(), self.config.get("retrieval") or {})
        self.use_llm = use_llm

    def validate_data(self) -> dict[str, Any]:
        case_validation = self.case_store.validate()
        principle_validation = self.principle_store.validate(self.case_store)
        return {"ok": case_validation["ok"] and principle_validation["ok"], "cases": case_validation, "principles": principle_validation}

    def inspect_query(self, query: str) -> dict[str, Any]:
        return self._analyze_query(query).to_dict()

    def retrieve(self, query: str) -> dict[str, Any]:
        analysis = self._analyze_query(query)
        principles, cases = self._retrieve(analysis)
        return {
            "query_analysis": analysis.to_dict(),
            "principle_candidates": [serialize_retrieval(x) for x in principles],
            "case_candidates": [serialize_retrieval(x) for x in cases],
        }

    def ask(self, query: str) -> dict[str, Any]:
        analysis = self._analyze_query(query)
        principles, cases = self._retrieve(analysis)
        candidate = candidate_route(analysis.to_dict(), principles, cases, self.config)
        assessment, assessment_meta = self._assess_applicability(analysis, principles, cases, candidate)
        route = final_route_from_assessment_with_config(assessment, candidate["candidate_route"], self.config)
        answer, answer_meta = self._generate_answer(analysis, principles, cases, assessment, route)
        guards = run_guards(answer, analysis.to_dict(), cases, assessment)
        if not guards["ok"] and route != "insufficient_evidence":
            answer = deterministic_answer(analysis, principles, cases, assessment, route)
            guards = run_guards(answer, analysis.to_dict(), cases, assessment)
            answer_meta = {
                "used_llm": False,
                "stage": "answer_safety_fallback",
                "reason": "guard failure on model answer",
                "original_answer_meta": answer_meta,
            }
        return {
            "query": query,
            "selected_route": route,
            "query_analysis": analysis.to_dict(),
            "principle_candidates": [serialize_retrieval(x) for x in principles],
            "selected_principles": assessment.get("applicable_principles", []),
            "rejected_principles": assessment.get("rejected_principles", []),
            "retrieved_cases": [serialize_retrieval(x) for x in cases],
            "applicability_assessment": assessment,
            "answer": answer,
            "citations": citation_labels(cases),
            "guards": guards,
            "model_metadata": merge_model_metadata([assessment_meta, answer_meta]),
        }

    def _analyze_query(self, query: str) -> QueryAnalysis:
        # MVP-first: deterministic analysis is stable and inspectable. LLM structured
        # analysis can be added here without changing downstream contracts.
        return analyze_query_deterministic(query)

    def _retrieve(self, analysis: QueryAnalysis) -> tuple[list[RetrievalResult], list[RetrievalResult]]:
        cfg = self.config.get("retrieval") or {}
        principles = self.principle_search.search(
            analysis,
            top_k=int(cfg.get("principle_top_k", 3)),
            min_score=float(cfg.get("min_principle_score", 0.8)),
        )
        principles = filter_principles_for_query(analysis, principles)
        case_pool = self.case_search.search(
            analysis,
            top_k=int(cfg.get("case_candidate_pool", max(int(cfg.get("case_top_k", 6)) * 2, 8))),
            min_score=float(cfg.get("min_case_score", 1.8)),
        )
        # Add supporting cases for selected principles, and keep conflicts/exceptions
        # visible through ordinary search rather than only support-only evidence.
        by_id = {case.item_id: case for case in case_pool}
        for principle in principles:
            for record_id in principle.item.supporting_records:
                record = self.case_store.get(record_id)
                if record and record_id not in by_id:
                    item = RetrievalResult(
                        item_id=record.record_id,
                        episode_id=record.episode_id,
                        title=record.title,
                        item=record,
                        score=max(principle.score * 0.8, 1.9),
                        matched_terms=[],
                        matched_fields={"principle_link": [principle.item_id]},
                        score_breakdown={"principle_link_score": principle.score},
                        relationship="principle_supporting_case",
                        selection_reason=f"selected: supporting case for {principle.item_id}",
                    )
                    case_pool.append(item)
                    by_id[record_id] = item
        supporting_records = {rid for principle in principles for rid in principle.item.supporting_records}
        cases = annotate_case_roles(analysis, list(by_id.values()), principle_supporting_records=supporting_records)
        cases = select_conflict_aware_cases(cases, int(cfg.get("case_top_k", 6)))
        return principles, cases

    def _client(self) -> OpenAICompatibleClient:
        return OpenAICompatibleClient(self.config["model"])

    def _assess_applicability(
        self,
        analysis: QueryAnalysis,
        principles: list[RetrievalResult],
        cases: list[RetrievalResult],
        candidate: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        fallback = deterministic_assessment(analysis, principles, cases, candidate)
        if not self.use_llm:
            return fallback, {"used_llm": False}
        try:
            messages = [
                {"role": "system", "content": APPLICABILITY_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "query_analysis": analysis.to_dict(),
                            "candidate_route": candidate,
                            "principles": [compact_principle(x) for x in principles],
                            "cases": [compact_case(x) for x in cases],
                            "allowed_routes": ["principle_assisted", "case_only", "insufficient_evidence"],
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            payload, result = self._client().json_chat(messages)
            normalized = normalize_assessment(payload, fallback)
            normalized = postprocess_conflict_classification(normalized, cases)
            return normalized, model_meta(result, "applicability", messages)
        except Exception as exc:
            fallback["warnings"] = [f"LLM applicability failed; deterministic fallback used: {exc}"]
            return fallback, {"used_llm": False, "error": str(exc), "stage": "applicability"}

    def _generate_answer(
        self,
        analysis: QueryAnalysis,
        principles: list[RetrievalResult],
        cases: list[RetrievalResult],
        assessment: dict[str, Any],
        route: str,
    ) -> tuple[str, dict[str, Any]]:
        if route == "insufficient_evidence":
            return insufficient_answer(analysis, assessment), {"used_llm": False}
        if not self.use_llm:
            return deterministic_answer(analysis, principles, cases, assessment, route), {"used_llm": False}
        try:
            messages = [
                {"role": "system", "content": ANSWER_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "route": route,
                            "query_analysis": analysis.to_dict(),
                            "applicability_assessment": assessment,
                            "principles": [compact_principle(x) for x in principles],
                            "cases": [compact_case(x) for x in cases],
                            "required_citations": citation_labels(cases),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            result = self._client().chat(messages)
            return result.content, model_meta(result, "answer", messages)
        except Exception as exc:
            return deterministic_answer(analysis, principles, cases, assessment, route), {
                "used_llm": False,
                "error": str(exc),
                "stage": "answer",
            }


APPLICABILITY_SYSTEM = """You are the structured applicability assessor for an AI Investment Mentor.
Return JSON only. Principles are revisable reasoning abstractions, not evidence. Level 2 cases are evidence authority.
Select only one route: principle_assisted, case_only, or insufficient_evidence. Do not use principle_guided.
Reject principles when context mismatches, evidence is weak, contradictions are unresolved, current data is needed, or the principle is too generic.
Always preserve missing information, exceptions, conflicts, context differences, and current-data gaps.

Definitions:
- conflict: cases address a similar decision context but support different directions, priorities, or preferences.
- exception: a principle or tendency generally applies, but a specific condition prevents direct use.
- context_difference: cases look different but apply to different time horizons, asset types, market regimes, information completeness, position sizing, risk tolerance, or objectives.

Required conflict handling:
1. Do not mark every different conclusion as conflict.
2. First compare whether conditions and decision context are similar.
3. Put explainable differences into resolved_context_differences.
4. Put same-context divergent preferences into conflicting_evidence.
5. Put unresolved issues into unresolved_conflicts and lower confidence.
6. Severe unresolved conflicts should downgrade principle_assisted to case_only, or case_only to insufficient_evidence.
7. Do not ignore counter-cases merely to preserve a principle.
8. If a case is marked evidence_role_candidate=exception, classify it as an exception unless it directly opposes another same-context case. Do not duplicate pure exceptions as conflicts.

Return these fields when relevant:
conflicting_evidence, resolved_context_differences, exceptions_triggered, unresolved_conflicts, conflict_resolution, confidence_impact."""


ANSWER_SYSTEM = """You are an AI Investment Mentor runtime, not the podcast speaker.
Answer in Traditional Chinese. Be concise and practical, preferably under 700 Chinese characters.
Separate speaker historical evidence, model interpretation, current-data limitation, and user-scenario assumptions when relevant.
Use Level 4 principles only as an organizing framework. Support speaker-specific claims with Level 2 record citations.
Do not provide current buy/sell advice without current data tools. Include citations using episode, timestamp, and record IDs.
When evidence conflicts, explain the trade-off naturally: what differs, which conditions separate the cases, what remains unresolved, and cite both sides."""


def deterministic_assessment(
    analysis: QueryAnalysis,
    principles: list[RetrievalResult],
    cases: list[RetrievalResult],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    route = candidate["candidate_route"]
    if any("not covered" in str(x) or "沒有資料" in str(x) for x in analysis.missing_information):
        route = "insufficient_evidence"
    if analysis.current_data_requirement and len(cases) < 2:
        route = "insufficient_evidence"
    if route == "principle_assisted" and not principles:
        route = "case_only" if cases else "insufficient_evidence"
    return {
        "candidate_principles": [x.item_id for x in principles],
        "applicable_principles": [x.item_id for x in principles[:1]] if route == "principle_assisted" else [],
        "rejected_principles": [x.item_id for x in principles[1:]] if principles else [],
        "matched_conditions": [],
        "unmatched_conditions": analysis.missing_information,
        "missing_information": analysis.missing_information,
        "exceptions_triggered": collect_fields(cases, "exceptions", limit=4),
        "conflicting_evidence": candidate_conflicts(cases),
        "resolved_context_differences": candidate_context_differences(cases),
        "unresolved_conflicts": [],
        "conflict_resolution": "No unresolved conflict identified by deterministic fallback.",
        "confidence_impact": "none",
        "supporting_cases": [x.item_id for x in cases],
        "counter_cases": [],
        "current_data_needed": analysis.current_data_requirement,
        "principle_overreach_risk": "medium" if route == "principle_assisted" else "low",
        "provisional_conclusion": "Use retrieved cases as evidence; principle is framework only." if cases else "No sufficient evidence.",
        "confidence": "medium" if route != "insufficient_evidence" else "low",
        "selected_route": route,
        "route_reasons": candidate["route_reasons"],
    }


def filter_principles_for_query(analysis: QueryAnalysis, principles: list[RetrievalResult]) -> list[RetrievalResult]:
    if not principles:
        return []
    query = analysis.query
    domains = set(analysis.target_domains)
    principle_terms = ["敘事", "數據", "資料", "估值", "利空", "利多", "結構", "narrative", "data"]
    has_principle_context = any(term in query for term in principle_terms)
    if analysis.query_type == "non_investment_domain":
        return []
    if not has_principle_context:
        return []
    return principles


def normalize_assessment(payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    out = {**fallback, **{k: v for k, v in payload.items() if k in fallback or k in {"warnings"}}}
    if out.get("selected_route") not in {"principle_assisted", "case_only", "insufficient_evidence"}:
        out["selected_route"] = fallback["selected_route"]
    for key in [
        "candidate_principles",
        "applicable_principles",
        "rejected_principles",
        "missing_information",
        "supporting_cases",
        "conflicting_evidence",
        "resolved_context_differences",
        "exceptions_triggered",
        "unresolved_conflicts",
    ]:
        if not isinstance(out.get(key), list):
            out[key] = fallback.get(key, [])
    for key in ["conflict_resolution", "confidence_impact"]:
        if not isinstance(out.get(key), str):
            out[key] = fallback.get(key, "")
    return out


def postprocess_conflict_classification(assessment: dict[str, Any], cases: list[RetrievalResult]) -> dict[str, Any]:
    role_by_id = {case.item_id: case.evidence_role_candidate for case in cases}
    kept_conflicts = []
    moved = []
    for item in assessment.get("conflicting_evidence") or []:
        ids = referenced_record_ids(item)
        if ids and all(role_by_id.get(rid) == "exception" for rid in ids):
            moved.append(item)
            continue
        kept_conflicts.append(item)
    if moved:
        assessment["conflicting_evidence"] = kept_conflicts
        existing = assessment.get("exceptions_triggered") or []
        assessment["exceptions_triggered"] = [*existing, *moved]
        note = "Exception-only candidate conflicts were reclassified as exceptions."
        assessment["conflict_resolution"] = "; ".join(x for x in [str(assessment.get("conflict_resolution") or ""), note] if x)
    return assessment


def referenced_record_ids(item: Any) -> list[str]:
    if isinstance(item, str):
        return []
    if not isinstance(item, dict):
        return []
    ids: list[str] = []
    for key in ["record_id", "case_id"]:
        value = item.get(key)
        if isinstance(value, str):
            ids.append(value)
    value = item.get("record_ids")
    if isinstance(value, list):
        ids.extend(str(x) for x in value)
    return ids


def deterministic_answer(
    analysis: QueryAnalysis,
    principles: list[RetrievalResult],
    cases: list[RetrievalResult],
    assessment: dict[str, Any],
    route: str,
) -> str:
    lines: list[str] = []
    if analysis.current_data_requirement:
        lines.append("先說限制：目前 runtime 沒有即時股價、新聞或財報工具，所以不能判斷現在是否適合買賣；只能用歷史 podcast evidence 提供推理框架。")
    if route == "principle_assisted" and principles:
        p = principles[0].item
        lines.append(f"可用的歷史推理框架：{p.name}。這是 Level 4 抽象，只能輔助思考，不能當成額外證據。")
    if assessment.get("unresolved_conflicts"):
        lines.append("目前有未解的 historical evidence conflict，因此不能給單邊結論；必須先釐清缺少條件。")
    elif assessment.get("conflicting_evidence"):
        lines.append("歷史案例中有不同做法；需要比較它們的適用條件，而不是直接選邊。")
    if assessment.get("resolved_context_differences"):
        lines.append("部分差異可由時間尺度、估值/敘事條件、部位或市場階段解釋，屬於 context difference。")
    lines.append("從 Level 2 cases 來看，較安全的回答是條件式的：先確認資料與條件，再決定是否行動；不要只因敘事或情緒做操作。")
    if assessment.get("missing_information"):
        lines.append("缺少資訊：" + "；".join(str(x) for x in assessment["missing_information"]))
    lines.append("主要證據：")
    for case in cases[:3]:
        record: ReasoningRecord = case.item
        cites = "; ".join(c.label() for c in record.citations()[:2])
        lines.append(f"- {record.title}（{cites}）")
    return "\n".join(lines)


def insufficient_answer(analysis: QueryAnalysis, assessment: dict[str, Any]) -> str:
    prefix = ""
    if analysis.current_data_requirement:
        prefix = "目前沒有即時市場資料工具，不能回答當前買賣判斷。"
    return prefix + "目前已載入的 podcast evidence 不足以支撐 speaker-specific 結論；需要更多情境或更多來源 cases。"


def compact_principle(item: RetrievalResult) -> dict[str, Any]:
    p = item.item
    f = p.fields
    return {
        "principle_id": p.principle_id,
        "name": p.name,
        "score": item.score,
        "decision_context": f.get("decision_context"),
        "core_preference": f.get("core_preference"),
        "decision_tradeoff": f.get("decision_tradeoff"),
        "conditions": f.get("conditions"),
        "exceptions": f.get("exceptions"),
        "contradictions": f.get("contradictions"),
        "counterexamples": f.get("counterexamples"),
        "runtime_mode_recommendation": f.get("runtime_mode_recommendation"),
        "confidence": f.get("confidence"),
        "supporting_records": f.get("supporting_records"),
    }


def compact_case(item: RetrievalResult) -> dict[str, Any]:
    r: ReasoningRecord = item.item
    f = r.fields
    return {
        "record_id": r.record_id,
        "episode_id": r.episode_id,
        "title": r.title,
        "score": item.score,
        "relationship": item.relationship,
        "evidence_role_candidate": item.evidence_role_candidate,
        "role_reason": item.role_reason,
        "conflict_signal_score": item.conflict_signal_score,
        "exception_signal_score": item.exception_signal_score,
        "claims": f.get("claims", [])[:3],
        "reasoning_steps": f.get("reasoning_steps", [])[:3],
        "assumptions": f.get("assumptions", [])[:2],
        "conditions": f.get("conditions", [])[:3],
        "view_change_conditions": f.get("view_change_conditions", [])[:2],
        "risks": f.get("risks", [])[:3],
        "exceptions": f.get("exceptions", [])[:3],
        "contradictions": f.get("contradictions", [])[:3],
        "decisions_or_preferences": f.get("decisions_or_preferences", [])[:3],
        "actions_or_avoided_actions": f.get("actions_or_avoided_actions", [])[:3],
        "uncertainty": f.get("uncertainty", [])[:2],
        "speaker_behavior": f.get("speaker_behavior", [])[:2],
        "citations": [c.label() for c in r.citations()[:2]],
    }


def serialize_retrieval(item: RetrievalResult) -> dict[str, Any]:
    return {
        "id": item.item_id,
        "episode_id": item.episode_id,
        "title": item.title,
        "score": item.score,
        "matched_terms": item.matched_terms,
        "matched_fields": item.matched_fields,
        "score_breakdown": item.score_breakdown,
        "relationship": item.relationship,
        "evidence_role_candidate": item.evidence_role_candidate,
        "role_reason": item.role_reason,
        "conflict_signal_score": item.conflict_signal_score,
        "exception_signal_score": item.exception_signal_score,
        "selection_reason": item.selection_reason,
    }


def citation_labels(cases: list[RetrievalResult]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for case in cases:
        record: ReasoningRecord = case.item
        for citation in record.citations()[:3]:
            label = citation.label()
            if label not in seen:
                labels.append(label)
                seen.add(label)
    return labels


def collect_fields(cases: list[RetrievalResult], field: str, limit: int) -> list[str]:
    out: list[str] = []
    for case in cases:
        values = case.item.fields.get(field) or []
        if isinstance(values, list):
            out.extend(str(x) for x in values)
    return out[:limit]


def select_conflict_aware_cases(cases: list[RetrievalResult], top_k: int) -> list[RetrievalResult]:
    cases = sorted(cases, key=lambda x: x.score, reverse=True)
    selected: list[RetrievalResult] = []
    def add(predicate):
        for case in cases:
            if case in selected:
                continue
            if predicate(case):
                selected.append(case)
                if len(selected) >= top_k:
                    return

    add(lambda x: x.evidence_role_candidate == "potential_conflict")
    add(lambda x: x.evidence_role_candidate == "exception")
    add(lambda x: x.evidence_role_candidate == "context_difference")
    add(lambda x: x.evidence_role_candidate == "supporting")
    add(lambda x: True)
    return sorted(selected[:top_k], key=lambda x: x.score, reverse=True)


def candidate_conflicts(cases: list[RetrievalResult]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for case in cases:
        if case.evidence_role_candidate != "potential_conflict":
            continue
        out.append(
            {
                "record_ids": [case.item_id],
                "conflict_topic": case.title,
                "why_they_conflict": case.role_reason,
                "whether_resolvable_by_context": "unknown",
                "effect_on_conclusion": "requires LLM applicability assessment",
            }
        )
    return out[:4]


def candidate_context_differences(cases: list[RetrievalResult]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for case in cases:
        if case.evidence_role_candidate != "context_difference":
            continue
        out.append(
            {
                "record_ids": [case.item_id],
                "apparent_difference": case.title,
                "context_difference": case.role_reason,
                "why_not_a_true_conflict": "candidate context difference; final classification requires applicability assessment",
            }
        )
    return out[:4]


def model_meta(result: ChatResult, stage: str, messages: list[dict[str, str]] | None = None) -> dict[str, Any]:
    prompt_chars = sum(len(m.get("content", "")) for m in messages or [])
    return {
        "used_llm": True,
        "stage": stage,
        "model": result.model,
        "usage": result.usage,
        "latency_seconds": result.latency_seconds,
        "retries": result.retries,
        "prompt_chars": prompt_chars,
    }


def merge_model_metadata(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"stages": items}
