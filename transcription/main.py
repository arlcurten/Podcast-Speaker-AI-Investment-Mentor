from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import load_project_dotenv, private_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Private-data transcription and pre-extraction quality gate.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check CUDA libraries/dependencies without downloading or loading a model")
    sub = commands.add_parser("cache-model", help="Explicit managed model download; default dry-run, no inference")
    sub.add_argument("--config", required=True, type=Path)
    sub.add_argument("--execute", action="store_true")
    sub = commands.add_parser("batch-export", help="Checksum-bound portable output bundle; default dry-run")
    sub.add_argument("--config", required=True, type=Path)
    sub.add_argument("--output", required=True, type=Path)
    sub.add_argument("--execute", action="store_true")
    sub.add_argument("--dry-run", action="store_true")
    sub = commands.add_parser("batch-import", help="Verify and import a downloaded bundle; never overwrite canonical raw data")
    sub.add_argument("--source", required=True, type=Path)
    sub.add_argument("--dest-root", required=True, type=Path)
    sub.add_argument("--storage-config", type=Path, help="Private batch YAML containing local storage safety settings")
    sub.add_argument("--execute", action="store_true")
    sub.add_argument("--dry-run", action="store_true")
    sub = commands.add_parser("catalog-fetch", help="Fetch metadata only into a new private catalog snapshot")
    sub.add_argument("--rss-url", required=True)
    sub.add_argument("--quarantine-ambiguous", action="store_true", help="Exclude ambiguous items from runnable episodes but retain their metadata for review")
    sub.add_argument("--output", required=True, type=Path)
    for name in ("batch-plan", "batch-run", "batch-status", "batch-resume", "batch-validate"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", required=True, type=Path)
        sub.add_argument("--episode", action="append")
        sub.add_argument("--episode-file", type=Path)
        sub.add_argument("--max-episodes", type=int)
        sub.add_argument("--dry-run", action="store_true")
        sub.add_argument("--retry-failed", action="store_true")
        sub.add_argument("--device", choices=["cpu", "cuda"])
        for flag in ("output-root", "audio-root", "transcript-root", "compute-type"):
            sub.add_argument(f"--{flag}")
    sub = commands.add_parser("validate-transcript")
    sub.add_argument("--input", required=True, type=Path)
    sub.add_argument("--episode")
    sub.add_argument("--review", type=Path, help="Private source-hash-bound transcript_review.v1 assessment")
    sub.add_argument("--require-ready", action="store_true", help="Nonzero exit if content review is missing or rejects extraction")
    sub = commands.add_parser("prepare")
    sub.add_argument("--input", required=True, type=Path)
    sub.add_argument("--output", required=True, type=Path)
    sub.add_argument("--review", type=Path)
    sub = commands.add_parser("review-packet", help="Private listening candidates; never automatic acceptance")
    sub.add_argument("--input", required=True, type=Path)
    sub.add_argument("--output", required=True, type=Path)
    sub.add_argument("--audio", type=Path, help="Optional existing audio; hash only, no decoding or model")
    sub.add_argument("--samples", type=int, default=8)
    sub.add_argument("--context-seconds", type=float, default=12.0)
    sub.add_argument("--segment-id", type=int, action="append", help="Explicit raw segment ID to include")
    args = parser.parse_args(argv)
    load_project_dotenv()
    try:
        if args.command == "doctor":
            from .environment import doctor

            result = doctor()
            code = 0 if result["ready"] else 1
        elif args.command == "cache-model":
            from .environment import cache_model
            from .batch import load_batch_config

            result = cache_model(load_batch_config(args.config), execute=args.execute)
            code = 0
        elif args.command == "batch-export":
            from .batch import load_batch_config
            from .transfer import export_bundle

            result = export_bundle(load_batch_config(args.config), args.output, execute=args.execute and not args.dry_run)
            code = 0
        elif args.command == "batch-import":
            from .batch import load_batch_config
            from .transfer import import_bundle

            settings = load_batch_config(args.storage_config).get("storage") if args.storage_config else None
            result = import_bundle(args.source, args.dest_root, execute=args.execute and not args.dry_run, storage=settings)
            code = 0
        elif args.command == "catalog-fetch":
            from .catalog import fetch_catalog

            result = fetch_catalog(args.rss_url, args.output, quarantine_ambiguous=args.quarantine_ambiguous)
            code = 0
        elif args.command == "validate-transcript":
            from .quality import quality_gate

            result = quality_gate(private_path(args.input), args.episode, private_path(args.review) if args.review else None)
            code = 0 if not args.require_ready or result["ready_for_extraction"] else 1
        elif args.command == "prepare":
            from .prepare import prepare

            result = prepare(private_path(args.input), private_path(args.output), private_path(args.review) if args.review else None)
            code = 0
        elif args.command == "review-packet":
            from .review import create_review_packet

            result = create_review_packet(args.input, args.output, audio=args.audio, sample_count=args.samples,
                                          context_seconds=args.context_seconds, segment_ids=args.segment_id)
            code = 0
        else:
            from . import batch

            cfg = batch.load_batch_config(args.config, args)
            if args.episode:
                cfg["episode_ids"] = args.episode
            if args.episode_file:
                cfg["episode_ids"] = [line.strip() for line in args.episode_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            if args.command == "batch-plan" or args.dry_run:
                result = batch.plan(cfg, max_episodes=args.max_episodes)
            elif args.command in {"batch-run", "batch-resume"}:
                result = batch.run_batch(cfg, dry_run=False, resume=args.command == "batch-resume", retry_failed=args.retry_failed, max_episodes=args.max_episodes)
            elif args.command == "batch-status":
                result = batch.status(cfg)
            else:
                result = batch.validate_batch_outputs(cfg)
            code = 1 if result.get("valid") is False or result.get("status_counts", {}).get("failed") else 0
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        # Do not render external-service exceptions or environment values here.
        print(f"{type(exc).__name__}: {exc}" if args.command not in {"batch-run", "batch-resume"} else f"Batch setup failed ({type(exc).__name__}); check private paths, config and checkpoint compatibility.", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return code
