#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from modules.agent import answer_question
from modules.common import ROOT, read_yaml, write_json
from modules.domain import annotate_record
from modules.knowledge import KnowledgeBase
from modules.retrieval import HybridRetriever


EXAMPLE_QUESTIONS = [
    "他通常怎麼看追高？",
    "他如何控制部位風險？",
    "他比較能接受踏空還是追高？",
    "哪些條件會讓他改變看法？",
    "這一集有哪些互相矛盾或有條件差異的觀點？",
    "面對不確定市場時，他會怎麼控制風險？",
    "他如何看待踏空？",
    "他在操作上重視哪些條件？",
    "這一集有哪些與投資心理相關的觀點？",
    "Apple 漲價這段他看重哪些判斷依據？",
    "科技巨頭和 AI 供應鏈之間的矛盾是什麼？",
    "他對職涯選擇有什麼看法？",
    "合夥創業他重視什麼風險？",
    "現在可以買 Apple 嗎？",
    "他有沒有談到房地產投資策略？",
]


CROSS_EPISODE_QUESTIONS = [
    "他在不同時期如何看待追高？",
    "面對市場不確定性時，他的做法是否一致？",
    "有哪些支持同一原則的不同案例？",
    "有沒有互相矛盾或有條件差異的觀點？",
    "他對風險與踏空的態度有沒有改變？",
    "哪些建議只適用於特定市場環境？",
    "他在台積電、AI、美股不同脈絡下的風險控管有什麼共通點？",
    "現在適合追高買科技股嗎？",
]


DIFFICULT_RETRIEVAL_QUESTIONS = [
    "他在操作上最重視哪些條件？",
    "他面對市場不確定性時有固定做法嗎？",
    "他對追高與踏空的取捨是什麼？",
    "哪些觀點只適用於特定市場環境？",
    "他有沒有前後不一致或有條件差異的看法？",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Legacy case-only Mentor RAG baseline over semantic reasoning records.")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="Ask one question.")
    ask.add_argument("question")
    ask.add_argument("--top-k", type=int)
    ask.add_argument("--mode", choices=["lexical", "hybrid_dense", "hybrid_rerank"])
    ask.add_argument("--debug", action="store_true")

    sub.add_parser("eval", help="Run a small fixed evaluation set.")
    sub.add_parser("eval-cross", help="Run a concise cross-episode evaluation set.")
    sub.add_parser("eval-retrieval", help="Compare lexical, dense, and reranked retrieval without final answer generation.")
    sub.add_parser("eval-domain-gate", help="Compare hybrid dense retrieval with and without the domain gate.")
    sub.add_parser("build-domain-index", help="Write deterministic Mentor-specific domain annotations.")

    args = parser.parse_args()
    provider = read_yaml(ROOT / "config" / "provider.yaml")
    data_cfg = read_yaml(ROOT / "config" / "data.yaml")
    retrieval_cfg = data_cfg.get("retrieval") or {}
    kb = KnowledgeBase()

    if args.command == "ask":
        result = answer_question(
            args.question,
            kb,
            provider,
            top_k=args.top_k or int(retrieval_cfg.get("top_k", 3)),
            min_score=float(retrieval_cfg.get("min_score", 1.0)),
            retrieval_mode=args.mode or retrieval_cfg.get("mode", "hybrid_dense"),
            retrieval_config=retrieval_cfg,
        )
        print_answer(result, debug=args.debug)
        return 0

    if args.command in {"eval", "eval-cross"}:
        questions = CROSS_EPISODE_QUESTIONS if args.command == "eval-cross" else EXAMPLE_QUESTIONS
        report_name = "mentor_cross_episode_eval_report.md" if args.command == "eval-cross" else "mentor_eval_report.md"
        json_name = "mentor_cross_episode_eval_results.json" if args.command == "eval-cross" else "mentor_eval_results.json"
        results = []
        for question in questions:
            result = answer_question(
                question,
                kb,
                provider,
                top_k=int(retrieval_cfg.get("top_k", 5)),
                min_score=float(retrieval_cfg.get("min_score", 1.0)),
                retrieval_mode=retrieval_cfg.get("mode", "hybrid_dense"),
                retrieval_config=retrieval_cfg,
            )
            results.append(result)
        write_json(ROOT / "reports" / json_name, results)
        write_eval_markdown(ROOT / "reports" / report_name, results)
        print(json.dumps({"questions": len(results), "report": f"reports/{report_name}"}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "eval-retrieval":
        questions = [*CROSS_EPISODE_QUESTIONS, *DIFFICULT_RETRIEVAL_QUESTIONS]
        results = compare_retrieval_modes(questions, kb, provider, retrieval_cfg)
        write_json(ROOT / "reports" / "retrieval_comparison_results.json", results)
        write_retrieval_comparison_markdown(ROOT / "reports" / "retrieval_comparison_report.md", results)
        print(json.dumps({"questions": len(questions), "report": "reports/retrieval_comparison_report.md"}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "eval-domain-gate":
        questions = [*CROSS_EPISODE_QUESTIONS, *DIFFICULT_RETRIEVAL_QUESTIONS]
        results = compare_domain_gate(questions, kb, retrieval_cfg)
        write_json(ROOT / "reports" / "domain_gate_comparison_results.json", results)
        write_domain_gate_markdown(ROOT / "reports" / "domain_gate_comparison_report.md", results)
        print(json.dumps({"questions": len(questions), "report": "reports/domain_gate_comparison_report.md"}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "build-domain-index":
        rows = [
            {
                "record_id": annotation.record_id,
                "domains": annotation.domains,
                "investment_relevance": annotation.investment_relevance,
                "rationale": annotation.rationale,
            }
            for annotation in [annotate_record(record) for record in kb.records]
        ]
        write_json(ROOT / "data" / "record_domain_annotations.json", {"records": rows})
        print(json.dumps({"records": len(rows), "path": "data/record_domain_annotations.json"}, ensure_ascii=False, indent=2))
        return 0

    return 1


def print_answer(result: dict, debug: bool = False) -> None:
    print("\nRetrieved records:")
    for item in result["retrieved_records"]:
        print(f"- {item['record_id']} ({item['score']:.2f}): {item['title']}")
    print("\nAnswer:\n")
    print(result["answer"])
    print("\nCitations:")
    for citation in result["citations"]:
        print(f"- {citation}")
    if debug:
        print("\nDebug:")
        print(json.dumps({k: v for k, v in result.items() if k not in {"answer"}}, ensure_ascii=False, indent=2))


def write_eval_markdown(path: Path, results: list[dict]) -> None:
    lines = ["# Legacy Case-Only Mentor RAG Evaluation", ""]
    for idx, result in enumerate(results, 1):
        lines.extend(
            [
                f"## {idx}. {result['question']}",
                "",
                "Retrieved records:",
                *[
                    f"- {item['record_id']}: {item['title']} (score {item['score']:.2f}; fields {', '.join(item.get('matched_fields', {})) or 'none'})"
                    for item in result["retrieved_records"]
                ],
                "",
                f"Evidence sufficient: `{result['review'].get('evidence_sufficient')}`",
                f"Citation valid: `{result['review'].get('citation_valid')}`",
                f"Episodes retrieved: `{', '.join(sorted({item.get('episode_id', '') for item in result['retrieved_records']}))}`",
                "",
                "Answer:",
                "",
                result["answer"],
                "",
                "Citations:",
                *[f"- {citation}" for citation in result["citations"]],
                "",
                "Automated review:",
                *[f"- {warning}" for warning in result["review"].get("warnings", [])],
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def compare_retrieval_modes(
    questions: list[str],
    kb: KnowledgeBase,
    provider: dict,
    retrieval_cfg: dict,
) -> list[dict]:
    rows = []
    for question in questions:
        row = {"question": question, "modes": {}}
        comparisons = [
            ("lexical", "lexical", retrieval_cfg),
            ("hybrid_dense_baseline", "hybrid_dense", retrieval_config_with_gate(retrieval_cfg, enabled=False)),
            ("hybrid_dense_domain_gate", "hybrid_dense", retrieval_config_with_gate(retrieval_cfg, enabled=True)),
            ("hybrid_rerank_domain_gate", "hybrid_rerank", retrieval_config_with_gate(retrieval_cfg, enabled=True)),
        ]
        for label, mode, cfg in comparisons:
            retriever = HybridRetriever(kb.records, config=cfg)
            records = retriever.search(
                question,
                top_k=int(retrieval_cfg.get("top_k", 5)),
                min_score=float(retrieval_cfg.get("min_score", 1.0)),
                mode=mode,
                provider_config=provider if mode == "hybrid_rerank" else None,
            )
            row["modes"][label] = {
                "records": [
                    {
                        "record_id": item.record.record_id,
                        "episode_id": item.record.episode_id,
                        "title": item.record.title,
                        "score": item.score,
                        "score_breakdown": item.score_breakdown,
                        "matched_fields": item.matched_fields,
                        "selection_reason": item.selection_reason,
                        "rerank_reason": item.rerank_reason,
                    }
                    for item in records
                ],
                "episodes": sorted({item.record.episode_id for item in records}),
                "obvious_false_positive_notes": obvious_false_positive_notes(question, records),
                "domain_rejections": retriever.last_rejections[:8],
            }
        rows.append(row)
    return rows


def retrieval_config_with_gate(config: dict, enabled: bool) -> dict:
    copied = dict(config)
    gate = dict((config.get("domain_gate") or {}))
    gate["enabled"] = enabled
    copied["domain_gate"] = gate
    return copied


def compare_domain_gate(questions: list[str], kb: KnowledgeBase, retrieval_cfg: dict) -> list[dict]:
    rows = []
    for question in questions:
        row = {"question": question, "modes": {}}
        for label, enabled in [("hybrid_dense_baseline", False), ("hybrid_dense_domain_gate", True)]:
            cfg = retrieval_config_with_gate(retrieval_cfg, enabled=enabled)
            retriever = HybridRetriever(kb.records, config=cfg)
            records = retriever.search(
                question,
                top_k=int(retrieval_cfg.get("top_k", 5)),
                min_score=float(retrieval_cfg.get("min_score", 1.0)),
                mode="hybrid_dense",
            )
            row["modes"][label] = {
                "records": [
                    {
                        "record_id": item.record.record_id,
                        "episode_id": item.record.episode_id,
                        "title": item.record.title,
                        "score": item.score,
                        "domains": item.score_breakdown.get("domains", []),
                        "investment_relevance": item.score_breakdown.get("investment_relevance"),
                        "selection_reason": item.selection_reason,
                    }
                    for item in records
                ],
                "episodes": sorted({item.record.episode_id for item in records}),
                "obvious_false_positive_notes": obvious_false_positive_notes(question, records),
                "domain_rejections": retriever.last_rejections[:12],
            }
        rows.append(row)
    return rows


def write_domain_gate_markdown(path: Path, results: list[dict]) -> None:
    baseline_fp = sum(len(r["modes"]["hybrid_dense_baseline"].get("obvious_false_positive_notes") or []) for r in results)
    gated_fp = sum(len(r["modes"]["hybrid_dense_domain_gate"].get("obvious_false_positive_notes") or []) for r in results)
    total_rejections = sum(len(r["modes"]["hybrid_dense_domain_gate"].get("domain_rejections") or []) for r in results)
    lines = [
        "# Domain Gate Retrieval Comparison",
        "",
        "Compares `hybrid_dense` before and after deterministic record-level domain gating.",
        "",
        f"- Baseline potential false-positive notes: {baseline_fp}",
        f"- Domain-gated potential false-positive notes: {gated_fp}",
        f"- Domain gate rejections recorded: {total_rejections}",
        "- API cost: $0; this evaluation is local retrieval only.",
        "",
    ]
    for idx, result in enumerate(results, 1):
        lines.extend([f"## {idx}. {result['question']}", ""])
        for mode, data in result["modes"].items():
            lines.extend([f"### {mode}", "", f"Episodes: `{', '.join(data['episodes']) or 'none'}`", ""])
            for rec in data["records"]:
                lines.append(
                    f"- `{rec['record_id']}` {rec['title']} "
                    f"(score {rec['score']:.3f}; domains {', '.join(rec.get('domains') or [])}; relevance {rec.get('investment_relevance')})"
                )
            if data["obvious_false_positive_notes"]:
                lines.extend(["", "Potential false positives:", *[f"- {note}" for note in data["obvious_false_positive_notes"]]])
            if data.get("domain_rejections"):
                lines.extend(["", "Domain gate rejections:"])
                for rejection in data["domain_rejections"][:6]:
                    lines.append(
                        f"- `{rejection['record_id']}` {rejection['title']} "
                        f"({', '.join(rejection['domains'])}; {rejection['investment_relevance']}): {rejection['reason']}"
                    )
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def obvious_false_positive_notes(question: str, records: list) -> list[str]:
    notes: list[str] = []
    investment_terms = ["追高", "踏空", "市場", "風險", "操作", "投資", "科技股", "台積電", "AI", "美股"]
    if any(term in question for term in investment_terms):
        weak_keywords = ["代購", "言論審查", "神經多樣性", "職涯", "合夥", "客服"]
        for item in records:
            text = f"{item.record.title}\n{item.record.searchable_text()}"
            if any(term in text for term in weak_keywords):
                notes.append(f"{item.record.record_id} may be lexically related but semantically weak for an investment query")
    return notes


def write_retrieval_comparison_markdown(path: Path, results: list[dict]) -> None:
    lines = [
        "# Retrieval Comparison",
        "",
        "Compares lexical baseline, hybrid dense baseline, hybrid dense with domain gate, and optional reranking over the current four-episode dataset.",
        "",
        "Dense retrieval uses local TF-IDF + LSA/SVD embeddings over compact reasoning-record text. Domain gate uses deterministic Mentor-specific domain annotations. Reranking uses the configured OpenRouter chat model on only the top candidates.",
        "",
    ]
    for idx, result in enumerate(results, 1):
        lines.extend([f"## {idx}. {result['question']}", ""])
        for mode, data in result["modes"].items():
            lines.extend([f"### {mode}", "", f"Episodes: `{', '.join(data['episodes']) or 'none'}`", ""])
            for rec in data["records"]:
                b = rec["score_breakdown"]
                lines.append(
                    f"- `{rec['record_id']}` {rec['title']} "
                    f"(score {rec['score']:.3f}; lexical {b.get('lexical', 0):.3f}; "
                    f"dense {b.get('dense', 0):.3f}; metadata {b.get('metadata', 0):.3f}; "
                    f"rerank {b.get('rerank', 0):.3f})"
                )
                if rec.get("rerank_reason"):
                    lines.append(f"  - rerank: {rec['rerank_reason']}")
                if rec.get("selection_reason"):
                    lines.append(f"  - selection: {rec['selection_reason']}")
            if data["obvious_false_positive_notes"]:
                lines.extend(["", "Potential false positives:", *[f"- {note}" for note in data["obvious_false_positive_notes"]]])
            if data.get("domain_rejections"):
                lines.extend(["", "Domain gate rejections:"])
                for rejection in data["domain_rejections"]:
                    lines.append(
                        f"- `{rejection['record_id']}` {rejection['title']} "
                        f"({', '.join(rejection['domains'])}; {rejection['investment_relevance']}): {rejection['reason']}"
                    )
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
