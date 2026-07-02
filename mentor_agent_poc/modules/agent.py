from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from modules.common import ROOT, OpenAICompatibleClient, read_text
from modules.knowledge import KnowledgeBase, ReasoningRecord
from modules.retrieval import RetrievedRecord, search_reasoning_records


def answer_question(
    question: str,
    kb: KnowledgeBase,
    provider_config: dict[str, Any],
    top_k: int = 3,
    min_score: float = 1.0,
) -> dict[str, Any]:
    current_market_required = needs_current_market_data(question)
    retrieved = search_reasoning_records(question, kb.records, top_k=top_k, min_score=min_score)
    if not retrieved:
        return {
            "question": question,
            "retrieved_records": [],
            "answer": "目前 EP674 semantic knowledge 中沒有找到足夠相關的證據。這個 MVP 不會用一般模型知識補答案，也不能查詢即時市場資訊。",
            "citations": [],
            "review": {
                "evidence_sufficient": False,
                "warnings": ["no retrieved evidence"],
                "current_market_data_required": current_market_required,
            },
        }

    context = build_context(retrieved)
    system = read_text(ROOT / "prompts" / "mentor_system.md")
    user = f"""User question:
{question}

Retrieved podcast semantic records:
{context}

    Current market data required: {current_market_required}

    Answer in Traditional Chinese. Keep it concise. Cite evidence exactly using the provided citation labels.
    If current market data is required, begin by saying this MVP cannot decide current buy/sell suitability and can only provide a historical reasoning framework from retrieved podcast evidence.
"""
    result = OpenAICompatibleClient(provider_config).chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}]
    )
    citations = citation_labels(retrieved)
    return {
        "question": question,
        "retrieved_records": [
            {
                "record_id": item.record.record_id,
                "episode_id": item.record.episode_id,
                "title": item.record.title,
                "score": item.score,
                "matched_terms": item.matched_terms,
                "matched_fields": item.matched_fields,
                "score_breakdown": item.score_breakdown,
            }
            for item in retrieved
        ],
        "answer": enforce_current_market_guard(result.content, current_market_required),
        "citations": citations,
        "usage": result.usage,
        "latency_seconds": result.latency_seconds,
        "retries": result.retries,
        "review": automated_review(
            enforce_current_market_guard(result.content, current_market_required),
            retrieved,
            citations,
            current_market_required,
        ),
    }


def build_context(retrieved: list[RetrievedRecord]) -> str:
    chunks = []
    seen: set[str] = set()
    for item in retrieved:
        record = item.record
        if record.record_id in seen:
            continue
        seen.add(record.record_id)
        f = record.fields
        chunks.append(
            "\n".join(
                [
                    f"Record: {record.record_id}",
                    f"Episode: {record.episode_id}",
                    f"Title: {record.title}",
                    f"Score: {item.score:.2f}",
                    f"Matched fields: {', '.join(item.matched_fields) if item.matched_fields else 'none'}",
                    f"Citations: {'; '.join(citation.label() for citation in record.citations()[:4])}",
                    _list("Claims", f.get("claims")),
                    _list("Reasoning", f.get("reasoning_steps")),
                    _list("Conditions", f.get("conditions")),
                    _list("Risks", f.get("risks")),
                    _list("Exceptions", f.get("exceptions")),
                    _list("Uncertainty", f.get("uncertainty")),
                    _list("Decisions/preferences", f.get("decisions_or_preferences")),
                    _list("Speaker behavior", f.get("speaker_behavior")),
                    _evidence_quotes(record),
                ]
            )
        )
    return "\n\n---\n\n".join(chunks)


def citation_labels(retrieved: list[RetrievedRecord]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for item in retrieved:
        for citation in item.record.citations()[:4]:
            label = citation.label()
            if label not in seen:
                seen.add(label)
                labels.append(label)
    return labels


def automated_review(
    answer: str,
    retrieved: list[RetrievedRecord],
    citations: list[str],
    current_market_required: bool = False,
) -> dict[str, Any]:
    warnings: list[str] = []
    if len(answer) > 2500:
        warnings.append("answer is longer than expected for MVP demo")
    if not any(item.record.record_id in answer for item in retrieved):
        warnings.append("answer may not cite reasoning record IDs")
    if not any(item.record.episode_id in answer for item in retrieved):
        warnings.append("answer may not cite episode ID")
    current_terms = ["現在股價", "目前價格", "最新", "現在市場", "目前股價", "現價"]
    if any(term in answer for term in current_terms) and "不能" not in answer and "無法" not in answer:
        warnings.append("answer may imply current market knowledge without tool support")
    if current_market_required and not any(term in answer for term in ["不能", "無法", "沒有即時", "不具備即時"]):
        warnings.append("current-market query does not clearly state limitation")
    retrieved_record_ids = {item.record.record_id for item in retrieved}
    cited_record_ids = {_record_id_from_citation(citation) for citation in citations}
    missing = sorted(x for x in cited_record_ids if x and x not in retrieved_record_ids)
    if missing:
        warnings.append(f"citations reference non-retrieved records: {missing}")
    answer_record_ids = set(re.findall(r"EP\d+-reasoning-\d+", answer))
    unsupported_answer_ids = sorted(answer_record_ids - retrieved_record_ids)
    if unsupported_answer_ids:
        warnings.append(f"answer cites non-retrieved records: {unsupported_answer_ids}")
    if retrieved and retrieved[0].score < 2.0:
        warnings.append("top retrieval score is weak; answer should state evidence may be limited")
    return {
        "evidence_sufficient": bool(retrieved),
        "citation_valid": not missing,
        "current_market_data_required": current_market_required,
        "warnings": warnings or ["no major automated review warnings"],
    }


def _record_id_from_citation(citation: str) -> str | None:
    match = re.search(r"record (EP\d+-reasoning-\d+)", citation)
    return match.group(1) if match else None


def needs_current_market_data(question: str) -> bool:
    current_terms = ["現在", "今天", "目前", "最新", "現階段"]
    action_terms = ["買", "賣", "加碼", "減碼", "追", "進場", "出場", "適合", "可以"]
    return any(term in question for term in current_terms) and any(term in question for term in action_terms)


def enforce_current_market_guard(answer: str, current_market_required: bool) -> str:
    if not current_market_required:
        return answer
    if any(term in answer for term in ["不能判斷現在", "無法判斷現在", "沒有即時", "不具備即時"]):
        return answer
    prefix = (
        "先說限制：這個 MVP 只使用 EP674 的歷史 podcast semantic knowledge，"
        "沒有即時股價、財報、新聞或市場資料，所以不能判斷現在是否適合買賣；"
        "下面只能整理歷史推理框架。\n\n"
    )
    return prefix + answer


def _list(label: str, value: Any, limit: int = 4) -> str:
    if not isinstance(value, list) or not value:
        return f"{label}: []"
    return f"{label}: " + " | ".join(str(x) for x in value[:limit])


def _evidence_quotes(record: ReasoningRecord) -> str:
    quotes = []
    for ev in record.fields.get("source_evidence", [])[:3]:
        if isinstance(ev, dict) and ev.get("quote"):
            quotes.append(str(ev["quote"]))
    return "Evidence quotes: " + " | ".join(quotes)
