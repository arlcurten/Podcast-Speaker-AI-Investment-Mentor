from __future__ import annotations

import sys
import types

import pytest

from transcription import environment


def test_doctor_no_model_or_network(monkeypatch):
    monkeypatch.setattr(environment, "version", lambda _: "synthetic")
    monkeypatch.setattr(environment.shutil, "which", lambda x: x)
    monkeypatch.setattr(environment, "run_command", lambda *a, **k: (0, "synthetic tool", ""))
    monkeypatch.setattr(environment.ctypes, "CDLL", lambda _: None)
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_supported_compute_types=lambda _: {"float16"}))
    result = environment.doctor()
    assert result["ready"]
    assert not result["model_initialized"]
    assert not result["network_used"]


def test_doctor_missing_cuda_fails_clearly(monkeypatch):
    monkeypatch.setattr(environment, "version", lambda _: "synthetic")
    monkeypatch.setattr(environment.shutil, "which", lambda _: None)
    monkeypatch.setattr(environment.ctypes, "CDLL", lambda _: (_ for _ in ()).throw(OSError()))
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_supported_compute_types=lambda _: (_ for _ in ()).throw(RuntimeError())))
    result = environment.doctor()
    assert not result["ready"]
    assert any("silently fall back" in s for s in result["errors"])


def test_cache_dry_run_does_not_import_whisper_or_write(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "cache"))
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    result = environment.cache_model({"model_name": "large-v3"})
    assert result["dry_run"]
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="only"):
        environment.cache_model({"model_name": "unknown-download"})
    monkeypatch.delenv("HF_HOME")
    with pytest.raises(ValueError, match="HF_HOME"):
        environment.cache_model({"model_name": "large-v3"})
