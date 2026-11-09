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


def _prepare(proposal_ref):
    pins = {}
    proposal = json.loads(_read(proposal_ref, pins))
    case_release._validate(proposal, PROPOSAL)
    _require(digest(proposal["revision_notes"]) == proposal["revision_notes_sha256"], "Reviewer notes pin mismatch.")
    for key in ("parent_config", "level1", "level2", "original_call", "review_report"):
        _read(proposal[key], pins)
    _require(_read(proposal["review_report"], pins).strip(), "Empty review report.")
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
    _require(call.get("stage") == "level2" and call.get("episode_id") == eid
             and call.get("status") == "completed" and call.get("fingerprint") == parent["fingerprint"]
             and call.get("usage") == parent["model_metadata"]
             and call.get("request_context_sha256") == digest(pipeline.context(
                 "level2", source, [l1], context_format=pipeline._context_format(cfg))),
             "Parent L2 lacks a matching completed call record.")
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
    _require(root.parent.is_dir() and not root.exists(), "Output parent must exist; output version must be new.")
    old_root = _path(Path(cfg["output_root"]) / cfg["run_id"])
    _require(not root.is_relative_to(old_root) and not old_root.is_relative_to(root)
             and not any(p.is_relative_to(root) for p in pins), "Revision output overlaps original inputs/run.")
    revised_cfg["episodes"][0]["reuse_artifacts"] = {"level1": copy.deepcopy(proposal["level1"])}
    metadata = {
        "provider": "agent_review_revision", "model": None, "model_call_performed": False,
        "model_calls": 0, "usage": None, "cost_usd": None, "proposal": copy.deepcopy(proposal_ref),
        "revision_implementation_sha256": sha256_file(Path(__file__)),
        "parent_config": proposal["parent_config"], "parent_artifact": proposal["level2"],
        "original_model_record": proposal["original_call"], "review_report": proposal["review_report"],
        "reviewer": proposal["reviewer"], "revision_notes": proposal["revision_notes"],
        "revision_notes_sha256": proposal["revision_notes_sha256"],
        "revision_notes_authority": "revision_reviewer_commentary_not_source_or_approval",
        "assessment_scope": "Unchanged historical model diagnostics, not a re-review of revised text.",
        "authority": "editorial_revision_not_model_response_or_source_acceptance",
        "note": "Config model retained for fingerprint/L1 compatibility only; no inference performed."}
    prompt, meta, fingerprint = pipeline.stage_inputs(revised_cfg, source_map, "level2", source, [l1])
    artifact = {"schema_version": pipeline.artifact_version(revised_cfg), "stage": "level2", "created_at": utc_now(),
                "fingerprint": fingerprint, "source": meta, "content": content, "generation_prompt": prompt,
                "evidence": derived[0], "qualification": derived[1], "warnings": derived[2],
                "model_metadata": metadata, "semantic_review_status": "pending", "evidence_authority": "level2"}
    _check(pins)
    return proposal, revised_cfg, source_map, l1, artifact, root, pins


def execute(proposal_path, proposal_sha256, *, apply=False):
    """Default plan is read-only. Application creates a new version, never resumes."""
    ref = {"path": str(_path(proposal_path)), "sha256": proposal_sha256}
    proposal, cfg, sources, l1, artifact, root, pins = _prepare(ref)
    result = {"schema_version": "knowledge_case_revision_receipt.v1",
              "status": "planned_not_applied", "proposal": ref, "output": str(root),
              "edited_fields": [{"record_id": e["record_id"], "field": e["field"]} for e in proposal["edits"]],
              "revision_notes_sha256": proposal["revision_notes_sha256"],
              "revision_implementation_sha256": artifact["model_metadata"]["revision_implementation_sha256"],
              "source_acceptance": "not_granted", "semantic_review_status": "pending",
              "ready_for_serving": False, "model_calls": 0, "level3_reused": False,
              "level1_action": "referenced", "qualification_action": "freshly_derived"}
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
    result.update(status="applied_candidate_l2_only", created_at=utc_now(),
                  revised_level2={"path": str(artifact_path), "sha256": checksum},
                  extraction_config={"path": str(config_path), "sha256": sha256_file(config_path)},
                  dependencies=[{"path": str(p), "sha256": h} for p, h in sorted(pins.items())],
                  through_level2_validation="passed")
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
