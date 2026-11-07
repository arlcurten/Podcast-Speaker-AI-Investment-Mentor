from pathlib import Path

import pytest

from knowledge.pipeline import run
from knowledge.release import publish, load_release
from transcription.common import atomic_write_json, sha256_file, read_json, PROJECT_ROOT
from knowledge.tests.test_pipeline import cfg, FakeModel


def acceptance(cfg, tmp_path):
    run(cfg, backend=FakeModel())
    root = Path(cfg["output_root"]) / cfg["run_id"]
    paths = [root / "SYN1/level2.json", root / "SYN2/level2.json", root / "cross_episode/level4.json"]
    review = {"schema_version": "knowledge_review.v1", "decision": "accept", "reviewer": "synthetic test",
              "method": "Synthetic fixtures, not corpus quality approval", "reviewed_at": "2026-11-06T00:00:00Z",
              "unresolved_critical_issues": [], "artifact_sha256": {str(p.relative_to(root)): sha256_file(p) for p in paths},
              "accepted_case_ids": [f"{eid}:R{n}" for eid in ("SYN1", "SYN2") for n in (1, 2)],
              "accepted_principle_ids": ["CROSS:P1"]}
    path = tmp_path / "semantic_review.json"
    atomic_write_json(path, review)
    return path


def test_release_load_and_tamper_checks(cfg, tmp_path):
    review = acceptance(cfg, tmp_path)
    path = tmp_path / "release.json"
    assert publish(cfg, review, path)["case_count"] == 4
    cases, principles = load_release(path)
    assert set(principles) == {"CROSS:P1"}
    assert cases["SYN1:R1"]["evidence"]["timestamp_regions"]
    with pytest.raises(FileExistsError): publish(cfg, review, path)
    data = read_json(review);data["decision"] = "reject";atomic_write_json(review, data)
    with pytest.raises(ValueError, match="review changed"): load_release(path)


def test_review_cannot_hide_countercases_or_missing_support(cfg, tmp_path):
    review = acceptance(cfg, tmp_path)
    data = read_json(review);data["accepted_case_ids"].remove("SYN2:R1");atomic_write_json(review, data)
    with pytest.raises(ValueError, match="supporting AND"): publish(cfg, review, tmp_path / "release.json")


def test_public_sample_cannot_be_runtime_release():
    with pytest.raises(ValueError, match="public"): load_release(PROJECT_ROOT / "sample_outputs/synthetic/release.json")


def test_unreviewed_version_and_critical_issue_block_publish(cfg, tmp_path):
    review = acceptance(cfg, tmp_path)
    data = read_json(review);data["artifact_sha256"]["SYN1/level2.json"] = "wrong";atomic_write_json(review, data)
    with pytest.raises(ValueError, match="exact artifact"): publish(cfg, review, tmp_path / "release.json")
    data["unresolved_critical_issues"] = ["Attribution unknown"];atomic_write_json(review, data)
    with pytest.raises(ValueError, match="Unresolved"): publish(cfg, review, tmp_path / "release.json")
