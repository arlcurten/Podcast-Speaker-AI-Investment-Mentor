"""Explicit, zero-call recovery of private evidence rejected by the old note scope."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

from transcription.annotations import _reference
from transcription.common import atomic_write_json, child_path, private_path, sha256_file, utc_now
from . import pipeline, schemas

CONFIG_VERSION = "profile_evidence_recovery_config.v1"
VERSION = "profile_recovered_evidence.v1"
OLD_ERROR = "Supplementary commentary must stay within its item's anchors."


def _ref(value):
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError("Recovery references require a private path and explicit SHA-256.")
    pipeline._checksum(value["sha256"])
    return {"path": str(private_path(value["path"])), "sha256": value["sha256"]}


def _config(value):
    if (not isinstance(value, dict)
            or set(value) != {"schema_version", "commentary_scope", "manifest", "request", "result"}
            or value["schema_version"] != CONFIG_VERSION
            or value["commentary_scope"] != schemas.EPISODE_CONTEXT):
        raise ValueError("Use an explicit episode-context evidence recovery configuration.")
    return {**value, **{key: _ref(value[key]) for key in ("manifest", "request", "result")}}


def _read(ref, pins):
    ref = _ref(ref)
    _pin(pins, Path(ref["path"]), ref["sha256"])
    return pipeline._pinned_json(Path(ref["path"]), ref["sha256"], pins)


def _pin(pins, path, checksum):
    if path in pins and pins[path] != checksum:
        raise ValueError("Conflicting recovery provenance pins.")
    pins[path] = checksum


def _recheck(pins):
    if any(sha256_file(path) != checksum for path, checksum in pins.items()):
        raise ValueError("Recovery input changed during validation.")


def _timestamp(value):
    if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
        raise ValueError("Recovery lineage requires an explicit timestamp with timezone.")


def _implementation():
    return {"profile": pipeline._implementation(), "recovery_code_sha256": sha256_file(Path(__file__))}


def _profile_lineage(historical, current):
    if (not isinstance(historical, dict) or set(historical) != {"python", "packages", "code"}
            or not isinstance(historical["python"], str) or not historical["python"]
            or not isinstance(historical["packages"], dict) or not historical["packages"]
            or any(not isinstance(v, str) or not v for v in historical["packages"].values())
            or not isinstance(historical["code"], dict)
            or set(historical["code"]) != set(current["code"])):
        raise ValueError("Original implementation lineage is missing or malformed.")
    for checksum in historical["code"].values():
        pipeline._checksum(checksum)


def _prepare(config, pins, created_at, recovery_implementation=None):
    config = _config(config)
    parent = _read(config["manifest"], pins)
    request = _read(config["request"], pins)
    result = _read(config["result"], pins)
    if not all(isinstance(value, dict) for value in (parent, request, result)):
        raise ValueError("Recovery requires object-shaped manifest and call records.")
    if parent.get("schema_version") != "profile_run.v1" or parent.get("policy") != pipeline.POLICY:
        raise ValueError("Recovery requires the original quarantined profile manifest.")
    old_cfg = pipeline._config(parent["config"])
    old_root = child_path(Path(old_cfg["output_root"]), old_cfg["run_id"])
    if (Path(config["manifest"]["path"]) != old_root / "run_manifest.json"
            or parent["config"] != old_cfg or parent.get("config_digest") != pipeline.digest(old_cfg)
            or any(key in old_cfg for key in ("commentary_scope", "reuse_evidence", "reuse_failed_synthesis"))):
        raise ValueError("Recovery requires an original item-scoped evidence run and matching config digest.")
    current = _implementation()
    _profile_lineage(parent.get("implementation"), current["profile"])
    if recovery_implementation is None:
        recovery_implementation = current
    if (not isinstance(recovery_implementation, dict)
            or set(recovery_implementation) != {"profile", "recovery_code_sha256"}):
        raise ValueError("Recovery implementation lineage is missing or malformed.")
    _profile_lineage(recovery_implementation["profile"], current["profile"])
    pipeline._checksum(recovery_implementation["recovery_code_sha256"])
    # Source pins are part of the original config, not a new acceptance or relabeling.
    for entry in old_cfg["episodes"]:
        for key in ("raw", "draft", "source_review"):
            if entry.get(key) is not None:
                _pin(pins, Path(entry[key]), entry[key + "_sha256"])
        if entry["source_review"] is not None:
            review_path = Path(entry["source_review"])
            review = _read({"path": str(review_path), "sha256": entry["source_review_sha256"]}, pins)
            if review.get("schema_version") == "transcript_review.v2":
                path, checksum = _reference(review["annotations"], review_path)
                notes = _read({"path": str(path), "sha256": checksum}, pins)
                for supporting in notes["supporting_reviews"]:
                    supporting_path, supporting_sha = _reference(supporting, path)
                    _pin(pins, supporting_path, supporting_sha)
    if "catalog" in old_cfg:
        _pin(pins, Path(old_cfg["catalog"]["path"]), old_cfg["catalog"]["sha256"])
    _pin(pins, pipeline.common.TERMINOLOGY, sha256_file(pipeline.common.TERMINOLOGY))
    sources = pipeline._sources(old_cfg)
    eid, attempt = request.get("episode_id"), request.get("attempt")
    if eid not in sources or type(attempt) is not int or not 1 <= attempt <= old_cfg["retry_count"] + 1:
        raise ValueError("Failed evidence episode/attempt is outside its original run.")
    source = sources[eid]
    expected_request = child_path(old_root, "calls", f"{eid}_evidence_{attempt}.request.json")
    expected_result = expected_request.with_name(expected_request.name.replace(".request.json", ".result.json"))
    if Path(config["request"]["path"]) != expected_request or Path(config["result"]["path"]) != expected_result:
        raise ValueError("Recovery call references do not belong to the pinned run/episode/attempt.")
    fingerprint = pipeline.digest({"run": parent, "stage": "evidence", "source": pipeline._meta(source),
                                   "upstream": [], "schema": schemas.schema("evidence"),
                                   "prompt": schemas.PROMPTS["evidence"]})
    previous, previous_calls = None, []
    for number in range(1, attempt + 1):
        if number == attempt:
            req, res, request_sha = request, result, config["request"]["sha256"]
        else:
            refs = {}
            for kind in ("request", "result"):
                path = child_path(old_root, "calls", f"{eid}_evidence_{number}.{kind}.json")
                payload = path.read_bytes()
                refs[kind] = {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest()}
                _pin(pins, path, refs[kind]["sha256"])
                if kind == "request":
                    req = json.loads(payload)
                else:
                    res = json.loads(payload)
            request_sha = refs["request"]["sha256"]
            previous_calls.append(refs)
        context = pipeline._repair_context(pipeline._context("evidence", source, []), previous)
        if (req.get("schema_version") != "profile_call.v1" or res.get("schema_version") != "profile_call.v1"
                or req.get("stage") != "evidence" or req.get("episode_id") != eid or req.get("attempt") != number
                or req.get("schema") != schemas.schema("evidence") or req.get("prompt") != schemas.PROMPTS["evidence"]
                or req.get("context") != context or req.get("fingerprint") != fingerprint
                or res.get("fingerprint") != fingerprint or res.get("request_sha256") != request_sha
                or res.get("status") != "failed" or not isinstance(res.get("error"), dict)):
            raise ValueError("Original call/source context, failure record or fingerprint lineage differs.")
        _timestamp(req.get("created_at"))
        _timestamp(res.get("created_at"))
        previous = res
    error = result["error"]
    if (error != {"phase": "validation", "type": "ValueError", "message": OLD_ERROR}
            or not isinstance(result.get("response"), dict)):
        raise ValueError("Recovery requires a model response rejected by the original item-anchor rule.")
    content = result["response"]
    try:
        pipeline._derive_evidence(content, source)
    except ValueError as exc:
        if str(exc) != OLD_ERROR:
            raise ValueError("Original item-anchor failure cannot be reproduced.") from None
    else:
        raise ValueError("Original response does not reproduce the recorded validation failure.")
    grounding = pipeline._derive_evidence(content, source, schemas.EPISODE_CONTEXT)
    _timestamp(created_at)
    artifact = {"schema_version": VERSION, "stage": "evidence", "status": "recovered_candidate",
                "created_at": created_at, "commentary_scope": schemas.EPISODE_CONTEXT,
                "source": pipeline._meta(source), "content": copy.deepcopy(content), "grounding": grounding,
                "policy": copy.deepcopy(pipeline.POLICY), "model_metadata": copy.deepcopy(result.get("usage")),
                "recovery_config": config, "original_fingerprint": fingerprint,
                "original_error": copy.deepcopy(error), "previous_calls": previous_calls,
                "recovery": {"implementation": copy.deepcopy(recovery_implementation), "model_calls": 0,
                             "content_sha256": pipeline.digest(content),
                             "operation": "unchanged_response_revalidated_with_episode_context"}}
    artifact["fingerprint"] = pipeline.digest({k: v for k, v in artifact.items() if k != "created_at"})
    if _implementation() != current:
        raise ValueError("Recovery implementation changed during validation.")
    _recheck(pins)
    return artifact, old_root


def prepare(config: dict) -> dict:
    """Validate old pinned records and derive a candidate in memory, without writing."""
    artifact, _ = _prepare(config, {}, utc_now())
    return artifact


def load_checked(ref: dict, pins: dict | None = None) -> dict:
    """Read a recovery artifact, recheck its full lineage and recompute its grounding.

    Future synthesis must opt in to this distinct schema via a pinned reference.
    This function does not fabricate a completed model receipt or grant eligibility.
    """
    pins = {} if pins is None else pins
    artifact = _read(ref, pins)
    if not isinstance(artifact, dict) or artifact.get("schema_version") != VERSION:
        raise ValueError("Expected an explicit recovered-evidence candidate artifact.")
    details = artifact.get("recovery")
    if not isinstance(details, dict):
        raise ValueError("Recovery implementation lineage is missing.")
    recorded = details.get("implementation")
    if recorded is None:
        raise ValueError("Recovery implementation lineage is missing.")
    # Preserve historical code identity while checking the contract with today's validator.
    expected, old_root = _prepare(artifact["recovery_config"], pins, artifact.get("created_at"), recorded)
    if Path(_ref(ref)["path"]).is_relative_to(old_root) or artifact != expected:
        raise ValueError("Recovered evidence content, provenance, policy or grounding differs.")
    _recheck(pins)
    return artifact


def apply(config: dict, output: Path) -> dict:
    """Publish one new immutable private candidate; originals always remain failed."""
    output = private_path(output)
    pins = {}
    artifact, old_root = _prepare(config, pins, utc_now())
    if output.is_relative_to(old_root) or output in pins:
        raise ValueError("Recovery must use a distinct output outside the original run and input files.")
    _recheck(pins)
    atomic_write_json(output, artifact, overwrite=False)
    ref = {"path": str(output), "sha256": sha256_file(output)}
    if load_checked(ref, pins) != artifact:
        raise ValueError("Recovery read-back differs; preserve and inspect the candidate.")
    return ref


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apply", "validate"))
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--config", type=Path)
    selection.add_argument("--artifact", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "apply" and (args.config is None or args.output is None):
        parser.error("apply requires --config and --output")
    if (args.artifact is None) != (args.sha256 is None) or (args.command == "validate" and args.output is not None):
        parser.error("--sha256 requires --artifact; --output is only valid with apply")
    try:
        config_pins = {}
        if args.config is not None:
            path = private_path(args.config)
            payload = path.read_bytes()
            config_pins[path] = hashlib.sha256(payload).hexdigest()
            config = json.loads(payload)
            if args.command == "apply":
                if private_path(args.output) == path:
                    raise ValueError("Recovery output cannot overwrite its config.")
                ref = apply(config, args.output)
                artifact = load_checked(ref)
            else:
                ref, artifact = None, prepare(config)
        else:
            ref = _ref({"path": str(args.artifact), "sha256": args.sha256})
            artifact = load_checked(ref)
        _recheck(config_pins)
        report = {"status": "validated" if args.command == "validate" else "created",
                  "schema_version": VERSION, "episode_id": artifact["source"]["episode_id"],
                  "model_calls": 0, "policy": artifact["policy"]}
        if ref is not None:
            report["artifact_sha256"] = ref["sha256"]
        print(json.dumps(report, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "note": "Inspect the private recovery configuration and original call records."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
