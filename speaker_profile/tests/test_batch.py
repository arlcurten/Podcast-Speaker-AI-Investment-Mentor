"""Synthetic evidence-only batches. No live provider or corpus is accessed."""
import copy
import fcntl
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge.codex_backend import ModelCallError
from speaker_profile import batch, pipeline
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_pipeline import SyntheticModel, cfg


@pytest.fixture(autouse=True)
def no_live_backend(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Live inference is forbidden in batch tests"))
    monkeypatch.setattr(batch, "CodexBackend", forbidden)
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    return forbidden


@pytest.fixture
def settings(cfg):
    return batch._config({
        "schema_version": "profile_batch.v1", "batch_id": "synthetic_evidence_batch",
        "output_root": cfg["output_root"], "model": "synthetic", "reasoning_effort": "high",
        "timeout_seconds": 20, "max_input_chars": 100000, "retry_count": 0,
        "max_provider_failures": 3, "reserved_episode_ids": cfg["reserved_episode_ids"],
        "excluded_episode_ids": [], "episodes": cfg["episodes"],
    })


def root(settings):
    return Path(settings["output_root"]) / settings["batch_id"]


def snapshot(path):
    return {p: p.read_bytes() for p in path.rglob("*") if p.is_file()}


def source_snapshot(settings):
    return {Path(e[k]): Path(e[k]).read_bytes() for e in settings["episodes"] for k in ("raw", "draft")}


def factory(models):
    def make(*args, **kwargs):
        model = SyntheticModel()
        models.append(model)
        return model
    return make


def test_plan_is_read_only_and_uses_zero_models(settings, no_live_backend):
    before = source_snapshot(settings)
    plan = batch.execute(settings, workers=12)
    assert plan["model_calls"] == 0 and plan["estimated_new_calls"] == 4
    assert plan["counts"] == {"pending": 4}
    assert plan["through"] == "evidence" and plan["synthesis"] == "not_run"
    assert not root(settings).exists()
    assert source_snapshot(settings) == before
    no_live_backend.assert_not_called()


def test_end_to_end_resume_validate_and_status_never_reextract(settings, no_live_backend):
    before = source_snapshot(settings)
    models = []
    result = batch.execute(settings, command="run", workers=8, backend_factory=factory(models))
    assert result["state"] == "completed_candidate" and result["counts"] == {"completed_candidate": 4}
    assert result["model_calls"] == 4 and all(m.calls == ["evidence"] for m in models)
    assert not list(root(settings).rglob("synthesis.json"))
    assert source_snapshot(settings) == before
    for row in result["episodes"]:
        config = read_json(Path(row["config"]["path"]))
        assert config["max_calls"] == 1 and len(config["episodes"]) == 1
        assert row["artifact"]["sha256"] == sha256_file(Path(row["artifact"]["path"]))
        artifact = read_json(Path(row["artifact"]["path"]))
        assert artifact["stage"] == "evidence" and artifact["policy"] == pipeline.POLICY
    retained = snapshot(root(settings))
    for command in ("plan", "validate", "status"):
        checked = batch.execute(settings, command=command, workers=5)
        assert checked["model_calls"] == 0 and checked["state"] == "completed_candidate"
        assert snapshot(root(settings)) == retained
    follow = batch.execute(settings, command="run", workers=9)
    assert follow["model_calls"] == 0 and follow["state"] == "completed_candidate"
    assert {p: p.read_bytes() for p in retained if p.name != "summary.json"} == {
        p: v for p, v in retained.items() if p.name != "summary.json"}
    no_live_backend.assert_not_called()


def test_exclusions_never_load_or_extract_pilot_sources(settings, monkeypatch):
    settings["excluded_episode_ids"] = ["SYN1", "SYN2", "SYNTHETIC_PILOT_NOT_IN_LIST"]
    for entry in settings["episodes"][:2]:
        entry["raw"] = str(Path(settings["output_root"]).parent / (entry["episode_id"] + "-not-loaded.json"))
    original = pipeline.run
    seen = []
    def inspect(config, **kwargs):
        seen.append(config["episodes"][0]["episode_id"])
        assert kwargs["through"] == "evidence"
        return original(config, **kwargs)
    monkeypatch.setattr(pipeline, "run", inspect)
    models = []
    result = batch.execute(settings, command="run", workers=4, backend_factory=factory(models))
    assert set(seen) == {"SYN3", "SYN4"} and len(models) == 2
    assert result["excluded_episode_ids"] == settings["excluded_episode_ids"]
    assert not (root(settings) / "episodes/SYN1").exists()
    assert not (root(settings) / "episodes/SYN2").exists()


@pytest.mark.parametrize("workers", [0, -1, True, 1.5, "4"])
def test_invalid_workers_rejected_without_writes(settings, workers):
    with pytest.raises(ValueError, match="workers"):
        batch.execute(settings, command="run", workers=workers)
    assert not root(settings).exists()


@pytest.mark.parametrize("fault", ["unknown_input", "reuse_pilot", "reserved", "duplicate", "nonempty_findings",
                                  "invalid_hash", "public_output", "nested_source", "failure_limit"])
def test_bad_config_rejected_before_output(settings, fault):
    if fault == "unknown_input": settings["knowledge_cases"] = "forbidden"
    elif fault == "reuse_pilot": settings["reuse_evidence"] = {}
    elif fault == "reserved": settings["reserved_episode_ids"].append("SYN1")
    elif fault == "duplicate": settings["episodes"].append(copy.deepcopy(settings["episodes"][0]))
    elif fault == "nonempty_findings": settings["episodes"][0]["prior_findings"] = ["Unbound finding"]
    elif fault == "invalid_hash": settings["episodes"][0]["raw_sha256"] = "bad"
    elif fault == "public_output": settings["output_root"] = str(Path(batch.__file__).resolve().parents[1])
    elif fault == "nested_source": settings["episodes"][0]["raw"] = str(root(settings) / "raw.json")
    else: settings["max_provider_failures"] = 0
    with pytest.raises(ValueError): batch.execute(settings)


def test_new_config_or_code_requires_new_batch_id(settings, monkeypatch):
    batch.execute(settings, command="run", backend_factory=factory([]))
    before = snapshot(root(settings))
    changed = copy.deepcopy(settings); changed["model"] = "different"
    with pytest.raises(batch.IncompatibleBatch, match="new batch_id"):
        batch.execute(changed, command="run")
    implementation = batch._implementation()
    monkeypatch.setattr(batch, "_implementation", lambda: {**implementation, "batch_sha256": "f" * 64})
    with pytest.raises(batch.IncompatibleBatch, match="new batch_id"):
        batch.execute(settings, command="run")
    assert snapshot(root(settings)) == before


class FailOne(SyntheticModel):
    def complete(self, prompt, context, output_schema):
        if context["source"]["episode_id"] == "SYN1":
            raise ModelCallError("Synthetic provider SECRET must not be echoed", {
                "provider": "synthetic", "usage": {"input_tokens": 17, "output_tokens": 0}, "latency_seconds": 1.0})
        return super().complete(prompt, context, output_schema)


def test_failure_isolated_explicit_retry_creates_new_version_and_keeps_metadata(settings):
    first = batch.execute(settings, command="run", workers=1, backend_factory=lambda *a, **k: FailOne())
    assert first["counts"] == {"failed": 1, "completed_candidate": 3}
    failure = first["episodes"][0]
    assert failure["failure_kind"] == "provider"
    call = failure["call_records"][0]
    assert call["sha256"] == sha256_file(Path(call["path"]))
    assert call["model_metadata"]["usage"]["input_tokens"] == 17
    assert "SECRET" not in json.dumps(failure)
    old_attempt = root(settings) / "runs/SYN1/attempt_0001"
    before = snapshot(old_attempt)
    forbidden = Mock(side_effect=AssertionError("No implicit retry"))
    follow = batch.execute(settings, command="run", backend_factory=forbidden)
    assert follow["counts"] == first["counts"] and follow["model_calls"] == 0
    forbidden.assert_not_called()
    plan = batch.execute(settings, retry_failed=True)
    assert plan["estimated_new_calls"] == 1
    models = []
    retried = batch.execute(settings, command="run", retry_failed=True, backend_factory=factory(models))
    assert retried["counts"] == {"completed_candidate": 4} and len(models) == 1
    assert retried["episodes"][0]["attempt"] == 2
    assert snapshot(old_attempt) == before
    assert (root(settings) / "episodes/SYN1/attempt_0001.json").exists()
    assert (root(settings) / "episodes/SYN1/attempt_0002.json").exists()


def test_provider_circuit_leaves_remaining_pending_and_survives_resume(settings):
    settings["max_provider_failures"] = 1
    first = batch.execute(settings, command="run", workers=1, backend_factory=lambda *a, **k: FailOne())
    assert first["state"] == "needs_attention"
    assert first["counts"] == {"failed": 1, "pending": 3}
    assert first["pending_episode_ids"] == ["SYN2", "SYN3", "SYN4"]
    assert not (root(settings) / "episodes/SYN2").exists()
    forbidden = Mock(side_effect=AssertionError("Circuit needs explicit retry"))
    follow = batch.execute(settings, command="run", workers=1, backend_factory=forbidden)
    assert follow["state"] == "needs_attention" and follow["pending_episode_ids"] == first["pending_episode_ids"]
    forbidden.assert_not_called()
    retried = batch.execute(settings, command="run", retry_failed=True, backend_factory=factory([]))
    assert retried["state"] == "completed_candidate"
    assert retried["provider_failure_count"] == 1 and retried["consecutive_provider_failures"] == 0


class FailSelected(SyntheticModel):
    def __init__(self, ids):
        super().__init__()
        self.ids = ids

    def complete(self, prompt, context, output_schema):
        if context["source"]["episode_id"] in self.ids:
            raise ModelCallError("Synthetic unavailable provider", {"provider": "synthetic", "usage": None})
        return super().complete(prompt, context, output_schema)


def test_scattered_provider_errors_do_not_stop_queue_and_totals_survive_resume(settings):
    settings["max_provider_failures"] = 2
    result = batch.execute(settings, command="run", workers=1,
                           backend_factory=lambda *a, **k: FailSelected({"SYN1", "SYN3"}))
    assert result["counts"] == {"failed": 2, "completed_candidate": 2}
    assert result["provider_failure_count"] == 2 and result["consecutive_provider_failures"] == 0
    assert result["stop_reason"] is None
    for command in ("status", "plan", "validate", "run"):
        result = batch.execute(settings, command=command)
        assert result["provider_failure_count"] == 2 and result["consecutive_provider_failures"] == 0
    result = batch.execute(settings, command="run", retry_failed=True, backend_factory=factory([]))
    assert result["state"] == "completed_candidate" and result["provider_failure_count"] == 2


def test_skipping_completed_evidence_does_not_reset_failure_streak(settings):
    settings["max_provider_failures"] = 2
    fail = lambda *a, **k: FailSelected({"SYN1", "SYN3", "SYN4"})
    first = batch.execute(settings, command="run", workers=1, backend_factory=fail)
    assert first["provider_failure_count"] == 3 and first["consecutive_provider_failures"] == 2
    result = batch.execute(settings, command="run", workers=1, retry_failed=True, backend_factory=fail)
    assert result["state"] == "needs_attention" and result["provider_failure_count"] == 5
    assert result["consecutive_provider_failures"] == 2
    assert [r["attempt"] for r in result["episodes"]] == [2, 1, 2, 1]


def test_circuit_reconciles_episode_checkpoint_after_aggregate_write_interruption(settings, monkeypatch):
    settings["max_provider_failures"] = 1
    original = batch.atomic_write_json
    summaries = 0
    class SimulatedInterruption(BaseException):
        pass
    def interrupt(path, value, **kwargs):
        nonlocal summaries
        if path.name == "summary.json":
            summaries += 1
            if summaries == 2:
                raise SimulatedInterruption()
        return original(path, value, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(batch, "atomic_write_json", interrupt)
        with pytest.raises(SimulatedInterruption):
            batch.execute(settings, command="run", workers=1, backend_factory=lambda *a, **k: FailOne())
    forbidden = Mock(side_effect=AssertionError("Interrupted circuit cannot reset itself"))
    result = batch.execute(settings, command="run", backend_factory=forbidden)
    assert result["state"] == "needs_attention" and result["provider_failure_count"] == 1
    assert result["consecutive_provider_failures"] == 1 and len(result["pending_episode_ids"]) == 3
    forbidden.assert_not_called()


def test_one_exhausted_episode_is_one_provider_failure_even_with_internal_retry(settings):
    settings["retry_count"] = 1
    result = batch.execute(settings, command="run", backend_factory=lambda *a, **k: FailOne())
    assert result["provider_failure_count"] == 1
    assert len(result["episodes"][0]["call_records"]) == 2


def test_missing_circuit_summary_does_not_silently_reset_provider_history(settings):
    batch.execute(settings, command="run", backend_factory=lambda *a, **k: FailOne())
    (root(settings) / "summary.json").unlink()
    with pytest.raises(ValueError, match="restore summary"):
        batch.execute(settings, command="run", retry_failed=True)


def test_source_failure_is_isolated_and_requires_explicit_retry(settings):
    raw = Path(settings["episodes"][0]["raw"]); original = raw.read_bytes()
    atomic_write_json(raw, {"changed": "Synthetic damaged input"})
    models = []
    result = batch.execute(settings, command="run", workers=2, backend_factory=factory(models))
    assert result["counts"] == {"failed": 1, "completed_candidate": 3} and len(models) == 3
    assert result["episodes"][0]["attempt"] == 0
    atomic_write_json(raw, json.loads(original))
    assert sha256_file(raw) == settings["episodes"][0]["raw_sha256"]
    forbidden = Mock(side_effect=AssertionError("Retry must be explicit"))
    assert batch.execute(settings, command="run", backend_factory=forbidden)["counts"]["failed"] == 1
    forbidden.assert_not_called()
    assert batch.execute(settings, command="run", retry_failed=True,
                         backend_factory=factory([]))["state"] == "completed_candidate"


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_completed_evidence_never_regenerated_even_with_retry(settings, damage):
    first = batch.execute(settings, command="run", backend_factory=factory([]))
    row = first["episodes"][0]; path = Path(row["artifact"]["path"])
    checkpoint = root(settings) / "episodes/SYN1/status.json"; before = checkpoint.read_bytes()
    if damage == "missing": path.unlink()
    else:
        value = read_json(path); value["tampered"] = True; atomic_write_json(path, value)
    forbidden = Mock(side_effect=AssertionError("Never replace completed evidence"))
    result = batch.execute(settings, command="run", retry_failed=True, backend_factory=forbidden)
    assert result["counts"] == {"validation_failed": 1, "completed_candidate": 3}
    assert checkpoint.read_bytes() == before
    assert not (root(settings) / "episodes/SYN1/attempt_0002.json").exists()
    forbidden.assert_not_called()


def test_checkpoint_lost_after_artifact_write_recovers_without_models(settings):
    result = batch.execute(settings, command="run", backend_factory=factory([]))
    checkpoint = root(settings) / "episodes/SYN1/status.json"; checkpoint.unlink()
    artifact = Path(result["episodes"][0]["artifact"]["path"]); before = artifact.read_bytes()
    recovered = batch.execute(settings, command="run")
    assert recovered["state"] == "completed_candidate" and recovered["model_calls"] == 0
    assert artifact.read_bytes() == before and read_json(checkpoint)["state"] == "completed_candidate"


def test_interrupted_attempt_is_not_automatically_retried(settings):
    folder = root(settings); folder.mkdir(parents=True)
    atomic_write_json(folder / "batch_manifest.json", batch._manifest(settings))
    entry = settings["episodes"][0]
    config = batch._episode_config(settings, entry, folder, 1)
    atomic_write_json(folder / "episodes/SYN1/attempt_0001.json", config)
    models = []
    result = batch.execute(settings, command="run", backend_factory=factory(models))
    assert result["counts"] == {"interrupted": 1, "completed_candidate": 3} and len(models) == 3
    before = (folder / "episodes/SYN1/attempt_0001.json").read_bytes()
    result = batch.execute(settings, command="run", retry_failed=True, backend_factory=factory([]))
    assert result["state"] == "completed_candidate" and result["episodes"][0]["attempt"] == 2
    assert (folder / "episodes/SYN1/attempt_0001.json").read_bytes() == before


def test_batch_process_lock_blocks_duplicate_scheduler(settings):
    folder = root(settings); folder.mkdir(parents=True)
    with (folder / ".batch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        forbidden = Mock(side_effect=AssertionError("No duplicate scheduler"))
        with pytest.raises(BlockingIOError):
            batch.execute(settings, command="run", backend_factory=forbidden)
    forbidden.assert_not_called()
    assert not (folder / "batch_manifest.json").exists()


def test_episode_lock_defers_collision_without_failed_retry(settings):
    folder = root(settings); folder.mkdir(parents=True)
    atomic_write_json(folder / "batch_manifest.json", batch._manifest(settings))
    _, run_root, _ = batch._paths(folder, "SYN1", 1); run_root.mkdir(parents=True)
    models = []
    with (run_root / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = batch.execute(settings, command="run", backend_factory=factory(models))
    assert result["counts"] == {"deferred": 1, "completed_candidate": 3}
    result = batch.execute(settings, command="run", backend_factory=factory(models))
    assert result["state"] == "completed_candidate"
    assert result["episodes"][0]["attempt"] == 1
    assert sum(len(m.calls) for m in models) == 4


def test_retry_does_not_start_new_attempt_while_previous_pipeline_is_busy(settings):
    batch.execute(settings, command="run", backend_factory=lambda *a, **k: FailOne())
    run_root = root(settings) / "runs/SYN1/attempt_0001"
    forbidden = Mock(side_effect=AssertionError("Prior run is still busy"))
    with (run_root / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = batch.execute(settings, command="run", retry_failed=True, backend_factory=forbidden)
    assert result["episodes"][0]["state"] == "deferred"
    assert not (root(settings) / "episodes/SYN1/attempt_0002.json").exists()
    forbidden.assert_not_called()


def test_symlink_cannot_redirect_episode_writes(settings, tmp_path):
    folder = root(settings); folder.mkdir(parents=True)
    atomic_write_json(folder / "batch_manifest.json", batch._manifest(settings))
    (folder / "episodes").mkdir(); outside = tmp_path / "outside"; outside.mkdir()
    (folder / "episodes/SYN1").symlink_to(outside, target_is_directory=True)
    models = []
    result = batch.execute(settings, command="run", backend_factory=factory(models))
    assert result["counts"] == {"validation_failed": 1, "completed_candidate": 3}
    assert not list(outside.iterdir()) and len(models) == 3


def test_cli_plan_status_validate_and_run_are_evidence_only(settings, tmp_path, capsys, monkeypatch):
    path = tmp_path / "batch.json"; atomic_write_json(path, settings)
    assert batch.main(["plan", "--config", str(path), "--workers", "8"]) == 0
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
    assert batch.main(["validate", "--config", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "incomplete"
    models = []; monkeypatch.setattr(batch, "CodexBackend", factory(models))
    assert batch.main(["run", "--config", str(path), "--workers", "5"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"] == {"completed_candidate": 4}
    assert batch.main(["status", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["validation_scope"] == "checkpoint_only"
    assert batch.main(["validate", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
    assert all(model.calls == ["evidence"] for model in models)
