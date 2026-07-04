#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .modules.common import ROOT, read_yaml
from .modules.runtime import MentorRuntime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Investment Mentor runtime.")
    parser.add_argument("--config", default=str(ROOT / "config" / "agent.yaml"))
    parser.add_argument("--no-llm", action="store_true", help="Use deterministic fallback path without API calls.")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="Ask the Mentor Agent.")
    ask.add_argument("--query", required=True)
    ask.add_argument("--json", action="store_true")

    inspect = sub.add_parser("inspect-query", help="Show query/scenario analysis.")
    inspect.add_argument("--query", required=True)

    retrieve = sub.add_parser("retrieve", help="Show principle and case retrieval.")
    retrieve.add_argument("--query", required=True)
    retrieve.add_argument("--json", action="store_true")

    compare = sub.add_parser("compare", help="Run a lightweight legacy/new comparison scaffold.")
    compare.add_argument("--query-file", default=str(ROOT / "config" / "comparison_questions.yaml"))
    compare.add_argument("--json", action="store_true")

    sub.add_parser("validate-data", help="Validate private Level 2 and Level 4 artifacts.")

    args = parser.parse_args(argv)
    runtime = MentorRuntime(Path(args.config), use_llm=not args.no_llm)

    if args.command == "validate-data":
        print(json.dumps(runtime.validate_data(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "inspect-query":
        print(json.dumps(runtime.inspect_query(args.query), ensure_ascii=False, indent=2))
        return 0
    if args.command == "retrieve":
        result = runtime.retrieve(args.query)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else human_retrieval(result))
        return 0
    if args.command == "ask":
        result = runtime.ask(args.query)
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else human_answer(result))
        return 0
    if args.command == "compare":
        result = run_comparison(runtime, Path(args.query_file))
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else human_comparison(result))
        return 0
    return 1


def run_comparison(runtime: MentorRuntime, query_file: Path) -> dict:
    data = read_yaml(query_file)
    rows = []
    for item in data.get("questions", []):
        query = str(item["query"])
        result = runtime.ask(query)
        rows.append(
            {
                "id": item.get("id"),
                "query": query,
                "legacy_answer": "Not executed here; legacy_mentor_rag_poc remains the deterministic baseline.",
                "new_answer": result["answer"],
                "new_selected_route": result["selected_route"],
                "principle_used_or_rejected": {
                    "selected": result.get("selected_principles", []),
                    "rejected": result.get("rejected_principles", []),
                },
                "missing_information": result["applicability_assessment"].get("missing_information", []),
                "exceptions_retained": result["applicability_assessment"].get("exceptions_triggered", []),
                "conflicts_retained": result["applicability_assessment"].get("conflicting_evidence", []),
                "citation_validity": result["guards"]["checks"]["citations"],
                "current_data_limitation": result["guards"]["checks"]["current_market"],
            }
        )
    return {"question_count": len(rows), "rows": rows}


def human_answer(result: dict) -> str:
    warnings = result.get("guards", {}).get("warnings", [])
    lines = [
        f"Route: {result['selected_route']}",
        "",
        result["answer"],
        "",
        "Citations:",
        *[f"- {c}" for c in result.get("citations", [])[:8]],
        "",
        "Guard warnings:",
        *(f"- {w}" for w in warnings),
    ]
    if not warnings:
        lines.append("- none")
    return "\n".join(lines)


def human_retrieval(result: dict) -> str:
    lines = ["Principle candidates:"]
    lines += [f"- {x['id']} score={x['score']:.2f} {x['title']}" for x in result.get("principle_candidates", [])]
    lines.append("Case candidates:")
    lines += [f"- {x['id']} score={x['score']:.2f} {x['title']}" for x in result.get("case_candidates", [])]
    return "\n".join(lines)


def human_comparison(result: dict) -> str:
    lines = [f"Compared {result['question_count']} queries"]
    for row in result["rows"]:
        lines.append(f"- {row['id']}: {row['new_selected_route']} | {row['query']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

