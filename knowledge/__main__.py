from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from transcription.common import load_project_dotenv
from .pipeline import load_config, run, STAGES


def main(argv=None):
    parser = argparse.ArgumentParser(description="Versioned source-grounded offline extraction")
    parser.add_argument("command", choices=["plan", "run", "validate", "schema", "publish", "extend"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--through", choices=STAGES, default="level4")
    parser.add_argument("--sources", type=Path, help="Private list of newly accepted episode source paths")
    parser.add_argument("--run-id", help="Unused update run ID")
    parser.add_argument("--replace-existing", action="store_true", help="Explicitly replace listed episodes with new accepted source versions; keep old files/releases")
    args = parser.parse_args(argv)
    load_project_dotenv({"PODCAST_DATA_ROOT", "PIPELINE_DATA_ROOT", "PRIVATE_DOCS_ROOT", "KNOWLEDGE_MODEL_ID"})
    try:
        if args.command == "schema":
            from .schemas import schema
            result = schema(args.through)
        else:
            if not args.config:
                parser.error("--config is required")
            cfg = load_config(args.config)
            if args.command == "extend":
                from .pipeline import extend
                if not args.sources or not args.run_id or not args.output:
                    parser.error("extend requires --sources, --run-id and --output")
                result = extend(cfg, args.sources, args.run_id, args.output, replace_existing=args.replace_existing)
            elif args.command == "publish":
                from .release import publish
                if not args.review or not args.output:
                    parser.error("publish requires --review and --output")
                result = publish(cfg, args.review, args.output)
            else:
                result = run(cfg, through=args.through, dry_run=args.command == "plan", validate_only=args.command == "validate")
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Knowledge operation failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
