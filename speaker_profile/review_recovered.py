"""Explicit zero-call prose revisions of recovered synthesis candidates only."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from knowledge import schemas as knowledge_schemas
from knowledge.schemas import TEXT, obj
from transcription.common import atomic_write_json, child_path, private_path, sha256_file, utc_now
from . import pipeline, recover_evidence, recover_synthesis, review

VERSION = "profile_reviewed_recovered_revision.v1"
PROPOSAL_VERSION = "profile_recovered_synthesis_revision.v1"
AUTHORITY = review.AUTHORITY
PROPOSAL_SCHEMA = obj(
    schema_version={"const": PROPOSAL_VERSION}, parent_artifact=review.PIN,
    reviewer=TEXT, method=TEXT, rationale=TEXT,
    edits=copy.deepcopy(review.PROPOSAL_SCHEMA["properties"]["edits"]))


def _implementation():
    return {"profile": pipeline._implementation(),
            **{key: sha256_file(Path(module.__file__)) for key, module in {
                "editorial_helpers_sha256": review, "recovery_sha256": recover_synthesis,
                "evidence_checker_sha256": recover_evidence, "knowledge_schemas_sha256": knowledge_schemas}.items()},
            "revision_sha256": sha256_file(Path(__file__))}


def _replay(proposal_ref, output, pins, created_at, implementation=None):
    current = _implementation()
    recorded = current if implementation is None else implementation
    if not isinstance(recorded, dict) or set(recorded) != set(current):
        raise ValueError("Missing recovered revision implementation lineage.")
    recover_evidence._profile_lineage(recorded["profile"], current["profile"])
    for key in set(recorded) - {"profile"}:
        pipeline._checksum(recorded[key])
    recover_evidence._timestamp(created_at)
    proposal, proposal_pin = review._read(proposal_ref["path"], {}, proposal_ref["sha256"])
    recover_evidence._pin(pins, Path(proposal_pin["path"]), proposal_pin["sha256"])
    if next(Draft202012Validator(PROPOSAL_SCHEMA).iter_errors(proposal), None) is not None:
        raise ValueError("Invalid explicit recovered-parent revision proposal.")
    if (any(not proposal[key].strip() for key in ("reviewer", "method", "rationale"))
            or any(not edit["reason"].strip() for edit in proposal["edits"])):
        raise ValueError("Editorial revisions require substantive reviewer identity, method and reasons.")
    parent_ref = recover_evidence._ref(proposal["parent_artifact"])
    parent = recover_synthesis.load_checked(parent_ref, pins)
    manifest = recover_evidence._read(parent["parent"]["manifest"], pins)
    cfg = pipeline._config(manifest["config"])
    if output.is_relative_to(child_path(Path(cfg["output_root"]), cfg["run_id"])):
        raise ValueError("Editorial revision must be outside the original failed run.")
    sources = pipeline._sources(cfg)
    upstream = [recover_evidence._read(parent["evidence"][e["episode_id"]], pins) for e in cfg["episodes"]]
    if review._json(parent["grounding"]) != review._json(pipeline._derive_synthesis(parent["content"], upstream)):
        raise ValueError("Recovered parent grounding changed during replay.")
    content = copy.deepcopy(parent["content"])
    hypotheses = {h["hypothesis_id"]: h for h in content["hypotheses"]}
    catalog, seen = pipeline._catalog(upstream), {}
    for edit in proposal["edits"]:
        hid = edit["hypothesis_id"]
        if hid not in hypotheses:
            raise ValueError("Unknown hypothesis ID.")
        hypothesis = hypotheses[hid]
        container, key, parts = review._target(hypothesis, edit["field"])
        for previous in seen.setdefault(hid, []):
            if parts[:len(previous)] == previous or previous[:len(parts)] == parts:
                raise ValueError("Duplicate or conflicting editorial paths.")
        seen[hid].append(parts)
        if review._json(container[key]) != review._json(edit["expected"]):
            raise ValueError("Expected old value does not exactly match the recovered parent.")
        if parts[0] == "statement_action_relationships" and key == "relationship" and edit["value"] != "unresolved":
            raise ValueError("Relationship revisions may only downgrade to unresolved.")
        review._anchors(edit, hypothesis, catalog, sources)
        container[key] = copy.deepcopy(edit["value"])
    grounding = pipeline._derive_synthesis(content, upstream)
    if review._json(content["evidence_accounting"]) != review._json(parent["content"]["evidence_accounting"]):
        raise ValueError("Editorial revisions cannot change evidence accounting.")
    # Whole-list edits must not erase, rewrite or relocate recovered comparison caveats.
    originals = {h["hypothesis_id"]: h for h in parent["content"]["hypotheses"]}
    for row in parent["comparison_ledger"]:
        before = originals[row["hypothesis_id"]]["limitations"]
        after = hypotheses[row["hypothesis_id"]]["limitations"]
        preserved = row["preserved_limitation"]
        positions = [index for index, text in enumerate(before) if text == preserved]
        if (not positions or after.count(preserved) != len(positions)
                or any(index >= len(after) or after[index] != preserved for index in positions)):
            raise ValueError("Recovered comparison limitations must remain unchanged and in place.")
    result = {"schema_version": VERSION, "stage": "synthesis", "status": "editorial_candidate",
              "authority": AUTHORITY, "created_at": created_at, "additional_model_calls": 0, "model_usage": None,
              "output_path": str(output), "proposal": proposal_pin, "parent_artifact": parent_ref,
              "implementation": copy.deepcopy(recorded),
              "editorial_review": {key: copy.deepcopy(proposal[key]) for key in ("reviewer", "method", "rationale", "edits")},
              "recovery_editorial_review": copy.deepcopy(parent["editorial_review"]),
              **{key: copy.deepcopy(parent[key]) for key in (
                  "parent", "original_fingerprint", "original_error", "original_model_usage",
                  "comparison_ledger", "source", "evidence", "policy")},
              "content": content, "grounding": grounding}
    result["fingerprint"] = pipeline.digest({key: value for key, value in result.items() if key != "created_at"})
    if (recover_synthesis.load_checked(parent_ref, pins) != parent
            or pipeline._sources(cfg) != sources or _implementation() != current):
        raise ValueError("Recovered parent, sources or implementation changed during revision.")
    recover_evidence._recheck(pins)
    return result


def prepare(proposal: str | Path, output: str | Path) -> dict:
    path = private_path(proposal)
    return _replay({"path": str(path), "sha256": sha256_file(path)}, private_path(output), {}, utc_now())


def load_checked(ref: dict, pins: dict | None = None) -> dict:
    """Replay with current validators while retaining pinned historical implementation metadata."""
    pins = {} if pins is None else pins
    artifact = recover_evidence._read(ref, pins)
    if (not isinstance(artifact, dict) or artifact.get("schema_version") != VERSION
            or not isinstance(artifact.get("implementation"), dict)):
        raise ValueError("Expected an explicit recovered-parent revision with implementation lineage.")
    expected = _replay(artifact["proposal"], private_path(ref["path"]), pins,
                       artifact.get("created_at"), artifact["implementation"])
    if review._json(artifact) != review._json(expected):
        raise ValueError("Recovered-parent revision differs from independent editorial replay.")
    recover_evidence._recheck(pins)
    return artifact


def apply(proposal: str | Path, output: str | Path) -> dict:
    path, proposal_path = private_path(output), private_path(proposal)
    if path.exists():
        raise FileExistsError("Editorial output already exists; choose a new version.")
    pins = {}
    artifact = _replay({"path": str(proposal_path), "sha256": sha256_file(proposal_path)}, path, pins, utc_now())
    if path in pins or private_path(output) != path:
        raise ValueError("Editorial output cannot replace an input or change its path.")
    atomic_write_json(path, artifact, overwrite=False)
    ref = {"path": str(path), "sha256": sha256_file(path)}
    if load_checked(ref, pins) != artifact:
        raise ValueError("Editorial revision read-back differs.")
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
                          "note": "Inspect the private proposal and pinned recovered parent."}))
        return 2
    print(json.dumps({"status": "created" if args.command == "apply" else "validated", "schema_version": VERSION,
                      "artifact_sha256": ref["sha256"], "additional_model_calls": 0,
                      "authority": AUTHORITY, "policy": artifact["policy"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
