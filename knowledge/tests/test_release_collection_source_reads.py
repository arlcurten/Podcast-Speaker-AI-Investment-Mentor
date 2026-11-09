"""Synthetic leaf replay and source-read races; no providers or private corpus."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge import case_release, pipeline, release
from knowledge.codex_backend import CodexBackend
from knowledge.tests.test_case_release import prepared, approval, publish
from knowledge.tests.test_pipeline import cfg, FakeModel, independent_pairs
from transcription.common import PROJECT_ROOT, atomic_write_json, read_json, sha256_file


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Synthetic release tests cannot call providers"))
    monkeypatch.setattr(CodexBackend, "complete", forbidden)
    yield
    forbidden.assert_not_called()


def ref(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def scoped_leaf(prepared, path, ids):
    review_path = path.with_suffix(".review.json")
    packet = case_release.review_packet(prepared["config"], ids)
    atomic_write_json(review_path, approval(packet))
    case_release.publish(prepared["config"], review_path, path)
    return path


@pytest.fixture
def scoped_leaves(prepared, tmp_path):
    publish(prepared)
    second = scoped_leaf(prepared, tmp_path / "second.json", ["SYN2:R1"])
    return [prepared["output"], second]


def full_leaf(config, path, ids, principles=()):
    artifacts = [{**r, "sha256": sha256_file(Path(r["path"]))} for r in release.release_locations(config)]
    review_path = path.with_suffix(".review.json")
    atomic_write_json(review_path, {"schema_version": "knowledge_review.v1", "decision": "accept",
        "reviewer": "Synthetic reviewer", "method": "Synthetic fixture only", "reviewed_at": "2026-01-01T00:00:00Z",
        "unresolved_critical_issues": [], "artifact_sha256": {r["logical_path"]: r["sha256"] for r in artifacts},
        "accepted_case_ids": ids, "accepted_principle_ids": list(principles)})
    release.publish(config, review_path, path)
    return path


@pytest.fixture
def full_leaves(cfg, tmp_path):
    pipeline.run(cfg, backend=FakeModel())
    return [full_leaf(cfg, tmp_path / f"full-{n}.json", [f"SYN{n}:R1"]) for n in (1, 2)]


def collection(paths):
    return {"schema_version": release.COLLECTION_VERSION, "created_at": "2026-01-01T00:00:00Z",
            "scope": "reviewed_leaf_union", "leaves": [ref(p) for p in paths]}


def test_scoped_union_readback_preserves_cases_and_no_original_writes(scoped_leaves, tmp_path):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    path = tmp_path / "collection.json"
    result = release.publish_collection([ref(p) for p in scoped_leaves], path)
    assert result["case_count"] == 2 and result["principle_count"] == 0
    cases, principles = release.load_release(path)
    assert set(cases) == {"SYN1:R1", "SYN2:R1"} and not principles
    for leaf in scoped_leaves:
        originals, _ = release.load_release(leaf)
        for rid, original in originals.items():
            assert cases[rid] == {**original, "leaf_release_sha256": sha256_file(leaf)}
            assert cases[rid]["scoped_review"]["source_acceptance"] == "not_granted"
    assert all(p.read_bytes() == payload for p, payload in before.items())
    with pytest.raises(FileExistsError): release.publish_collection([ref(p) for p in scoped_leaves], path)


def test_mixed_leaf_types_and_disjoint_same_episode_selection(prepared, scoped_leaves, tmp_path):
    reviewed = cfg.__wrapped__(tmp_path / "reviewed")
    pipeline.run(reviewed, backend=FakeModel())
    full = full_leaf(reviewed, tmp_path / "full.json", ["SYN2:R2"])
    cases, principles = release.load_release_payload(collection([scoped_leaves[0], full]))
    assert set(cases) == {"SYN1:R1", "SYN2:R2"} and not principles
    other = scoped_leaf(prepared, tmp_path / "same-episode.json", ["SYN1:R2"])
    cases, _ = release.load_release_payload(collection([scoped_leaves[0], other]))
    assert set(cases) == {"SYN1:R1", "SYN1:R2"}
    # Both leaves validate alone, but candidate/reviewed extraction versions cannot mix for one episode.
    with pytest.raises(ValueError, match="episode source or artifact versions"):
        release.load_release_payload(collection([scoped_leaves[1], full]))


@pytest.mark.parametrize("fault", ["empty", "extra", "scope", "time", "missing_hash", "bad_hash", "public",
                                  "duplicate_path", "duplicate_hash", "alias", "nested", "unsupported"])
def test_strict_collection_contract(scoped_leaves, tmp_path, fault):
    data = collection(scoped_leaves)
    if fault == "empty": data["leaves"] = []
    elif fault == "extra": data["accepted_case_ids"] = ["SYN1:R1"]
    elif fault == "scope": data["scope"] = "episode_acceptance"
    elif fault == "time": data["created_at"] = "2026-01-01"
    elif fault == "missing_hash": del data["leaves"][0]["sha256"]
    elif fault == "bad_hash": data["leaves"][0]["sha256"] = "0" * 64
    elif fault == "public": data["leaves"][0]["path"] = str(PROJECT_ROOT / "README.md")
    elif fault == "duplicate_path": data["leaves"][1]["path"] = data["leaves"][0]["path"]
    elif fault == "duplicate_hash": data["leaves"][1]["sha256"] = data["leaves"][0]["sha256"]
    elif fault == "alias":
        alias = tmp_path / "alias.json"
        alias.symlink_to(scoped_leaves[0])
        data["leaves"][1]["path"] = str(alias)
    else:
        leaf = tmp_path / "other.json"
        atomic_write_json(leaf, data if fault == "nested" else {"schema_version": "candidate.v1"})
        data["leaves"] = [ref(leaf)]
    with pytest.raises(ValueError): release.load_release_payload(data)


def test_duplicate_cases_even_identical_are_rejected(scoped_leaves, tmp_path):
    other = tmp_path / "same-cases.json"
    data = read_json(scoped_leaves[0])
    data["created_at"] = "2026-01-02T00:00:00Z"
    atomic_write_json(other, data)
    with pytest.raises(ValueError, match="Duplicate collection case or principle ID"):
        release.load_release_payload(collection([scoped_leaves[0], other]))


def test_duplicate_principles_with_disjoint_cases_are_rejected(cfg, tmp_path):
    pipeline.run(cfg, backend=FakeModel())
    first = full_leaf(cfg, tmp_path / "first.json", ["SYN1:R1", "SYN2:R1"], ["CROSS:P1"])
    class OtherSupports(FakeModel):
        def complete(self, *args):
            content, usage = super().complete(*args)
            if "consolidation_reason" in content:
                p = content["principles"][0]
                p["supporting_records"] = ["SYN1:R2", "SYN2:R2"]
                p["context_relationships"] = independent_pairs(p["supporting_records"])
            return content, usage
    config = {**cfg, "run_id": "other-synthetic"}
    pipeline.run(config, backend=OtherSupports())
    second = full_leaf(config, tmp_path / "second.json", ["SYN1:R2", "SYN2:R2"], ["CROSS:P1"])
    with pytest.raises(ValueError, match="Duplicate collection case or principle ID"):
        release.load_release_payload(collection([first, second]))


def test_disjoint_full_release_cases_require_identical_episode_versions(full_leaves, cfg, tmp_path):
    config = {**cfg, "run_id": "other-synthetic", "model": "synthetic-new-version"}
    pipeline.run(config, backend=FakeModel())
    other = full_leaf(config, tmp_path / "other.json", ["SYN1:R2"])
    with pytest.raises(ValueError, match="episode source or artifact versions"):
        release.load_release_payload(collection([full_leaves[0], other]))


def test_missing_leaf_counter_cannot_be_healed_by_union(cfg, tmp_path):
    class CounterModel(FakeModel):
        def complete(self, *args):
            content, usage = super().complete(*args)
            if "consolidation_reason" in content:
                content["principles"][0]["counterexamples"] = [{"record_id": "SYN1:R2", "explanation": "Synthetic exception"}]
            return content, usage
    pipeline.run(cfg, backend=CounterModel())
    first = full_leaf(cfg, tmp_path / "first.json", ["SYN1:R1", "SYN2:R1", "SYN1:R2"], ["CROSS:P1"])
    second = full_leaf(cfg, tmp_path / "second.json", ["SYN1:R2"])
    data = read_json(first)
    review = read_json(Path(data["review_path"]))
    review["accepted_case_ids"].remove("SYN1:R2")
    atomic_write_json(Path(data["review_path"]), review)
    data.update(accepted_case_ids=review["accepted_case_ids"], review_sha256=sha256_file(Path(data["review_path"])))
    atomic_write_json(first, data)
    for paths in ([first, second], [second, first]):
        with pytest.raises(ValueError, match="supporting AND counter"):
            release.load_release_payload(collection(paths))


def test_invalid_partial_leaf_does_not_return_valid_subset(scoped_leaves):
    leaf = read_json(scoped_leaves[1])
    leaf["accepted_case_ids"] = ["SYN2:R2"]
    atomic_write_json(scoped_leaves[1], leaf)
    with pytest.raises(ValueError, match="dependency/selection manifest"):
        release.load_release_payload(collection(scoped_leaves))


@pytest.mark.parametrize("index", [0, 1])
def test_revoked_or_invalid_leaf_rejects_entire_collection(scoped_leaves, index):
    data = collection(scoped_leaves)
    leaf = read_json(scoped_leaves[index])
    review_path = Path(leaf["review"]["path"])
    review = read_json(review_path)
    review["decision"] = "revoked"
    atomic_write_json(review_path, review)
    with pytest.raises(ValueError): release.load_release_payload(data)
    # Rebinding the review hash still cannot manufacture acceptance.
    leaf["review"] = ref(review_path)
    atomic_write_json(scoped_leaves[index], leaf)
    with pytest.raises(ValueError, match="revoked"):
        release.load_release_payload(collection(scoped_leaves))


@pytest.mark.parametrize("kind", ["descriptor", "review", "raw", "normalized", "level1", "level3"])
def test_full_leaf_dependencies_rechecked_after_last_leaf(full_leaves, cfg, monkeypatch, kind):
    data = collection(full_leaves)
    first = read_json(full_leaves[0])
    root = Path(cfg["output_root"]) / cfg["run_id"]
    targets = {"descriptor": full_leaves[0], "review": Path(first["review_path"]),
               "raw": Path(cfg["episodes"][0]["raw"]), "normalized": Path(cfg["episodes"][0]["normalized"]),
               "level1": root / "SYN1/level1.json", "level3": root / "SYN1/level3.json"}
    original = release._load_leaf_payload
    calls = []
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        calls.append(True)
        if len(calls) == 2:
            target = targets[kind]
            atomic_write_json(target, {**read_json(target), "synthetic_change": True})
        return value
    monkeypatch.setattr(release, "_load_leaf_payload", changed)
    with pytest.raises(ValueError, match="dependency changed"):
        release.load_release_payload(data)
    assert len(calls) == 2


@pytest.mark.parametrize("target", ["first_review", "second_review", "annotations", "supporting_review"])
def test_scoped_leaf_dependencies_rechecked_after_last_leaf(scoped_leaves, prepared, monkeypatch, target):
    data = collection(scoped_leaves)
    paths = {"first_review": prepared["review"],
             "second_review": Path(read_json(scoped_leaves[1])["review"]["path"]),
             "annotations": prepared["sidecar"], "supporting_review": prepared["supporting"]}
    original = release._load_leaf_payload
    calls = []
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        calls.append(True)
        if len(calls) == 2:
            path = paths[target]
            atomic_write_json(path, {**read_json(path), "synthetic_change": True})
        return value
    monkeypatch.setattr(release, "_load_leaf_payload", changed)
    with pytest.raises(ValueError, match="dependency changed"):
        release.load_release_payload(data)


def test_collection_descriptor_changed_during_leaf_replay_fails(scoped_leaves, tmp_path, monkeypatch):
    path = tmp_path / "collection.json"
    atomic_write_json(path, collection(scoped_leaves))
    original = release._load_leaf_payload
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        atomic_write_json(path, {**read_json(path), "created_at": "2026-01-02T00:00:00Z"})
        return value
    monkeypatch.setattr(release, "_load_leaf_payload", changed)
    with pytest.raises(ValueError, match="dependency changed"):
        release.load_release(path)


def test_publication_readback_must_be_exact_descriptor(scoped_leaves, tmp_path, monkeypatch):
    path = tmp_path / "collection.json"
    write = release.atomic_write_json
    def changed(output, data, **kwargs):
        write(output, data, **kwargs)
        atomic_write_json(output, {**data, "created_at": "2026-01-02T00:00:00Z"})
    monkeypatch.setattr(release, "atomic_write_json", changed)
    with pytest.raises(ValueError, match="read-back differs"):
        release.publish_collection([ref(p) for p in scoped_leaves], path)
