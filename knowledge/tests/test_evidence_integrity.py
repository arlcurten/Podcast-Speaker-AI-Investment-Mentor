"""Adversarial synthetic regressions for coverage and abstraction provenance."""
import copy
from pathlib import Path

import pytest

from knowledge import pipeline
from knowledge.provenance import _counterevidence_accounting, qualify, provenance_statistics
from knowledge.release import publish
from knowledge.schemas import VERSION, schema, validate_proposal
from knowledge.tests.test_pipeline import cfg, FakeModel, case, principle
from transcription.common import atomic_write_json, read_json, sha256_file
from transcription.prepare import prepare


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


def additional_counterevidence(kind="counterexample", status="retained"):
    p, required = cross(), parent()
    p["counterevidence_dispositions"] = [disposition()]
    p["contradictions"] = copy.deepcopy(required["contradictions"])
    extra = {**principle("SYN2:P2", ["SYN2:R2"]), "episode_id": "SYN2", "eligible_for_level4": False}
    field = "counterexamples" if kind == "counterexample" else "contradictions"
    extra[field] = [{"record_id": "SYN2:R3", "explanation": "Synthetic relevant evidence beyond support overlap"}]
    p["counterevidence_dispositions"].append({
        "source_principle_id": "SYN2:P2", "record_id": "SYN2:R3", "kind": kind,
        "disposition": status, "reason": "Synthetic case-local comparison with the supplied counterevidence"})
    if status != "context_excluded":
        p[field].extend(copy.deepcopy(extra[field]))
    return p, {"SYN1:P1": required, "SYN2:P2": extra}


@pytest.mark.parametrize("kind", ["counterexample", "contradiction"])
@pytest.mark.parametrize("status", ["retained", "context_excluded", "unresolved"])
def test_additional_exact_upstream_counterevidence_is_allowed_without_support_inflation(cases, kind, status):
    p, upstream = additional_counterevidence(kind, status)
    before = provenance_statistics(p["supporting_records"], cases, p["context_relationships"])
    q = qualify(p, cases, "level4", {}, upstream)
    accounting = q["counterevidence_accounting"]
    assert accounting["upstream_link_count"] == 2
    assert accounting["required_link_count"] == accounting["additional_link_count"] == 1
    assert accounting["unique_record_count"] == 2
    assert accounting[status] == (2 if status == "retained" else 1)
    assert q["provenance"] == before
    assert q["supporting_records"] == ["SYN1:R1", "SYN2:R1"]
    assert q["provenance"]["independent_source_context_count"] == 2
    assert q["confidence"]["overall"] != "high"
    if status == "unresolved":
        assert q["confidence"]["overall"] == "low"
        assert not q["eligible_for_online_reasoning"]


@pytest.mark.parametrize("mutation", ["wrong_parent", "wrong_record", "wrong_kind", "missing_case",
                                      "duplicate", "missing_required", "blank_reason", "nontext_reason",
                                      "retained_but_absent", "excluded_but_retained", "unknown_disposition"])
def test_additional_counterevidence_keeps_exact_link_and_disposition_checks(cases, mutation):
    p, upstream = additional_counterevidence()
    row = p["counterevidence_dispositions"][-1]
    if mutation == "wrong_parent":
        row["source_principle_id"] = "SYN1:P1"  # Existing parent, but not this tuple.
    elif mutation == "wrong_record":
        row["record_id"] = "SYN1:R3"  # Existing case and upstream link, but under another parent/kind.
    elif mutation == "wrong_kind":
        row["kind"] = "contradiction"
        p["contradictions"].extend(p.pop("counterexamples"))
        p["counterexamples"] = []
    elif mutation == "missing_case":
        del cases["SYN2:R3"]
        row["disposition"] = "context_excluded"
        p["counterexamples"] = []
    elif mutation == "duplicate":
        p["counterevidence_dispositions"].append(copy.deepcopy(row))
    elif mutation == "missing_required":
        p["counterevidence_dispositions"].pop(0)
    elif mutation == "blank_reason":
        row["reason"] = " \n\t"
    elif mutation == "nontext_reason":
        row["reason"] = None
    elif mutation == "retained_but_absent":
        p["counterexamples"] = []
    elif mutation == "excluded_but_retained":
        row["disposition"] = "context_excluded"
    else:
        row["disposition"] = "ignored"
    with pytest.raises(ValueError):
        qualify(p, cases, "level4", {}, upstream)


def test_optional_links_do_not_make_other_nonoverlapping_links_required(cases):
    p, upstream = additional_counterevidence()
    upstream["SYN2:P2"]["contradictions"] = [
        {"record_id": "SYN2:R2", "explanation": "Synthetic unselected additional link"}]
    accounting = qualify(p, cases, "level4", {}, upstream)["counterevidence_accounting"]
    assert accounting["upstream_link_count"] == 2
    assert accounting["required_link_count"] == accounting["additional_link_count"] == 1


def test_optional_links_can_be_selected_when_no_parent_overlaps_support(cases):
    p, upstream = additional_counterevidence()
    del upstream["SYN1:P1"]
    p["counterevidence_dispositions"].pop(0)
    p["contradictions"] = []
    accounting = qualify(p, cases, "level4", {}, upstream)["counterevidence_accounting"]
    assert accounting["required_link_count"] == 0
    assert accounting["additional_link_count"] == accounting["upstream_link_count"] == 1


def test_same_record_in_multiple_upstream_links_is_not_independent_evidence(cases):
    p, upstream = additional_counterevidence("contradiction")
    extra = upstream.pop("SYN2:P2")
    extra.update(principle_id="SYN1:P2", episode_id="SYN1", supporting_records=["SYN1:R2"])
    extra["contradictions"][0]["record_id"] = "SYN1:R3"
    upstream["SYN1:P2"] = extra
    p["counterevidence_dispositions"][-1].update(source_principle_id="SYN1:P2", record_id="SYN1:R3")
    p["contradictions"] = copy.deepcopy(upstream["SYN1:P1"]["contradictions"])
    baseline = provenance_statistics(p["supporting_records"], cases, p["context_relationships"])
    q = qualify(p, cases, "level4", {}, upstream)
    assert q["counterevidence_accounting"]["upstream_link_count"] == 2
    assert q["counterevidence_accounting"]["unique_record_count"] == 1
    assert q["provenance"] == baseline


def test_same_record_different_kinds_need_distinct_exact_upstream_links(cases):
    p, upstream = additional_counterevidence()
    link = {"record_id": "SYN2:R3", "explanation": "Synthetic separately classified contradiction"}
    upstream["SYN2:P2"]["contradictions"] = [link]
    p["contradictions"].append(copy.deepcopy(link))
    p["counterevidence_dispositions"].append({**p["counterevidence_dispositions"][-1], "kind": "contradiction"})
    accounting = qualify(p, cases, "level4", {}, upstream)["counterevidence_accounting"]
    assert accounting["upstream_link_count"] == 3
    assert accounting["required_link_count"] == 1 and accounting["additional_link_count"] == 2
    assert accounting["unique_record_count"] == 2


@pytest.mark.parametrize("status", ["retained", "context_excluded", "unresolved"])
def test_required_only_accounting_preserves_exact_legacy_shape(cases, status):
    p, upstream = additional_counterevidence()
    p["counterevidence_dispositions"] = [disposition(status)]
    p["counterexamples"] = []
    if status == "context_excluded":
        p["contradictions"] = []
    expected = {"upstream_link_count": 1, "retained": int(status == "retained"),
                "context_excluded": int(status == "context_excluded"), "unresolved": int(status == "unresolved"),
                "unique_record_count": 1,
                "scope": "Link counts are not independent evidence counts; exclusions require semantic review."}
    assert _counterevidence_accounting(p, cases, upstream) == expected
    # Extra unselected upstream links must not change any existing qualification field.
    assert qualify(p, cases, "level4", {}, upstream) == qualify(p, cases, "level4", {}, {"SYN1:P1": parent()})


def test_no_counterevidence_and_level3_qualifications_stay_unchanged(cases):
    p = cross()
    assert _counterevidence_accounting(p, cases, {}) == {
        "upstream_link_count": 0, "retained": 0, "context_excluded": 0, "unresolved": 0,
        "unique_record_count": 0,
        "scope": "Link counts are not independent evidence counts; exclusions require semantic review."}
    ancestor = parent()
    assert qualify(ancestor, cases, "level3")["counterevidence_accounting"] == {}


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


def test_interrupted_decision_revision_preserves_both_regions_and_unknown_result(cfg, tmp_path):
    """Hand-authored extraction checks transport, not model semantic quality."""
    entry = cfg["episodes"][0]
    raw = read_json(Path(entry["raw"]))
    texts = [
        "Fictional host: In the inventory decision last year I assumed the supplier's new counts "
        "were comparable to its old counts, and added a bounded position.",
        "Fictional programme logistics: the next recording will be on a different day.",
        "Fictional host returning to that inventory decision: the supplier then disclosed a new "
        "counting definition. I had treated incomparable counts as confirmation. I stopped adding "
        "and reduced the position while checking comparable observations. This explanation is my "
        "own interpretation; I have not reported the eventual profit or loss.",
    ]
    for segment, text in zip(raw["segments"], texts):
        segment["text"] = text
    raw_path, review_path, norm_path = [tmp_path / f"revision-{kind}.json" for kind in ("raw", "review", "normalized")]
    atomic_write_json(raw_path, raw)
    review = read_json(Path(entry["review"]))
    review["transcript_sha256"] = sha256_file(raw_path)
    atomic_write_json(review_path, review)
    prepare(raw_path, norm_path, review_path)
    source = pipeline.load_source(raw_path, norm_path, review_path)
    l1 = {"episode_id": "SYN1", "episode_summary": "Synthetic interrupted decision revision",
          "threads": [{"thread_id": "SYN1:T1", "title": "Inventory decision and correction",
                       "summary": "One event revisited", "domains": ["investment"],
                       "merged_ids": [0, 2], "related_threads": []}],
          "excluded_regions": [{"merged_ids": [1], "category": "logistics", "reason": "Recording schedule only"}]}
    pipeline.derive("level1", l1, source, [])
    row = case("SYN1", 1)
    row.update(title="Synthetic inventory revision", merged_ids=[0, 2],
               claims=["Host reports adding, then reducing a bounded position"],
               reasoning_steps=["Assumed count comparability and added", "Supplier disclosed a definition change",
                                "Host attributes revision to that comparability error", "Stopped adding and reduced"],
               assumptions=["Counts were comparable"], conditions=["Same inventory decision"],
               preferred_actions=["Stop adding; reduce while verifying comparability"],
               decision_preferences=["Require comparable observations"],
               view_change_conditions=["Supplier discloses changed counting definition"],
               speaker_behavior=["Reports correcting an earlier assumption"],
               risks=[], exceptions=[], contradictions=[],
               uncertainty=["Eventual profit or loss not reported", "Causal explanation is the host's interpretation"],
               decision_context={"kind": "historical_event", "description": "Inventory decision last year",
                                 "event_reference": "Inventory decision", "event_time": None})
    l2 = {"episode_id": "SYN1", "cases": [row], "thread_dispositions": [
        {"thread_id": "SYN1:T1", "status": "covered", "record_ids": [row["record_id"]],
         "explanation": "Initial decision and resumed correction retained", "unextracted_regions": []}]}
    resolved, _, _ = pipeline.derive("level2", l2, source, [{"stage": "level1", "content": l1}])
    upstream = [{"stage": "level2", "content": l2, "evidence": resolved,
                 "source": {"publication_date": source["publication_date"]}}]
    context = pipeline.context("level3", source, upstream)
    forwarded = context["cases"][0]
    assert all(forwarded[key] == value for key, value in row.items())
    assert resolved[row["record_id"]]["source_segment_ids"] == [0, 2]
    assert [r["merged_ids"] for r in forwarded["evidence"]["timestamp_regions"]] == [[0], [2]]
    p = {**principle("SYN1:P1", [row["record_id"]]), "episode_id": "SYN1"}
    qualified = qualify(p, {row["record_id"]: forwarded}, "level3")
    assert qualified["provenance"]["independent_source_context_count"] == 1
    assert qualified["principle_type"] == "case_derived_hypothesis"
    assert not qualified["eligible_for_level4"]


@pytest.mark.parametrize("result", ["a profit", "a loss", "no known outcome"])
def test_narrated_outcomes_do_not_override_context_independence(cases, result):
    for rid in ("SYN1:R1", "SYN2:R1"):
        cases[rid]["decision_context"].update(kind="historical_event", event_reference="Same fictional trade")
    before = qualify(cross(), cases, "level4")
    for rid in ("SYN1:R1", "SYN2:R1"):
        cases[rid]["claims"].append(f"Host reports {result}; not independently verified")
    after = qualify(cross(), cases, "level4")
    assert after["confidence"] == before["confidence"]
    assert after["provenance"]["independent_source_context_count"] == 1
    assert not after["eligible_for_online_reasoning"]
