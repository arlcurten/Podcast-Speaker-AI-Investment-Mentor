"""Explicit selected-hypothesis review for private, provisional assisted teaching.

prepare returns review inputs, never approval. publish consumes an explicitly
authored, hash-bound review. read/load_checked replay the candidate on every use.
Review text is an attributed judgment, not semantic certification by this code.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from knowledge.schemas import array, enum, obj
from transcription.common import atomic_write_json, private_path, sha256_file, utc_now, load_project_dotenv
from . import native_evidence as native, native_synthesis as synthesis, pipeline, recover_evidence as pins_api

VERSION = "profile_scoped_release.v1"
REVIEW_VERSION = "profile_scoped_review.v1"
SCOPE = "selected_hypotheses_provisional_native_teaching"
TEXT, REF = native.TEXT, native.REF
SHA = REF["properties"]["sha256"]
IDS = {**array(TEXT, 1), "uniqueItems": True}
LIMITS = array(TEXT, 1)
SOURCE_FINDING = obj(decision=enum("fit_for_scoped_use", "context_only", "claim_bearing_ambiguity"), rationale=TEXT)
SEMANTIC_FINDING = obj(decision=enum("supported_with_limits", "context_only", "unsupported"), rationale=TEXT)
REVIEW_SCHEMA = obj(
    schema_version={"const": REVIEW_VERSION}, scope={"const": SCOPE},
    decision=enum("accept", "reject", "revoked"), candidate=REF,
    reviewer=native.ACTOR, method=TEXT, reviewed_at=TEXT, reports=array(REF, 1),
    independence=enum("independent", "nonblind", "self_review"),
    selected_hypothesis_ids=IDS, bundle_sha256=SHA, limitations=LIMITS,
    hypotheses=array(obj(hypothesis_id=TEXT, hypothesis_sha256=SHA,
        source_fitness=SOURCE_FINDING, semantic_fidelity=SEMANTIC_FINDING, limitations=LIMITS), 1),
    observations=array(obj(evidence_id=TEXT, observation_sha256=SHA,
        source_fitness=SOURCE_FINDING, semantic_fidelity=SEMANTIC_FINDING, limitations=LIMITS), 1),
    context_findings=array(obj(context_ref=TEXT, context_sha256=SHA,
        disposition=enum("retained_limitation", "not_material_to_selected_claims", "blocks_selected_claims"),
        reason=TEXT)))
POLICY = {
    "scope": SCOPE, "provisional": True, "source_acceptance": "not_granted",
    "source_holds": "unchanged", "candidate_policy": "unchanged",
    "global_profile_approval": False, "online_api_approval": False,
    "hypotheses_are_evidence": False, "replaces_level2_speaker_claims": False,
    "review_authority": "reviewer_scoped_use_judgment_not_speaker_evidence",
    "semantic_certification_by_code": False,
    "usage": "Conditional teaching context only; retain source attribution, uncertainty and all contrary sides. "
             "No unconditional persona, current investment advice or inferred independent event count.",
}
RELEASE_SCHEMA = obj(
    schema_version={"const": VERSION}, created_at=TEXT, output_path=TEXT,
    candidate=REF, review=REF, selected_hypothesis_ids=IDS, bundle_sha256=SHA,
    dependencies=array(REF, 1), implementation_sha256=SHA,
    policy={"const": POLICY}, model_calls_this_operation={"const": 0})


def _context_refs(bundle):
    context = bundle["context"]
    refs = {"/source_context/" + eid: row for eid, row in context["source_context"].items()}
    for key in ("known_context", "comparison_ledger", "comparison_coverage"):
        refs.update({f"/{key}/{i}": row for i, row in enumerate(context[key])})
    return refs


def _packet(candidate_ref, selected, pins):
    synthesis._validate(selected, IDS)
    candidate_ref = pins_api._ref(candidate_ref)
    candidate = synthesis.load_checked(candidate_ref, pins)  # Exactly one upstream replay per operation.
    hypotheses = {h["hypothesis_id"]: h for h in candidate["content"]["hypotheses"]}
    if not set(selected) <= hypotheses.keys():
        raise ValueError("Select only existing candidate hypothesis IDs.")
    catalog = {}
    for descriptor in candidate["evidence"].values():
        catalog.update(pipeline._catalog([native._read(descriptor["artifact"], pins)]))
    closure = lambda ids: synthesis.inventory._basis_closure(catalog, ids)
    core = closure(rid for hid in selected for rid in
                   hypotheses[hid]["supporting_evidence"] + hypotheses[hid]["counterevidence"])
    # One incident comparison pass, not a transitive expansion or Cartesian audit.
    coverage = [row for row in candidate["comparison_coverage"] if core.intersection(closure(row["evidence_ids"]))]
    comparison_ids = {row["comparison_id"] for row in coverage}
    comparisons = [row for row in candidate["comparison_ledger"] if row["comparison_id"] in comparison_ids]
    claims = {"hypothesis:" + hid for hid in selected} | {"comparison:" + cid for cid in comparison_ids}
    known = [row for row in candidate["known_context"] if row["claim_id"] in claims]
    required = set(core)
    for row in coverage:
        required.update(row["evidence_ids"])
    for row in known:
        required.update(row["required_evidence_ids"])
    required = closure(required)
    episodes = {catalog[rid]["episode_id"] for rid in required}
    context = {
        "comparison_ledger": comparisons, "comparison_coverage": coverage, "known_context": known,
        "prior_hypotheses": {key: candidate["prior_hypotheses"][key]
                             for key in sorted({row["prior_hypothesis_key"] for row in known})},
        "source_context": {eid: candidate["source_context"][eid] for eid in sorted(episodes)},
        "reading": candidate["reading"], "inventory_gaps": candidate["inventory_gaps"],
        "scope": "Incident context is not additional approved hypotheses or independent observations; "
                 "pending/unresolved context remains visible, not automatically adjudicated.",
    }
    bundle = copy.deepcopy({
        "hypotheses": [hypotheses[hid] for hid in selected],
        "hypothesis_anchors": [row for row in candidate["hypothesis_anchors"] if row["hypothesis_id"] in selected],
        "grounding": {hid: candidate["grounding"]["hypotheses"][hid] for hid in selected},
        "claim_evidence_ids": sorted(core), "context_only_evidence_ids": sorted(required - core),
        "observations": {rid: catalog[rid] for rid in sorted(required)},
        "evidence_provenance": {eid: candidate["evidence"][eid] for eid in sorted(episodes)},
        "evidence_accounting": [row for row in candidate["content"]["evidence_accounting"] if row["evidence_id"] in required],
        "context": context, "candidate_policy": candidate["policy"],
        "candidate_status": candidate["status"], "candidate_authority": candidate["authority"],
        "authorship": candidate["authorship"], "reports": candidate["reports"],
        "model_metadata": candidate["model_metadata"], "adoption": candidate["adoption"],
    })
    packet = {"schema_version": "profile_scoped_review_packet.v1", "candidate": candidate_ref,
        "candidate_created_at": candidate["created_at"], "selected_hypothesis_ids": list(selected),
        "bundle": bundle, "bundle_sha256": pipeline.digest(bundle),
        "required_context_refs": sorted(_context_refs(bundle)),
        "policy": copy.deepcopy(POLICY), "status": "review_inputs_only", "model_calls_this_operation": 0}
    if native._size(packet) > native.MAX_JSON_BYTES:
        raise ValueError("Scoped read bundle exceeds byte limit; choose a narrower selection, never truncate.")
    return packet


def prepare(candidate_ref: dict, selected_hypothesis_ids: list[str]) -> dict:
    """Read-only review packet. No decisions are generated or source holds lifted."""
    pins = {}
    packet = _packet(candidate_ref, selected_hypothesis_ids, pins)
    pins_api._recheck(pins)
    return packet


def _check_findings(rows, expected, identifier, checksum, core):
    actual = synthesis._unique(rows, identifier)
    if actual.keys() != expected.keys():
        raise ValueError("Review must cover exactly every selected hypothesis and bundled observation.")
    for key, row in actual.items():
        if row[checksum] != pipeline.digest(expected[key]):
            raise ValueError("Reviewed hypothesis/observation differs from the pinned source bundle.")
        source, semantic = row["source_fitness"]["decision"], row["semantic_fidelity"]["decision"]
        if source == "claim_bearing_ambiguity" or semantic == "unsupported":
            raise ValueError("Reviewer identifies an unsupported claim or claim-bearing source ambiguity.")
        if key in core and (source != "fit_for_scoped_use" or semantic != "supported_with_limits"):
            raise ValueError("Selected claims and all support/counter/bases need explicit scoped source and semantic findings.")


def _load_review(review_ref, pins):
    review_ref = pins_api._ref(review_ref)
    reviewed = native._read(review_ref, pins)
    synthesis._validate(reviewed, REVIEW_SCHEMA)
    if reviewed["decision"] != "accept":
        raise ValueError("Explicit accepted scoped review required; rejected/revoked reviews cannot publish.")
    report_paths = set()
    for ref in reviewed["reports"]:
        ref = pins_api._ref(ref)
        path = Path(ref["path"])
        if path in report_paths:
            raise ValueError("Review reports must identify distinct private files.")
        report_paths.add(path)
        pins_api._pin(pins, path, ref["sha256"])
    pins_api._recheck(pins)
    packet = _packet(reviewed["candidate"], reviewed["selected_hypothesis_ids"], pins)
    if native._time(reviewed["reviewed_at"]) < native._time(packet["candidate_created_at"]):
        raise ValueError("Review must follow the exact candidate adoption.")
    if reviewed["bundle_sha256"] != packet["bundle_sha256"]:
        raise ValueError("Review is stale or bound to a different selected source/context bundle.")
    bundle = packet["bundle"]
    hypotheses = {row["hypothesis_id"]: row for row in bundle["hypotheses"]}
    _check_findings(reviewed["hypotheses"], hypotheses, "hypothesis_id", "hypothesis_sha256", set(hypotheses))
    _check_findings(reviewed["observations"], bundle["observations"], "evidence_id", "observation_sha256",
                    set(bundle["claim_evidence_ids"]))
    expected = _context_refs(bundle)
    findings = synthesis._unique(reviewed["context_findings"], "context_ref")
    if findings.keys() != expected.keys():
        raise ValueError("Every incident comparison and episode source context requires an explicit finding.")
    for key, row in findings.items():
        if row["context_sha256"] != pipeline.digest(expected[key]):
            raise ValueError("Reviewed comparison/source context has changed.")
        if row["disposition"] == "blocks_selected_claims":
            raise ValueError("Reviewer identifies context that blocks selected claims.")
    pins_api._recheck(pins)
    return reviewed, packet


def _descriptor(review_ref, output, created_at, pins):
    implementation = sha256_file(Path(__file__))
    review_ref = pins_api._ref(review_ref)
    reviewed, packet = _load_review(review_ref, pins)
    path = private_path(output)
    if path in pins:
        raise ValueError("Release cannot replace an input.")
    if native._time(created_at) < native._time(reviewed["reviewed_at"]):
        raise ValueError("Publication cannot predate review.")
    value = {"schema_version": VERSION, "created_at": created_at, "output_path": str(path),
        "candidate": packet["candidate"], "review": review_ref,
        "selected_hypothesis_ids": reviewed["selected_hypothesis_ids"], "bundle_sha256": packet["bundle_sha256"],
        "dependencies": native._refs(pins), "implementation_sha256": implementation,
        "policy": copy.deepcopy(POLICY), "model_calls_this_operation": 0}
    if sha256_file(Path(__file__)) != implementation:
        raise ValueError("Publication implementation changed during validation.")
    pins_api._recheck(pins)
    return value, reviewed, packet


def publish(review_ref: dict, output: str | Path) -> dict:
    """Explicit immutable descriptor; no candidate mutation, generation or default selection."""
    path = private_path(output)
    if path.exists():
        raise FileExistsError("Scoped publications are immutable; choose a new version.")
    pins = {}
    value, _, _ = _descriptor(review_ref, path, utc_now(), pins)
    if private_path(output) != path:
        raise ValueError("Publication output path changed during validation.")
    pins_api._recheck(pins)
    atomic_write_json(path, value, overwrite=False)
    return {"path": str(path), "sha256": sha256_file(path)}


def load_checked(ref: dict) -> dict:
    """Return full reviewed context only after replaying all pinned candidate sources."""
    ref = pins_api._ref(ref)
    value = native._read(ref, {})
    synthesis._validate(value, RELEASE_SCHEMA)
    if value["output_path"] != ref["path"]:
        raise ValueError("Scoped publication path differs from its recorded output.")
    if value["implementation_sha256"] != sha256_file(Path(__file__)):
        raise ValueError("Publication implementation differs; use its historical code for replay.")
    pins = {}
    expected, reviewed, packet = _descriptor(value["review"], ref["path"], value["created_at"], pins)
    if synthesis.review._json(value) != synthesis.review._json(expected):
        raise ValueError("Scoped publication differs from replayed review, dependencies or selection.")
    pins_api._pin(pins, Path(ref["path"]), ref["sha256"])
    pins_api._recheck(pins)
    return {"release": value, "review": reviewed, "bundle": packet["bundle"],
            "policy": copy.deepcopy(POLICY), "model_calls_this_operation": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "publish", "validate", "read", "schema"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--hypothesis", action="append")
    args = parser.parse_args(argv)
    if args.command == "schema":
        if args.input or args.sha256 or args.output or args.hypothesis:
            parser.error("schema takes no input/output options")
        print(json.dumps(REVIEW_SCHEMA, indent=2))
        return 0
    if (not args.input or not args.sha256 or (args.command == "publish") != bool(args.output)
            or (args.command == "prepare") != bool(args.hypothesis)):
        parser.error("All operations need --input/--sha256; only prepare needs --hypothesis, only publish needs --output")
    try:
        load_project_dotenv({"PODCAST_DATA_ROOT", "PIPELINE_DATA_ROOT", "PRIVATE_DOCS_ROOT"})
        ref = {"path": str(args.input), "sha256": args.sha256}
        if args.command == "prepare":
            value = prepare(ref, args.hypothesis)
        elif args.command == "publish":
            value = {"status": "published", "release": publish(ref, args.output), "model_calls_this_operation": 0}
        else:
            value = load_checked(ref)
            if args.command == "validate":
                value = {"status": "validated", "selected_hypothesis_ids": value["release"]["selected_hypothesis_ids"],
                         "policy": POLICY, "model_calls_this_operation": 0}
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "note": "Inspect private review and dependency pins; no scoped-use approval generated.",
                          "model_calls_this_operation": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
