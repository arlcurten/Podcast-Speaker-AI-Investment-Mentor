from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from modules.common import OpenAICompatibleClient
from modules.domain import annotate_record, classify_query_intent, domain_adjustment, should_reject_for_query
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
    "現在", "可以", "現在可", "在可", "可以買", "怎麼", "通常", "哪些", "這一", "一集", "觀點", "他的", "他在", "會怎",
}

DEFAULT_RETRIEVAL_CONFIG = {
    "candidate_k": 12,
    "top_k": 5,
    "min_score": 2.0,
    "min_rerank_score": 0.45,
    "max_per_episode": 3,
    "lexical_weight": 0.50,
    "dense_weight": 0.35,
    "metadata_weight": 0.15,
    "mmr_lambda": 0.72,
    "domain_gate": {
        "enabled": True,
        "min_investment_relevance_for_investment_query": "medium",
        "matching_domain_boost": 1.12,
        "mismatch_penalty": 0.55,
    },
}


@dataclass
class RetrievedRecord:
    record: ReasoningRecord
    score: float
    matched_terms: list[str]
    matched_fields: dict[str, list[str]] = field(default_factory=dict)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    selection_reason: str = "selected"
    rerank_reason: str = ""


class LocalLSAEmbedder:
    """Small local semantic projection over canonical record text; no external vector DB."""

    model_name = "local-tfidf-char-word-lsa"

    def __init__(self, records: list[ReasoningRecord]):
        self.records = records
        self.texts = [canonical_embedding_text(record) for record in records]
        self.record_ids = [record.record_id for record in records]
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=1, sublinear_tf=True)
        matrix = self.vectorizer.fit_transform(self.texts)
        n_components = max(2, min(32, matrix.shape[0] - 1, matrix.shape[1] - 1))
        self.svd: TruncatedSVD | None = None
        if n_components >= 2:
            self.svd = TruncatedSVD(n_components=n_components, random_state=13)
            self.record_vectors = self._normalize(self.svd.fit_transform(matrix))
        else:
            self.record_vectors = self._normalize(matrix.toarray())

    def score(self, query: str) -> dict[str, float]:
        query_vec = self.vectorizer.transform([expand_query(query)])
        if self.svd is not None:
            query_arr = self.svd.transform(query_vec)
        else:
            query_arr = query_vec.toarray()
        query_arr = self._normalize(query_arr)
        sims = cosine_similarity(query_arr, self.record_vectors)[0]
        return {record_id: float(score) for record_id, score in zip(self.record_ids, sims)}

    @staticmethod
    def _normalize(values: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return values / norms


class HybridRetriever:
    def __init__(self, records: list[ReasoningRecord], config: dict[str, Any] | None = None):
        self.records = records
        self.config = {**DEFAULT_RETRIEVAL_CONFIG, **(config or {})}
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
        self.embedder = LocalLSAEmbedder(records)
        self.domain_annotations = {record.record_id: annotate_record(record) for record in records}
        self.last_rejections: list[dict[str, Any]] = []

    def search(
        self,
        query: str,
        top_k: int | None = None,
        min_score: float | None = None,
        mode: str = "hybrid_dense",
        provider_config: dict[str, Any] | None = None,
    ) -> list[RetrievedRecord]:
        top_k = top_k or int(self.config["top_k"])
        min_score = float(self.config["min_score"] if min_score is None else min_score)
        candidate_k = max(int(self.config["candidate_k"]), top_k)
        candidates = self._candidate_scores(query, min_score=0.0)
        if mode == "lexical":
            candidates = [c for c in candidates if c.score >= min_score]
            return candidates[:top_k]
        if mode in {"hybrid_dense", "hybrid_rerank"}:
            dense_scores = self.embedder.score(query)
            hybrid: list[RetrievedRecord] = []
            for item in candidates:
                lexical = item.score_breakdown.get("lexical", item.score)
                metadata = item.score_breakdown.get("metadata", 0.0)
                dense = dense_scores.get(item.record.record_id, 0.0)
                final = (
                    normalize_score(lexical) * float(self.config["lexical_weight"])
                    + dense * float(self.config["dense_weight"])
                    + normalize_score(metadata) * float(self.config["metadata_weight"])
                ) * 10.0
                item.score = final
                item.score_breakdown.update({"dense": dense, "final_pre_rerank": final})
                hybrid.append(item)
            hybrid.sort(key=lambda x: x.score, reverse=True)
            hybrid = self._apply_domain_gate(query, hybrid)
            hybrid = [item for item in hybrid if item.score >= min_score]
            selected = self._diversify(hybrid[:candidate_k], top_k=top_k)
            if mode == "hybrid_dense":
                return selected
            reranked = rerank_with_llm(query, hybrid[:candidate_k], provider_config, min_score=float(self.config["min_rerank_score"]))
            return self._diversify(reranked, top_k=top_k)
        raise ValueError(f"Unknown retrieval mode: {mode}")

    def _candidate_scores(self, query: str, min_score: float) -> list[RetrievedRecord]:
        expanded_query = expand_query(query)
        query_terms = terms_for_text(expanded_query)
        if not query_terms:
            return []
        results: list[RetrievedRecord] = []
        for record in self.records:
            bm25 = self._bm25(record, query_terms)
            field_score, matched_fields = self._field_score(record, query_terms)
            phrase_score = self._phrase_score(record, expanded_query)
            metadata_score = self._metadata_score(matched_fields)
            lexical = bm25 + field_score + phrase_score
            matched_terms = sorted(set(query_terms) & set(self.doc_terms[record.record_id]))
            if lexical >= min_score:
                results.append(
                    RetrievedRecord(
                        record=record,
                        score=lexical,
                        matched_terms=matched_terms[:16],
                        matched_fields=matched_fields,
                        score_breakdown={
                            "bm25": bm25,
                            "field": field_score,
                            "phrase": phrase_score,
                            "metadata": metadata_score,
                            "lexical": lexical,
                        },
                    )
                )
        results.sort(key=lambda item: item.score, reverse=True)
        return results

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

    @staticmethod
    def _metadata_score(matched_fields: dict[str, list[str]]) -> float:
        metadata_fields = {"tags", "conditions", "risks", "exceptions", "uncertainty", "speaker_behavior"}
        return sum(len(v) for k, v in matched_fields.items() if k in metadata_fields)

    def _diversify(self, items: list[RetrievedRecord], top_k: int) -> list[RetrievedRecord]:
        selected: list[RetrievedRecord] = []
        episode_counts: Counter[str] = Counter()
        max_per_episode = int(self.config.get("max_per_episode", 3))
        for item in items:
            if episode_counts[item.record.episode_id] >= max_per_episode:
                item.selection_reason = "rejected: episode cap"
                continue
            if any(near_duplicate(item.record, prev.record) for prev in selected):
                item.selection_reason = "rejected: near duplicate"
                continue
            selected.append(item)
            episode_counts[item.record.episode_id] += 1
            if len(selected) >= top_k:
                break
        return selected

    def _apply_domain_gate(self, query: str, items: list[RetrievedRecord]) -> list[RetrievedRecord]:
        gate_cfg = self.config.get("domain_gate") or {}
        if not gate_cfg.get("enabled", True):
            return items
        intent = classify_query_intent(query)
        kept: list[RetrievedRecord] = []
        self.last_rejections = []
        for item in items:
            annotation = self.domain_annotations[item.record.record_id]
            reject, reason = should_reject_for_query(intent, annotation, gate_cfg)
            item.score_breakdown.update(
                {
                    "query_intent": intent,
                    "domains": annotation.domains,
                    "investment_relevance": annotation.investment_relevance,
                }
            )
            if reject:
                self.last_rejections.append(
                    {
                        "record_id": item.record.record_id,
                        "episode_id": item.record.episode_id,
                        "title": item.record.title,
                        "score_before_gate": item.score,
                        "domains": annotation.domains,
                        "investment_relevance": annotation.investment_relevance,
                        "reason": reason,
                    }
                )
                continue
            multiplier = domain_adjustment(intent, annotation, gate_cfg)
            item.score *= multiplier
            item.score_breakdown["domain_multiplier"] = multiplier
            item.selection_reason = f"selected: domain gate kept ({intent}; {annotation.investment_relevance}; {', '.join(annotation.domains)})"
            kept.append(item)
        kept.sort(key=lambda x: x.score, reverse=True)
        return kept


def rerank_with_llm(
    query: str,
    candidates: list[RetrievedRecord],
    provider_config: dict[str, Any] | None,
    min_score: float = 0.45,
) -> list[RetrievedRecord]:
    if not provider_config or not candidates:
        return candidates
    payload = [
        {
            "record_id": item.record.record_id,
            "episode_id": item.record.episode_id,
            "title": item.record.title,
            "summary": compact_record_text(item.record, max_chars=1200),
            "lexical_score": round(item.score_breakdown.get("lexical", 0), 4),
            "dense_score": round(item.score_breakdown.get("dense", 0), 4),
        }
        for item in candidates[:12]
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "You are a strict retrieval reranker for Traditional Chinese podcast semantic records. "
                "Judge relevance to the user question. Penalize records that only share keywords. "
                "Reward records that directly answer the requested concept, preserve market context, conditions, risks, exceptions, and add unique evidence. "
                "Return JSON only."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": query,
                    "scoring": "score each record from 0.0 to 1.0; 0.45 is minimum useful relevance",
                    "records": payload,
                    "return_schema": {"records": [{"record_id": "...", "relevance_score": 0.0, "reason": "short reason"}]},
                },
                ensure_ascii=False,
            ),
        },
    ]
    start = time.perf_counter()
    result = OpenAICompatibleClient(provider_config).chat(messages)
    latency = time.perf_counter() - start
    try:
        data = json.loads(result.content)
        scores = {str(x.get("record_id")): x for x in data.get("records", []) if isinstance(x, dict)}
    except Exception:
        return candidates
    reranked: list[RetrievedRecord] = []
    for item in candidates:
        entry = scores.get(item.record.record_id)
        if not entry:
            continue
        rerank_score = float(entry.get("relevance_score") or 0)
        item.score_breakdown.update({
            "rerank": rerank_score,
            "rerank_latency_seconds": latency,
            "rerank_cost": float((result.usage or {}).get("cost") or 0),
            "rerank_tokens": int((result.usage or {}).get("total_tokens") or 0),
        })
        item.rerank_reason = str(entry.get("reason") or "")
        item.score = rerank_score
        if rerank_score >= min_score:
            reranked.append(item)
        else:
            item.selection_reason = f"rejected: rerank below {min_score}"
    reranked.sort(key=lambda x: x.score, reverse=True)
    return reranked


def search_reasoning_records(
    query: str,
    records: list[ReasoningRecord],
    top_k: int = 3,
    min_score: float = 1.0,
    mode: str = "hybrid_dense",
    config: dict[str, Any] | None = None,
    provider_config: dict[str, Any] | None = None,
) -> list[RetrievedRecord]:
    return HybridRetriever(records, config=config).search(
        query,
        top_k=top_k,
        min_score=min_score,
        mode=mode,
        provider_config=provider_config,
    )


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


def normalize_score(score: float) -> float:
    return score / (score + 8.0) if score > 0 else 0.0


def canonical_embedding_text(record: ReasoningRecord) -> str:
    f = record.fields
    parts = [
        f"Title: {record.title}",
        _compact_list("Claims", f.get("claims")),
        _compact_list("Reasoning", f.get("reasoning_steps")),
        _compact_list("Conditions", f.get("conditions")),
        _compact_list("Risks", f.get("risks")),
        _compact_list("Exceptions", f.get("exceptions")),
        _compact_list("Uncertainty", f.get("uncertainty")),
        _compact_list("Decision tags", f.get("decisions_or_preferences")),
        _compact_list("Subject tags", f.get("tags")),
        _compact_list("Speaker behavior", f.get("speaker_behavior")),
    ]
    return "\n".join(part for part in parts if part.strip())


def compact_record_text(record: ReasoningRecord, max_chars: int = 1400) -> str:
    text = canonical_embedding_text(record)
    return text[:max_chars]


def _compact_list(label: str, value: Any) -> str:
    if isinstance(value, list):
        return f"{label}: " + " | ".join(str(x) for x in value[:5])
    if value:
        return f"{label}: {value}"
    return ""


def near_duplicate(a: ReasoningRecord, b: ReasoningRecord) -> bool:
    if a.record_id == b.record_id:
        return True
    a_terms = set(terms_for_text(canonical_embedding_text(a)))
    b_terms = set(terms_for_text(canonical_embedding_text(b)))
    if not a_terms or not b_terms:
        return False
    return len(a_terms & b_terms) / len(a_terms | b_terms) > 0.82
