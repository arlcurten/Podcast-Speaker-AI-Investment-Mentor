#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from modules.common import DATA, path_for_report, project_relative_path, read_json, run_command, setup_logging, terminology_prompt, utc_now, write_json
from modules.transcript.audit_transcript_segments import audit


CONFIGS = {
    "large-v3": {"model": "large-v3", "beam_size": 5},
    "large-v3-turbo": {"model": "large-v3-turbo", "beam_size": 5},
}


def pkg(name: str) -> str | None:
    try:
        return version(name)
    except Exception:
        return None


def choose_device_compute() -> tuple[str, str]:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def peak_vram_mb() -> float | None:
    try:
        import torch

        if torch.cuda.is_available():
            return round(torch.cuda.max_memory_allocated() / 1024 / 1024, 2)
    except Exception:
        return None
    return None


class VramMonitor:
    def __init__(self, enabled: bool, sampling_interval_seconds: float = 1.0) -> None:
        self.enabled = enabled and shutil.which("nvidia-smi") is not None
        self.sampling_interval_seconds = sampling_interval_seconds
        self.baseline_mb: float | None = None
        self.peak_mb: float | None = None
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
        code, out, _ = run_command(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            timeout=5,
        )
        if code != 0:
            return None
        values: list[float] = []
        for line in out.splitlines():
            try:
                values.append(float(line.strip()))
            except ValueError:
                continue
        return max(values) if values else None


def make_clip(src: Path, seconds: int) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="gooaye_clip_")) / "clip.wav"
    code, _, err = run_command([
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(src),
        "-t",
        str(seconds),
        "-ar",
        "16000",
        "-ac",
        "1",
        str(tmp),
    ], timeout=max(120, seconds // 2))
    if code != 0:
        raise RuntimeError(f"ffmpeg clip creation failed: {err}")
    return tmp


def srt_time(value: float) -> str:
    millis = int(round(value * 1000))
    h, rem = divmod(millis, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_outputs(out_dir: Path, segments: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "transcript.json", {"metadata": metadata, "segments": segments})
    srt_lines = []
    md_lines = [f"# Transcript {metadata['episode_id']} {metadata['configuration_name']}", ""]
    for idx, seg in enumerate(segments, start=1):
        srt_lines.extend([str(idx), f"{srt_time(seg['start'])} --> {srt_time(seg['end'])}", seg["text"].strip(), ""])
        md_lines.append(f"[{seg['start']:.2f} - {seg['end']:.2f}] {seg['text'].strip()}")
    (out_dir / "transcript.srt").write_text("\n".join(srt_lines), encoding="utf-8")
    (out_dir / "transcript.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    write_json(out_dir / "run_metadata.json", metadata)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", required=True)
    parser.add_argument("--config", choices=sorted(CONFIGS), required=True)
    parser.add_argument("--max-seconds", type=int, default=None, help="Partial-audio benchmark length")
    parser.add_argument("--device", default=None, choices=["cpu", "cuda", "auto"])
    parser.add_argument("--compute-type", default=None)
    args = parser.parse_args()
    setup_logging()
    try:
        from faster_whisper import WhisperModel
        import psutil
    except Exception as exc:
        print(f"Missing transcription dependency: {exc}", file=sys.stderr)
        return 2
    audio_path = DATA / "audio" / args.episode / "episode.mp3"
    audio_meta_path = DATA / "audio" / args.episode / "audio_metadata.json"
    if not audio_path.exists():
        print(f"Audio not found: {audio_path}", file=sys.stderr)
        return 3
    audio_meta = read_json(audio_meta_path) if audio_meta_path.exists() else {}
    source_audio = audio_path
    cleanup_parent: Path | None = None
    if args.max_seconds:
        try:
            source_audio = make_clip(audio_path, args.max_seconds)
            cleanup_parent = source_audio.parent
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 4
    device, compute_type = choose_device_compute()
    if args.device and args.device != "auto":
        device = args.device
    if args.compute_type:
        compute_type = args.compute_type
    cfg = CONFIGS[args.config]
    prompt = terminology_prompt()
    out_name = args.config
    if args.max_seconds:
        out_name = f"{args.config}_partial_{args.max_seconds}s"
    if args.compute_type:
        out_name = f"{out_name}_{args.compute_type}"
    out_dir = DATA / "transcripts" / args.episode / out_name
    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    vram_monitor = VramMonitor(device == "cuda")
    start = time.perf_counter()
    vram_monitor.start()
    status = "success"
    failure_reason = None
    segments_out: list[dict[str, Any]] = []
    try:
        model = WhisperModel(cfg["model"], device=device, compute_type=compute_type)
        segments, info = model.transcribe(
            str(source_audio),
            language="zh",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=cfg["beam_size"],
            initial_prompt=prompt,
            word_timestamps=False,
        )
        for idx, seg in enumerate(segments):
            peak_rss = max(peak_rss, process.memory_info().rss)
            segments_out.append({
                "segment_id": idx,
                "start": float(seg.start),
                "end": float(seg.end),
                "text": seg.text,
                "avg_logprob": getattr(seg, "avg_logprob", None),
                "no_speech_prob": getattr(seg, "no_speech_prob", None),
                "compression_ratio": getattr(seg, "compression_ratio", None),
                "model": cfg["model"],
                "configuration": args.config,
            })
        detected_language = getattr(info, "language", None)
        language_probability = getattr(info, "language_probability", None)
    except Exception as exc:
        status = "failed"
        failure_reason = str(exc)
        detected_language = None
        language_probability = None
    finally:
        vram_monitor.stop()
    runtime = time.perf_counter() - start
    audio_duration = float(args.max_seconds or audio_meta.get("duration_seconds") or 0)
    metadata = {
        "episode_id": args.episode,
        "configuration_name": out_name,
        "backend": "faster-whisper",
        "model": cfg["model"],
        "model_revision": None,
        "package_versions": {
            "faster_whisper": pkg("faster-whisper"),
            "ctranslate2": pkg("ctranslate2"),
            "torch": pkg("torch"),
        },
        "device": device,
        "compute_type": compute_type,
        "beam_size": cfg["beam_size"],
        "vad_enabled": True,
        "vad_parameters": {"min_silence_duration_ms": 500},
        "timestamp_settings": "segment-level timestamps",
        "initial_prompt": prompt,
        "source_audio": path_for_report(source_audio),
        "source_audio_project_relative_path": project_relative_path(source_audio),
        "source_audio_runtime_absolute_path": str(source_audio.resolve()),
        "partial_audio_seconds": args.max_seconds,
        "audio_duration_seconds": audio_duration,
        "runtime_seconds": runtime,
        "realtime_factor": runtime / audio_duration if audio_duration else None,
        "peak_ram_mb": round(peak_rss / 1024 / 1024, 2),
        "peak_vram_mb": vram_monitor.peak_mb if vram_monitor.peak_mb is not None else peak_vram_mb(),
        "peak_vram_method": "nvidia-smi memory.used polling" if vram_monitor.peak_mb is not None else "torch.cuda.max_memory_allocated or unavailable",
        "vram_measurement_reliable": bool(vram_monitor.peak_mb is not None),
        "vram_measurement_scope": vram_monitor.measurement_scope if vram_monitor.peak_mb is not None else "unavailable",
        "vram_sampling_interval_seconds": vram_monitor.sampling_interval_seconds if vram_monitor.peak_mb is not None else None,
        "baseline_vram_mb": vram_monitor.baseline_mb,
        "detected_language": detected_language,
        "language_probability": language_probability,
        "segment_count": len(segments_out),
        "transcript_char_count": sum(len(seg["text"]) for seg in segments_out),
        "status": status,
        "failure_reason": failure_reason,
        "completed_at": utc_now(),
    }
    write_outputs(out_dir, segments_out, metadata)
    if status == "success":
        write_json(DATA / "evaluation" / args.episode / "segment_audit.json", audit(out_dir / "transcript.json"))
    if cleanup_parent:
        shutil.rmtree(cleanup_parent, ignore_errors=True)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0 if status == "success" else 5


if __name__ == "__main__":
    raise SystemExit(main())
