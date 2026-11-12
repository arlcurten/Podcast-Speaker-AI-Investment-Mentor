"""Synthetic calls only: parallel execution retains serial history compatibility."""
import fcntl
import json
import signal
import threading
from pathlib import Path

import pytest

from knowledge import parallel_discovery as parallel
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_corpus_discovery import (cfg, ready, no_provider, discovery, pin, save,
                                    FakeBackend, response_for)


def run(ready, model=None, **kwargs):
    return parallel.execute(discovery, pin(ready["path"]), command="run",
                            backend_factory=lambda: model or FakeBackend(), **kwargs)


def test_concurrency_receipts_serial_replay_and_skip(ready):
    barrier = threading.Barrier(2, timeout=10)
    lock = threading.Lock()
    active = peak = 0

    class Model:
        def complete(self, prompt, context, schema):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait()
            with lock:
                active -= 1
            return response_for(context), {"provider": "synthetic"}

    implementation = discovery._implementation()
    result = run(ready, Model(), workers=2)
    assert result["status"] == "completed" and peak == 2
    assert result["admitted_calls"] == result["model_calls_this_execution"] == 2
    assert discovery._implementation() == implementation
    receipts = list(ready["root"].glob("parallel_executions/*/start.json"))
    assert len(receipts) == 1
    assert read_json(receipts[0])["parallel_sha256"] == sha256_file(Path(parallel.__file__))
    assert discovery.execute(pin(ready["path"]), command="validate")["status"] == "validated"
    assert run(ready)["model_calls_this_execution"] == 0


def test_serial_parallel_serial_resume(ready):
    result = discovery.execute(pin(ready["path"]), command="run", max_packets=1, backend=FakeBackend())
    assert result["status"] == "paused_at_packet_limit"
    before = {p: p.read_bytes() for p in ready["root"].glob("packet_*/*.json")}
    assert run(ready)["model_calls_this_execution"] == 1
    assert all(p.read_bytes() == value for p, value in before.items())
    assert discovery.execute(pin(ready["path"]), command="run", backend=FakeBackend())["model_calls_this_execution"] == 0


def test_budget_never_resets(ready):
    ready["config"]["max_calls"] = 1
    save(ready)
    assert run(ready, workers=8)["admitted_calls"] == 1
    result = run(ready, workers=8)
    assert result["status"] == "call_budget_exhausted" and result["model_calls_this_execution"] == 0
    ready["config"]["max_calls"] = 2
    save(ready)
    with pytest.raises(ValueError, match="changed"):
        run(ready)


def test_validation_isolation_and_explicit_retry(ready):
    model = FakeBackend(lambda value, ctx: {} if not model.calls[:-1] else value)
    result = run(ready, model, workers=1)
    assert result["completed_packets"] == 1 and result["model_calls_this_execution"] == 2
    assert run(ready)["model_calls_this_execution"] == 0
    repaired = FakeBackend()
    result = run(ready, repaired, retry_failed=True)
    assert result["status"] == "completed" and result["admitted_calls"] == 3
    assert "validation_repair" in repaired.calls[0]
    assert discovery.execute(pin(ready["path"]), command="validate")["status"] == "validated"


def test_retry_exhaustion(ready):
    model = FakeBackend(lambda *_: {})
    run(ready, model)
    run(ready, model, retry_failed=True)
    assert run(ready, model, retry_failed=True)["model_calls_this_execution"] == 0
    assert len(list(ready["root"].glob("packet_*/attempt_*.request.json"))) == 4


def test_provider_failure_stops_new_admission(ready):
    class Model:
        def complete(self, *args):
            raise RuntimeError("synthetic provider quota")

    result = run(ready, Model(), workers=1)
    assert result["model_calls_this_execution"] == result["admitted_calls"] == 1
    assert run(ready)["model_calls_this_execution"] == 0
    assert run(ready, retry_failed=True)["status"] == "completed"


def test_provider_failure_drains_other_admitted_call(ready):
    barrier = threading.Barrier(2, timeout=10)
    lock = threading.Lock()
    count = 0

    class Model:
        def complete(self, prompt, context, schema):
            nonlocal count
            with lock:
                count += 1
                index = count
            barrier.wait()
            if index == 1:
                raise RuntimeError("synthetic provider outage")
            return response_for(context), None

    result = run(ready, Model())
    assert result["admitted_calls"] == 2 and result["completed_packets"] == 1
    assert len(list(ready["root"].glob("packet_*/*.seal.json"))) == 2
    assert discovery.execute(pin(ready["path"]), command="plan")["admitted_calls"] == 2


def test_interrupted_request_requires_inspection(ready):
    class Model:
        def complete(self, *args):
            raise KeyboardInterrupt()

    result = run(ready, Model(), workers=1)
    assert result["status"] == "interrupted_needs_inspection"
    assert result["admitted_calls"] == result["model_calls_this_execution"] == 1
    assert run(ready, retry_failed=True)["model_calls_this_execution"] == 0
    assert not list(ready["root"].glob("packet_*/*.result.json"))


def test_signal_drains_and_restores_handler(ready):
    old = signal.getsignal(signal.SIGTERM)

    class Model:
        def complete(self, prompt, context, schema):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return response_for(context), None

    result = run(ready, Model(), workers=1)
    assert result["status"] == "stopped_admissions_drained"
    assert result["completed_packets"] == result["admitted_calls"] == 1
    assert signal.getsignal(signal.SIGTERM) == old
    assert run(ready)["status"] == "completed"


def test_factory_failure_not_counted_as_model_call(ready):
    def fail():
        raise OSError("synthetic constructor failure")
    result = parallel.execute(discovery, pin(ready["path"]), command="run", workers=1, backend_factory=fail)
    assert result["admitted_calls"] == 1 and result["model_calls_this_execution"] == 0


def test_lock_excludes_parallel_and_serial(ready):
    ready["root"].mkdir(parents=True)
    with (ready["root"] / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            run(ready)
    assert not list(ready["root"].glob("packet_*/*.request.json"))


def test_changed_source_rejected_before_call(ready):
    path = Path(ready["source_cfg"]["episodes"][0]["raw"])
    atomic_write_json(path, {"changed": True})
    with pytest.raises(ValueError):
        run(ready)
    assert not list(ready["root"].glob("packet_*/*.request.json"))


def test_source_drift_during_call_stops_and_preserves_failure(ready, monkeypatch):
    check = discovery._check_pins
    changed = False

    def pins(value):
        if changed:
            raise ValueError("synthetic source drift")
        check(value)

    class Model:
        def complete(self, prompt, context, schema):
            nonlocal changed
            changed = True
            return response_for(context), None

    monkeypatch.setattr(discovery, "_check_pins", pins)
    result = run(ready, Model(), workers=1)
    assert result["admitted_calls"] == 1 and result["completed_packets"] == 0
    receipt = read_json(next(ready["root"].glob("packet_*/*.result.json")))
    assert receipt["error"]["phase"] == "source_validation"


def test_response_and_repair_oversize(ready):
    ready["config"]["max_response_chars"] = 100
    save(ready)
    model = FakeBackend(lambda *_: {"oversize": "x" * 100000})
    assert run(ready, model)["completed_packets"] == 0
    result = run(ready, retry_failed=True)
    assert result["status"] == "repair_input_overflow" and result["model_calls_this_execution"] == 0
    assert result["admitted_calls"] == 2


def test_initial_oversize_no_calls(ready):
    ready["config"]["max_input_chars"] = 1
    save(ready)
    with pytest.raises(ValueError):
        run(ready)
    assert not list(ready["root"].glob("packet_*/*.request.json"))


def test_tampered_receipt_rejected(ready):
    run(ready)
    path = next(ready["root"].glob("packet_*/*.result.json"))
    result = read_json(path)
    result["usage"] = {"tampered": True}
    atomic_write_json(path, result)
    with pytest.raises(ValueError, match="seal mismatch"):
        run(ready)


def test_plan_and_worker_validation(ready):
    assert parallel.execute(discovery, pin(ready["path"]), workers=2)["status"] == "planned"
    assert not ready["root"].exists()
    with pytest.raises(ValueError, match="workers"):
        run(ready, workers=0)


def test_invocation_limit_and_exact_request_pin(ready):
    result = run(ready, workers=8, max_packets=1)
    assert result["status"] == "paused_at_packet_limit" and result["admitted_calls"] == 1
    path = next(ready["root"].glob("packet_*/*.request.json"))
    request = read_json(path)
    request["prompt"] += "tampered"
    atomic_write_json(path, request)
    with pytest.raises(ValueError, match="exact planned input"):
        run(ready)


def test_implementation_drift_during_generation(ready, monkeypatch):
    implementation = discovery._implementation
    changed = False

    def current():
        value = implementation()
        return {**value, "runner_sha256": "0" * 64} if changed else value

    class Model:
        def complete(self, prompt, context, schema):
            nonlocal changed
            changed = True
            return response_for(context), None

    monkeypatch.setattr(discovery, "_implementation", current)
    result = run(ready, Model(), workers=1)
    assert result["admitted_calls"] == 1 and result["completed_packets"] == 0
    receipt = read_json(next(ready["root"].glob("packet_*/*.result.json")))
    assert receipt["error"]["phase"] == "source_validation"


def test_admission_failure_drains_inflight_without_fake_receipt(ready, monkeypatch):
    materialize = discovery._materialize
    started, release = threading.Event(), threading.Event()
    count = 0

    def material(*args):
        nonlocal count
        count += 1
        if count == 2:
            assert started.wait(10)
            release.set()
            raise ValueError("synthetic pre-admission failure")
        return materialize(*args)

    class Model:
        def complete(self, prompt, context, schema):
            started.set()
            assert release.wait(10)
            return response_for(context), None

    monkeypatch.setattr(discovery, "_materialize", material)
    with pytest.raises(ValueError, match="pre-admission"):
        run(ready, Model(), workers=2)
    assert len(list(ready["root"].glob("packet_*/*.request.json"))) == 1
    receipt = read_json(next(ready["root"].glob("packet_*/*.result.json")))
    assert receipt["status"] == "completed"
    finish = read_json(next(ready["root"].glob("parallel_executions/*/finish.json")))
    assert finish["status"] == "aborted_needs_inspection"
    assert finish["model_calls_this_execution"] == 1


@pytest.mark.parametrize("adapter_name", ["knowledge", "profile"])
@pytest.mark.parametrize("status,code", [
    ("planned", 0), ("validated", 0), ("completed", 0),
    ("failed_needs_explicit_retry", 1), ("interrupted_needs_inspection", 1),
    ("call_budget_exhausted", 1), ("paused_at_packet_limit", 1),
    ("repair_input_overflow", 1), ("stopped_admissions_drained", 1),
    ("aborted_needs_inspection", 1), ("unknown_future_status", 1),
])
def test_cli_exit_status(monkeypatch, capsys, adapter_name, status, code):
    command = {"planned": "plan", "validated": "validate"}.get(status, "run")
    monkeypatch.setattr("sys.argv", ["parallel_discovery", command, "--adapter", adapter_name,
                                   "--config", "/synthetic/config.json", "--sha256", "a" * 64,
                                   "--workers", "3"])

    def execute(adapter, ref, **kwargs):
        assert adapter.__name__ == ("knowledge" if adapter_name == "knowledge" else "speaker_profile") + ".corpus_discovery"
        assert ref == {"path": "/synthetic/config.json", "sha256": "a" * 64}
        assert kwargs["workers"] == 3 and kwargs["command"] == command
        return {"status": status, "model_calls_this_execution": 0}

    monkeypatch.setattr(parallel, "execute", execute)
    with pytest.raises(SystemExit) as exc:
        parallel.main()
    assert exc.value.code == code
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_cli_real_failed_return_exits_nonzero(ready, monkeypatch, capsys):
    execute = parallel.execute

    def mocked_provider(adapter, ref, **kwargs):
        return execute(adapter, ref, backend_factory=lambda: FakeBackend(lambda *_: {}), **kwargs)

    ref = pin(ready["path"])
    monkeypatch.setattr("sys.argv", ["parallel_discovery", "run", "--adapter", "knowledge",
                                   "--config", ref["path"], "--sha256", ref["sha256"], "--workers", "2"])
    monkeypatch.setattr(parallel, "execute", mocked_provider)
    with pytest.raises(SystemExit) as exc:
        parallel.main()
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed_needs_explicit_retry"
