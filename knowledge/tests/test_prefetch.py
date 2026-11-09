"""Look-ahead uses the original stage store, never a parallel corpus."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event, Lock
from unittest.mock import Mock
import fcntl

import pytest

from knowledge import batch, prefetch
from knowledge.tests.test_batch import make_candidate_batch, candidate_pipeline
from knowledge.tests.test_pipeline import cfg, FakeModel
from knowledge.tests.test_candidate_extraction import CandidateModel
from transcription.common import atomic_write_json, read_json, sha256_file


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


def refill_settings(cfg, count):
    settings, root = setup(cfg)
    template = settings["episodes"][0]
    settings["episodes"] = [{**template, "episode_id": f"SYN{i}"} for i in range(count)]
    atomic_write_json(root / "batch_manifest.json", settings)
    return settings, root


def refill(settings, **kwargs):
    return prefetch.continue_pending(settings, "rolling", continuous_refill=True,
                                    minimum_queue_gap=kwargs.pop("minimum_queue_gap", 0),
                                    sleeper=lambda _: None, **kwargs)


def synthetic_outcome(root, eid, outcome):
    if outcome in {"failed", "succeeded", "content_failure"}:
        call = ({"status": "completed"} if outcome == "succeeded" else
                {"status": "failed", "response": {} if outcome == "content_failure" else None,
                 "error_type": "ValueError" if outcome == "content_failure" else "ModelCallError"})
        atomic_write_json(root / "stages" / eid / "calls" / f"{eid}_level1_1.json", call, overwrite=False)
    status = "failed" if outcome in {"failed", "content_failure"} else "completed_candidate"
    return {"episodes": [{"episode_id": eid, "status": status}]}


def test_refill_three_slots_progress_past_sixteen_while_one_is_blocked(cfg):
    settings, root = refill_settings(cfg, 24)
    slow_started, release_slow = Event(), Event()
    wave_started = [Event() for _ in range(7)]
    release_wave = [Event() for _ in range(7)]
    wave_counts = [0] * 7
    lock = Lock()
    seen, active = [], set()
    high_water = 0

    def runner(config, ids, run_id, **kwargs):
        nonlocal high_water
        eid, = ids
        assert kwargs["workers"] == 1 and kwargs["controller_owned"]
        if eid != "SYN23":
            assert slow_started.wait(10)
        with lock:
            assert eid not in seen
            seen.append(eid)
            active.add(eid)
            high_water = max(high_water, len(active))
            wave = (len(seen) - 2) // 3
        assert any(read_json(p)["episode_id"] == eid
                   for p in (root / "controllers/rolling/admissions").glob("*.json"))
        if eid == "SYN23":
            slow_started.set()
            assert release_slow.wait(10)
        else:
            with lock:
                assert "SYN23" in active
                if wave < 7:
                    wave_counts[wave] += 1
                    if wave_counts[wave] == 3:
                        wave_started[wave].set()
            if wave < 7:
                assert release_wave[wave].wait(10)
        with lock:
            active.remove(eid)
        return synthetic_outcome(root, eid, "succeeded")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(refill, settings, runner=runner)
        try:
            for index in range(7):
                assert wave_started[index].wait(10), "All three free slots must refill"
                release_wave[index].set()
            with (root / ".prefetch_controller.lock").open("a") as lane:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lane, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            for event in release_wave:
                event.set()
            release_slow.set()
        assert future.result(timeout=10)["state"] == "tail_drained_main_queue_remains"
    assert len(seen) == len(set(seen)) == 24 and high_water == 4
    receipts = list((root / "controllers/rolling/completions").glob("*.json"))
    assert len(receipts) == 24
    never = Mock(side_effect=AssertionError("Resume must not repeat calls"))
    assert refill(settings, runner=never)["state"] == "tail_drained_main_queue_remains"
    never.assert_not_called()


@pytest.mark.parametrize("middle", ["succeeded", "content_failure", "cached"])
def test_refill_consecutive_provider_guard_not_scattered_total(cfg, middle):
    settings, root = refill_settings(cfg, 7)
    outcomes = iter(["failed", middle, "failed", middle, "failed", "succeeded", "succeeded"])
    seen = []
    def runner(config, ids, run_id, **kwargs):
        seen.extend(ids)
        return synthetic_outcome(root, ids[0], next(outcomes))
    result = refill(settings, workers=1, runner=runner)
    assert result["total_provider_failures"] == 3
    if middle == "cached":
        assert result["state"] == "needs_attention_provider_failures"
        assert result["consecutive_provider_failures"] == 3 and len(seen) == 5
    else:
        assert result["state"] == "tail_drained_main_queue_remains"
        assert result["consecutive_provider_failures"] == 0 and len(seen) == 7
    # Receipts, not a mutable status file or later episode retries, govern resume.
    (root / "controllers/rolling/status.json").unlink()
    for eid in seen:
        atomic_write_json(root / "stages" / eid / "calls" / f"{eid}_level2_2.json", {"status": "completed"})
    again = refill(settings, workers=1, runner=Mock(side_effect=AssertionError("No repeats")))
    assert again["state"] == result["state"]
    assert again["total_provider_failures"] == result["total_provider_failures"]


def test_refill_drain_finishes_inflight_without_more_admissions(cfg):
    settings, root = refill_settings(cfg, 12)
    all_started, release = Event(), Event()
    seen, lock = [], Lock()
    def runner(config, ids, run_id, **kwargs):
        with lock:
            seen.extend(ids)
            if len(seen) == 4:
                all_started.set()
        assert release.wait(10)
        return synthetic_outcome(root, ids[0], "succeeded")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(refill, settings, runner=runner)
        try:
            assert all_started.wait(10)
            prefetch.request_drain(settings, "rolling")
            assert not future.done()
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result["state"] == "drained" and len(seen) == 4
    assert len(list((root / "controllers/rolling/completions").glob("*.json"))) == 4
    assert refill(settings, runner=Mock(side_effect=AssertionError("Sticky drain")))["state"] == "drained"
    # A new controller imports completed trial inventory in production; no marker
    # removal or call cancellation is part of the drain operation.


def test_refill_preserves_admission_interrupted_before_trial_plan(cfg):
    settings, root = setup(cfg)
    atomic_write_json(root / "controllers/rolling/admissions/000001.json",
                      {"episode_id": "SYN2", "trial_id": "rolling_refill_000001"})
    runner = Mock(side_effect=AssertionError("Unknown outcome requires inspection"))
    result = refill(settings, runner=runner)
    assert result["state"] == "needs_attention_interrupted_admissions"
    assert result["interrupted_episode_ids"] == ["SYN2"]
    runner.assert_not_called()


def test_refill_rechecks_queue_gap_after_main_advances(cfg):
    settings, root = refill_settings(cfg, 8)
    seen = []
    def runner(config, ids, run_id, **kwargs):
        seen.extend(ids)
        for eid in ("SYN0", "SYN1", "SYN2", "SYN3"):
            atomic_write_json(root / eid / "status.json", {"status": "running"})
        return synthetic_outcome(root, ids[0], "succeeded")
    result = refill(settings, workers=1, minimum_queue_gap=3, runner=runner)
    assert seen == ["SYN7"]
    assert result["state"] == "tail_drained_main_queue_remains"


@pytest.mark.parametrize("claimed,status", [("SYN2", "skipped_main_queue"),
                                           ("SYN1", "deferred_queue_near")])
def test_refill_worker_rechecks_main_queue_after_durable_admission(cfg, monkeypatch, claimed, status):
    settings, root = setup(cfg)
    original = prefetch.atomic_write_json
    def write(path, payload, **kwargs):
        result = original(path, payload, **kwargs)
        if path.parent.name == "admissions":
            original(root / claimed / "status.json", {"status": "running"})
        return result
    monkeypatch.setattr(prefetch, "atomic_write_json", write)
    runner = Mock(side_effect=AssertionError("Admission became unsafe before worker execution"))
    assert refill(settings, minimum_queue_gap=1, runner=runner)["state"] == "tail_drained_main_queue_remains"
    runner.assert_not_called()
    assert read_json(root / "controllers/rolling/completions/000001.json")["status"] == status


def test_refill_real_trials_reuse_stages_without_repeated_model_calls(cfg):
    settings, root = setup(cfg)
    model = CandidateModel()
    def runner(*args, **kwargs):
        return prefetch.execute(*args, **kwargs, backend_factory=lambda *a, **kw: model)
    assert refill(settings, runner=runner)["state"] == "tail_drained_main_queue_remains"
    assert len(model.calls) == 6
    assert refill(settings, runner=runner)["state"] == "tail_drained_main_queue_remains"
    # Also prove a fresh controller and the main scheduler reuse the same store.
    assert prefetch.continue_pending(settings, "new", continuous_refill=True,
                                    minimum_queue_gap=0, runner=runner)["state"] == "tail_drained_main_queue_remains"
    assert batch.execute(settings, backend_factory=lambda *a, **kw: model)["counts"] == {"completed_candidate": 2}
    assert len(model.calls) == 6


def test_chunk_controller_honors_drain_before_next_chunk(cfg):
    settings, root = refill_settings(cfg, 20)
    seen = []
    def runner(config, ids, run_id, **kwargs):
        seen.extend(ids)
        prefetch.request_drain(settings, "chunked")
        return {"episodes": [{"episode_id": eid, "status": "completed_candidate"} for eid in ids]}
    result = prefetch.continue_pending(settings, "chunked", minimum_queue_gap=0, runner=runner)
    assert result["state"] == "drained" and len(seen) == 16


def test_refill_requires_new_identity_when_switching_from_chunks(cfg):
    settings, root = setup(cfg)
    atomic_write_json(root / "controllers/rolling/manifest.json", {
        "schema_version": "knowledge_continuation.v1", "batch_config": settings,
        "workers": 4, "minimum_queue_gap": 0})
    with pytest.raises(ValueError, match="new run ID"):
        refill(settings, runner=Mock())


def test_refill_provider_trip_drains_other_calls_and_remains_latched(cfg, monkeypatch):
    settings, root = refill_settings(cfg, 12)
    releases = [Event() for _ in range(3)]
    accounted = [Event() for _ in range(3)]
    finish_successes, initial_started = Event(), Event()
    seen, lock = [], Lock()
    original = prefetch.atomic_write_json
    def write(path, payload, **kwargs):
        result = original(path, payload, **kwargs)
        if path == root / "controllers/rolling/status.json":
            streak = payload.get("consecutive_provider_failures", 0)
            if 1 <= streak <= 3:
                accounted[streak - 1].set()
        return result
    monkeypatch.setattr(prefetch, "atomic_write_json", write)
    def runner(config, ids, run_id, **kwargs):
        eid, = ids
        with lock:
            seen.append(eid)
            if len(seen) == 4:
                initial_started.set()
        if eid in {"SYN11", "SYN10", "SYN9"}:
            assert releases[11 - int(eid[3:])].wait(10)
            return synthetic_outcome(root, eid, "failed")
        assert finish_successes.wait(10)
        return synthetic_outcome(root, eid, "succeeded")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(refill, settings, runner=runner)
        try:
            assert initial_started.wait(10)
            for index in range(3):
                releases[index].set()
                assert accounted[index].wait(10)
            admitted_at_trip = len(list((root / "controllers/rolling/admissions").glob("*.json")))
            assert not future.done()
        finally:
            for event in releases:
                event.set()
            finish_successes.set()
        result = future.result(timeout=10)
    assert len(seen) == admitted_at_trip <= 6
    assert result["state"] == "needs_attention_provider_failures"
    assert result["consecutive_provider_failures"] == 0
    assert result["provider_failure_limit_reached"]
    assert refill(settings, runner=Mock(side_effect=AssertionError("Trip stays latched")))["state"] == result["state"]


def test_refill_preserves_per_episode_lock_collision(cfg):
    settings, root = setup(cfg)
    stage = root / "stages/SYN2"
    stage.mkdir(parents=True)
    model = CandidateModel()
    def runner(*args, **kwargs):
        return prefetch.execute(*args, **kwargs, backend_factory=lambda *a, **kw: model)
    with (stage / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = refill(settings, runner=runner)
    assert result["state"] == "tail_drained_main_queue_remains"
    assert len(model.calls) == 3
    rows = [read_json(p) for p in (root / "controllers/rolling/completions").glob("*.json")]
    assert next(row for row in rows if row["episode_id"] == "SYN2")["status"] == "skipped_busy"
    assert refill(settings, runner=runner)["state"] == result["state"]
    assert len(model.calls) == 3


BURST_UNTIL = "2030-01-01T00:00:00+00:00"
BURST_EPOCH = datetime.fromisoformat(BURST_UNTIL).timestamp()


@pytest.mark.parametrize("extra,until", [(True, BURST_UNTIL), (-1, BURST_UNTIL), (5, BURST_UNTIL),
                                       (1, None), (0, BURST_UNTIL), (1, "2030-01-01T00:00:00"),
                                       (1, "2030-01-01T00:00:00-08:00"), (1, "not-a-date")])
def test_burst_requires_bounded_workers_and_explicit_utc_deadline(cfg, extra, until):
    settings, root = setup(cfg)
    with pytest.raises(ValueError):
        refill(settings, extra_workers=extra, burst_until=until)
    assert not (root / "controllers").exists()


def test_chunk_and_standalone_worker_limits_remain_unchanged(cfg):
    settings, _ = setup(cfg)
    with pytest.raises(ValueError, match="continuous refill"):
        prefetch.continue_pending(settings, "chunk", extra_workers=1, burst_until=BURST_UNTIL)
    with pytest.raises(ValueError, match="1-4"):
        refill(settings, workers=5, extra_workers=1, burst_until=BURST_UNTIL)


@pytest.mark.parametrize("now", [BURST_EPOCH, BURST_EPOCH + 1])
def test_burst_expired_before_start_uses_only_baseline_and_stays_expired(cfg, now):
    settings, root = refill_settings(cfg, 6)
    started, release, lock = Event(), Event(), Lock()
    active, seen = set(), []
    high_water = 0
    def runner(config, ids, run_id, **kwargs):
        nonlocal high_water
        eid, = ids
        with lock:
            active.add(eid)
            seen.append(eid)
            high_water = max(high_water, len(active))
            if len(active) == 2:
                started.set()
        assert release.wait(10)
        with lock:
            active.remove(eid)
        return synthetic_outcome(root, eid, "succeeded")
    options = dict(workers=2, extra_workers=2, burst_until=BURST_UNTIL)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(refill, settings, runner=runner, clock=lambda: now, **options)
        try:
            assert started.wait(10)
            assert len(list((root / "controllers/rolling/admissions").glob("*.json"))) == 2
        finally:
            release.set()
        result = future.result(timeout=10)
    assert high_water == 2 and len(seen) == len(set(seen)) == 6
    assert result["burst_expired"] and result["admission_worker_budget"] == 2
    # A backwards wall clock on resume must not reactivate an expired burst.
    again = refill(settings, runner=Mock(side_effect=AssertionError("No repeated calls")),
                   clock=lambda: BURST_EPOCH - 100, **options)
    assert again["burst_expired"] and again["admission_worker_budget"] == 2


def test_burst_deadline_drains_inflight_then_continues_baseline(cfg, monkeypatch):
    settings, root = refill_settings(cfg, 8)
    now = [BURST_EPOCH - 10]
    first = ["SYN7", "SYN6", "SYN5"]
    releases = {eid: Event() for eid in first}
    accounted = {eid: Event() for eid in first}
    all_started, baseline_started, finish = Event(), Event(), Event()
    lock, active, seen = Lock(), set(), []
    original = prefetch.atomic_write_json
    def write(path, payload, **kwargs):
        result = original(path, payload, **kwargs)
        if path.parent.name == "completions" and payload["episode_id"] in accounted:
            accounted[payload["episode_id"]].set()
        return result
    monkeypatch.setattr(prefetch, "atomic_write_json", write)
    def runner(config, ids, run_id, **kwargs):
        eid, = ids
        with lock:
            assert eid not in seen
            seen.append(eid)
            active.add(eid)
            if len(seen) == 3:
                all_started.set()
            if eid not in first:
                assert now[0] >= BURST_EPOCH and len(active) == 1
                baseline_started.set()
        assert (releases[eid] if eid in releases else finish).wait(10)
        with lock:
            active.remove(eid)
        return synthetic_outcome(root, eid, "succeeded")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(refill, settings, workers=1, extra_workers=2, burst_until=BURST_UNTIL,
                             clock=lambda: now[0], runner=runner)
        try:
            assert all_started.wait(10)
            now[0] = BURST_EPOCH
            for eid in first[:2]:
                releases[eid].set()
                assert accounted[eid].wait(10)
                assert len(list((root / "controllers/rolling/admissions").glob("*.json"))) == 3
            assert not baseline_started.is_set()
            releases[first[2]].set()
            assert baseline_started.wait(10)
        finally:
            for event in releases.values():
                event.set()
            finish.set()
        result = future.result(timeout=10)
    assert result["state"] == "tail_drained_main_queue_remains"
    assert result["failed_episode_ids"] == []
    assert result["burst_expired"] and result["admission_worker_budget"] == 1
    assert len(seen) == len(set(seen)) == 8


def test_burst_expiry_is_rechecked_between_admissions(cfg, monkeypatch):
    settings, root = refill_settings(cfg, 5)
    now = [BURST_EPOCH - 1]
    original = prefetch.atomic_write_json
    def write(path, payload, **kwargs):
        result = original(path, payload, **kwargs)
        if path.parent.name == "admissions":
            now[0] = BURST_EPOCH
        return result
    monkeypatch.setattr(prefetch, "atomic_write_json", write)
    def runner(config, ids, run_id, **kwargs):
        admissions = list((root / "controllers/rolling/admissions").glob("*.json"))
        completions = list((root / "controllers/rolling/completions").glob("*.json"))
        assert len(admissions) - len(completions) == 1
        return synthetic_outcome(root, ids[0], "succeeded")
    result = refill(settings, workers=1, extra_workers=4, burst_until=BURST_UNTIL,
                    clock=lambda: now[0], runner=runner)
    assert result["burst_expired"] and result["failed_episode_ids"] == []


def test_burst_options_are_pinned_and_same_deadline_is_canonical(cfg):
    settings, root = setup(cfg)
    def runner(config, ids, run_id, **kwargs):
        return synthetic_outcome(root, ids[0], "succeeded")
    options = dict(workers=1, extra_workers=2, burst_until=BURST_UNTIL, clock=lambda: BURST_EPOCH - 1)
    result = refill(settings, runner=runner, **options)
    assert result["admission_worker_budget"] == 3 and not result["burst_expired"]
    identity = read_json(root / "controllers/rolling/manifest.json")
    assert identity["burst"] == {"extra_workers": 2, "admission_deadline_utc": BURST_UNTIL}
    assert refill(settings, runner=Mock(side_effect=AssertionError("No repeats")),
                  **{**options, "burst_until": "2030-01-01T00:00:00Z"})["state"] == result["state"]
    for change in ({"extra_workers": 1}, {"burst_until": "2030-01-01T00:00:01Z"}):
        with pytest.raises(ValueError, match="new run ID"):
            refill(settings, **{**options, **change})


def test_burst_keeps_exclusive_controller_lock(cfg):
    settings, root = setup(cfg)
    runner = Mock(side_effect=AssertionError("Do not bypass the existing controller"))
    with (root / ".prefetch_controller.lock").open("a") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            refill(settings, extra_workers=4, burst_until=BURST_UNTIL, runner=runner)
    runner.assert_not_called()


def test_maximum_burst_has_eight_distinct_active_jobs(cfg):
    settings, root = refill_settings(cfg, 10)
    started, release, lock = Event(), Event(), Lock()
    active, seen = set(), []
    high_water = 0
    def runner(config, ids, run_id, **kwargs):
        nonlocal high_water
        eid, = ids
        with lock:
            assert eid not in seen
            seen.append(eid)
            active.add(eid)
            high_water = max(high_water, len(active))
            if len(active) == 8:
                started.set()
        assert release.wait(10)
        with lock:
            active.remove(eid)
        return synthetic_outcome(root, eid, "succeeded")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(refill, settings, workers=4, extra_workers=4, burst_until=BURST_UNTIL,
                             clock=lambda: BURST_EPOCH - 1, runner=runner)
        try:
            assert started.wait(10)
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result["admission_worker_budget"] == 8 and result["failed_episode_ids"] == []
    assert high_water == 8 and len(seen) == len(set(seen)) == 10


@pytest.mark.parametrize("callback,pin", [(True, "a" * 64), (lambda: True, None),
                                         (None, "a" * 64), (lambda: True, "bad")])
def test_burst_guard_requires_callback_and_identity(cfg, callback, pin):
    settings, _ = setup(cfg)
    with pytest.raises(ValueError, match="burst guard"):
        refill(settings, extra_workers=1, burst_until=BURST_UNTIL,
               burst_allowed=callback, burst_guard_sha256=pin)


@pytest.mark.parametrize("fails", [False, True])
def test_burst_guard_holds_only_extra_and_records_reason(cfg, fails):
    settings, root = refill_settings(cfg, 5)
    def guard():
        if fails:
            raise OSError("Synthetic unavailable monitor")
        return False
    def runner(config, ids, run_id, **kwargs):
        admissions = list((root / "controllers/rolling/admissions").glob("*.json"))
        completions = list((root / "controllers/rolling/completions").glob("*.json"))
        assert len(admissions) - len(completions) == 1
        return synthetic_outcome(root, ids[0], "succeeded")
    result = refill(settings, workers=1, extra_workers=4, burst_until=BURST_UNTIL,
                    clock=lambda: BURST_EPOCH - 1, runner=runner,
                    burst_allowed=guard, burst_guard_sha256="a" * 64)
    assert result["failed_episode_ids"] == []
    assert result["admission_worker_budget"] == 1 and not result["burst_expired"]
    assert result["burst_admission_reason"] == ("callback_error" if fails else "resource_hold")
    assert result["burst_guard_error_type"] == ("OSError" if fails else None)
    with pytest.raises(ValueError, match="new run ID"):
        refill(settings, workers=1, extra_workers=4, burst_until=BURST_UNTIL,
               burst_allowed=guard, burst_guard_sha256="b" * 64)


def test_burst_guard_recovery_can_refill_before_completion_but_not_after_deadline(cfg, monkeypatch):
    settings, root = refill_settings(cfg, 9)
    allowed, now = [True], [BURST_EPOCH - 1]
    releases = {f"SYN{i}": Event() for i in range(9)}
    accounted = {eid: Event() for eid in releases}
    first_wave, second_wave = Event(), Event()
    seen, lock = [], Lock()
    original = prefetch.atomic_write_json
    def write(path, payload, **kwargs):
        result = original(path, payload, **kwargs)
        if path.parent.name == "completions":
            accounted[payload["episode_id"]].set()
        return result
    monkeypatch.setattr(prefetch, "atomic_write_json", write)
    def runner(config, ids, run_id, **kwargs):
        eid, = ids
        with lock:
            assert eid not in seen
            seen.append(eid)
            if len(seen) == 3:
                first_wave.set()
            if len(seen) == 5:
                second_wave.set()
        assert releases[eid].wait(10)
        return synthetic_outcome(root, eid, "succeeded")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(refill, settings, workers=1, extra_workers=2,
                             burst_until=BURST_UNTIL, clock=lambda: now[0],
                             burst_allowed=lambda: allowed[0], burst_guard_sha256="a" * 64,
                             poll_seconds=0.01, runner=runner)
        try:
            assert first_wave.wait(10)
            allowed[0] = False
            for eid in ("SYN8", "SYN7"):
                releases[eid].set()
                assert accounted[eid].wait(10)
            assert len(seen) == 3
            allowed[0] = True
            # SYN6 is still blocked: polling alone must permit recovered extra slots.
            assert second_wave.wait(10)
            now[0] = BURST_EPOCH
            for eid in ("SYN5", "SYN4"):
                releases[eid].set()
                assert accounted[eid].wait(10)
            assert len(seen) == 5
        finally:
            for event in releases.values():
                event.set()
        result = future.result(timeout=10)
    assert len(seen) == len(set(seen)) == 9
    assert result["failed_episode_ids"] == []
    assert result["burst_expired"] and result["admission_worker_budget"] == 1
    assert result["burst_admission_reason"] == "deadline_expired"


def test_expired_burst_never_invokes_guard(cfg):
    settings, root = setup(cfg)
    guard = Mock(side_effect=AssertionError("Expired burst must stay expired"))
    result = refill(settings, workers=1, extra_workers=1, burst_until=BURST_UNTIL,
                    clock=lambda: BURST_EPOCH,
                    burst_allowed=guard, burst_guard_sha256="a" * 64,
                    runner=lambda config, ids, run_id, **kwargs:
                    synthetic_outcome(root, ids[0], "succeeded"))
    assert result["burst_expired"]
    guard.assert_not_called()


def test_burst_rechecks_deadline_after_guard_returns(cfg):
    settings, root = refill_settings(cfg, 4)
    now = [BURST_EPOCH - 1]
    def guard():
        now[0] = BURST_EPOCH
        return True
    def runner(config, ids, run_id, **kwargs):
        admissions = list((root / "controllers/rolling/admissions").glob("*.json"))
        completions = list((root / "controllers/rolling/completions").glob("*.json"))
        assert len(admissions) - len(completions) == 1
        return synthetic_outcome(root, ids[0], "succeeded")
    result = refill(settings, workers=1, extra_workers=3, burst_until=BURST_UNTIL,
                    clock=lambda: now[0], burst_allowed=guard, burst_guard_sha256="a" * 64,
                    runner=runner)
    assert result["failed_episode_ids"] == []
    assert result["burst_expired"] and result["admission_worker_budget"] == 1
    assert result["burst_admission_reason"] == "deadline_expired"
