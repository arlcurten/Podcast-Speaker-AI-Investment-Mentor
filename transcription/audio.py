from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import threading
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any, Callable

import requests

from .common import run_command

def download(url: str, dest: Path, timeout: int, retries: int, *, max_bytes: int = 500_000_000,
             check_space: Callable[[int], Any] | None = None) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    headers = {"User-Agent": "podcast-mentor-transcription/1.0"}
    last_exc: Exception | None = None
    start = time.perf_counter()
    http_status = None
    content_type = None
    content_length = None
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout, headers=headers) as response:
                http_status = response.status_code
                response.raise_for_status()
                content_type = response.headers.get("Content-Type")
                content_length = response.headers.get("Content-Length")
                if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                    raise ValueError("Audio exceeds configured download byte limit.")
                if content_type and not content_type.lower().startswith(("audio/", "application/octet-stream")):
                    raise ValueError("Unexpected audio Content-Type.")
                with part.open("wb") as fh:
                    written = 0
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            written += len(chunk)
                            if written > max_bytes:
                                raise ValueError("Streaming audio exceeds configured download byte limit.")
                            if check_space:
                                check_space(written + 64_000_000)
                            fh.write(chunk)
                if content_length and content_length.isdigit() and not response.headers.get("Content-Encoding"):
                    if part.stat().st_size != int(content_length):
                        raise ValueError("Downloaded audio size does not match HTTP Content-Length.")
            inspect(part)
            part.replace(dest)
            break
        except Exception as exc:
            last_exc = exc
            if part.exists():
                part.unlink()
            if attempt == retries:
                raise
            sleep_s = min(2 ** attempt, 30)
            logging.warning("Download failed on attempt %s/%s (%s); sleeping %ss", attempt, retries, type(exc).__name__, sleep_s)
            time.sleep(sleep_s)
    elapsed = time.perf_counter() - start
    return {
        "http_status": http_status,
        "http_content_type": content_type,
        "http_content_length": int(content_length) if content_length and content_length.isdigit() else content_length,
        "download_runtime_seconds": elapsed,
        "last_exception": str(last_exc) if last_exc and not dest.exists() else None,
    }



def inspect(path: Path) -> dict[str, Any]:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found")
    args = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration,bit_rate:stream=codec_name,codec_type,sample_rate,channels,bit_rate",
        "-of",
        "json",
        str(path),
    ]
    code, out, err = run_command(args, timeout=60)
    if code != 0:
        raise RuntimeError(f"ffprobe failed: {err}")
    data = json.loads(out)
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    stream = audio_streams[0] if audio_streams else {}
    decode_code, _, decode_err = run_command(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], timeout=180)
    fmt = data.get("format", {})
    if not audio_streams or decode_code != 0 or float(fmt.get("duration") or 0) <= 0:
        raise ValueError("Audio has no decodable stream or valid duration.")
    return {
        "project_relative_path": str(path),
        "runtime_absolute_path": str(path.resolve()),
        "audio_path": str(path),
        "duration_seconds": float(fmt["duration"]) if fmt.get("duration") else None,
        "format": fmt.get("format_name"),
        "codec": stream.get("codec_name"),
        "bitrate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else fmt.get("bit_rate"),
        "stream_bitrate": int(stream["bit_rate"]) if str(stream.get("bit_rate", "")).isdigit() else stream.get("bit_rate"),
        "sample_rate": int(stream["sample_rate"]) if str(stream.get("sample_rate", "")).isdigit() else stream.get("sample_rate"),
        "channels": stream.get("channels"),
        "decodable": decode_code == 0,
        "decode_error": decode_err if decode_code != 0 else None,
    }



def pkg(name: str) -> str | None:
    try:
        return version(name)
    except Exception:
        return None



class VramMonitor:
    def __init__(self, enabled: bool, sampling_interval_seconds: float = 1.0) -> None:
        self.enabled = enabled and shutil.which("nvidia-smi") is not None
        self.sampling_interval_seconds = sampling_interval_seconds
        self.baseline_mb: float | None = None
        self.peak_mb: float | None = None
        self.sampling_failures = 0
        self.measurement_scope = "total-device memory.used; includes other GPU processes"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        self.baseline_mb = self._sample_total_used_mb()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            value = self._sample_total_used_mb()
            if value is not None:
                self.peak_mb = value if self.peak_mb is None else max(self.peak_mb, value)
            time.sleep(self.sampling_interval_seconds)

    def _sample_total_used_mb(self) -> float | None:
        try:
            code, out, _ = run_command(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            self.sampling_failures += 1
            return None
        if code != 0:
            self.sampling_failures += 1
            return None
        values: list[float] = []
        for line in out.splitlines():
            try:
                value = float(line.strip())
                if math.isfinite(value) and value >= 0:
                    values.append(value)
            except ValueError:
                continue
        if not values:
            self.sampling_failures += 1
        return max(values) if values else None
