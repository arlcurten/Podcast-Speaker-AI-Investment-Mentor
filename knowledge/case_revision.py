"""Hash-bound editorial revision of one candidate L2; never inference or approval."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from transcription.common import atomic_write_json, private_path, safe_id, sha256_file, utc_now
from . import case_release, pipeline
from .provenance import digest
from .schemas import TEXT, IDS, array, enum, obj


FIELDS = ("title", "claims", "reasoning_steps", "assumptions", "conditions", "risks", "exceptions",
          "uncertainty", "contradictions", "preferred_actions", "decision_preferences",
          "view_change_conditions", "speaker_behavior", "tags", "domains", "decision_context")
REF = case_release.REF
ANCHOR = obj(merged_id={"type": "integer", "minimum": 0}, segment_id={"type": "integer", "minimum": 0},
             start={"type": "number", "minimum": 0}, end={"type": "number", "minimum": 0},
             text_sha256=case_release.SHA)
EDIT = obj(record_id=TEXT, field=enum(*FIELDS), expected={}, value={}, primary_evidence_sha256=case_release.SHA,
           anchors=array(ANCHOR, 1))
NOTE = obj(record_id=TEXT, merged_ids=IDS, note=TEXT)
PROPOSAL = obj(schema_version=enum("knowledge_case_revision.v1"), run_id=TEXT, output_root=TEXT,
               parent_config=REF, level1=REF, level2=REF, original_call=REF, review_report=REF,
               reviewer=obj(identity=TEXT, method=TEXT), edits=array(EDIT, 1),
               revision_notes=array(NOTE, 1), revision_notes_sha256=case_release.SHA)
PROPOSAL_V2 = copy.deepcopy(PROPOSAL)
PROPOSAL_V2["properties"].update(schema_version=enum("knowledge_case_revision.v2"),
                                 parent_application=obj(receipt=REF, proposal=REF))
PROPOSAL_V2["required"].append("parent_application")
MAX_REVISIONS = 32


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _path(value):
    path = Path(value)
    _require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
             "Use absolute private paths without symlinks.")
    return private_path(path)


def _read(ref, pins):
    case_release._validate(ref, REF)
    path = _path(ref["path"])
    payload = path.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    _require(checksum == ref["sha256"] and pins.get(path, checksum) == checksum, "Revision input pin mismatch.")
    pins[path] = checksum
    return payload


def _check(pins):
    for path, checksum in pins.items():
        _require(sha256_file(_path(path)) == checksum, "Revision dependency changed during validation.")


def _edit(parent, edits, source):
    result = copy.deepcopy(parent["content"])
    cases = {c["record_id"]: c for c in result["cases"]}
    segments = {s["merged_id"]: s for s in source["segments"]}
    seen = set()
    for edit in edits:
        rid, field = edit["record_id"], edit["field"]
        _require(rid in cases and (rid, field) not in seen, "Unknown case or duplicate edited field.")
        seen.add((rid, field))
        case = cases[rid]
        _require(case[field] == edit["expected"] and edit["value"] != edit["expected"],
                 "Expected field value mismatch or no-op edit.")
        _require(digest(parent["evidence"][rid]) == edit["primary_evidence_sha256"],
                 "Primary evidence digest mismatch.")
        anchors = set()
        for anchor in edit["anchors"]:
            mid, sid = anchor["merged_id"], anchor["segment_id"]
            _require(sid not in anchors and mid in case["merged_ids"]
                     and sid in segments[mid]["source_segment_ids"], "Duplicate or non-primary raw anchor.")
            anchors.add(sid)
            raw = source["raw_segments"][sid]
            _require((anchor["start"], anchor["end"], anchor["text_sha256"]) ==
                     (raw["start"], raw["end"], hashlib.sha256(raw["text"].encode()).hexdigest()),
                     "Raw anchor text/time mismatch.")
        case[field] = copy.deepcopy(edit["value"])
    return result


def _prepare(proposal_ref, *, replay=None, ancestry=(), protected_roots=None):
    if protected_roots is None:
        protected_roots = set()
    proposal_path = _path(proposal_ref["path"])
    _require(proposal_path not in ancestry and len(ancestry) < MAX_REVISIONS,
             "Revision chain cycle or depth limit exceeded.")
    ancestry = (*ancestry, proposal_path)
    pins = {}
    proposal = json.loads(_read(proposal_ref, pins))
    _require(isinstance(proposal, dict), "Revision proposal must be an object.")
    chained = proposal.get("schema_version") == "knowledge_case_revision.v2"
    case_release._validate(proposal, PROPOSAL_V2 if chained else PROPOSAL)
    _require(digest(proposal["revision_notes"]) == proposal["revision_notes_sha256"], "Reviewer notes pin mismatch.")
    for key in ("parent_config", "level1", "level2", "original_call", "review_report"):
        _read(proposal[key], pins)
    _require(_read(proposal["review_report"], pins).strip(), "Empty review report.")
    history = []
    if chained:
        prior = proposal["parent_application"]
        receipt, previous, previous_artifact, prior_pins = _load_application(prior, ancestry, protected_roots)
        _require(proposal["parent_config"] == receipt["extraction_config"]
                 and proposal["level2"] == receipt["revised_level2"]
                 and proposal["level1"] == previous["level1"]
                 and proposal["original_call"] == previous["original_call"],
                 "Revision chain parent config/artifact/L1/original call mismatch.")
        for path, checksum in prior_pins.items():
            _require(pins.get(path, checksum) == checksum, "Conflicting revision chain pins.")
            pins[path] = checksum
        old_meta = previous_artifact["model_metadata"]
        history = copy.deepcopy(old_meta.get("revision_history", []))
        history.append({"application": copy.deepcopy(prior),
                        **{key: copy.deepcopy(old_meta[key]) for key in
                           ("review_report", "reviewer", "revision_notes", "revision_notes_sha256",
                            "revision_implementation_sha256")}})
    # Reuse the file-bound config/source reader, without environment model substitution.
    cfg = case_release._config(proposal["parent_config"]["path"], pins, proposal["parent_config"]["sha256"])
    _require(len(cfg["episodes"]) == 1, "Revision requires exactly one candidate episode.")
    entry = cfg["episodes"][0]
    eid = entry["episode_id"]
    for key in ("raw", "normalized", "review"):
        if entry.get(key):
            _path(entry[key])
            _require(isinstance(entry.get(key + "_sha256"), str), "Explicit source hashes required.")
    source_map = pipeline.sources(cfg)
    case_release._source_dependencies(cfg, source_map, pins)
    source = source_map[eid]
    upstream = []
    for stage in ("level1", "level2"):
        ref = proposal[stage]
        located, expected = pipeline.stage_location(cfg, stage, eid)
        _require(located == _path(ref["path"]) and (expected is None or expected == ref["sha256"]),
                 "Parent config/artifact reference mismatch.")
        artifact, _ = pipeline.checked_artifact(cfg, source_map, stage, source, upstream,
                                                located, ref["sha256"])
        _require(artifact.get("semantic_review_status") == "pending", "Only pending candidate artifacts may be revised.")
        upstream.append(artifact)
    l1, parent = upstream
    call = json.loads(_read(proposal["original_call"], pins))
    if not chained:
        editorial = isinstance(parent["model_metadata"], dict) and (
            parent["model_metadata"].get("provider") == "agent_review_revision")
        _require(not editorial and isinstance(call, dict)
                 and call.get("stage") == "level2" and call.get("episode_id") == eid
                 and call.get("status") == "completed" and call.get("fingerprint") == parent["fingerprint"]
                 and call.get("usage") == parent["model_metadata"]
                 and call.get("request_context_sha256") == digest(pipeline.context(
                     "level2", source, [l1], context_format=pipeline._context_format(cfg))),
                 "Parent L2 lacks a matching completed call record; editorial parents require v2 chain replay.")
    content = _edit(parent, proposal["edits"], source)
    cases = {c["record_id"]: c for c in content["cases"]}
    edited = {e["record_id"] for e in proposal["edits"]}
    _require({n["record_id"] for n in proposal["revision_notes"]} == edited,
             "Reviewer notes must cover exactly the edited cases.")
    for note in proposal["revision_notes"]:
        _require(set(note["merged_ids"]) <= set(cases[note["record_id"]]["merged_ids"]),
                 "Reviewer note outside primary evidence.")
    derived = pipeline.derive("level2", content, source, [l1])
    _require(derived[0] == parent["evidence"], "Revision must preserve primary evidence and historical assessments.")
    revised_cfg = copy.deepcopy(cfg)
    revised_cfg.update(run_id=safe_id(proposal["run_id"]), output_root=str(_path(proposal["output_root"])))
    _require(revised_cfg["run_id"] != cfg["run_id"], "A new run ID is required.")
    root = _path(Path(revised_cfg["output_root"]) / revised_cfg["run_id"])
    _require(root.parent.is_dir() and (replay is not None or not root.exists()),
             "Output parent must exist; output version must be new.")
    old_root = _path(Path(cfg["output_root"]) / cfg["run_id"])
    protected_roots.add(old_root)
    _require(all(not root.is_relative_to(old) and not old.is_relative_to(root) for old in protected_roots)
             and not any(p.is_relative_to(root) for p in pins), "Revision output overlaps original inputs/run.")
    protected_roots.add(root)
    revised_cfg["episodes"][0]["reuse_artifacts"] = {"level1": copy.deepcopy(proposal["level1"])}
    metadata = {
        "provider": "agent_review_revision", "model": None, "model_call_performed": False,
        "model_calls": 0, "usage": None, "cost_usd": None, "proposal": copy.deepcopy(proposal_ref),
        "revision_implementation_sha256": (replay["revision_implementation_sha256"] if replay is not None
                                            else sha256_file(Path(__file__))),
        "parent_config": proposal["parent_config"], "parent_artifact": proposal["level2"],
        "original_model_record": proposal["original_call"], "review_report": proposal["review_report"],
        "reviewer": proposal["reviewer"], "revision_notes": proposal["revision_notes"],
        "revision_notes_sha256": proposal["revision_notes_sha256"],
        "revision_notes_authority": "revision_reviewer_commentary_not_source_or_approval",
        "assessment_scope": "Unchanged historical model diagnostics, not a re-review of revised text.",
        "authority": "editorial_revision_not_model_response_or_source_acceptance",
        "note": "Config model retained for fingerprint/L1 compatibility only; no inference performed."}
    if chained:
        metadata.update(revision_contract=proposal["schema_version"],
                        parent_application=copy.deepcopy(proposal["parent_application"]),
                        revision_history=history, original_model_usage=copy.deepcopy(call["usage"]),
                        original_model_artifact=copy.deepcopy(old_meta.get("original_model_artifact",
                                                                          previous["level2"])),
                        ancestry_validation="known_v1_v2_semantic_replay_not_historical_code_execution")
    prompt, meta, fingerprint = pipeline.stage_inputs(revised_cfg, source_map, "level2", source, [l1])
    artifact = {"schema_version": pipeline.artifact_version(revised_cfg), "stage": "level2", "created_at": utc_now(),
                "fingerprint": fingerprint, "source": meta, "content": content, "generation_prompt": prompt,
                "evidence": derived[0], "qualification": derived[1], "warnings": derived[2],
                "model_metadata": metadata, "semantic_review_status": "pending", "evidence_authority": "level2"}
    _check(pins)
    return proposal, revised_cfg, source_map, l1, artifact, root, pins


def _receipt(proposal, ref, artifact, root):
    version = proposal["schema_version"].rsplit(".", 1)[1]
    return {"schema_version": "knowledge_case_revision_receipt." + version,
              "status": "planned_not_applied", "proposal": ref, "output": str(root),
              "edited_fields": [{"record_id": e["record_id"], "field": e["field"]} for e in proposal["edits"]],
              "revision_notes_sha256": proposal["revision_notes_sha256"],
              "revision_implementation_sha256": artifact["model_metadata"]["revision_implementation_sha256"],
              "source_acceptance": "not_granted", "semantic_review_status": "pending",
              "ready_for_serving": False, "model_calls": 0, "level3_reused": False,
              "level1_action": "referenced", "qualification_action": "freshly_derived"}


def _applied(result, artifact_ref, config_ref, pins, created_at):
    result.update(status="applied_candidate_l2_only", created_at=created_at,
                  revised_level2=artifact_ref, extraction_config=config_ref,
                  dependencies=[{"path": str(p), "sha256": h} for p, h in sorted(pins.items())],
                  through_level2_validation="passed")
    return result


def _load_application(application, ancestry, protected_roots):
    """Replay a pinned recipe, not arbitrary historical executable code or receipts."""
    envelope_pins = {}
    receipt = json.loads(_read(application["receipt"], envelope_pins))
    _require(isinstance(receipt, dict) and receipt.get("proposal") == application["proposal"],
             "Prior receipt/proposal mismatch.")
    case_release._validate(receipt.get("revision_implementation_sha256"), case_release.SHA)
    case_release._validate(receipt.get("created_at"), TEXT)
    for key in ("revised_level2", "extraction_config"):
        case_release._validate(receipt.get(key), REF)
    proposal, cfg, _sources, _l1, expected, root, pins = _prepare(
        application["proposal"], replay=receipt, ancestry=ancestry, protected_roots=protected_roots)
    eid = cfg["episodes"][0]["episode_id"]
    _require(_path(application["receipt"]["path"]) == root / "application_receipt.json"
             and _path(receipt["revised_level2"]["path"]) == root / eid / "level2.json"
             and _path(receipt["extraction_config"]["path"]) == root / "extraction_config.json",
             "Prior application output location mismatch.")
    recorded_cfg = json.loads(_read(receipt["extraction_config"], envelope_pins))
    recorded = json.loads(_read(receipt["revised_level2"], envelope_pins))
    _require(isinstance(recorded, dict), "Prior artifact must be an object.")
    case_release._validate(recorded.get("created_at"), TEXT)
    # Timestamps and recorded code hashes are historical provenance, not recomputed claims.
    expected["created_at"] = recorded["created_at"]
    _require(digest(recorded_cfg) == digest(cfg) and digest(recorded) == digest(expected),
             "Prior application config/artifact differs from semantic replay (including notes/history).")
    expected_receipt = _applied(_receipt(proposal, application["proposal"], expected, root),
                                receipt["revised_level2"], receipt["extraction_config"], pins,
                                receipt["created_at"])
    _require(digest(receipt) == digest(expected_receipt), "Prior application receipt differs from semantic replay.")
    for path, checksum in envelope_pins.items():
        _require(pins.get(path, checksum) == checksum, "Conflicting prior application pins.")
        pins[path] = checksum
    _check(pins)
    return receipt, proposal, recorded, pins


def execute(proposal_path, proposal_sha256, *, apply=False):
    """Default plan is read-only. Application creates a new version, never resumes."""
    ref = {"path": str(_path(proposal_path)), "sha256": proposal_sha256}
    proposal, cfg, sources, l1, artifact, root, pins = _prepare(ref)
    result = _receipt(proposal, ref, artifact, root)
    if not apply:
        return result
    # Exclusive directory creation reserves the version; failures remain for inspection.
    # No backend, pipeline generation, call receipt, L1 copy or L3 is created.
    _check(pins)
    _path(root)
    root.mkdir(exist_ok=False)
    config_path, artifact_path = root / "extraction_config.json", root / artifact["source"]["episode_id"] / "level2.json"
    atomic_write_json(config_path, cfg, overwrite=False)
    atomic_write_json(artifact_path, artifact, overwrite=False)
    checked, checksum = pipeline.checked_artifact(cfg, sources, "level2", sources[artifact["source"]["episode_id"]],
                                                  [l1], artifact_path, sha256_file(artifact_path))
    _require(checked == artifact and pipeline.sources(cfg) == sources, "Revision read-back/source mismatch.")
    validation = pipeline.run(cfg, through="level2", validate_only=True)
    _require(validation["model_calls_this_execution"] == 0 and
             [(a["stage"], a["action"]) for a in validation["artifacts"]] ==
             [("level1", "referenced"), ("level2", "reused")], "Unexpected revision validation stages.")
    _check(pins)
    _applied(result, {"path": str(artifact_path), "sha256": checksum},
             {"path": str(config_path), "sha256": sha256_file(config_path)}, pins, utc_now())
    atomic_write_json(root / "application_receipt.json", result, overwrite=False)
    _check(pins)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--proposal-sha256", required=True)
    parser.add_argument("--apply", action="store_true", help="Create a new candidate version; never approval/publication.")
    args = parser.parse_args(argv)
    print(json.dumps(execute(args.proposal, args.proposal_sha256, apply=args.apply), indent=2))


if __name__ == "__main__":
    main()
