"""Synthetic orchestration checks; no corpus approval or subscription calls."""
from copy import deepcopy
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

from knowledge import batch
from knowledge.tests.test_pipeline import cfg, FakeModel
from transcription.common import atomic_write_json, read_json, sha256_file


def make_batch(cfg):
    entries = [{"episode_id": e["episode_id"], "raw": e["raw"], "draft": e["normalized"],
                "raw_sha256": sha256_file(Path(e["raw"])), "draft_sha256": sha256_file(Path(e["normalized"])),
                "prior_findings": []} for e in cfg["episodes"]]
    result = {"schema_version": "knowledge_batch.v1", "batch_id": "synthetic_batch",
              "output_root": cfg["output_root"], "model": "synthetic", "reasoning_effort": "high",
              "episodes": entries}
    path = Path(cfg["output_root"]).parent / "batch.json"
    atomic_write_json(path, result)
    return batch.load_batch(path), path


def reviewer_for(cfg, held=None):
    def review(raw, draft, output, backend, prior_findings):
        eid = read_json(raw)["metadata"]["episode_id"]
        source = next(e for e in cfg["episodes"] if e["episode_id"] == eid)
        return {"accepted": eid != held, "review_path": source["review"], "report_path": source["review"]}
    return review


def make_candidate_batch(cfg, review_schema=None):
    settings, path = make_batch(cfg)
    settings["extraction_mode"] = "candidate"
    for entry, source in zip(settings["episodes"], cfg["episodes"]):
        draft = Path(entry["draft"]).with_name(entry["episode_id"] + "-draft.json")
        batch.prepare(Path(entry["raw"]), draft)
        entry.update(draft=str(draft), draft_sha256=sha256_file(draft),
                     source_review=None, source_review_sha256=None)
        if review_schema:
            review = read_json(Path(source["review"]))
            review.update(schema_version=review_schema, decision="uncertain",
                          unresolved_critical_issues=["Synthetic ambiguity; do not supply missing context."])
            review_path = draft.with_name(entry["episode_id"] + "-held-review.json")
            atomic_write_json(review_path, review)
            entry.update(source_review=str(review_path), source_review_sha256=sha256_file(review_path))
    atomic_write_json(path, settings)
    return batch.load_batch(path), path


@pytest.fixture
def candidate_pipeline(monkeypatch):
    # Only test the orchestration boundary here; source/artifact validation belongs to the pipeline.
    loader = Mock(side_effect=read_json)
    runner = Mock(side_effect=lambda config, **kwargs: {
        "model_calls_this_execution": 0 if kwargs.get("validate_only") else 3,
        "ready_for_serving": False,
    })
    monkeypatch.setattr(batch.pipeline, "load_config", loader)
    monkeypatch.setattr(batch.pipeline, "run", runner)
    return loader, runner


def test_dry_run_never_calls_review_or_model(cfg):
    settings, _ = make_batch(cfg)
    assert settings["extraction_mode"] == "reviewed"
    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run must not call a model/reviewer")
    result = batch.execute(settings, dry_run=True, reviewer=forbidden, backend_factory=forbidden)
    assert result["maximum_initial_calls"] == 8
    assert result["extraction_mode"] == "reviewed"
    assert not (Path(settings["output_root"]) / settings["batch_id"]).exists()


@pytest.mark.parametrize("value", [None, True, "unknown", [], {}])
def test_invalid_transport_rejected_before_planning(cfg, value):
    settings, path = make_batch(cfg)
    settings["level3_transport"] = value
    atomic_write_json(path, settings)
    with pytest.raises(ValueError, match="transport"):
        batch.load_batch(path)
    with pytest.raises(ValueError, match="transport"):
        batch.execute(settings, dry_run=True)


def test_reference_transport_selected_without_changing_semantic_config(cfg, monkeypatch, candidate_pipeline):
    from knowledge import transport
    settings, path = make_candidate_batch(cfg)
    original_episode = batch._episode_config(settings, settings["episodes"][0],
        Path(settings["output_root"]) / settings["batch_id"], Path(settings["episodes"][0]["draft"]), None)
    settings["level3_transport"] = transport.FORMAT
    atomic_write_json(path, settings)
    settings = batch.load_batch(path)
    wrapped = Mock(return_value=object())
    ordinary = Mock(side_effect=AssertionError("Must use configured transport"))
    monkeypatch.setattr(transport, "L3ReferenceBackend", wrapped)
    monkeypatch.setattr(batch, "CodexBackend", ordinary)
    batch.execute(settings, dry_run=True)
    wrapped.assert_not_called()
    result = batch.execute(settings)
    assert result["counts"] == {"completed_candidate": 2}
    assert wrapped.call_count == 2
    ordinary.assert_not_called()
    root = Path(settings["output_root"]) / settings["batch_id"]
    assert read_json(root / "batch_manifest.json")["level3_transport"] == transport.FORMAT
    assert read_json(root / "SYN1/extraction_config.json") == original_episode
    forbidden = Mock(side_effect=AssertionError("Completed episodes must only validate"))
    batch.execute(settings, backend_factory=forbidden)
    forbidden.assert_not_called()
    settings["level3_transport"] = "expanded"
    with pytest.raises(ValueError, match="Batch settings changed"):
        batch.execute(settings)


def test_episode_isolation_resume_and_no_level4(cfg):
    settings, _ = make_batch(cfg)
    model = FakeModel()
    factory = lambda *args, **kwargs: model
    report = batch.execute(settings, workers=2, reviewer=reviewer_for(cfg, "SYN2"), backend_factory=factory)
    assert report["counts"] == {"completed": 1, "held": 1}
    assert model.calls == ["level1", "level2", "level3"]
    assert not report["ready_for_serving"]
    again = batch.execute(settings, reviewer=reviewer_for(cfg), backend_factory=factory)
    assert again["counts"] == report["counts"]
    assert len(model.calls) == 3
    assert again["episodes"][1]["action"] == "retained_hold"


def test_failed_episode_does_not_block_another_or_automatically_retry(cfg):
    settings, _ = make_batch(cfg)
    normal = reviewer_for(cfg)
    seen = []
    def review(raw, *args, **kwargs):
        eid = read_json(raw)["metadata"]["episode_id"]
        seen.append(eid)
        if eid == "SYN1":
            raise ValueError("Synthetic failure")
        return normal(raw, *args, **kwargs)
    factory = lambda *args, **kwargs: FakeModel()
    result = batch.execute(settings, reviewer=review, backend_factory=factory)
    assert result["counts"] == {"failed": 1, "completed": 1}
    batch.execute(settings, reviewer=review, backend_factory=factory)
    assert seen.count("SYN1") == 1
    result = batch.execute(settings, reviewer=normal, backend_factory=factory, retry_failed=True)
    assert result["counts"] == {"completed": 2}


def test_source_and_config_mismatch_fail_before_model(cfg):
    settings, path = make_batch(cfg)
    value = read_json(path)
    value["episodes"][0]["raw_sha256"] = "0" * 64
    atomic_write_json(path, value)
    with pytest.raises(ValueError, match="Pinned source changed"):
        batch.load_batch(path)
    batch.execute(settings, reviewer=reviewer_for(cfg), backend_factory=lambda *a, **k: FakeModel())
    settings["reasoning_effort"] = "xhigh"
    with pytest.raises(ValueError, match="Batch settings changed"):
        batch.execute(settings)


def test_worker_and_limit_constraints(cfg):
    settings, _ = make_batch(cfg)
    for workers in (0, 5, True):
        with pytest.raises(ValueError):
            batch.execute(settings, workers=workers, dry_run=True)
    with pytest.raises(ValueError):
        batch.execute(settings, max_episodes=0, dry_run=True)


def test_environment_cannot_silently_change_pinned_model(cfg, monkeypatch):
    _, path = make_batch(cfg)
    monkeypatch.setenv("KNOWLEDGE_MODEL_ID", "different")
    with pytest.raises(ValueError, match="pinned batch model"):
        batch.load_batch(path)


def test_corrupt_checkpoint_does_not_abort_other_episode(cfg):
    settings, _ = make_batch(cfg)
    path = Path(settings["output_root"]) / settings["batch_id"] / "SYN1/status.json"
    path.parent.mkdir(parents=True)
    path.write_text("broken", encoding="utf-8")
    result = batch.execute(settings, reviewer=reviewer_for(cfg), backend_factory=lambda *a, **k: FakeModel())
    assert result["counts"] == {"failed": 1, "completed": 1}
    assert path.read_text() == "broken"


@pytest.mark.parametrize("mode", [None, "", "Candidate", "unchecked", True, [], {}])
def test_invalid_extraction_mode_is_rejected(cfg, mode):
    settings, path = make_batch(cfg)
    settings["extraction_mode"] = mode
    atomic_write_json(path, settings)
    with pytest.raises(ValueError, match="extraction_mode"):
        batch.load_batch(path)
    with pytest.raises(ValueError, match="extraction_mode"):
        batch.execute(settings, dry_run=True)


@pytest.mark.parametrize("limit, calls", [(None, 6), (1, 3)])
def test_candidate_dry_run_counts_only_three_calls_per_episode(cfg, monkeypatch, limit, calls):
    settings, _ = make_candidate_batch(cfg)
    forbidden = Mock(side_effect=AssertionError("dry-run must not review, prepare or infer"))
    monkeypatch.setattr(batch, "prepare", forbidden)
    result = batch.execute(settings, max_episodes=limit, dry_run=True,
                           reviewer=forbidden, backend_factory=forbidden)
    assert result["maximum_initial_calls"] == calls
    assert result["extraction_mode"] == "candidate"
    assert not result["automatic_release"] and not result["ready_for_serving"]
    assert not (Path(settings["output_root"]) / settings["batch_id"]).exists()
    forbidden.assert_not_called()


@pytest.mark.parametrize("review_schema", [None, "transcript_review.v1", "transcript_review.v2"])
def test_candidate_stages_pinned_draft_and_optional_review_without_review_or_prepare(
        cfg, monkeypatch, candidate_pipeline, review_schema):
    settings, _ = make_candidate_batch(cfg, review_schema)
    forbidden = Mock(side_effect=AssertionError("candidate must not review or prepare"))
    monkeypatch.setattr(batch, "prepare", forbidden)
    monkeypatch.setattr("transcription.source_review.review_source", forbidden)
    factory = Mock(return_value=object())
    report = batch.execute(settings, workers=2, backend_factory=factory)
    assert report["counts"] == {"completed_candidate": 2}
    assert not report["automatic_release"] and not report["ready_for_serving"]
    assert report["extraction_mode"] == "candidate"
    root = Path(settings["output_root"]) / settings["batch_id"]
    assert read_json(root / "batch_manifest.json") == settings
    for entry, episode in zip(settings["episodes"], report["episodes"]):
        folder = root / entry["episode_id"]
        staged = read_json(folder / "extraction_config.json")
        assert staged == {
            "schema_version": "knowledge_config.v1", "run_id": entry["episode_id"],
            "output_root": str(root / "stages"), "model": settings["model"],
            "reasoning_effort": settings["reasoning_effort"], "timeout_seconds": settings["timeout_seconds"],
            "max_input_chars": settings["max_input_chars"], "max_calls": 3, "retry_count": 0,
            "extraction_mode": "candidate",
            "episodes": [{"episode_id": entry["episode_id"], "raw": entry["raw"],
                          "normalized": entry["draft"], "review": entry["source_review"],
                          "raw_sha256": entry["raw_sha256"], "normalized_sha256": entry["draft_sha256"],
                          "review_sha256": entry["source_review_sha256"]}],
        }
        assert not (folder / "normalized.json").exists() and not (folder / "review").exists()
        assert sha256_file(Path(entry["draft"])) == entry["draft_sha256"]
        assert not read_json(Path(entry["draft"]))["metadata"]["quality"]["ready_for_extraction"]
        if review_schema:
            assert sha256_file(Path(entry["source_review"])) == entry["source_review_sha256"]
            assert read_json(Path(entry["source_review"]))["decision"] == "uncertain"
        assert episode["quarantined"] and episode["publication_status"] == "blocked_candidate"
        assert episode["source_review_status"] == ("context_only" if review_schema else "not_supplied")
        assert episode["semantic_review_status"] == "pending"
        assert not episode["ready_for_serving"] and not episode["automatic_release"]
    loader, runner = candidate_pipeline
    assert loader.call_count == runner.call_count == factory.call_count == 2
    assert all(call.kwargs == {"through": "level3", "backend": factory.return_value,
                               "validate_only": False} for call in runner.call_args_list)
    loader.reset_mock()
    runner.reset_mock()
    again = batch.execute(settings, reviewer=forbidden, backend_factory=forbidden)
    assert again["counts"] == {"completed_candidate": 2}
    assert loader.call_count == runner.call_count == 2
    assert all(call.kwargs == {"through": "level3", "backend": None, "validate_only": True}
               for call in runner.call_args_list)
    assert all(e["report"]["model_calls_this_execution"] == 0 for e in again["episodes"])
    forbidden.assert_not_called()


@pytest.mark.parametrize("change", ["missing_hash", "wrong_hash", "missing_path", "public_path"])
def test_candidate_review_requires_private_hash_pinned_path(cfg, change):
    settings, path = make_candidate_batch(cfg, "transcript_review.v1")
    entry = settings["episodes"][0]
    if change == "missing_hash":
        del entry["source_review_sha256"]
    elif change == "wrong_hash":
        entry["source_review_sha256"] = "0" * 64
    elif change == "missing_path":
        del entry["source_review"]
    else:
        entry["source_review"] = str(Path(batch.__file__).resolve())
    atomic_write_json(path, settings)
    with pytest.raises(ValueError):
        batch.load_batch(path)


@pytest.mark.parametrize("field", ["raw", "draft", "source_review"])
def test_candidate_rechecks_pins_and_isolates_changed_source(cfg, candidate_pipeline, field):
    settings, _ = make_candidate_batch(cfg, "transcript_review.v1")
    path = Path(settings["episodes"][0][field])
    changed = read_json(path)
    changed["synthetic_change"] = True
    atomic_write_json(path, changed)
    factory = Mock(return_value=object())
    result = batch.execute(settings, workers=2, backend_factory=factory)
    assert result["counts"] == {"failed": 1, "completed_candidate": 1}
    assert result["episodes"][0]["error_type"] == "ValueError"
    assert factory.call_count == candidate_pipeline[1].call_count == 1
    candidate_pipeline[1].reset_mock()
    forbidden = Mock(side_effect=AssertionError("no automatic retry or paid completed resume"))
    again = batch.execute(settings, backend_factory=forbidden)
    assert again["episodes"][0]["action"] == "needs_explicit_retry"
    assert candidate_pipeline[1].call_args.kwargs["validate_only"]
    forbidden.assert_not_called()


def test_candidate_pipeline_failure_is_isolated_and_retry_is_explicit(cfg, candidate_pipeline):
    settings, _ = make_candidate_batch(cfg)
    runner = candidate_pipeline[1]
    run = runner.side_effect
    def fail_one(config, **kwargs):
        if config["run_id"] == "SYN1":
            raise ValueError("Synthetic extraction failure")
        return run(config, **kwargs)
    runner.side_effect = fail_one
    factory = Mock(return_value=object())
    result = batch.execute(settings, workers=2, backend_factory=factory)
    assert result["counts"] == {"failed": 1, "completed_candidate": 1}
    factory.reset_mock()
    again = batch.execute(settings, backend_factory=factory)
    assert again["counts"] == result["counts"]
    assert again["episodes"][0]["action"] == "needs_explicit_retry"
    factory.assert_not_called()
    runner.side_effect = run
    result = batch.execute(settings, retry_failed=True, backend_factory=factory)
    assert result["counts"] == {"completed_candidate": 2}
    assert factory.call_count == 1


@pytest.mark.parametrize("change", ["missing_stage", "missing_config", "changed_config"])
def test_completed_candidate_never_regenerates_invalid_work(cfg, candidate_pipeline, change):
    settings, _ = make_candidate_batch(cfg)
    factory = Mock(return_value=object())
    batch.execute(settings, backend_factory=factory)
    config_path = Path(settings["output_root"]) / settings["batch_id"] / "SYN1/extraction_config.json"
    runner = candidate_pipeline[1]
    if change == "missing_stage":
        run = runner.side_effect
        def fail_validation(config, **kwargs):
            assert kwargs["validate_only"]
            if config["run_id"] == "SYN1":
                raise ValueError("Missing knowledge artifact; validation does not run a model.")
            return run(config, **kwargs)
        runner.side_effect = fail_validation
    elif change == "missing_config":
        config_path.unlink()
    else:
        altered = read_json(config_path)
        altered["max_calls"] = 99
        atomic_write_json(config_path, altered)
    runner.reset_mock()
    forbidden = Mock(side_effect=AssertionError("completed candidate must not instantiate a backend"))
    result = batch.execute(settings, retry_failed=True, backend_factory=forbidden)
    assert result["counts"] == {"failed": 1, "completed_candidate": 1}
    assert result["episodes"][0]["candidate_extraction_completed"]
    assert all(call.kwargs["validate_only"] for call in runner.call_args_list)
    again = batch.execute(settings, retry_failed=True, backend_factory=forbidden)
    assert again["counts"] == result["counts"]
    if change == "missing_config":
        assert not config_path.exists()
    elif change == "changed_config":
        assert read_json(config_path) == altered
    forbidden.assert_not_called()


@pytest.mark.parametrize("change", ["mode", "review", "limits", "timeout"])
def test_candidate_batch_pins_all_settings(cfg, candidate_pipeline, change):
    settings, _ = make_candidate_batch(cfg, "transcript_review.v1")
    batch.execute(settings, backend_factory=Mock(return_value=object()))
    if change == "mode":
        settings["extraction_mode"] = "reviewed"
    elif change == "review":
        settings["episodes"][0]["source_review_sha256"] = "0" * 64
    elif change == "limits":
        settings["max_input_chars"] += 1
    else:
        settings["timeout_seconds"] += 1
    candidate_pipeline[1].reset_mock()
    forbidden = Mock(side_effect=AssertionError("changed settings must fail before inference"))
    with pytest.raises(ValueError, match="Batch settings changed"):
        batch.execute(settings, reviewer=forbidden, backend_factory=forbidden)
    candidate_pipeline[1].assert_not_called()
    forbidden.assert_not_called()


def test_reviewed_batch_without_explicit_mode_still_resumes(cfg):
    settings, path = make_batch(cfg)
    del settings["extraction_mode"]
    model = FakeModel()
    factory = lambda *args, **kwargs: model
    result = batch.execute(settings, reviewer=reviewer_for(cfg), backend_factory=factory)
    assert result["counts"] == {"completed": 2}
    again = batch.execute(batch.load_batch(path), reviewer=reviewer_for(cfg), backend_factory=factory)
    assert again["counts"] == result["counts"]
    assert len(model.calls) == 6


def test_candidate_rejects_unintegrated_prior_findings_before_any_calls(cfg, candidate_pipeline):
    settings, path = make_candidate_batch(cfg, "transcript_review.v1")
    settings["episodes"][0]["prior_findings"] = ["Synthetic ambiguity must not be silently dropped"]
    atomic_write_json(path, settings)
    with pytest.raises(ValueError, match="prior_findings must be empty; use a pinned source_review"):
        batch.load_batch(path)
    forbidden = Mock(side_effect=AssertionError("invalid candidate findings must fail before inference"))
    for dry_run in (False, True):
        with pytest.raises(ValueError, match="prior_findings must be empty; use a pinned source_review"):
            batch.execute(settings, dry_run=dry_run, reviewer=forbidden, backend_factory=forbidden)
    candidate_pipeline[1].assert_not_called()
    forbidden.assert_not_called()
    assert not (Path(settings["output_root"]) / settings["batch_id"]).exists()


@pytest.mark.parametrize("review_schema", [None, "transcript_review.v1"])
def test_candidate_real_pipeline_mocked_model_resume_and_missing_artifact(cfg, monkeypatch, review_schema):
    from knowledge.tests.test_candidate_extraction import CandidateModel

    settings, _ = make_candidate_batch(cfg, review_schema)
    if review_schema is None:
        for entry in settings["episodes"]:
            del entry["source_review"], entry["source_review_sha256"]
    forbidden = Mock(side_effect=AssertionError("candidate must not review, prepare or regenerate completed work"))
    monkeypatch.setattr(batch, "prepare", forbidden)
    model = CandidateModel()
    report = batch.execute(settings, reviewer=forbidden, backend_factory=lambda *a, **k: model)
    assert report["counts"] == {"completed_candidate": 2}
    assert model.calls == ["level1", "level2", "level3"] * 2
    for episode in report["episodes"]:
        assert episode["report"]["model_calls_this_execution"] == 3
        for record in episode["report"]["artifacts"]:
            artifact = read_json(Path(record["artifact_path"]))
            assert artifact["schema_version"] == "knowledge_candidate.v1"
    resumed = batch.execute(settings, reviewer=forbidden, backend_factory=forbidden)
    assert resumed["counts"] == report["counts"]
    assert all(e["report"]["model_calls_this_execution"] == 0 for e in resumed["episodes"])
    last_stage = Path(report["episodes"][0]["report"]["artifacts"][-1]["artifact_path"])
    last_stage.unlink()
    for retry in (False, True):
        failed = batch.execute(settings, retry_failed=retry, reviewer=forbidden, backend_factory=forbidden)
        assert failed["counts"] == {"failed": 1, "completed_candidate": 1}
        assert failed["episodes"][0]["error_type"] == "ValueError"
        assert not last_stage.exists()
    assert len(model.calls) == 6
    forbidden.assert_not_called()


def completed_batch(cfg, mode="candidate"):
    from knowledge.tests.test_candidate_extraction import CandidateModel

    settings, path = make_candidate_batch(cfg) if mode == "candidate" else make_batch(cfg)
    model = CandidateModel() if mode == "candidate" else FakeModel()
    report = batch.execute(settings, reviewer=reviewer_for(cfg), backend_factory=lambda *a, **k: model)
    assert report["counts"] == {"completed_candidate" if mode == "candidate" else "completed": 2}
    return settings, path, report, model


@pytest.mark.parametrize("mode", ["candidate", "reviewed"])
def test_consolidation_references_existing_stages_and_only_l4_is_generated(cfg, monkeypatch, mode):
    settings, path, report, model = completed_batch(cfg, mode)
    root = Path(settings["output_root"]) / settings["batch_id"]
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    forbidden = Mock(side_effect=AssertionError("consolidation planning must not review, prepare or infer"))
    monkeypatch.setattr(batch, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.CodexBackend", forbidden)
    monkeypatch.setattr(batch, "prepare", forbidden)
    runner = Mock(wraps=batch.pipeline.run)
    monkeypatch.setattr(batch.pipeline, "run", runner)
    output = path.with_name("consolidation.json")
    result = batch.consolidation_config(settings, output, "synthetic_l4")
    assert result["episode_ids"] == ["SYN1", "SYN2"]
    assert result["model_calls"] == 0 and result["estimated_new_calls"] == 1
    assert result["referenced_artifacts"] == 6
    assert not result["automatic_release"] and not result["ready_for_serving"]
    assert [call.kwargs for call in runner.call_args_list] == [
        {"through": "level3", "validate_only": True},
        {"through": "level3", "validate_only": True},
        {"through": "level4", "dry_run": True},
    ]
    consolidated = batch.pipeline.load_config(output)
    assert consolidated["output_root"] == str(root / "consolidations")
    assert consolidated["run_id"] == "synthetic_l4" and consolidated["extraction_mode"] == mode
    assert consolidated["max_calls"] == 1 and consolidated["retry_count"] == 0
    for key in ("model", "reasoning_effort", "timeout_seconds", "max_input_chars"):
        assert consolidated[key] == settings[key]
    for source, episode in zip(consolidated["episodes"], report["episodes"]):
        original = read_json(Path(episode["config"]))["episodes"][0]
        assert {k: v for k, v in source.items() if k != "reuse_artifacts"} == original
        assert source["reuse_artifacts"] == {
            r["stage"]: {"path": r["artifact_path"], "sha256": r["sha256"]}
            for r in episode["report"]["artifacts"]}
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert not (root / "consolidations").exists()
    assert len(model.calls) == 6
    forbidden.assert_not_called()
    # Execute only with a synthetic model to prove L1-L3 are referenced, never regenerated.
    generated = batch.pipeline.run(consolidated, through="level4", backend=model)
    assert generated["model_calls_this_execution"] == 1 and model.calls[-1] == "level4"
    assert len(model.calls) == 7
    assert [r["action"] for r in generated["artifacts"]] == ["referenced"] * 6 + ["created"]
    assert all(p.read_bytes() == payload for p, payload in before.items())
    assert batch.pipeline.run(consolidated, through="level4", validate_only=True)["model_calls_this_execution"] == 0
    forbidden.assert_not_called()


@pytest.mark.parametrize("state", ["running", "failed", "held", "completed"])
def test_consolidation_rejects_any_incomplete_or_wrong_mode_job(cfg, state):
    settings, path, _, _ = completed_batch(cfg)
    status_path = Path(settings["output_root"]) / settings["batch_id"] / "SYN2/status.json"
    status = read_json(status_path)
    status["status"] = state
    atomic_write_json(status_path, status)
    output = path.with_name("consolidation.json")
    with pytest.raises(ValueError, match="Selected episode SYN2 is not completed_candidate"):
        batch.consolidation_config(settings, output, "synthetic_l4")
    assert not output.exists()
    assert read_json(status_path) == status


@pytest.mark.parametrize("limit", [0, 1, -1, True, 1.5])
def test_consolidation_requires_two_episodes_and_valid_limit(cfg, limit):
    settings, path = make_candidate_batch(cfg)
    output = path.with_name("consolidation.json")
    with pytest.raises(ValueError):
        batch.consolidation_config(settings, output, "synthetic_l4", max_episodes=limit)
    assert not output.exists()


def test_consolidation_prefix_does_not_discover_or_silently_skip_jobs(cfg):
    settings, path, _, _ = completed_batch(cfg)
    root = Path(settings["output_root"]) / settings["batch_id"]
    settings["episodes"].append({**settings["episodes"][0], "episode_id": "SYN3"})
    atomic_write_json(root / "batch_manifest.json", settings)
    atomic_write_json(root / "SYN3/status.json", {"episode_id": "SYN3", "status": "running"})
    output = path.with_name("consolidation.json")
    result = batch.consolidation_config(settings, output, "synthetic_l4", max_episodes=2)
    assert result["episode_ids"] == ["SYN1", "SYN2"]
    missing = path.with_name("all.json")
    with pytest.raises(ValueError, match="Selected episode SYN3 is not completed_candidate"):
        batch.consolidation_config(settings, missing, "all_l4")
    assert not missing.exists()


@pytest.mark.parametrize("fault", ["missing_stage", "tampered_stage", "source", "settings", "mode", "missing_config"])
def test_consolidation_rejects_invalid_completed_work_without_output(cfg, fault):
    settings, path, report, model = completed_batch(cfg)
    episode = report["episodes"][0]
    config_path = Path(episode["config"])
    stage = Path(episode["report"]["artifacts"][-1]["artifact_path"])
    if fault == "missing_stage":
        stage.unlink()
    elif fault == "tampered_stage":
        value = read_json(stage)
        value["fingerprint"] = "changed"
        atomic_write_json(stage, value)
    elif fault == "source":
        source = Path(settings["episodes"][0]["draft"])
        value = read_json(source)
        value["synthetic_change"] = True
        atomic_write_json(source, value)
    elif fault == "missing_config":
        config_path.unlink()
    else:
        value = read_json(config_path)
        value["timeout_seconds" if fault == "settings" else "extraction_mode"] = 1 if fault == "settings" else "reviewed"
        atomic_write_json(config_path, value)
    output = path.with_name("consolidation.json")
    with pytest.raises((ValueError, OSError)):
        batch.consolidation_config(settings, output, "synthetic_l4")
    assert not output.exists() and len(model.calls) == 6


@pytest.mark.parametrize("fault", ["existing_output", "existing_run", "batch_changed", "public_output", "unsafe_run"])
def test_consolidation_destination_and_batch_are_immutable(cfg, fault):
    settings, path, _, _ = completed_batch(cfg)
    output = path.with_name("consolidation.json")
    run_id = "synthetic_l4"
    if fault == "existing_output":
        atomic_write_json(output, {"preserve": True})
    elif fault == "existing_run":
        (Path(settings["output_root"]) / settings["batch_id"] / "consolidations" / run_id).mkdir(parents=True)
    elif fault == "batch_changed":
        settings["model"] = "different"
    elif fault == "public_output":
        output = Path(batch.__file__).resolve().with_name("consolidation.json")
    else:
        run_id = "../escape"
    with pytest.raises(ValueError):
        batch.consolidation_config(settings, output, run_id)
    if fault == "existing_output":
        assert read_json(output) == {"preserve": True}
    else:
        assert not output.exists()


def test_consolidation_cli_never_executes_batch(cfg, monkeypatch, capsys):
    settings, path, _, model = completed_batch(cfg)
    forbidden = Mock(side_effect=AssertionError("consolidation CLI must not execute a batch"))
    monkeypatch.setattr(batch, "execute", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.CodexBackend", forbidden)
    output = path.with_name("consolidation.json")
    assert batch.main(["--config", str(path), "--consolidate-config", str(output),
                       "--run-id", "synthetic_l4", "--max-episodes", "2"]) == 0
    assert output.exists() and len(model.calls) == 6
    assert '"model_calls": 0' in capsys.readouterr().out
    forbidden.assert_not_called()


@pytest.mark.parametrize("fault", ["dry_run_failure", "extra_calls", "checkpoint_changed"])
def test_consolidation_final_plan_must_pass_before_any_config_write(cfg, monkeypatch, fault):
    settings, path, _, model = completed_batch(cfg)
    original_run = batch.pipeline.run
    def changed_plan(config, **kwargs):
        if kwargs.get("dry_run"):
            if fault == "dry_run_failure":
                raise ValueError("Synthetic L4 plan failure")
            result = original_run(config, **kwargs)
            if fault == "extra_calls":
                result["estimated_new_calls"] = 2
            else:
                status_path = Path(settings["output_root"]) / settings["batch_id"] / "SYN1/status.json"
                status = read_json(status_path)
                status["status"] = "running"
                atomic_write_json(status_path, status)
            return result
        assert kwargs.get("validate_only")
        return original_run(config, **kwargs)
    monkeypatch.setattr(batch.pipeline, "run", changed_plan)
    output = path.with_name("consolidation.json")
    with pytest.raises(ValueError):
        batch.consolidation_config(settings, output, "synthetic_l4")
    assert not output.exists() and len(model.calls) == 6


@pytest.mark.parametrize("extra", [["--run-id", "l4"], ["--consolidate-config", "/tmp/unused.json"],
                                  ["--consolidate-config", "/tmp/unused.json", "--run-id", "l4", "--dry-run"],
                                  ["--consolidate-config", "/tmp/unused.json", "--run-id", "l4", "--retry-failed"]])
def test_consolidation_cli_rejects_ambiguous_options(cfg, monkeypatch, extra):
    _, path = make_batch(cfg)
    forbidden = Mock(side_effect=AssertionError("invalid CLI must not execute"))
    monkeypatch.setattr(batch, "execute", forbidden)
    monkeypatch.setattr(batch, "consolidation_config", forbidden)
    with pytest.raises(SystemExit) as error:
        batch.main(["--config", str(path), *extra])
    assert error.value.code == 2
    forbidden.assert_not_called()


@pytest.mark.parametrize("key, value", [
    ("retry_count", -1), ("retry_count", 2), ("retry_count", True), ("retry_count", None),
    ("retry_count", "1"), ("max_consecutive_failures", 0), ("max_consecutive_failures", -1),
    ("max_consecutive_failures", True), ("max_consecutive_failures", None), ("max_consecutive_failures", 1.5),
])
def test_optional_retry_and_failure_limits_are_strictly_validated(cfg, key, value):
    settings, path = make_batch(cfg)
    settings[key] = value
    atomic_write_json(path, settings)
    with pytest.raises(ValueError, match=key):
        batch.load_batch(path)
    with pytest.raises(ValueError, match=key):
        batch.execute(settings, dry_run=True)


def test_optional_retry_and_failure_limits_do_not_change_old_configs(cfg):
    settings, _ = make_batch(cfg)
    assert "retry_count" not in settings and "max_consecutive_failures" not in settings
    result = batch.execute(settings, reviewer=reviewer_for(cfg), backend_factory=lambda *a, **k: FakeModel())
    manifest = Path(settings["output_root"]) / settings["batch_id"] / "batch_manifest.json"
    assert read_json(manifest) == settings
    assert "failure_limit_reached" not in result


@pytest.mark.parametrize("retry_count", [0, 1])
def test_candidate_retry_budget_is_forwarded_and_pinned(cfg, candidate_pipeline, retry_count):
    settings, path = make_candidate_batch(cfg)
    settings["retry_count"] = retry_count
    settings["max_consecutive_failures"] = 2
    atomic_write_json(path, settings)
    settings = batch.load_batch(path)
    result = batch.execute(settings, backend_factory=Mock(return_value=object()))
    assert result["counts"] == {"completed_candidate": 2}
    assert not result["failure_limit_reached"]
    for episode in result["episodes"]:
        staged = read_json(Path(episode["config"]))
        assert staged["retry_count"] == retry_count
        assert staged["max_calls"] == 3 * (1 + retry_count)
    settings["retry_count"] = 1 - retry_count
    with pytest.raises(ValueError, match="Batch settings changed"):
        batch.execute(settings)


@pytest.mark.parametrize("mode", ["candidate", "reviewed"])
def test_batch_one_structured_retry_uses_pipeline_repair_without_changing_consolidation_budget(cfg, mode):
    from knowledge.tests.test_candidate_extraction import CandidateModel

    settings, path = make_candidate_batch(cfg) if mode == "candidate" else make_batch(cfg)
    settings["retry_count"] = 1
    base = CandidateModel() if mode == "candidate" else FakeModel()
    seen, repaired = set(), []
    class RepairOnce:
        def complete(self, prompt, context, schema):
            response, usage = base.complete(prompt, context, schema)
            if "threads" in response:
                eid = response["episode_id"]
                if eid not in seen:
                    seen.add(eid)
                    response["threads"][0]["merged_ids"] = [999]
                else:
                    repaired.append(context["validation_repair"])
            return response, usage
    model = RepairOnce()
    result = batch.execute(settings, reviewer=reviewer_for(cfg), backend_factory=lambda *a, **k: model)
    assert result["counts"] == {"completed_candidate" if mode == "candidate" else "completed": 2}
    assert len(base.calls) == 8 and len(repaired) == 2
    assert all(e["report"]["model_calls_this_execution"] == 4 for e in result["episodes"])
    output = path.with_name("consolidation.json")
    batch.consolidation_config(settings, output, "repaired_l4")
    consolidated = read_json(output)
    assert consolidated["max_calls"] == 1 and consolidated["retry_count"] == 0


def failure_batch(cfg, count=5, limit=2):
    settings, path = make_candidate_batch(cfg)
    # The mocked pipeline below owns source validation; these are synthetic orchestration slots.
    for n in range(3, count + 1):
        settings["episodes"].append({**settings["episodes"][0], "episode_id": f"SYN{n}"})
    settings["max_consecutive_failures"] = limit
    atomic_write_json(path, settings)
    return batch.load_batch(path)


def test_failure_limit_defers_queued_jobs_without_backend_or_episode_writes(cfg, candidate_pipeline):
    settings = failure_batch(cfg)
    candidate_pipeline[1].side_effect = RuntimeError("Synthetic account or model failure")
    factory = Mock(return_value=object())
    result = batch.execute(settings, workers=1, backend_factory=factory)
    assert result["counts"] == {"failed": 2, "deferred": 3}
    assert result["failure_limit_reached"] and result["consecutive_failures"] == 2
    assert factory.call_count == candidate_pipeline[1].call_count == 2
    root = Path(settings["output_root"]) / settings["batch_id"]
    failed = {eid: (root / eid / "status.json").read_bytes() for eid in ("SYN1", "SYN2")}
    for episode in result["episodes"][2:]:
        assert episode["action"] == "failure_limit_reached"
        assert not episode["ready_for_serving"]
        assert not (root / episode["episode_id"]).exists()
    candidate_pipeline[1].side_effect = lambda config, **kwargs: {"model_calls_this_execution": 3, "ready_for_serving": False}
    factory.reset_mock()
    resumed = batch.execute(settings, backend_factory=factory)
    assert resumed["counts"] == {"failed": 2, "completed_candidate": 3}
    assert not resumed["failure_limit_reached"] and resumed["consecutive_failures"] == 0
    assert factory.call_count == 3
    for episode in resumed["episodes"][:2]:
        assert episode["action"] == "needs_explicit_retry"
        assert (root / episode["episode_id"] / "status.json").read_bytes() == failed[episode["episode_id"]]


def test_actual_completion_resets_consecutive_failures(cfg, candidate_pipeline):
    settings = failure_batch(cfg, count=5, limit=2)
    normal = candidate_pipeline[1].side_effect
    def intermittent(config, **kwargs):
        if config["run_id"] in {"SYN1", "SYN3", "SYN4"}:
            raise RuntimeError("Synthetic failure")
        return normal(config, **kwargs)
    candidate_pipeline[1].side_effect = intermittent
    result = batch.execute(settings, workers=1, backend_factory=Mock(return_value=object()))
    assert result["counts"] == {"failed": 3, "completed_candidate": 1, "deferred": 1}
    assert result["consecutive_failures"] == 2
    assert candidate_pipeline[1].call_count == 4


@pytest.mark.parametrize("old_state", ["failed", "held", "running"])
def test_old_failures_and_holds_neither_count_nor_reset_failure_streak(cfg, candidate_pipeline, old_state):
    settings = failure_batch(cfg, count=4, limit=2)
    root = Path(settings["output_root"]) / settings["batch_id"]
    old_path = root / "SYN2/status.json"
    old_status = {"episode_id": "SYN2", "status": old_state, "ready_for_serving": False}
    atomic_write_json(old_path, old_status)
    candidate_pipeline[1].side_effect = RuntimeError("Synthetic failure")
    factory = Mock(return_value=object())
    result = batch.execute(settings, workers=1, backend_factory=factory)
    assert [r["status"] for r in result["episodes"]] == ["failed", old_state, "failed", "deferred"]
    assert factory.call_count == 2
    assert result["consecutive_failures"] == 2 and result["failure_limit_reached"]
    assert read_json(old_path) == old_status and not (root / "SYN4").exists()


def test_unreadable_checkpoint_counts_toward_failure_limit(cfg, candidate_pipeline):
    settings = failure_batch(cfg, count=3, limit=1)
    root = Path(settings["output_root"]) / settings["batch_id"]
    path = root / "SYN1/status.json"
    atomic_write_json(path, "not a checkpoint object")
    factory = Mock(return_value=object())
    result = batch.execute(settings, workers=1, backend_factory=factory)
    assert result["counts"] == {"failed": 1, "deferred": 2}
    assert result["failure_limit_reached"]
    assert read_json(path) == "not a checkpoint object"
    factory.assert_not_called()
    candidate_pipeline[1].assert_not_called()


def test_parallel_failure_limit_stops_queue_but_allows_admitted_job_to_finish(cfg, monkeypatch, candidate_pipeline):
    settings = failure_batch(cfg, count=4, limit=1)
    second_started, deferred_observed = Event(), Event()
    normal = candidate_pipeline[1].side_effect
    def concurrent(config, **kwargs):
        if config["run_id"] == "SYN1":
            assert second_started.wait(5), "second worker did not start"
            raise RuntimeError("Synthetic account failure")
        assert config["run_id"] == "SYN2", "queued job reached the pipeline after failure limit"
        second_started.set()
        assert deferred_observed.wait(5), "queued jobs were not deferred before in-flight completion"
        return normal(config, **kwargs)
    candidate_pipeline[1].side_effect = concurrent
    original_write = batch.atomic_write_json
    def observe_summary(path, payload, **kwargs):
        original_write(path, payload, **kwargs)
        if path.name == "summary.json" and payload.get("counts", {}).get("deferred"):
            deferred_observed.set()
    monkeypatch.setattr(batch, "atomic_write_json", observe_summary)
    factory = Mock(return_value=object())
    result = batch.execute(settings, workers=2, backend_factory=factory)
    assert result["counts"] == {"failed": 1, "completed_candidate": 1, "deferred": 2}
    assert result["failure_limit_reached"] and result["consecutive_failures"] == 0
    assert factory.call_count == candidate_pipeline[1].call_count == 2
    root = Path(settings["output_root"]) / settings["batch_id"]
    assert not (root / "SYN3").exists() and not (root / "SYN4").exists()


def candidate_reuse_batch(cfg, prefix=3):
    original, path, report, model = completed_batch(cfg)
    settings = deepcopy(original)
    settings["batch_id"] = "synthetic_reuse_batch"
    for entry, episode in zip(settings["episodes"], report["episodes"]):
        entry["reuse_artifacts"] = {
            r["stage"]: {"path": r["artifact_path"], "sha256": r["sha256"]}
            for r in episode["report"]["artifacts"][:prefix]}
    path = path.with_name("reuse_batch.json")
    atomic_write_json(path, settings)
    old_root = Path(original["output_root"]) / original["batch_id"]
    return batch.load_batch(path), path, old_root, model


def test_candidate_references_validate_without_models_copying_or_old_output_writes(cfg, monkeypatch):
    settings, path, old_root, model = candidate_reuse_batch(cfg)
    before = {p: p.read_bytes() for p in old_root.rglob("*") if p.is_file()}
    forbidden = Mock(side_effect=AssertionError("references must not review, normalize or call a backend"))
    monkeypatch.setattr(batch, "prepare", forbidden)
    monkeypatch.setattr(batch, "CodexBackend", forbidden)
    root = Path(settings["output_root"]) / settings["batch_id"]
    plan = batch.execute(settings, dry_run=True, reviewer=forbidden, backend_factory=forbidden)
    assert plan["reuse_bindings_validated"] and plan["referenced_artifacts"] == 6
    assert plan["estimated_new_calls"] == plan["maximum_initial_calls"] == 0
    assert not root.exists()
    for _ in range(2):
        report = batch.execute(settings, reviewer=forbidden, backend_factory=forbidden)
        assert report["counts"] == {"completed_candidate": 2}
        for entry, episode in zip(settings["episodes"], report["episodes"]):
            assert episode["report"]["model_calls_this_execution"] == 0
            assert [r["action"] for r in episode["report"]["artifacts"]] == ["referenced"] * 3
            source = read_json(Path(episode["config"]))["episodes"][0]
            assert source["reuse_artifacts"] == entry["reuse_artifacts"]
            assert source["raw_sha256"] == entry["raw_sha256"]
            assert source["normalized_sha256"] == entry["draft_sha256"]
            assert episode["quarantined"] and not episode["ready_for_serving"]
    assert not (root / "stages").exists()
    output = path.with_name("reuse_consolidation.json")
    result = batch.consolidation_config(settings, output, "reuse_l4")
    assert result["model_calls"] == 0 and result["estimated_new_calls"] == 1
    for original, consolidated in zip(settings["episodes"], read_json(output)["episodes"]):
        assert consolidated["reuse_artifacts"] == original["reuse_artifacts"]
    assert {p: p.read_bytes() for p in old_root.rglob("*") if p.is_file()} == before
    assert len(model.calls) == 6
    forbidden.assert_not_called()


@pytest.mark.parametrize("prefix", [1, 2])
def test_candidate_reuse_prefix_only_generates_remaining_stages(cfg, prefix):
    from knowledge.tests.test_candidate_extraction import CandidateModel

    settings, _, old_root, _ = candidate_reuse_batch(cfg, prefix)
    before = {p: p.read_bytes() for p in old_root.rglob("*") if p.is_file()}
    model = CandidateModel()
    plan = batch.execute(settings, dry_run=True)
    assert plan["estimated_new_calls"] == plan["maximum_initial_calls"] == 2 * (3 - prefix)
    report = batch.execute(settings, backend_factory=lambda *a, **k: model)
    assert report["counts"] == {"completed_candidate": 2}
    assert model.calls == list(batch.pipeline.STAGES[prefix:3]) * 2
    for episode in report["episodes"]:
        assert [r["action"] for r in episode["report"]["artifacts"]] == ["referenced"] * prefix + ["created"] * (3 - prefix)
    assert {p: p.read_bytes() for p in old_root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("fault", ["hash", "missing", "draft_pin", "draft_repinned", "review_added",
                                   "other_episode", "fingerprint", "schema", "evidence"])
def test_candidate_reuse_invalid_bindings_fail_before_any_worker_model(cfg, dry_run, fault):
    settings, _, _, _ = candidate_reuse_batch(cfg, prefix=2)
    # Corrupt the second episode so a first-job-only preflight would incorrectly infer.
    entry = settings["episodes"][1]
    ref = entry["reuse_artifacts"]["level2"]
    path = Path(ref["path"])
    if fault == "missing":
        path.unlink()
    elif fault == "hash":
        ref["sha256"] = "0" * 64
    elif fault in {"draft_pin", "draft_repinned"}:
        draft = Path(entry["draft"])
        value = read_json(draft)
        value["synthetic_revision"] = True
        atomic_write_json(draft, value)
        if fault == "draft_repinned":
            entry["draft_sha256"] = sha256_file(draft)
    elif fault == "review_added":
        entry["source_review"] = cfg["episodes"][1]["review"]
        entry["source_review_sha256"] = sha256_file(Path(entry["source_review"]))
    elif fault == "other_episode":
        entry["reuse_artifacts"] = deepcopy(settings["episodes"][0]["reuse_artifacts"])
    else:
        value = read_json(path)
        if fault == "fingerprint":
            value["fingerprint"] = "0" * 64
        elif fault == "schema":
            value["schema_version"] = "knowledge.v2"
        else:
            value["evidence"] = {}
        atomic_write_json(path, value)
        ref["sha256"] = sha256_file(path)
    factory = Mock(side_effect=AssertionError("preflight must reject before any backend"))
    with pytest.raises((ValueError, OSError)):
        batch.execute(settings, dry_run=dry_run, backend_factory=factory)
    factory.assert_not_called()
    assert not (Path(settings["output_root"]) / settings["batch_id"]).exists()


@pytest.mark.parametrize("fault", ["not_mapping", "gap", "level4", "missing_sha", "bad_sha", "public_path",
                                   "bad_path_type", "extra_field", "reviewed"])
def test_batch_reuse_shape_and_mode_are_validated_on_load_and_execute(cfg, fault):
    settings, path = make_candidate_batch(cfg)
    reference = {"path": str(path.with_name("synthetic_level1.json")), "sha256": "a" * 64}
    reuse = {"level1": reference}
    if fault == "not_mapping":
        reuse = []
    elif fault == "gap":
        reuse = {"level2": reference}
    elif fault == "level4":
        reuse = {s: reference for s in batch.pipeline.STAGES}
    elif fault == "missing_sha":
        del reference["sha256"]
    elif fault == "bad_sha":
        reference["sha256"] = "A" * 64
    elif fault == "public_path":
        reference["path"] = str(Path(batch.__file__).resolve())
    elif fault == "bad_path_type":
        reference["path"] = None
    elif fault == "extra_field":
        reference["accept"] = True
    else:
        settings["extraction_mode"] = "reviewed"
    settings["episodes"][0]["reuse_artifacts"] = reuse
    atomic_write_json(path, settings)
    with pytest.raises(ValueError):
        batch.load_batch(path)
    with pytest.raises(ValueError):
        batch.execute(settings, dry_run=True)


def test_candidate_reuse_explicit_revised_l2_preserves_parent_and_regenerates_only_l3(cfg):
    from knowledge.tests.test_candidate_extraction import CandidateModel

    settings, path, old_root, _ = candidate_reuse_batch(cfg, prefix=2)
    settings["episodes"] = settings["episodes"][:1]
    entry = settings["episodes"][0]
    ref = entry["reuse_artifacts"]["level2"]
    parent = Path(ref["path"])
    parent_bytes = parent.read_bytes()
    revision = read_json(parent)
    assessment = revision["content"]["case_assessments"][0]
    case = revision["content"]["cases"][0]
    assessment["supplementary_notes"] = [{"kind": "interpretive_caution", "merged_ids": case["merged_ids"],
                                          "note": "[Agent clarification] Synthetic revision; no new speaker evidence."}]
    episode_cfg = batch.pipeline.load_config(old_root / entry["episode_id"] / "extraction_config.json")
    source = batch.pipeline.sources(episode_cfg)[entry["episode_id"]]
    upstream = read_json(Path(entry["reuse_artifacts"]["level1"]["path"]))
    revision["evidence"], revision["qualification"], revision["warnings"] = batch.pipeline.derive(
        "level2", revision["content"], source, [upstream])
    revision["revision_provenance"] = {"parent_sha256": sha256_file(parent), "reviewer": "synthetic fixture"}
    revised_path = path.with_name("synthetic_revised_level2.json")
    atomic_write_json(revised_path, revision, overwrite=False)
    entry["reuse_artifacts"]["level2"] = {"path": str(revised_path), "sha256": sha256_file(revised_path)}
    stale_l3 = old_root / "stages" / entry["episode_id"] / entry["episode_id"] / "level3.json"
    entry["reuse_artifacts"]["level3"] = {"path": str(stale_l3), "sha256": sha256_file(stale_l3)}
    forbidden = Mock(side_effect=AssertionError("old L3 must not be rebound to revised L2"))
    with pytest.raises(ValueError, match="incompatible"):
        batch.execute(settings, backend_factory=forbidden)
    forbidden.assert_not_called()
    del entry["reuse_artifacts"]["level3"]
    model = CandidateModel()
    report = batch.execute(settings, backend_factory=lambda *a, **k: model)
    assert report["counts"] == {"completed_candidate": 1} and model.calls == ["level3"]
    assert report["episodes"][0]["report"]["artifacts"][1]["artifact_path"] == str(revised_path)
    assert parent.read_bytes() == parent_bytes and read_json(revised_path) == revision


@pytest.mark.parametrize("fault", ["references", "raw_pin", "draft_pin"])
def test_candidate_reuse_consolidation_requires_exact_batch_config_pins(cfg, fault):
    settings, path, _, _ = candidate_reuse_batch(cfg)
    report = batch.execute(settings, backend_factory=Mock(side_effect=AssertionError("no inference")))
    config_path = Path(report["episodes"][0]["config"])
    changed = read_json(config_path)
    source = changed["episodes"][0]
    if fault == "references":
        ref = source["reuse_artifacts"]["level2"]
        alternate = path.with_name("identical_synthetic_l2.json")
        atomic_write_json(alternate, read_json(Path(ref["path"])))
        ref.update(path=str(alternate), sha256=sha256_file(alternate))
    else:
        del source["raw_sha256" if fault == "raw_pin" else "normalized_sha256"]
    atomic_write_json(config_path, changed)
    output = path.with_name("invalid_consolidation.json")
    with pytest.raises(ValueError, match="source/reuse pins differ"):
        batch.consolidation_config(settings, output, "invalid_l4")
    assert not output.exists() and read_json(config_path) == changed


def test_candidate_reuse_rechecks_reference_after_backend_construction(cfg):
    settings, _, _, _ = candidate_reuse_batch(cfg, prefix=2)
    settings["episodes"] = settings["episodes"][:1]
    backend = Mock()
    def factory(*args, **kwargs):
        path = Path(settings["episodes"][0]["reuse_artifacts"]["level2"]["path"])
        value = read_json(path)
        value["synthetic_change"] = True
        atomic_write_json(path, value)
        return backend
    result = batch.execute(settings, backend_factory=factory)
    assert result["counts"] == {"failed": 1}
    backend.complete.assert_not_called()


def test_candidate_reuse_pins_cannot_change_under_existing_batch_id(cfg):
    settings, path, _, _ = candidate_reuse_batch(cfg)
    forbidden = Mock(side_effect=AssertionError("no inference"))
    batch.execute(settings, backend_factory=forbidden)
    root = Path(settings["output_root"]) / settings["batch_id"]
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    ref = settings["episodes"][0]["reuse_artifacts"]["level2"]
    alternate = path.with_name("alternate_synthetic_l2.json")
    atomic_write_json(alternate, read_json(Path(ref["path"])))
    ref.update(path=str(alternate), sha256=sha256_file(alternate))
    with pytest.raises(ValueError, match="Batch settings changed"):
        batch.execute(settings, backend_factory=forbidden)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    forbidden.assert_not_called()
