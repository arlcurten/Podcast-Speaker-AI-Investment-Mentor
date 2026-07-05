from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any


INVESTMENT_DOMAINS = {"investment", "market", "industry", "portfolio", "behavioral_investing", "risk_management"}


@dataclass
class QueryAnalysis:
    query: str
    query_type: str
    user_scenario_facts: list[str]
    decision_being_asked: str
    target_domains: list[str]
    historical_or_conceptual_or_current: str
    missing_information: list[str]
    current_data_requirement: bool
    speaker_specificity_requirement: str
    candidate_search_terms: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_query_deterministic(query: str) -> QueryAnalysis:
    current = _has_any(query, ["現在", "今天", "目前", "最新", "現階段"])
    action = _has_any(query, ["買", "賣", "加碼", "減碼", "進場", "出場", "適合", "追高"])
    if _has_any(query, ["職涯", "工作", "薪水", "轉職"]):
        qtype = "non_investment_domain"
        mode = "conceptual"
    elif current and action:
        qtype = "current_market_question"
        mode = "current"
    elif _has_any(query, ["怎麼看", "如何看待", "通常", "有沒有", "哪些觀點"]):
        qtype = "historical_view"
        mode = "historical"
    elif _has_any(query, ["應該", "如果", "情境", "我"]):
        qtype = "new_scenario_decision"
        mode = "conceptual"
    else:
        qtype = "investment_concept"
        mode = "conceptual"

    domains: set[str] = set()
    if _has_any(query, ["風險", "不確定", "停損", "追高", "踏空", "部位", "操作", "條件"]):
        domains.update(["risk_management", "investment", "behavioral_investing"])
    if _has_any(query, ["市場", "台股", "美股", "估值", "本益比", "科技股", "AI", "Apple", "台積電"]):
        domains.update(["market", "industry", "investment"])
    if _has_any(query, ["職涯", "工作", "薪水", "轉職"]):
        domains.add("career")
    if _has_any(query, ["創業", "合夥", "股權", "商業"]):
        domains.add("business")
    if not domains:
        domains.add("general")

    missing: list[str] = []
    if qtype == "new_scenario_decision":
        if not re.search(r"(部位|時間|標的|風險|價格|條件|資金)", query):
            missing.append("user scenario lacks position size, time horizon, target, or risk constraints")
    if current:
        missing.append("current market/news/price data is not available in this runtime")
    if _has_any(query, ["沒有在節目中談過", "節目沒有談過", "沒談過", "沒有資料", "未載入"]):
        missing.append("query states the target is not covered by loaded podcast evidence")

    terms = extract_search_terms(query)
    return QueryAnalysis(
        query=query,
        query_type=qtype,
        user_scenario_facts=[query] if qtype == "new_scenario_decision" else [],
        decision_being_asked="; ".join(terms[:6]) or query,
        target_domains=sorted(domains),
        historical_or_conceptual_or_current=mode,
        missing_information=missing,
        current_data_requirement=current and action,
        speaker_specificity_requirement="speaker_specific_evidence_required",
        candidate_search_terms=terms,
    )


def extract_search_terms(query: str) -> list[str]:
    cjk_terms = re.findall(r"[\u4e00-\u9fff]{2,}", query)
    latin_terms = re.findall(r"[A-Za-z0-9_+-]{2,}", query)
    expansions: list[str] = []
    mapping = {
        "追高": ["風險", "踏空", "操作條件", "market_correction"],
        "踏空": ["追高", "投資心理", "market sentiment"],
        "不確定": ["風險", "等待", "conditions"],
        "風險": ["risk_management", "conditions", "exceptions"],
        "條件": ["conditions", "view_change_conditions", "switch_conditions"],
        "市場": ["market", "valuation", "sentiment"],
        "敘事": ["narrative", "data", "structural explanation"],
        "數據": ["data", "empirical", "evidence"],
    }
    for key, values in mapping.items():
        if key in query:
            expansions.extend(values)
    seen: set[str] = set()
    out: list[str] = []
    for term in [*cjk_terms, *latin_terms, *expansions]:
        if term not in seen:
            seen.add(term)
            out.append(term)
    return out


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)
