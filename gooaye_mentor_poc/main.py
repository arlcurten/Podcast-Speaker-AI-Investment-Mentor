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

    # Download one explicitly selected episode audio file from the manifest.
    "download": "modules.source.download_episode",

    # Validate downloaded audio with metadata, file size, SHA-256, and ffprobe.
    "inspect-audio": "modules.source.inspect_audio",

    # Run faster-whisper transcription for an episode or partial clip.
    "transcribe": "modules.transcript.transcribe_episode",

    # Analyze raw transcript segment duration, ordering, overlap, and fragmentation.
    "audit": "modules.transcript.audit_transcript_segments",

    # Merge adjacent raw ASR segments into deterministic readable utterances.
    "merge": "modules.transcript.merge_transcript_segments",

    # Verify merged utterances exactly map back to raw source segment IDs and text.
    "validate-merge": "modules.transcript.validate_merged_transcript",

    # Build Traditional Chinese normalized transcript layers from raw and merged text.
    "normalize": "modules.transcript.normalize_transcript",

    # Compare transcript outputs and produce lightweight evaluation tables.
    "compare": "modules.transcript.compare_transcripts",

    # Build the minimal deterministic topic/classification/routing POC output.
    "topic-review-poc": "modules.review.build_topic_segments",

    # Run a fixed-clip local GPU benchmark without touching full transcripts.
    "benchmark-clip": "modules.benchmark.run_benchmark_clip",

    # Summarize clean GPU benchmark artifacts into a report and JSON summary.
    "benchmark-report": "modules.benchmark.build_clean_gpu_benchmark_report",

    # Build the historical Local POC report from existing artifacts.
    "poc-report": "modules.reports.build_poc_report",
    
    # Repair old generated metadata paths after repository moves.
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
