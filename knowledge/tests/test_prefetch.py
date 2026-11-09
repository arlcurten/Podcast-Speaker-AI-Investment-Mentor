"""Look-ahead uses the original stage store, never a parallel corpus."""
from pathlib import Path
from unittest.mock import Mock
import fcntl

import pytest

from knowledge import batch, prefetch
from knowledge.tests.test_batch import make_candidate_batch, candidate_pipeline
from knowledge.tests.test_pipeline import cfg, FakeModel
from knowledge.tests.test_candidate_extraction import CandidateModel
from transcription.common import atomic_write_json, sha256_file


def setup(cfg):
    settings, _ = make_candidate_batch(cfg)
    root = Path(settings["output_root"]) / settings["batch_id"]
    atomic_write_json(root / "batch_manifest.json", settings, overwrite=False)
    return settings, root


def test_plan_does_not_create_jobs_or_call_model(cfg):
    settings, root = setup(cfg)
    factory = Mock(side_effect=AssertionError("No inference"))
    result = prefetch.execute(settings, ["SYN2"], "trial", workers=4, dry_run=True, backend_factory=factory)
    assert result["model_calls"] == 0
    assert not (root / "accelerations").exists()
    assert not (root / "SYN2").exists()
    factory.assert_not_called()


@pytest.mark.parametrize("ids,workers", [([], 1), (["SYN1", "SYN1"], 1), (["UNKNOWN"], 1),
                                       (["SYN1"], 0), (["SYN1"], 5), (["SYN1"], True)])
def test_invalid_trial_rejected(cfg, ids, workers):
    settings, _ = setup(cfg)
    with pytest.raises(ValueError):
        prefetch.execute(settings, ids, "trial", workers=workers, dry_run=True)


def test_does_not_admit_main_queue_work_or_changed_config(cfg):
    settings, root = setup(cfg)
    atomic_write_json(root / "SYN1/status.json", {"status": "running"})
    with pytest.raises(ValueError, match="unstarted"):
        prefetch.execute(settings, ["SYN1"], "trial", dry_run=True)
    settings["model"] = "changed"
    with pytest.raises(ValueError, match="manifest"):
        prefetch.execute(settings, ["SYN2"], "trial", dry_run=True)


def test_main_queue_reuses_prefetched_artifacts_without_model_calls(cfg):
    settings, root = setup(cfg)
    before = sha256_file(root / "batch_manifest.json")
    model = CandidateModel()
    result = prefetch.execute(settings, ["SYN1", "SYN2"], "trial", workers=2,
                              backend_factory=lambda *a, **kw: model)
    assert all(r["status"] == "completed_candidate" for r in result["episodes"])
    assert len(model.calls) == 6
    assert not (root / "summary.json").exists()
    assert not (root / "SYN1/status.json").exists()
    assert sha256_file(root / "batch_manifest.json") == before
    follow = CandidateModel()
    result = batch.execute(settings, backend_factory=lambda *a, **kw: follow)
    assert result["counts"] == {"completed_candidate": 2}
    assert follow.calls == []
    with pytest.raises(ValueError, match="trial ID"):
        prefetch.execute(settings, ["SYN1"], "trial")


def test_pipeline_lock_prevents_double_generation(cfg):
    settings, root = setup(cfg)
    folder = root / "stages/SYN1"
    folder.mkdir(parents=True)
    model = FakeModel()
    with (folder / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = prefetch.execute(settings, ["SYN1"], "trial", backend_factory=lambda *a, **kw: model)
    assert result["episodes"][0]["status"] == "skipped_busy"
    assert model.calls == []


def test_failure_isolation_and_circuit(cfg, monkeypatch, candidate_pipeline):
    settings, root = setup(cfg)
    settings["max_consecutive_failures"] = 1
    atomic_write_json(root / "batch_manifest.json", settings)
    candidate_pipeline[1].side_effect = ValueError("Synthetic failure")
    result = prefetch.execute(settings, ["SYN1", "SYN2"], "trial", workers=1,
                              backend_factory=Mock(return_value=object()))
    assert [r["status"] for r in result["episodes"]] == ["failed", "deferred"]
    assert result["failure_limit_reached"]
    assert not (root / "SYN2/extraction_config.json").exists()


def test_reverse_lock_collision_is_deferred_not_failed(cfg):
    settings, root = setup(cfg)
    settings["max_consecutive_failures"] = 1
    atomic_write_json(root / "batch_manifest.json", settings)
    folder = root / "stages/SYN1"
    folder.mkdir(parents=True)
    model = CandidateModel()
    with (folder / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = batch.execute(settings, backend_factory=lambda *a, **kw: model)
    assert result["counts"] == {"deferred": 1, "completed_candidate": 1}
    assert result["episodes"][0]["action"] == "episode_busy"
    assert not result["failure_limit_reached"]
    resumed = batch.execute(settings, backend_factory=lambda *a, **kw: model)
    assert resumed["counts"] == {"completed_candidate": 2}
    assert len(model.calls) == 6


def test_episode_symlink_cannot_escape_private_batch(cfg, tmp_path):
    settings, root = setup(cfg)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "SYN1").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        prefetch.execute(settings, ["SYN1"], "trial", dry_run=True)
    assert list(outside.iterdir()) == []


def test_queue_distance_guard(cfg):
    settings, root = setup(cfg)
    prefetch.execute(settings, ["SYN2"], "trial", dry_run=True, minimum_queue_gap=1)
    atomic_write_json(root / "SYN1/status.json", {"status": "completed_candidate"})
    with pytest.raises(ValueError, match="too close"):
        prefetch.execute(settings, ["SYN2"], "trial", dry_run=True, minimum_queue_gap=1)


@pytest.mark.parametrize("different", [False, True])
def test_main_config_creation_race_compares_existing_content(cfg, monkeypatch, candidate_pipeline, different):
    settings, _ = setup(cfg)
    original = batch.atomic_write_json
    def race(path, payload, **kwargs):
        if path.name == "extraction_config.json" and not path.exists():
            original(path, {**payload, **({"model": "different"} if different else {})}, overwrite=False)
        return original(path, payload, **kwargs)
    monkeypatch.setattr(batch, "atomic_write_json", race)
    result = batch.execute(settings, backend_factory=Mock(return_value=object()))
    assert result["counts"] == ({"failed": 2} if different else {"completed_candidate": 2})


def test_continuation_reuses_existing_success_and_drains_pending(cfg):
    settings, root = setup(cfg)
    model = CandidateModel()
    prefetch.execute(settings, ["SYN1"], "first", backend_factory=lambda *a, **kw: model)
    def runner(*args, **kwargs):
        return prefetch.execute(*args, **kwargs, backend_factory=lambda *a, **kw: model)
    result = prefetch.continue_pending(settings, "controller", minimum_queue_gap=0, runner=runner)
    assert result["state"] == "tail_drained_main_queue_remains"
    assert len(model.calls) == 6
    again = prefetch.continue_pending(settings, "controller", minimum_queue_gap=0, runner=runner)
    assert again["state"] == result["state"] and len(model.calls) == 6


def test_continuation_waits_for_live_trial_without_more_concurrency(cfg):
    settings, root = setup(cfg)
    folder = root / "accelerations/live"
    atomic_write_json(folder / "plan.json", {"episode_ids": ["SYN1", "SYN2"]})
    with (folder / ".trial.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def sleeper(seconds):
            atomic_write_json(folder / "summary.json", {"episodes": [
                {"episode_id": eid, "status": "completed_candidate"} for eid in ["SYN1", "SYN2"]]})
            fcntl.flock(lock, fcntl.LOCK_UN)
        runner = Mock(side_effect=AssertionError("Do not duplicate existing work"))
        result = prefetch.continue_pending(settings, "controller", minimum_queue_gap=0,
                                           runner=runner, sleeper=sleeper)
    assert result["state"] == "tail_drained_main_queue_remains"
    runner.assert_not_called()


def test_continuation_preserves_interrupted_trial(cfg):
    settings, root = setup(cfg)
    atomic_write_json(root / "accelerations/interrupted/plan.json", {"episode_ids": ["SYN1", "SYN2"]})
    result = prefetch.continue_pending(settings, "controller", minimum_queue_gap=0,
                                       runner=Mock(side_effect=AssertionError("Preserve interrupted work")))
    assert result["interrupted_episode_ids"] == ["SYN1", "SYN2"]


def test_manual_trial_cannot_add_workers_while_controller_owns_lane(cfg):
    settings, root = setup(cfg)
    with (root / ".prefetch_controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="controller"):
            prefetch.execute(settings, ["SYN1"], "extra", dry_run=True)


def test_continuation_isolates_content_failure_and_continues(cfg):
    settings, root = setup(cfg)
    calls = []
    def runner(config, ids, run_id, **kwargs):
        calls.append(ids)
        rows = [{"episode_id": ids[0], "status": "failed"}]
        if len(ids) > 1:
            rows += [{"episode_id": ids[1], "status": "deferred"}]
        folder = root / "accelerations" / run_id
        atomic_write_json(folder / "plan.json", {"episode_ids": ids})
        atomic_write_json(folder / "summary.json", {"episodes": rows})
        return {"episodes": rows}
    result = prefetch.continue_pending(settings, "controller", minimum_queue_gap=0, runner=runner)
    assert len(calls) == 2
    assert result["failed_episode_ids"] == ["SYN1", "SYN2"]


def test_continuation_provider_failure_stop_is_visible(cfg):
    settings, root = setup(cfg)
    folder = root / "controllers/controller"
    atomic_write_json(folder / "status.json", {"provider_failure_count": 2})
    def runner(config, ids, run_id, **kwargs):
        eid = ids[0]
        atomic_write_json(root / "stages" / eid / "calls" / f"{eid}_level1_1.json",
                          {"status": "failed", "response": None, "error_type": "ModelCallError"})
        return {"episodes": [{"episode_id": eid, "status": "failed"},
                             {"episode_id": ids[1], "status": "completed_candidate"}]}
    result = prefetch.continue_pending(settings, "controller", minimum_queue_gap=0, runner=runner)
    assert result["state"] == "needs_attention_provider_failures"
    assert result["provider_failure_count"] == 3
    with pytest.raises(ValueError, match="provider"):
        prefetch.continue_pending(settings, "controller", minimum_queue_gap=0, runner=runner)


def test_continuation_chunk_size_is_not_a_total_work_limit(cfg):
    settings, root = setup(cfg)
    template = settings["episodes"][0]
    settings["episodes"] = [{**template, "episode_id": f"SYN{i}"} for i in range(40)]
    atomic_write_json(root / "batch_manifest.json", settings)
    sizes = []
    def runner(config, ids, run_id, **kwargs):
        sizes.append(len(ids))
        rows = [{"episode_id": eid, "status": "completed_candidate"} for eid in ids]
        folder = root / "accelerations" / run_id
        atomic_write_json(folder / "plan.json", {"episode_ids": ids})
        atomic_write_json(folder / "summary.json", {"episodes": rows})
        return {"episodes": rows}
    result = prefetch.continue_pending(settings, "controller", minimum_queue_gap=0, runner=runner)
    assert sizes == [16, 16, 8]
    assert result["state"] == "tail_drained_main_queue_remains"


def test_standalone_holds_lane_lock_before_publishing_plan(cfg, monkeypatch):
    settings, root = setup(cfg)
    def inspect(*args, **kwargs):
        with (root / ".prefetch_controller.lock").open("a") as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return {"locked": True}
    monkeypatch.setattr(prefetch, "_execute", inspect)
    assert prefetch.execute(settings, ["SYN1"], "trial") == {"locked": True}


def test_provider_failures_reconstructed_after_lost_controller_checkpoint(cfg):
    settings, root = setup(cfg)
    rows = []
    for eid in ("SYN1", "SYN2", "SYN3"):
        rows.append({"episode_id": eid, "status": "failed"})
        atomic_write_json(root / "stages" / eid / "calls" / f"{eid}_level1_1.json",
                          {"status": "failed", "response": None, "error_type": "ModelCallError"})
    atomic_write_json(root / "accelerations/controller_0001/summary.json", {"episodes": rows})
    runner = Mock(side_effect=AssertionError("Provider failures must survive restart"))
    result = prefetch.continue_pending(settings, "controller", minimum_queue_gap=0, runner=runner)
    assert result["state"] == "needs_attention_provider_failures"
    assert result["provider_failure_count"] == 3
    runner.assert_not_called()
