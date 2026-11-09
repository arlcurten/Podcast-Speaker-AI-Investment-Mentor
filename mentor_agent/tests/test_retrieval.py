"""Synthetic selection tests; no release approval or model calls."""

import copy

import pytest

from mentor_agent.retrieval import KnowledgeIndex


@pytest.fixture
def corpus():
    settings = {
        "lexical_weight": 0.65, "cosine_weight": 0.25, "metadata_weight": 0.10,
        "bm25_scale": 5.0, "minimum_score": 0.08, "domain_mismatch_multiplier": 0.1,
        "candidate_limit": 12, "case_limit": 6, "max_per_episode": 1,
        "search_query_limit": 3, "principle_limit": 2,
    }
    cases = {
        rid: {"record_id": rid, "episode_id": rid.split(":")[0], "merged_ids": [n],
              "title": "evidence" if n < 2 else "maintenance", "domains": ["investment"],
              "tags": [], "contradictions": [], "exceptions": []}
        for n, rid in enumerate(("SYN1:R1", "SYN2:R1", "SYN1:R2", "SYN2:R2", "SYN3:R1"))
    }
    principles = {"CROSS:P1": {
        "principle_id": "CROSS:P1", "domains": ["investment"],
        "eligible_for_online_reasoning": True, "runtime_mode_recommendation": "principle_assisted",
        "supporting_records": ["SYN1:R1", "SYN2:R1"], "contradictions": [], "counterexamples": [],
        "counterevidence_dispositions": [{
            "source_principle_id": "SYN1:P1", "record_id": "SYN1:R2", "kind": "contradiction",
            "disposition": "context_excluded", "reason": "Different information horizon",
        }],
    }}
    return cases, principles, settings


def select(corpus):
    cases, principles, settings = corpus
    return KnowledgeIndex(cases, principles, settings).select("evidence", {
        "search_terms": [], "target_domains": ["investment"], "principle_ids": list(principles),
    })


def test_poor_match_excluded_source_is_required_context_not_conflict(corpus):
    cases, principles, settings = corpus
    settings["case_limit"] = 3
    original = copy.deepcopy(corpus)
    assert "SYN1:R2" not in {
        hit["record_id"] for hit in KnowledgeIndex(*corpus).search("evidence", ["investment"])
    }
    result = select(corpus)
    hits = {case["record_id"]: case["retrieval"] for case in result["cases"]}
    assert set(hits) == {"SYN1:R1", "SYN2:R1", "SYN1:R2"}
    assert result["principles"] == [principles["CROSS:P1"]]
    assert not result["rejected_principles"]
    assert hits["SYN1:R2"]["roles"] == ["context_difference"]
    reason = hits["SYN1:R2"]["role_reasons"][0]
    assert "Offline context exclusion" in reason
    assert "CROSS:P1" in reason and "SYN1:P1" in reason
    assert "Different information horizon" in reason
    assert "query applicability still requires comparison" in reason
    assert corpus == original


def test_excluded_source_counts_toward_per_principle_budget(corpus):
    corpus[2]["case_limit"] = 2
    result = select(corpus)
    assert not result["principles"]
    assert result["rejected_principles"] == [{
        "principle_id": "CROSS:P1", "reason": "Evidence bundle exceeds context limit",
    }]
    assert len(result["cases"]) <= 2


def test_excluded_sources_count_toward_combined_budget(corpus):
    _, principles, settings = corpus
    second = copy.deepcopy(principles["CROSS:P1"])
    second["principle_id"] = "CROSS:P2"
    second["counterevidence_dispositions"][0].update(record_id="SYN2:R2", source_principle_id="SYN2:P1")
    principles["CROSS:P2"] = second
    settings["case_limit"] = 3  # Each bundle fits, but their four-case union does not.
    result = select(corpus)
    assert not result["principles"]
    assert {row["principle_id"] for row in result["rejected_principles"]} == set(principles)
    assert all(row["reason"] == "Combined evidence exceeds context budget" for row in result["rejected_principles"])
    assert len(result["cases"]) <= 3


def test_shared_exclusions_deduplicate_cases_but_preserve_each_origin(corpus):
    _, principles, settings = corpus
    first = principles["CROSS:P1"]
    first["counterevidence_dispositions"].append({
        **first["counterevidence_dispositions"][0], "source_principle_id": "SYN1:P2",
        "kind": "counterexample", "reason": "Different objective",
    })
    second = copy.deepcopy(first)
    second["principle_id"] = "CROSS:P2"
    second["counterevidence_dispositions"] = [{
        **first["counterevidence_dispositions"][0], "reason": "Different position size",
    }]
    principles["CROSS:P2"] = second
    settings["case_limit"] = 3
    result = select(corpus)
    assert len(result["principles"]) == 2 and not result["rejected_principles"]
    assert len(result["cases"]) == len({case["record_id"] for case in result["cases"]}) == 3
    hit = next(case["retrieval"] for case in result["cases"] if case["record_id"] == "SYN1:R2")
    assert set(hit["roles"]) == {"context_difference"}
    reasons = hit["role_reasons"]
    assert len(reasons) == 3
    for pid, source, reason in (
        ("CROSS:P1", "SYN1:P1", "Different information horizon"),
        ("CROSS:P1", "SYN1:P2", "Different objective"),
        ("CROSS:P2", "SYN1:P1", "Different position size"),
    ):
        assert any(pid in text and source in text and reason in text for text in reasons)


def test_mixed_retained_and_excluded_links_preserve_existing_roles(corpus):
    _, principles, settings = corpus
    p = principles["CROSS:P1"]
    p["contradictions"] = [{"record_id": "SYN2:R2", "explanation": "Opposing comparable choice"}]
    p["counterexamples"] = [{"record_id": "SYN3:R1", "explanation": "Bounded exception"}]
    for kind, rid in (("contradiction", "SYN2:R2"), ("counterexample", "SYN3:R1")):
        p["counterevidence_dispositions"].append({
            "source_principle_id": "SYN2:P1", "record_id": rid, "kind": kind,
            "disposition": "retained", "reason": "Comparable scope retained",
        })
    settings["case_limit"] = 5
    result = select(corpus)
    hits = {case["record_id"]: case["retrieval"] for case in result["cases"]}
    assert len(result["cases"]) == len(hits) == 5
    assert not result["rejected_principles"]
    assert hits["SYN1:R2"]["roles"] == ["context_difference"]
    assert hits["SYN2:R2"]["roles"] == ["potential_conflict"]
    assert hits["SYN2:R2"]["role_reasons"] == ["Opposing comparable choice"]
    assert hits["SYN3:R1"]["roles"] == ["exception"]
    assert hits["SYN3:R1"]["role_reasons"] == ["Bounded exception"]


def test_same_source_excluded_for_one_principle_retained_for_another(corpus):
    _, principles, settings = corpus
    second = copy.deepcopy(principles["CROSS:P1"])
    second["principle_id"] = "CROSS:P2"
    second["counterevidence_dispositions"][0]["disposition"] = "retained"
    second["contradictions"] = [{"record_id": "SYN1:R2", "explanation": "Comparable for second abstraction"}]
    principles["CROSS:P2"] = second
    settings["case_limit"] = 3
    result = select(corpus)
    assert len(result["principles"]) == 2 and not result["rejected_principles"]
    assert len(result["cases"]) == len({case["record_id"] for case in result["cases"]}) == 3
    hit = next(case["retrieval"] for case in result["cases"] if case["record_id"] == "SYN1:R2")
    assert hit["roles"] == ["context_difference", "potential_conflict"]
    assert "CROSS:P1" in hit["role_reasons"][0] and "SYN1:P1" in hit["role_reasons"][0]
    assert "Different information horizon" in hit["role_reasons"][0]
    assert hit["role_reasons"][1] == "Comparable for second abstraction"


def test_unresolved_principle_remains_ineligible(corpus):
    p = corpus[1]["CROSS:P1"]
    p["counterevidence_dispositions"][0]["disposition"] = "unresolved"
    p["contradictions"] = [{"record_id": "SYN1:R2", "explanation": "Unresolved comparison"}]
    p.update(eligible_for_online_reasoning=False, runtime_mode_recommendation="not_eligible")
    result = select(corpus)
    assert not result["principles"]
    assert result["rejected_principles"] == [{"principle_id": "CROSS:P1", "reason": "Unavailable or not eligible"}]
    assert all("context_difference" not in case["retrieval"]["roles"] for case in result["cases"])
