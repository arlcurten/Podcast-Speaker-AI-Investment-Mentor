"""Synthetic, hash-bound editorial ancestry; no real source or backend calls."""
import copy
from pathlib import Path

import pytest

from knowledge import case_revision as revision
from knowledge.provenance import digest
from knowledge.tests.test_case_revision import prepared, ref, rewrite, run, snapshot
from knowledge.tests.test_pipeline import cfg
from transcription.common import atomic_write_json, read_json, sha256_file


def child(previous, receipt, number):
    q = copy.deepcopy(previous["proposal"])
    parent = read_json(Path(receipt["revised_level2"]["path"]))
    q.update(schema_version="knowledge_case_revision.v2", run_id=f"revision_{number}",
             parent_config=receipt["extraction_config"], level2=receipt["revised_level2"],
             parent_application={"receipt": ref(Path(receipt["output"]) / "application_receipt.json"),
                                 "proposal": ref(previous["path"])})
    q["edits"][0].update(expected=parent["content"]["cases"][0]["title"],
                         value=f"Synthetic conditional preference revision {number}")
    q["revision_notes"][0]["note"] = f"Synthetic revision {number}: text-only commentary, not approval."
    q["revision_notes_sha256"] = digest(q["revision_notes"])
    p = {**previous, "proposal": q, "path": previous["tmp"] / f"proposal_{number}.json",
         "out": Path(q["output_root"]) / q["run_id"]}
    rewrite(p)
    return p


@pytest.fixture
def second(prepared):
    return child(prepared, run(prepared, apply=True), 2)


def test_three_revisions_replay_history_no_approval_or_calls(prepared, monkeypatch):
    p = prepared
    original_usage = p["parent"]["model_metadata"]
    original_artifact = copy.deepcopy(p["proposal"]["level2"])
    # Historical implementation hashes remain recorded, not relabeled as this code.
    real_sha = revision.sha256_file
    with monkeypatch.context() as m:
        m.setattr(revision, "sha256_file", lambda path: "a" * 64 if path == Path(revision.__file__) else real_sha(path))
        first = run(p, apply=True)
    second = child(p, first, 2)
    before = snapshot(p["tmp"])
    assert run(second)["schema_version"] == "knowledge_case_revision_receipt.v2"
    assert snapshot(p["tmp"]) == before
    middle = run(second, apply=True)
    third = child(second, middle, 3)
    before = snapshot(p["tmp"])
    last = run(third, apply=True)
    assert all((f.read_bytes(), f.stat().st_mtime_ns, f.stat().st_mode) == v for f, v in before.items())
    artifact = read_json(Path(last["revised_level2"]["path"]))
    meta = artifact["model_metadata"]
    assert meta["original_model_usage"] == original_usage
    assert meta["original_model_artifact"] == original_artifact
    assert meta["usage"] is None and meta["model"] is None
    assert meta["model_call_performed"] is False and meta["model_calls"] == 0
    assert [h["revision_notes"] for h in meta["revision_history"]] == [
        p["proposal"]["revision_notes"], second["proposal"]["revision_notes"]]
    assert meta["revision_history"][0]["revision_implementation_sha256"] == "a" * 64
    assert meta["revision_implementation_sha256"] == real_sha(Path(revision.__file__))
    assert meta["revision_notes"] == third["proposal"]["revision_notes"]
    assert artifact["evidence"] == p["parent"]["evidence"]
    assert artifact["content"]["case_assessments"] == p["parent"]["content"]["case_assessments"]
    assert artifact["semantic_review_status"] == "pending" and not last["ready_for_serving"]
    assert last["source_acceptance"] == "not_granted" and last["model_calls"] == 0
    assert sorted(f.name for f in third["out"].rglob("*") if f.is_file()) == [
        "application_receipt.json", "extraction_config.json", "level2.json"]
    # A fourth plan proves the v2 artifact and receipt themselves can be replayed.
    fourth = child(third, last, 4)
    assert run(fourth)["status"] == "planned_not_applied"


def test_editorial_parent_cannot_be_disguised_as_v1(second):
    second["proposal"]["schema_version"] = "knowledge_case_revision.v1"
    del second["proposal"]["parent_application"]
    rewrite(second)
    before = snapshot(second["tmp"])
    with pytest.raises(ValueError, match="editorial parents require v2"):
        run(second, apply=True)
    assert snapshot(second["tmp"]) == before


def repin_parent(p, *, artifact=None, config=None, receipt=None):
    q = p["proposal"]
    receipt_path = Path(q["parent_application"]["receipt"]["path"])
    r = receipt or read_json(receipt_path)
    for key, value, top_key in (("revised_level2", artifact, "level2"),
                                ("extraction_config", config, "parent_config")):
        if value is not None:
            path = Path(r[key]["path"])
            atomic_write_json(path, value)
            r[key] = q[top_key] = ref(path)
    atomic_write_json(receipt_path, r)
    q["parent_application"]["receipt"] = ref(receipt_path)
    rewrite(p)


@pytest.mark.parametrize("fault", ["notes_loss", "notes_changed", "undeclared_content", "evidence",
    "assessment", "attribution", "implementation", "metadata_type", "config", "receipt_status", "receipt_calls",
    "receipt_type",
    "receipt_version", "receipt_dependencies", "receipt_extra", "wrong_output", "wrong_proposal",
    "wrong_ancestor", "wrong_l1", "wrong_parent_config", "wrong_parent_artifact", "prior_edit",
    "prior_value", "prior_anchor", "prior_report", "raw", "normalized"])
def test_rehashed_chain_mismatches_fail_before_any_writes(second, fault):
    p = second; q = p["proposal"]
    parent = read_json(Path(q["level2"]["path"]))
    meta = parent["model_metadata"]
    receipt_path = Path(q["parent_application"]["receipt"]["path"])
    receipt = read_json(receipt_path)
    if fault in {"notes_loss", "notes_changed", "undeclared_content", "evidence", "assessment", "attribution",
                 "implementation", "metadata_type"}:
        if fault == "notes_loss": del meta["revision_notes"]
        elif fault == "notes_changed":
            meta["revision_notes"][0]["note"] = "Lost earlier caveat"
            meta["revision_notes_sha256"] = digest(meta["revision_notes"])
        elif fault == "undeclared_content": parent["content"]["cases"][1]["title"] = "Undeclared change"
        elif fault == "evidence": parent["evidence"] = {}
        elif fault == "assessment": parent["content"]["case_assessments"][0]["rationale"] = "Rewritten diagnostics"
        elif fault == "attribution": parent["content"]["cases"][0]["speaker_attribution"] = "listener"
        elif fault == "metadata_type": meta["model_call_performed"] = 0
        else: meta["revision_implementation_sha256"] = "b" * 64
        repin_parent(p, artifact=parent)
    elif fault == "config":
        config = read_json(Path(q["parent_config"]["path"]))
        config["retry_count"] += 1
        repin_parent(p, config=config)
    elif fault.startswith("receipt_") or fault == "wrong_output":
        if fault == "receipt_status": receipt["status"] = "planned_not_applied"
        elif fault == "receipt_calls": receipt["model_calls"] = 1
        elif fault == "receipt_type": receipt["model_calls"] = False
        elif fault == "receipt_version": receipt["schema_version"] = "knowledge_case_revision_receipt.v99"
        elif fault == "receipt_dependencies": receipt["dependencies"] = []
        elif fault == "receipt_extra": receipt["approval"] = True
        else: receipt["output"] = str(p["tmp"] / "wrong")
        repin_parent(p, receipt=receipt)
    elif fault == "wrong_proposal": q["parent_application"]["proposal"] = q["review_report"]
    elif fault == "wrong_ancestor": q["original_call"] = q["review_report"]
    elif fault == "wrong_l1": q["level1"] = q["level2"]
    elif fault == "wrong_parent_config": q["parent_config"] = q["review_report"]
    elif fault == "wrong_parent_artifact": q["level2"] = q["level1"]
    elif fault in {"prior_edit", "prior_value", "prior_anchor"}:
        path = Path(q["parent_application"]["proposal"]["path"])
        old = read_json(path)
        if fault == "prior_edit": old["edits"][0]["expected"] = "Wrong ancestor field"
        elif fault == "prior_value": old["edits"][0]["value"] = "Different proposed revision"
        else: old["edits"][0]["anchors"][0]["text_sha256"] = "0" * 64
        atomic_write_json(path, old)
        q["parent_application"]["proposal"] = receipt["proposal"] = ref(path)
        repin_parent(p, receipt=receipt)
    elif fault == "prior_report":
        path = Path(meta["review_report"]["path"])
        path.write_bytes(path.read_bytes() + b"Changed prior report")
    else:
        path = Path(p["cfg"]["episodes"][0][fault])
        path.write_bytes(path.read_bytes() + b"\n")
    rewrite(p)
    before = snapshot(p["tmp"])
    with pytest.raises(ValueError): run(p, apply=True)
    assert snapshot(p["tmp"]) == before and not p["out"].exists()


@pytest.mark.parametrize("key", ["receipt", "proposal"])
def test_prior_bytes_tampered_without_repinning(second, key):
    path = Path(second["proposal"]["parent_application"][key]["path"])
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="pin mismatch"): run(second, apply=True)
    assert not second["out"].exists()


@pytest.mark.parametrize("fault", ["history_loss", "original_usage", "original_artifact"])
def test_v2_history_and_original_model_provenance_cannot_be_lost(second, fault):
    p = child(second, run(second, apply=True), 3)
    parent = read_json(Path(p["proposal"]["level2"]["path"]))
    meta = parent["model_metadata"]
    if fault == "history_loss": meta["revision_history"] = []
    elif fault == "original_usage": meta["original_model_usage"] = None
    else: meta["original_model_artifact"] = meta["parent_artifact"]
    repin_parent(p, artifact=parent)
    before = snapshot(p["tmp"])
    with pytest.raises(ValueError, match="semantic replay"): run(p, apply=True)
    assert snapshot(p["tmp"]) == before


@pytest.mark.parametrize("fault", ["exists", "ancestor_overlap", "depth"])
def test_collision_and_bounded_depth_before_writes(second, monkeypatch, fault):
    p = second
    if fault == "exists": p["out"].mkdir()
    elif fault == "ancestor_overlap":
        p["proposal"]["output_root"] = str(p["root"])
        rewrite(p)
    else: monkeypatch.setattr(revision, "MAX_REVISIONS", 1)
    before = snapshot(p["tmp"])
    with pytest.raises(ValueError): run(p, apply=True)
    assert snapshot(p["tmp"]) == before


def test_cycle_guard_precedes_read(prepared, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Cycle must be rejected before reading")
    monkeypatch.setattr(revision, "_read", forbidden)
    with pytest.raises(ValueError, match="cycle"):
        revision._prepare(ref(prepared["path"]), ancestry=(prepared["path"],))


def test_unknown_contract_or_missing_chain_not_implicitly_upgraded(second):
    for version in ("knowledge_case_revision.v99", "knowledge_case_revision.v2"):
        second["proposal"]["schema_version"] = version
        second["proposal"].pop("parent_application", None)
        rewrite(second)
        with pytest.raises(ValueError): run(second, apply=True)
        assert not second["out"].exists()


def test_rehashed_original_call_must_still_match_generated_ancestor(second):
    p = second; q = p["proposal"]
    call_path = Path(q["original_call"]["path"])
    call = read_json(call_path)
    call["usage"] = {"provider": "agent_review_revision", "model_calls": 0}
    atomic_write_json(call_path, call)
    old_path = Path(q["parent_application"]["proposal"]["path"])
    old = read_json(old_path)
    old["original_call"] = q["original_call"] = ref(call_path)
    atomic_write_json(old_path, old)
    receipt = read_json(Path(q["parent_application"]["receipt"]["path"]))
    receipt["proposal"] = q["parent_application"]["proposal"] = ref(old_path)
    repin_parent(p, receipt=receipt)
    before = snapshot(p["tmp"])
    with pytest.raises(ValueError, match="completed call record"): run(p, apply=True)
    assert snapshot(p["tmp"]) == before


def test_ancestor_change_during_application_retains_failed_version_no_receipt(second, monkeypatch):
    original = revision.atomic_write_json
    def tamper(path, value, **kwargs):
        original(path, value, **kwargs)
        if path.name == "level2.json":
            ancestor = Path(second["proposal"]["parent_application"]["receipt"]["path"])
            ancestor.write_bytes(ancestor.read_bytes() + b"\n")
    monkeypatch.setattr(revision, "atomic_write_json", tamper)
    with pytest.raises(ValueError, match="changed"): run(second, apply=True)
    assert second["out"].exists()
    assert not (second["out"] / "application_receipt.json").exists()
