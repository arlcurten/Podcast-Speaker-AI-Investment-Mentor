#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from modules.common import path_for_report, setup_logging, sha256_file, terminology_prompt, write_json
from modules.transcript.transcribe_episode import CONFIGS, VramMonitor, pkg, srt_time


def write_transcript_outputs(out_dir: Path, segments: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "transcript.json", {"metadata": metadata, "segments": segments})
    srt_lines: list[str] = []
    md_lines = [f"# Clean Benchmark Transcript {metadata['model']}", ""]
    for idx, seg in enumerate(segments, start=1):
        srt_lines.extend([str(idx), f"{srt_time(seg['start'])} --> {srt_time(seg['end'])}", seg["text"].strip(), ""])
        md_lines.append(f"[{seg['start']:.2f} - {seg['end']:.2f}] {seg['text'].strip()}")
    (out_dir / "transcript.srt").write_text("\n".join(srt_lines), encoding="utf-8")
    (out_dir / "transcript.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    write_json(out_dir / "run_metadata.json", metadata)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", required=True)
    parser.add_argument("--episode", default="EP674")
    parser.add_argument("--model", choices=sorted(CONFIGS), required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    parser.add_argument("--compute-type", default=None)
    parser.add_argument("--audio-duration", type=float, required=True)
    parser.add_argument("--clip-start", type=float, required=True)
    parser.add_argument("--clip-end", type=float, required=True)
    parser.add_argument("--sampling-interval", type=float, default=0.5)
    args = parser.parse_args()
    setup_logging()
    try:
        from faster_whisper import WhisperModel
        import psutil
    except Exception as exc:
        print(f"Missing transcription dependency: {exc}", file=sys.stderr)
        return 2

    clip_path = Path(args.clip)
    if not clip_path.exists():
        print(f"Clip not found: {clip_path}", file=sys.stderr)
        return 3
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = CONFIGS[args.model]
    compute_type = args.compute_type or ("float16" if args.model == "large-v3-turbo" else "int8_float16")
    prompt = terminology_prompt()
    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    vram_monitor = VramMonitor(args.device == "cuda", sampling_interval_seconds=args.sampling_interval)
    started = time.perf_counter()
    model_load_seconds: float | None = None
    failure_stage: str | None = None
    failure_reason: str | None = None
    detected_language = None
    language_probability = None
    segments_out: list[dict[str, Any]] = []
    status = "success"
    vram_monitor.start()
    try:
        failure_stage = "model_load"
        model_start = time.perf_counter()
        model = WhisperModel(cfg["model"], device=args.device, compute_type=compute_type)
        model_load_seconds = time.perf_counter() - model_start
        failure_stage = "transcription"
        segments, info = model.transcribe(
            str(clip_path),
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
                "configuration": f"{args.model}-clean",
            })
        detected_language = getattr(info, "language", None)
        language_probability = getattr(info, "language_probability", None)
        failure_stage = None
    except Exception as exc:
        status = "failed"
        failure_reason = str(exc)
    finally:
        vram_monitor.stop()
    runtime = time.perf_counter() - started
    transcript_text = "".join(s["text"] for s in segments_out)
    metadata = {
        "benchmark_type": "clean_same_clip",
        "episode_id": args.episode,
        "clip_path": path_for_report(clip_path),
        "clip_sha256": sha256_file(clip_path),
        "clip_start_seconds": args.clip_start,
        "clip_end_seconds": args.clip_end,
        "audio_duration_seconds": args.audio_duration,
        "backend": "faster-whisper",
        "model": cfg["model"],
        "device": args.device,
        "compute_type": compute_type,
        "beam_size": cfg["beam_size"],
        "vad_enabled": True,
        "vad_parameters": {"min_silence_duration_ms": 500},
        "timestamp_settings": "segment-level timestamps",
        "initial_prompt": prompt,
        "package_versions": {
            "faster_whisper": pkg("faster-whisper"),
            "ctranslate2": pkg("ctranslate2"),
            "torch": pkg("torch"),
        },
        "runtime_seconds": runtime,
        "model_load_seconds": model_load_seconds,
        "realtime_factor": runtime / args.audio_duration if args.audio_duration else None,
        "peak_ram_mb": round(peak_rss / 1024 / 1024, 2),
        "baseline_vram_mb": vram_monitor.baseline_mb,
        "peak_vram_mb": vram_monitor.peak_mb,
        "peak_vram_method": "nvidia-smi memory.used polling",
        "vram_measurement_reliable": bool(vram_monitor.peak_mb is not None),
        "vram_measurement_scope": vram_monitor.measurement_scope,
        "vram_sampling_interval_seconds": vram_monitor.sampling_interval_seconds,
        "detected_language": detected_language,
        "language_probability": language_probability,
        "segment_count": len(segments_out),
        "transcript_char_count": sum(len(s["text"]) for s in segments_out),
        "transcript_text_sha256": hashlib.sha256(transcript_text.encode("utf-8")).hexdigest() if segments_out else None,
        "status": status,
        "failure_stage": failure_stage,
        "failure_reason": failure_reason,
        "quality_review_status": "pending_manual_review",
    }
    if status == "success":
        write_transcript_outputs(out_dir, segments_out, metadata)
    else:
        write_json(out_dir / "run_metadata.json", metadata)
        write_json(out_dir / "failure.json", metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0 if status == "success" else 10


if __name__ == "__main__":
    raise SystemExit(main())
