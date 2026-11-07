from __future__ import annotations

import copy
import json
from itertools import combinations
from pathlib import Path

import pytest

from knowledge import pipeline
from knowledge.provenance import case_catalog, qualify, provenance_statistics, compact_ids
from transcription.common import atomic_write_json, read_json, sha256_file
from transcription.prepare import prepare


def case(eid, n):
    return {"record_id": f"{eid}:R{n}", "title": f"Synthetic case {n}", "thread_ids": [f"{eid}:T{n}"],
            "speaker_attribution": "host", "claims": ["Wait for comparable evidence before adding exposure"],
            "reasoning_steps": ["Check comparable observations, then decide position size"],
            "assumptions": ["Observations are reliable"], "conditions": ["Comparable data exists"],
            "risks": ["Missing an early opportunity"], "exceptions": ["Small bounded experiment"],
            "uncertainty": ["Outcome remains uncertain"], "contradictions": [],
            "preferred_actions": ["Wait"], "decision_preferences": ["Accept missed upside"],
            "view_change_conditions": ["New contrary evidence"], "speaker_behavior": ["Revises on contrary evidence"],
            "tags": ["evidence", "risk"], "domains": ["investment"], "merged_ids": [n - 1],
            "decision_context": {"kind": "hypothetical_scenario", "description": f"Fictional decision {eid} context {n}",
                                 "event_reference": None, "event_time": None}}


def independent_pairs(refs):
    return [{"record_ids": list(pair), "relationship": "independent", "basis": "distinct_decision_scenarios",
             "explanation": "Separate fictional decisions, not repeat narrations of one event"}
            for pair in combinations(refs, 2)]


def principle(pid, refs):
    return {"principle_id": pid, "name": "Synthetic conditional preference", "domains": ["investment"],
            "decision_context": "Uncertain opportunity with comparable data", "core_preference": "Accept missed upside until comparable evidence exists",
            "reasoning_steps": ["Check comparability", "Set bounded exposure"], "preferred_actions": ["Wait"],
            "conditions": ["Comparable observations can become available"], "exceptions": ["Bounded small experiment"],
            "supporting_records": refs, "counterexamples": [], "contradictions": [], "principle_type": "recurring_principle",
            "distinctive_behavior": "Explicitly accepts missed upside for data comparability",
            "decision_tradeoff": {"option_a": "Enter early", "option_b": "Wait", "preferred_option": "Wait",
                                  "reason_for_preference": "Comparable evidence", "cost_of_preference": "Missed upside",
                                  "switch_conditions": ["Bounded risk experiment"]},
            "falsification_conditions": ["Repeated unconditional entries with unbounded downside"],
            "evidence_summary": "Two separate synthetic decision contexts",
            "context_relationships": independent_pairs(refs),
            "proposed_assessment": {"evidence_strength": "high", "speaker_specificity": "medium",
                                    "condition_clarity": "high", "contradiction_severity": "none", "rationale": "Synthetic contexts"}}


class FakeModel:
    def __init__(self):
        self.calls = []
    def complete(self, prompt, context, schema):
        props = schema["properties"]
        eid = (context.get("episode") or {}).get("episode_id")
        if "threads" in props:
            stage = "level1"
            result = {"episode_id": eid, "episode_summary": "Synthetic investing discussion", "excluded_regions": [], "threads": [
                {"thread_id": f"{eid}:T{n}", "title": f"Context {n}", "summary": "Independent synthetic context",
                 "domains": ["investment"], "merged_ids": [0] if n == 1 else [1, 2], "related_threads": []} for n in [1, 2]]}
        elif "cases" in props:
            stage = "level2"
            result = {"episode_id": eid, "cases": [case(eid, n) for n in [1, 2]]}
            result["cases"][1]["merged_ids"] = [1, 2]
            result["thread_dispositions"] = [
                {"thread_id": f"{eid}:T{n}", "status": "covered", "record_ids": [f"{eid}:R{n}"],
                 "explanation": "Case includes the context and its Q&A qualification", "unextracted_regions": []}
                for n in [1, 2]]
        elif "strategy" in props:
            stage = "level3"
            p = principle(f"{eid}:P1", [f"{eid}:R1", f"{eid}:R2"])
            result = {"episode_id": eid, "strategy": "principle_extraction", "strategy_reason": "Independent synthetic contexts",
                      "principles": [{**p, "episode_id": eid}]}
        else:
            stage = "level4"
            p = principle("CROSS:P1", ["SYN1:R1", "SYN2:R1"])
            result = {"consolidation_reason": "Independent synthetic episodes", "principles": [
                {**p, "derived_from": ["SYN1:P1", "SYN2:P1"], "shared_decision_logic": "Comparable evidence before exposure",
                 "context_differences": ["Different fictional decisions"], "principle_evolution": [],
                 "counterevidence_dispositions": []}]}
        self.calls.append(stage)
        return result, {"provider": "mock", "model": "synthetic", "usage": None, "cost_usd": None}


def synthetic_texts(episode_id):
    """Invented sources, never quotes or facts about the real podcast speaker."""
    first, second = ("component orders", "retail inventory") if episode_id == "SYN1" else ("software renewals", "freight demand")
    return [
        f"Fictional host: For {first}, I would wait for comparable observations before adding a normal position. "
        "Different measurement definitions make the headline growth unreliable. I accept missing early upside "
        "in exchange for checking comparability. If independent comparable reports confirm the improvement, "
        "I could add bounded exposure. If they contradict the thesis, I would reconsider, not average down automatically.",
        f"Fictional host, separate decision: For {second}, the popular story is not enough for my normal position. "
        "I first check whether the new observations measure the same thing as the old ones, then decide size. "
        "Waiting risks missing the opportunity and that is a cost I accept. A small experiment with a predefined "
        "maximum loss is an exception, not evidence that I support unlimited early entry. Results remain uncertain.",
        "Fictional listener: Should I buy every early story with a large position? Fictional host: No. "
        "The small experiment I described requires a known loss budget and an explicit exit condition. "
        "If losing that amount would impair necessary spending, I would not run the experiment. This is "
        "a conditional example, not a promise of profit or a rule for every market. No current company facts are given.",
    ]


@pytest.fixture
def cfg(tmp_path):
    entries = []
    for n in (1, 2):
        eid = f"SYN{n}"
        raw, review, norm = (tmp_path / f"{eid}-{suffix}.json" for suffix in ("raw", "review", "normalized"))
        atomic_write_json(raw, {"metadata": {"episode_id": eid, "model": "synthetic", "language": "en", "audio_duration_seconds": 150,
                                             "source_episode_metadata": {"episode_id": eid, "publication_date": f"202{n}-01-01"}},
                                "segments": [{"segment_id": i, "start": i * 50, "end": i * 50 + 45,
                                              "text": text} for i, text in enumerate(synthetic_texts(eid))]})
        atomic_write_json(review, {"schema_version": "transcript_review.v1", "episode_id": eid, "transcript_sha256": sha256_file(raw),
                                  "decision": "accept", "reviewer": "synthetic fixture", "reviewed_at": "2026-11-06T00:00:00Z",
                                  "method": "Synthetic data, not real source acceptance", "sampled_segment_ids": [0, 1, 2], "unresolved_critical_issues": []})
        prepare(raw, norm, review)
        entries.append({"episode_id": eid, "raw": str(raw), "review": str(review), "normalized": str(norm)})
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"schema_version": "knowledge_config.v1", "run_id": "synthetic", "output_root": str(tmp_path / "knowledge"),
                               "model": "synthetic", "episodes": entries}))
    return pipeline.load_config(path)


def test_complete_four_levels_and_no_paid_resume(cfg):
    model = FakeModel()
    report = pipeline.run(cfg, backend=model)
    assert report["model_calls_this_execution"] == 7
    assert model.calls == ["level1", "level2", "level3", "level1", "level2", "level3", "level4"]
    assert not report["ready_for_serving"]
    again = pipeline.run(cfg, backend=model)
    assert again["model_calls_this_execution"] == 0
    assert all(r["action"] == "reused" for r in again["artifacts"])
    assert pipeline.run(cfg, validate_only=True, backend=model)["model_calls_this_execution"] == 0
    root = Path(cfg["output_root"]) / cfg["run_id"]
    l3 = read_json(root / "SYN1/level3.json")
    assert l3["qualification"]["SYN1:P1"]["confidence"]["overall"] == "medium"
    assert not l3["qualification"]["SYN1:P1"]["eligible_for_online_reasoning"]
    l4 = read_json(root / "cross_episode/level4.json")
    q = l4["qualification"]["CROSS:P1"]
    assert q["runtime_mode_recommendation"] == "principle_assisted"
    assert q["provenance"]["supporting_episode_count"] == 2
    assert q["provenance"]["level1_thread_ids"] == ["SYN1:T1", "SYN2:T1"]


def test_plan_is_read_only(cfg):
    assert pipeline.run(cfg, dry_run=True)["maximum_initial_calls"] == 7
    assert not Path(cfg["output_root"]).exists()


@pytest.mark.parametrize("change", ["unaccepted", "review_hash", "mapping", "raw_text", "timestamps", "normalized_text", "annotations", "normalizer"])
def test_invalid_source_blocks_before_model(cfg, change):
    entry = cfg["episodes"][0]
    if change in {"unaccepted", "review_hash"}:
        p = Path(entry["review"]); d = read_json(p)
        d["decision"] = "uncertain" if change == "unaccepted" else "accept"
        d["method"] += " changed"
    else:
        p = Path(entry["normalized"]); d = read_json(p)
        if change == "mapping": d["segments"][0]["source_segment_ids"] = [999]
        elif change == "raw_text": d["segments"][0]["raw_text"] = "fabricated"
        elif change == "normalized_text": d["segments"][0]["normalized_text_zh_tw"] = "Fabricated normalized claim"
        elif change == "annotations": d["segments"][0]["terminology_annotations"] = [{"suggested_text": "Fabricated"}]
        elif change == "normalizer": d["metadata"]["opencc_version"] = "unknown"
        else: d["segments"][0]["end"] = 1000
    atomic_write_json(p, d)
    model = FakeModel()
    with pytest.raises(ValueError): pipeline.run(cfg, backend=model)
    assert not model.calls


def test_single_case_same_context_and_generic_constraints(cfg):
    pipeline.run(cfg, through="level2", backend=FakeModel())
    root = Path(cfg["output_root"]) / cfg["run_id"]
    cases = case_catalog([read_json(root / f"{eid}/level2.json") for eid in ("SYN1", "SYN2")])
    p = {**principle("SYN1:P1", ["SYN1:R1"]), "episode_id": "SYN1"}
    q = qualify(p, cases, "level3")
    assert q["principle_type"] == "case_derived_hypothesis"
    assert not q["eligible_for_level4"]
    p["supporting_records"].append("SYN1:R2")
    p["context_relationships"] = independent_pairs(p["supporting_records"])
    cases["SYN1:R2"]["thread_ids"] = cases["SYN1:R1"]["thread_ids"]
    q = qualify(p, cases, "level3")
    assert q["provenance"]["independent_source_context_count"] == 1
    assert q["confidence"]["overall"] == "low"
    p["supporting_records"] = ["SYN1:R1", "SYN2:R1"]
    p["context_relationships"] = independent_pairs(p["supporting_records"])
    p["derived_from"] = p["supporting_records"]
    p["proposed_assessment"]["speaker_specificity"] = "low"
    q = qualify(p, cases, "level4")
    assert q["genericness_status"] == "too_generic"
    assert not q["eligible_for_online_reasoning"]
    p["proposed_assessment"]["speaker_specificity"] = "medium"
    p["proposed_assessment"]["contradiction_severity"] = "major"
    assert not qualify(p, cases, "level4")["eligible_for_online_reasoning"]


def test_invalid_principle_provenance_fails_structurally(cfg):
    pipeline.run(cfg, through="level2", backend=FakeModel())
    root = Path(cfg["output_root"]) / cfg["run_id"]
    cases = case_catalog([read_json(root / f"{eid}/level2.json") for eid in ("SYN1", "SYN2")])
    p = {**principle("CROSS:P1", ["SYN1:R1", "SYN1:R2"]), "derived_from": ["SYN1:R1"]}
    with pytest.raises(ValueError, match="two independent"): qualify(p, cases, "level4")
    p["supporting_records"] = ["SYN1:R1", "SYN2:R1"]
    p["context_relationships"] = independent_pairs(p["supporting_records"])
    p["derived_from"] = ["INELIGIBLE:P1"]
    with pytest.raises(ValueError, match="ineligible"): qualify(p, cases, "level4")
    p["episode_id"] = "SYN1"
    with pytest.raises(ValueError, match="same episode"): qualify(p, cases, "level3")
    with pytest.raises(ValueError): provenance_statistics(["CROSS:P1"], cases)
    with pytest.raises(ValueError): provenance_statistics(["SYN1:R1", "SYN1:R1"], cases)
    cases["SYN1:R1"]["speaker_attribution"] = "listener"
    with pytest.raises(ValueError, match="Listener"): qualify(p, cases, "level3")


def test_invalid_model_reference_retry_once_no_partial_publish(cfg):
    class BadModel(FakeModel):
        def complete(self, *args):
            data, usage = super().complete(*args)
            data["threads"][0]["merged_ids"] = [999]
            return data, usage
    cfg["retry_count"] = 1
    model = BadModel()
    with pytest.raises(ValueError, match="validation failed"): pipeline.run(cfg, through="level1", backend=model)
    assert len(model.calls) == 2
    root = Path(cfg["output_root"]) / cfg["run_id"]
    assert len(list((root / "calls").glob("*.json"))) == 2
    assert not (root / "SYN1/level1.json").exists()


def test_version_mismatch_and_evidence_tamper_rejected(cfg):
    pipeline.run(cfg, backend=FakeModel())
    changed = copy.deepcopy(cfg); changed["model"] = "different-model"
    with pytest.raises(ValueError, match="incompatible"): pipeline.run(changed, validate_only=True)
    path = Path(cfg["output_root"]) / cfg["run_id"] / "SYN1/level2.json"
    d = read_json(path); d["evidence"]["SYN1:R1"]["timestamp_regions"][0]["end"] = 999
    atomic_write_json(path, d)
    with pytest.raises(ValueError, match="reconstruction"): pipeline.run(cfg, validate_only=True)


def test_compact_evidence_not_fake_contiguous_range():
    assert compact_ids([1, 2, 3, 7, 9, 10]) == {"source_segment_id_ranges": [{"start_id": 1, "end_id": 3}, {"start_id": 9, "end_id": 10}], "source_segment_ids": [7]}
