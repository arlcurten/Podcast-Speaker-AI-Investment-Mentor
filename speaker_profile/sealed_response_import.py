"""Explicit zero-provider-call import into a NEW role-aware Profile episode run.

Only first-attempt sealed responses from one finished original episode execution
are supported. Old completed and validation-failed results are revalidated without
changing response or usage. Imported admissions count toward the target lifetime
budget; recovery_import/receipt.json distinguishes them from new provider calls.

Publication uses Linux renameat2(RENAME_NOREPLACE). The destination must not exist;
failed staging directories remain private for inspection. No live model backend,
silent history migration, old receipt rewrite or source approval is performed.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path
import sys
import uuid
from contextlib import ExitStack

from knowledge import corpus_inventory as shared, discovery_runner as core, provenance
from transcription.common import atomic_write_json, child_path, private_path, safe_id, sha256_file
from . import episode_discovery as source_adapter, role_aware_episode_discovery as target_adapter

VERSION = "profile_sealed_response_revalidation_import.v1"


def _ref(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def _configuration(ref, adapter, pins):
    ref = shared._ref(ref)
    cfg = shared._read(ref, pins)
    shared._validate(cfg, adapter.CONFIG)
    safe_id(cfg["run_id"])
    cfg = {**cfg, "output_root": str(private_path(cfg["output_root"])), "inventory": shared._ref(cfg["inventory"])}
    return ref, cfg, child_path(Path(cfg["output_root"]), cfg["run_id"])


def _lock(stack, path, *, existing=False):
    stream = stack.enter_context(path.open("r+" if existing else "a"))
    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _publish(stage, destination):
    # No fallback to replace/rename: an independently created target must survive.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOSYS, "Atomic no-replace directory publication is required.")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))
    fd = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _finished_execution(root, manifest, histories, count, pins):
    starts = list(root.glob("parallel_executions/*/start.json"))
    if len(starts) != 1:
        raise ValueError("Import requires exactly one source execution with start and finish receipts.")
    start_path, finish_path = starts[0], starts[0].with_name("finish.json")
    start, finish = shared._read(_ref(start_path), pins), shared._read(_ref(finish_path), pins)
    completed = sum(bool(h) and h[-1]["status"] == "completed" for h in histories.values())
    from knowledge import parallel_discovery
    if (start.get("schema_version") != "parallel_discovery_execution.v1"
            or start.get("run_sha256") != provenance.digest(manifest)
            or start.get("parallel_sha256") != sha256_file(Path(parallel_discovery.__file__))
            or start.get("initial_admitted_calls") != 0
            or finish.get("start_sha256") != pins[start_path]
            or finish.get("admitted_calls") != count or finish.get("completed_packets") != completed
            or finish.get("max_calls") != manifest["config"]["max_calls"]
            or finish.get("model_calls_this_execution") != count
            or finish.get("status") not in {"completed", "failed_needs_explicit_retry", "stopped_admissions_drained",
                                            "paused_at_packet_limit", "call_budget_exhausted"}):
        raise ValueError("Source finish does not bind a drained, fully accounted execution; inspect instead.")
    return {"start": _ref(start_path), "finish": _ref(finish_path),
            "reported_provider_calls": finish["model_calls_this_execution"]}


def import_responses(source_config_ref, source_manifest_sha256, target_config_ref, *, expected_responses, progress=None):
    """Never invokes a model. Returns only after publishing a complete import.

    The target preparation is the sole inventory load/packet planning pass. The
    source manifest must have exactly that same plan and unchanged old code pins;
    source read_history then independently reconstructs every admitted request.
    """
    if type(expected_responses) is not int or expected_responses < 1:
        raise ValueError("expected_responses must be a positive explicit count.")
    pins = {}
    source_ref, source_cfg, source_root = _configuration(source_config_ref, source_adapter, pins)
    target_ref, target_cfg, target_root = _configuration(target_config_ref, target_adapter, pins)
    if source_root.is_relative_to(target_root) or target_root.is_relative_to(source_root):
        raise ValueError("Source and target discovery roots must be separate, non-nested runs.")
    if target_root.exists():
        raise FileExistsError("Target already exists; inspect the explicit import receipt or use an authorized new version.")
    # Generation inputs must be identical. Run/version/budget changes are explicit
    # target configuration, never a license to reinterpret different input data.
    for key in ("inventory", "model", "reasoning_effort", "timeout_seconds", "max_input_chars", "max_response_chars", "packet_strategy"):
        if source_cfg[key] != target_cfg[key]:
            raise ValueError("Source and target generation inputs must be identical: " + key)
    if expected_responses > target_cfg["max_calls"]:
        raise ValueError("Imported admissions exceed the target lifetime budget.")
    own_sha = sha256_file(Path(__file__))
    source_implementation, target_implementation = source_adapter._implementation(), target_adapter._implementation()
    target_root.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        _lock(stack, source_root / ".run.lock", existing=True)
        _lock(stack, target_root.parent / ("." + target_root.name + ".sealed-import.lock"))
        if target_root.exists():
            raise FileExistsError("Target appeared while acquiring import lock.")
        source_manifest_path = source_root / "run_manifest.json"
        source_manifest_ref = {"path": str(source_manifest_path), "sha256": source_manifest_sha256}
        source_manifest = shared._read(source_manifest_ref, pins)
        if (source_manifest.get("schema_version") != source_adapter.RUN_VERSION
                or source_manifest.get("config_ref") != source_ref or source_manifest.get("config") != source_cfg
                or source_manifest.get("implementation") != source_implementation
                or source_manifest.get("policy") != source_adapter.POLICY):
            raise ValueError("Source manifest does not match its pinned original adapter/configuration.")
        cfg, root, catalog, packets, manifest, prepared_pins, prompt = target_adapter._prepare(target_ref, progress)
        if cfg != target_cfg or root != target_root or manifest["implementation"] != target_implementation:
            raise ValueError("Target changed during preparation.")
        pins.update(prepared_pins)
        if source_manifest["packets"] != packets:
            raise ValueError("Source and target packet plans/contexts differ; this importer cannot migrate them.")
        histories, count = core.read_history(source_adapter, source_root, source_manifest, packets, prompt, catalog, pins)
        if count != expected_responses:
            raise ValueError("Source admission count differs from expected_responses.")
        selected = []
        for packet in packets:
            history = histories.get(packet["packet_id"], [])
            if not history:
                continue
            if len(history) != 1 or history[0]["status"] == "interrupted":
                raise ValueError("Only one sealed first-attempt response per source packet is importable.")
            result = core._result(history[0])
            if result["status"] == "failed" and result["error"]["phase"] != "validation":
                raise ValueError("Provider/source-validation failures are not response-revalidation imports.")
            selected.append((packet, history[0]))
        if len(selected) != expected_responses:
            raise ValueError("Every source admission must have one importable sealed response.")
        source_execution = _finished_execution(source_root, source_manifest, histories, count, pins)

        def check_pins():
            target_adapter._check_pins(pins)
            if (source_adapter._implementation() != source_implementation
                    or target_adapter._implementation() != target_implementation
                    or sha256_file(Path(__file__)) != own_sha):
                raise ValueError("Importer or adapter implementation changed during revalidation.")

        check_pins()
        stage = child_path(target_root.parent, "." + target_root.name + ".sealed-import-" + uuid.uuid4().hex)
        stage.mkdir()
        # This inode becomes the target lock at publication, excluding the normal
        # runner until the fully staged import has been published and checked.
        _lock(stack, stage / ".run.lock")
        atomic_write_json(stage / "run_manifest.json", manifest, overwrite=False)
        pins[stage / "run_manifest.json"] = sha256_file(stage / "run_manifest.json")
        mappings = []
        source_completed = 0
        for packet, entry in selected:
            context = target_adapter._materialize(packet, catalog, pins, prompt, cfg["max_input_chars"])
            original_request = shared._read(_ref(entry["request"]), pins)
            original_result = shared._read({"path": str(entry["result"]), "sha256": entry["result_sha256"]}, pins)
            request = core.build_request(target_adapter, manifest, packet, context, 1, prompt)
            if any(original_request[key] != request[key] for key in ("prompt", "context", "schema", "packet_id", "attempt", "previous_result_digest")):
                raise ValueError("Imported response input differs from its exact original request.")
            derived = core.check_response(target_adapter, original_result["response"], context, cfg)
            if derived != original_result["response"]:
                raise ValueError("Revalidation must not transform the saved response.")
            folder = child_path(stage, packet["packet_id"])
            req, res, seal = [folder / ("attempt_0001." + part + ".json") for part in ("request", "result", "seal")]
            atomic_write_json(req, request, overwrite=False)
            pins[req] = sha256_file(req)
            # Usage belongs to the original generation. The outside-attempt import
            # receipt explicitly says this completed result is a revalidation, not
            # a new provider execution. Core's strict receipt keys stay untouched.
            result = {"schema_version": target_adapter.RESULT_VERSION, "request_sha256": pins[req],
                      "status": "completed", "response": original_result["response"],
                      "usage": original_result["usage"], "error": None}
            core._write_result(req, res, seal, result, manifest, packet["packet_id"], 1, pins)
            old_seal = entry["request"].with_name("attempt_0001.seal.json")
            mappings.append({"packet_id": packet["packet_id"], "kind": "sealed_response_revalidation_not_provider_execution",
                "source_status": original_result["status"],
                "source": {"request": _ref(entry["request"]), "result": _ref(entry["result"]), "seal": _ref(old_seal)},
                "destination": {part: {"path": str(target_root / path.relative_to(stage)), "sha256": pins[path]}
                                for part, path in (("request", req), ("result", res), ("seal", seal))},
                "context_sha256": provenance.digest(context), "response_sha256": provenance.digest(original_result["response"]),
                "usage_sha256": provenance.digest(original_result["usage"]), "response_and_usage_unchanged": True})
            source_completed += original_result["status"] == "completed"
            if progress:
                progress("sealed_response_revalidation", len(mappings), expected_responses, 0, 0.0)
        imported, admitted = core.read_history(target_adapter, stage, manifest, packets, prompt, catalog, pins)
        if admitted != count or sum(bool(h) and h[-1]["status"] == "completed" for h in imported.values()) != count:
            raise ValueError("Staged import failed unchanged runner history replay.")
        receipt = {"schema_version": VERSION, "status": "sealed_responses_revalidated_ready_to_resume",
            "importer_sha256": own_sha, "source_config": source_ref, "source_manifest": source_manifest_ref,
            "source_execution": source_execution, "target_config": target_ref,
            "target_manifest": {"path": str(target_root / "run_manifest.json"), "sha256": pins[stage / "run_manifest.json"]},
            "source_implementation": source_implementation, "target_implementation": target_implementation,
            "imported_admissions": count, "imported_completed_source_responses": source_completed,
            "imported_validation_failed_source_responses": count - source_completed,
            "new_provider_calls": 0, "provider_calls_this_import": 0, "new_provider_usage": None,
            "usage_authority": "Each destination usage is copied exactly from its pinned original provider result, not new usage.",
            "admission_accounting": "Core admitted_calls includes these imported durable requests. Subtract imported_admissions when reporting new-run provider admissions; do not subtract them from the configured lifetime cap.",
            "remaining_packets": len(packets) - count, "mappings": mappings,
            "source_acceptance": "not_granted", "semantic_review_performed": False, "publication_authorized": False}
        receipt_path = stage / "recovery_import" / "receipt.json"
        atomic_write_json(receipt_path, receipt, overwrite=False)
        receipt_sha = sha256_file(receipt_path)
        pins[receipt_path] = receipt_sha
        atomic_write_json(receipt_path.with_name("seal.json"), {
            "schema_version": "profile_revalidation_import_seal.v1", "receipt_sha256": receipt_sha,
            "target_run_sha256": provenance.digest(manifest)}, overwrite=False)
        pins[receipt_path.with_name("seal.json")] = sha256_file(receipt_path.with_name("seal.json"))
        check_pins()
        _publish(stage, target_root)
        pins = {(target_root / path.relative_to(stage) if path.is_relative_to(stage) else path): checksum
                for path, checksum in pins.items()}
        check_pins()
        return {"status": "imported_ready_to_resume", "imported_admissions": count, "new_provider_calls": 0,
                "remaining_packets": len(packets) - count,
                "receipt": {"path": str(target_root / "recovery_import" / "receipt.json"), "sha256": receipt_sha}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-config", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--source-manifest-sha256", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--expected-responses", type=int, required=True)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)

    def progress(phase, done, total, probes, elapsed):
        print(json.dumps({"phase": phase, "completed": done, "total": total,
                          "context_probes": probes, "elapsed_seconds": round(elapsed, 2)}), file=sys.stderr, flush=True)

    result = import_responses({"path": args.source_config, "sha256": args.source_sha256},
        args.source_manifest_sha256, {"path": args.config, "sha256": args.sha256},
        expected_responses=args.expected_responses, progress=progress if args.progress else None)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
