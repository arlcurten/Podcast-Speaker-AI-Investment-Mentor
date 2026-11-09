"""Bounded reviewed or quarantined candidate L1-L3 extraction; never auto-publish."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import json
import os
import re
from pathlib import Path
from threading import Event, Lock

from transcription.common import atomic_write_json, child_path, private_path, read_json, safe_id, sha256_file, utc_now
from transcription.prepare import prepare
from . import pipeline
from .codex_backend import CodexBackend
from .provenance import digest


def _reuse_artifacts(entry: dict, mode: str) -> dict:
    if "reuse_artifacts" not in entry:
        return {}
    if mode != "candidate":
        raise ValueError("Batch reuse_artifacts requires candidate extraction_mode.")
    reuse = entry["reuse_artifacts"]
    if (not isinstance(reuse, dict) or len(reuse) > 3
            or set(reuse) != set(pipeline.STAGES[:len(reuse)])):
        raise ValueError("reuse_artifacts must be a contiguous Level 1-3 prefix.")
    result = {}
    for stage, reference in reuse.items():
        if (not isinstance(reference, dict) or set(reference) != {"path", "sha256"}
                or not isinstance(reference["path"], str)
                or not isinstance(reference["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", reference["sha256"])):
            raise ValueError("Reused artifacts require a private path and SHA-256.")
        result[stage] = {"path": str(private_path(reference["path"])), "sha256": reference["sha256"]}
    return result


def _episode_config(cfg: dict, entry: dict, root: Path, normalized: Path, review) -> dict:
    retry_count = cfg.get("retry_count", 0)
    result = {
        "schema_version": "knowledge_config.v1", "run_id": entry["episode_id"],
        "output_root": str(root / "stages"), "model": cfg["model"],
        "reasoning_effort": cfg["reasoning_effort"], "timeout_seconds": cfg["timeout_seconds"],
        "max_input_chars": cfg["max_input_chars"], "max_calls": 3 * (1 + retry_count),
        "retry_count": retry_count,
        "episodes": [{"episode_id": entry["episode_id"], "raw": entry["raw"], "normalized": str(normalized),
                      "review": str(review) if review is not None else None}],
    }
    if cfg.get("extraction_mode", "reviewed") == "candidate":
        result["extraction_mode"] = "candidate"
        source = result["episodes"][0]
        source.update(raw_sha256=entry["raw_sha256"], normalized_sha256=entry["draft_sha256"],
                      review_sha256=entry.get("source_review_sha256"))
        if "reuse_artifacts" in entry:
            source["reuse_artifacts"] = _reuse_artifacts(entry, "candidate")
    return result


def load_batch(path: Path) -> dict:
    cfg = read_json(private_path(path.resolve()))
    if cfg.get("schema_version") != "knowledge_batch.v1":
        raise ValueError("Expected knowledge_batch.v1.")
    cfg.setdefault("extraction_mode", "reviewed")
    if cfg["extraction_mode"] not in ("reviewed", "candidate"):
        raise ValueError("extraction_mode must be reviewed or candidate.")
    safe_id(cfg["batch_id"])
    cfg["output_root"] = str(private_path(cfg["output_root"]))
    if not isinstance(cfg.get("model"), str) or not cfg["model"].strip():
        raise ValueError("An explicit subscription model is required.")
    if os.environ.get("KNOWLEDGE_MODEL_ID", cfg["model"]) != cfg["model"]:
        raise ValueError("KNOWLEDGE_MODEL_ID differs from the pinned batch model.")
    if cfg.get("reasoning_effort") not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("Configure a supported reasoning effort.")
    for key, default in (("timeout_seconds", 1800), ("max_input_chars", 180000)):
        cfg.setdefault(key, default)
        if type(cfg[key]) is not int or cfg[key] <= 0:
            raise ValueError(f"Invalid {key}.")
    if "retry_count" in cfg and (type(cfg["retry_count"]) is not int or cfg["retry_count"] not in (0, 1)):
        raise ValueError("retry_count must be 0 or 1.")
    if "max_consecutive_failures" in cfg and (type(cfg["max_consecutive_failures"]) is not int
                                              or cfg["max_consecutive_failures"] < 1):
        raise ValueError("max_consecutive_failures must be a positive integer.")
    entries = cfg.get("episodes")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Explicit episode sources are required.")
    ids = set()
    for entry in entries:
        eid = safe_id(entry["episode_id"])
        if eid in ids:
            raise ValueError("Duplicate episode in batch.")
        ids.add(eid)
        for field in ("raw", "draft"):
            entry[field] = str(private_path(entry[field]))
            if sha256_file(Path(entry[field])) != entry[field + "_sha256"]:
                raise ValueError("Pinned source changed; use a new reviewed batch version.")
        if entry.get("source_review") is not None:
            entry["source_review"] = str(private_path(entry["source_review"]))
            if sha256_file(Path(entry["source_review"])) != entry.get("source_review_sha256"):
                raise ValueError("Pinned source review changed or lacks SHA-256; use a new batch version.")
        elif entry.get("source_review_sha256") is not None:
            raise ValueError("source_review_sha256 requires a source_review path.")
        if not isinstance(entry.get("prior_findings", []), list):
            raise ValueError("prior_findings must be an explicit list.")
        if cfg["extraction_mode"] == "candidate" and entry.get("prior_findings"):
            raise ValueError("Candidate prior_findings must be empty; use a pinned source_review for context.")
        if "reuse_artifacts" in entry:
            entry["reuse_artifacts"] = _reuse_artifacts(entry, cfg["extraction_mode"])
    return cfg


def execute(cfg: dict, *, workers: int = 1, max_episodes: int | None = None,
            dry_run: bool = False, retry_failed: bool = False, reviewer=None, backend_factory=None) -> dict:
    mode = cfg.get("extraction_mode", "reviewed")
    if mode not in ("reviewed", "candidate"):
        raise ValueError("extraction_mode must be reviewed or candidate.")
    retry_count = cfg.get("retry_count", 0)
    if type(retry_count) is not int or retry_count not in (0, 1):
        raise ValueError("retry_count must be 0 or 1.")
    failure_limit = cfg.get("max_consecutive_failures")
    if "max_consecutive_failures" in cfg and (type(failure_limit) is not int or failure_limit < 1):
        raise ValueError("max_consecutive_failures must be a positive integer.")
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("Use 1-4 workers; measure subscription limits before raising concurrency.")
    if max_episodes is not None and (type(max_episodes) is not int or max_episodes < 1):
        raise ValueError("max_episodes must be positive.")
    if mode == "candidate" and any(entry.get("prior_findings") for entry in cfg["episodes"]):
        raise ValueError("Candidate prior_findings must be empty; use a pinned source_review for context.")
    root = child_path(private_path(cfg["output_root"]), safe_id(cfg["batch_id"]))
    selected = cfg["episodes"][:max_episodes]
    for entry in cfg["episodes"]:
        _reuse_artifacts(entry, mode)
    # Preflight every selected reference before any worker can start inference.
    reuse_plans = {}
    for entry in selected:
        if entry.get("reuse_artifacts"):
            if os.environ.get("KNOWLEDGE_MODEL_ID", cfg["model"]) != cfg["model"]:
                raise ValueError("KNOWLEDGE_MODEL_ID differs from the pinned batch model.")
            episode_cfg = _episode_config(cfg, entry, root, Path(entry["draft"]), entry.get("source_review"))
            reuse_plans[entry["episode_id"]] = pipeline.run(episode_cfg, through="level3", dry_run=True)
    if dry_run:
        reused = sum(len(e.get("reuse_artifacts", {})) for e in selected)
        reuse_details = ({"reuse_bindings_validated": True, "referenced_artifacts": reused,
                          "estimated_new_calls": sum(p["estimated_new_calls"] for p in reuse_plans.values())
                          + 3 * (len(selected) - len(reuse_plans))} if reuse_plans else {})
        return {"dry_run": True, "episode_ids": [e["episode_id"] for e in selected],
                "maximum_initial_calls": len(selected) * (3 if mode == "candidate" else 4) - reused, "workers": workers,
                "through": "level3", "extraction_mode": mode, "automatic_release": False,
                "ready_for_serving": False, **reuse_details}
    backend_factory = backend_factory or CodexBackend
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".batch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pinned = root / "batch_manifest.json"
        if pinned.exists():
            previous = read_json(pinned)
            if {"extraction_mode": "reviewed", **previous} != {"extraction_mode": "reviewed", **cfg}:
                raise ValueError("Batch settings changed; use a new batch_id.")
        else:
            atomic_write_json(pinned, cfg, overwrite=False)
        if mode == "reviewed" and reviewer is None:
            from transcription.source_review import review_source
            reviewer = review_source
        stop = Event()
        failure_lock = Lock()
        consecutive_failures = 0

        def job(entry):
            eid = entry["episode_id"]
            folder = child_path(root, eid)
            status = folder / "status.json"
            old = read_json(status) if status.exists() else {}
            if old.get("status") in {"failed", "running"} and not retry_failed:
                return {**old, "action": "needs_explicit_retry"}
            if old.get("status") == "held":
                return {**old, "action": "retained_hold"}
            metadata = {"episode_id": eid, "updated_at": utc_now(), "source_raw_sha256": entry["raw_sha256"],
                        "extraction_mode": mode, "automatic_release": False, "ready_for_serving": False}
            if mode == "candidate":
                metadata.update(source_draft_sha256=entry["draft_sha256"],
                                source_review_sha256=entry.get("source_review_sha256"),
                                source_review_status="context_only" if entry.get("source_review") else "not_supplied",
                                candidate_extraction_completed=(old.get("status") == "completed_candidate"
                                                                or old.get("candidate_extraction_completed") is True),
                                publication_status="blocked_candidate", quarantined=True)
            def checkpoint(state, **details):
                result = {**metadata, "updated_at": utc_now(), "status": state, **details}
                atomic_write_json(status, result)
                return result
            checkpoint("running", stage="candidate_source_validation" if mode == "candidate" else "source_review")
            try:
                fields = ("raw", "draft", "source_review") if entry.get("source_review") is not None else ("raw", "draft")
                for field in fields:
                    if sha256_file(Path(entry[field])) != entry[field + "_sha256"]:
                        raise ValueError("Pinned source changed.")
                validate_only = mode == "candidate" and metadata["candidate_extraction_completed"]
                has_references = bool(entry.get("reuse_artifacts"))
                backend = None
                if not validate_only and not has_references:
                    backend = backend_factory(cfg["model"], folder / "tmp", timeout_seconds=cfg["timeout_seconds"],
                                              reasoning_effort=cfg["reasoning_effort"], max_input_chars=cfg["max_input_chars"])
                if mode == "candidate":
                    review = entry.get("source_review")
                    normalized = Path(entry["draft"])
                else:
                    result = reviewer(Path(entry["raw"]), Path(entry["draft"]), folder / "review", backend,
                                      prior_findings=entry.get("prior_findings", []))
                    if not result["accepted"]:
                        return checkpoint("held" if result["review_path"] else "failed", stage="source_review",
                                          review_report=result["report_path"])
                    review = result["review_path"]
                    normalized = folder / "normalized.json"
                    if not normalized.exists():
                        prepare(Path(entry["raw"]), normalized, Path(review))
                episode_cfg = _episode_config(cfg, entry, root, normalized, review)
                config_path = folder / "extraction_config.json"
                if config_path.exists():
                    if read_json(config_path) != episode_cfg:
                        raise ValueError("Episode config changed; do not overwrite prior work.")
                else:
                    if validate_only:
                        raise ValueError("Completed candidate config is missing; restore it without model calls.")
                    try:
                        atomic_write_json(config_path, episode_cfg, overwrite=False)
                    except FileExistsError:
                        if read_json(config_path) != episode_cfg:
                            raise ValueError("Concurrent episode config differs; preserve it for inspection.")
                checkpoint("running", stage="level1_to_level3", config=str(config_path))
                loaded_cfg = pipeline.load_config(config_path)
                if has_references and not validate_only:
                    if loaded_cfg["model"] != cfg["model"]:
                        raise ValueError("Episode model differs from the pinned batch model.")
                    plan = pipeline.run(loaded_cfg, through="level3", dry_run=True)
                    validate_only = plan["estimated_new_calls"] == 0
                    if not validate_only:
                        backend = backend_factory(cfg["model"], folder / "tmp", timeout_seconds=cfg["timeout_seconds"],
                                                  reasoning_effort=cfg["reasoning_effort"], max_input_chars=cfg["max_input_chars"])
                report = pipeline.run(loaded_cfg, through="level3", backend=backend,
                                      validate_only=validate_only)
                if mode == "candidate":
                    metadata["candidate_extraction_completed"] = True
                return checkpoint("completed_candidate" if mode == "candidate" else "completed",
                                  stage="level3", config=str(config_path),
                                  report=report, semantic_review_status="pending")
            except BlockingIOError:
                return checkpoint("deferred", action="episode_busy", note="Another worker owns this episode; resume later.")
            except Exception as exc:
                # Provider bodies may contain source text or secrets. Keep diagnostics private and bounded.
                return checkpoint("failed", error_type=type(exc).__name__,
                                  note="Inspect immutable per-stage call/review records; no automatic retry or publication.")

        def guarded_job(entry):
            nonlocal consecutive_failures
            with failure_lock:
                if stop.is_set():
                    return {"episode_id": entry["episode_id"], "status": "deferred", "action": "failure_limit_reached",
                            "extraction_mode": mode, "automatic_release": False, "ready_for_serving": False}
            try:
                result = job(entry)
            except Exception as exc:
                result = {"episode_id": entry["episode_id"], "status": "failed", "error_type": type(exc).__name__,
                          "note": "Episode checkpoint is unreadable or unwritable; preserved for inspection.",
                          "extraction_mode": mode, "automatic_release": False, "ready_for_serving": False}
            # Account before this worker can admit another queued job, not in the result collector.
            with failure_lock:
                if result["status"] == "failed" and result.get("action") != "needs_explicit_retry":
                    consecutive_failures += 1
                    if failure_limit is not None and consecutive_failures >= failure_limit:
                        stop.set()
                elif result["status"] in {"completed", "completed_candidate"}:
                    consecutive_failures = 0
            return result

        results = {}
        def summary():
            failure_status = {}
            if failure_limit is not None:
                with failure_lock:
                    failure_status = {"max_consecutive_failures": failure_limit,
                                      "consecutive_failures": consecutive_failures, "failure_limit_reached": stop.is_set()}
            return {"schema_version": "knowledge_batch_report.v1", "batch_id": cfg["batch_id"],
                    "extraction_mode": mode, "automatic_release": False,
                    "config_sha256": digest(cfg), "updated_at": utc_now(), "selected_count": len(selected),
                    "finished_count": len(results), "counts": dict(Counter(r["status"] for r in results.values())),
                    "episodes": [results[e["episode_id"]] for e in selected if e["episode_id"] in results],
                    "level4": "not_run; consolidate explicit reviewed episode selection separately",
                    "ready_for_serving": False, **failure_status}
        atomic_write_json(root / "summary.json", summary())
        with ThreadPoolExecutor(max_workers=workers) as pool:
            tasks = {pool.submit(guarded_job, e): e["episode_id"] for e in selected}
            for task in as_completed(tasks):
                try:
                    result = task.result()
                except Exception as exc:
                    result = {"episode_id": tasks[task], "status": "failed", "error_type": type(exc).__name__,
                              "note": "Episode checkpoint is unreadable or unwritable; preserved for inspection.",
                              "extraction_mode": mode, "automatic_release": False, "ready_for_serving": False}
                results[result["episode_id"]] = result
                atomic_write_json(root / "summary.json", summary())
                print(json.dumps({"episode_id": result["episode_id"], "status": result["status"],
                                  "finished": len(results), "selected": len(selected)}), flush=True)
        return summary()


def consolidation_config(cfg: dict, output: Path, run_id: str, max_episodes: int | None = None) -> dict:
    """Plan L4 over explicit completed jobs without copying stages or calling a model."""
    mode = cfg.get("extraction_mode", "reviewed")
    if mode not in ("reviewed", "candidate"):
        raise ValueError("extraction_mode must be reviewed or candidate.")
    if max_episodes is not None and (type(max_episodes) is not int or max_episodes < 1):
        raise ValueError("max_episodes must be positive.")
    selected = cfg["episodes"][:max_episodes]
    if len(selected) < 2 or len({e["episode_id"] for e in selected}) != len(selected):
        raise ValueError("Consolidation requires at least two distinct explicit completed episodes.")
    root = child_path(private_path(cfg["output_root"]), safe_id(cfg["batch_id"]))
    output = private_path(output)
    run_root = child_path(root, "consolidations", safe_id(run_id))
    if output.exists() or run_root.exists():
        raise ValueError("Consolidation requires a new config path and unused run_id.")
    manifest = root / "batch_manifest.json"
    pins = {manifest: sha256_file(manifest)}
    previous = read_json(manifest)
    if {"extraction_mode": "reviewed", **previous} != {"extraction_mode": "reviewed", **cfg}:
        raise ValueError("Batch settings changed; do not consolidate a different batch version.")
    result = {"schema_version": "knowledge_config.v1", "run_id": run_id,
              "output_root": str(run_root.parent), "extraction_mode": mode,
              **{key: cfg[key] for key in ("model", "reasoning_effort", "timeout_seconds", "max_input_chars")},
              "max_calls": 1, "retry_count": 0, "episodes": []}
    expected_status = "completed_candidate" if mode == "candidate" else "completed"
    for entry in selected:
        eid = safe_id(entry["episode_id"])
        folder = child_path(root, eid)
        status_path, config_path = folder / "status.json", folder / "extraction_config.json"
        pins[status_path] = sha256_file(status_path)
        status = read_json(status_path)
        if status.get("status") != expected_status or status.get("episode_id") != eid:
            raise ValueError(f"Selected episode {eid} is not {expected_status}; no episodes are silently omitted.")
        pins[config_path] = sha256_file(config_path)
        for field in ("raw", "draft", "source_review"):
            if entry.get(field) is not None and sha256_file(Path(entry[field])) != entry[field + "_sha256"]:
                raise ValueError("Pinned source changed; do not consolidate changed inputs.")
        episode_cfg = pipeline.load_config(config_path)
        if (episode_cfg["run_id"] != eid or episode_cfg["output_root"] != str(root / "stages")
                or episode_cfg.get("extraction_mode", "reviewed") != mode
                or any(episode_cfg[key] != result[key] for key in ("model", "reasoning_effort", "timeout_seconds", "max_input_chars"))
                or len(episode_cfg["episodes"]) != 1):
            raise ValueError("Completed episode extraction settings differ from the pinned batch.")
        source = episode_cfg["episodes"][0]
        normalized = entry["draft"] if mode == "candidate" else str(folder / "normalized.json")
        if (source["episode_id"] != eid or source["raw"] != entry["raw"] or source["normalized"] != normalized
                or (mode == "candidate" and source.get("review") != entry.get("source_review"))):
            raise ValueError("Completed episode sources differ from the pinned batch.")
        if mode == "candidate" and (
                source.get("reuse_artifacts", {}) != _reuse_artifacts(entry, mode)
                or any(source.get(key) != entry.get(batch_key) for key, batch_key in (
                    ("raw_sha256", "raw_sha256"), ("normalized_sha256", "draft_sha256"),
                    ("review_sha256", "source_review_sha256")))):
            raise ValueError("Completed candidate source/reuse pins differ from the pinned batch.")
        validated = pipeline.run(episode_cfg, through="level3", validate_only=True)
        references = {r["stage"]: {"path": r["artifact_path"], "sha256": r["sha256"]}
                      for r in validated["artifacts"]}
        result["episodes"].append({**source, "reuse_artifacts": references})
    plan = pipeline.run(result, through="level4", dry_run=True)
    if plan["estimated_new_calls"] != 1 or any(sha256_file(path) != checksum for path, checksum in pins.items()):
        raise ValueError("Consolidation inputs changed or plan is not exactly one new L4 call.")
    atomic_write_json(output, result, overwrite=False)
    return {"config": str(output), "run_id": run_id, "extraction_mode": mode,
            "episode_ids": [e["episode_id"] for e in selected], "model_calls": 0,
            "estimated_new_calls": 1, "referenced_artifacts": len(selected) * 3,
            "automatic_release": False, "ready_for_serving": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-episodes", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--consolidate-config", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    if (args.consolidate_config is None) != (args.run_id is None):
        parser.error("--consolidate-config and --run-id must be supplied together.")
    if args.consolidate_config is not None and (args.dry_run or args.retry_failed):
        parser.error("Consolidation only writes a planned config; omit --dry-run and --retry-failed.")
    try:
        cfg = load_batch(args.config)
        if args.consolidate_config is not None:
            result = consolidation_config(cfg, args.consolidate_config, args.run_id, max_episodes=args.max_episodes)
        else:
            result = execute(cfg, workers=args.workers, max_episodes=args.max_episodes,
                             dry_run=args.dry_run, retry_failed=args.retry_failed)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"Batch setup failed ({type(exc).__name__}); verify private paths and pinned configuration.\n")
    print(json.dumps(result if args.dry_run else {k: v for k, v in result.items() if k != "episodes"}, indent=2))
    return int(bool(result.get("counts", {}).get("failed")))


if __name__ == "__main__":
    raise SystemExit(main())
