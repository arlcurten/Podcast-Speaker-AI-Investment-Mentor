"""Opt-in parallel provider execution with unchanged discovery packets and receipts.

Only provider calls run concurrently. Planning, admission, validation and persistence
remain serial under the existing process lock. SIGINT/SIGTERM stop admissions and
drain admitted calls (potentially until the configured provider timeout). Hard kills
leave counted, interrupted requests requiring inspection, never automatic retries.
No measured provider speedup is implied; quotas and serial replay still limit speed.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
from pathlib import Path
import signal
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager

from transcription.common import atomic_write_json, child_path, sha256_file
from . import discovery_runner as core, provenance


@contextmanager
def _signals(stop):
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, lambda *_: stop.set())
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def execute(adapter, config_ref, *, command="plan", workers=2, backend_factory=None,
            max_packets=None, retry_failed=False, stop_event=None):
    """Factory returns a backend per admission; injected backends must be thread-safe.

    A durable request consumes lifetime budget even if the provider never starts.
    All provider exceptions stop admission, without guessing quota error strings.
    Validation failures isolate a packet; retries only target prior failed history.
    """
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer.")
    if command != "run":
        return core.execute(adapter, config_ref, command=command,
                            max_packets=max_packets, retry_failed=retry_failed)
    if max_packets is not None and (type(max_packets) is not int or max_packets < 1):
        raise ValueError("max_packets must be positive.")
    cfg, root, catalog, packets, manifest, pins, prompt = adapter._prepare(config_ref)
    stop = stop_event if stop_event is not None else threading.Event()
    gate = threading.Lock()
    calls = admitted = 0
    implementation_path = Path(__file__).resolve()
    implementation_sha = sha256_file(implementation_path)

    def check_pins():
        adapter._check_pins(pins)
        if (adapter._implementation() != manifest["implementation"]
                or sha256_file(implementation_path) != implementation_sha):
            raise ValueError("Discovery implementation changed during parallel execution.")

    def invoke(context):
        nonlocal calls
        try:
            model = backend_factory() if backend_factory else core.codex_backend.CodexBackend(
                cfg["model"], root / "tmp", timeout_seconds=cfg["timeout_seconds"],
                reasoning_effort=cfg["reasoning_effort"], max_input_chars=cfg["max_input_chars"])
            with gate:
                calls += 1
            response, usage = model.complete(prompt, context, copy.deepcopy(adapter.PROPOSAL))
            return response, usage, None
        except BaseException as exc:
            with gate:
                stop.set()
            return None, getattr(exc, "metadata", None), exc

    root.mkdir(parents=True, exist_ok=True)
    with (root / ".run.lock").open("a") as lock, _signals(stop):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        histories, count = core.read_history(adapter, root, manifest, packets, prompt, catalog, pins)

        def summary(status):
            return {"status": status, "packet_count": len(packets), "admitted_calls": count,
                    "max_calls": cfg["max_calls"], "model_calls_this_execution": calls,
                    "completed_packets": sum(bool(h) and h[-1]["status"] == "completed"
                                             for h in histories.values()),
                    **adapter._counts(catalog), "packet_plan": manifest["packets"],
                    "policy": copy.deepcopy(adapter.POLICY)}

        if any(h and h[-1]["status"] == "interrupted" for h in histories.values()):
            return summary("interrupted_needs_inspection")
        manifest_path = root / "run_manifest.json"
        if not manifest_path.exists():
            atomic_write_json(manifest_path, manifest, overwrite=False)
        pins[manifest_path] = sha256_file(manifest_path)
        receipt_root = child_path(root, "parallel_executions", uuid.uuid4().hex)
        start = {"schema_version": "parallel_discovery_execution.v1",
                 "run_sha256": provenance.digest(manifest), "parallel_sha256": implementation_sha,
                 "adapter": adapter.__name__, "workers": workers, "max_packets": max_packets,
                 "retry_failed": retry_failed, "initial_admitted_calls": count}
        atomic_write_json(receipt_root / "start.json", start, overwrite=False)
        start_sha = sha256_file(receipt_root / "start.json")
        pins[receipt_root / "start.json"] = start_sha
        pending, blocked = [], False
        for packet in packets:
            history = histories.get(packet["packet_id"], [])
            if history and history[-1]["status"] == "completed":
                continue
            if history and (not retry_failed or len(history) > cfg["retry_count"]):
                blocked = True
                if core._result(history[-1])["error"]["phase"] in {"provider", "source_validation"}:
                    stop.set()
                continue
            pending.append(packet)
        status = "failed_needs_explicit_retry" if blocked else "completed"
        inflight, cursor, fatal = {}, 0, None

        def finish(future, job):
            nonlocal status
            packet, history, context, req, res, seal = job
            response, usage, error = future.result()
            phase = "provider"
            if error is not None and not isinstance(error, Exception):
                histories[packet["packet_id"]] = [*history, {"status": "interrupted", "request": req}]
                status = "interrupted_needs_inspection"
                return
            if error is None:
                try:
                    phase = "validation"
                    core.check_response(adapter, response, context, cfg)
                    phase = "source_validation"
                    check_pins()
                except Exception as exc:
                    error = exc
                    if phase == "source_validation":
                        stop.set()
            result = {"schema_version": adapter.RESULT_VERSION, "request_sha256": pins[req],
                      "status": "failed" if error else "completed", "response": response,
                      "usage": usage, "error": {"phase": phase, "message": str(error)[:1200]} if error else None}
            core._write_result(req, res, seal, result, manifest, packet["packet_id"], len(history) + 1, pins)
            histories[packet["packet_id"]] = [*history, {"status": result["status"], "request": req,
                                                       "result": res, "result_sha256": pins[res]}]
            if error and status != "interrupted_needs_inspection":
                status = "failed_needs_explicit_retry"

        with ThreadPoolExecutor(max_workers=workers) as pool:
            while inflight or (cursor < len(pending) and not stop.is_set()):
                # Consume every observable completion before considering replacement work.
                for future in list(inflight):
                    if future.done():
                        job = inflight.pop(future)
                        try:
                            finish(future, job)
                        except BaseException as exc:
                            fatal = fatal or exc
                            stop.set()
                if stop.is_set():
                    if inflight:
                        wait(inflight, return_when=FIRST_COMPLETED)
                    continue
                if cursor < len(pending) and len(inflight) < workers:
                    if count >= cfg["max_calls"]:
                        status = "call_budget_exhausted"
                        cursor = len(pending)
                        continue
                    if max_packets is not None and admitted >= max_packets:
                        status = "paused_at_packet_limit"
                        cursor = len(pending)
                        continue
                    try:
                        packet = pending[cursor]
                        history = histories.get(packet["packet_id"], [])
                        context = adapter._materialize(packet, catalog, pins, prompt, cfg["max_input_chars"])
                        request = core.build_request(adapter, manifest, packet, context, len(history) + 1,
                                                     prompt, core._result(history[-1]) if history else None)
                        if adapter._size(prompt, request["context"]) > cfg["max_input_chars"]:
                            status = "repair_input_overflow"
                            cursor += 1
                            continue
                        check_pins()
                        folder = child_path(root, packet["packet_id"])
                        prefix = f"attempt_{len(history) + 1:04d}"
                        req, res, seal = (folder / f"{prefix}.{part}.json" for part in ("request", "result", "seal"))
                        # The gate linearizes durable admissions against provider failures.
                        with gate:
                            if stop.is_set():
                                continue
                            atomic_write_json(req, request, overwrite=False)
                            count += 1
                            admitted += 1
                            pins[req] = sha256_file(req)
                            future = pool.submit(invoke, request["context"])
                            inflight[future] = (packet, history, context, req, res, seal)
                        cursor += 1
                    except BaseException as exc:
                        fatal = fatal or exc
                        stop.set()
                elif inflight:
                    wait(inflight, return_when=FIRST_COMPLETED)
        if fatal is not None:
            atomic_write_json(receipt_root / "finish.json", {
                "start_sha256": start_sha,
                **summary("aborted_needs_inspection"), "error": str(fatal)[:1200]}, overwrite=False)
            raise fatal
        complete = all(histories.get(p["packet_id"]) and histories[p["packet_id"]][-1]["status"] == "completed"
                       for p in packets)
        if complete and not stop.is_set():
            check_pins()
            artifact = adapter._aggregate(catalog, packets, histories, manifest)
            output = root / "discovery.json"
            if output.exists():
                if core._read_json(output) != artifact:
                    raise ValueError("Existing discovery artifact differs; never overwrite it.")
            else:
                atomic_write_json(output, artifact, overwrite=False)
            check_pins()
            status = "completed"
        elif stop.is_set() and status == "completed":
            status = "stopped_admissions_drained"
        result = summary(status)
        atomic_write_json(receipt_root / "finish.json", {
            "start_sha256": start_sha, **result}, overwrite=False)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "validate"))
    parser.add_argument("--adapter", choices=("knowledge", "profile"), required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--max-packets", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    if args.adapter == "knowledge":
        from . import corpus_discovery as adapter
    else:
        from speaker_profile import corpus_discovery as adapter
    result = execute(adapter, {"path": args.config, "sha256": args.sha256},
                     command=args.command, workers=args.workers,
                     max_packets=args.max_packets, retry_failed=args.retry_failed)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] in {"planned", "validated", "completed"} else 1)


if __name__ == "__main__":
    main()
