"""Scoped synthetic cases remain conditional, review-bound and visible to users."""
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError

from knowledge.tests.test_pipeline import cfg
from knowledge.tests.test_case_release import prepared, publish
from mentor_agent.runtime import Mentor
from mentor_agent.service import create_app
from mentor_agent.tests.test_runtime import FakeMentor
from mentor_agent.contracts import ASSESSMENT


class CaseModel(FakeMentor):
    def __init__(self):
        super().__init__()
        self.contexts = []

    def complete(self, prompt, context, schema):
        self.contexts.append(context)
        result, usage = super().complete(prompt, context, schema)
        if "query_type" in result:
            result["principle_ids"] = []
        if "selected_route" in result:
            result.update(selected_route="case_only", applicable_principles=[])
        return result, usage


@pytest.fixture
def scoped_mentor(prepared):
    publish(prepared)
    settings = yaml.safe_load((Path(__file__).parents[1] / "config/default.yaml").read_text())
    settings["release"] = str(prepared["output"])
    return Mentor(settings, backend=CaseModel())


def test_scoped_release_runs_case_only_with_original_hold_and_visible_limits(scoped_mentor):
    mentor = scoped_mentor
    result = mentor.ask("evidence wait risk")
    assert result["route"] == "case_only"
    assert len(mentor.index.cases) == 1 and not mentor.index.principles
    assert result["citations"] and result["evidence_review_limits"]
    assert result["evidence_review_limits"][0]["source_acceptance"] == "not_granted"
    for context in mentor.backend.contexts[1:]:
        case = context["cases"][0]
        assert case["source_assessment"]["eligible_for_online_reasoning"] is False
        assert case["scoped_review"]["usage"] == "case_only"
        assert case["scoped_review"]["limitations"]
        assert case["supplementary_notes"]
        assert case["evidence"]["timestamp_regions"]
        assert "reviewer" not in case["scoped_review"]


def test_scope_limits_survive_no_llm_and_http(scoped_mentor):
    result = scoped_mentor.ask("evidence wait risk", no_llm=True)
    assert result["evidence_review_limits"]
    assert not scoped_mentor.backend.calls
    with TestClient(create_app(scoped_mentor), base_url="http://127.0.0.1") as client:
        response = client.post("/ask", json={"query": "evidence wait risk"})
    assert response.status_code == 200
    assert response.json()["evidence_review_limits"]
    assert "retrieval" not in response.json()


def test_scope_review_revocation_invalidates_loaded_runtime(scoped_mentor, prepared):
    review = json.loads(prepared["review"].read_text())
    review["decision"] = "revoked"
    prepared["review"].write_text(json.dumps(review))
    with pytest.raises(ValueError, match="Reviewed release unavailable"):
        scoped_mentor.ask("evidence wait risk")
    assert not scoped_mentor.backend.calls


@pytest.mark.parametrize("invented_id", [False, True])
def test_internal_context_difference_needs_only_its_real_case(scoped_mentor, invented_id):
    class InternalDifference(CaseModel):
        def complete(self, prompt, context, schema):
            response, usage = super().complete(prompt, context, schema)
            if "selected_route" in response:
                ids = [context["cases"][0]["record_id"]]
                if invented_id:
                    ids.append("invented-placeholder")
                response["context_differences"] = [{
                    "record_ids": ids, "topic": "Two conditions inside one synthetic case",
                    "shared_conditions": "Uncertainty", "differing_preferences": "Wait or limit exposure",
                    "explanation": "The same case describes alternatives under differing conditions.",
                    "effect_on_conclusion": "Do not turn an alternative into an unconditional instruction.",
                }]
                Draft202012Validator(ASSESSMENT).validate(response)
            return response, usage
    scoped_mentor.backend = InternalDifference()
    result = scoped_mentor.ask("evidence wait risk")
    assert scoped_mentor.backend.contexts[0]["available_case_count"] == 1
    if invented_id:
        assert result["route"] == "insufficient_evidence"
        assert "answer" not in scoped_mentor.backend.calls
    else:
        assert result["route"] == "case_only"
        assert len(result["citations"]) == 1
        assert result["answer"]["conflicts_and_exceptions"]
        broken = json.loads(json.dumps(result["assessment"]))
        broken["conflicting_evidence"] = broken.pop("context_differences")
        broken["context_differences"] = []
        with pytest.raises(ValidationError):
            Draft202012Validator(ASSESSMENT).validate(broken)
