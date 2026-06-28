#!/usr/bin/env python3
from __future__ import annotations

import argparse
import runpy
import sys


COMMANDS = {
    "inspect-env": "modules.source.inspect_environment",
    "fetch-rss": "modules.source.fetch_rss",
    "download": "modules.source.download_episode",
    "inspect-audio": "modules.source.inspect_audio",
    "transcribe": "modules.transcript.transcribe_episode",
    "audit": "modules.transcript.audit_transcript_segments",
    "merge": "modules.transcript.merge_transcript_segments",
    "validate-merge": "modules.transcript.validate_merged_transcript",
    "normalize": "modules.transcript.normalize_transcript",
    "compare": "modules.transcript.compare_transcripts",
    "topic-review-poc": "modules.review.build_topic_segments",
    "benchmark-clip": "modules.benchmark.run_benchmark_clip",
    "benchmark-report": "modules.benchmark.build_clean_gpu_benchmark_report",
    "poc-report": "modules.reports.build_poc_report",
    "repair-metadata": "modules.maintenance.repair_existing_metadata",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Local POC modular entrypoint.")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args, rest = parser.parse_known_args()
    sys.argv = [args.command, *rest]
    runpy.run_module(COMMANDS[args.command], run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
