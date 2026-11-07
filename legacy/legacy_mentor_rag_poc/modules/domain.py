from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from modules.knowledge import ReasoningRecord


INVESTMENT_INTENTS = {
    "investment_decision",
    "market_analysis",
    "risk_management",
    "behavioral_investing",
}

RELEVANCE_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


RECORD_OVERRIDES: dict[str, tuple[list[str], str, str]] = {
    "EP450-reasoning-005": (["personal"], "none", "neurodiversity/self-adjustment; not investment evidence"),
    "EP450-reasoning-007": (["business"], "low", "daigou business operations/regulatory risk; not market-investment evidence"),
    "EP450-reasoning-008": (["other"], "none", "speech moderation/censorship; not investment evidence"),
    "EP674-reasoning-007": (["career", "personal"], "low", "career compensation advice; useful only for career queries"),
    "EP674-reasoning-008": (["business"], "low", "business ownership structure; useful only for business queries"),
    "EP670-reasoning-008": (["personal", "portfolio"], "medium", "personal finance/capital preservation; adjacent to portfolio risk"),
    "EP57-reasoning-005": (["business", "industry"], "medium", "business-model comparison of listed travel platforms"),
    "EP57-reasoning-006": (["investment", "other"], "medium", "stock-analysis learning workflow"),
    "EP57-reasoning-007": (["other"], "none", "media/generational critique; not investment evidence"),
    "EP57-reasoning-008": (["other"], "none", "communication style/censorship; not investment evidence"),
}


@dataclass(frozen=True)
class DomainAnnotation:
    record_id: str
    domains: list[str]
    investment_relevance: str
    rationale: str


def classify_query_intent(query: str) -> str:
    if any(term in query for term in ["職涯", "工作", "薪水", "MTK", "轉職", "職場"]):
        return "career"
    if any(term in query for term in ["創業", "合夥", "股權", "公司治理", "代購", "商業模式"]):
        return "business"
    if any(term in query for term in ["心理", "踏空", "恐慌", "貪婪", "情緒"]):
        return "behavioral_investing"
    if any(term in query for term in ["風險", "不確定", "停損", "部位", "追高", "操作", "條件"]):
        return "risk_management"
    if any(term in query for term in ["市場", "台股", "美股", "AI", "科技股", "台積電", "Apple", "AMD", "NVIDIA"]):
        return "market_analysis"
    if any(term in query for term in ["買", "賣", "加碼", "減碼", "進場", "出場"]):
        return "investment_decision"
    if any(term in query for term in ["人生", "家庭", "消費", "花錢", "生活"]):
        return "personal"
    return "general"


def annotate_record(record: ReasoningRecord) -> DomainAnnotation:
    if record.record_id in RECORD_OVERRIDES:
        domains, relevance, reason = RECORD_OVERRIDES[record.record_id]
        return DomainAnnotation(
            record_id=record.record_id,
            domains=domains,
            investment_relevance=relevance,
            rationale=f"deterministic current-index override: {reason}",
        )

    text = f"{record.title}\n{record.searchable_text()}".lower()
    domains: set[str] = set()

    if has_any(text, ["tsmc", "台積", "apple", "amd", "nvidia", "ai", "semiconductor", "supply_chain", "space", "spacex", "群聯", "美光", "被動元件"]):
        domains.add("industry")
    if has_any(text, ["market", "市場", "correction", "valuation", "p/e", "本益比", "估值", "股價", "台股", "美股", "多頭", "修正", "regime", "景氣"]):
        domains.add("market")
    if has_any(text, ["portfolio", "部位", "滿倉", "position", "etf", "dividend", "yield", "資金", "capital", "復委託"]):
        domains.add("portfolio")
    if has_any(text, ["risk_management", "risk", "風險", "停損", "moving_average", "季線", "uncertainty", "不確定", "timing", "追高", "踏空"]):
        domains.add("investment")
    if has_any(text, ["behavioral", "psychology", "sentiment", "narrative", "attention", "noise", "anchoring", "fear", "greed", "心理", "敘事", "情緒"]):
        domains.add("behavioral_investing")
    if has_any(text, ["business", "founder", "ownership", "stakeholder", "代購", "客服", "商業", "股權", "合夥", "創業"]):
        domains.add("business")
    if has_any(text, ["career", "job", "early_career", "mtk", "職涯", "工作", "薪水"]):
        domains.add("career")
    if has_any(text, ["personal_finance", "life_transitions", "神經", "adhd", "自閉", "家庭", "個人財務"]):
        domains.add("personal")
    if has_any(text, ["media", "censorship", "communication", "gatekeeping", "言論", "審查", "創作者"]):
        domains.add("other")

    if not domains:
        domains.add("other")

    relevance = infer_investment_relevance(text, domains)
    return DomainAnnotation(
        record_id=record.record_id,
        domains=sorted(domains),
        investment_relevance=relevance,
        rationale=build_rationale(domains, relevance),
    )


def infer_investment_relevance(text: str, domains: set[str]) -> str:
    if domains & {"investment", "market", "portfolio", "industry", "behavioral_investing"}:
        if has_any(text, ["stock", "stocks", "股", "market", "市場", "valuation", "估值", "ai", "tsmc", "apple", "amd", "nvidia", "etf", "部位", "風險", "本益比"]):
            return "high"
        return "medium"
    if domains & {"business", "career", "personal"}:
        if has_any(text, ["投資", "資金", "capital", "financial", "財務", "復委託", "券商"]):
            return "medium"
        return "low"
    return "none"


def should_reject_for_query(intent: str, annotation: DomainAnnotation, config: dict[str, Any] | None = None) -> tuple[bool, str]:
    cfg = config or {}
    if not cfg.get("enabled", True):
        return False, "domain gate disabled"
    domains = set(annotation.domains)
    relevance = RELEVANCE_RANK.get(annotation.investment_relevance, 0)
    min_rel = RELEVANCE_RANK.get(str(cfg.get("min_investment_relevance_for_investment_query", "medium")), 2)

    if intent in INVESTMENT_INTENTS:
        if relevance < min_rel and domains <= {"business", "career", "personal", "other"}:
            return True, f"rejected: {intent} query with {annotation.investment_relevance} investment relevance and domains {sorted(domains)}"
        if domains <= {"career"} or domains <= {"personal"} or domains <= {"other"}:
            return True, f"rejected: domain mismatch for {intent}: {sorted(domains)}"
    if intent == "career" and domains.isdisjoint({"career", "personal", "business"}):
        return True, f"rejected: career query mismatch: {sorted(domains)}"
    if intent == "business" and domains.isdisjoint({"business", "industry", "market"}):
        return True, f"rejected: business query mismatch: {sorted(domains)}"
    return False, "kept"


def domain_adjustment(intent: str, annotation: DomainAnnotation, config: dict[str, Any] | None = None) -> float:
    cfg = config or {}
    if not cfg.get("enabled", True):
        return 1.0
    domains = set(annotation.domains)
    boost = float(cfg.get("matching_domain_boost", 1.12))
    penalty = float(cfg.get("mismatch_penalty", 0.55))
    if intent == "risk_management" and domains & {"investment", "market", "portfolio", "behavioral_investing"}:
        return boost
    if intent == "behavioral_investing" and domains & {"behavioral_investing", "investment", "market"}:
        return boost
    if intent == "market_analysis" and domains & {"market", "industry", "investment", "portfolio"}:
        return boost
    if intent == "investment_decision" and domains & {"investment", "market", "portfolio", "industry", "behavioral_investing"}:
        return boost
    if intent == "career" and domains & {"career", "personal", "business"}:
        return boost
    if intent == "business" and domains & {"business", "industry", "market"}:
        return boost
    if intent in INVESTMENT_INTENTS and domains <= {"business", "career", "personal", "other"}:
        return penalty
    return 1.0


def has_any(text: str, needles: list[str]) -> bool:
    return any(needle.lower() in text for needle in needles)


def build_rationale(domains: set[str], relevance: str) -> str:
    return f"deterministic domain tags from title/tags/claims/risks; investment_relevance={relevance}; domains={sorted(domains)}"
