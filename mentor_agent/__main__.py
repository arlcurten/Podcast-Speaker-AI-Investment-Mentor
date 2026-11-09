from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from transcription.common import atomic_write_json, private_path, load_project_dotenv
from .runtime import Mentor, load_config


def main(argv=None):
    parser = argparse.ArgumentParser(description="Reviewed private knowledge + local retrieval + OpenRouter Mentor")
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "config/default.yaml")
    parser.add_argument("--no-llm", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ask", "retrieve"):
        p = sub.add_parser(name)
        p.add_argument("--query", required=True)
        p.add_argument("--json", action="store_true")
        p.add_argument("--output", type=Path, help="Optional immutable full diagnostics, private path only")
    sub.add_parser("validate-data")
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.command == "serve":
        if args.no_llm:
            parser.error("serve is a live API command; use --no-llm retrieve for diagnostics")
        if not 1 <= args.port <= 65535:
            parser.error("serve --port must be between 1 and 65535")
    load_project_dotenv({"PODCAST_DATA_ROOT", "PIPELINE_DATA_ROOT", "PRIVATE_DOCS_ROOT", "MENTOR_RELEASE", "MENTOR_MODEL_ID", "OPENROUTER_API_KEY"})
    try:
        cfg = load_config(args.config)
        mentor = Mentor(cfg)
        if args.command == "validate-data":
            print(json.dumps({"valid": True, "cases": len(mentor.index.cases), "principles": len(mentor.index.principles), "model_calls": 0}))
            return 0
        if args.command == "serve":
            from .service import create_app
            import uvicorn
            from .model import OpenRouter
            mentor.backend = OpenRouter(cfg)  # Fail clearly on missing local credential before binding.
            uvicorn.run(create_app(mentor), host="127.0.0.1", port=args.port, access_log=False)
            return 0
        result = mentor.ask(args.query, no_llm=args.no_llm or args.command == "retrieve")
        if args.output:
            atomic_write_json(private_path(args.output), result, overwrite=False)
        if args.json or args.command == "retrieve":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Route:", result["route"])
            answer = result["answer"]
            for key in ("conditional_answer", "interpretation", "conditions_and_risks", "conflicts_and_exceptions", "missing_information", "current_data_limitation"):
                value = answer[key]
                if value:
                    print(key + ":", " / ".join(value) if isinstance(value, list) else value)
            for cite in result["citations"]:
                print(cite["label"])
            for review in result["evidence_review_limits"]:
                print("Source review limits (" + review["record_id"] + "):", " / ".join(review["limitations"]))
        return 0
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        print(f"Mentor setup/operation failed ({type(exc).__name__}); set MENTOR_RELEASE to a reviewed private knowledge_release.v1 or knowledge_case_release.v1, verify source reviews, and configure OPENROUTER_API_KEY / MENTOR_MODEL_ID for live calls.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
