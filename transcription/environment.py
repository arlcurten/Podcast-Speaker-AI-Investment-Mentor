"""Read-only worker diagnostics and explicit, storage-checked model caching."""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .common import private_path, run_command
from .storage import StorageGuard


def doctor() -> dict:
    versions, errors = {}, []
    for package in ("faster-whisper", "ctranslate2", "av", "PyYAML", "psutil"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
            errors.append(f"Install missing dependency: {package}")
    tools = {}
    for name in ("ffmpeg", "ffprobe", "nvidia-smi"):
        if not shutil.which(name):
            tools[name] = None
            errors.append(f"Required executable missing: {name}")
            continue
        command = [name, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"] if name == "nvidia-smi" else [name, "-version"]
        code, stdout, _ = run_command(command, timeout=10)
        tools[name] = stdout.splitlines()[0] if code == 0 and stdout else None
        if code:
            errors.append(f"Environment command failed: {name}")
    for library in ("libcublas.so.12", "libcudnn.so.9"):
        try:
            ctypes.CDLL(library)
        except OSError:
            errors.append(f"CUDA library unavailable: {library}; use scripts/transcription_worker.sh")
    supported = []
    if versions.get("ctranslate2"):
        try:
            import ctranslate2
            supported = sorted(ctranslate2.get_supported_compute_types("cuda"))
        except RuntimeError:
            errors.append("CTranslate2 cannot access CUDA; do not silently fall back to CPU.")
    return {"ready": not errors, "python": platform.python_version(), "packages": versions,
            "tools": tools, "supported_cuda_compute_types": supported, "errors": errors,
            "model_initialized": False, "network_used": False}


def cache_model(cfg: dict, *, execute: bool = False) -> dict:
    # Restrict managed downloads to the two reviewed multilingual checkpoints.
    if cfg["model_name"] not in {"large-v3", "large-v3-turbo"}:
        raise ValueError("Managed cache supports large-v3 and large-v3-turbo only.")
    home = os.environ.get("HF_HOME")
    if not home:
        raise ValueError("Set HF_HOME to an external private/worker cache directory first.")
    cache = private_path(home) / "hub"
    if not execute:
        return {"dry_run": True, "model": cfg["model_name"], "cache": str(cache), "reserve_bytes": 6_000_000_000}
    from .batch import resolve_paths
    from faster_whisper.utils import download_model

    paths = resolve_paths(cfg)
    guard = StorageGuard(cfg.get("storage") or {}, audio_root=paths.audio_root,
                         write_roots=[cache], budget_file=paths.output_root / "storage_budget.json")
    guard.check(6_000_000_000)
    path = Path(download_model(cfg["model_name"], cache_dir=str(cache), revision=cfg.get("model_revision")))
    guard.check()
    return {"dry_run": False, "model": cfg["model_name"], "resolved_revision": path.name,
            "cache": str(cache), "model_initialized": False}
