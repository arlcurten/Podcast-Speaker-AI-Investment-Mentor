"""Candidate source handling uses synthetic sources and no inference."""
import copy
from pathlib import Path

import pytest

from knowledge import pipeline
from knowledge.candidates import source_issues
from knowledge.tests.test_annotations import annotated_cfg
from knowledge.tests.test_pipeline import cfg, FakeModel
from transcription.common import atomic_write_json, read_json, sha256_file


class CandidateModel(FakeModel):
    def complete(self, prompt, context, schema):
        result, usage = super().complete(prompt, context, schema)
        if "source_observations" in schema["properties"]:
            result["source_observations"] = []
        if "case_assessments" in schema["properties"]:
            result["case_assessments"] = []
            for c in result["cases"]:
                result["case_assessments"].append({
                    "record_id": c["record_id"], "status": "usable_with_limits",
                    "rationale": "Fictional direct claim; no source acceptance implied.",
                    "supplementary_notes": [], "source_issue_dispositions": [
                        {"issue_id": i["issue_id"], "disposition": "not_used_in_claims",
                         "reason": "Synthetic doubtful statement not included in the extracted claims."}
                        for i in context["source_issues"] if set(c["merged_ids"]) & set(i["merged_ids"])]})
        return result, usage


def candidate(cfg):
    cfg = copy.deepcopy(cfg)
    cfg["extraction_mode"] = "candidate"
    cfg["run_id"] = "candidate_synthetic"
    return cfg


def test_four_levels_unaccepted_source_no_release_or_overwrite(cfg):
    cfg = candidate(cfg)
    raw_hashes = {}
    for e in cfg["episodes"]:
        raw_hashes[e["raw"]] = sha256_file(Path(e["raw"]))
        review = read_json(Path(e["review"]))
        review["decision"] = "uncertain"
        review["unresolved_critical_issues"] = ["Synthetic local ambiguity."]
        atomic_write_json(Path(e["review"]), review)
    model = CandidateModel()
    report = pipeline.run(cfg, backend=model)
    assert len(model.calls) == 7
    assert not report["ready_for_serving"]
    root = Path(cfg["output_root"]) / cfg["run_id"]
    artifact = read_json(root / "cross_episode/level4.json")
    assert artifact["schema_version"] == "knowledge_candidate.v1"
    assert not artifact["qualification"]["CROSS:P1"]["eligible_for_online_reasoning"]
    assert artifact["qualification"]["CROSS:P1"]["runtime_mode_recommendation"] == "not_eligible"
    assert all(sha256_file(Path(p)) == h for p, h in raw_hashes.items())
    assert pipeline.run(cfg, backend=model)["model_calls_this_execution"] == 0


def test_no_review_needed_but_transcript_mapping_verified(cfg):
    cfg = candidate(cfg)
    for e in cfg["episodes"]:
        e["review"] = None
    model = CandidateModel()
    assert not pipeline.run(cfg, dry_run=True)["sources_accepted"]
    assert not model.calls
    path = Path(cfg["episodes"][0]["normalized"])
    norm = read_json(path)
    norm["segments"][0]["normalized_text_zh_tw"] = "Fabricated context"
    atomic_write_json(path, norm)
    with pytest.raises(ValueError, match="differ"):
        pipeline.run(cfg, backend=model)
    assert not model.calls


def test_known_local_issues_retained_without_whole_episode_hold(annotated_cfg):
    cfg, _, _ = annotated_cfg
    cfg = candidate(cfg)
    source = pipeline.sources(cfg)["SYN1"]
    assert len(source_issues(source)) == 3
    pipeline.run(cfg, through="level2", backend=CandidateModel())
    path = Path(cfg["output_root"]) / cfg["run_id"] / "SYN1/level2.json"
    artifact = read_json(path)
    assessment = artifact["evidence"]["SYN1:R1"]["candidate_source_assessment"]
    assert assessment["authority"] == "model_assessment_not_source"
    assert assessment["source_issues"]
    assert not assessment["eligible_for_online_reasoning"]


@pytest.mark.parametrize("fault", ["missing_assessment", "unanchored_note", "unknown_issue", "commentary_as_claim"])
def test_invalid_candidate_assessment_not_saved(cfg, fault):
    cfg = candidate(cfg)
    class Bad(CandidateModel):
        def complete(self, *args):
            result, meta = super().complete(*args)
            if "case_assessments" in result:
                row = result["case_assessments"][0]
                if fault == "missing_assessment":
                    result["case_assessments"].pop()
                elif fault == "unanchored_note":
                    row["supplementary_notes"] = [{"kind": "uncertainty", "merged_ids": [999], "note": "Synthetic note"}]
                elif fault == "unknown_issue":
                    row["source_issue_dispositions"] = [{"issue_id": "unknown", "disposition": "not_used_in_claims", "reason": "Synthetic"}]
                else:
                    result["cases"][0]["claims"] = ["[Agent clarification] Invented speaker motive"]
            return result, meta
    with pytest.raises(ValueError, match="validation failed"):
        pipeline.run(cfg, through="level2", backend=Bad())
    assert not (Path(cfg["output_root"]) / cfg["run_id"] / "SYN1/level2.json").exists()


def test_unresolved_case_limits_principle_not_other_cases(cfg):
    cfg = candidate(cfg)
    class Uncertain(CandidateModel):
        def complete(self, *args):
            result, meta = super().complete(*args)
            if "case_assessments" in result:
                result["case_assessments"][0]["status"] = "unresolved"
            return result, meta
    pipeline.run(cfg, through="level3", backend=Uncertain())
    root = Path(cfg["output_root"]) / cfg["run_id"] / "SYN1"
    q = read_json(root / "level3.json")["qualification"]["SYN1:P1"]
    assert q["confidence"]["overall"] == "low"
    assert not q["eligible_for_level4"]
    cases = read_json(root / "level2.json")["evidence"]
    assert cases["SYN1:R2"]["candidate_source_assessment"]["status"] == "usable_with_limits"


def test_candidate_cannot_be_reused_as_reviewed(cfg):
    cfg = candidate(cfg)
    pipeline.run(cfg, through="level1", backend=CandidateModel())
    cfg["extraction_mode"] = "reviewed"
    with pytest.raises(ValueError, match="Incompatible knowledge schema"):
        pipeline.run(cfg, through="level1", validate_only=True)


def test_one_structural_repair_gets_exact_failure_and_preserves_attempt(cfg):
    cfg = candidate(cfg)
    cfg["episodes"] = cfg["episodes"][:1]
    cfg["retry_count"] = 1
    class Repair(CandidateModel):
        def complete(self, prompt, context, schema):
            result, meta = super().complete(prompt, context, schema)
            if len(self.calls) == 1:
                result["threads"][0]["merged_ids"] = [999]
            else:
                feedback = context["validation_repair"]
                assert feedback["previous_proposal"]["threads"][0]["merged_ids"] == [999]
                assert "evidence" in feedback["validation_error"]
                assert feedback["previous_call_sha256"]
            return result, meta
    model = Repair()
    pipeline.run(cfg, through="level1", backend=model)
    root = Path(cfg["output_root"]) / cfg["run_id"]
    calls = [read_json(p) for p in sorted((root / "calls").glob("*.json"))]
    assert [c["status"] for c in calls] == ["failed", "completed"]
    assert calls[1]["repair_from_sha256"]
    assert calls[0]["response"]["threads"][0]["merged_ids"] == [999]


def test_valid_candidate_proposal_can_be_revalidated_without_inference(cfg, monkeypatch):
    cfg = candidate(cfg)
    cfg["episodes"] = cfg["episodes"][:1]
    original = pipeline.derive

    def old_validator(*args):
        original(*args)
        raise ValueError("Synthetic obsolete candidate restriction")

    monkeypatch.setattr(pipeline, "derive", old_validator)
    first = CandidateModel()
    with pytest.raises(ValueError, match="validation failed"):
        pipeline.run(cfg, through="level1", backend=first)
    calls_root = Path(cfg["output_root"]) / cfg["run_id"] / "calls"
    failed = next(calls_root.glob("*.json"))
    pinned = sha256_file(failed)
    monkeypatch.setattr(pipeline, "derive", original)
    resumed = CandidateModel()
    report = pipeline.run(cfg, through="level1", backend=resumed)
    assert not resumed.calls
    assert report["model_calls_this_execution"] == 0
    assert report["revalidated_candidate_stages"] == 1
    assert sha256_file(failed) == pinned
    recovery = read_json(sorted(calls_root.glob("*.json"))[-1])
    assert recovery["status"] == "revalidated"
    assert recovery["usage"]["recovered_call_sha256"] == pinned
    assert recovery["usage"]["usage"] is None
    assert pipeline.run(cfg, through="level1", validate_only=True)
