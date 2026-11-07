#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .common import (find_episode, load_manifest, run_command, sha256_file, utc_now,
                     atomic_write_json, read_json, private_path, child_path, safe_id, TERMINOLOGY)
from .audio import download as stream_download, inspect as inspect_audio, VramMonitor, pkg
from .common import terminology_prompt
from .quality import validate_transcript
from .storage import StorageGuard, StorageLimitError


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
    return private_path(value)


def load_batch_config(path: Path, overrides: argparse.Namespace | None = None) -> dict[str, Any]:
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError("Invalid batch config YAML.") from exc
    if not isinstance(cfg, dict):
        raise ValueError("Batch config must be a mapping.")
    if overrides:
        for key, attr in [
            ("output_root", "output_root"),
            ("audio_root", "audio_root"),
            ("transcript_root", "transcript_root"),
            ("device", "device"),
            ("compute_type", "compute_type"),
            ("inference_mode", "inference_mode"),
            ("inference_batch_size", "inference_batch_size"),
        ]:
            value = getattr(overrides, attr, None)
            if value is not None:
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
    cfg.setdefault("inference_mode", "sequential")
    cfg.setdefault("inference_batch_size", 1)
    cfg.setdefault("vad", {"enabled": True, "min_silence_duration_ms": 500})
    cfg.setdefault("audio_policy", "delete_on_success")
    cfg.setdefault("retry_count", 2)
    cfg.setdefault("download_timeout_seconds", 60)
    cfg.setdefault("skip_existing", True)
    cfg.setdefault("storage", {})
    if not isinstance(cfg["storage"], dict):
        raise ValueError("storage must be a mapping.")
    if cfg["audio_policy"] not in {"keep", "delete_on_success", "reuse_existing"}:
        raise ValueError("audio_policy must be one of: keep, delete_on_success, reuse_existing")
    safe_id(str(cfg["batch_id"]))
    if not isinstance(cfg["episode_ids"], list):
        raise ValueError("episode_ids must be a list.")
    if type(cfg["retry_count"]) is not int or cfg["retry_count"] < 0:
        raise ValueError("retry_count must be a nonnegative integer.")
    if cfg["device"] not in {"cpu", "cuda"}:
        raise ValueError("device must explicitly be cpu or cuda.")
    if not isinstance(cfg.get("cost") or {}, dict):
        raise ValueError("cost must be a mapping.")
    rate = (cfg.get("cost") or {}).get("hourly_rate_usd")
    if rate is not None and (type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0):
        raise ValueError("hourly_rate_usd must be null or a nonnegative finite number.")
    if not isinstance(cfg["vad"], dict) or type(cfg["beam_size"]) is not int or cfg["beam_size"] < 1:
        raise ValueError("vad must be a mapping and beam_size a positive integer.")
    inference_settings(cfg)
    return cfg


def inference_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    mode = cfg.get("inference_mode", "sequential")
    size = cfg.get("inference_batch_size", 1)
    if mode not in ("sequential", "batched"):
        raise ValueError("inference_mode must be sequential or batched.")
    if type(size) is not int or not 1 <= size <= 32:
        raise ValueError("inference_batch_size must be an integer between 1 and 32.")
    if mode == "sequential" and size != 1:
        raise ValueError("Sequential inference requires inference_batch_size: 1.")
    if mode == "batched":
        if cfg.get("condition_on_previous_text", False) is not False:
            raise ValueError("Batched inference requires condition_on_previous_text: false; upstream ignores this option.")
        if (cfg.get("vad") or {}).get("enabled", True) is not True:
            raise ValueError("Batched inference requires vad.enabled: true for long audio.")
    return {"inference_mode": mode, "inference_batch_size": size}


def resolve_paths(cfg: dict[str, Any]) -> BatchPaths:
    batch_id = safe_id(str(cfg["batch_id"]))
    pipeline_root = os.environ.get("PIPELINE_DATA_ROOT")
    podcast_root = os.environ.get("PODCAST_DATA_ROOT")
    output_root = expand_path(cfg.get("output_root")) or (private_path(pipeline_root) / "transcription" / "batches" if pipeline_root else None)
    audio_root = expand_path(cfg.get("audio_root")) or (Path(podcast_root) / "audio" if podcast_root else None)
    transcript_root = expand_path(cfg.get("transcript_root")) or (private_path(podcast_root) / "transcripts" / "raw" / batch_id if podcast_root else None)
    if output_root is None or audio_root is None or transcript_root is None:
        raise RuntimeError("Set output_root/audio_root/transcript_root or PODCAST_DATA_ROOT and PIPELINE_DATA_ROOT.")
    output_root, audio_root, transcript_root = map(private_path, (output_root, audio_root, transcript_root))
    child_path(output_root, batch_id)
    checkpoint = expand_path(cfg.get("checkpoint_path")) or child_path(output_root, batch_id, "checkpoint.json")
    if not checkpoint.is_relative_to(output_root):
        raise ValueError("Checkpoint must stay under output_root.")
    return BatchPaths(output_root=output_root, audio_root=audio_root, transcript_root=transcript_root, checkpoint_path=checkpoint)


def load_checkpoint(path: Path) -> dict[str, Any]:
    if path.exists():
        checkpoint = read_json(path)
        if not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != "transcription_checkpoint.v2" or not isinstance(checkpoint.get("jobs"), dict):
            raise ValueError("Unsupported checkpoint. Use a new batch ID for the formal pipeline.")
        return checkpoint
    return {"schema_version": "transcription_checkpoint.v2", "jobs": {}, "created_at": utc_now(), "updated_at": utc_now()}


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
    inference = inference_settings(cfg)
    return {
        "schema_version": "transcription_raw.v1",
        "terminology_sha256": sha256_file(TERMINOLOGY),
        "model_name": cfg.get("model_name"),
        "device": cfg.get("device"),
        "compute_type": cfg.get("compute_type"),
        "language": cfg.get("language"),
        "beam_size": cfg.get("beam_size"),
        "vad": cfg.get("vad"),
        "condition_on_previous_text": cfg.get("condition_on_previous_text"),
        "word_timestamps": cfg.get("word_timestamps"),
        **({"model_revision": cfg["model_revision"]} if cfg.get("model_revision") else {}),
        # Preserve sequential checkpoints, but never skip across decoder modes/sizes.
        **({**inference, "batched_profile": "timestamped_v1"} if inference["inference_mode"] == "batched" else {}),
    }


def resolve_manifest_rows(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = expand_path(cfg.get("manifest_path"))
    return load_manifest(manifest)


def resolve_jobs(cfg: dict[str, Any], rows: list[dict[str, Any]], max_episodes: int | None = None) -> list[dict[str, Any]]:
    if max_episodes is not None and max_episodes < 1:
        raise ValueError("max_episodes must be positive.")
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
    if not jobs:
        raise ValueError("Select at least one episode.")
    return jobs


def transcript_path(paths: BatchPaths, episode_id: str) -> Path:
    return child_path(paths.transcript_root, safe_id(episode_id), "raw_transcript.json")


def run_metadata_path(paths: BatchPaths, episode_id: str) -> Path:
    return child_path(paths.transcript_root, safe_id(episode_id), "raw_run_metadata.json")


def audio_path(paths: BatchPaths, episode_id: str) -> Path:
    return child_path(paths.audio_root, safe_id(episode_id), "episode.mp3")


def completed_output(paths: BatchPaths, episode_id: str, cfg: dict[str, Any] | None = None) -> bool:
    tpath = transcript_path(paths, episode_id)
    if not tpath.exists():
        return False
    try:
        validate_transcript(tpath, episode_id)
        metadata = read_json(tpath)["metadata"]
        if cfg is not None and metadata.get("config_fingerprint") != config_fingerprint(cfg):
            return False
        return bool(metadata.get("model") and metadata.get("source_episode_metadata") and metadata.get("audio_sha256"))
    except (ValueError, KeyError, OSError):
        return False


def check_compatibility(cfg: dict[str, Any], checkpoint: dict[str, Any]) -> None:
    if checkpoint.get("batch_id", cfg["batch_id"]) != cfg["batch_id"]:
        raise ValueError("Checkpoint batch ID mismatch.")
    if checkpoint.get("config_fingerprint", config_fingerprint(cfg)) != config_fingerprint(cfg):
        raise ValueError("Config mismatch; select the original settings or a new batch/output version.")


def plan(cfg: dict[str, Any], *, max_episodes: int | None = None) -> dict[str, Any]:
    paths = resolve_paths(cfg)
    rows = resolve_manifest_rows(cfg)
    checkpoint = load_checkpoint(paths.checkpoint_path)
    check_compatibility(cfg, checkpoint)
    jobs = []
    for item in resolve_jobs(cfg, rows, max_episodes):
        episode_id = item["episode_id"]
        existing = completed_output(paths, episode_id, cfg)
        status = checkpoint.get("jobs", {}).get(episode_id, {}).get("status")
        collision = (transcript_path(paths, episode_id).exists() or run_metadata_path(paths, episode_id).exists()) and not existing
        action = "blocked_existing_output" if collision else ("skip_completed" if existing and cfg.get("skip_existing", True) else "run")
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
    return {"batch_id": cfg["batch_id"], "jobs": jobs, "checkpoint_path": str(paths.checkpoint_path),
            **inference_settings(cfg)}


def validate_audio(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError(f"audio missing or empty: {path}")
    meta = inspect_audio(path)
    return {**meta, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def download_audio(episode: dict[str, Any], dest: Path, timeout: int, retries: int,
                   storage: StorageGuard | None = None) -> dict[str, Any]:
    if dest.exists() and dest.stat().st_size > 0:
        return {"skipped_existing": True, **validate_audio(dest)}
    url = episode.get("audio_url")
    if not url:
        raise ValueError("episode missing audio_url")
    if storage:
        storage.admit_download()
    result = stream_download(str(url), dest, timeout=timeout, retries=retries,
                             **({"max_bytes": storage.max_download_bytes, "check_space": storage.check_progress} if storage else {}))
    return {**result, **validate_audio(dest)}


def ensure_cuda_if_requested(device: str) -> None:
    if device != "cuda":
        return
    if not shutil.which("nvidia-smi"):
        raise RuntimeError("CUDA requested but nvidia-smi is unavailable; choose CPU explicitly or fix the GPU environment.")
    code, out, err = run_command(["nvidia-smi", "-L"], timeout=10)
    if code != 0:
        raise RuntimeError(f"CUDA requested but nvidia-smi failed: {err or out}")


def serialize_segment(segment: Any, segment_id: int, word_timestamps: bool = False) -> dict[str, Any]:
    item = {"segment_id": segment_id, "start": float(segment.start), "end": float(segment.end), "text": segment.text}
    # ASR diagnostics help prioritize review; they are not calibrated accuracy scores.
    for key in ("avg_logprob", "no_speech_prob", "compression_ratio"):
        value = getattr(segment, key, None)
        item[key] = float(value) if type(value) in (int, float) and math.isfinite(value) else None
    if word_timestamps:
        item["words"] = [{"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                         for w in (getattr(segment, "words", None) or [])]
    return item


def transcribe_audio(audio: Path, cfg: dict[str, Any], episode: dict[str, Any],
                     storage: StorageGuard | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    worker_code_commit = os.environ.get("TRANSCRIPTION_CODE_COMMIT")
    if worker_code_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", worker_code_commit):
        raise ValueError("TRANSCRIPTION_CODE_COMMIT must identify the deployed 40-character Git commit.")
    inference = inference_settings(cfg)
    ensure_cuda_if_requested(str(cfg.get("device")))
    try:
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model
        if inference["inference_mode"] == "batched":
            from faster_whisper import BatchedInferencePipeline
        import psutil
    except Exception as exc:
        raise RuntimeError(f"Missing transcription dependency: {exc}") from exc
    process = psutil.Process(os.getpid())
    peak_rss = process.memory_info().rss
    vram = VramMonitor(str(cfg.get("device")) == "cuda")
    start = time.perf_counter()
    vram.start()
    try:
        model_load_start = time.perf_counter()
        model_path = str(cfg["model_name"])
        if not Path(model_path).is_dir():
            model_path = download_model(model_path, local_files_only=bool(cfg.get("local_files_only", False)),
                                        revision=cfg.get("model_revision"))
        model = WhisperModel(model_path, device=str(cfg["device"]), compute_type=str(cfg["compute_type"]),
                             local_files_only=True)
        decoder = BatchedInferencePipeline(model=model) if inference["inference_mode"] == "batched" else model
        model_load_seconds = time.perf_counter() - model_load_start
        if storage:
            storage.check(64_000_000)
        segments, info = decoder.transcribe(
            str(audio),
            language=cfg.get("language"),
            vad_filter=bool((cfg.get("vad") or {}).get("enabled", True)),
            vad_parameters={"min_silence_duration_ms": int((cfg.get("vad") or {}).get("min_silence_duration_ms", 500))},
            beam_size=int(cfg.get("beam_size", 5)),
            initial_prompt=terminology_prompt(),
            condition_on_previous_text=bool(cfg.get("condition_on_previous_text", False)),
            word_timestamps=bool(cfg.get("word_timestamps", False)),
            # The batched decoder defaults to text-only tokens, unlike WhisperModel.
            without_timestamps=False,
            **({"batch_size": inference["inference_batch_size"]} if inference["inference_mode"] == "batched" else {}),
        )
        out = []
        for idx, seg in enumerate(segments):
            if storage and idx % 25 == 0:
                storage.check_progress(64_000_000)
            peak_rss = max(peak_rss, process.memory_info().rss)
            out.append(serialize_segment(seg, idx, bool(cfg.get("word_timestamps"))))
    finally:
        vram.stop()
    total_seconds = time.perf_counter() - start
    duration = inspect_audio(audio).get("duration_seconds")
    metadata = {
        "episode_id": episode.get("episode_id"),
        "backend": "faster-whisper",
        "worker_code_commit": worker_code_commit,
        "model": cfg.get("model_name"),
        "model_revision": Path(model_path).name if Path(model_path).parent.name == "snapshots" else cfg.get("model_revision"),
        "device": cfg.get("device"),
        "compute_type": cfg.get("compute_type"),
        "language": cfg.get("language"),
        "beam_size": cfg.get("beam_size"),
        "vad": cfg.get("vad"),
        "condition_on_previous_text": cfg.get("condition_on_previous_text"),
        "word_timestamps": cfg.get("word_timestamps"),
        "detected_language": getattr(info, "language", None),
        **inference,
        "decoding_profile": "timestamped_v1" if inference["inference_mode"] == "batched" else "sequential_default_v1",
        "decoding_caveats": (["Batched decoding uses independent VAD chunks and does not apply sequential temperature fallback or silence/log-probability rejection thresholds. Re-review content and timestamps."]
                             if inference["inference_mode"] == "batched" else []),
        "language_probability": getattr(info, "language_probability", None),
        "audio_duration_seconds": duration,
        "model_load_seconds": model_load_seconds,
        "transcription_seconds": total_seconds - model_load_seconds,
        "total_episode_seconds": total_seconds,
        "realtime_factor": total_seconds / duration if duration else None,
        "peak_ram_mb": round(peak_rss / 1024 / 1024, 2),
        "peak_vram_mb": vram.peak_mb,
        "vram_measurement_scope": vram.measurement_scope,
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
    if not shutil.which("nvidia-smi"):
        return None
    code, out, _ = run_command(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], timeout=5)
    return out.splitlines()[0].strip() if code == 0 and out else None


def write_transcript(paths: BatchPaths, episode_id: str, segments: list[dict[str, Any]], metadata: dict[str, Any], episode: dict[str, Any]) -> None:
    from .quality import validate_payload

    payload = {"schema_version": "transcription_raw.v1", "metadata": {**metadata, "source_episode_metadata": episode}, "segments": segments}
    validate_payload(payload, episode_id)
    atomic_write_json(transcript_path(paths, episode_id), payload, overwrite=False)
    atomic_write_json(run_metadata_path(paths, episode_id), payload["metadata"], overwrite=False)


def record_failure(checkpoint: dict[str, Any], paths: BatchPaths, episode_id: str, stage: str, exc: Exception, attempt: int, retryable: bool) -> None:
    # Third-party exceptions can contain authenticated URLs; never persist them.
    failure = {"episode_id": episode_id, "stage": stage, "error_type": exc.__class__.__name__, "message": f"{stage} failed ({type(exc).__name__}); verify inputs/settings before retry.", "attempt": attempt, "timestamp": utc_now(), "retryable": retryable}
    update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "failed", "stage": stage, "last_error": failure})


def cleanup_completed_audio(cfg: dict[str, Any], paths: BatchPaths, checkpoint: dict[str, Any], episode_id: str) -> dict[str, Any]:
    job = checkpoint["jobs"][episode_id]
    if cfg.get("audio_policy") != "delete_on_success" or not job.get("audio_owned_by_batch"):
        return job
    target = audio_path(paths, episode_id)
    try:
        if target.exists():
            meta = read_json(transcript_path(paths, episode_id))["metadata"]
            if sha256_file(target) != meta["audio_sha256"]:
                raise ValueError("Audio replaced since transcription")
            target.unlink()
        return update_job(checkpoint, paths.checkpoint_path, episode_id, {"audio_cleanup": "deleted_or_absent", "cleanup_warning": None})
    except (OSError, ValueError):
        return update_job(checkpoint, paths.checkpoint_path, episode_id, {"cleanup_warning": "Validated transcript retained; audio cleanup requires inspection."})


def run_job(cfg: dict[str, Any], paths: BatchPaths, checkpoint: dict[str, Any], item: dict[str, Any], *, retry_failed: bool = False,
            storage: StorageGuard | None = None) -> dict[str, Any]:
    episode_id = item["episode_id"]
    episode = item["episode"]
    fingerprint = config_fingerprint(cfg)
    existing = checkpoint.get("jobs", {}).get(episode_id, {})
    check_compatibility(cfg, checkpoint)
    if existing.get("config_fingerprint", fingerprint) != fingerprint:
        raise ValueError(f"Config mismatch for {episode_id}; use a separate batch/output version.")
    if completed_output(paths, episode_id, cfg) and cfg.get("skip_existing", True):
        meta = read_json(transcript_path(paths, episode_id))["metadata"]
        if meta.get("source_episode_metadata") != episode:
            raise ValueError("Source episode metadata changed; use a new batch version.")
        sidecar = run_metadata_path(paths, episode_id)
        if sidecar.exists() and read_json(sidecar) != meta:
            raise ValueError("Run metadata disagrees with transcript; preserve files for review.")
        if not sidecar.exists():
            atomic_write_json(sidecar, meta, overwrite=False)
        validation = validate_transcript(transcript_path(paths, episode_id), episode_id)
        update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "completed", "stage": "output_validated", "validation": validation, "config_fingerprint": fingerprint})
        update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "completed", "stage": "job_completed", "last_action": "skipped", "last_error": None, "output": str(transcript_path(paths, episode_id))})
        return cleanup_completed_audio(cfg, paths, checkpoint, episode_id)
    if transcript_path(paths, episode_id).exists() or run_metadata_path(paths, episode_id).exists():
        raise ValueError("Existing incomplete/incompatible output will not be overwritten; use a new output version.")
    if existing.get("status") == "failed" and not retry_failed:
        return existing
    attempts = int(cfg.get("retry_count", 2)) + 1
    previous_attempt = existing.get("attempt", 0)
    for attempt in range(1, attempts + 1):
        stage = "metadata_resolved"
        started = time.perf_counter()
        try:
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "pending", "stage": "metadata_resolved", "config_fingerprint": fingerprint, "attempt": previous_attempt + attempt})
            stage = "audio_download"
            if storage:
                storage.check(64_000_000)
            owned_audio = bool(checkpoint.get("jobs", {}).get(episode_id, {}).get("audio_owned_by_batch")) or not audio_path(paths, episode_id).exists()
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "downloading"})
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"audio_owned_by_batch": owned_audio})
            audio_meta = download_audio(episode, audio_path(paths, episode_id), int(cfg.get("download_timeout_seconds", 60)), 1, storage)
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "downloaded", "audio_metadata": audio_meta, "stage": "audio_downloaded"})
            stage = "transcribing"
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "transcribing", "stage": "transcribing"})
            segments, metadata = transcribe_audio(audio_path(paths, episode_id), cfg, episode, storage)
            metadata.update({"episode_id": episode_id, "model": cfg["model_name"], "language": cfg.get("language"),
                             "audio_sha256": audio_meta["sha256"], "audio_duration_seconds": audio_meta["duration_seconds"],
                             "config_fingerprint": fingerprint, "download_seconds": audio_meta.get("download_runtime_seconds", 0),
                             "total_episode_seconds": time.perf_counter() - started, "retry_count": previous_attempt + attempt - 1})
            transcription_seconds = metadata.get("transcription_seconds")
            metadata["realtime_factor"] = transcription_seconds / audio_meta["duration_seconds"] if transcription_seconds is not None else None
            rate = (cfg.get("cost") or {}).get("hourly_rate_usd")
            metadata["hourly_rate_usd"] = rate
            metadata["estimated_job_cost_usd"] = rate * metadata["total_episode_seconds"] / 3600 if rate is not None else None
            metadata["cost_scope"] = "episode processing estimate only; excludes idle time and storage"
            stage = "persist_output"
            if storage:
                storage.check(64_000_000)
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "transcribing", "stage": "transcription_completed", "segment_count": len(segments)})
            write_transcript(paths, episode_id, segments, metadata, episode)
            validation = validate_transcript(transcript_path(paths, episode_id), episode_id)
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "transcribing", "stage": "output_validated", "validation": validation})
            update_job(checkpoint, paths.checkpoint_path, episode_id, {"status": "completed", "stage": "job_completed", "last_action": "transcribed", "last_error": None, "completed_at": utc_now(), "output": str(transcript_path(paths, episode_id))})
            return cleanup_completed_audio(cfg, paths, checkpoint, episode_id)
        except Exception as exc:
            retryable = not isinstance(exc, StorageLimitError) and attempt < attempts and not transcript_path(paths, episode_id).exists()
            record_failure(checkpoint, paths, episode_id, stage, exc, previous_attempt + attempt, retryable)
            if not retryable:
                return checkpoint["jobs"][episode_id]
            time.sleep(min(2**attempt, 30))
    return checkpoint["jobs"][episode_id]


def build_summary(cfg: dict[str, Any], checkpoint: dict[str, Any], paths: BatchPaths) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for job in checkpoint.get("jobs", {}).values():
        status = job.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    summary = {"batch_id": cfg["batch_id"], "status_counts": counts,
               "skipped_completed_count": sum(j.get("last_action") == "skipped" for j in checkpoint.get("jobs", {}).values()),
               "updated_at": utc_now(), "checkpoint_path": str(paths.checkpoint_path)}
    atomic_write_json(paths.output_root / str(cfg["batch_id"]) / "summary.json", summary)
    failures = [job.get("last_error") for job in checkpoint.get("jobs", {}).values() if job.get("last_error")]
    atomic_write_json(paths.output_root / str(cfg["batch_id"]) / "failures.json", failures)
    return summary


def validate_batch_outputs(cfg: dict[str, Any]) -> dict[str, Any]:
    paths = resolve_paths(cfg)
    checkpoint = load_checkpoint(paths.checkpoint_path)
    check_compatibility(cfg, checkpoint)
    errors: list[dict[str, Any]] = []
    validated_jobs: list[dict[str, Any]] = []
    expected = resolve_jobs(cfg, resolve_manifest_rows(cfg))
    for item in expected:
        episode_id = item["episode_id"]
        job = checkpoint.get("jobs", {}).get(episode_id, {})
        status = job.get("status")
        if status != "completed":
            errors.append({"episode_id": episode_id, "error": "Job is not completed."})
            validated_jobs.append({"episode_id": episode_id, "status": status, "valid": False})
            continue
        tpath = transcript_path(paths, episode_id)
        try:
            validation = validate_transcript(tpath, episode_id)
            if not completed_output(paths, episode_id, cfg):
                raise ValueError("Missing metadata or config mismatch.")
            if read_json(run_metadata_path(paths, episode_id)) != read_json(tpath)["metadata"]:
                raise ValueError("Run metadata mismatch.")
            stages = {entry["stage"] for entry in job.get("stage_history", [])}
            if not {"output_validated", "job_completed"} <= stages:
                raise ValueError("Missing completion stages.")
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
    if dry_run:
        return plan(cfg, max_episodes=max_episodes)
    paths = resolve_paths(cfg)
    jobs = resolve_jobs(cfg, resolve_manifest_rows(cfg), max_episodes)
    paths.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    # One local worker per checkpoint; no distributed scheduler is implied.
    with paths.checkpoint_path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("This batch is already running.") from exc
        checkpoint = load_checkpoint(paths.checkpoint_path)
        check_compatibility(cfg, checkpoint)
        checkpoint.update({"batch_id": cfg["batch_id"], "config_fingerprint": config_fingerprint(cfg)})
        storage = StorageGuard(cfg.get("storage") or {}, audio_root=paths.audio_root,
                               write_roots=[paths.output_root, paths.transcript_root, paths.audio_root],
                               budget_file=paths.output_root / "storage_budget.json")
        checkpoint["storage"] = storage.check()
        atomic_write_json(paths.checkpoint_path, checkpoint)
        batch_manifest = {"batch_id": cfg["batch_id"], "config_fingerprint": config_fingerprint(cfg), "jobs": [{"episode_id": j["episode_id"], "title": j["episode"].get("episode_title")} for j in jobs], "created_at": utc_now()}
        atomic_write_json(paths.output_root / str(cfg["batch_id"]) / "batch_manifest.json", batch_manifest)
        for item in jobs:
            try:
                job = run_job(cfg, paths, checkpoint, item, retry_failed=retry_failed, storage=storage)
                if (job.get("last_error") or {}).get("error_type") == "StorageLimitError":
                    break
            except Exception as exc:
                record_failure(checkpoint, paths, item["episode_id"], "preflight", exc, 0, False)
        return build_summary(cfg, checkpoint, paths)


def status(cfg: dict[str, Any]) -> dict[str, Any]:
    paths = resolve_paths(cfg)
    checkpoint = load_checkpoint(paths.checkpoint_path)
    check_compatibility(cfg, checkpoint)
    return {"batch_id": cfg["batch_id"], "checkpoint_exists": paths.checkpoint_path.exists(), "jobs": checkpoint["jobs"]}
