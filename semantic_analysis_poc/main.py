#!/usr/bin/env python3
from __future__ import annotations

import argparse
import runpy
import sys


COMMANDS = {
    "extract-level3": "modules.principles",
    "extract-level4": "modules.principles",
    "preflight-auth": "modules.preflight_auth",
    "pilot": "modules.run_pilot",
    "report-principles": "modules.principles",
    "run-episode": "modules.run_episode",
    "validate-principles": "modules.principles",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Semantic Analysis POC entrypoint.")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args, rest = parser.parse_known_args()
    sys.argv = [args.command, *rest]
    runpy.run_module(COMMANDS[args.command], run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
