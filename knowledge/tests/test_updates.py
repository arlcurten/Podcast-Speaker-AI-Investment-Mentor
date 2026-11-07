import copy
from pathlib import Path

import pytest

from knowledge import pipeline
from knowledge.release import publish, load_release
from knowledge.tests.test_pipeline import cfg, FakeModel
from knowledge.tests.test_release import acceptance
from transcription.common import atomic_write_json, read_json, sha256_file, PROJECT_ROOT
from transcription.prepare import prepare


def new_source(cfg, directory, eid="SYN3"):
    directory.mkdir(parents=True)
    raw = read_json(Path(cfg["episodes"][0]["raw"]))
    raw["metadata"]["episode_id"] = eid
    raw["metadata"]["source_episode_metadata"].update(episode_id=eid, publication_date="2023-01-01")
    raw_path, norm_path, review_path = [directory / f"{key}.json" for key in ("raw", "normalized", "review")]
    atomic_write_json(raw_path, raw, overwrite=False)
    review = read_json(Path(cfg["episodes"][0]["review"]))
    review.update(episode_id=eid, transcript_sha256=sha256_file(raw_path))
    atomic_write_json(review_path, review, overwrite=False)
    prepare(raw_path, norm_path, review_path)
    return {"episode_id": eid, "raw": str(raw_path), "normalized": str(norm_path), "review": str(review_path)}


def update_config(cfg, tmp_path):
    pipeline.run(cfg, backend=FakeModel())
    source_file = tmp_path / "additions.json"
    atomic_write_json(source_file, [new_source(cfg, tmp_path / "new-source")])
    target = tmp_path / "next.json"
    result = pipeline.extend(cfg, source_file, "update_v2", target)
    assert result["model_calls"] == 0
    assert result["referenced_artifacts"] == 6
    assert result["estimated_new_calls"] == 4
    return pipeline.load_config(target)


def test_extend_references_without_copying_and_only_extracts_new_episode(cfg, tmp_path):
    updated = update_config(cfg, tmp_path)
    old_paths = [Path(r["path"]) for e in updated["episodes"][:2] for r in e["reuse_artifacts"].values()]
    old_hashes = {p: sha256_file(p) for p in old_paths}
    root = Path(updated["output_root"]) / updated["run_id"]
    assert not root.exists()
    planned = pipeline.run(updated, dry_run=True)
    assert planned["estimated_new_calls"] == 4 and not root.exists()
    backend = FakeModel()
    result = pipeline.run(updated, backend=backend)
    assert backend.calls == ["level1", "level2", "level3", "level4"]
    assert sum(a["action"] == "referenced" for a in result["artifacts"]) == 6
    assert not (root / "SYN1").exists() and not (root / "SYN2").exists()
    assert all(sha256_file(p) == h for p, h in old_hashes.items())
    assert pipeline.run(updated, backend=backend)["model_calls_this_execution"] == 0
    assert pipeline.run(updated, dry_run=True)["estimated_new_calls"] == 0


def test_missing_or_changed_reference_fails_before_any_model_call(cfg, tmp_path):
    updated = update_config(cfg, tmp_path)
    # Put the new episode first: preflight must still reject later broken reuse.
    updated["episodes"] = updated["episodes"][-1:] + updated["episodes"][:-1]
    ref = Path(updated["episodes"][1]["reuse_artifacts"]["level2"]["path"])
    ref.write_text(ref.read_text() + " ")
    backend = FakeModel()
    with pytest.raises(ValueError, match="checksum"):
        pipeline.run(updated, backend=backend)
    assert backend.calls == []
    ref.unlink()
    with pytest.raises(ValueError, match="Missing pinned"):
        pipeline.run(updated, dry_run=True)


def test_reuse_requires_same_model_and_validated_content(cfg, tmp_path):
    updated = update_config(cfg, tmp_path)
    updated["model"] = "different-model"
    backend = FakeModel()
    with pytest.raises(ValueError, match="incompatible"):
        pipeline.run(updated, backend=backend)
    assert not backend.calls


def test_interrupted_update_resumes_new_stage_without_repeating_old_work(cfg, tmp_path):
    updated = update_config(cfg, tmp_path)
    updated["max_calls"] = 1
    first = FakeModel()
    with pytest.raises(ValueError, match="call limit"):
        pipeline.run(updated, backend=first)
    assert first.calls == ["level1"]
    updated["max_calls"] = 3
    resumed = FakeModel()
    assert pipeline.run(updated, backend=resumed)["model_calls_this_execution"] == 3
    assert resumed.calls == ["level2", "level3", "level4"]


def test_update_release_pins_old_cases_and_preserves_old_release(cfg, tmp_path):
    old_review = acceptance(cfg, tmp_path)
    old_release = tmp_path / "old_release.json"
    publish(cfg, old_review, old_release)
    old_hash = sha256_file(old_release)
    updated = update_config(cfg, tmp_path)
    pipeline.run(updated, backend=FakeModel())
    review = read_json(old_review)
    review["accepted_case_ids"] += ["SYN3:R1", "SYN3:R2"]
    review["artifact_sha256"] = {r["logical_path"]: sha256_file(Path(r["path"])) for r in pipeline.release_locations(updated)}
    new_review = tmp_path / "new_review.json"
    atomic_write_json(new_review, review)
    new_release = tmp_path / "new_release.json"
    publish(updated, new_review, new_release)
    assert len(load_release(new_release)[0]) == 6
    assert len(load_release(old_release)[0]) == 4
    assert (load_release(old_release)[0]["SYN1:R1"]["knowledge_artifact_sha256"]
            == load_release(new_release)[0]["SYN1:R1"]["knowledge_artifact_sha256"])
    assert sha256_file(old_release) == old_hash
    # An arbitrary reviewed-looking artifact may not replace a configured one.
    payload = read_json(new_release)
    original = Path(payload["artifacts"][0]["path"])
    impostor = tmp_path / "impostor.json"
    impostor.write_bytes(original.read_bytes())
    payload["artifacts"][0]["path"] = str(impostor)
    atomic_write_json(new_release, payload)
    with pytest.raises(ValueError, match="exactly match"):
        load_release(new_release)


def test_extension_chain_points_to_original_artifacts(cfg, tmp_path):
    updated = update_config(cfg, tmp_path)
    pipeline.run(updated, backend=FakeModel())
    additions = tmp_path / "fourth.json"
    atomic_write_json(additions, [new_source(cfg, tmp_path / "fourth-source", "SYN4")])
    target = tmp_path / "third-config.json"
    pipeline.extend(updated, additions, "update_v3", target)
    next_cfg = pipeline.load_config(target)
    assert next_cfg["episodes"][0]["reuse_artifacts"] == updated["episodes"][0]["reuse_artifacts"]
    assert pipeline.run(next_cfg, dry_run=True)["estimated_new_calls"] == 4


def test_extension_rejects_duplicate_episode_and_unaccepted_source(cfg, tmp_path):
    pipeline.run(cfg, backend=FakeModel())
    path = tmp_path / "additions.json"
    atomic_write_json(path, [cfg["episodes"][0]])
    with pytest.raises(ValueError, match="Duplicate"):
        pipeline.extend(cfg, path, "next", tmp_path / "out.json")
    source = new_source(cfg, tmp_path / "bad")
    review = read_json(Path(source["review"])); review["decision"] = "uncertain"
    atomic_write_json(Path(source["review"]), review)
    atomic_write_json(path, [source])
    with pytest.raises(ValueError, match="not accepted"):
        pipeline.extend(cfg, path, "next", tmp_path / "out.json")
    assert not (tmp_path / "out.json").exists()


def test_config_reuse_must_be_private_pinned_prefix(cfg, tmp_path):
    bad = copy.deepcopy(cfg)
    path = tmp_path / "bad.json"
    bad["episodes"][0]["reuse_artifacts"] = {"level2": {"path": str(tmp_path / "a.json"), "sha256": "a" * 64}}
    atomic_write_json(path, bad)
    with pytest.raises(ValueError, match="prefix"):
        pipeline.load_config(path)
    bad["episodes"][0]["reuse_artifacts"] = {"level1": {"path": str(PROJECT_ROOT / "sample_outputs/synthetic/a.json"), "sha256": "a" * 64}}
    atomic_write_json(path, bad)
    with pytest.raises(ValueError, match="public"):
        pipeline.load_config(path)


def test_validate_missing_run_is_read_only(cfg):
    with pytest.raises(ValueError, match="Missing knowledge"):
        pipeline.run(cfg, validate_only=True)
    assert not Path(cfg["output_root"]).exists()


def test_replacement_is_explicit_versioned_and_retains_other_episodes(cfg, tmp_path):
    pipeline.run(cfg, backend=FakeModel())
    raw_path = Path(cfg["episodes"][0]["raw"])
    original_hash = sha256_file(raw_path)
    replacement = new_source(cfg, tmp_path / "revised", "SYN1")
    changes = tmp_path / "changes.json"
    atomic_write_json(changes, [replacement])
    target = tmp_path / "revised-config.json"
    with pytest.raises(ValueError, match="Duplicate"):
        pipeline.extend(cfg, changes, "revised", target)
    result = pipeline.extend(cfg, changes, "revised", target, replace_existing=True)
    assert result["new_episode_ids"] == []
    assert result["replaced_episode_ids"] == ["SYN1"]
    assert result["referenced_artifacts"] == 3
    updated = pipeline.load_config(target)
    assert "reuse_artifacts" not in updated["episodes"][0]
    backend = FakeModel()
    pipeline.run(updated, backend=backend)
    assert backend.calls == ["level1", "level2", "level3", "level4"]
    assert sha256_file(raw_path) == original_hash
    assert pipeline.run(cfg, validate_only=True)["model_calls_this_execution"] == 0


def test_identical_replacement_never_schedules_paid_regeneration(cfg, tmp_path):
    pipeline.run(cfg, backend=FakeModel())
    changes = tmp_path / "same.json"
    atomic_write_json(changes, [cfg["episodes"][0]])
    with pytest.raises(ValueError, match="unchanged"):
        pipeline.extend(cfg, changes, "same", tmp_path / "same-config.json", replace_existing=True)


def test_prompt_edit_blocks_extraction_reuse_but_not_reviewed_release(cfg, tmp_path, monkeypatch):
    review = acceptance(cfg, tmp_path)
    release = tmp_path / "release.json"
    publish(cfg, review, release)
    prompts = tmp_path / "changed-prompts"
    prompts.mkdir()
    for p in pipeline.PROMPTS.glob("*.md"):
        (prompts / p.name).write_text(p.read_text() + "\nChanged future extraction policy.\n")
    monkeypatch.setattr(pipeline, "PROMPTS", prompts)
    with pytest.raises(ValueError, match="incompatible"):
        pipeline.run(cfg, dry_run=True)
    assert len(load_release(release)[0]) == 4
    with pytest.raises(ValueError, match="never new extraction"):
        pipeline.run(cfg, allow_pinned_prompts=True, backend=FakeModel())


def test_reference_changed_during_call_never_publishes_new_stage(cfg, tmp_path):
    updated = update_config(cfg, tmp_path)
    reference = Path(updated["episodes"][0]["reuse_artifacts"]["level2"]["path"])

    class ChangingModel(FakeModel):
        def complete(self, *args):
            response = super().complete(*args)
            reference.write_text(reference.read_text() + " ")
            return response

    backend = ChangingModel()
    with pytest.raises(ValueError, match="changed during extraction"):
        pipeline.run(updated, backend=backend)
    assert backend.calls == ["level1"]
    root = Path(updated["output_root"]) / updated["run_id"]
    assert not (root / "SYN3/level1.json").exists()
    assert len(list((root / "calls").glob("*.json"))) == 1


def test_replacement_release_citations_distinguish_same_record_id(cfg, tmp_path):
    from mentor_agent.runtime import citation

    review = acceptance(cfg, tmp_path)
    old_release = tmp_path / "release-old.json"
    publish(cfg, review, old_release)
    old_citation = citation(load_release(old_release)[0]["SYN1:R1"])
    additions = tmp_path / "replacement-sources.json"
    atomic_write_json(additions, [new_source(cfg, tmp_path / "replacement", "SYN1")])
    next_config = tmp_path / "replacement-config.json"
    pipeline.extend(cfg, additions, "replacement", next_config, replace_existing=True)
    updated = pipeline.load_config(next_config)
    pipeline.run(updated, backend=FakeModel())
    new_review = read_json(review)
    new_review["artifact_sha256"] = {r["logical_path"]: sha256_file(Path(r["path"]))
                                      for r in pipeline.release_locations(updated)}
    review_path, release_path = tmp_path / "review-new.json", tmp_path / "release-new.json"
    atomic_write_json(review_path, new_review)
    publish(updated, review_path, release_path)
    revised = citation(load_release(release_path)[0]["SYN1:R1"])
    assert old_citation["record_id"] == revised["record_id"]
    assert old_citation["knowledge_artifact_sha256"] != revised["knowledge_artifact_sha256"]
    assert citation(load_release(old_release)[0]["SYN1:R1"]) == old_citation


def test_extend_cli_is_no_inference_and_reports_plan(cfg, tmp_path, capsys, monkeypatch):
    from knowledge.__main__ import main

    pipeline.run(cfg, backend=FakeModel())
    source_file = tmp_path / "sources.json"
    target = tmp_path / "cli-config.json"
    atomic_write_json(source_file, [new_source(cfg, tmp_path / "cli-source")])
    monkeypatch.setenv("KNOWLEDGE_MODEL_ID", "synthetic")
    assert main(["extend", "--config", str(tmp_path / "config.yaml"), "--sources", str(source_file),
                 "--run-id", "cli-update", "--output", str(target)]) == 0
    import json
    result = json.loads(capsys.readouterr().out)
    assert result["model_calls"] == 0 and result["estimated_new_calls"] == 4
    assert not (Path(cfg["output_root"]) / "cli-update").exists()
