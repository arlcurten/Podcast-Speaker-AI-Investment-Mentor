"""Synthetic scoped approvals only; no real-source acceptance or live model calls."""
import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge import case_release, pipeline
from knowledge.codex_backend import CodexBackend
from knowledge.tests.test_pipeline import cfg
from knowledge.tests.test_candidate_extraction import CandidateModel
from transcription.common import PROJECT_ROOT, atomic_write_json, read_json, sha256_file
from transcription.prepare import prepare
from transcription.tests.test_annotations import annotation_review, repin


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No live inference in scoped release tests"))
    monkeypatch.setattr(CodexBackend, "complete", forbidden)
    yield forbidden
    forbidden.assert_not_called()


class ScopedModel(CandidateModel):
    def complete(self, *args):
        content, usage = super().complete(*args)
        if "source_observations" in content:
            content["source_observations"] = [{"kind": "interpretive_caution", "merged_ids": [0],
                "note": "Synthetic analyst note: conditions do not establish an observed outcome."}]
        if "case_assessments" in content:
            content["thread_dispositions"][0]["unextracted_regions"] = [{"merged_ids": [0],
                "reason": "An unrelated product-label fragment is not used in the case."}]
            content["case_assessments"][0]["supplementary_notes"] = [{"kind": "interpretive_caution", "merged_ids": [0],
                "note": "Synthetic model commentary, not a speaker assertion."}]
        return content, usage


def approval(packet):
    rows = []
    for p in packet["cases"]:
        row = {key: copy.deepcopy(p[key]) for key in case_release.BINDINGS}
        row.update(reviewed_merged_ids=p["case"]["merged_ids"],
            source_fitness={"decision": "fit_for_selected_claims", "rationale": "Synthetic claim-specific reading only.",
                            "reviewed_claim_refs": p["required_claim_refs"]},
            semantic_fidelity={"decision": "faithful", "rationale": "Synthetic conditions and attribution retained."},
            limitations=["Synthetic text-only case scope; not episode acceptance or verified outcome."],
            supplementary_notes=[{"kind": "interpretive_caution", "merged_ids": p["case"]["merged_ids"],
                                  "note": "Synthetic reviewer commentary, never speaker evidence."}])
        for field, inventory, identifier in (("issue_dispositions", "source_issues", "issue_id"),
                ("global_dispositions", "global_limitations", "limitation_id"),
                ("omission_dispositions", "omissions", "omission_id")):
            row[field] = [{identifier: n[identifier], "disposition": "not_material_to_claims",
                           "reason": "Synthetic excluded label/global scope does not determine this case's claims.",
                           "claim_refs": []} for n in p[inventory]]
        rows.append(row)
    return {"schema_version": "knowledge_case_review.v1", "decision": "accept", "scope": "selected_case_use",
            "reviewer": "Synthetic reviewer", "method": "Synthetic source-grounded fixture; no actual speaker or audio.",
            "reviewed_at": "2026-01-01T00:00:00Z", "limitations": ["Synthetic tests only, not corpus acceptance."],
            "candidate_config": packet["candidate_config"], "accepted_case_ids": [r["record_id"] for r in rows], "cases": rows}


@pytest.fixture
def prepared(cfg, tmp_path):
    cfg.update(extraction_mode="candidate", run_id="scoped_synthetic")
    entry = cfg["episodes"][0]
    raw_path = Path(entry["raw"])
    raw = read_json(raw_path)
    raw["segments"][0]["text"] += " A separate product-name fragment is unclear."
    atomic_write_json(raw_path, raw)
    review, sidecar, supporting = annotation_review(raw_path, tmp_path / "source-notes")
    notes = read_json(sidecar)
    notes["annotations"][0].update(kind="uncertainty", note="An unrelated product-name fragment is unresolved.")
    extra = copy.deepcopy(notes["annotations"][0])
    extra.update(annotation_id="SYN:OTHER", note="Another context has an unresolved label.", anchors=[
        {"segment_id": 2, "raw_text": raw["segments"][2]["text"], "start": raw["segments"][2]["start"],
         "end": raw["segments"][2]["end"]}])
    notes["annotations"].append(extra)
    atomic_write_json(sidecar, notes)
    repin(review, sidecar)
    reviewed = read_json(review)
    reviewed.update(decision="uncertain", unresolved_critical_issues=["Synthetic episode has unresolved labels."])
    atomic_write_json(review, reviewed)
    draft = tmp_path / "candidate-draft.json"
    prepare(raw_path, draft)
    entry.update(normalized=str(draft), review=str(review))
    for e in cfg["episodes"]:
        for field in ("raw", "normalized", "review"):
            e[field + "_sha256"] = sha256_file(Path(e[field]))
    config_path = tmp_path / "candidate-config.json"
    atomic_write_json(config_path, cfg)
    pipeline.run(cfg, through="level2", backend=ScopedModel())
    packet = case_release.review_packet(config_path, ["SYN1:R1"])
    review_path = tmp_path / "case-review.json"
    atomic_write_json(review_path, approval(packet))
    return {"cfg": cfg, "config": config_path, "review": review_path, "packet": packet,
            "sidecar": sidecar, "supporting": supporting, "output": tmp_path / "case-release.json"}


def publish(prepared):
    return case_release.publish(prepared["config"], prepared["review"], prepared["output"])


def test_partial_selection_reuses_l1_l2_preserves_hold_and_separate_commentary(prepared):
    p = prepared
    root = Path(p["cfg"]["output_root"]) / p["cfg"]["run_id"]
    paths = [p["config"], p["review"], p["sidecar"], p["supporting"], *root.rglob("*.json")]
    paths += [Path(e[k]) for e in p["cfg"]["episodes"] for k in ("raw", "normalized", "review")]
    before = {path: path.read_bytes() for path in paths}
    result = publish(p)
    cases, principles = case_release.load_payload(read_json(p["output"]))
    assert result["case_count"] == 1 and result["principle_count"] == 0
    assert set(cases) == {"SYN1:R1"} and principles == {}
    assert not list(root.rglob("level3.json")) and not list(root.rglob("level4.json"))
    case = cases["SYN1:R1"]
    original = p["packet"]["cases"][0]
    assert all(case[k] == v for k, v in original["case"].items())
    assert case["source_assessment"] == original["source_assessment"]
    assert case["source_assessment"]["eligible_for_online_reasoning"] is False
    assert case["evidence"]["extraction_mode"] == "candidate"
    assert case["knowledge_artifact_sha256"] == original["artifact_sha256"]
    assert case["scoped_review"]["source_acceptance"] == "not_granted"
    assert case["scoped_review"]["decision"] == "accept"
    assert case["scoped_review"]["usage"] == "case_only"
    assert case["scoped_review"]["omissions"] and case["scoped_review"]["global_limitations"]
    assert {n["authority"] for n in case["supplementary_notes"]} == {
        "model_assessment_not_source", "reviewer_commentary_not_source"}
    assert read_json(Path(p["cfg"]["episodes"][0]["review"]))["decision"] == "uncertain"
    assert all(path.read_bytes() == data for path, data in before.items())
    with pytest.raises(FileExistsError): publish(p)


def test_packet_is_read_only_and_does_not_create_approval(prepared):
    p = prepared
    packet = case_release.review_packet(p["config"], ["SYN1:R1", "SYN2:R2"])
    assert packet["model_calls"] == 0 and "decision" not in packet
    assert len(packet["cases"]) == 2 and not p["output"].exists()
    first = packet["cases"][0]
    assert {r["issue_id"] for r in first["source_issues"]} == {"SYN:A01", "L1:O1"}
    assert "SYN:OTHER" not in {r["issue_id"] for r in first["source_issues"]}
    assert first["source_regions"] and first["required_claim_refs"]
    for ids in ([], ["SYN1:R1", "SYN1:R1"], ["SYN9:R1"]):
        with pytest.raises(ValueError): case_release.review_packet(p["config"], ids)


@pytest.mark.parametrize("disposition", ["retained_limitation", "unresolved_for_claims"])
def test_claim_bearing_critical_issue_cannot_be_approved(prepared, disposition):
    review = read_json(prepared["review"])
    issue = next(r for r in review["cases"][0]["issue_dispositions"] if r["issue_id"] == "SYN:A01")
    issue.update(disposition=disposition, claim_refs=["/claims/0"])
    atomic_write_json(prepared["review"], review)
    with pytest.raises(ValueError, match="critical|ambiguity"): publish(prepared)
    assert not prepared["output"].exists()


def test_noncritical_caution_is_retained_without_becoming_speaker_evidence(prepared):
    review = read_json(prepared["review"])
    issue = next(r for r in review["cases"][0]["issue_dispositions"] if r["issue_id"] == "L1:O1")
    issue.update(disposition="retained_limitation", claim_refs=["/uncertainty/0"])
    review["cases"][0]["global_dispositions"][0].update(disposition="retained_limitation", claim_refs=["/uncertainty/0"])
    atomic_write_json(prepared["review"], review)
    publish(prepared)
    cases, _ = case_release.load_payload(read_json(prepared["output"]))
    assert cases["SYN1:R1"]["scoped_review"]["issue_dispositions"] == review["cases"][0]["issue_dispositions"]
    assert cases["SYN1:R1"]["claims"] == prepared["packet"]["cases"][0]["case"]["claims"]


@pytest.mark.parametrize("fault", ["missing_issue", "duplicate_issue", "foreign_issue", "missing_global", "missing_omission",
    "bad_claim_ref", "unrelated_with_claim", "retained_without_claim", "missing_claim_review", "wrong_case_hash",
    "wrong_artifact_hash", "wrong_l1_hash", "wrong_source_hash", "wrong_region", "unbound_note", "missing_limits",
    "empty_reviewer", "bad_time", "no_timezone", "needs_revision", "source_unresolved", "extra_case", "unknown_case",
    "relabeled_scope", "unresolved_global", "unresolved_omission", "injected_claims"])
def test_review_rejects_incomplete_or_fabricated_scope(prepared, fault):
    p = prepared
    review = read_json(p["review"]); row = review["cases"][0]
    if fault == "missing_issue": row["issue_dispositions"].pop(0)
    elif fault == "duplicate_issue": row["issue_dispositions"].append(copy.deepcopy(row["issue_dispositions"][0]))
    elif fault == "foreign_issue": row["issue_dispositions"][0]["issue_id"] = "SYN:OTHER"
    elif fault == "missing_global": row["global_dispositions"].pop()
    elif fault == "missing_omission": row["omission_dispositions"].clear()
    elif fault == "bad_claim_ref": row["issue_dispositions"][0]["claim_refs"] = ["/claims/999"]
    elif fault == "unrelated_with_claim": row["issue_dispositions"][0]["claim_refs"] = ["/claims/0"]
    elif fault == "retained_without_claim": row["issue_dispositions"][1]["disposition"] = "retained_limitation"
    elif fault == "missing_claim_review": row["source_fitness"]["reviewed_claim_refs"].pop()
    elif fault == "wrong_case_hash": row["case_sha256"] = "0" * 64
    elif fault == "wrong_artifact_hash": row["artifact_sha256"] = "0" * 64
    elif fault == "wrong_l1_hash": row["level1_sha256"] = "0" * 64
    elif fault == "wrong_source_hash": row["source_binding"]["raw_sha256"] = "0" * 64
    elif fault == "wrong_region": row["reviewed_merged_ids"] = [2]
    elif fault == "unbound_note": row["supplementary_notes"][0]["merged_ids"] = [999]
    elif fault == "missing_limits": row["limitations"] = []
    elif fault == "empty_reviewer": review["reviewer"] = "  "
    elif fault == "bad_time": review["reviewed_at"] = "not a time"
    elif fault == "no_timezone": review["reviewed_at"] = "2026-01-01T00:00:00"
    elif fault == "needs_revision": row["semantic_fidelity"]["decision"] = "needs_revision"
    elif fault == "source_unresolved": row["source_fitness"]["decision"] = "unresolved"
    elif fault == "extra_case": review["accepted_case_ids"].append("SYN2:R1")
    elif fault == "unknown_case": row["record_id"] = review["accepted_case_ids"][0] = "SYN9:R1"
    elif fault == "relabeled_scope": review["scope"] = "whole_episode"
    elif fault == "unresolved_global": row["global_dispositions"][0]["disposition"] = "unresolved_for_claims"
    elif fault == "unresolved_omission": row["omission_dispositions"][0]["disposition"] = "unresolved_for_claims"
    else: row["claims"] = ["Invented replacement"]
    atomic_write_json(p["review"], review)
    with pytest.raises(ValueError): publish(p)
    assert not p["output"].exists()


@pytest.mark.parametrize("target", ["config", "review", "raw", "normalized", "source_review", "sidecar", "supporting", "l1", "l2"])
def test_release_tampering_and_revocation_fail_closed(prepared, target):
    p = prepared
    publish(p)
    data = read_json(p["output"])
    e = p["cfg"]["episodes"][0]
    root = Path(p["cfg"]["output_root"]) / p["cfg"]["run_id"] / e["episode_id"]
    paths = {**{k: p[k] for k in ("config", "review", "sidecar", "supporting")}, "raw": Path(e["raw"]),
             "normalized": Path(e["normalized"]), "source_review": Path(e["review"]),
             "l1": root / "level1.json", "l2": root / "level2.json"}
    path = paths[target]; value = read_json(path)
    if target == "review": value["decision"] = "revoked"
    elif target == "l2": value["content"]["cases"][0]["claims"] = ["Changed claim"]
    else: value["tampered"] = True
    atomic_write_json(path, value)
    with pytest.raises(ValueError): case_release.load_payload(data)


@pytest.mark.parametrize("field", ["accepted_case_ids", "dependencies", "source_acceptance", "schema_version", "candidate_config"])
def test_release_payload_cannot_relabel_or_select_unreviewed_cases(prepared, field):
    publish(prepared)
    data = read_json(prepared["output"])
    if field == "accepted_case_ids": data[field].append("SYN2:R2")
    elif field == "dependencies": data[field].pop()
    elif field == "source_acceptance": data[field] = "accepted"
    elif field == "schema_version": data[field] = "knowledge_release.v1"
    else: data[field]["sha256"] = "0" * 64
    with pytest.raises(ValueError): case_release.load_payload(data)


def test_changed_claims_cannot_be_hidden_by_repinning_artifact_only(prepared):
    p = prepared
    root = Path(p["cfg"]["output_root"]) / p["cfg"]["run_id"] / "SYN1"
    path = root / "level2.json"
    artifact = read_json(path); artifact["content"]["cases"][0]["claims"] = ["A changed synthetic claim"]
    atomic_write_json(path, artifact)
    review = read_json(p["review"])
    review["cases"][0]["artifact_sha256"] = sha256_file(path)
    atomic_write_json(p["review"], review)
    with pytest.raises(ValueError, match="binding"): publish(p)


def test_revoked_review_is_rejected_even_if_descriptor_is_repinned(prepared):
    publish(prepared)
    data = read_json(prepared["output"])
    review = read_json(prepared["review"]); review["decision"] = "revoked"
    atomic_write_json(prepared["review"], review)
    data["review"]["sha256"] = sha256_file(prepared["review"])
    with pytest.raises(ValueError, match="revoked"): case_release.load_payload(data)


def test_public_inputs_and_outputs_are_rejected(prepared):
    public = PROJECT_ROOT / "sample_outputs/synthetic/uncreated-case-release.json"
    with pytest.raises(ValueError, match="public"):
        case_release.publish(prepared["config"], prepared["review"], public)
    with pytest.raises(ValueError, match="public"):
        case_release.review_packet(public, ["SYN1:R1"])
    cfg = copy.deepcopy(prepared["cfg"])
    cfg["episodes"][0]["raw"] = str(PROJECT_ROOT / "sample_outputs/synthetic/raw.json")
    atomic_write_json(prepared["config"], cfg)
    with pytest.raises(ValueError, match="public"):
        case_release.review_packet(prepared["config"], ["SYN1:R1"])
    assert not public.exists()


def test_source_dependency_change_during_read_back_prevents_return(prepared, monkeypatch):
    original = case_release.atomic_write_json
    def change_after_write(path, payload, **kwargs):
        original(path, payload, **kwargs)
        if path == prepared["output"]:
            value = read_json(prepared["supporting"]); value["changed"] = True
            original(prepared["supporting"], value)
    monkeypatch.setattr(case_release, "atomic_write_json", change_after_write)
    with pytest.raises(ValueError): publish(prepared)


def test_final_dependency_pin_check_catches_late_change(prepared, monkeypatch):
    publish(prepared)
    original = case_release._reviewed
    def change_after_review(*args):
        result = original(*args)
        value = read_json(prepared["config"]); value["changed"] = True
        atomic_write_json(prepared["config"], value)
        return result
    monkeypatch.setattr(case_release, "_reviewed", change_after_review)
    with pytest.raises(ValueError, match="changed"):
        case_release.load_payload(read_json(prepared["output"]))


def test_config_mode_missing_artifact_and_source_pin_mismatch_never_generate(prepared):
    p = prepared
    cfg = copy.deepcopy(p["cfg"]); cfg["extraction_mode"] = "reviewed"
    atomic_write_json(p["config"], cfg)
    with pytest.raises(ValueError, match="candidate"): case_release.review_packet(p["config"], ["SYN1:R1"])
    cfg["extraction_mode"] = "candidate"; cfg["episodes"][0]["raw_sha256"] = "0" * 64
    atomic_write_json(p["config"], cfg)
    with pytest.raises(ValueError, match="Pinned source"): case_release.review_packet(p["config"], ["SYN1:R1"])
    atomic_write_json(p["config"], p["cfg"])
    root = Path(cfg["output_root"]) / cfg["run_id"]
    (root / "SYN1/level2.json").unlink()
    with pytest.raises(ValueError, match="Missing candidate"): case_release.review_packet(p["config"], ["SYN1:R1"])


def test_config_model_is_file_bound_not_environment_substituted(prepared, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_MODEL_ID", "unrelated-runtime-model")
    assert publish(prepared)["case_count"] == 1


def test_pinned_candidate_references_need_no_new_output_or_l3(prepared, tmp_path):
    p = prepared
    cfg = copy.deepcopy(p["cfg"])
    original_root = Path(cfg["output_root"]) / cfg["run_id"]
    cfg["run_id"] = "reference_only_selection"
    for entry in cfg["episodes"]:
        entry["reuse_artifacts"] = {stage: {
            "path": str(original_root / entry["episode_id"] / (stage + ".json")),
            "sha256": sha256_file(original_root / entry["episode_id"] / (stage + ".json"))}
            for stage in ("level1", "level2")}
    config = tmp_path / "reference-config.json"
    atomic_write_json(config, cfg)
    packet = case_release.review_packet(config, ["SYN1:R1"])
    review = tmp_path / "reference-review.json"
    atomic_write_json(review, approval(packet))
    assert case_release.publish(config, review, tmp_path / "reference-release.json")["case_count"] == 1
    assert not (Path(cfg["output_root"]) / cfg["run_id"]).exists()
    cfg["episodes"][0]["reuse_artifacts"]["level2"]["sha256"] = "0" * 64
    atomic_write_json(config, cfg)
    with pytest.raises(ValueError, match="checksum"):
        case_release.review_packet(config, ["SYN1:R1"])


def test_review_config_hash_must_be_explicit_and_exact(prepared):
    review = read_json(prepared["review"])
    review["candidate_config"]["sha256"] = "0" * 64
    atomic_write_json(prepared["review"], review)
    with pytest.raises(ValueError, match="different candidate config"): publish(prepared)
