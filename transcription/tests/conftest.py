import pytest
from types import SimpleNamespace


@pytest.fixture(autouse=True)
def synthetic_storage_environment(monkeypatch):
    # Unit tests must not depend on a real Windows host mount or its capacity.
    from transcription import storage

    monkeypatch.setattr(storage, "is_wsl", lambda: False)
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=100_000_000_000))
