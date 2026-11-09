"""Bounded private profile extraction; no acceptance, publication or serving."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import load_config, run
from .schemas import schema


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "validate", "extract", "schema"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--through", choices=("evidence", "synthesis"), default="synthesis")
    args = parser.parse_args(argv)
    if args.command != "schema" and args.config is None:
        parser.error("--config is required")
    try:
        result = schema(args.through) if args.command == "schema" else run(
            load_config(args.config), through=args.through, dry_run=args.command == "plan",
            validate_only=args.command == "validate")
    except (ValueError, OSError, KeyError, RuntimeError, TypeError) as exc:
        # Never echo source text, provider bodies, paths or credentials into a service log.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "note": "Inspect private config and immutable profile call records."}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
