#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from modules.common import find_episode, load_manifest, run_command, sha256_file, utc_now
from modules.source.download_episode import download as stream_download
from modules.source.inspect_audio import inspect as inspect_audio
from modules.transcript.transcribe_episode import VramMonitor, pkg, terminology_prompt


STATUSES = {"pending", "downloading", "downloaded", "transcribing", "completed", "failed", "skipped"}
STAGES = ["metadata_resolved", "audio_downloaded", "transcription_completed", "output_validated", "job_completed"]


@dataclass
class BatchPaths:
    output_root: Path
    audio_root: Path
    transcript_root: Path
    checkpoint_path: Path


def expand_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(os.path.expandvars(value)).expanduser()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_batch_config(path: Path, overrides: argparse.Namespace | None = None) -> dict[str, Any]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if overrides:
        for key, attr in [
            ("output_root", "output_root"),
            ("audio_root", "audio_root"),
            ("transcript_root", "transcript_root"),
            ("device", "device"),
            ("compute_type", "compute_type"),
        ]:
            value = getattr(overrides, attr, None)
            if value:
                cfg[key] = value
    cfg.setdefault("batch_id", "transcription_batch")
    cfg.setdefault("episode_ids", [])
    cfg.setdefault("model_name", "large-v3-turbo")
    cfg.setdefault("device", "cuda")
    cfg.setdefault("compute_type", "float16")
    cfg.setdefault("language", "zh")
    cfg.setdefault("beam_size", 5)
    cfg.setdefault("condition_on_previous_text", False)
    cfg.setdefault("word_timestamps", False)
    cfg.setdefault("vad", {"enabled": True, "min_silence_duration_ms": 500})
    cfg.setdefault("audio_policy", "delete_on_success")
    cfg.setdefault("retry_count", 2)
    cfg.setdefault("download_timeout_seconds", 60)
    cfg.setdefault("skip_existing", True)
    if cfg["audio_policy"] not in {"keep", "delete_on_success", "reuse_existing"}:
        raise ValueError("audio_policy must be one of: keep, delete_on_success, reuse_existing")
    return cfg


def resolve_paths(cfg: dict[str, Any]) -> BatchPaths:
    pipeline_root = os.environ.get("PIPELINE_DATA_ROOT")
    podcast_root = os.environ.get("PODCAST_DATA_ROOT")
    output_root = expand_path(cfg.get("output_root")) or (Path(pipeline_root) / "legacy_segment" / "batches" if pipeline_root else None)
    audio_root = expand_path(cfg.get("audio_root")) or (Path(podcast_root) / "audio" if podcast_root else None)
    transcript_root = expand_path(cfg.get("transcript_root")) or (Path(podcast_root) / "transcripts" / "raw" if podcast_root else None)
    if output_root is None or audio_root is None or transcript_root is None:
        raise RuntimeError("Set output_root/audio_root/transcript_root or PODCAST_DATA_ROOT and PIPELINE_DATA_ROOT.")
    checkpoint = expand_path(cfg.get("checkpoint_path")) or output_root / str(cfg["batch_id"]) / "checkpoint.json"
    return BatchPaths(output_root=output_root, audio_root=audio_root, transcript_root=transcript_root, checkpoint_path=checkpoint)


def load_checkpoint(path: Path) -> dict[str, Any]:
    if path.exists():
        return read_json(path)
    return {"schema_version": "transcription_batch_checkpoint.v1", "jobs": {}, "created_at": utc_now(), "updated_at": utc_now()}


def update_job(checkpoint: dict[str, Any], path: Path, episode_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    jobs = checkpoint.setdefault("jobs", {})
    job = jobs.setdefault(episode_id, {"episode_id": episode_id, "status": "pending", "stage_history": []})
    job.update(patch)
    if "stage" in patch:
        job.setdefault("stage_history", []).append({"stage": patch["stage"], "timestamp": utc_now()})
    checkpoint["updated_at"] = utc_now()
    atomic_write_json(path, checkpoint)
    return job


def config_fingerprint(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_name": cfg.get("model_name"),
        "device": cfg.get("device"),
        "compute_type": cfg.get("compute_type"),
        "language": cfg.get("language"),
        "beam_size": cfg.get("beam_size"),
        "vad": cfg.get("vad"),
        "condition_on_previous_text": cfg.get("condition_on_previous_text"),
        "word_timestamps": cfg.get("word_timestamps"),
    }


def resolve_manifest_rows(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = expand_path(cfg.get("manifest_path"))
    return load_manifest(manifest)


def resolve_jobs(cfg: dict[str, Any], rows: list[dict[str, Any]], max_episodes: int | None = None) -> list[dict[str, Any]]:
    jobs = []
    seen: set[str] = set()
    for query in cfg.get("episode_ids") or []:
        episode = find_episode(rows, str(query))
        episode_id = str(episode["episode_id"])
        if episode_id in seen:
            continue
        seen.add(episode_id)
        jobs.append({"query": str(query), "episode_id": episode_id, "episode": episode})
        if max_episodes and len(jobs) >= max_episodes:
            break
    return jobs


def transcript_path(paths: BatchPaths, episode_id: str) -> Path:
    return paths.transcript_root / episode_id / "raw_transcript.json"


def run_metadata_path(paths: BatchPaths, episode_id: str) -> Path:
    return paths.transcript_root / episode_id / "raw_run_metadata.json"


def audio_path(paths: BatchPaths, episode_id: str) -> Path:
    return paths.audio_root / episode_id / "episode.mp3"


def completed_output(paths: BatchPaths, episode_id: str) -> bool:
    tpath = transcript_path(paths, episode_id)
    if not tpath.exists():
        return False
    try:
        payload = read_json(tpath)
    except Exception:
        return False
    return bool(payload.get("segments"))


def plan(cfg: dict[str, Any], *, max_episodes: int | None = None) -> dict[str, Any]:
    paths = resolve_paths(cfg)
    rows = resolve_manifest_rows(cfg)
    checkpoint = load_checkpoint(paths.checkpoint_path)
    jobs = []
    for item in resolve_jobs(cfg, rows, max_episodes):
        episode_id = item["episode_id"]
        existing = completed_output(paths, episode_id)
        status = checkpoint.get("jobs", {}).get(episode_id, {}).get("status")
        action = "skip_completed" if existing and cfg.get("skip_existing", True) else "run"
        jobs.append({
            "query": item["query"],
            "episode_id": episode_id,
            "title": item["episode"].get("episode_title"),
            "audio_url": item["episode"].get("audio_url"),
            "expected_audio_path": str(audio_path(paths, episode_id)),
            "expected_transcript_path": str(transcript_path(paths, episode_id)),
            "existing_status": status,
            "action": action,
        })
    return {"batch_id": cfg["batch_id"], "jobs": jobs, "checkpoint_path": str(paths.checkpoint_path)}


def validate_audio(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError(f"audio missing or empty: {path}")
    meta = inspect_audio(path)
    return {**meta, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def download_audio(episode: dict[str, Any], dest: Path, timeout: int, retries: int) -> dict[str, Any]:
    if dest.exists() and dest.stat().st_size > 0:
        return {"skipped_existing": True, **validate_audio(dest)}
    url = episode.get("audio_url")
    if not url:
        raise ValueError("episode missing audio_url")
    result = stream_download(str(url), dest, timeout=timeout, retries=retries)
    return {**result, **validate_audio(dest)}


def ensure_cuda_if_requested(device: str) -> None:
    if device != "cuda":
        return
    code, out, err = run_command(["nvidia-smi", "-L"], timeout=10)
    if code != 0:
        raise RuntimeError(f"CUDA requested but nvidia-smi failed: {err or out}")


def transcribe_audio(audio: Path, cfg: dict[str, Any], episode: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ensure_cuda_if_requested(str(cfg.get("device")))
    try:
        from faster_whisper import WhisperModel
        import psutil
    except Exception as exc:
        raise RuntimeError(f"Missing transcription dependency: {exc}") from exc
    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    vram = VramMonitor(str(cfg.get("device")) == "cuda")
    start = time.perf_counter()
    vram.start()
    model_load_start = time.perf_counter()
    model = WhisperModel(str(cfg["model_name"]), device=str(cfg["device"]), compute_type=str(cfg["compute_type"]))
    model_load_seconds = time.perf_counter() - model_load_start
    segments, info = model.transcribe(
        str(audio),
        language=cfg.get("language"),
        vad_filter=bool((cfg.get("vad") or {}).get("enabled", True)),
        vad_parameters={"min_silence_duration_ms": int((cfg.get("vad") or {}).get("min_silence_duration_ms", 500))},
        beam_size=int(cfg.get("beam_size", 5)),
        initial_prompt=terminology_prompt(),
        condition_on_previous_text=bool(cfg.get("condition_on_previous_text", False)),
        word_timestamps=bool(cfg.get("word_timestamps", False)),
    )
    out = []
    for idx, seg in enumerate(segments):
        peak_rss = max(peak_rss, process.memory_info().rss)
        out.append({"segment_id": idx, "start": float(seg.start), "end": float(seg.end), "text": seg.text})
    vram.stop()
    total_seconds = time.perf_counter() - start
    duration = inspect_audio(audio).get("duration_seconds")
    metadata = {
        "episode_id": episode.get("episode_id"),
        "backend": "faster-whisper",
        "model": cfg.get("model_name"),
        "device": cfg.get("device"),
        "compute_type": cfg.get("compute_type"),
        "language": cfg.get("language"),
        "beam_size": cfg.get("beam_size"),
        "vad": cfg.get("vad"),
        "condition_on_previous_text": cfg.get("condition_on_previous_text"),
        "word_timestamps": cfg.get("word_timestamps"),
        "detected_language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "audio_duration_seconds": duration,
        "model_load_seconds": model_load_seconds,
        "transcription_seconds": total_seconds - model_load_seconds,
        "total_episode_seconds": total_seconds,
        "realtime_factor": total_seconds / duration if duration else None,
        "peak_ram_mb": round(peak_rss / 1024 / 1024, 2),
        "peak_vram_mb": vram.peak_mb,
        "gpu_model": gpu_model(),
        "package_versions": {"faster_whisper": pkg("faster-whisper"), "ctranslate2": pkg("ctranslate2"), "torch": pkg("torch")},
        "hourly_rate_usd": (cfg.get("cost") or {}).get("hourly_rate_usd"),
        "estimated_job_cost_usd": None,
        "completed_at": utc_now(),
    }
    if metadata["hourly_rate_usd"]:
        metadata["estimated_job_cost_usd"] = float(metadata["hourly_rate_usd"]) * total_seconds / 3600
    return out, metadata


def gpu_model() -> str | None:
    code, out, _ = run_command(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], timeout=5)
    return out.splitlines()[0].strip() if code == 0 and out else None


def validate_transcript(path: Path) -> dict[str, Any]:
    try:
        payload = read_json(path)
    except Exception as exc:
        raise ValueError(f"invalid transcript JSON: {exc}") from exc
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("transcript has no segments")
    previous_end = -1.0
    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        if start < 0 or end < start:
            raise ValueError(f"invalid timestamps in segment {segment.get('segment_id')}")
        if start < previous_end - 5:
            raise ValueError("segment timestamps appear unsorted")
        previous_end = max(previous_end, end)
    return {"segment_count": len(segments), "validated_at": utc_now()}


def write_transcript(paths: BatchPaths, episode_id: str, segments: list[dict[str, Any]], metadata: dict[str, Any], episode: dict[str, Any]) -> None:
    payload = {"metadata": {**metadata, "source_episode_metadata": episode}, "segments": segments}
    atomic_write_json(transcript_path(paths, episode_id), payload)
    atomic_write_json(run_metadata_path(paths, episode_id), payload["metadata"])


def record_failure(checkpoint: dict[str, Any], paths: BatchPaths, episode_id: str, stage: str, exc: Exception, attempt: int, retryable: bool) -> None:
    failure = {"episode_id": episode_id, "stage": stage, "error_type": exc.__class__.__name__, "message": str(exc), "attempt": attempt, "timestamp": utc_now(), "retryable": retryable}
    update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "failed", "stage": stage, "last_error": failure})


def run_job(cfg: dict[str, Any], paths: BatchPaths, checkpoint: dict[str, Any], item: dict[str, Any], *, retry_failed: bool = False) -> dict[str, Any]:
    episode_id = item["episode_id"]
    episode = item["episode"]
    fingerprint = config_fingerprint(cfg)
    existing = checkpoint.get("jobs", {}).get(episode_id, {})
    if existing.get("status") == "completed" and cfg.get("skip_existing", True) and not retry_failed:
        return update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "skipped", "stage": "job_completed", "skip_reason": "completed checkpoint"})
    if completed_output(paths, episode_id) and cfg.get("skip_existing", True) and not retry_failed:
        return update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "skipped", "stage": "job_completed", "skip_reason": "existing valid transcript"})
    if existing.get("config_fingerprint") and existing["config_fingerprint"] != fingerprint and not retry_failed:
        raise RuntimeError(f"Config mismatch for {episode_id}; use retry/resume with compatible config or separate batch_id.")
    attempts = int(cfg.get("retry_count", 2)) + 1
    for attempt in range(1, attempts + 1):
        try:
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "pending", "stage": "metadata_resolved", "config_fingerprint": fingerprint, "attempt": attempt})
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "downloading", "stage": "audio_downloaded"})
            audio_meta = download_audio(episode, audio_path(paths, episode_id), int(cfg.get("download_timeout_seconds", 60)), 1)
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "downloaded", "audio_metadata": audio_meta, "stage": "audio_downloaded"})
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "transcribing", "stage": "transcribing"})
            segments, metadata = transcribe_audio(audio_path(paths, episode_id), cfg, episode)
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "transcribing", "stage": "transcription_completed", "segment_count": len(segments)})
            write_transcript(paths, episode_id, segments, metadata, episode)
            validation = validate_transcript(transcript_path(paths, episode_id))
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "transcribing", "stage": "output_validated", "validation": validation})
            if cfg.get("audio_policy") == "delete_on_success":
                audio_path(paths, episode_id).unlink(missing_ok=True)
            return update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "completed", "stage": "job_completed", "completed_at": utc_now(), "output": str(transcript_path(paths, episode_id))})
        except Exception as exc:
            retryable = attempt < attempts
            record_failure(checkpoint, paths, episode_id, "transcription_batch", exc, attempt, retryable)
            if not retryable:
                return checkpoint["jobs"][episode_id]
            time.sleep(min(2**attempt, 30))
    return checkpoint["jobs"][episode_id]


def build_summary(cfg: dict[str, Any], checkpoint: dict[str, Any], paths: BatchPaths) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for job in checkpoint.get("jobs", {}).values():
        status = job.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    summary = {"batch_id": cfg["batch_id"], "status_counts": counts, "updated_at": utc_now(), "checkpoint_path": str(paths.checkpoint_path)}
    atomic_write_json(paths.output_root / str(cfg["batch_id"]) / "summary.json", summary)
    failures = [job.get("last_error") for job in checkpoint.get("jobs", {}).values() if job.get("last_error")]
    atomic_write_json(paths.output_root / str(cfg["batch_id"]) / "failures.json", failures)
    return summary


def validate_batch_outputs(cfg: dict[str, Any]) -> dict[str, Any]:
    paths = resolve_paths(cfg)
    checkpoint = load_checkpoint(paths.checkpoint_path)
    errors: list[dict[str, Any]] = []
    validated_jobs: list[dict[str, Any]] = []
    for episode_id, job in sorted(checkpoint.get("jobs", {}).items()):
        status = job.get("status")
        if status != "completed":
            validated_jobs.append({"episode_id": episode_id, "status": status, "valid": status in {"failed", "skipped", "pending", "downloaded", "transcribing"}})
            continue
        tpath = transcript_path(paths, episode_id)
        try:
            validation = validate_transcript(tpath)
        except Exception as exc:
            errors.append({"episode_id": episode_id, "path": str(tpath), "error": str(exc)})
            validated_jobs.append({"episode_id": episode_id, "status": status, "valid": False})
        else:
            validated_jobs.append({"episode_id": episode_id, "status": status, "valid": True, **validation})
    result = {
        "batch_id": cfg["batch_id"],
        "checkpoint_path": str(paths.checkpoint_path),
        "valid": not errors,
        "job_count": len(validated_jobs),
        "validated_jobs": validated_jobs,
        "errors": errors,
        "validated_at": utc_now(),
    }
    atomic_write_json(paths.output_root / str(cfg["batch_id"]) / "validation_summary.json", result)
    return result


def run_batch(cfg: dict[str, Any], *, dry_run: bool, resume: bool, retry_failed: bool, max_episodes: int | None) -> dict[str, Any]:
    paths = resolve_paths(cfg)
    jobs = resolve_jobs(cfg, resolve_manifest_rows(cfg), max_episodes)
    checkpoint = load_checkpoint(paths.checkpoint_path)
    batch_manifest = {"batch_id": cfg["batch_id"], "config_fingerprint": config_fingerprint(cfg), "jobs": [{"episode_id": j["episode_id"], "title": j["episode"].get("episode_title")} for j in jobs], "created_at": utc_now()}
    atomic_write_json(paths.output_root / str(cfg["batch_id"]) / "batch_manifest.json", batch_manifest)
    if dry_run:
        return plan(cfg, max_episodes=max_episodes)
    for item in jobs:
        run_job(cfg, paths, checkpoint, item, retry_failed=retry_failed)
    return build_summary(cfg, checkpoint, paths)


def status(cfg: dict[str, Any]) -> dict[str, Any]:
    paths = resolve_paths(cfg)
    checkpoint = load_checkpoint(paths.checkpoint_path)
    return build_summary(cfg, checkpoint, paths)


def main() -> int:
    command = Path(sys.argv[0]).name
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--episode", action="append", default=None)
    parser.add_argument("--episode-file")
    parser.add_argument("--output-root")
    parser.add_argument("--audio-root")
    parser.add_argument("--transcript-root")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--skip-existing", action="store_true", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-episodes", type=int)
    parser.add_argument("--device")
    parser.add_argument("--compute-type")
    args = parser.parse_args()
    cfg = load_batch_config(Path(args.config), args)
    if args.episode:
        cfg["episode_ids"] = args.episode
    if args.episode_file:
        cfg["episode_ids"] = [line.strip() for line in Path(args.episode_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.skip_existing is not None:
        cfg["skip_existing"] = args.skip_existing
    try:
        if command == "batch-plan":
            result = plan(cfg, max_episodes=args.max_episodes)
        elif command in {"batch-run", "batch-resume"}:
            result = run_batch(cfg, dry_run=args.dry_run, resume=args.resume or command == "batch-resume", retry_failed=args.retry_failed, max_episodes=args.max_episodes)
        elif command == "batch-status":
            result = status(cfg)
        elif command == "batch-validate":
            result = validate_batch_outputs(cfg)
        else:
            raise ValueError(f"Unsupported batch command: {command}")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
