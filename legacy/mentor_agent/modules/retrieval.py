from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .query import QueryAnalysis
from .stores import Principle, ReasoningRecord


@dataclass
class RetrievalResult:
    item_id: str
    episode_id: str | None
    title: str
    item: Any
    score: float
    matched_terms: list[str]
    matched_fields: dict[str, list[str]] = field(default_factory=dict)
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    relationship: str = "candidate"
    selection_reason: str = "selected"
    evidence_role_candidate: str = "neutral"
    role_reason: str = ""
    conflict_signal_score: float = 0.0
    exception_signal_score: float = 0.0


class Searchable(Protocol):
    def searchable_text(self) -> str: ...


FIELD_WEIGHTS = {
    "title": 3.0,
    "tags": 2.5,
    "claims": 2.2,
    "reasoning_steps": 2.0,
    "conditions": 2.3,
    "risks": 2.2,
    "exceptions": 2.2,
    "uncertainty": 2.0,
    "decisions_or_preferences": 2.0,
    "speaker_behavior": 1.8,
}


class HybridSearch:
    def __init__(self, items: list[Any], *, item_id, title, text_fields, config: dict[str, Any] | None = None):
        self.items = items
        self.item_id = item_id
        self.title = title
        self.text_fields = text_fields
        self.config = config or {}
        self.doc_terms: dict[str, Counter[str]] = {}
        self.field_terms: dict[str, dict[str, set[str]]] = {}
        self.doc_freq: Counter[str] = Counter()
        texts = []
        self.ids = []
        for item in items:
            iid = item_id(item)
            self.ids.append(iid)
            fields = text_fields(item)
            terms: list[str] = []
            fmap: dict[str, set[str]] = {}
            for name, value in fields.items():
                field_terms = terms_for_text(str(value or ""))
                fmap[name] = set(field_terms)
                terms.extend(field_terms)
            counts = Counter(terms)
            self.doc_terms[iid] = counts
            self.field_terms[iid] = fmap
            for term in counts:
                self.doc_freq[term] += 1
            texts.append("\n".join(str(v) for v in fields.values()))
        self.avg_doc_len = sum(sum(c.values()) for c in self.doc_terms.values()) / max(len(self.doc_terms), 1)
        self.embedder = LocalLSA(texts, self.ids)

    def search(self, analysis: QueryAnalysis, *, top_k: int, min_score: float = 0.0) -> list[RetrievalResult]:
        query = " ".join([analysis.query, *analysis.candidate_search_terms])
        qterms = terms_for_text(query)
        dense = self.embedder.score(query)
        results: list[RetrievalResult] = []
        for item in self.items:
            iid = self.item_id(item)
            lexical, matched_fields = self._lexical_score(iid, qterms)
            dense_score = dense.get(iid, 0.0)
            metadata = sum(len(v) for k, v in matched_fields.items() if k in {"tags", "conditions", "risks", "exceptions", "domains"})
            final = normalize_score(lexical) * 5.0 + dense_score * 3.5 + normalize_score(metadata) * 1.5
            if final < min_score:
                continue
            results.append(
                RetrievalResult(
                    item_id=iid,
                    episode_id=getattr(item, "episode_id", None),
                    title=self.title(item),
                    item=item,
                    score=final,
                    matched_terms=sorted(set(qterms) & set(self.doc_terms[iid]))[:16],
                    matched_fields=matched_fields,
                    score_breakdown={"lexical": lexical, "dense": dense_score, "metadata": metadata, "final": final},
                )
            )
        results.sort(key=lambda x: x.score, reverse=True)
        return diversify(results, top_k)

    def _lexical_score(self, iid: str, qterms: list[str]) -> tuple[float, dict[str, list[str]]]:
        counts = self.doc_terms[iid]
        doc_len = sum(counts.values())
        n_docs = max(len(self.items), 1)
        bm25 = 0.0
        for term in qterms:
            tf = counts.get(term, 0)
            if not tf:
                continue
            df = self.doc_freq.get(term, 0)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            denom = tf + 1.4 * (1 - 0.75 + 0.75 * doc_len / max(self.avg_doc_len, 1))
            bm25 += idf * (tf * 2.4) / denom
        fields: dict[str, list[str]] = {}
        field_score = 0.0
        for fname, fterms in self.field_terms[iid].items():
            matched = sorted(set(qterms) & fterms)
            if matched:
                fields[fname] = matched[:10]
                field_score += FIELD_WEIGHTS.get(fname, 1.0) * len(matched)
        return bm25 + field_score / math.sqrt(max(len(set(qterms)), 1)), fields


class LocalLSA:
    def __init__(self, texts: list[str], ids: list[str]):
        self.ids = ids
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=1, sublinear_tf=True)
        matrix = self.vectorizer.fit_transform(texts or ["empty"])
        n_components = min(32, matrix.shape[0] - 1, matrix.shape[1] - 1)
        if matrix.shape[0] >= 3 and n_components >= 2:
            self.svd: TruncatedSVD | None = TruncatedSVD(n_components=n_components, random_state=13)
            vectors = self.svd.fit_transform(matrix)
        else:
            self.svd = None
            vectors = matrix.toarray()
        self.vectors = normalize(vectors)

    def score(self, query: str) -> dict[str, float]:
        q = self.vectorizer.transform([query])
        arr = self.svd.transform(q) if self.svd else q.toarray()
        sims = cosine_similarity(normalize(arr), self.vectors)[0]
        return {iid: float(score) for iid, score in zip(self.ids, sims)}


def make_case_search(records: list[ReasoningRecord], config: dict[str, Any]) -> HybridSearch:
    return HybridSearch(
        records,
        item_id=lambda r: r.record_id,
        title=lambda r: r.title,
        text_fields=lambda r: r.field_texts(),
        config=config,
    )


def make_principle_search(principles: list[Principle], config: dict[str, Any]) -> HybridSearch:
    return HybridSearch(
        principles,
        item_id=lambda p: p.principle_id,
        title=lambda p: p.name,
        text_fields=lambda p: {
            "title": p.name,
            "domains": " ".join(p.fields.get("domains", [])),
            "decision_context": p.fields.get("decision_context"),
            "core_preference": p.fields.get("core_preference"),
            "reasoning_steps": " ".join(p.fields.get("reasoning_steps", [])),
            "conditions": " ".join(p.fields.get("conditions", [])),
            "exceptions": " ".join(p.fields.get("exceptions", [])),
            "distinctive_behavior": " ".join(p.fields.get("distinctive_behavior", [])),
        },
        config=config,
    )


def annotate_case_roles(
    analysis: QueryAnalysis,
    cases: list[RetrievalResult],
    *,
    principle_supporting_records: set[str] | None = None,
) -> list[RetrievalResult]:
    supporting = principle_supporting_records or set()
    conflict_query = any(term in analysis.query for term in ["矛盾", "衝突", "不同情境", "取捨", "trade-off", "conflict"])
    if "還是" in analysis.query and any(term in analysis.query for term in ["加碼", "避開", "拒絕", "參與", "追高", "忍住"]):
        conflict_query = True
    exception_query = any(term in analysis.query for term in ["例外", "不適用", "不能直接套用", "什麼情況", "除非", "override"])
    for case in cases:
        fields = getattr(case.item, "fields", {}) or {}
        has_conditions = bool(fields.get("conditions") or fields.get("view_change_conditions"))
        has_exceptions = bool(fields.get("exceptions") or fields.get("view_change_conditions"))
        has_contradictions = bool(fields.get("contradictions"))
        has_decision = bool(fields.get("decisions_or_preferences") or fields.get("actions_or_avoided_actions"))
        conflict_score = 0.0
        exception_score = 0.0
        reasons: list[str] = []
        if has_contradictions:
            conflict_score += 1.0
            reasons.append("record has contradiction metadata")
        if conflict_query and has_decision and has_conditions:
            conflict_score += 0.45
            reasons.append("query asks about conflicting/conditional decisions")
        if case.item_id in supporting:
            reasons.append("linked supporting case for retrieved principle")
        if has_exceptions:
            exception_score += 0.7
            reasons.append("record has exception or switch-condition metadata")
        if exception_query and has_exceptions:
            exception_score += 0.4
            reasons.append("query asks about exception/applicability")

        if has_contradictions or conflict_score >= 0.45:
            role = "potential_conflict"
        elif exception_score >= 0.8:
            role = "exception"
        elif conflict_query and has_conditions:
            role = "context_difference"
        elif case.item_id in supporting:
            role = "supporting"
        else:
            role = "neutral"
        case.evidence_role_candidate = role
        case.role_reason = "; ".join(reasons) or "selected by relevance"
        case.conflict_signal_score = round(conflict_score, 3)
        case.exception_signal_score = round(exception_score, 3)
        case.score_breakdown = {
            **case.score_breakdown,
            "conflict_signal": case.conflict_signal_score,
            "exception_signal": case.exception_signal_score,
        }
    return cases


def diversify(items: list[RetrievalResult], top_k: int) -> list[RetrievalResult]:
    selected: list[RetrievalResult] = []
    episode_counts: Counter[str] = Counter()
    seen_terms: set[str] = set()
    for item in items:
        if item.episode_id and episode_counts[item.episode_id] >= 3:
            item.selection_reason = "rejected: episode cap"
            continue
        terms = set(terms_for_text(item.title))
        if selected and terms and len(terms & seen_terms) / max(len(terms), 1) > 0.9:
            item.selection_reason = "rejected: near duplicate"
            continue
        selected.append(item)
        seen_terms |= terms
        if item.episode_id:
            episode_counts[item.episode_id] += 1
        if len(selected) >= top_k:
            break
    return selected


def terms_for_text(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-zA-Z0-9_][a-zA-Z0-9_+-]{1,}", lowered)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = [a + b for a, b in zip(cjk, cjk[1:])]
    trigrams = [a + b + c for a, b, c in zip(cjk, cjk[1:], cjk[2:])]
    return [*latin, *bigrams, *trigrams]


def normalize_score(score: float) -> float:
    return score / (score + 8.0) if score > 0 else 0.0


def normalize(values: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return values / norms
