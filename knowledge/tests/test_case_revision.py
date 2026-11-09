"""Synthetic editorial revisions only; no real sources, backend or approval."""
import copy
import hashlib
import json
import socket
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge import case_release, case_revision as revision, pipeline
from knowledge.codex_backend import CodexBackend
from knowledge.provenance import digest
from knowledge.tests.test_candidate_extraction import CandidateModel
from knowledge.tests.test_pipeline import cfg
from transcription.common import PROJECT_ROOT, atomic_write_json, read_json, sha256_file


def ref(path):
    return {"path": str(path), "sha256": sha256_file(path)}


@pytest.fixture
def prepared(cfg, tmp_path, monkeypatch):
    cfg.update(extraction_mode="candidate", run_id="parent_candidate")
    cfg["episodes"] = cfg["episodes"][:1]
    entry = cfg["episodes"][0]
    entry["review"] = None
    for key in ("raw", "normalized"):
        entry[key + "_sha256"] = sha256_file(Path(entry[key]))
    config = tmp_path / "parent.json"
    atomic_write_json(config, cfg)
    pipeline.run(cfg, through="level2", backend=CandidateModel())
    root = Path(cfg["output_root"]) / cfg["run_id"]
    l1, l2 = root / "SYN1/level1.json", root / "SYN1/level2.json"
    parent = read_json(l2)
    report = tmp_path / "review.md"
    report.write_text("Synthetic text review; neither source nor semantic approval.\n")
    raw = read_json(Path(entry["raw"]))["segments"][0]
    anchor = {"merged_id": 0, "segment_id": raw["segment_id"], "start": raw["start"], "end": raw["end"],
              "text_sha256": hashlib.sha256(raw["text"].encode()).hexdigest()}
    notes = [{"record_id": "SYN1:R1", "merged_ids": [0],
              "note": "Reviewer commentary: this is a conditional statement, not an executed action. "
                      "The original model assessment is historical, not a review of revised text."}]
    proposal = {"schema_version": "knowledge_case_revision.v1", "run_id": "new_candidate",
                "output_root": str(tmp_path), "parent_config": ref(config), "level1": ref(l1), "level2": ref(l2),
                "original_call": ref(root / "calls/SYN1_level2_2.json"), "review_report": ref(report),
                "reviewer": {"identity": "Synthetic agent", "method": "Text-only nonblind synthetic review"},
                "edits": [{"record_id": "SYN1:R1", "field": "title", "expected": parent["content"]["cases"][0]["title"],
                           "value": "Synthetic conditional preference", "primary_evidence_sha256": digest(parent["evidence"]["SYN1:R1"]),
                           "anchors": [anchor]}], "revision_notes": notes, "revision_notes_sha256": digest(notes)}
    path = tmp_path / "proposal.json"
    atomic_write_json(path, proposal)
    forbidden = Mock(side_effect=AssertionError("No backend/network/subprocess permitted"))
    monkeypatch.setattr(CodexBackend, "__init__", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    yield {"path": path, "proposal": proposal, "root": root, "out": tmp_path / "new_candidate", "cfg": cfg,
           "parent": parent, "tmp": tmp_path}
    forbidden.assert_not_called()


def run(p, *, apply=False):
    return revision.execute(p["path"], sha256_file(p["path"]), apply=apply)


def rewrite(p):
    atomic_write_json(p["path"], p["proposal"])


def snapshot(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode) for p in root.rglob("*") if p.is_file()}


def test_plan_read_only_then_apply_new_l2_only(prepared):
    p = prepared
    before = snapshot(p["tmp"])
    plan = run(p)
    assert plan["status"] == "planned_not_applied" and plan["model_calls"] == 0
    assert snapshot(p["tmp"]) == before and not p["out"].exists()
    result = run(p, apply=True)
    assert result["status"] == "applied_candidate_l2_only" and result["model_calls"] == 0
    assert result["source_acceptance"] == "not_granted" and not result["ready_for_serving"]
    assert all((f.read_bytes(), f.stat().st_mtime_ns, f.stat().st_mode) == v for f, v in before.items())
    new = read_json(Path(result["revised_level2"]["path"]))
    expected = copy.deepcopy(p["parent"]["content"])
    expected["cases"][0]["title"] = p["proposal"]["edits"][0]["value"]
    assert new["content"] == expected
    assert new["evidence"] == p["parent"]["evidence"] and new["source"] == p["parent"]["source"]
    assert new["semantic_review_status"] == "pending"
    meta = new["model_metadata"]
    assert meta["provider"] == "agent_review_revision" and meta["model"] is None
    assert meta["model_call_performed"] is False and meta["usage"] is None
    assert meta["revision_notes"] == p["proposal"]["revision_notes"]
    assert meta["revision_notes_sha256"] == digest(meta["revision_notes"])
    assert meta["assessment_scope"].startswith("Unchanged historical")
    assert "not_source_or_approval" in meta["revision_notes_authority"]
    assert meta["original_model_record"] == p["proposal"]["original_call"]
    cfg = read_json(Path(result["extraction_config"]["path"]))
    assert cfg["episodes"][0]["reuse_artifacts"] == {"level1": p["proposal"]["level1"]}
    assert sorted(str(f.relative_to(p["out"])) for f in p["out"].rglob("*") if f.is_file()) == [
        "SYN1/level2.json", "application_receipt.json", "extraction_config.json"]
    assert pipeline.run(cfg, through="level2", validate_only=True)["model_calls_this_execution"] == 0
    packet = case_release.review_packet(Path(result["extraction_config"]["path"]), ["SYN1:R1"])
    assert packet["cases"][0]["source_binding"]["source_acceptance"] == "not_granted"
    with pytest.raises(ValueError, match="new"):
        run(p, apply=True)


def test_fresh_derivation_not_copied_qualification(prepared, monkeypatch):
    original = pipeline.derive
    def derive(stage, content, source, upstream):
        ev, qual, warnings = original(stage, content, source, upstream)
        if stage == "level2" and content["cases"][0]["title"] == "Synthetic conditional preference":
            qual["synthetic_revision_check"] = "fresh result"
            warnings.append("Synthetic deterministic warning")
        return ev, qual, warnings
    monkeypatch.setattr(pipeline, "derive", derive)
    result = run(prepared, apply=True)
    new = read_json(Path(result["revised_level2"]["path"]))
    assert new["qualification"]["synthetic_revision_check"] == "fresh result"
    assert new["warnings"][-1] == "Synthetic deterministic warning"
    assert "synthetic_revision_check" not in prepared["parent"]["qualification"]


@pytest.mark.parametrize("field", ["record_id", "thread_ids", "merged_ids", "speaker_attribution", "case_assessments", "evidence"])
def test_protected_fields_rejected(prepared, field):
    prepared["proposal"]["edits"][0]["field"] = field
    rewrite(prepared)
    with pytest.raises(ValueError): run(prepared, apply=True)
    assert not prepared["out"].exists()


@pytest.mark.parametrize("fault", ["expected", "noop", "unknown_case", "duplicate_field", "primary_hash", "anchor_text",
    "anchor_time", "foreign_anchor", "duplicate_anchor", "no_anchors", "invalid_value", "notes_hash", "notes_missing_case",
    "notes_foreign_region", "same_run", "existing_output", "overlap", "multi_episode", "reviewed_mode", "missing_source_pin",
    "incomplete_call", "wrong_call_context", "wrong_call_usage", "wrong_l1", "accepted_parent"])
def test_fail_closed_before_output(prepared, fault):
    p = prepared; q = p["proposal"]; e = q["edits"][0]
    if fault == "expected": e["expected"] = "Stale"
    elif fault == "noop": e["value"] = e["expected"]
    elif fault == "unknown_case": e["record_id"] = "SYN1:R99"
    elif fault == "duplicate_field": q["edits"].append(copy.deepcopy(e))
    elif fault == "primary_hash": e["primary_evidence_sha256"] = "0" * 64
    elif fault == "anchor_text": e["anchors"][0]["text_sha256"] = "0" * 64
    elif fault == "anchor_time": e["anchors"][0]["end"] += 1
    elif fault == "foreign_anchor": e["anchors"][0].update(merged_id=1, segment_id=1)
    elif fault == "duplicate_anchor": e["anchors"].append(copy.deepcopy(e["anchors"][0]))
    elif fault == "no_anchors": e["anchors"] = []
    elif fault == "invalid_value": e["value"] = []
    elif fault == "notes_hash": q["revision_notes"][0]["note"] += " Changed"
    elif fault == "notes_missing_case":
        q["revision_notes"][0]["record_id"] = "SYN1:R2"
        q["revision_notes_sha256"] = digest(q["revision_notes"])
    elif fault == "notes_foreign_region":
        q["revision_notes"][0]["merged_ids"] = [1]
        q["revision_notes_sha256"] = digest(q["revision_notes"])
    elif fault == "same_run": q["run_id"] = p["cfg"]["run_id"]
    elif fault == "existing_output": p["out"].mkdir()
    elif fault == "overlap": q["output_root"] = str(p["root"])
    elif fault in {"multi_episode", "reviewed_mode", "missing_source_pin"}:
        cfg = read_json(Path(q["parent_config"]["path"]))
        if fault == "multi_episode":
            extra = copy.deepcopy(cfg["episodes"][0]); extra["episode_id"] = "SYN2"
            cfg["episodes"].append(extra)
        elif fault == "reviewed_mode": cfg["extraction_mode"] = "reviewed"
        else: del cfg["episodes"][0]["raw_sha256"]
        path = Path(q["parent_config"]["path"]); atomic_write_json(path, cfg); q["parent_config"] = ref(path)
    elif fault in {"incomplete_call", "wrong_call_context", "wrong_call_usage"}:
        path = Path(q["original_call"]["path"]); call = read_json(path)
        if fault == "incomplete_call": call["status"] = "failed"
        elif fault == "wrong_call_context": call["request_context_sha256"] = "0" * 64
        else: call["usage"] = {}
        atomic_write_json(path, call); q["original_call"] = ref(path)
    elif fault == "wrong_l1": q["level1"] = q["level2"]
    elif fault == "accepted_parent":
        path = Path(q["level2"]["path"]); a = read_json(path); a["semantic_review_status"] = "accepted"
        atomic_write_json(path, a); q["level2"] = ref(path)
    rewrite(p)
    with pytest.raises(ValueError): run(p, apply=True)
    assert not (p["out"] / "application_receipt.json").exists()


@pytest.mark.parametrize("key", ["parent_config", "level1", "level2", "original_call", "review_report", "raw", "normalized"])
def test_tampered_inputs_rejected(prepared, key):
    p = prepared
    path = Path(p["cfg"]["episodes"][0][key] if key in {"raw", "normalized"} else p["proposal"][key]["path"])
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError): run(p, apply=True)
    assert not p["out"].exists()


def test_proposal_pin_and_symlink_and_public_paths(prepared):
    p = prepared
    with pytest.raises(ValueError): revision.execute(p["path"], "0" * 64, apply=True)
    link = p["tmp"] / "link.json"; link.symlink_to(p["path"])
    with pytest.raises(ValueError, match="symlink"): revision.execute(link, sha256_file(p["path"]))
    p["proposal"]["output_root"] = str(PROJECT_ROOT / "sample_outputs")
    rewrite(p)
    with pytest.raises(ValueError, match="public"): run(p, apply=True)


def test_config_model_not_replaced_by_environment(prepared, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_MODEL_ID", "not-the-parent-model")
    result = run(prepared, apply=True)
    assert read_json(Path(result["extraction_config"]["path"]))["model"] == prepared["cfg"]["model"]


def test_late_input_tamper_is_detected_and_failed_version_not_resumed(prepared, monkeypatch):
    original = revision.atomic_write_json
    def tamper(path, value, **kwargs):
        original(path, value, **kwargs)
        if path.name == "level2.json":
            report = Path(prepared["proposal"]["review_report"]["path"])
            report.write_text("Changed during revision")
    monkeypatch.setattr(revision, "atomic_write_json", tamper)
    with pytest.raises(ValueError, match="changed"): run(prepared, apply=True)
    assert prepared["out"].exists() and not (prepared["out"] / "application_receipt.json").exists()


def test_multiple_paired_fields_preserve_assessments_and_other_cases(prepared):
    p = prepared; q = p["proposal"]
    original = p["parent"]["content"]["cases"][0]
    for field, value in [("conditions", []), ("uncertainty", original["uncertainty"] + ["Reviewer: conditional only"]),
                         ("decision_context", {**original["decision_context"], "event_reference": "Synthetic condition"})]:
        q["edits"].append({**copy.deepcopy(q["edits"][0]), "field": field, "expected": original[field], "value": value})
    rewrite(p)
    result = run(p, apply=True)
    new = read_json(Path(result["revised_level2"]["path"]))
    assert new["content"]["case_assessments"] == p["parent"]["content"]["case_assessments"]
    assert new["content"]["cases"][0]["speaker_attribution"] == original["speaker_attribution"]
    assert new["content"]["cases"][1] == p["parent"]["content"]["cases"][1]


def test_listener_revision_uses_same_contract_without_changing_attribution(prepared):
    p = prepared; q = p["proposal"]
    path = Path(q["level2"]["path"])
    parent = read_json(path)
    parent["content"]["cases"][0]["speaker_attribution"] = "listener"
    source = pipeline.sources(p["cfg"])["SYN1"]
    l1 = read_json(Path(q["level1"]["path"]))
    parent["evidence"], parent["qualification"], parent["warnings"] = pipeline.derive("level2", parent["content"], source, [l1])
    atomic_write_json(path, parent)
    q["level2"] = ref(path)
    q["edits"][0]["primary_evidence_sha256"] = digest(parent["evidence"]["SYN1:R1"])
    rewrite(p)
    result = run(p, apply=True)
    assert read_json(Path(result["revised_level2"]["path"]))["content"]["cases"][0]["speaker_attribution"] == "listener"


def test_original_l3_left_untouched_not_reused(prepared):
    p = prepared
    pipeline.run(p["cfg"], through="level3", backend=CandidateModel())
    old_l3 = p["root"] / "SYN1/level3.json"
    before = old_l3.read_bytes()
    result = run(p, apply=True)
    assert not result["level3_reused"] and old_l3.read_bytes() == before
    assert not list(p["out"].rglob("level3.json"))


def test_write_failure_preserves_partial_version_without_success(prepared, monkeypatch):
    original = revision.atomic_write_json
    def fail(path, value, **kwargs):
        if path.name == "level2.json":
            raise OSError("Synthetic write failure")
        original(path, value, **kwargs)
    monkeypatch.setattr(revision, "atomic_write_json", fail)
    with pytest.raises(OSError, match="Synthetic"): run(prepared, apply=True)
    assert (prepared["out"] / "extraction_config.json").is_file()
    assert not (prepared["out"] / "application_receipt.json").exists()
    with pytest.raises(ValueError, match="new"): run(prepared, apply=True)


def test_cli_defaults_to_plan(prepared, capsys):
    revision.main(["--proposal", str(prepared["path"]), "--proposal-sha256", sha256_file(prepared["path"])])
    assert json.loads(capsys.readouterr().out)["status"] == "planned_not_applied"
    assert not prepared["out"].exists()
