"""Conservative host-aware storage admission for sequential local processing."""
from __future__ import annotations

import platform
import shutil
import json
import re
import os
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


def volume_bytes(root: Path) -> int:
    """Count this allocated workspace, not other tenants on a shared filesystem."""
    def fail_scan(error: OSError) -> None:
        raise error

    total = 0
    for directory, directories, files in os.walk(root, followlinks=False, onerror=fail_scan):
        for name in [".", *files, *[name for name in directories if (Path(directory) / name).is_symlink()]]:
            info = (Path(directory) / name).lstat()
            total += max(info.st_size, getattr(info, "st_blocks", 0) * 512)
    return total


class StorageGuard:
    def __init__(self, settings: dict[str, Any], *, audio_root: Path, write_roots: list[Path], budget_file: Path):
        self.settings = settings
        self.audio_root = audio_root
        self.write_roots = write_roots
        self.budget_file = private_path(settings.get("budget_file") or budget_file)
        host = settings.get("host_root")
        self.shared_volume_root = private_path(settings["shared_volume_root"]) if settings.get("shared_volume_root") else None
        if self.shared_volume_root and is_wsl():
            raise StorageLimitError("Shared-volume accounting cannot replace the WSL host-drive safety check.")
        if settings.get("volume_capacity_bytes") is not None and not self.shared_volume_root:
            raise ValueError("volume_capacity_bytes requires shared_volume_root.")
        if is_wsl() and not host:
            raise StorageLimitError("WSL requires storage.host_root pointing to the actual Windows backing-drive mount; virtual ext4 free space is not sufficient.")
        self.host_root = private_path(host) if host else (self.shared_volume_root or filesystem_root(write_roots[0]))
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
        if self.shared_volume_root:
            self._init_volume_budget()
            return
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

    def _init_volume_budget(self) -> None:
        root = self.shared_volume_root
        capacity = self.settings.get("volume_capacity_bytes")
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("Shared-volume accounting requires the actual allocated volume_capacity_bytes.")
        if not root.is_dir() or root == Path("/") or self.host_root != root:
            raise StorageLimitError("Use one existing allocated volume root, not the whole host.")
        if any(not p.resolve().is_relative_to(root) for p in [*self.write_roots, self.audio_root, self.budget_file]):
            raise StorageLimitError("All writes, audio, caches and budget must stay under the allocated volume root.")
        self.volume_capacity_bytes = capacity
        if self.budget_file.exists():
            self.budget = read_json(self.budget_file)
            if (not isinstance(self.budget, dict) or self.budget.get("schema_version") != "storage_volume_budget.v1"
                    or self.budget.get("volume_root") != str(root) or self.budget.get("volume_capacity_bytes") != capacity
                    or type(self.budget.get("initial_used_bytes")) is not int or self.budget["initial_used_bytes"] < 0
                    or any(type(self.budget.get(k)) is not int or self.budget[k] <= 0
                           for k in ("max_growth_bytes", "minimum_free_bytes"))):
                raise StorageLimitError("Storage volume budget identity is incompatible; preserve the earlier budget.")
            self.max_growth_bytes = min(self.max_growth_bytes, self.budget["max_growth_bytes"])
            self.minimum_free_bytes = max(self.minimum_free_bytes, self.budget["minimum_free_bytes"])
        else:
            self.budget = {"schema_version": "storage_volume_budget.v1", "created_at": utc_now(),
                           "volume_root": str(root), "volume_capacity_bytes": capacity,
                           "initial_used_bytes": volume_bytes(root), "max_growth_bytes": self.max_growth_bytes,
                           "minimum_free_bytes": self.minimum_free_bytes,
                           "scope": "Allocated workspace usage, including environments and model cache; not shared-pool free-space decline or an OS quota."}
            self.check()
            atomic_write_json(self.budget_file, self.budget, overwrite=False)

    def check(self, reserve_bytes: int = 0) -> dict[str, Any]:
        free = shutil.disk_usage(self.host_root).free
        if self.shared_volume_root:
            used = volume_bytes(self.shared_volume_root)
            available = min(free, self.volume_capacity_bytes - used)
            growth = max(0, used - self.budget["initial_used_bytes"])
            if available - reserve_bytes < self.minimum_free_bytes:
                raise StorageLimitError("Allocated volume reached the free-space safety reserve.")
        else:
            growth = max(0, self.budget["initial_host_free_bytes"] - free)
        if free - reserve_bytes < self.minimum_free_bytes:
            raise StorageLimitError("Host free space reached the safety reserve; stop and free space before retrying.")
        if growth + reserve_bytes > self.max_growth_bytes:
            raise StorageLimitError("Storage growth budget reached; no additional work admitted.")
        for root in self.write_roots:
            if shutil.disk_usage(existing_parent(root)).free - reserve_bytes < self.minimum_free_bytes:
                raise StorageLimitError("An output filesystem reached the free-space safety reserve.")
        result = {"host_free_bytes": free, "minimum_free_bytes": self.minimum_free_bytes,
                  "max_growth_bytes": self.max_growth_bytes}
        if self.shared_volume_root:
            result.update(volume_used_bytes=used, volume_growth_bytes=growth,
                          volume_capacity_bytes=self.volume_capacity_bytes, volume_available_bytes=available)
        else:
            result["host_growth_bytes"] = growth
        return result

    def admit_download(self) -> None:
        self.check(self.max_download_bytes + 64_000_000)
        if tree_bytes(self.audio_root) + self.max_download_bytes > self.max_audio_bytes:
            raise StorageLimitError("Audio working-set limit reached; inspect retained failed jobs before downloading more.")
