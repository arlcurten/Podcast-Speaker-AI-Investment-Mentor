"""Replayable editorial recovery of invalid typed comparisons, without inference."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from knowledge.schemas import TEXT, array, obj
from transcription.annotations import _reference
from transcription.common import atomic_write_json, child_path, private_path, sha256_file, utc_now
from . import pipeline, recover_evidence, review
from .schemas import QUOTE

VERSION = "profile_recovered_synthesis.v1"
AUTHORITY = "agent_editorial_revision_not_source_evidence"
PROPOSAL_SCHEMA = obj(
    schema_version={"const": "profile_synthesis_recovery.v1"},
    manifest=review.PIN, request=review.PIN, result=review.PIN,
    reviewer=TEXT, method=TEXT, reviewed_at=TEXT, rationale=TEXT,
    corrections=array(obj(
        hypothesis_id=TEXT, relationship_index={"type": "integer", "minimum": 0}, expected={}, reason=TEXT,
        evidence_ids={**array(TEXT, 1), "uniqueItems": True},
        source_anchors={**array(obj(episode_id=TEXT, **QUOTE["properties"]), 1), "uniqueItems": True}), 1))


def _implementation():
    return {"profile": pipeline._implementation(), "recovery_sha256": sha256_file(Path(__file__)),
            "editorial_helpers_sha256": sha256_file(Path(review.__file__)),
            "evidence_checker_sha256": sha256_file(Path(recover_evidence.__file__))}


def _source_pins(cfg, pins):
    for entry in cfg["episodes"]:
        for key in ("raw", "draft", "source_review"):
            if entry.get(key) is not None:
                recover_evidence._pin(pins, Path(entry[key]), entry[key + "_sha256"])
        if entry["source_review"] is not None:
            path = Path(entry["source_review"])
            data = recover_evidence._read({"path": str(path), "sha256": entry["source_review_sha256"]}, pins)
            if data.get("schema_version") == "transcript_review.v2":
                sidecar, checksum = _reference(data["annotations"], path)
                notes = recover_evidence._read({"path": str(sidecar), "sha256": checksum}, pins)
                for ref in notes["supporting_reviews"]:
                    dependency, expected = _reference(ref, sidecar)
                    recover_evidence._pin(pins, dependency, expected)
    if "catalog" in cfg:
        recover_evidence._pin(pins, Path(cfg["catalog"]["path"]), cfg["catalog"]["sha256"])
    recover_evidence._pin(pins, pipeline.common.TERMINOLOGY, sha256_file(pipeline.common.TERMINOLOGY))


def _replay(proposal_ref, output, pins, created_at, implementation=None):
    current = _implementation()
    recorded = current if implementation is None else implementation
    if not isinstance(recorded, dict) or set(recorded) != set(current):
        raise ValueError("Missing synthesis recovery implementation lineage.")
    recover_evidence._profile_lineage(recorded["profile"], current["profile"])
    for key in set(recorded) - {"profile"}:
        pipeline._checksum(recorded[key])
    proposal, proposal_pin = review._read(proposal_ref["path"], {}, proposal_ref["sha256"])
    recover_evidence._pin(pins, Path(proposal_pin["path"]), proposal_pin["sha256"])
    if next(Draft202012Validator(PROPOSAL_SCHEMA).iter_errors(proposal), None) is not None:
        raise ValueError("Invalid profile_synthesis_recovery.v1 proposal.")
    if (any(not proposal[key].strip() for key in ("reviewer", "method", "rationale"))
            or any(not row["reason"].strip() for row in proposal["corrections"])):
        raise ValueError("Editorial recovery requires substantive reviewer identity, method and reasons.")
    recover_evidence._timestamp(proposal["reviewed_at"])
    recover_evidence._timestamp(created_at)
    parent = recover_evidence._read(proposal["manifest"], pins)
    old_cfg = pipeline._config(parent["config"])
    old_root = child_path(Path(old_cfg["output_root"]), old_cfg["run_id"])
    if output.is_relative_to(old_root):
        raise ValueError("Editorial recovery must be outside the original failed run.")
    _source_pins(old_cfg, pins)
    sources = pipeline._sources(old_cfg)
    # This configuration is used only by existing read-only provenance validators.
    cfg = copy.deepcopy(old_cfg)
    cfg.update(run_id="editorial_check_" + pipeline.digest(proposal)[:24], output_root=str(output.parent))
    if not any(key in cfg for key in ("reuse_evidence", "reuse_recovered_evidence")):
        cfg["reuse_evidence"] = {"manifest": proposal["manifest"], "artifacts": {
            e["episode_id"]: {"path": str(child_path(old_root, e["episode_id"], "evidence.json")),
                              "sha256": sha256_file(child_path(old_root, e["episode_id"], "evidence.json"))}
            for e in cfg["episodes"]}}
    cfg["reuse_failed_synthesis"] = {key: proposal[key] for key in ("manifest", "request", "result")}
    cfg = pipeline._config(cfg)
    reused = pipeline._reused_evidence(cfg, sources, pins)
    reused.update(pipeline._reused_recovered_evidence(cfg, sources, pins))
    failed = pipeline._reused_failed_synthesis(cfg, sources, reused, pins)
    if not failed["revalidated_error"]["message"].startswith("Statement/action relationship kinds disagree with evidence"):
        raise ValueError("Only failed statement/action kind comparisons can be editorially recovered.")
    ancestors = [reused[e["episode_id"]] for e in old_cfg["episodes"]]
    catalog = pipeline._catalog(ancestors)
    content = copy.deepcopy(failed["response"])
    hypotheses = {h["hypothesis_id"]: h for h in content["hypotheses"]}
    invalid = {}
    for h in content["hypotheses"]:
        for index, link in enumerate(h["statement_action_relationships"]):
            kinds = {key: catalog[link[key]]["evidence_kind"] for key in ("statement_id", "action_id")}
            if kinds != {"statement_id": "direct_statement", "action_id": "reported_action"}:
                invalid[h["hypothesis_id"], index] = (link, kinds)
    seen, ledger = set(), []
    for correction in proposal["corrections"]:
        key = correction["hypothesis_id"], correction["relationship_index"]
        if key not in invalid or key in seen:
            raise ValueError("Each correction must identify a distinct kind-invalid original relationship.")
        seen.add(key)
        link, kinds = invalid[key]
        if review._json(correction["expected"]) != review._json(link):
            raise ValueError("Expected relationship does not exactly match the original failed response.")
        if set(correction["evidence_ids"]) != {link["statement_id"], link["action_id"]}:
            raise ValueError("A comparison correction must anchor both original evidence IDs, without new references.")
        review._anchors(correction, hypotheses[key[0]], catalog, sources)
        limitation = ("[Reviewer comparison; not source evidence] "
                      f"{link['statement_id']} ({kinds['statement_id']}) / "
                      f"{link['action_id']} ({kinds['action_id']}), original relationship={link['relationship']}. "
                      f"Original comparison: {link['reason']} Reviewer limitation: {correction['reason']}")
        hypotheses[key[0]]["limitations"].append(limitation)
        ledger.append({**copy.deepcopy(correction), "original_evidence_kinds": kinds,
                       "preserved_limitation": limitation, "authority": AUTHORITY})
    if seen != set(invalid):
        raise ValueError("Every invalid typed comparison needs an explicit anchored reviewer correction.")
    for h in content["hypotheses"]:
        h["statement_action_relationships"] = [link for index, link in enumerate(h["statement_action_relationships"])
                                               if (h["hypothesis_id"], index) not in invalid]
    grounding = pipeline._derive_synthesis(content, ancestors)
    result = {"schema_version": VERSION, "stage": "synthesis", "status": "editorial_candidate",
              "authority": AUTHORITY, "created_at": created_at, "additional_model_calls": 0, "model_usage": None,
              "proposal": proposal_pin, "parent": copy.deepcopy(cfg["reuse_failed_synthesis"]),
              "original_fingerprint": failed["fingerprint"], "original_error": copy.deepcopy(failed["error"]),
              "original_model_usage": copy.deepcopy(failed.get("usage")), "implementation": copy.deepcopy(recorded),
              "editorial_review": {key: proposal[key] for key in ("reviewer", "method", "reviewed_at", "rationale")},
              "comparison_ledger": ledger, "source": {"episodes": [pipeline._meta(sources[e["episode_id"]]) for e in old_cfg["episodes"]]},
              "evidence": {e["episode_id"]: (cfg.get("reuse_recovered_evidence", {}).get(e["episode_id"])
                            or cfg["reuse_evidence"]["artifacts"][e["episode_id"]]) for e in cfg["episodes"]},
              "content": content, "grounding": grounding, "policy": copy.deepcopy(pipeline.POLICY)}
    result["fingerprint"] = pipeline.digest({k: v for k, v in result.items() if k != "created_at"})
    if pipeline._sources(old_cfg) != sources or _implementation() != current:
        raise ValueError("Synthesis recovery source or implementation changed during replay.")
    recover_evidence._recheck(pins)
    return result


def prepare(proposal: str | Path, output: str | Path) -> dict:
    path = private_path(proposal)
    return _replay({"path": str(path), "sha256": sha256_file(path)}, private_path(output), {}, utc_now())


def load_checked(ref: dict, pins: dict | None = None) -> dict:
    pins = {} if pins is None else pins
    artifact = recover_evidence._read(ref, pins)
    if not isinstance(artifact, dict) or artifact.get("schema_version") != VERSION:
        raise ValueError("Expected a separately identified recovered synthesis candidate.")
    if not isinstance(artifact.get("implementation"), dict):
        raise ValueError("Missing synthesis recovery implementation lineage.")
    expected = _replay(artifact["proposal"], private_path(ref["path"]), pins,
                       artifact.get("created_at"), artifact["implementation"])
    if review._json(artifact) != review._json(expected):
        raise ValueError("Recovered synthesis differs from independent editorial replay.")
    recover_evidence._recheck(pins)
    return artifact


def apply(proposal: str | Path, output: str | Path) -> dict:
    path = private_path(output)
    proposal_path = private_path(proposal)
    pins = {}
    artifact = _replay({"path": str(proposal_path), "sha256": sha256_file(proposal_path)}, path, pins, utc_now())
    if path in pins:
        raise ValueError("Recovery output cannot replace an input.")
    atomic_write_json(path, artifact, overwrite=False)
    ref = {"path": str(path), "sha256": sha256_file(path)}
    if load_checked(ref, pins) != artifact:
        raise ValueError("Editorial recovery read-back differs.")
    return ref


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apply", "validate"))
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--sha256")
    args = parser.parse_args(argv)
    if args.command == "apply" and (not args.proposal or not args.output or args.artifact or args.sha256):
        parser.error("apply requires only --proposal and --output")
    if args.command == "validate" and (not args.artifact or not args.sha256 or args.proposal or args.output):
        parser.error("validate requires only --artifact and --sha256")
    try:
        ref = apply(args.proposal, args.output) if args.command == "apply" else {
            "path": str(args.artifact), "sha256": args.sha256}
        artifact = load_checked(ref)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "note": "Inspect the private proposal and original failed call records."}))
        return 2
    print(json.dumps({"status": "created" if args.command == "apply" else "validated", "schema_version": VERSION,
                      "artifact_sha256": ref["sha256"], "additional_model_calls": 0,
                      "authority": AUTHORITY, "policy": artifact["policy"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
