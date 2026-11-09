"""Private, evidence-only profile batches with explicit sources and versioned retries."""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import re
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
from pathlib import Path

from knowledge.codex_backend import CodexBackend, ModelCallError
from knowledge.provenance import digest
from transcription.common import atomic_write_json, child_path, private_path, read_json, safe_id, sha256_file, utc_now
from . import pipeline

SOURCE_OPTIONS = ("model", "reasoning_effort", "timeout_seconds", "max_input_chars", "retry_count",
                  "episodes", "reserved_episode_ids", "catalog")
STATES = {"pending", "running", "interrupted", "deferred", "failed", "completed_candidate"}


class IncompatibleBatch(ValueError):
    pass


def _ids(values):
    if not isinstance(values, list):
        raise ValueError("Episode exclusions and reservations must be explicit lists.")
    result = [safe_id(value) for value in values]
    if len(set(result)) != len(result):
        raise ValueError("Duplicate episode exclusion or reservation.")
    return result


def _config(value):
    cfg = copy.deepcopy(value)
    allowed = {"schema_version", "batch_id", "output_root", "excluded_episode_ids", "max_provider_failures",
               *SOURCE_OPTIONS}
    if not isinstance(cfg, dict) or cfg.get("schema_version") != "profile_batch.v1" or set(cfg) - allowed:
        raise ValueError("Expected profile_batch.v1; only transcript source inputs are supported. "
                         "Multi-episode pilot reuse is not supported here: explicitly exclude those episode IDs.")
    safe_id(cfg["batch_id"])
    cfg["output_root"] = str(private_path(cfg["output_root"]))
    root = child_path(Path(cfg["output_root"]), cfg["batch_id"])
    cfg["excluded_episode_ids"] = _ids(cfg.get("excluded_episode_ids", []))
    cfg.setdefault("max_provider_failures", 3)
    if type(cfg["max_provider_failures"]) is not int or cfg["max_provider_failures"] < 1:
        raise ValueError("max_provider_failures must be a positive integer.")
    base = {key: cfg[key] for key in SOURCE_OPTIONS if key in cfg}
    base.update(schema_version="profile_config.v1", run_id="preflight", output_root=str(root / "runs"))
    normalized = pipeline._config(base)
    for key in SOURCE_OPTIONS:
        if key in normalized:
            cfg[key] = normalized[key]
    for entry in cfg["episodes"]:
        for key in ("raw", "draft", "source_review"):
            if entry.get(key) and Path(entry[key]).is_relative_to(root):
                raise ValueError("Transcript inputs must be outside the batch output directory.")
    if "catalog" in cfg and Path(cfg["catalog"]["path"]).is_relative_to(root):
        raise ValueError("Selection catalog must be outside the batch output directory.")
    return cfg


def load_batch(path: Path) -> dict:
    return _config(read_json(private_path(path)))


def _implementation():
    return {"profile": pipeline._implementation(), "batch_sha256": sha256_file(Path(__file__))}


def _manifest(cfg):
    return {"schema_version": "profile_batch_run.v1", "config": cfg, "config_sha256": digest(cfg),
            "implementation": _implementation(), "through": "evidence", "policy": pipeline.POLICY}


def _check_manifest(root, expected, *, live=False):
    path = child_path(root, "batch_manifest.json")
    if path.exists():
        if read_json(path) != expected:
            raise IncompatibleBatch("Profile batch config or code changed; use a new batch_id. No automatic migration.")
    elif root.exists() and any(p.name != ".batch.lock" for p in root.iterdir()):
        raise IncompatibleBatch("Existing batch output lacks its immutable manifest; inspect before a new batch_id.")
    if live and _implementation() != expected["implementation"]:
        raise IncompatibleBatch("Profile implementation changed during execution; use a new batch_id.")


def _episode_config(cfg, entry, root, attempt):
    value = {key: cfg[key] for key in SOURCE_OPTIONS if key in cfg and key != "episodes"}
    value.update(schema_version="profile_config.v1", run_id=f"attempt_{attempt:04d}",
                 output_root=str(child_path(root, "runs", entry["episode_id"])), episodes=[entry],
                 max_calls=1 + cfg["retry_count"])
    return pipeline._config(value)


def _paths(root, eid, attempt):
    name = f"attempt_{attempt:04d}"
    return (child_path(root, "episodes", eid, name + ".json"),
            child_path(root, "runs", eid, name),
            child_path(root, "runs", eid, name, eid, "evidence.json"))


def _view(cfg, entry, root):
    eid = entry["episode_id"]
    folder = child_path(root, "episodes", eid)
    status_path = child_path(root, "episodes", eid, "status.json")
    saved = read_json(status_path) if status_path.exists() else {}
    configs = {}
    for path in folder.glob("attempt_*.json"):
        match = re.fullmatch(r"attempt_([0-9]{4,})\.json", path.name)
        if not match:
            raise ValueError("Unrecognized immutable episode attempt config.")
        attempt = int(match[1])
        path = child_path(root, "episodes", eid, path.name)
        if attempt in configs or read_json(path) != _episode_config(cfg, entry, root, attempt):
            raise IncompatibleBatch("Episode config differs; preserve it and use a new batch_id.")
        configs[attempt] = path
    latest = max(configs, default=0)
    if set(configs) != set(range(1, latest + 1)):
        raise ValueError("Episode attempt history is incomplete; restore it before proceeding.")
    if saved:
        attempt = saved.get("attempt")
        if (saved.get("episode_id") != eid or saved.get("state") not in STATES
                or type(attempt) is not int or not 0 <= attempt <= latest):
            raise ValueError("Episode checkpoint does not match its immutable attempt history.")
        if attempt and saved.get("config") != {"path": str(configs[attempt]), "sha256": sha256_file(configs[attempt])}:
            raise ValueError("Episode config checkpoint pin differs.")
        if saved["state"] == "completed_candidate" and attempt != latest:
            raise ValueError("A completed episode has conflicting subsequent attempts.")
    if saved and saved["attempt"] == latest:
        return {**saved, "model_calls_this_execution": 0}
    return {"episode_id": eid, "state": "interrupted" if latest else "pending", "attempt": latest,
            "model_calls_this_execution": 0}


def _inspect(cfg, entry, root, row, retry_failed):
    eid, attempt = entry["episode_id"], row["attempt"]
    config = _episode_config(cfg, entry, root, attempt or 1)
    _, _, artifact = _paths(root, eid, attempt or 1)
    if row["state"] == "completed_candidate":
        if row.get("artifact") != {"path": str(artifact), "sha256": sha256_file(artifact)}:
            raise ValueError("Completed evidence is missing or changed; never regenerate it automatically.")
    if artifact.exists():
        pipeline.run(config, through="evidence", validate_only=True)
        return {**row, "state": "completed_candidate", "action": "reused",
                "artifact": {"path": str(artifact), "sha256": sha256_file(artifact)}}
    if row["state"] in {"failed", "interrupted", "running"} and not retry_failed:
        return {**row, "action": "needs_explicit_retry"}
    pipeline.run(config, through="evidence", dry_run=True)
    return {**row, "action": "retry" if attempt and row["state"] != "deferred" else "planned"}


def _call_records(root, eid, attempt):
    if not attempt:
        return []
    _, folder, _ = _paths(root, eid, attempt)
    records = []
    for path in sorted((folder / "calls").glob("*.result.json")):
        path = child_path(root, "runs", eid, folder.name, "calls", path.name)
        data = path.read_bytes()
        result = json.loads(data)
        records.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                        "status": result.get("status"), "error": result.get("error"),
                        "model_metadata": result.get("usage")})
    return records


def _save(root, row):
    value = {"schema_version": "profile_batch_episode.v1", "updated_at": utc_now(),
             "through": "evidence", "policy": pipeline.POLICY, **row}
    value["checkpoint_time_ns"] = time.time_ns()
    atomic_write_json(child_path(root, "episodes", row["episode_id"], "status.json"), value)
    return value


def _failure(row, exc, records):
    provider = isinstance(exc, ModelCallError) or any(
        r["status"] == "failed" and (r.get("error") or {}).get("phase") == "model" for r in records[-1:])
    kind = "provider" if provider else "input" if any(
        (r.get("error") or {}).get("phase") == "input" for r in records[-1:]) else "source_or_validation"
    return {**row, "state": "failed", "action": "failed", "error_type": type(exc).__name__,
            "failure_kind": kind, "diagnostic": "Inspect retained call records; retry explicitly or use a new batch_id for changed inputs/code.",
            "call_records": records, "exception_metadata": getattr(exc, "metadata", None),
            "model_calls_this_execution": None if records else 0}


def _run_job(cfg, entry, root, manifest, retry_failed, factory):
    row = _view(cfg, entry, root)
    try:
        _check_manifest(root, manifest, live=True)
        checked = _inspect(cfg, entry, root, row, retry_failed)
        if checked["state"] == "completed_candidate":
            if row["state"] != "completed_candidate":
                path = _paths(root, entry["episode_id"], row["attempt"])[0]
                checked["config"] = {"path": str(path), "sha256": sha256_file(path)}
                return _save(root, checked)
            return checked
        if checked["action"] == "needs_explicit_retry":
            return checked
        with ExitStack() as stack:
            # Retrying uses a new immutable run, while retaining the old run lock.
            if checked["action"] == "retry":
                old_root = _paths(root, entry["episode_id"], row["attempt"])[1]
                if old_root.exists():
                    lock = stack.enter_context((old_root / ".run.lock").open("a"))
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    checked = _inspect(cfg, entry, root, row, retry_failed)
                    if checked["state"] == "completed_candidate":
                        path = _paths(root, entry["episode_id"], row["attempt"])[0]
                        checked["config"] = {"path": str(path), "sha256": sha256_file(path)}
                        return _save(root, checked)
            attempt = row["attempt"] if row["state"] == "deferred" and row["attempt"] else row["attempt"] + 1
            config = _episode_config(cfg, entry, root, attempt)
            path, _, artifact = _paths(root, entry["episode_id"], attempt)
            if path.exists():
                if read_json(path) != config:
                    raise IncompatibleBatch("Existing attempt config differs; use a new batch_id.")
            else:
                atomic_write_json(path, config, overwrite=False)
            row = _save(root, {"episode_id": entry["episode_id"], "state": "running", "attempt": attempt,
                               "config": {"path": str(path), "sha256": sha256_file(path)},
                               "model_calls_this_execution": 0})
            _check_manifest(root, manifest, live=True)
            backend = factory(cfg["model"], child_path(root, "runs", entry["episode_id"], config["run_id"], "tmp"),
                              timeout_seconds=cfg["timeout_seconds"], reasoning_effort=cfg["reasoning_effort"],
                              max_input_chars=cfg["max_input_chars"])
            report = pipeline.run(config, through="evidence", backend=backend)
            _check_manifest(root, manifest, live=True)
            if sha256_file(path) != row["config"]["sha256"]:
                raise IncompatibleBatch("Episode config changed during extraction; use a new batch_id.")
            return _save(root, {**row, "state": "completed_candidate", "action": report["artifacts"][0]["action"],
                               "artifact": {"path": str(artifact), "sha256": sha256_file(artifact)},
                               "model_calls_this_execution": report["model_calls"]})
    except BlockingIOError:
        return _save(root, {**row, "state": "deferred", "action": "episode_busy", "model_calls_this_execution": 0})
    except IncompatibleBatch:
        raise
    except Exception as exc:
        # Never erase a completion pin or turn corruption into permission to regenerate.
        if row["state"] == "completed_candidate":
            return {**row, "state": "validation_failed", "action": "restore_completed_evidence",
                    "error_type": type(exc).__name__, "diagnostic": "Completed checkpoint retained; restore exact pinned bytes."}
        return _save(root, _failure(row, exc, _call_records(root, entry["episode_id"], row["attempt"])))


def _account_provider(circuit, row, limit):
    failed = row.get("action") == "failed" and row.get("failure_kind") == "provider"
    created = row.get("state") == "completed_candidate" and row.get("action") == "created"
    eid, attempt = row["episode_id"], row.get("attempt", 0)
    if not (failed or created) or attempt <= circuit["accounted_attempts"].get(eid, -1):
        return
    circuit["accounted_attempts"][eid] = attempt
    if failed:
        circuit["total_failures"] += 1
        circuit["consecutive_failures"] += 1
        if circuit["consecutive_failures"] >= limit:
            circuit["limit_reached"] = True
    else:
        circuit["consecutive_failures"] = 0


def _provider_circuit(root, rows, limit):
    path = child_path(root, "summary.json")
    circuit = {"total_failures": 0, "consecutive_failures": 0, "limit_reached": False,
               "accounted_attempts": {}}
    if path.exists():
        circuit = copy.deepcopy(read_json(path)["provider_circuit"])
        if (any(type(circuit[k]) is not int or circuit[k] < 0 for k in ("total_failures", "consecutive_failures"))
                or type(circuit["limit_reached"]) is not bool
                or not isinstance(circuit["accounted_attempts"], dict)
                or any(type(a) is not int or a < 0 for a in circuit["accounted_attempts"].values())):
            raise ValueError("Invalid provider circuit checkpoint; restore summary.json before resuming.")
    elif any(r["state"] in {"failed", "completed_candidate"} for r in rows.values()):
        raise ValueError("Missing provider circuit checkpoint; restore summary.json before resuming.")
    # Episode checkpoints can be newer than the aggregate after a process interruption.
    for row in sorted(rows.values(), key=lambda r: r.get("checkpoint_time_ns", 0)):
        _account_provider(circuit, row, limit)
    return circuit


def _report(cfg, rows, workers, command, circuit, stop_reason=None):
    values = list(rows.values())
    complete = bool(values) and all(row["state"] == "completed_candidate" for row in values)
    state = "needs_attention" if stop_reason else "completed_candidate" if complete else "incomplete" if values else "no_work"
    measured = [row.get("model_calls_this_execution", 0) for row in values]
    return {"schema_version": "profile_batch_report.v1", "batch_id": cfg["batch_id"], "command": command,
            "state": state, "through": "evidence", "workers": workers, "updated_at": utc_now(),
            "counts": dict(Counter(row["state"] for row in values)), "episodes": values,
            "pending_episode_ids": [r["episode_id"] for r in values if r["state"] in {"pending", "running", "deferred"}],
            "needs_retry_episode_ids": [r["episode_id"] for r in values if r["state"] in {"failed", "interrupted"}],
            "excluded_episode_ids": cfg["excluded_episode_ids"], "reserved_episode_ids": cfg["reserved_episode_ids"],
            "estimated_new_calls": sum(r.get("action") in {"planned", "retry"} for r in values),
            "model_calls": 0 if command != "run" else sum(measured) if all(type(n) is int for n in measured) else None,
            "provider_failure_count": circuit["total_failures"],
            "consecutive_provider_failures": circuit["consecutive_failures"],
            "provider_circuit": copy.deepcopy(circuit), "stop_reason": stop_reason,
            "provider_failure_policy": "consecutive_exhausted_errors; only_created_evidence_resets_streak; retry_failed_reopens_circuit",
            "validation_scope": "checkpoint_only" if command == "status" else "source_and_evidence_bindings_not_semantic_acceptance",
            "synthesis": "not_run", "policy": pipeline.POLICY}


def execute(config, *, command="plan", workers=1, retry_failed=False, backend_factory=None):
    if command not in {"plan", "run", "status", "validate"}:
        raise ValueError("Choose plan, run, status or validate; this batch only extracts evidence.")
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer.")
    if retry_failed and command not in {"plan", "run"}:
        raise ValueError("Explicit retries apply only to plan or run.")
    cfg = _config(config)
    root = child_path(Path(cfg["output_root"]), cfg["batch_id"])
    manifest = _manifest(cfg)
    _check_manifest(root, manifest)
    entries = [e for e in cfg["episodes"] if e["episode_id"] not in cfg["excluded_episode_ids"]]

    def view(entry, inspect=False):
        try:
            row = _view(cfg, entry, root)
            return _inspect(cfg, entry, root, row, retry_failed) if inspect else row
        except IncompatibleBatch:
            raise
        except Exception as exc:
            return {"episode_id": entry["episode_id"], "state": "validation_failed", "error_type": type(exc).__name__,
                    "diagnostic": "Inspect source pins or immutable attempt/checkpoint files; no regeneration authorized.",
                    "model_calls_this_execution": 0}

    if command != "run":
        rows = {e["episode_id"]: view(e) for e in entries}
        circuit = _provider_circuit(root, rows, cfg["max_provider_failures"])
        if command != "status":
            rows = {e["episode_id"]: view(e, inspect=True) for e in entries}
        _check_manifest(root, manifest, live=True)
        return _report(cfg, rows, workers, command, circuit,
                       "provider_failure_limit" if circuit["limit_reached"] else None)
    root.mkdir(parents=True, exist_ok=True)
    with child_path(root, ".batch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _check_manifest(root, manifest, live=True)
        manifest_path = child_path(root, "batch_manifest.json")
        if not manifest_path.exists():
            atomic_write_json(manifest_path, manifest, overwrite=False)
        rows = {e["episode_id"]: view(e) for e in entries}
        circuit = _provider_circuit(root, rows, cfg["max_provider_failures"])
        if retry_failed:
            circuit.update(consecutive_failures=0, limit_reached=False)
        stop_reason = "provider_failure_limit" if circuit["limit_reached"] else None

        def checkpoint():
            report = _report(cfg, rows, workers, command, circuit, stop_reason)
            atomic_write_json(child_path(root, "summary.json"), report)
            return report

        checkpoint()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending, futures = iter(entries), {}
            while True:
                while not stop_reason and len(futures) < workers:
                    entry = next(pending, None)
                    if entry is None:
                        break
                    if rows[entry["episode_id"]]["state"] == "validation_failed":
                        continue
                    futures[pool.submit(_run_job, cfg, entry, root, manifest, retry_failed,
                                        backend_factory or CodexBackend)] = entry["episode_id"]
                if not futures:
                    break
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                outcomes = []
                for future in done:
                    eid = futures.pop(future)
                    try:
                        row = future.result()
                    except Exception as exc:
                        row = {**rows[eid], "state": "validation_failed", "error_type": type(exc).__name__,
                               "diagnostic": "Inspect immutable checkpoint/config; nothing was overwritten."}
                        if isinstance(exc, IncompatibleBatch):
                            stop_reason = "configuration_or_code_changed_use_new_batch_id"
                    rows[eid] = row
                    outcomes.append(row)
                for row in sorted(outcomes, key=lambda r: r.get("checkpoint_time_ns", 0)):
                    _account_provider(circuit, row, cfg["max_provider_failures"])
                    if circuit["limit_reached"]:
                        stop_reason = stop_reason or "provider_failure_limit"
                checkpoint()
        return checkpoint()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "status", "validate"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = execute(load_batch(args.config), command=args.command, workers=args.workers, retry_failed=args.retry_failed)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        diagnostic = str(exc) if isinstance(exc, IncompatibleBatch) else "Check private config/source pins, locks and immutable call records."
        print(json.dumps({"state": "failed", "error_type": type(exc).__name__, "diagnostic": diagnostic}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    if args.command == "plan":
        return int(bool(result["counts"].get("validation_failed")))
    return int(result["state"] not in {"completed_candidate", "no_work"})


if __name__ == "__main__":
    raise SystemExit(main())
