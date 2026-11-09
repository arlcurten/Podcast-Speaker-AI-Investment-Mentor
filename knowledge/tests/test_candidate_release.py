"""Synthetic candidate quarantine checks; never source-quality certification."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from knowledge import pipeline, release
from knowledge.candidates import load_candidate_source
from knowledge.codex_backend import CodexBackend
from knowledge.tests.test_pipeline import cfg, FakeModel
from knowledge.tests.test_release import acceptance
from transcription.common import atomic_write_json, read_json, sha256_file
from transcription.prepare import prepare


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Candidate release tests must never call a live model.")

    monkeypatch.setattr(CodexBackend, "complete", forbidden)


def forbid_release_dependencies(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Candidate rejection must precede filesystem and pipeline access.")

    for name in ("run", "private_path", "read_json", "sha256_file",
                 "release_locations", "atomic_write_json"):
        monkeypatch.setattr(release, name, forbidden)


def test_publish_candidate_rejects_before_filesystem_or_validation(monkeypatch, tmp_path):
    forbid_release_dependencies(monkeypatch)
    config = {"run_id": "synthetic-candidate", "extraction_mode": "candidate"}
    output = tmp_path / "release.json"

    with pytest.raises(ValueError, match="(?i)candidate"):
        release.publish(config, tmp_path / "missing-review.json", output)

    assert not output.exists()


def test_load_candidate_source_config_rejects_before_filesystem_or_validation(monkeypatch):
    forbid_release_dependencies(monkeypatch)
    payload = {
        "schema_version": "knowledge_release.v1",
        "evidence_authority": "level2",
        "run_id": "synthetic-candidate",
        "source_config": {"run_id": "synthetic-candidate", "extraction_mode": "candidate"},
        "extraction_mode": "reviewed",
        "review_path": "nonexistent-synthetic-review.json",
        "review_sha256": "0" * 64,
        "artifacts": [],
        "accepted_case_ids": ["SYN1:R1"],
        "accepted_principle_ids": [],
    }

    with pytest.raises(ValueError, match="(?i)candidate"):
        release.load_release_payload(payload)


@pytest.mark.parametrize("mode", [None, "reviewed"], ids=["legacy-default", "explicit-reviewed"])
def test_reviewed_release_round_trip_remains_available(cfg, tmp_path, mode):
    cfg.pop("extraction_mode", None)
    if mode is not None:
        cfg["extraction_mode"] = mode
    review_path = acceptance(cfg, tmp_path)
    output = tmp_path / "reviewed-release.json"

    result = release.publish(cfg, review_path, output)
    cases, principles = release.load_release(output)

    assert result["case_count"] == 4
    assert result["principle_count"] == 1
    assert set(cases) == {f"{eid}:R{n}" for eid in ("SYN1", "SYN2") for n in (1, 2)}
    assert set(principles) == {"CROSS:P1"}
    assert cases["SYN1:R1"]["evidence"]["source_raw_sha256"] == sha256_file(Path(cfg["episodes"][0]["raw"]))


@pytest.mark.parametrize("entrypoint", ["publish", "load_release_payload"])
def test_candidate_config_cannot_use_an_otherwise_valid_semantic_acceptance(cfg, tmp_path, entrypoint):
    review_path = acceptance(cfg, tmp_path)
    reviewed_output = tmp_path / "reviewed-release.json"
    release.publish(cfg, review_path, reviewed_output)
    payload = read_json(reviewed_output)
    candidate_cfg = copy.deepcopy(cfg)
    candidate_cfg["extraction_mode"] = "candidate"
    payload["source_config"] = candidate_cfg
    candidate_output = tmp_path / "candidate-release.json"
    protected = [review_path, reviewed_output] + [Path(row["path"]) for row in payload["artifacts"]]
    original_hashes = {path: sha256_file(path) for path in protected}

    with pytest.raises(ValueError, match="(?i)candidate"):
        if entrypoint == "publish":
            release.publish(candidate_cfg, review_path, candidate_output)
        else:
            release.load_release_payload(payload)

    assert not candidate_output.exists()
    assert all(sha256_file(path) == checksum for path, checksum in original_hashes.items())


class CandidateModel(FakeModel):
    def __init__(self):
        super().__init__()
        self.contexts = []

    def complete(self, prompt, context, schema):
        result, usage = super().complete(prompt, context, schema)
        self.contexts.append((self.calls[-1], copy.deepcopy(context)))
        if "threads" in result:
            result["source_observations"] = []
        elif "cases" in result:
            result["case_assessments"] = [
                {"record_id": case["record_id"], "status": "usable_with_limits",
                 "rationale": "Synthetic case; no source acceptance is granted.",
                 "source_issue_dispositions": [], "supplementary_notes": []}
                for case in result["cases"]
            ]
        return result, usage


def candidate_descriptor(cfg, tmp_path):
    """An adversarial acceptance of invented data, not a real review or release."""
    artifacts = [{**row, "sha256": sha256_file(Path(row["path"]))}
                 for row in pipeline.release_locations(cfg)]
    review = {
        "schema_version": "knowledge_review.v1", "decision": "accept",
        "reviewer": "synthetic adversarial fixture", "method": "Not corpus quality approval",
        "reviewed_at": "2026-11-08T00:00:00Z", "unresolved_critical_issues": [],
        "artifact_sha256": {row["logical_path"]: row["sha256"] for row in artifacts},
        "accepted_case_ids": [f"{eid}:R{n}" for eid in ("SYN1", "SYN2") for n in (1, 2)],
        "accepted_principle_ids": ["CROSS:P1"],
    }
    review_path = tmp_path / "crafted-candidate-review.json"
    atomic_write_json(review_path, review)
    return {
        "schema_version": "knowledge_release.v1", "evidence_authority": "level2",
        "run_id": cfg["run_id"], "source_config": copy.deepcopy(cfg), "artifacts": artifacts,
        "review_path": str(review_path), "review_sha256": sha256_file(review_path),
        "accepted_case_ids": review["accepted_case_ids"],
        "accepted_principle_ids": review["accepted_principle_ids"],
    }


@pytest.fixture
def candidate_run(cfg):
    # Accepted synthetic sources isolate quarantine failures from source-quality failures.
    cfg["extraction_mode"] = "candidate"
    model = CandidateModel()
    report = pipeline.run(cfg, backend=model)
    assert report["model_calls_this_execution"] == 7
    assert pipeline.run(cfg, validate_only=True)["model_calls_this_execution"] == 0
    for row in report["artifacts"]:
        assert read_json(Path(row["artifact_path"]))["schema_version"] == "knowledge_candidate.v1"
    return cfg, report


@pytest.mark.parametrize("mode", ["candidate", "reviewed", None], ids=["candidate", "relabeled", "omitted"])
@pytest.mark.parametrize("entrypoint", ["publish", "load_release_payload"])
def test_candidate_artifacts_cannot_publish_with_crafted_acceptance(candidate_run, tmp_path, mode, entrypoint):
    cfg, report = candidate_run
    payload = candidate_descriptor(cfg, tmp_path)
    config = payload["source_config"]
    if mode is None:
        config.pop("extraction_mode")
    else:
        config["extraction_mode"] = mode
    paths = [Path(row["artifact_path"]) for row in report["artifacts"]]
    original_hashes = {path: sha256_file(path) for path in paths}
    output = tmp_path / "forbidden-release.json"

    with pytest.raises(ValueError, match="(?i)(candidate|incompatible knowledge schema)"):
        if entrypoint == "publish":
            release.publish(config, Path(payload["review_path"]), output)
        else:
            release.load_release_payload(payload)

    assert not output.exists()
    assert all(sha256_file(path) == checksum for path, checksum in original_hashes.items())


@pytest.mark.parametrize("mode", ["reviewed", None], ids=["explicit-reviewed", "legacy-default"])
def test_changing_candidate_schema_label_cannot_bypass_pinned_provenance(candidate_run, tmp_path, mode):
    cfg, report = candidate_run
    for row in report["artifacts"]:
        path = Path(row["artifact_path"])
        artifact = read_json(path)
        artifact["schema_version"] = "knowledge.v2"
        atomic_write_json(path, artifact)
    # Re-pin the forged review so rejection must come from provenance reconstruction.
    payload = candidate_descriptor(cfg, tmp_path)
    if mode is None:
        payload["source_config"].pop("extraction_mode")
    else:
        payload["source_config"]["extraction_mode"] = mode

    with pytest.raises(ValueError, match="(?i)incompatible"):
        release.load_release_payload(payload)


@pytest.mark.parametrize("decision", ["uncertain", "retranscribe"])
def test_v1_held_review_limitations_reach_candidate_context_unchanged(cfg, tmp_path, decision):
    cfg["extraction_mode"] = "candidate"
    entry = cfg["episodes"][0]
    raw_path = Path(entry["raw"])
    review_path, draft_path = tmp_path / "held-review.json", tmp_path / "unreviewed-draft.json"
    review = read_json(Path(entry["review"]))
    limitations = ["Synthetic speaker attribution is unresolved.", "Synthetic missing context requires separate review."]
    review.update(decision=decision, unresolved_critical_issues=limitations)
    atomic_write_json(review_path, review)
    prepare(raw_path, draft_path)
    entry.update(review=str(review_path), normalized=str(draft_path))
    hashes = {path: sha256_file(path) for path in (raw_path, review_path, draft_path)}

    source = load_candidate_source(raw_path, draft_path, review_path)
    assert source["source_acceptance"] == "not_granted"
    assert source["prior_source_decision"] == decision
    assert source["prior_review_limitations"] == limitations
    assert source["review_sha256"] == hashes[review_path]
    assert "reviewed_annotations" not in source
    model = CandidateModel()
    report = pipeline.run(cfg, through="level2", backend=model)
    contexts = {stage: context for stage, context in model.contexts
                if context["episode"]["episode_id"] == entry["episode_id"]}
    assert set(contexts) == {"level1", "level2"}
    for context in contexts.values():
        assert context["episode"]["prior_review_limitations"] == limitations
        assert context["episode"]["prior_source_decision"] == decision
        assert context["episode"]["source_acceptance"] == "not_granted"
    for row in report["artifacts"]:
        artifact = read_json(Path(row["artifact_path"]))
        if artifact["source"]["episode_id"] == entry["episode_id"]:
            assert artifact["source"]["prior_review_limitations"] == limitations
    assert all(sha256_file(path) == checksum for path, checksum in hashes.items())


@pytest.mark.parametrize("field,value", [
    ("schema_version", "transcript_review.unknown"),
    ("schema_version", None),
    ("unresolved_critical_issues", "not a list"),
    ("unresolved_critical_issues", [None]),
])
def test_malformed_optional_review_blocks_candidate_before_model(cfg, tmp_path, field, value):
    cfg["extraction_mode"] = "candidate"
    entry = cfg["episodes"][0]
    review = read_json(Path(entry["review"]))
    review.update(decision="uncertain")
    review[field] = value
    review_path = tmp_path / "malformed-held-review.json"
    atomic_write_json(review_path, review)
    entry["review"] = str(review_path)
    model = CandidateModel()

    with pytest.raises(ValueError):
        pipeline.run(cfg, through="level1", backend=model)

    assert model.calls == []
    assert not Path(cfg["output_root"]).exists()
