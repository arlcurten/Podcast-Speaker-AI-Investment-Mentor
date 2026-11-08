from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from knowledge.tests.test_pipeline import cfg
from knowledge.tests.test_release import acceptance
from knowledge.release import publish
from mentor_agent.runtime import Mentor, load_config, finalize_route, validate_answer, compact_case
from mentor_agent.retrieval import KnowledgeIndex
from mentor_agent.service import create_app


class FakeMentor:
    def __init__(self, *, rejected=False, unresolved=False, current=False, empty=False, corrupt=False):
        self.rejected, self.unresolved, self.current, self.empty, self.corrupt = rejected, unresolved, current, empty, corrupt
        self.calls = []
    def complete(self, prompt, context, schema):
        props = schema["properties"]
        if "query_type" in props:
            self.calls.append("analysis")
            result = {"query_type": "scenario", "scenario_facts": ["Synthetic uncertain opportunity"],
                      "decision_being_asked": "Wait or take bounded exposure", "target_domains": ["investment"],
                      "missing_information": ["Downside limit"], "current_data_required": self.current,
                      "search_terms": ["evidence risk wait"] if not self.empty else ["zxqv"],
                      "principle_ids": [] if self.empty else ["CROSS:P1"]}
        elif "selected_route" in props:
            self.calls.append("applicability")
            ids = [c["record_id"] for c in context["cases"]]
            result = {"selected_route": "principle_assisted", "applicable_principles": [] if self.rejected else ["CROSS:P1"],
                      "rejected_principles": [{"principle_id": "CROSS:P1", "reason": "Different decision context"}] if self.rejected else [],
                      "supporting_cases": ids, "conflicting_evidence": [], "context_differences": [], "exceptions_triggered": [],
                      "unresolved_conflicts": [{"record_ids": ids[:2], "issue": "Synthetic opposing actions under unclear conditions", "missing_information": ["Time horizon"], "severity": "major"}] if self.unresolved else [],
                      "missing_information": [], "current_data_required": self.current, "confidence": "medium",
                      "conditional_conclusion": "If comparable evidence is missing, limit conclusions", "route_reasons": ["Synthetic evidence"]}
        else:
            self.calls.append("answer")
            ids = [c["record_id"] for c in context["cases"]]
            result = {"conditional_answer": "If evidence is comparable, consider bounded exposure; otherwise wait.",
                      "historical_evidence": [{"statement": "Synthetic cases accept missed upside for better evidence", "record_ids": ids if not self.corrupt else ["MISSING:R1"]}],
                      "interpretation": "A conditional framework, not a permanent speaker policy.", "conditions_and_risks": ["Evidence comparability"],
                      "conflicts_and_exceptions": ["Bounded experiments differ from unconditional entry"],
                      "missing_information": ["Downside limit"], "current_data_limitation": "No current data verified."}
        return result, {"provider": "mock", "cost_usd": None}


@pytest.fixture
def mentor_setup(cfg, tmp_path, monkeypatch):
    monkeypatch.delenv("MENTOR_RELEASE", raising=False)
    monkeypatch.delenv("MENTOR_MODEL_ID", raising=False)
    review = acceptance(cfg, tmp_path)
    release = tmp_path / "release.json"
    publish(cfg, review, release)
    settings = yaml.safe_load((Path(__file__).parents[1] / "config/default.yaml").read_text())
    settings["release"] = str(release)
    path = tmp_path / "mentor.yaml";path.write_text(yaml.safe_dump(settings))
    return load_config(path)


def test_principle_assisted_and_compact_source_links(mentor_setup):
    model = FakeMentor();mentor = Mentor(mentor_setup, backend=model)
    result = mentor.ask("Uncertain evidence: wait or add exposure?")
    assert result["route"] == "principle_assisted"
    assert model.calls == ["analysis", "applicability", "answer"]
    assert result["guards"][0]["status"] == "passed"
    assert all(c["regions"] and c["record_id"].startswith("SYN") for c in result["citations"])
    assert all(len(c["knowledge_artifact_sha256"]) == 64 for c in result["citations"])
    compact = compact_case(result["retrieval"]["cases"][0])
    assert all(k in compact for k in ("conditions", "assumptions", "exceptions", "contradictions", "evidence"))
    assert "lexical_score" not in compact and "raw_text" not in compact


def test_rejected_principle_really_downgrades(mentor_setup):
    result = Mentor(mentor_setup, backend=FakeMentor(rejected=True)).ask("evidence wait")
    assert result["route"] == "case_only"
    assert result["assessment"]["applicable_principles"] == []


def test_unresolved_conflict_preserved_with_both_sides_and_no_answer_call(mentor_setup):
    model = FakeMentor(unresolved=True)
    result = Mentor(mentor_setup, backend=model).ask("evidence wait")
    assert result["route"] == "insufficient_evidence"
    assert result["assessment"]["confidence"] == "low"
    assert "answer" not in model.calls
    assert len(result["citations"]) == 2
    assert "Time horizon" in result["answer"]["missing_information"]


def test_no_evidence_does_not_generate_speaker_answer(mentor_setup):
    model = FakeMentor(empty=True)
    result = Mentor(mentor_setup, backend=model).ask("zxqv")
    assert result["route"] == "insufficient_evidence"
    assert model.calls == ["analysis"]
    assert result["citations"] == []


def test_current_data_guard_and_missing_information(mentor_setup):
    result = Mentor(mentor_setup, backend=FakeMentor()).ask("Should I enter now at today's price?")
    assert result["analysis"]["current_data_required"]
    assert result["assessment"]["current_data_required"]
    assert "not a current buy/sell" in result["answer"]["current_data_limitation"]
    assert "Downside limit" in result["answer"]["missing_information"]


def test_unknown_citation_fails_closed(mentor_setup):
    result = Mentor(mentor_setup, backend=FakeMentor(corrupt=True)).ask("evidence wait")
    assert result["route"] == "insufficient_evidence"
    assert result["answer"]["historical_evidence"] == []


def test_parse_failure_retry_is_bounded(mentor_setup):
    class Broken(FakeMentor):
        def complete(self, *args):
            self.calls.append("broken")
            return {}, {"usage": {"input_tokens": 1}}
    model = Broken();result = Mentor(mentor_setup, backend=model).ask("evidence")
    assert len(model.calls) == 2
    assert len(result["calls"]) == 2
    assert result["route"] == "insufficient_evidence"


def test_no_llm_retrieval_and_loopback_api(mentor_setup):
    model = FakeMentor();mentor = Mentor(mentor_setup, backend=model)
    assert mentor.ask("evidence wait", no_llm=True)["route"] == "insufficient_evidence"
    assert not model.calls
    client = TestClient(create_app(mentor), base_url="http://127.0.0.1")
    assert client.get("/health").json()["case_count"] == 4
    response = client.post("/ask", json={"query": "evidence wait"})
    assert response.status_code == 200
    assert response.json()["route"] == "principle_assisted"
    assert response.json()["release_sha256"] == mentor.release_hash
    assert "retrieval" not in response.json()
    assert client.post("/ask", json={"query": ""}).status_code == 422
    assert client.get("/health", headers={"host": "external.example"}).status_code == 400


def test_candidate_roles_preserve_conflict_and_exception_not_synonyms(mentor_setup):
    mentor = Mentor(mentor_setup, backend=FakeMentor())
    cases, principles = copy.deepcopy(mentor.index.cases), copy.deepcopy(mentor.index.principles)
    principles["CROSS:P1"]["contradictions"] = [{"record_id": "SYN1:R2", "explanation": "Potential opposing preference"}]
    principles["CROSS:P1"]["counterexamples"] = [{"record_id": "SYN2:R2", "explanation": "Bounded exception"}]
    index = KnowledgeIndex(cases, principles, mentor_setup["retrieval"])
    result = index.select("evidence wait", {"search_terms": [], "target_domains": ["investment"], "principle_ids": ["CROSS:P1"]})
    hits = {c["record_id"]: c for c in result["cases"]}
    assert "potential_conflict" in hits["SYN1:R2"]["retrieval"]["roles"]
    assert "exception" in hits["SYN2:R2"]["retrieval"]["roles"]
    assert "potential_conflict" not in hits["SYN2:R2"]["retrieval"]["roles"]
    assert "supporting" in hits["SYN1:R1"]["retrieval"]["roles"]
    mismatch = index.select("evidence wait", {"search_terms": [], "target_domains": ["career"], "principle_ids": ["CROSS:P1"]})
    assert not mismatch["principles"]


def test_conflict_answer_cannot_drop_one_side(mentor_setup):
    result = Mentor(mentor_setup, backend=FakeMentor()).ask("evidence wait")
    assessment, answer = result["assessment"], result["answer"]
    ids = [c["record_id"] for c in result["retrieval"]["cases"]][:2]
    assessment["conflicting_evidence"] = [{"record_ids": ids}]
    assessment["supporting_cases"] = ids[:1]
    answer["historical_evidence"][0]["record_ids"] = ids[:1]
    with pytest.raises(ValueError, match="side citation"):
        validate_answer(answer, assessment, result["retrieval"]["cases"], mentor_setup)
    answer["historical_evidence"][0]["record_ids"] = ids
    assert len(validate_answer(answer, assessment, result["retrieval"]["cases"], mentor_setup)) == 2


def test_context_difference_alone_does_not_downgrade(mentor_setup):
    result = Mentor(mentor_setup, backend=FakeMentor()).ask("evidence wait")
    result["assessment"]["context_differences"] = [{"record_ids": ["SYN1:R1", "SYN2:R1"]}]
    assert finalize_route(result["analysis"], result["retrieval"], result["assessment"], 1) == "principle_assisted"
    result["assessment"]["unresolved_conflicts"] = [{"record_ids": ["SYN1:R1", "SYN2:R1"], "severity": "minor", "missing_information": []}]
    assert finalize_route(result["analysis"], result["retrieval"], result["assessment"], 1) == "case_only"


def test_unconditional_speaker_claim_rejected(mentor_setup):
    result = Mentor(mentor_setup, backend=FakeMentor()).ask("evidence wait")
    result["answer"]["interpretation"] = "speaker always buys"
    with pytest.raises(ValueError, match="Unconditional"):
        validate_answer(result["answer"], result["assessment"], result["retrieval"]["cases"], mentor_setup)


def test_answer_must_cite_the_assessed_support(mentor_setup):
    result = Mentor(mentor_setup, backend=FakeMentor()).ask("evidence wait")
    result["answer"]["historical_evidence"][0]["record_ids"] = result["assessment"]["supporting_cases"][:1]
    with pytest.raises(ValueError, match="supporting case citations"):
        validate_answer(result["answer"], result["assessment"], result["retrieval"]["cases"], mentor_setup)


def test_final_answer_budget_includes_injected_limits_and_missing_information(mentor_setup):
    class VerboseAnalysis(FakeMentor):
        def complete(self, prompt, context, schema):
            result, metadata = super().complete(prompt, context, schema)
            if "query_type" in result:
                result["missing_information"] = ["Additional scenario uncertainty " * 100]
            return result, metadata

    result = Mentor(mentor_setup, backend=VerboseAnalysis()).ask("evidence wait")
    assert result["route"] == "insufficient_evidence"
    assert result["guards"][0]["status"] == "fallback"
    assert not result["answer"]["historical_evidence"]


@pytest.mark.parametrize("kind", ["context_differences", "exceptions_triggered"])
def test_fallback_preserves_context_and_exception_citations(mentor_setup, kind):
    mentor = Mentor(mentor_setup, backend=FakeMentor())
    result = mentor.ask("evidence wait")
    ids = result["assessment"]["supporting_cases"][:2]
    result["assessment"][kind] = [{"record_ids": ids}]
    result = mentor.fallback(result, "Synthetic guard failure")
    assert {c["record_id"] for c in result["citations"]} == set(ids)
    assert result["answer"]["conflicts_and_exceptions"]


def test_release_revoked_during_model_call_suppresses_response(mentor_setup):
    from transcription.common import atomic_write_json, read_json

    class RevokingModel(FakeMentor):
        def complete(self, *args):
            result = super().complete(*args)
            release = read_json(Path(mentor_setup["release"]))
            review = Path(release["review_path"])
            data = read_json(review)
            data["decision"] = "reject"
            atomic_write_json(review, data)
            return result

    mentor = Mentor(mentor_setup, backend=RevokingModel())
    with pytest.raises(ValueError, match="Reviewed release"):
        mentor.ask("evidence wait")


def test_release_descriptor_change_during_validation_is_rejected(mentor_setup, monkeypatch):
    from mentor_agent import runtime

    mentor = Mentor(mentor_setup, backend=FakeMentor())
    original = runtime.load_release

    def changing_release(path):
        result = original(path)
        path.write_text(path.read_text() + "\n")
        return result

    monkeypatch.setattr(runtime, "load_release", changing_release)
    with pytest.raises(ValueError, match="Reviewed release"):
        mentor.validate_release()


def test_each_stage_retries_at_most_once(mentor_setup):
    class TransientFailure(FakeMentor):
        def complete(self, *args):
            result, metadata = super().complete(*args)
            if len(self.calls) % 2:
                return {}, metadata
            return result, metadata

    model = TransientFailure()
    result = Mentor(mentor_setup, backend=model).ask("evidence wait")
    assert result["route"] == "principle_assisted"
    assert model.calls == ["analysis", "analysis", "applicability", "applicability", "answer", "answer"]
    assert [c["attempt"] for c in result["calls"]] == [1, 2, 1, 2, 1, 2]


def test_online_principles_do_not_expose_l3_promotion_flag(mentor_setup):
    class CaptureModel(FakeMentor):
        def complete(self, prompt, context, schema):
            if "principles" in context:
                self.assessment_principles = context["principles"]
            return super().complete(prompt, context, schema)

    model = CaptureModel()
    result = Mentor(mentor_setup, backend=model).ask("evidence wait")
    assert result["route"] == "principle_assisted"
    for actual, diagnostic in zip(model.assessment_principles, result["retrieval"]["principles"]):
        assert diagnostic["eligible_for_level4"] is False
        assert actual == {k: v for k, v in diagnostic.items() if k != "eligible_for_level4"}
        assert actual["eligible_for_online_reasoning"] is True
        assert actual["runtime_mode_recommendation"] == "principle_assisted"


def test_empty_new_gaps_do_not_discard_prior_missing_information(mentor_setup):
    class AdditionalGapsOnly(FakeMentor):
        def complete(self, prompt, context, schema):
            result, metadata = super().complete(prompt, context, schema)
            if "selected_route" in result or "conditional_answer" in result:
                result["missing_information"] = []
            return result, metadata

    result = Mentor(mentor_setup, backend=AdditionalGapsOnly()).ask("evidence wait")
    assert result["analysis"]["missing_information"]
    assert result["assessment"]["missing_information"] == result["analysis"]["missing_information"]
    assert result["answer"]["missing_information"] == result["analysis"]["missing_information"]
