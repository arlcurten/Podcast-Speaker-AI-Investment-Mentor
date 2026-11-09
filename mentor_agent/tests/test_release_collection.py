"""Synthetic collection serving keeps existing runtime review checks and citations."""
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from knowledge import release
from knowledge.tests.test_release_collection_source_reads import cfg, prepared, scoped_leaves, no_provider, ref
from mentor_agent.runtime import Mentor
from mentor_agent.tests.test_scoped_release import CaseModel
from transcription.common import atomic_write_json, read_json, sha256_file


@pytest.fixture(autouse=True)
def no_live_mentor(monkeypatch):
    monkeypatch.setattr("mentor_agent.runtime.OpenRouter", Mock(side_effect=AssertionError("No live Mentor calls")))


@pytest.fixture
def collection_mentor(scoped_leaves, tmp_path):
    path = tmp_path / "collection.json"
    release.publish_collection([ref(p) for p in scoped_leaves], path)
    settings = yaml.safe_load((Path(__file__).parents[1] / "config/default.yaml").read_text())
    settings["release"] = str(path)
    return Mentor(settings, backend=CaseModel())


def test_collection_runtime_uses_both_leaves_and_cites_origin(collection_mentor, scoped_leaves):
    mentor = collection_mentor
    diagnostic = mentor.ask("evidence wait risk", no_llm=True)
    assert not mentor.backend.calls
    assert set(h["record_id"] for h in diagnostic["retrieval"]["diagnostic_hits"]) == {"SYN1:R1", "SYN2:R1"}
    result = mentor.ask("evidence wait risk")
    assert result["route"] == "case_only" and len(result["citations"]) == 2
    assert result["release_sha256"] == sha256_file(mentor.release_path)
    assert {c["leaf_release_sha256"] for c in result["citations"]} == {sha256_file(p) for p in scoped_leaves}
    assert all(c["knowledge_artifact_sha256"] and c["source_raw_sha256"] for c in result["citations"])
    assert all(r["source_acceptance"] == "not_granted" for r in result["evidence_review_limits"])


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("during_inference", [False, True])
def test_either_review_revoked_before_or_during_query_blocks_answer(collection_mentor, scoped_leaves, index, during_inference):
    mentor = collection_mentor
    path = Path(read_json(scoped_leaves[index])["review"]["path"])
    def revoke():
        atomic_write_json(path, {**read_json(path), "decision": "revoked"})
    if during_inference:
        complete = mentor.backend.complete
        def changed(*args):
            result = complete(*args)
            revoke()
            return result
        mentor.backend.complete = changed
    else:
        revoke()
    with pytest.raises(ValueError, match="Reviewed release unavailable"):
        mentor.ask("evidence wait risk")
    assert bool(mentor.backend.calls) == during_inference


@pytest.mark.parametrize("index", [0, 1])
def test_changed_leaf_invalidates_cached_index_without_model_call(collection_mentor, scoped_leaves, index):
    path = scoped_leaves[index]
    atomic_write_json(path, {**read_json(path), "created_at": "2026-01-02T00:00:00Z"})
    with pytest.raises(ValueError, match="Reviewed release unavailable"):
        collection_mentor.ask("evidence wait risk", no_llm=True)
    assert not collection_mentor.backend.calls
