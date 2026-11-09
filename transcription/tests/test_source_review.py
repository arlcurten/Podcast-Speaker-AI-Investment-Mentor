"""Synthetic model judgments only; tests do not accept any real transcript."""
import copy
from pathlib import Path

import pytest

from knowledge.tests.test_pipeline import cfg
from transcription.common import atomic_write_json, read_json, sha256_file
from transcription.prepare import prepare
from transcription.quality import quality_gate
from transcription.source_review import review_source


class Reviewer:
    def __init__(self, issues=None, mutate=None):
        self.calls = 0
        self.issues = issues or []
        self.mutate = mutate

    def complete(self, prompt, context, schema):
        self.calls += 1
        assert "No audio is available" in prompt
        result = {"schema_version": "source_review_result.v1", "episode_id": context["episode_id"],
                  "transcript_sha256": context["transcript_sha256"], "draft_sha256": context["draft_sha256"],
                  "decision": "accept", "rationale": "Synthetic source has explicit conditions and attribution.",
                  "coverage": context["required_coverage"], "issues": self.issues, "annotations_reviewed": True,
                  "method": {"basis": "text_only_semantic_readiness", "audio_available": False,
                             "audio_listened": False, "description": "Synthetic full text review fixture.",
                             "limitations": ["Not audio accuracy certification."]}}
        if self.mutate:
            self.mutate(result)
        return result, {"provider": "mock", "model": "synthetic", "usage": None, "cost_usd": None}


def inputs(cfg):
    e = cfg["episodes"][0]
    return Path(e["raw"]), Path(e["normalized"]), Path(cfg["output_root"]) / "review"


def issue(raw, severity="noncritical"):
    row = read_json(raw)["segments"][0]
    return {"issue_id": "S1", "severity": severity, "disposition": "unresolved", "explanation": "Synthetic uncertainty.",
            "anchors": [{"segment_id": row["segment_id"], "start": row["start"], "end": row["end"], "raw_text": row["text"]}]}


def test_accept_and_reuse_without_extra_call(cfg):
    raw, draft, output = inputs(cfg)
    model = Reviewer()
    result = review_source(raw, draft, output, model)
    assert result["accepted"]
    assert quality_gate(raw, review_path=Path(result["review_path"]))["ready_for_extraction"]
    assert review_source(raw, draft, output, model) == result
    assert model.calls == 1


@pytest.mark.parametrize("severity,accepted", [("critical", False), ("noncritical", True)])
def test_cautions_retained_and_critical_forces_hold(cfg, severity, accepted):
    raw, draft, output = inputs(cfg)
    result = review_source(raw, draft, output, Reviewer([issue(raw, severity)]))
    assert result["accepted"] is accepted
    notes = read_json(output / "annotations.json")["annotations"]
    assert notes[0]["kind"] == "uncertainty"
    assert notes[0]["anchors"][0]["raw_text"] == read_json(raw)["segments"][0]["text"]
    norm = output / "normalized.json"
    prepare(raw, norm, Path(result["review_path"]))
    assert read_json(norm)["metadata"]["quality"]["ready_for_extraction"] is accepted


def test_prior_critical_cannot_be_omitted_by_model(cfg):
    raw, draft, output = inputs(cfg)
    result = review_source(raw, draft, output, Reviewer(), prior_findings=[issue(raw, "critical")])
    assert not result["accepted"]
    assert read_json(output / "annotations.json")["annotations"][0]["annotation_id"] == "prior:S1"


@pytest.mark.parametrize("mutation", [
    lambda r: r["coverage"].update(raw_segment_count=99),
    lambda r: r.update(transcript_sha256="0" * 64),
    lambda r: r["method"].update(audio_listened=True),
])
def test_invalid_claims_fail_closed(cfg, mutation):
    raw, draft, output = inputs(cfg)
    result = review_source(raw, draft, output, Reviewer(mutate=mutation))
    assert not result["accepted"] and result["review_path"] is None
    assert read_json(output / "report.json")["status"] == "failed"
    with pytest.raises(ValueError, match="Incomplete source-review"):
        review_source(raw, draft, output, Reviewer())


def test_changed_draft_and_changed_notes_rejected(cfg):
    raw, draft, output = inputs(cfg)
    model = Reviewer([issue(raw)])
    result = review_source(raw, draft, output, model)
    notes = read_json(output / "annotations.json")
    notes["annotations"][0]["note"] = "Tampered"
    atomic_write_json(output / "annotations.json", notes)
    with pytest.raises(ValueError):
        review_source(raw, draft, output, model)
    assert model.calls == 1
    altered = read_json(draft)
    altered["segments"][0]["normalized_text_zh_tw"] = "Invented synthetic change"
    atomic_write_json(draft, altered)
    with pytest.raises(ValueError, match="Draft text"):
        review_source(raw, draft, output.parent / "other", model)
    assert model.calls == 1


def test_incomplete_request_not_repeated_and_errors_not_echoed(cfg):
    raw, draft, output = inputs(cfg)
    class Broken:
        def complete(self, *args):
            raise RuntimeError("sensitive-placeholder-must-not-be-logged")
    result = review_source(raw, draft, output, Broken())
    assert not result["accepted"]
    assert "sensitive-placeholder" not in Path(result["report_path"]).read_text()
    with pytest.raises(ValueError, match="Incomplete source-review"):
        review_source(raw, draft, output, Reviewer())


def test_invalid_anchor_rejected(cfg):
    raw, draft, output = inputs(cfg)
    note = issue(raw)
    note["anchors"][0]["start"] += 1
    result = review_source(raw, draft, output, Reviewer([note]))
    assert not result["accepted"]
    assert not (output / "complete.json").exists()
