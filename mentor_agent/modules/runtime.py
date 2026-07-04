from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .common import ROOT, read_yaml
from .guards import run_guards
from .model import ChatResult, OpenAICompatibleClient
from .query import QueryAnalysis, analyze_query_deterministic
from .retrieval import RetrievalResult, make_case_search, make_principle_search
from .routing import candidate_route, final_route_from_assessment
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
        route = final_route_from_assessment(assessment, candidate["candidate_route"])
        answer, answer_meta = self._generate_answer(analysis, principles, cases, assessment, route)
        guards = run_guards(answer, analysis.to_dict(), cases, assessment)
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
        cases = self.case_search.search(
            analysis,
            top_k=int(cfg.get("case_top_k", 6)),
            min_score=float(cfg.get("min_case_score", 1.8)),
        )
        # Add supporting cases for selected principles, and keep conflicts/exceptions
        # visible through ordinary search rather than only support-only evidence.
        by_id = {case.item_id: case for case in cases}
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
                    cases.append(item)
                    by_id[record_id] = item
        cases.sort(key=lambda x: x.score, reverse=True)
        return principles, cases[: int(cfg.get("case_top_k", 6))]

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
            payload, result = self._client().json_chat(
                [
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
            )
            normalized = normalize_assessment(payload, fallback)
            return normalized, model_meta(result, "applicability")
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
            result = self._client().chat(
                [
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
            )
            return result.content, model_meta(result, "answer")
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
Always preserve missing information, exceptions, conflicts, and current-data gaps."""


ANSWER_SYSTEM = """You are an AI Investment Mentor runtime, not the podcast speaker.
Answer in Traditional Chinese. Be concise and practical.
Separate speaker historical evidence, model interpretation, current-data limitation, and user-scenario assumptions when relevant.
Use Level 4 principles only as an organizing framework. Support speaker-specific claims with Level 2 record citations.
Do not provide current buy/sell advice without current data tools. Include citations using episode, timestamp, and record IDs."""


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
        "conflicting_evidence": collect_fields(cases, "contradictions", limit=4),
        "supporting_cases": [x.item_id for x in cases],
        "counter_cases": [],
        "current_data_needed": analysis.current_data_requirement,
        "principle_overreach_risk": "medium" if route == "principle_assisted" else "low",
        "provisional_conclusion": "Use retrieved cases as evidence; principle is framework only." if cases else "No sufficient evidence.",
        "confidence": "medium" if route != "insufficient_evidence" else "low",
        "selected_route": route,
        "route_reasons": candidate["route_reasons"],
    }


def normalize_assessment(payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    out = {**fallback, **{k: v for k, v in payload.items() if k in fallback or k in {"warnings"}}}
    if out.get("selected_route") not in {"principle_assisted", "case_only", "insufficient_evidence"}:
        out["selected_route"] = fallback["selected_route"]
    for key in ["candidate_principles", "applicable_principles", "rejected_principles", "missing_information", "supporting_cases"]:
        if not isinstance(out.get(key), list):
            out[key] = fallback.get(key, [])
    return out


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
        "claims": f.get("claims", [])[:3],
        "reasoning_steps": f.get("reasoning_steps", [])[:4],
        "conditions": f.get("conditions", [])[:4],
        "risks": f.get("risks", [])[:4],
        "exceptions": f.get("exceptions", [])[:4],
        "uncertainty": f.get("uncertainty", [])[:3],
        "speaker_behavior": f.get("speaker_behavior", [])[:3],
        "citations": [c.label() for c in r.citations()[:3]],
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


def model_meta(result: ChatResult, stage: str) -> dict[str, Any]:
    return {
        "used_llm": True,
        "stage": stage,
        "model": result.model,
        "usage": result.usage,
        "latency_seconds": result.latency_seconds,
        "retries": result.retries,
    }


def merge_model_metadata(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"stages": items}
