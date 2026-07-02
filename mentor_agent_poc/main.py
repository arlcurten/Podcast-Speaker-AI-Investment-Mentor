#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from modules.agent import answer_question
from modules.common import ROOT, read_yaml, write_json
from modules.knowledge import KnowledgeBase


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Mentor Agent POC over semantic reasoning records.")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="Ask one question.")
    ask.add_argument("question")
    ask.add_argument("--top-k", type=int)
    ask.add_argument("--debug", action="store_true")

    sub.add_parser("eval", help="Run a small fixed evaluation set.")

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
        )
        print_answer(result, debug=args.debug)
        return 0

    if args.command == "eval":
        results = []
        for question in EXAMPLE_QUESTIONS:
            result = answer_question(
                question,
                kb,
                provider,
                top_k=int(retrieval_cfg.get("top_k", 3)),
                min_score=float(retrieval_cfg.get("min_score", 1.0)),
            )
            results.append(result)
        write_json(ROOT / "reports" / "mentor_eval_results.json", results)
        write_eval_markdown(ROOT / "reports" / "mentor_eval_report.md", results)
        print(json.dumps({"questions": len(results), "report": "reports/mentor_eval_report.md"}, ensure_ascii=False, indent=2))
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
    lines = ["# Mentor Agent POC Evaluation", ""]
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


if __name__ == "__main__":
    raise SystemExit(main())
