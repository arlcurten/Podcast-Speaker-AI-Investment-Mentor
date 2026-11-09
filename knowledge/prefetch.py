"""Bounded look-ahead for an existing candidate batch; never changes its queue."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import json
from pathlib import Path
from threading import Event, Lock
import time

from . import batch, pipeline
from transcription.common import atomic_write_json, child_path, private_path, read_json, safe_id, utc_now


def execute(cfg, episode_ids, run_id, *, workers=1, dry_run=False, backend_factory=None,
            minimum_queue_gap=0, controller_owned=False):
    options = dict(workers=workers, dry_run=dry_run, backend_factory=backend_factory,
                   minimum_queue_gap=minimum_queue_gap)
    if controller_owned:
        return _execute(cfg, episode_ids, run_id, **options)
    root = child_path(private_path(cfg["output_root"]), safe_id(cfg["batch_id"]))
    lock_path = root / ".prefetch_controller.lock"
    if dry_run and not lock_path.exists():
        return _execute(cfg, episode_ids, run_id, **options)
    with lock_path.open("a") as lane_lock:
        try:
            fcntl.flock(lane_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A controller or standalone trial already owns additional concurrency.") from None
        return _execute(cfg, episode_ids, run_id, **options)


def _execute(cfg, episode_ids, run_id, *, workers, dry_run, backend_factory, minimum_queue_gap):
    if cfg.get("extraction_mode") != "candidate":
        raise ValueError("Look-ahead requires an existing candidate batch.")
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("Use 1-4 additional workers and account for other running services.")
    if type(minimum_queue_gap) is not int or minimum_queue_gap < 0:
        raise ValueError("minimum_queue_gap must be a nonnegative integer.")
    if not episode_ids or len(episode_ids) > 16 or len(set(episode_ids)) != len(episode_ids):
        raise ValueError("Select 1-16 distinct episodes for a bounded concurrency trial.")
    entries = {e["episode_id"]: e for e in cfg["episodes"]}
    if not set(episode_ids) <= entries.keys():
        raise ValueError("Every selected episode must belong to the pinned batch.")
    root = child_path(private_path(cfg["output_root"]), safe_id(cfg["batch_id"]))
    if read_json(root / "batch_manifest.json") != cfg:
        raise ValueError("Existing batch manifest differs from the supplied config.")
    folder = child_path(root, "accelerations", safe_id(run_id))
    if folder.exists():
        raise ValueError("Use a new trial ID; existing trial reports are preserved.")
    # Do not steal admitted jobs. The pipeline's per-episode lock also arbitrates
    # a race with the main scheduler; only one process can generate artifacts.
    for eid in episode_ids:
        if child_path(root, eid, "status.json").exists():
            raise ValueError("Select unstarted episodes, away from the main queue head.")
    def queue_gap(eid):
        preceding = list(entries)[:list(entries).index(eid)]
        return sum(not child_path(root, other, "status.json").exists()
                   for other in preceding if other not in episode_ids)
    if any(queue_gap(eid) < minimum_queue_gap for eid in episode_ids):
        raise ValueError("Selected episodes are too close to the main queue head.")
    plan = {"schema_version": "knowledge_prefetch.v1", "run_id": run_id,
            "batch_id": cfg["batch_id"], "episode_ids": episode_ids,
            "workers": workers, "minimum_queue_gap": minimum_queue_gap,
            "created_at": utc_now(), "automatic_release": False}
    if dry_run:
        return {**plan, "dry_run": True, "model_calls": 0}
    folder.mkdir(parents=True, exist_ok=False)
    atomic_write_json(folder / "plan.json", plan, overwrite=False)
    stop, mutex = Event(), Lock()
    failures = 0

    def job(eid):
        nonlocal failures
        if stop.is_set():
            return {"episode_id": eid, "status": "deferred"}
        if child_path(root, eid, "status.json").exists():
            return {"episode_id": eid, "status": "skipped_main_queue"}
        if queue_gap(eid) < minimum_queue_gap:
            return {"episode_id": eid, "status": "deferred_queue_near"}
        entry = entries[eid]
        config = batch._episode_config(cfg, entry, root, Path(entry["draft"]), entry.get("source_review"))
        path = child_path(root, eid, "extraction_config.json")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                atomic_write_json(path, config, overwrite=False)
            except FileExistsError:
                pass
            if read_json(path) != config:
                raise ValueError("Episode config differs; never overwrite another job.")
            loaded = pipeline.load_config(path)
            backend = (backend_factory(cfg["model"], root / eid / "tmp",
                       timeout_seconds=cfg["timeout_seconds"], reasoning_effort=cfg["reasoning_effort"],
                       max_input_chars=cfg["max_input_chars"]) if backend_factory else None)
            report = pipeline.run(loaded, through="level3", backend=backend)
            return {"episode_id": eid, "status": "completed_candidate", "report": report}
        except BlockingIOError:
            return {"episode_id": eid, "status": "skipped_busy"}
        except Exception as exc:
            with mutex:
                failures += 1
                if failures >= cfg.get("max_consecutive_failures", 3):
                    stop.set()
            return {"episode_id": eid, "status": "failed", "error_type": type(exc).__name__}

    rows = []
    with (folder / ".trial.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(job, eid) for eid in episode_ids]
            for future in as_completed(futures):
                row = future.result()
                rows.append(row)
                atomic_write_json(folder / "summary.json", {**plan, "updated_at": utc_now(),
                                  "episodes": rows, "failure_limit_reached": stop.is_set()})
                print(json.dumps({"episode_id": row["episode_id"], "status": row["status"]}), flush=True)
    return {**plan, "episodes": rows, "failure_limit_reached": stop.is_set()}


def _trial_inventory(root):
    """Wait for existing trials; preserve failed/interrupted work for review."""
    excluded, failed, interrupted = set(), set(), set()
    for folder in sorted((root / "accelerations").glob("*")):
        plan_path = folder / "plan.json"
        if not plan_path.exists():
            continue
        lock_path = folder / ".trial.lock"
        if lock_path.exists():
            with lock_path.open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True, excluded, failed, interrupted
        plan = read_json(plan_path)
        summary_path = folder / "summary.json"
        rows = read_json(summary_path).get("episodes", []) if summary_path.exists() else []
        recorded = {row["episode_id"] for row in rows}
        interrupted.update(set(plan["episode_ids"]) - recorded)
        excluded.update(row["episode_id"] for row in rows
                        if row["status"] in {"completed_candidate", "failed", "skipped_busy"})
        failed.update(row["episode_id"] for row in rows if row["status"] == "failed")
    excluded.update(interrupted)
    return False, excluded, failed, interrupted


def _provider_failed(root, eid):
    calls = list(child_path(root, "stages", eid, "calls").glob("*.json"))
    latest = max(calls, key=lambda p: int(p.stem.rsplit("_", 1)[1])) if calls else None
    call = read_json(latest) if latest else {}
    return (call.get("status") == "failed" and call.get("response") is None
            and call.get("error_type") in {"ModelCallError", "RuntimeError"})


def _historical_provider_failures(root, run_id):
    episodes = set()
    for path in (root / "accelerations").glob(f"{run_id}_*/summary.json"):
        episodes.update(row["episode_id"] for row in read_json(path).get("episodes", [])
                        if row["status"] == "failed" and _provider_failed(root, row["episode_id"]))
    return len(episodes)


def continue_pending(cfg, run_id, *, workers=4, minimum_queue_gap=128, poll_seconds=30,
                     runner=None, sleeper=time.sleep):
    """Drain safe tail jobs in chunks, rather than stopping at the chunk size."""
    if cfg.get("extraction_mode") != "candidate" or type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("Continuous look-ahead requires candidate mode and 1-4 workers.")
    if type(minimum_queue_gap) is not int or minimum_queue_gap < 0 or poll_seconds <= 0:
        raise ValueError("Invalid queue distance or polling interval.")
    root = child_path(private_path(cfg["output_root"]), safe_id(cfg["batch_id"]))
    if read_json(root / "batch_manifest.json") != cfg:
        raise ValueError("Existing batch manifest differs from supplied config.")
    folder = child_path(root, "controllers", safe_id(run_id))
    folder.mkdir(parents=True, exist_ok=True)
    identity = {"schema_version": "knowledge_continuation.v1", "batch_config": cfg,
                "workers": workers, "minimum_queue_gap": minimum_queue_gap}
    manifest = folder / "manifest.json"
    if manifest.exists() and read_json(manifest) != identity:
        raise ValueError("Controller config changed; use a new run ID.")
    runner = runner or execute
    state_path = folder / "status.json"
    previous = read_json(state_path) if state_path.exists() else {}
    provider_failures = previous.get("provider_failure_count", 0)
    if provider_failures >= 3:
        raise ValueError("Repeated provider failures require inspection before resuming.")
    def checkpoint(state, **details):
        result = {"schema_version": "knowledge_continuation_status.v1", "state": state,
                  "updated_at": utc_now(), "provider_failure_count": provider_failures,
                  "automatic_release": False, **details}
        atomic_write_json(state_path, result)
        return result
    with (root / ".prefetch_controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not manifest.exists():
            atomic_write_json(manifest, identity, overwrite=False)
        try:
            while True:
                provider_failures = max(provider_failures, _historical_provider_failures(root, run_id))
                if provider_failures >= 3:
                    return checkpoint("needs_attention_provider_failures")
                active, excluded, failed, interrupted = _trial_inventory(root)
                if active:
                    checkpoint("waiting_for_existing_trial")
                    sleeper(poll_seconds)
                    continue
                pending = [e["episode_id"] for e in cfg["episodes"]
                           if not child_path(root, e["episode_id"], "status.json").exists()]
                eligible = [eid for eid in pending[minimum_queue_gap:] if eid not in excluded]
                ids = list(reversed(eligible))[:16]
                if not ids:
                    return checkpoint("tail_drained_main_queue_remains",
                                      failed_episode_ids=sorted(failed), interrupted_episode_ids=sorted(interrupted))
                serial = 1
                while (root / "accelerations" / f"{run_id}_{serial:04d}").exists():
                    serial += 1
                trial_id = f"{run_id}_{serial:04d}"
                checkpoint("running_chunk", trial_id=trial_id, episode_ids=ids,
                           failed_episode_ids=sorted(failed), interrupted_episode_ids=sorted(interrupted))
                result = runner(cfg, ids, trial_id, workers=workers, minimum_queue_gap=minimum_queue_gap,
                                controller_owned=True)
                provider_errors = sum(row["status"] == "failed" and _provider_failed(root, row["episode_id"])
                                      for row in result["episodes"])
                provider_failures += provider_errors
                if provider_failures >= 3:
                    return checkpoint("needs_attention_provider_failures", trial_id=trial_id)
                checkpoint("chunk_completed", trial_id=trial_id)
                if provider_errors:
                    sleeper(max(60, poll_seconds))
        except Exception as exc:
            checkpoint("needs_attention", error_type=type(exc).__name__)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--episode", action="append")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--minimum-queue-gap", type=int)
    parser.add_argument("--continue-pending", action="store_true")
    args = parser.parse_args()
    if args.continue_pending and (args.episode or args.dry_run):
        parser.error("Continuous mode discovers pending jobs; omit --episode and --dry-run.")
    if not args.continue_pending and not args.episode:
        parser.error("Use explicit --episode or --continue-pending.")
    try:
        cfg = batch.load_batch(args.config)
        if args.continue_pending:
            result = continue_pending(cfg, args.run_id, workers=args.workers,
                                      minimum_queue_gap=128 if args.minimum_queue_gap is None else args.minimum_queue_gap)
        else:
            result = execute(cfg, args.episode, args.run_id, workers=args.workers,
                             dry_run=args.dry_run, minimum_queue_gap=args.minimum_queue_gap or 0)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"Look-ahead setup failed ({type(exc).__name__}); inspect private configuration.\n")
    if args.dry_run:
        print(json.dumps(result, indent=2))
    return int(result.get("state", "").startswith("needs_attention")
               or any(r["status"] == "failed" for r in result.get("episodes", [])))


if __name__ == "__main__":
    raise SystemExit(main())
