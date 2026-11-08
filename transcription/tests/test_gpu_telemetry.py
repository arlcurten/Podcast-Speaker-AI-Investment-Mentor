import subprocess

import pytest

from transcription import audio, batch


@pytest.mark.parametrize("error", [OSError("synthetic"), subprocess.TimeoutExpired("nvidia-smi", 5)])
def test_optional_gpu_queries_tolerate_failure_and_resume(monkeypatch, error):
    monkeypatch.setattr(audio.shutil, "which", lambda _: "/synthetic/nvidia-smi")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(audio, "run_command", fail)
    monkeypatch.setattr(batch, "run_command", fail)
    monitor = audio.VramMonitor(True)
    assert monitor._sample_total_used_mb() is None
    assert monitor.sampling_failures == 1
    assert batch.gpu_model() is None
    monkeypatch.setattr(audio, "run_command", lambda *a, **kw: (0, "4180", ""))
    assert monitor._sample_total_used_mb() == 4180


@pytest.mark.parametrize("response", [(1, "", ""), (0, "nan\nN/A\n-1", "")])
def test_unavailable_memory_is_not_reported_as_zero(monkeypatch, response):
    monkeypatch.setattr(audio, "run_command", lambda *a, **kw: response)
    monitor = audio.VramMonitor(False)
    assert monitor._sample_total_used_mb() is None
    assert monitor.peak_mb is None
    assert monitor.sampling_failures == 1
