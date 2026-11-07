from pathlib import Path
from types import SimpleNamespace

import pytest

from transcription import audio, storage
from transcription.common import atomic_write_json, read_json


@pytest.fixture
def guard(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=100_000_000_000))
    return storage.StorageGuard({}, audio_root=tmp_path / "audio", write_roots=[tmp_path], budget_file=tmp_path / "budget.json")


def test_wsl_requires_real_host_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "is_wsl", lambda: True)
    monkeypatch.setattr(storage, "windows_mount", lambda _: False)
    with pytest.raises(storage.StorageLimitError, match="WSL requires"):
        storage.StorageGuard({}, audio_root=tmp_path, write_roots=[tmp_path], budget_file=tmp_path / "budget.json")
    with pytest.raises(storage.StorageLimitError, match="Windows filesystem"):
        storage.StorageGuard({"host_root": str(tmp_path)}, audio_root=tmp_path, write_roots=[tmp_path], budget_file=tmp_path / "budget.json")


def test_windows_free_space_wins_over_large_virtual_capacity(guard, monkeypatch):
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=24_000_000_000))
    with pytest.raises(storage.StorageLimitError, match="safety reserve"):
        guard.check()


def test_growth_cap_persists_across_restart_and_cannot_silently_increase(guard, monkeypatch):
    original = read_json(guard.budget_file)
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=39_000_000_000))
    reloaded = storage.StorageGuard({"max_growth_bytes": 90_000_000_000}, audio_root=guard.audio_root,
                                    write_roots=guard.write_roots, budget_file=guard.budget_file)
    assert reloaded.max_growth_bytes == 60_000_000_000
    with pytest.raises(storage.StorageLimitError, match="growth budget"):
        reloaded.check()
    assert read_json(guard.budget_file) == original


def test_download_reservation_and_failed_audio_working_set(guard):
    guard.audio_root.mkdir()
    (guard.audio_root / "failed.mp3").write_bytes(b"retained failure")
    guard.max_audio_bytes = 20
    guard.max_download_bytes = 10
    with pytest.raises(storage.StorageLimitError, match="working-set"):
        guard.admit_download()
    (guard.audio_root / "failed.mp3").unlink()
    guard.admit_download()


def test_invalid_or_changed_budget_fails_closed(guard):
    atomic_write_json(guard.budget_file, {"schema_version": "unknown"})
    with pytest.raises(storage.StorageLimitError, match="identity"):
        storage.StorageGuard({}, audio_root=guard.audio_root, write_roots=guard.write_roots, budget_file=guard.budget_file)


def test_linux_cache_and_outputs_share_stable_budget_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "is_wsl", lambda: False)
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=100_000_000_000))
    budget = tmp_path / "budget.json"
    cache = tmp_path / "cache/hub"
    first = storage.StorageGuard({}, audio_root=tmp_path / "audio", write_roots=[cache], budget_file=budget)
    before = read_json(budget)
    cache.mkdir(parents=True)
    outputs = tmp_path / "pipeline/batches"
    outputs.mkdir(parents=True)
    second = storage.StorageGuard({}, audio_root=tmp_path / "audio", write_roots=[outputs, cache], budget_file=budget)
    assert first.host_root == second.host_root == storage.filesystem_root(tmp_path)
    assert read_json(budget) == before
    second.check()


def test_download_checks_stream_size_and_cleans_partial(tmp_path, monkeypatch):
    class Response:
        status_code = 200
        headers = {"Content-Type": "audio/mpeg"}

        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, **kwargs): yield b"12345"; yield b"67890"

    monkeypatch.setattr(audio.requests, "get", lambda *a, **kw: Response())
    monkeypatch.setattr(audio, "inspect", lambda *a: pytest.fail("Oversized audio must not be decoded"))
    dest = tmp_path / "episode.mp3"
    checks = []
    with pytest.raises(ValueError, match="byte limit"):
        audio.download("https://example.invalid/synthetic", dest, 1, 1, max_bytes=8, check_space=checks.append)
    assert checks == [64_000_005]
    assert not dest.exists() and not dest.with_suffix(".mp3.part").exists()


def test_host_floor_during_download_stops_and_cleans_partial(tmp_path, monkeypatch):
    class Response:
        status_code = 200
        headers = {"Content-Type": "audio/mpeg"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, **kwargs): yield b"12345"

    monkeypatch.setattr(audio.requests, "get", lambda *a, **kw: Response())
    def low_space(*args): raise storage.StorageLimitError("Host floor")
    dest = tmp_path / "episode.mp3"
    with pytest.raises(storage.StorageLimitError):
        audio.download("https://example.invalid/synthetic", dest, 1, 1, check_space=low_space)
    assert not dest.exists() and not dest.with_suffix(".mp3.part").exists()


@pytest.fixture
def shared_guard(tmp_path, monkeypatch):
    state = {"free": 67_000_000_000_000, "used": 5_000_000_000}
    monkeypatch.setattr(storage, "is_wsl", lambda: False)
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=state["free"]))
    monkeypatch.setattr(storage, "volume_bytes", lambda _: state["used"])
    settings = {"shared_volume_root": str(tmp_path), "volume_capacity_bytes": 80_000_000_000,
                "max_growth_bytes": 10_000_000_000}
    guard = storage.StorageGuard(settings, audio_root=tmp_path / "audio", write_roots=[tmp_path / "output"],
                                 budget_file=tmp_path / "volume_budget.json")
    return guard, state


def test_shared_pool_other_tenant_usage_does_not_spend_our_growth_budget(shared_guard):
    guard, state = shared_guard
    state["free"] -= 200_000_000_000
    result = guard.check()
    assert result["volume_growth_bytes"] == 0
    assert result["volume_available_bytes"] == 75_000_000_000
    assert "host_growth_bytes" not in result


def test_shared_volume_own_growth_and_allocated_capacity_are_enforced(shared_guard):
    guard, state = shared_guard
    state["used"] = 16_000_000_000
    with pytest.raises(storage.StorageLimitError, match="growth budget"):
        guard.check()
    state["used"] = 56_000_000_000
    with pytest.raises(storage.StorageLimitError, match="Allocated volume"):
        guard.check()
    state["used"] = 5_000_000_000
    state["free"] = 24_000_000_000
    with pytest.raises(storage.StorageLimitError, match="safety reserve"):
        guard.check()


def test_shared_volume_resume_preserves_limits_and_rejects_capacity_change(shared_guard):
    guard, state = shared_guard
    original = guard.budget_file.read_bytes()
    settings = {**guard.settings, "max_growth_bytes": 90_000_000_000}
    resumed = storage.StorageGuard(settings, audio_root=guard.audio_root, write_roots=guard.write_roots,
                                   budget_file=guard.budget_file)
    assert resumed.max_growth_bytes == 10_000_000_000
    assert guard.budget_file.read_bytes() == original
    settings["volume_capacity_bytes"] = 100_000_000_000
    with pytest.raises(storage.StorageLimitError, match="identity"):
        storage.StorageGuard(settings, audio_root=guard.audio_root, write_roots=guard.write_roots,
                             budget_file=guard.budget_file)


def test_shared_volume_cannot_bypass_wsl_or_escape_workspace(shared_guard, monkeypatch, tmp_path):
    guard, state = shared_guard
    monkeypatch.setattr(storage, "is_wsl", lambda: True)
    with pytest.raises(storage.StorageLimitError, match="WSL"):
        storage.StorageGuard(guard.settings, audio_root=guard.audio_root, write_roots=guard.write_roots,
                             budget_file=guard.budget_file)
    monkeypatch.setattr(storage, "is_wsl", lambda: False)
    with pytest.raises(storage.StorageLimitError, match="All writes"):
        storage.StorageGuard(guard.settings, audio_root=guard.audio_root, write_roots=[tmp_path.parent / "elsewhere"],
                             budget_file=guard.budget_file)


def test_shared_volume_never_silently_reinterprets_host_budget(shared_guard):
    guard, state = shared_guard
    atomic_write_json(guard.budget_file, {"schema_version": "storage_budget.v1"})
    with pytest.raises(storage.StorageLimitError, match="preserve"):
        storage.StorageGuard(guard.settings, audio_root=guard.audio_root, write_roots=guard.write_roots,
                             budget_file=guard.budget_file)


def test_volume_measurement_includes_nested_files_without_following_external_links(tmp_path):
    root = tmp_path / "volume"; root.mkdir()
    nested = root / "cache"; nested.mkdir()
    (nested / "model").write_bytes(b"x" * 4096)
    before = storage.volume_bytes(root)
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "large").write_bytes(b"x" * 1_000_000)
    (root / "external").symlink_to(outside, target_is_directory=True)
    assert storage.volume_bytes(root) < before + 100_000
    assert before >= 4096
