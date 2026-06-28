#!/usr/bin/env python3
from __future__ import annotations

import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from common import DATA, run_command, setup_logging, write_json


def package_version(name: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return None


def get_cpu_model() -> str:
    if Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def nvidia_info() -> dict[str, Any]:
    if shutil.which("nvidia-smi") is None:
        return {"has_nvidia_gpu": False}
    code, out, err = run_command(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        timeout=10,
    )
    if code != 0:
        return {"has_nvidia_gpu": False, "nvidia_smi_error": err}
    gpus = []
    for line in out.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 3:
            gpus.append({"name": parts[0], "vram_mb": parts[1], "driver_version": parts[2]})
    code_cuda, cuda_out, _ = run_command(["nvidia-smi"], timeout=10)
    cuda_version = None
    if code_cuda == 0 and "CUDA Version:" in cuda_out:
        cuda_version = cuda_out.split("CUDA Version:", 1)[1].split()[0]
    return {"has_nvidia_gpu": bool(gpus), "gpus": gpus, "cuda_version_nvidia_smi": cuda_version}


def torch_info() -> dict[str, Any]:
    info: dict[str, Any] = {"torch_version": package_version("torch")}
    try:
        import torch

        info["torch_cuda_available"] = bool(torch.cuda.is_available())
        info["torch_cuda_version"] = torch.version.cuda
        if torch.cuda.is_available():
            info["torch_cuda_device_count"] = torch.cuda.device_count()
            info["torch_cuda_device_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            info["torch_cuda_vram_mb"] = round(props.total_memory / 1024 / 1024)
        info["torch_mps_available"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    except Exception as exc:
        info["torch_import_error"] = str(exc)
    return info


def main() -> int:
    setup_logging()
    try:
        import psutil
    except Exception as exc:
        print(f"psutil is required for environment inspection: {exc}", file=sys.stderr)
        return 2

    disk = shutil.disk_usage(str(DATA))
    memory = psutil.virtual_memory()
    ffmpeg_code, ffmpeg_out, ffmpeg_err = run_command(["ffmpeg", "-version"], timeout=10) if shutil.which("ffmpeg") else (127, "", "not found")
    ffprobe_code, ffprobe_out, ffprobe_err = run_command(["ffprobe", "-version"], timeout=10) if shutil.which("ffprobe") else (127, "", "not found")
    info: dict[str, Any] = {
        "operating_system": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "is_apple_silicon": platform.system() == "Darwin" and platform.machine() == "arm64",
        "cpu_model": get_cpu_model(),
        "cpu_core_count_logical": psutil.cpu_count(logical=True),
        "cpu_core_count_physical": psutil.cpu_count(logical=False),
        "total_ram_gb": round(memory.total / 1024**3, 2),
        "available_ram_gb": round(memory.available / 1024**3, 2),
        "total_disk_gb": round(disk.total / 1024**3, 2),
        "available_disk_gb": round(disk.free / 1024**3, 2),
        "python_version": sys.version,
        "ffmpeg_version": ffmpeg_out.splitlines()[0] if ffmpeg_code == 0 and ffmpeg_out else None,
        "ffmpeg_error": ffmpeg_err if ffmpeg_code != 0 else None,
        "ffprobe_version": ffprobe_out.splitlines()[0] if ffprobe_code == 0 and ffprobe_out else None,
        "ffprobe_error": ffprobe_err if ffprobe_code != 0 else None,
        "faster_whisper_version": package_version("faster-whisper"),
        "ctranslate2_version": package_version("ctranslate2"),
    }
    info.update(nvidia_info())
    info.update(torch_info())
    if info.get("torch_cuda_available"):
        backend = "faster-whisper CUDA"
    elif info["is_apple_silicon"]:
        backend = "Apple Silicon candidate: MLX Whisper / whisper.cpp / PyTorch MPS"
    else:
        backend = "CPU candidate: faster-whisper int8 / turbo partial benchmark"
    info["recommended_transcription_backend"] = backend
    out_path = DATA / "environment_inspection.json"
    write_json(out_path, info)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
