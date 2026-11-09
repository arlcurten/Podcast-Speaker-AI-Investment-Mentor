"""Strict reviewed accounting and candidate-only partial-claim omission notes."""
import copy
import json

import pytest

from knowledge.coverage import coverage


@pytest.fixture
def accounting():
    source = {"segments": [{"merged_id": i} for i in range(6)]}
    episode_map = {"threads": [{"thread_id": f"SYN:T{n}", "merged_ids": list(range(start, start + 3))}
                               for n, start in ((1, 0), (2, 3))]}
    content = {"cases": [
        {"record_id": "SYN:R1", "thread_ids": ["SYN:T1"], "merged_ids": [0]},
        {"record_id": "SYN:R2", "thread_ids": ["SYN:T2"], "merged_ids": [3, 4, 5]}],
        "thread_dispositions": [
            {"thread_id": "SYN:T1", "record_ids": ["SYN:R1"], "status": "covered",
             "unextracted_regions": [{"merged_ids": [1, 2], "reason": "Synthetic uncertainty"}]},
            {"thread_id": "SYN:T2", "record_ids": ["SYN:R2"], "status": "covered", "unextracted_regions": []}]}
    return source, episode_map, content


def diagnostics(accounting):
    source, episode_map, content = accounting
    before = copy.deepcopy(accounting)
    with pytest.raises(ValueError, match="Thread regions must be covered") as error:
        coverage("level2", content, source, episode_map)
    assert accounting == before
    assert len(str(error.value)) <= 2000
    return json.loads(str(error.value).split("Merged-ID issues: ", 1)[1])


@pytest.mark.parametrize("skipped, expected", [
    ([1], {"missing": [2]}),
    ([0, 1, 2], {"overlap": [0]}),
    ([1, 2, 99], {"extra": [99]}),
    ([1, 2, 2], {"duplicate_unextracted": [2]}),
    ([99, 1, 0, 1], {"missing": [2], "overlap": [0], "extra": [99], "duplicate_unextracted": [1]}),
])
def test_exact_thread_and_merged_ids_for_each_failure(accounting, skipped, expected):
    accounting[2]["thread_dispositions"][0]["unextracted_regions"][0]["merged_ids"] = skipped
    assert diagnostics(accounting) == {"threads": [{"thread_id": "SYN:T1", **expected}], "omitted_threads": 0}


def test_all_affected_threads_are_reported_without_source_text(accounting):
    accounting[2]["thread_dispositions"][0]["unextracted_regions"] = []
    accounting[2]["cases"][1]["merged_ids"] = [3]
    assert diagnostics(accounting) == {"threads": [
        {"thread_id": "SYN:T1", "missing": [1, 2]},
        {"thread_id": "SYN:T2", "missing": [4, 5]}], "omitted_threads": 0}


def test_duplicate_skipped_ids_across_regions_are_identified(accounting):
    accounting[2]["thread_dispositions"][0]["unextracted_regions"].append(
        {"merged_ids": [2], "reason": "Another synthetic note"})
    assert diagnostics(accounting)["threads"] == [{"thread_id": "SYN:T1", "duplicate_unextracted": [2]}]


def test_partial_subclaim_caveat_does_not_relax_strict_overlap_rule(accounting):
    accounting[2]["thread_dispositions"][0]["unextracted_regions"].append({
        "merged_ids": [0], "reason": "A fictional name is unclear; a separate clear decision in this region is used."})
    assert diagnostics(accounting)["threads"] == [{"thread_id": "SYN:T1", "overlap": [0]}]


def test_valid_accounting_result_is_unchanged(accounting):
    source, episode_map, content = accounting
    assert coverage("level2", content, source, episode_map) == {
        "source_segment_count": 6, "thread_count": 2, "case_segment_count": 4,
        "threads_without_cases": [], "unextracted_thread_segment_count": 2,
        "unaccounted_thread_segment_count": 0,
        "scope": "Accounting only; exclusion reasons and topic completeness need source-grounded review."}


def test_candidate_retains_partial_omissions_without_counting_extra_evidence(accounting):
    source, episode_map, content = accounting
    source["extraction_mode"] = "candidate"
    notes = content["thread_dispositions"][0]["unextracted_regions"]
    notes.append({"merged_ids": [0, 2], "reason": "Unclear fictional entity, not needed for the clear decision."})
    before = copy.deepcopy(content)
    result = coverage("level2", content, source, episode_map)
    assert content == before
    assert result["case_segment_count"] == 4
    assert result["unextracted_thread_segment_count"] == 2
    assert result["repeated_omission_mention_count"] == 1
    assert result["partial_extraction_notes"] == [{
        "thread_id": "SYN:T1", "merged_ids": [0], "reason": notes[-1]["reason"],
        "authority": "model_reported_omission"}]


@pytest.mark.parametrize("skipped", [[1], [1, 2, 99]])
def test_candidate_still_rejects_missing_or_foreign_regions(accounting, skipped):
    accounting[0]["extraction_mode"] = "candidate"
    accounting[2]["thread_dispositions"][0]["unextracted_regions"][0]["merged_ids"] = skipped
    assert diagnostics(accounting)["threads"]


def test_large_missing_id_list_has_explicit_bounded_omissions(accounting):
    source, episode_map, content = accounting
    source["segments"] = [{"merged_id": i} for i in range(2000)]
    episode_map["threads"][0]["merged_ids"] = [0, *range(6, 2000)]
    content["thread_dispositions"][0]["unextracted_regions"] = []
    assert diagnostics(accounting) == {"threads": [{
        "thread_id": "SYN:T1", "missing": list(range(6, 26)),
        "omitted_id_counts": {"missing": 1974}}], "omitted_threads": 0}


def test_many_affected_threads_fit_repair_limit_with_explicit_omitted_count():
    source = {"segments": [{"merged_id": i} for i in range(100)]}
    episode_map = {"threads": [{"thread_id": f"SYN:T{i}", "merged_ids": [i]} for i in range(100)]}
    content = {"cases": [], "thread_dispositions": [
        {"thread_id": t["thread_id"], "record_ids": [], "status": "insufficient_source", "unextracted_regions": []}
        for t in episode_map["threads"]]}
    result = diagnostics((source, episode_map, content))
    assert 0 < len(result["threads"]) < 100
    assert result["omitted_threads"] == 100 - len(result["threads"])
    assert result["threads"] == [{"thread_id": f"SYN:T{i}", "missing": [i]} for i in range(len(result["threads"]))]
