"""Synthetic review parity and deterministic file-replacement race tests."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from knowledge import release
from knowledge.codex_backend import CodexBackend
from knowledge.tests.test_pipeline import cfg
from knowledge.tests.test_release import acceptance
from transcription.common import atomic_write_json, read_json, sha256_file


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Release integrity tests must never call a live model.")

    monkeypatch.setattr(CodexBackend, "complete", forbidden)


@pytest.fixture
def reviewed_payload(cfg, tmp_path):
    review_path = acceptance(cfg, tmp_path)
    review = read_json(review_path)
    artifacts = [{**row, "sha256": sha256_file(Path(row["path"]))}
                 for row in release.release_locations(cfg)]
    return {
        "schema_version": "knowledge_release.v1", "evidence_authority": "level2",
        "run_id": cfg["run_id"], "source_config": cfg, "artifacts": artifacts,
        "review_path": str(review_path), "review_sha256": sha256_file(review_path),
        "accepted_case_ids": review["accepted_case_ids"],
        "accepted_principle_ids": review["accepted_principle_ids"],
    }


@pytest.mark.parametrize("entrypoint", ["publish", "load_release_payload"])
@pytest.mark.parametrize("field,value", [
    ("schema_version", "knowledge_review.unknown"),
    ("schema_version", None),
    ("decision", "reject"),
    ("unresolved_critical_issues", ["Synthetic unresolved issue"]),
    ("unresolved_critical_issues", None),
    *[(field, value) for field in ("reviewer", "method", "reviewed_at")
      for value in (None, " \t", 42)],
])
def test_publication_and_loading_require_same_review_metadata(reviewed_payload, tmp_path, entrypoint, field, value):
    payload = reviewed_payload
    path = Path(payload["review_path"])
    review = read_json(path)
    if value is None:
        review.pop(field)
    else:
        review[field] = value
    atomic_write_json(path, review)
    payload["review_sha256"] = sha256_file(path)
    output = tmp_path / "invalid-release.json"

    with pytest.raises(ValueError, match="acceptance|Review requires|Unresolved"):
        if entrypoint == "publish":
            release.publish(payload["source_config"], path, output)
        else:
            release.load_release_payload(payload)

    assert not output.exists()


def test_valid_release_uses_one_snapshot_per_json_dependency(reviewed_payload, monkeypatch):
    payload = reviewed_payload
    reads = Counter()
    read_bytes, validate_run = Path.read_bytes, release.run
    validating = False

    def run(*args, **kwargs):
        nonlocal validating
        validating = True
        try:
            return validate_run(*args, **kwargs)
        finally:
            validating = False

    def snapshot(path):
        if not validating:
            reads[path] += 1
        return read_bytes(path)

    def separate_json_read(*args, **kwargs):
        pytest.fail("Release JSON must be parsed from its checksummed byte snapshot.")

    monkeypatch.setattr(release, "run", run)
    monkeypatch.setattr(Path, "read_bytes", snapshot)
    monkeypatch.setattr(release, "read_json", separate_json_read)
    cases, principles = release.load_release_payload(payload)

    assert len(cases) == 4
    assert set(principles) == {"CROSS:P1"}
    expected = [Path(payload["review_path"])] + [Path(row["path"]) for row in payload["artifacts"]]
    assert reads == Counter({path: 1 for path in expected})


def after_pipeline_validation(monkeypatch):
    validated = []
    original = release.run

    def run(*args, **kwargs):
        result = original(*args, **kwargs)
        validated.append(True)
        return result

    monkeypatch.setattr(release, "run", run)
    return validated


@pytest.mark.parametrize("target", ["review", "level2", "level4"])
def test_hash_must_match_bytes_parsed_not_a_separate_file_read(reviewed_payload, monkeypatch, target):
    payload = reviewed_payload
    path = (Path(payload["review_path"]) if target == "review" else
            Path(next(row["path"] for row in payload["artifacts"] if row["stage"] == target)))
    original_hash = sha256_file(path)
    read_bytes = Path.read_bytes
    validated = after_pipeline_validation(monkeypatch)
    intercepted = []

    def snapshot(current):
        data = read_bytes(current)
        if current == path and (target == "review" or validated):
            intercepted.append(True)
            return data + b" "
        return data

    monkeypatch.setattr(Path, "read_bytes", snapshot)
    with pytest.raises(ValueError, match="changed"):
        release.load_release_payload(payload)

    assert intercepted == [True]
    assert sha256_file(path) == original_hash


@pytest.mark.parametrize("target", ["review", "level2", "level4"])
@pytest.mark.parametrize("mutation", ["replace", "delete"])
def test_dependencies_rechecked_after_successful_snapshot_read(reviewed_payload, monkeypatch, target, mutation):
    payload = reviewed_payload
    path = (Path(payload["review_path"]) if target == "review" else
            Path(next(row["path"] for row in payload["artifacts"] if row["stage"] == target)))
    read_bytes = Path.read_bytes
    validated = after_pipeline_validation(monkeypatch)
    intercepted = []

    def snapshot(current):
        data = read_bytes(current)
        if current == path and not intercepted and (target == "review" or validated):
            intercepted.append(True)
            if mutation == "delete":
                path.unlink()
            else:
                replacement = path.with_suffix(".replacement")
                replacement.write_bytes(data + b" ")
                replacement.replace(path)
        return data

    monkeypatch.setattr(Path, "read_bytes", snapshot)
    with pytest.raises(ValueError, match="dependency changed"):
        release.load_release_payload(payload)

    assert intercepted == [True]


@pytest.mark.parametrize("mutation", ["replace", "delete"])
def test_release_descriptor_rechecked_before_return(reviewed_payload, tmp_path, monkeypatch, mutation):
    path = tmp_path / "release.json"
    atomic_write_json(path, reviewed_payload)
    read_bytes = Path.read_bytes

    def snapshot(current):
        data = read_bytes(current)
        if current == path:
            if mutation == "delete":
                path.unlink()
            else:
                path.write_bytes(data + b" ")
        return data

    monkeypatch.setattr(Path, "read_bytes", snapshot)
    with pytest.raises(ValueError, match="dependency changed"):
        release.load_release(path)


def test_publish_pins_review_to_original_snapshot(reviewed_payload, tmp_path, monkeypatch):
    payload = reviewed_payload
    path = Path(payload["review_path"])
    output = tmp_path / "release.json"
    read_bytes = Path.read_bytes
    intercepted = []

    def snapshot(current):
        data = read_bytes(current)
        if current == path and not intercepted:
            intercepted.append(True)
            path.write_bytes(data + b" ")
        return data

    monkeypatch.setattr(Path, "read_bytes", snapshot)
    with pytest.raises(ValueError, match="review changed"):
        release.publish(payload["source_config"], path, output)

    assert intercepted == [True]
    assert not output.exists()


@pytest.mark.parametrize("target", ["review", "artifact"])
@pytest.mark.parametrize("checksum", [None, "", "0" * 64])
def test_missing_or_incorrect_checksums_never_disable_verification(reviewed_payload, target, checksum):
    payload = reviewed_payload
    if target == "review":
        payload["review_sha256"] = checksum
    else:
        payload["artifacts"][0]["sha256"] = checksum

    with pytest.raises(ValueError, match="changed"):
        release.load_release_payload(payload)
