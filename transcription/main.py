from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from .common import load_project_dotenv, private_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Private-data transcription and pre-extraction quality gate.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot-create", "snapshot-verify", "snapshot-restore", "snapshot-protect", "corpus-review"):
        sub = commands.add_parser(name, help="Private raw preservation/review; no inference")
        if name == "snapshot-create":
            sub.add_argument("--selection", required=True, type=Path)
        else:
            sub.add_argument("--snapshot", required=True, type=Path)
        if name in {"snapshot-create", "snapshot-protect", "corpus-review"}:
            sub.add_argument("--root", required=True, type=Path)
        if name in {"snapshot-create", "snapshot-restore", "corpus-review"}:
            sub.add_argument("--output", required=True, type=Path)
        if name in {"snapshot-create", "snapshot-restore"}:
            sub.add_argument("--execute", action="store_true", help="Default is read-only dry-run")
            sub.add_argument("--storage-config", type=Path)
        if name == "snapshot-protect":
            sub.add_argument("--execute", action="store_true", help="Required before making source files read-only")
    commands.add_parser("doctor", help="Check CUDA libraries/dependencies without downloading or loading a model")
    sub = commands.add_parser("compare-transcripts", help="Compare two existing runs of the same audio; no inference or quality approval")
    sub.add_argument("--baseline", required=True, type=Path)
    sub.add_argument("--candidate", required=True, type=Path)
    sub.add_argument("--output", type=Path, help="Optional new private comparison report")
    sub = commands.add_parser("catalog-diff", help="Read-only catalog delta; never download or delete episodes")
    sub.add_argument("--before", required=True, type=Path)
    sub.add_argument("--after", required=True, type=Path)
    sub.add_argument("--output", type=Path, help="Optional new private diff report")
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
        sub.add_argument("--inference-mode", choices=["sequential", "batched"])
        sub.add_argument("--inference-batch-size", type=int)
        for flag in ("output-root", "audio-root", "transcript-root", "compute-type"):
            sub.add_argument(f"--{flag}")
    sub = commands.add_parser("validate-transcript")
    sub.add_argument("--input", required=True, type=Path)
    sub.add_argument("--episode")
    sub.add_argument("--review", type=Path, help="Private transcript_review.v1 or v2 assessment; v2 pins its annotation sidecar")
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
        if args.command.startswith("snapshot-") or args.command == "corpus-review":
            from .snapshot import create_snapshot, verify_snapshot, restore_snapshot, protect_raw_sources
            from .batch import load_batch_config
            settings = load_batch_config(args.storage_config).get("storage") if getattr(args, "storage_config", None) else None
            if args.command == "snapshot-create":
                result = create_snapshot(args.selection, args.root, args.output, execute=args.execute, storage=settings)
            elif args.command == "snapshot-restore":
                result = restore_snapshot(args.snapshot, args.output, execute=args.execute, storage=settings)
            elif args.command == "snapshot-protect":
                if not args.execute:
                    raise ValueError("Source permission changes require explicit --execute.")
                result = protect_raw_sources(args.snapshot, args.root)
            elif args.command == "corpus-review":
                from .review import scan_corpus
                result = scan_corpus(args.snapshot, args.root, args.output)
            else:
                manifest = verify_snapshot(args.snapshot)
                result = {"valid": True, "file_count": len(manifest["files"]), "content_review_accepted": False}
            code = 0
        elif args.command == "compare-transcripts":
            from .compare import compare_transcripts

            result = compare_transcripts(args.baseline, args.candidate, args.output)
            code = 0
        elif args.command == "catalog-diff":
            from .catalog import compare_catalogs
            result = compare_catalogs(args.before, args.after, args.output)
            code = 0
        elif args.command == "doctor":
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
    except (ValueError, KeyError, RuntimeError, OSError, zipfile.BadZipFile) as exc:
        # Do not render external-service exceptions or environment values here.
        print(f"{type(exc).__name__}: {exc}" if args.command not in {"batch-run", "batch-resume"} else f"Batch setup failed ({type(exc).__name__}); check private paths, config and checkpoint compatibility.", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return code
