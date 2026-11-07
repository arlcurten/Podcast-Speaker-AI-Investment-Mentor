"""Adversarial synthetic regressions for coverage and abstraction provenance."""
import copy
from pathlib import Path

import pytest

from knowledge import pipeline
from knowledge.provenance import qualify, provenance_statistics
from knowledge.release import publish
from knowledge.schemas import VERSION, schema, validate_proposal
from knowledge.tests.test_pipeline import cfg, FakeModel, case, principle
from transcription.common import atomic_write_json, read_json, sha256_file


@pytest.fixture
def cases():
    return {f"{eid}:R{n}": {**case(eid, n), "episode_id": eid, "publication_date": date}
            for eid, date in (("SYN1", "2021-01-01"), ("SYN2", "2025-01-01")) for n in (1, 2, 3)}


def cross():
    p = principle("CROSS:P1", ["SYN1:R1", "SYN2:R1"])
    return {**p, "derived_from": p["supporting_records"], "shared_decision_logic": "Synthetic comparable decisions",
            "context_differences": [], "principle_evolution": [], "counterevidence_dispositions": []}


def parent():
    p = {**principle("SYN1:P1", ["SYN1:R1", "SYN1:R2"]), "episode_id": "SYN1"}
    p["contradictions"] = [{"record_id": "SYN1:R3", "explanation": "Synthetic opposite choice in comparable conditions"}]
    return p


def disposition(status="retained"):
    return {"source_principle_id": "SYN1:P1", "record_id": "SYN1:R3", "kind": "contradiction",
            "disposition": status, "reason": "Explicit synthetic source/condition comparison for this abstraction"}


@pytest.mark.parametrize("stage", ["level3", "level4"])
def test_multi_record_hypothesis_never_eligible(cases, stage):
    p = cross() if stage == "level4" else {**principle("SYN1:P1", ["SYN1:R1", "SYN1:R2"]), "episode_id": "SYN1"}
    p["principle_type"] = "case_derived_hypothesis"
    q = qualify(p, cases, stage)
    assert q["provenance"]["independent_source_context_count"] == 2
    assert not q["eligible_for_level4"] and not q["eligible_for_online_reasoning"]


@pytest.mark.parametrize("relationship,basis", [("same_context", "retelling"), ("unknown", "insufficient_information")])
def test_different_episodes_do_not_prove_independence(cases, relationship, basis):
    p = cross()
    p["context_relationships"][0].update(relationship=relationship, basis=basis)
    q = qualify(p, cases, "level4")
    assert q["provenance"]["supporting_episode_count"] == 2
    assert q["provenance"]["independent_source_context_count"] == 1
    assert q["confidence"]["overall"] == "low"
    assert not q["eligible_for_online_reasoning"]


def test_same_event_overrides_model_independence_and_dates(cases):
    for rid in ("SYN1:R1", "SYN2:R1"):
        cases[rid]["decision_context"].update(kind="historical_event", event_reference="Fictional supply decision A")
    q = qualify(cross(), cases, "level4")
    assert q["provenance"]["publication_years"] == ["2021", "2025"]
    assert q["provenance"]["overridden_independence_pairs"] == [["SYN1:R1", "SYN2:R1"]]
    assert not q["eligible_for_online_reasoning"]


def test_unassessed_pairs_are_not_implicitly_independent(cases):
    stats = provenance_statistics(["SYN1:R1", "SYN2:R1"], cases)
    assert stats["independent_source_context_count"] == 1
    assert stats["unknown_context_pairs"]
    p = cross(); p["context_relationships"] = []
    with pytest.raises(ValueError, match="Every supporting-case pair"):
        qualify(p, cases, "level4")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "non_support", "wrong_basis", "blank"])
def test_invalid_context_accounting_rejected(cases, mutation):
    p = cross()
    row = p["context_relationships"][0]
    if mutation == "missing": p["context_relationships"] = []
    elif mutation == "duplicate": p["context_relationships"].append(copy.deepcopy(row))
    elif mutation == "non_support": row["record_ids"][1] = "SYN1:R3"
    elif mutation == "wrong_basis": row["basis"] = "retelling"
    else: row["explanation"] = " "
    with pytest.raises(ValueError): qualify(p, cases, "level4")


def test_generic_statements_cannot_assert_independent_decisions(cases):
    cases["SYN1:R1"]["decision_context"]["kind"] = "general_statement"
    with pytest.raises(ValueError, match="Generic/unknown"):
        qualify(cross(), cases, "level4")


def test_distinct_context_control_is_still_eligible(cases):
    q = qualify(cross(), cases, "level4")
    assert q["eligible_for_online_reasoning"]
    assert q["runtime_mode_recommendation"] == "principle_assisted"


def test_source_overlap_caps_independence_even_in_different_threads(cases):
    p = {**principle("SYN1:P1", ["SYN1:R1", "SYN1:R2"]), "episode_id": "SYN1"}
    cases["SYN1:R2"]["merged_ids"] = [0]
    q = qualify(p, cases, "level3")
    assert q["provenance"]["overlapping_source_pairs"]
    assert q["provenance"]["independent_source_context_count"] == 1


@pytest.mark.parametrize("direct_cases", [False, True])
def test_known_l3_counterevidence_cannot_silently_disappear(cases, direct_cases):
    p, ancestor = cross(), parent()
    if not direct_cases: p["derived_from"] = ["SYN1:P1", "SYN2:R1"]
    with pytest.raises(ValueError, match="Known upstream counterevidence"):
        qualify(p, cases, "level4", {"SYN1:P1": ancestor})


@pytest.mark.parametrize("status", ["retained", "context_excluded", "unresolved"])
def test_counterevidence_disposition_controls_confidence(cases, status):
    p, ancestor = cross(), parent()
    p["counterevidence_dispositions"] = [disposition(status)]
    if status != "context_excluded": p["contradictions"] = ancestor["contradictions"]
    q = qualify(p, cases, "level4", {"SYN1:P1": ancestor})
    assert q["counterevidence_accounting"][status] == 1
    assert q["counterevidence_accounting"]["unique_record_count"] == 1
    if status == "unresolved":
        assert q["confidence"]["overall"] == "low"
        assert not q["eligible_for_online_reasoning"]
    elif status == "retained":
        assert q["confidence"]["contradiction_severity"] == "minor"
    else:
        assert not q["contradictions"]
        assert q["eligible_for_online_reasoning"]  # Requires subsequent source-grounded exclusion review.


@pytest.mark.parametrize("mutation", ["missing_link", "duplicate", "wrong_parent", "wrong_record", "blank_reason", "excluded_but_retained"])
def test_invalid_counterevidence_disposition_fails(cases, mutation):
    p = cross(); p["counterevidence_dispositions"] = [disposition()]
    p["contradictions"] = parent()["contradictions"]
    row = p["counterevidence_dispositions"][0]
    if mutation == "missing_link": p["contradictions"] = []
    elif mutation == "duplicate": p["counterevidence_dispositions"].append(copy.deepcopy(row))
    elif mutation == "wrong_parent": row["source_principle_id"] = "SYN2:P9"
    elif mutation == "wrong_record": row["record_id"] = "SYN2:R3"
    elif mutation == "blank_reason": row["reason"] = " "
    else: row["disposition"] = "context_excluded"
    with pytest.raises(ValueError): qualify(p, cases, "level4", {"SYN1:P1": parent()})


def test_ineligible_parent_cannot_hide_known_conflicts(cases):
    ancestor = parent()
    ancestor["proposed_assessment"]["contradiction_severity"] = "major"
    assert not qualify(ancestor, cases, "level3")["eligible_for_level4"]
    with pytest.raises(ValueError, match="Known upstream counterevidence"):
        qualify(cross(), cases, "level4", {}, {"SYN1:P1": ancestor})


@pytest.fixture
def mapped(cfg):
    source = pipeline.sources(cfg)["SYN1"]
    model = FakeModel()
    l1, _ = model.complete("", pipeline.context("level1", source, []), schema("level1"))
    artifact = {"stage": "level1", "content": l1}
    l2, _ = model.complete("", pipeline.context("level2", source, [artifact]), schema("level2"))
    return source, artifact, l2


def test_partial_l1_coverage_fails(mapped):
    source, l1, _ = mapped
    l1["content"]["threads"] = l1["content"]["threads"][:1]
    with pytest.raises(ValueError, match="every source segment"):
        pipeline.derive("level1", l1["content"], source, [])


def test_explained_exclusions_are_accounted_not_assumed_semantically_correct(mapped):
    source, l1, _ = mapped
    l1["content"]["threads"] = l1["content"]["threads"][:1]
    l1["content"]["excluded_regions"] = [{"merged_ids": [1, 2], "category": "non_reasoning", "reason": "Synthetic exclusion requiring actual content review"}]
    _, q, warnings = pipeline.derive("level1", l1["content"], source, [])
    assert q["coverage"]["excluded_segment_count"] == 2
    assert warnings  # Validation must not claim this invented exclusion is correct.


@pytest.mark.parametrize("mids", [[0], [999], [1, 1]])
def test_invalid_l1_exclusion_rejected(mapped, mids):
    source, l1, _ = mapped
    l1["content"]["excluded_regions"] = [{"merged_ids": mids, "category": "logistics", "reason": "Synthetic"}]
    with pytest.raises(ValueError): pipeline.derive("level1", l1["content"], source, [])


def test_l2_source_must_match_declared_thread(mapped):
    source, l1, l2 = mapped
    l2["cases"][0]["merged_ids"] = [2]
    with pytest.raises(ValueError, match="evidence disagrees"):
        pipeline.derive("level2", l2, source, [l1])


def test_every_declared_thread_must_contribute_source(mapped):
    source, l1, l2 = mapped
    l2["cases"][0]["thread_ids"].append("SYN1:T2")
    with pytest.raises(ValueError, match="evidence disagrees"):
        pipeline.derive("level2", l2, source, [l1])


@pytest.mark.parametrize("mutation", ["omitted_thread", "omitted_region", "duplicate", "false_covered", "false_refs"])
def test_l2_coverage_disposition_is_checked(mapped, mutation):
    source, l1, l2 = mapped
    if mutation == "omitted_thread": l2["thread_dispositions"].pop()
    elif mutation == "omitted_region": l2["cases"][1]["merged_ids"] = [1]
    elif mutation == "duplicate": l2["thread_dispositions"].append(copy.deepcopy(l2["thread_dispositions"][0]))
    elif mutation == "false_covered": l2["thread_dispositions"][1]["status"] = "no_reusable_reasoning"
    else: l2["thread_dispositions"][1]["record_ids"] = ["SYN1:R1"]
    with pytest.raises(ValueError): pipeline.derive("level2", l2, source, [l1])


def test_intentional_l2_omission_is_reported(mapped):
    source, l1, l2 = mapped
    l2["cases"].pop()
    l2["thread_dispositions"][1].update(status="insufficient_source", record_ids=[],
        unextracted_regions=[{"merged_ids": [1, 2], "reason": "Synthetic attribution uncertainty needs review"}])
    _, q, warnings = pipeline.derive("level2", l2, source, [l1])
    assert q["coverage"]["threads_without_cases"] == ["SYN1:T2"]
    assert q["coverage"]["unaccounted_thread_segment_count"] == 0
    assert warnings


def test_explicitly_nonreasoning_episode_can_bypass(mapped):
    source, l1, _ = mapped
    l1["content"].update(threads=[], excluded_regions=[{
        "merged_ids": [0, 1, 2], "category": "logistics", "reason": "Synthetic all-logistics classification to review"}])
    pipeline.derive("level1", l1["content"], source, [])
    _, q, warnings = pipeline.derive("level2", {"episode_id": "SYN1", "cases": [], "thread_dispositions": []}, source, [l1])
    assert q["coverage"]["case_segment_count"] == 0 and warnings


def test_old_schema_requires_explicit_new_version_without_model_call(cfg):
    pipeline.run(cfg, backend=FakeModel())
    path = Path(cfg["output_root"]) / cfg["run_id"] / "SYN1/level1.json"
    data = read_json(path); data["schema_version"] = "knowledge.v1"
    atomic_write_json(path, data)
    original = sha256_file(path)
    model = FakeModel()
    with pytest.raises(ValueError, match="regenerate with a new run_id"):
        pipeline.run(cfg, backend=model)
    assert not model.calls and sha256_file(path) == original
    assert VERSION == "knowledge.v2"


def test_schema_requires_new_accounting_fields(mapped):
    _, l1, l2 = mapped
    for stage, value, key in (("level1", l1["content"], "excluded_regions"), ("level2", l2, "thread_dispositions")):
        value.pop(key)
        with pytest.raises(ValueError, match="schema invalid"): validate_proposal(stage, value)


def test_release_retains_excluded_counterevidence_for_audit(cfg, tmp_path):
    class WithCounterevidence(FakeModel):
        def complete(self, *args):
            result, usage = super().complete(*args)
            if self.calls[-1] == "level3" and result["episode_id"] == "SYN1":
                result["principles"][0]["contradictions"] = [{"record_id": "SYN1:R2", "explanation": "Synthetic context difference"}]
            if self.calls[-1] == "level4":
                assert args[1]["upstream_counterevidence"][0]["contradictions"]
                row = disposition("context_excluded"); row["record_id"] = "SYN1:R2"
                result["principles"][0]["counterevidence_dispositions"] = [row]
            return result, usage

    pipeline.run(cfg, backend=WithCounterevidence())
    paths = pipeline.release_locations(cfg)
    review = {"schema_version": "knowledge_review.v1", "decision": "accept", "reviewer": "Synthetic test",
              "method": "Mock review, not real approval", "reviewed_at": "2026-11-06T00:00:00Z",
              "unresolved_critical_issues": [], "artifact_sha256": {p["logical_path"]: sha256_file(Path(p["path"])) for p in paths},
              "accepted_case_ids": ["SYN1:R1", "SYN2:R1", "SYN2:R2"], "accepted_principle_ids": ["CROSS:P1"]}
    review_path = tmp_path / "review.json"
    atomic_write_json(review_path, review)
    with pytest.raises(ValueError, match="context-excluded"):
        publish(cfg, review_path, tmp_path / "release.json")
    review["accepted_case_ids"].append("SYN1:R2")
    atomic_write_json(review_path, review)
    assert publish(cfg, review_path, tmp_path / "release.json")["principle_count"] == 1
