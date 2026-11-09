"""Explicit case-use review over immutable candidates, never episode acceptance."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from transcription.annotations import _reference
from transcription.common import atomic_write_json, private_path, safe_id, sha256_file, utc_now
from . import candidates, pipeline
from .provenance import digest
from .schemas import array, enum, obj

TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
REF = obj(path=TEXT, sha256=SHA)
TEXTS = array(TEXT)
LIMITS = {**TEXTS, "minItems": 1}
IDS = {"type": "array", "items": {"type": "integer", "minimum": 0}, "uniqueItems": True, "minItems": 1}
NOTE = obj(kind=enum("uncertainty", "interpretive_caution"), merged_ids=IDS, note=TEXT)


def _dispositions(identifier):
    return array(obj(**{identifier: TEXT}, disposition=enum(
        "not_material_to_claims", "retained_limitation", "unresolved_for_claims"), reason=TEXT, claim_refs=TEXTS))


CASE_REVIEW = obj(
    record_id=TEXT, artifact_sha256=SHA, level1_sha256=SHA, case_sha256=SHA,
    source_binding={"type": "object"}, reviewed_merged_ids=IDS,
    source_fitness=obj(decision=enum("fit_for_selected_claims", "unresolved"), rationale=TEXT,
                       reviewed_claim_refs=TEXTS),
    semantic_fidelity=obj(decision=enum("faithful", "needs_revision"), rationale=TEXT),
    issue_dispositions=_dispositions("issue_id"), global_dispositions=_dispositions("limitation_id"),
    omission_dispositions=_dispositions("omission_id"), limitations=LIMITS, supplementary_notes=array(NOTE))
REVIEW_SCHEMA = obj(
    schema_version=enum("knowledge_case_review.v1"), decision=enum("accept", "reject", "revoked"),
    scope=enum("selected_case_use"), reviewer=TEXT, method=TEXT, reviewed_at=TEXT, limitations=LIMITS,
    candidate_config=REF, accepted_case_ids={**TEXTS, "minItems": 1, "uniqueItems": True},
    cases={**array(CASE_REVIEW), "minItems": 1})
RELEASE_SCHEMA = obj(
    schema_version=enum("knowledge_case_release.v1"), created_at=TEXT, scope=enum("selected_case_use"),
    evidence_authority=enum("level2"), source_acceptance=enum("not_granted"),
    candidate_config=REF, review=REF, dependencies=array(REF),
    accepted_case_ids={**TEXTS, "minItems": 1, "uniqueItems": True})
BINDINGS = ("record_id", "artifact_sha256", "level1_sha256", "case_sha256", "source_binding")
CLAIM_FIELDS = ("title", "speaker_attribution", "claims", "reasoning_steps", "assumptions", "conditions", "risks", "exceptions",
                "uncertainty", "contradictions", "preferred_actions", "decision_preferences",
                "view_change_conditions", "speaker_behavior")


def _validate(value, schema):
    if next(Draft202012Validator(schema).iter_errors(value), None) is not None:
        raise ValueError("Scoped case review/release does not conform to its schema.")


def _snapshot(path, pins, expected=None, *, yaml_config=False):
    path = private_path(path)
    payload = path.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    if (expected is not None and checksum != expected) or (path in pins and pins[path] != checksum):
        raise ValueError("Scoped release dependency hash changed.")
    value = yaml.safe_load(payload) if yaml_config else json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Scoped release inputs must be objects.")
    pins[path] = checksum
    return value


def _check_pins(pins):
    for path, checksum in pins.items():
        if sha256_file(private_path(path)) != checksum:
            raise ValueError("Scoped release dependency changed during validation.")


def _ref(path, pins):
    path = private_path(path)
    return {"path": str(path), "sha256": pins[path]}


def _config(path, pins, expected):
    # Parse the pinned bytes, without environment-based model substitution or inference settings.
    cfg = _snapshot(path, pins, expected, yaml_config=True)
    if cfg.get("schema_version") != "knowledge_config.v1" or cfg.get("extraction_mode") != "candidate":
        raise ValueError("Scoped case release requires an explicitly candidate knowledge config.")
    safe_id(cfg["run_id"])
    cfg["output_root"] = str(private_path(cfg["output_root"]))
    if not isinstance(cfg.get("model"), str) or not cfg["model"].strip():
        raise ValueError("Candidate config must pin its original model identity.")
    cfg.setdefault("reasoning_effort", "medium")
    pipeline._context_format(cfg)
    entries = cfg.get("episodes")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Candidate config needs explicit episode sources.")
    ids = [safe_id(e["episode_id"]) for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate configured episode.")
    for entry in entries:
        for key in ("raw", "normalized", "review"):
            entry[key] = str(private_path(entry[key])) if entry.get(key) else None
        reuse = entry.get("reuse_artifacts", {})
        if not isinstance(reuse, dict) or set(reuse) != set(pipeline.STAGES[:len(reuse)]) or "level4" in reuse:
            raise ValueError("Reused artifacts must be a contiguous Level 1-3 prefix.")
        for ref in reuse.values():
            _validate(ref, REF)
            ref["path"] = str(private_path(ref["path"]))
    return cfg


def _source_dependencies(cfg, sources, pins):
    for entry in cfg["episodes"]:
        source = sources[entry["episode_id"]]
        for key in ("raw", "normalized", "review"):
            if entry.get(key):
                _snapshot(entry[key], pins, source[key + "_sha256"])
        if not entry.get("review"):
            continue
        review_path = Path(entry["review"])
        review = _snapshot(review_path, pins, source["review_sha256"])
        owners = [(review_path, review)]
        if "annotations_sha256" in source:
            path, checksum = _reference(review["annotations"], review_path)
            notes = _snapshot(path, pins, checksum)
            owners.append((path, notes))
        for owner, document in owners:
            for ref in document.get("supporting_reviews", []):
                path, checksum = _reference(ref, owner)
                _snapshot(path, pins, checksum)


def _claim_refs(case):
    refs = []
    for field in CLAIM_FIELDS:
        value = case[field]
        refs.extend([f"/{field}/{i}" for i in range(len(value))] if isinstance(value, list) else [f"/{field}"])
    refs.extend(f"/decision_context/{key}" for key, value in case["decision_context"].items() if value is not None)
    return refs


def _globals(source):
    result = [{"limitation_id": f"{key}:{i}", "value": value}
              for key in ("source_limitations", "prior_review_limitations")
              for i, value in enumerate(source.get(key, []))]
    result.extend({"limitation_id": key, "value": source[key]}
                  for key in ("prior_source_decision", "source_recovery") if source.get(key) is not None)
    if source.get("review_sha256") is None:
        result.append({"limitation_id": "source_review", "value": "not_supplied"})
    return result


def _load_chain(config_path, pins, expected=None):
    cfg = _config(config_path, pins, expected)
    sources = pipeline.sources(cfg)
    _source_dependencies(cfg, sources, pins)
    stages = pipeline.plan_stages(cfg, sources, "level2", allow_pinned_prompts=True)
    if any(s["action"] == "planned" for s in stages):
        raise ValueError("Missing candidate L1/L2 artifact; scoped release never generates knowledge.")
    artifacts = {}
    for stage in stages:
        artifact = _snapshot(stage["artifact_path"], pins, stage["sha256"])
        artifacts[(artifact["source"]["episode_id"], stage["stage"])] = (artifact, stage["sha256"])
    catalog = {}
    for eid, source in sources.items():
        l1, l1_sha = artifacts[(eid, "level1")]
        l2, l2_sha = artifacts[(eid, "level2")]
        for case in l2["content"]["cases"]:
            rid, mids = case["record_id"], set(case["merged_ids"])
            issues = [i for i in candidates.source_issues(source, l1) if mids.intersection(i["merged_ids"])]
            omissions = [{"omission_id": f"{d['thread_id']}:omission:{n}", "thread_id": d["thread_id"], **note}
                        for d in l2["content"]["thread_dispositions"] if d["thread_id"] in case["thread_ids"]
                        for n, note in enumerate(d["unextracted_regions"])]
            catalog[rid] = {
                "record_id": rid, "artifact_sha256": l2_sha, "level1_sha256": l1_sha,
                "case_sha256": digest(case), "source_binding": pipeline._source_meta(source),
                "case": case, "evidence": l2["evidence"][rid], "required_claim_refs": _claim_refs(case),
                "source_assessment": l2["evidence"][rid]["candidate_source_assessment"],
                "source_issues": issues, "global_limitations": _globals(source), "omissions": omissions,
                "source_regions": [copy.deepcopy(s) for s in source["segments"] if s["merged_id"] in mids]}
    return catalog


def review_packet(candidate_config_path, case_ids):
    """Read-only private review inputs. This returns no approval or review template decision."""
    if (not isinstance(case_ids, list) or not case_ids or any(not isinstance(r, str) for r in case_ids)
            or len(case_ids) != len(set(case_ids))):
        raise ValueError("Choose explicit unique case IDs.")
    pins = {}
    catalog = _load_chain(candidate_config_path, pins)
    if any(r not in catalog for r in case_ids):
        raise ValueError("Unknown candidate case ID.")
    result = {"schema_version": "knowledge_case_packet.v1", "candidate_config": _ref(candidate_config_path, pins),
              "cases": [copy.deepcopy(catalog[r]) for r in case_ids], "model_calls": 0,
              "scope": "Review inputs only; no case approval, episode acceptance or publication."}
    _check_pins(pins)
    return result


def _disposition_check(rows, known, identifier, claim_refs, *, critical=False):
    expected = {item[identifier]: item for item in known}
    ids = [row[identifier] for row in rows]
    if len(expected) != len(known) or len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError("Every in-scope issue, global limitation and omission needs exactly one disposition.")
    for row in rows:
        refs = row["claim_refs"]
        if len(refs) != len(set(refs)) or not set(refs) <= set(claim_refs):
            raise ValueError("Disposition contains invalid claim references.")
        decision = row["disposition"]
        if decision == "unresolved_for_claims":
            raise ValueError("Unresolved claim-bearing ambiguity blocks case use.")
        if decision == "not_material_to_claims" and refs:
            raise ValueError("An unrelated issue cannot also be assigned to a case claim.")
        if decision == "retained_limitation" and not refs:
            raise ValueError("A retained limitation must identify affected case fields.")
        if critical and expected[row[identifier]].get("severity") == "critical" and decision != "not_material_to_claims":
            raise ValueError("A claim-bearing critical source issue cannot be approved as a limitation.")


def _reviewed(review, catalog, review_ref):
    if review["decision"] != "accept":
        raise ValueError("Scoped case-use review is not accepted or has been revoked.")
    try:
        timestamp = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Scoped review requires an ISO timestamp with timezone.") from exc
    if timestamp.tzinfo is None:
        raise ValueError("Scoped review timestamp requires timezone.")
    selected = review["accepted_case_ids"]
    if [r["record_id"] for r in review["cases"]] != selected or any(r not in catalog for r in selected):
        raise ValueError("Accepted case IDs must exactly match the reviewed cases in order.")
    cases = {}
    for row in review["cases"]:
        packet = catalog[row["record_id"]]
        if any(row[key] != packet[key] for key in BINDINGS):
            raise ValueError("Reviewed case/source/artifact binding differs from the original candidate.")
        if set(row["reviewed_merged_ids"]) != set(packet["case"]["merged_ids"]):
            raise ValueError("Review must cover all source regions of the selected case.")
        fitness = row["source_fitness"]
        refs = fitness["reviewed_claim_refs"]
        if (fitness["decision"] != "fit_for_selected_claims" or row["semantic_fidelity"]["decision"] != "faithful"
                or len(refs) != len(set(refs)) or set(refs) != set(packet["required_claim_refs"])):
            raise ValueError("All selected case fields require explicit source fitness and semantic review.")
        _disposition_check(row["issue_dispositions"], packet["source_issues"], "issue_id", refs, critical=True)
        _disposition_check(row["global_dispositions"], packet["global_limitations"], "limitation_id", refs)
        _disposition_check(row["omission_dispositions"], packet["omissions"], "omission_id", refs)
        for note in row["supplementary_notes"]:
            if not set(note["merged_ids"]) <= set(row["reviewed_merged_ids"]):
                raise ValueError("Review commentary must be anchored within the reviewed case.")
        evidence = copy.deepcopy(packet["evidence"])
        scoped = {**copy.deepcopy(row), "schema_version": review["schema_version"], "scope": review["scope"],
                  "decision": review["decision"],
                  "reviewer": review["reviewer"], "method": review["method"], "reviewed_at": review["reviewed_at"],
                  "review_sha256": review_ref["sha256"], "review_limitations": review["limitations"],
                  "authority": "reviewer_case_use_approval_not_speaker_evidence",
                  "source_acceptance": "not_granted", "usage": "case_only",
                  "source_issues": packet["source_issues"], "global_limitations": packet["global_limitations"],
                  "omissions": packet["omissions"]}
        notes = [{**copy.deepcopy(n), "authority": authority}
                 for values, authority in ((packet["source_assessment"]["supplementary_notes"], "model_assessment_not_source"),
                                           (row["supplementary_notes"], "reviewer_commentary_not_source")) for n in values]
        cases[row["record_id"]] = {**copy.deepcopy(packet["case"]), "episode_id": packet["source_binding"]["episode_id"],
            "publication_date": packet["source_binding"]["publication_date"], "evidence": evidence,
            "knowledge_artifact_sha256": packet["artifact_sha256"],
            "source_assessment": copy.deepcopy(packet["source_assessment"]), "supplementary_notes": notes,
            "scoped_review": scoped}
    return cases


def _load_review(config_ref, review_ref):
    pins = {}
    review = _snapshot(review_ref["path"], pins, review_ref["sha256"])
    _validate(review, REVIEW_SCHEMA)
    if review["candidate_config"] != config_ref:
        raise ValueError("Scoped review is bound to a different candidate config.")
    catalog = _load_chain(config_ref["path"], pins, config_ref["sha256"])
    cases = _reviewed(review, catalog, review_ref)
    _check_pins(pins)
    return review, cases, pins


def _validated_payload(data):
    """Return approved cases and the exact dependency pins checked for this leaf."""
    data = copy.deepcopy(data)
    _validate(data, RELEASE_SCHEMA)
    review, cases, pins = _load_review(data["candidate_config"], data["review"])
    expected = [_ref(path, pins) for path in sorted(pins)]
    if data["dependencies"] != expected or data["accepted_case_ids"] != review["accepted_case_ids"]:
        raise ValueError("Scoped release dependency/selection manifest differs from its reviewed inputs.")
    _check_pins(pins)
    return cases, pins


def load_payload(data):
    """Validate all private dependencies before returning approved cases and no principles."""
    cases, _ = _validated_payload(data)
    return cases, {}


def publish(candidate_config_path, review_path, output):
    """Publish a new descriptor only after explicit case-use approval; no inference."""
    config_path, review_path, output = map(private_path, (candidate_config_path, review_path, output))
    if output.exists():
        raise FileExistsError("Scoped releases are immutable; choose a new output version.")
    pins = {}
    _snapshot(config_path, pins, yaml_config=True)
    _snapshot(review_path, pins)
    config_ref, review_ref = _ref(config_path, pins), _ref(review_path, pins)
    review, cases, pins = _load_review(config_ref, review_ref)
    data = {"schema_version": "knowledge_case_release.v1", "created_at": utc_now(), "scope": "selected_case_use",
            "evidence_authority": "level2", "source_acceptance": "not_granted", "candidate_config": config_ref,
            "review": review_ref, "dependencies": [_ref(p, pins) for p in sorted(pins)],
            "accepted_case_ids": review["accepted_case_ids"]}
    load_payload(data)
    atomic_write_json(output, data, overwrite=False)
    stored = _snapshot(output, pins)
    if stored != data:
        raise ValueError("Published scoped release read-back differs.")
    load_payload(stored)
    _check_pins(pins)
    return {"release": str(output), "sha256": pins[output], "case_count": len(cases), "principle_count": 0}
