"""Quarantined extraction: local ambiguity is assessed, never silently repaired."""
from __future__ import annotations

import copy
from pathlib import Path

from transcription.annotations import load_annotations
from transcription.common import TERMINOLOGY, private_path, read_json, sha256_file
from transcription.source_review import _source
from .schemas import TEXT, IDS, obj, array, enum

VERSION = "knowledge_candidate.v1"
NOTE = obj(kind=enum("uncertainty", "interpretive_caution"), merged_ids=IDS,
           note={**TEXT, "maxLength": 600})
ASSESSMENT = obj(record_id=TEXT, status=enum("usable_with_limits", "unresolved"),
                 rationale=TEXT, supplementary_notes=array(NOTE),
                 source_issue_dispositions=array(obj(
                     issue_id=TEXT,
                     disposition=enum("retained_uncertainty", "not_used_in_claims", "context_not_material"),
                     reason=TEXT)))


def proposal_schema(stage, base):
    result = copy.deepcopy(base)
    if stage == "level1":
        result["properties"]["source_observations"] = array(NOTE)
        result["required"].append("source_observations")
    elif stage == "level2":
        result["properties"]["case_assessments"] = array(ASSESSMENT)
        result["required"].append("case_assessments")
    return result


def load_candidate_source(raw_path, normalized_path, review_path=None):
    raw_path, normalized_path = map(private_path, (raw_path, normalized_path))
    pins = {p: sha256_file(p) for p in (raw_path, normalized_path, TERMINOLOGY)}
    raw, draft, validation = _source(raw_path, normalized_path, pins)
    result = {"episode_id": validation["episode_id"], "raw_sha256": pins[raw_path],
              "normalized_sha256": pins[normalized_path], "review_sha256": None,
              "publication_date": raw["metadata"].get("source_episode_metadata", {}).get("publication_date"),
              "segments": draft["segments"],
              "raw_segments": {s["segment_id"]: s for s in raw["segments"]},
              "extraction_mode": "candidate", "source_acceptance": "not_granted",
              "source_limitations": validation["warnings"],
              "source_recovery": raw["metadata"].get("source_recovery")}
    if review_path:
        path = private_path(review_path)
        pins[path] = sha256_file(path)
        review = read_json(path)
        if (review.get("episode_id") != result["episode_id"]
                or review.get("transcript_sha256") != pins[raw_path]
                or review.get("decision") not in {"accept", "uncertain", "retranscribe"}):
            raise ValueError("Candidate review does not match the exact source.")
        notes = load_annotations(review, path, raw, pins[raw_path])
        blockers = review.get("unresolved_critical_issues", [])
        if not isinstance(blockers, list) or any(not isinstance(v, str) for v in blockers):
            raise ValueError("Malformed prior source review limitations.")
        result.update(review_sha256=pins[path], prior_source_decision=review["decision"],
                      prior_review_limitations=blockers)
        if notes:
            result.update(reviewed_annotations=notes, annotations_sha256=notes["sha256"])
    if any(sha256_file(p) != checksum for p, checksum in pins.items()):
        raise ValueError("Candidate source changed during validation.")
    return result


def source_issues(source, level1=None):
    issues = []
    for note in source.get("reviewed_annotations", {}).get("annotations", []):
        raw_ids = {a["segment_id"] for a in note["anchors"]}
        mids = [s["merged_id"] for s in source["segments"] if raw_ids.intersection(s["source_segment_ids"])]
        issues.append({"issue_id": note["annotation_id"], "merged_ids": mids,
                       "note": note["note"], "kind": note["kind"], "severity": note["severity"],
                       "authority": "reviewer_commentary_not_source"})
    if level1:
        issues.extend({"issue_id": f"L1:O{n}", **note, "authority": "reviewer_commentary_not_source"}
                      for n, note in enumerate(level1["content"]["source_observations"], 1))
    return issues


def _check_notes(notes, allowed):
    for note in notes:
        if not set(note["merged_ids"]) <= allowed:
            raise ValueError("Supplementary note must cite existing source within its case.")


def derive_candidate(stage, content, source, upstream, resolved, qualifications, warnings):
    if stage == "level1":
        _check_notes(content["source_observations"], {s["merged_id"] for s in source["segments"]})
    if stage == "level2":
        cases = {c["record_id"]: c for c in content["cases"]}
        assessments = content["case_assessments"]
        if (len(assessments) != len(cases)
                or {a["record_id"] for a in assessments} != set(cases)):
            raise ValueError("Every candidate case needs exactly one source assessment.")
        issues = source_issues(source, upstream[0])
        for assessment in assessments:
            rid = assessment["record_id"]
            case = cases[rid]
            allowed = set(case["merged_ids"])
            _check_notes(assessment["supplementary_notes"], allowed)
            overlapping = {i["issue_id"] for i in issues if allowed.intersection(i["merged_ids"])}
            dispositions = assessment["source_issue_dispositions"]
            if (len(dispositions) != len(overlapping)
                    or {d["issue_id"] for d in dispositions} != overlapping):
                raise ValueError("Known overlapping source issues require an explicit case-local disposition.")
            unresolved = any(d["disposition"] == "retained_uncertainty" for d in dispositions)
            if unresolved and assessment["status"] != "unresolved":
                raise ValueError("Unresolved meaning-bearing source issues cannot support a usable case assessment.")
            if assessment["status"] == "unresolved" and not case["uncertainty"]:
                raise ValueError("Unresolved candidate cases must retain uncertainty.")
            # Commentary is separate metadata. This catches explicit label leakage,
            # not all semantic misattribution (which still requires model review).
            for field in ("claims", "reasoning_steps", "assumptions", "preferred_actions", "decision_preferences"):
                if any("[Agent clarification]" in value for value in case[field]):
                    raise ValueError("Agent clarification cannot be presented as speaker evidence.")
            resolved[rid]["candidate_source_assessment"] = {
                **copy.deepcopy(assessment), "authority": "model_assessment_not_source",
                "source_issues": [i for i in issues if i["issue_id"] in overlapping],
                "eligible_for_online_reasoning": False}
    for ev in resolved.values():
        ev["extraction_mode"] = "candidate"
    if stage in {"level3", "level4"}:
        all_cases = {c["record_id"]: a["evidence"][c["record_id"]]
                     for a in upstream if a["stage"] == "level2" for c in a["content"]["cases"]}
        for p in content["principles"]:
            q = qualifications[p["principle_id"]]
            blocked = [rid for rid in p["supporting_records"]
                       if all_cases[rid]["candidate_source_assessment"]["status"] == "unresolved"]
            q["eligible_for_online_reasoning"] = False
            q["runtime_mode_recommendation"] = "not_eligible"
            q["source_review_status"] = "candidate_pending_review"
            q["eligibility_reasons"].append("Candidate extraction is not source or semantic publication acceptance.")
            if blocked:
                q["eligible_for_level4"] = False
                q["confidence"]["overall"] = "low"
                q["confidence"]["evidence_strength"] = "low"
                q["eligibility_reasons"].append("Unresolved source cases: " + ", ".join(blocked))
    warnings.append("Quarantined candidate: source/semantic acceptance not granted; reviewer notes are not evidence.")
