from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from modules.knowledge import ReasoningRecord


FIELD_WEIGHTS = {
    "title": 3.0,
    "tags": 2.6,
    "claims": 2.1,
    "reasoning_steps": 2.0,
    "conditions": 2.3,
    "risks": 2.3,
    "exceptions": 2.1,
    "uncertainty": 2.1,
    "decisions_or_preferences": 2.0,
    "actions_or_avoided_actions": 1.8,
    "view_change_conditions": 2.2,
    "speaker_behavior": 1.8,
    "teaching_or_explanation_style": 1.2,
    "observations": 1.4,
    "source_evidence": 1.0,
}


QUERY_EXPANSIONS = {
    "追高": ["短期", "恐慌", "風險", "technical_analysis", "risk_management", "moving_average", "market_correction"],
    "風險": ["risk", "risks", "risk_management", "uncertainty", "conditions", "technical_analysis", "market_correction"],
    "風險控制": ["risk_management", "conditions", "risks", "uncertainty", "portfolio", "position", "moving_average"],
    "不確定": ["uncertainty", "risks", "conditions", "exceptions", "market_correction", "narrative_analysis"],
    "不確定性": ["uncertainty", "risks", "conditions", "exceptions", "market_correction", "narrative_analysis"],
    "踏空": ["investor psychology", "fear", "greed", "market sentiment", "opportunity cost", "bull markets", "beta"],
    "操作": ["conditions", "decisions", "actions", "risk_management", "portfolio", "position", "moving_average"],
    "操作條件": ["conditions", "view_change_conditions", "assumptions", "risk_management", "moving_average", "market_correction"],
    "條件": ["conditions", "view_change_conditions", "assumptions", "risk_management"],
    "心理": ["investor psychology", "market sentiment", "greed", "fear", "speaker_behavior", "behavioral_finance"],
    "投資心理": ["investor psychology", "market sentiment", "greed", "fear", "behavioral_finance", "wealth_effect"],
    "部位": ["portfolio", "position", "allocation", "risk_management", "滿倉", "beta"],
    "停損": ["risk_management", "moving_average", "technical_analysis", "market_correction", "季線"],
    "等待": ["conditions", "uncertainty", "risk_management", "view_change_conditions", "market_correction"],
    "Apple": ["Apple", "蘋果", "果粉", "price hike", "consumer", "tech giants", "pricing_power"],
    "apple": ["Apple", "蘋果", "果粉", "price hike", "consumer", "tech giants", "pricing_power"],
    "蘋果": ["Apple", "果粉", "price hike", "consumer", "tech giants", "pricing_power"],
    "買": ["conditions", "risks", "uncertainty", "view_change_conditions"],
    "改變看法": ["view_change_conditions", "conditions", "exceptions", "uncertainty", "risks"],
    "矛盾": ["exceptions", "uncertainty", "conditions", "corrections", "contradictions"],
    "職涯": ["career_advice", "financial_priority", "early_career", "MTK", "job satisfaction", "financial stability"],
    "工作": ["career_advice", "financial_priority", "early_career", "MTK", "job satisfaction", "burnout"],
    "MTK": ["career_advice", "financial_priority", "early_career", "MTK"],
    "合夥": ["business_philosophy", "founder_control", "ownership_structure", "partnership", "stakeholders", "skin-in-the-game"],
    "創業": ["business_philosophy", "founder_control", "ownership_structure", "partnership", "stakeholders", "skin-in-the-game"],
    "股權": ["ownership_structure", "founder_control", "stakeholders"],
}


STOP_TERMS = {
    "現在",
    "可以",
    "現在可",
    "在可",
    "可以買",
    "怎麼",
    "通常",
    "哪些",
    "這一",
    "一集",
    "觀點",
    "他的",
    "他在",
    "會怎",
}


@dataclass
class RetrievedRecord:
    record: ReasoningRecord
    score: float
    matched_terms: list[str]
    matched_fields: dict[str, list[str]] = field(default_factory=dict)
    score_breakdown: dict[str, float] = field(default_factory=dict)


class HybridRetriever:
    def __init__(self, records: list[ReasoningRecord]):
        self.records = records
        self.doc_terms: dict[str, Counter[str]] = {}
        self.field_terms: dict[str, dict[str, set[str]]] = {}
        self.doc_freq: Counter[str] = Counter()
        for record in records:
            fields = record.field_texts()
            all_terms: list[str] = []
            field_map: dict[str, set[str]] = {}
            for field_name, text in fields.items():
                terms = terms_for_text(text)
                field_map[field_name] = set(terms)
                all_terms.extend(terms)
            counts = Counter(all_terms)
            self.doc_terms[record.record_id] = counts
            self.field_terms[record.record_id] = field_map
            for term in counts:
                self.doc_freq[term] += 1
        self.avg_doc_len = sum(sum(c.values()) for c in self.doc_terms.values()) / max(len(self.doc_terms), 1)

    def search(self, query: str, top_k: int = 3, min_score: float = 1.0) -> list[RetrievedRecord]:
        expanded_query = expand_query(query)
        query_terms = terms_for_text(expanded_query)
        if not query_terms:
            return []
        results: list[RetrievedRecord] = []
        for record in self.records:
            bm25 = self._bm25(record, query_terms)
            field_score, matched_fields = self._field_score(record, query_terms)
            phrase_score = self._phrase_score(record, expanded_query)
            score = bm25 + field_score + phrase_score
            matched_terms = sorted(set(query_terms) & set(self.doc_terms[record.record_id]))
            if score >= min_score:
                results.append(
                    RetrievedRecord(
                        record=record,
                        score=score,
                        matched_terms=matched_terms[:16],
                        matched_fields=matched_fields,
                        score_breakdown={
                            "bm25": bm25,
                            "field": field_score,
                            "phrase": phrase_score,
                        },
                    )
                )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    def _bm25(self, record: ReasoningRecord, query_terms: list[str]) -> float:
        counts = self.doc_terms[record.record_id]
        doc_len = sum(counts.values())
        k1 = 1.4
        b = 0.75
        score = 0.0
        n_docs = max(len(self.records), 1)
        for term in query_terms:
            tf = counts.get(term, 0)
            if not tf:
                continue
            df = self.doc_freq.get(term, 0)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1 - b + b * doc_len / max(self.avg_doc_len, 1))
            score += idf * (tf * (k1 + 1)) / denom
        return score

    def _field_score(self, record: ReasoningRecord, query_terms: list[str]) -> tuple[float, dict[str, list[str]]]:
        field_map = self.field_terms[record.record_id]
        matched_fields: dict[str, list[str]] = {}
        score = 0.0
        for field_name, field_terms in field_map.items():
            matched = sorted(set(query_terms) & field_terms)
            if not matched:
                continue
            matched_fields[field_name] = matched[:10]
            score += FIELD_WEIGHTS.get(field_name, 1.0) * len(matched)
        return score / math.sqrt(max(len(set(query_terms)), 1)), matched_fields

    def _phrase_score(self, record: ReasoningRecord, expanded_query: str) -> float:
        text = record.searchable_text()
        score = 0.0
        for phrase, expansions in QUERY_EXPANSIONS.items():
            if phrase in expanded_query and (phrase in text or any(item in text for item in expansions)):
                score += 1.5
        return score


def search_reasoning_records(
    query: str,
    records: list[ReasoningRecord],
    top_k: int = 3,
    min_score: float = 1.0,
) -> list[RetrievedRecord]:
    return HybridRetriever(records).search(query, top_k=top_k, min_score=min_score)


def terms_for_text(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-zA-Z0-9_][a-zA-Z0-9_+-]{1,}", lowered)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = [a + b for a, b in zip(cjk, cjk[1:])]
    trigrams = [a + b + c for a, b, c in zip(cjk, cjk[1:], cjk[2:])]
    return [term for term in [*latin, *bigrams, *trigrams] if term not in STOP_TERMS]


def expand_query(query: str) -> str:
    additions: list[str] = []
    for key, values in QUERY_EXPANSIONS.items():
        if key in query:
            additions.extend(values)
    return " ".join([query, *additions])
