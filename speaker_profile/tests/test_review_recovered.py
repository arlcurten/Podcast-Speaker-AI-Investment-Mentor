"""Editorial review must follow recovered provenance without copying evidence."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import pipeline, review
from transcription.common import atomic_write_json, read_json
from .test_pipeline import cfg, root, synthesis_proposal
from .test_recovered_synthesis import mixed
from .test_recovery import CompactModel
from .test_review import pin


class RecoveredHypothesisModel(CompactModel):
    def complete(self, *args):
        content, usage = super().complete(*args)
        if self.calls[-1] == "synthesis":
            content = synthesis_proposal(self.contexts[-1]["evidence"], ["SYN2:E1"])
        return content, usage


@pytest.fixture(params=["mixed", "recovered_only"])
def recovered_parent(mixed, tmp_path, monkeypatch, request):
    _, config = mixed
    if request.param == "recovered_only":
        config.pop("reuse_evidence")
        config["episodes"] = config["episodes"][1:]
    pipeline.run(config, backend=RecoveredHypothesisModel())
    config_path = tmp_path / "editorial-config.json"
    atomic_write_json(config_path, config)
    artifact = root(config) / "synthesis.json"
    parent = read_json(artifact)
    entry = next(e for e in config["episodes"] if e["episode_id"] == "SYN2")
    raw = read_json(Path(entry["raw"]))["segments"][0]
    proposal = {
        "schema_version": "profile_synthesis_revision.v1", "parent_config": pin(config_path),
        "parent_artifact": pin(artifact), "reviewer": "Synthetic source reviewer",
        "method": "Read exact synthetic source", "rationale": "Narrow the interpretation",
        "edits": [{"hypothesis_id": "PROFILE:H1", "field": "/conditional_description",
                   "expected": parent["content"]["hypotheses"][0]["conditional_description"],
                   "value": "Only the stated fictional preference, not a recurring trait.",
                   "reason": "The single source observation does not establish recurrence.",
                   "evidence_ids": ["SYN2:E1"],
                   "source_anchors": [{"episode_id": "SYN2", **{k: raw[k] for k in
                       ("segment_id", "start", "end", "text")}}]}]}
    proposal_path = tmp_path / "editorial-proposal.json"
    atomic_write_json(proposal_path, proposal)
    forbidden = Mock(side_effect=AssertionError("Editorial review cannot infer"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    return config, parent, proposal_path, tmp_path / "editorial-result.json", forbidden


def test_review_recovered_parent_preserves_provenance_and_source_hold(recovered_parent, tmp_path):
    config, parent, proposal, output, forbidden = recovered_parent
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    result = review.apply(proposal, output)
    assert review.validate(proposal, output) == result
    expected_refs = {**config.get("reuse_evidence", {}).get("artifacts", {}),
                     **config["reuse_recovered_evidence"]}
    assert result["evidence"] == expected_refs
    assert result["policy"] == parent["policy"] == pipeline.POLICY
    assert result["content"]["evidence_accounting"] == parent["content"]["evidence_accounting"]
    recovered = read_json(Path(expected_refs["SYN2"]["path"]))
    assert recovered["schema_version"] == "profile_recovered_evidence.v1"
    assert recovered["grounding"]["items"]["SYN2:E1"]["supplementary_context"]
    assert result["additional_model_calls"] == 0 and result["model_usage"] is None
    assert {p: p.read_bytes() for p in before} == before
    assert not list(root(config).glob("*/evidence.json"))
    forbidden.assert_not_called()


@pytest.mark.parametrize("fault", ["content", "source", "hash", "missing"])
def test_review_recovered_parent_rejects_tampered_evidence(recovered_parent, fault):
    config, _, proposal, output, forbidden = recovered_parent
    path = Path(config["reuse_recovered_evidence"]["SYN2"]["path"])
    value = read_json(path)
    if fault == "missing":
        path.unlink()
    else:
        if fault == "content": value["content"]["items"][0]["source_observation"] = "Unsupported claim"
        elif fault == "source": value["source"]["episode_id"] = "OTHER"
        else: value["recovery_config"]["result"]["sha256"] = "a" * 64
        atomic_write_json(path, value)
    with pytest.raises((ValueError, OSError)):
        review.apply(proposal, output)
    assert not output.exists()
    forbidden.assert_not_called()
