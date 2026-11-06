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
    monkeypatch.setattr(environment, "import_module", lambda name: sys.modules["ctranslate2"] if name == "ctranslate2" else object())
    result = environment.doctor()
    assert result["ready"]
    assert not result["model_initialized"]
    assert not result["network_used"]


def test_doctor_missing_cuda_fails_clearly(monkeypatch):
    monkeypatch.setattr(environment, "version", lambda _: "synthetic")
    monkeypatch.setattr(environment.shutil, "which", lambda _: None)
    monkeypatch.setattr(environment.ctypes, "CDLL", lambda _: (_ for _ in ()).throw(OSError()))
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_supported_compute_types=lambda _: (_ for _ in ()).throw(RuntimeError())))
    monkeypatch.setattr(environment, "import_module", lambda name: sys.modules["ctranslate2"] if name == "ctranslate2" else object())
    result = environment.doctor()
    assert not result["ready"]
    assert any("silently fall back" in s for s in result["errors"])


def test_doctor_installed_but_unimportable_package_fails_closed(monkeypatch):
    monkeypatch.setattr(environment, "version", lambda _: "synthetic")
    monkeypatch.setattr(environment.shutil, "which", lambda x: x)
    monkeypatch.setattr(environment, "run_command", lambda *a, **k: (0, "synthetic tool", ""))
    monkeypatch.setattr(environment.ctypes, "CDLL", lambda _: None)
    monkeypatch.setattr(environment, "import_module", lambda _: (_ for _ in ()).throw(ImportError()))
    result = environment.doctor()
    assert not result["ready"]
    assert result["dependency_imports"] == {"faster_whisper": False, "ctranslate2": False, "av": False}


def test_setup_reuses_runtime_budget_and_checks_all_write_roots(monkeypatch, tmp_path):
    observed = {}
    class Guard:
        def __init__(self, settings, **kwargs):
            observed.update(settings=settings, **kwargs)
        def check(self, reserve):
            observed["reserve"] = reserve
            return {"synthetic": True}
    monkeypatch.setattr(environment, "StorageGuard", Guard)
    venv, cache, pipeline = (tmp_path / name for name in ("venv", "cache", "pipeline"))
    assert environment.setup_storage_check(str(venv), str(cache), str(pipeline), str(tmp_path)) == {"synthetic": True}
    assert observed["budget_file"] == pipeline / "storage_budget.json"
    assert observed["write_roots"] == [venv, cache, pipeline]
    assert observed["reserve"] == 10_000_000_000
    assert observed["settings"]["host_root"] == str(tmp_path)
    environment.setup_storage_check(str(venv), str(cache), str(pipeline), reserve_bytes=0)
    assert observed["reserve"] == 0


def test_setup_refuses_non_environment_directory_and_public_paths(tmp_path):
    from transcription.common import PROJECT_ROOT
    directory = tmp_path / "not-a-venv"
    directory.mkdir()
    (directory / "keep.txt").write_text("User-owned file")
    with pytest.raises(ValueError, match="virtual environment"):
        environment.setup_storage_check(str(directory), str(tmp_path / "cache"), str(tmp_path / "pipeline"))
    with pytest.raises(ValueError, match="public"):
        environment.setup_storage_check(str(PROJECT_ROOT / "sample_outputs/venv"), str(tmp_path / "cache"), str(tmp_path / "pipeline"))


def test_setup_on_wsl_requires_confirmed_host_mount(monkeypatch, tmp_path):
    from transcription import storage
    monkeypatch.setattr(storage, "is_wsl", lambda: True)
    with pytest.raises(storage.StorageLimitError, match="WSL requires"):
        environment.setup_storage_check(str(tmp_path / "venv"), str(tmp_path / "cache"), str(tmp_path / "pipeline"))
    assert not (tmp_path / "venv").exists()


def test_setup_does_not_reset_existing_growth_budget(monkeypatch, tmp_path):
    from collections import namedtuple
    from transcription import storage
    from transcription.common import atomic_write_json
    usage = namedtuple("usage", "total used free")
    host = tmp_path / "host"; host.mkdir()
    pipeline = tmp_path / "pipeline"
    atomic_write_json(pipeline / "storage_budget.json", {
        "schema_version": "storage_budget.v1", "host_root": str(host),
        "initial_host_free_bytes": 100_000_000_000, "max_growth_bytes": 60_000_000_000,
        "minimum_free_bytes": 25_000_000_000})
    original = (pipeline / "storage_budget.json").read_bytes()
    monkeypatch.setattr(storage, "is_wsl", lambda: False)
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: usage(120_000_000_000, 85_000_000_000, 35_000_000_000))
    with pytest.raises(storage.StorageLimitError, match="growth budget"):
        environment.setup_storage_check(str(tmp_path / "venv"), str(tmp_path / "cache"), str(pipeline), str(host), reserve_bytes=0)
    assert (pipeline / "storage_budget.json").read_bytes() == original


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


def test_setup_shared_volume_passes_all_paths_and_actual_capacity(monkeypatch, tmp_path):
    observed = {}
    class Guard:
        def __init__(self, settings, **kwargs):
            observed.update(settings=settings, **kwargs)
        def check(self, reserve):
            return {"reserve": reserve}
    monkeypatch.setattr(environment, "StorageGuard", Guard)
    result = environment.setup_storage_check(str(tmp_path / "venv"), str(tmp_path / "cache"), str(tmp_path / "pipeline"),
                                            shared_volume_root=str(tmp_path), volume_capacity_bytes=80_000_000_000)
    assert observed["settings"]["volume_capacity_bytes"] == 80_000_000_000
    assert observed["budget_file"].name == "volume_storage_budget.json"
    assert result["reserve"] == 10_000_000_000
