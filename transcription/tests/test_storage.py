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
