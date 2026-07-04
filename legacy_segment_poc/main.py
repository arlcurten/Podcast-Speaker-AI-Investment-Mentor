#!/usr/bin/env python3
from __future__ import annotations

import argparse
import runpy
import sys


COMMANDS = {
    # Inspect local OS, CPU, RAM, GPU, CUDA, ffmpeg, and ffprobe availability.
    "inspect-env": "modules.source.inspect_environment",

    # Fetch the podcast RSS feed and build episode manifests.
    "fetch-rss": "modules.source.fetch_rss",

    #!! Download one explicitly selected episode audio file from the manifest.
    "download": "modules.source.download_episode",

    #!! Run faster-whisper transcription for an episode or partial clip.
    "transcribe": "modules.transcript.transcribe_episode",

    #!! Merge adjacent raw ASR segments and automatically validate source mapping.
    "merge": "modules.transcript.merge_transcript_segments",

    #!! Build Traditional Chinese normalized transcript layers from raw and merged text.
    "normalize": "modules.transcript.normalize_transcript",

    # Build the minimal deterministic topic/classification/routing POC output.
    "topic-review-poc": "modules.segmentation.build_topic_segments",

    # Build the historical Local POC report from existing artifacts.
    "poc-report": "modules.build_reports.build_poc_report",
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
