"""Conservative host-aware storage admission for sequential local processing."""
from __future__ import annotations

import platform
import shutil
import json
import re
from pathlib import Path
from typing import Any

from .common import atomic_write_json, private_path, read_json, utc_now, run_command


class StorageLimitError(RuntimeError):
    pass


def is_wsl() -> bool:
    return "microsoft" in platform.release().lower()


def windows_mount(path: Path) -> bool:
    code, out, _ = run_command(["findmnt", "--target", str(path), "--json", "--output", "FSTYPE,SOURCE"], timeout=10)
    if code:
        return False
    try:
        entry = json.loads(out)["filesystems"][0]
        return entry["fstype"] in {"9p", "drvfs"} and bool(re.match(r"^[A-Za-z]:", entry["source"]))
    except (ValueError, KeyError, IndexError):
        return False


def existing_parent(path: Path) -> Path:
    while not path.exists():
        path = path.parent
    return path


def filesystem_root(path: Path) -> Path:
    """Stable local budget identity as cache/output directories come into being."""
    current = existing_parent(path).resolve()
    if current.is_file():
        current = current.parent
    while current != current.parent and not current.is_mount():
        if current.parent.stat().st_dev != current.stat().st_dev:
            break
        current = current.parent
    return current


def tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_symlink():
            raise StorageLimitError("Audio working directory must not contain symlinks.")
        if item.is_file():
            total += item.stat().st_size
    return total


class StorageGuard:
    def __init__(self, settings: dict[str, Any], *, audio_root: Path, write_roots: list[Path], budget_file: Path):
        self.settings = settings
        self.audio_root = audio_root
        self.write_roots = write_roots
        self.budget_file = private_path(settings.get("budget_file") or budget_file)
        host = settings.get("host_root")
        if is_wsl() and not host:
            raise StorageLimitError("WSL requires storage.host_root pointing to the actual Windows backing-drive mount; virtual ext4 free space is not sufficient.")
        self.host_root = private_path(host) if host else filesystem_root(write_roots[0])
        if not self.host_root.exists():
            raise StorageLimitError("Configured host storage mount is unavailable.")
        if is_wsl() and not windows_mount(self.host_root):
            raise StorageLimitError("WSL host_root must refer to a Windows filesystem, not the Linux virtual disk.")
        self.minimum_free_bytes = settings.get("minimum_free_bytes", 25_000_000_000)
        self.max_growth_bytes = settings.get("max_growth_bytes", 60_000_000_000)
        self.max_audio_bytes = settings.get("max_audio_bytes", 2_000_000_000)
        self.max_download_bytes = settings.get("max_download_bytes", 500_000_000)
        for value in (self.minimum_free_bytes, self.max_growth_bytes, self.max_audio_bytes, self.max_download_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("Storage limits must be positive integer byte counts.")
        if self.max_download_bytes > self.max_audio_bytes:
            raise ValueError("max_download_bytes must fit max_audio_bytes.")
        if self.budget_file.exists():
            budget = read_json(self.budget_file)
            if (not isinstance(budget, dict) or budget.get("schema_version") != "storage_budget.v1" or budget.get("host_root") != str(self.host_root)
                    or any(type(budget.get(key)) is not int or budget[key] <= 0
                           for key in ("initial_host_free_bytes", "max_growth_bytes", "minimum_free_bytes"))):
                raise StorageLimitError("Storage budget identity is incompatible; do not reset it to bypass limits.")
            self.budget = budget
            self.max_growth_bytes = min(self.max_growth_bytes, budget["max_growth_bytes"])
            self.minimum_free_bytes = max(self.minimum_free_bytes, budget["minimum_free_bytes"])
        else:
            self.budget = {
                "schema_version": "storage_budget.v1", "created_at": utc_now(),
                "host_root": str(self.host_root), "initial_host_free_bytes": shutil.disk_usage(self.host_root).free,
                "max_growth_bytes": self.max_growth_bytes, "minimum_free_bytes": self.minimum_free_bytes,
                "scope": "Conservative net host free-space decline, including other applications; not a filesystem quota.",
            }
            self.check()
            atomic_write_json(self.budget_file, self.budget, overwrite=False)

    def check(self, reserve_bytes: int = 0) -> dict[str, Any]:
        free = shutil.disk_usage(self.host_root).free
        growth = max(0, self.budget["initial_host_free_bytes"] - free)
        if free - reserve_bytes < self.minimum_free_bytes:
            raise StorageLimitError("Host free space reached the safety reserve; stop and free space before retrying.")
        if growth + reserve_bytes > self.max_growth_bytes:
            raise StorageLimitError("Host storage growth budget reached; no additional work admitted.")
        for root in self.write_roots:
            if shutil.disk_usage(existing_parent(root)).free - reserve_bytes < self.minimum_free_bytes:
                raise StorageLimitError("An output filesystem reached the free-space safety reserve.")
        return {"host_free_bytes": free, "host_growth_bytes": growth,
                "minimum_free_bytes": self.minimum_free_bytes, "max_growth_bytes": self.max_growth_bytes}

    def admit_download(self) -> None:
        self.check(self.max_download_bytes + 64_000_000)
        if tree_bytes(self.audio_root) + self.max_download_bytes > self.max_audio_bytes:
            raise StorageLimitError("Audio working-set limit reached; inspect retained failed jobs before downloading more.")
