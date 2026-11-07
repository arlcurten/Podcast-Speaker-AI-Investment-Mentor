"""Read-only, same-audio run comparison. Never certify ASR accuracy from agreement."""
from __future__ import annotations

import math
import re
from pathlib import Path

from .common import atomic_write_json, private_path, read_json, sha256_file, utc_now
from .quality import validate_payload


def _positive(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else None


def _summary(payload: dict, digest: str) -> dict:
    metadata, segments = payload["metadata"], payload["segments"]
    seconds = _positive(metadata.get("transcription_seconds"))
    duration = _positive(metadata.get("audio_duration_seconds"))
    end = max(s["end"] for s in segments)
    speech_interval_seconds, covered_end = 0.0, 0.0
    for segment in segments:
        speech_interval_seconds += max(0.0, segment["end"] - max(covered_end, segment["start"]))
        covered_end = max(covered_end, segment["end"])
    return {
        "transcript_sha256": digest,
        "inference_mode": metadata.get("inference_mode", "sequential"),
        "inference_batch_size": metadata.get("inference_batch_size", 1),
        "decoding_caveats": metadata.get("decoding_caveats", []),
        "segment_count": len(segments),
        "text_character_count": sum(len(s["text"].strip()) for s in segments),
        "first_timestamp": segments[0]["start"], "last_timestamp": end,
        "audio_duration_seconds": duration,
        "last_timestamp_fraction": end / duration if duration else None,
        "transcribed_interval_seconds": speech_interval_seconds,
        "download_seconds": metadata.get("download_seconds"),
        "model_load_seconds": metadata.get("model_load_seconds"),
        "transcription_seconds": seconds,
        "total_episode_seconds": metadata.get("total_episode_seconds"),
        "realtime_factor": seconds / duration if seconds and duration else None,
        "peak_vram_mb": metadata.get("peak_vram_mb"),
        "retry_count": metadata.get("retry_count"),
        "hourly_rate_usd": metadata.get("hourly_rate_usd"),
        "estimated_job_cost_usd": metadata.get("estimated_job_cost_usd"),
        "cost_scope": metadata.get("cost_scope"),
    }


def _controls(metadata: dict) -> dict:
    fingerprint = metadata.get("config_fingerprint")
    fingerprint = fingerprint if isinstance(fingerprint, dict) else {}
    result = {key: fingerprint.get(key) for key in (
        "schema_version", "terminology_sha256", "model_name", "device", "compute_type",
        "language", "beam_size", "vad", "condition_on_previous_text", "word_timestamps",
    )}
    # Old formal runs may record a resolved revision only in run metadata.
    result.update(model_revision=metadata.get("model_revision"), gpu_model=metadata.get("gpu_model"),
                  package_versions=metadata.get("package_versions"))
    return result


def compare_transcripts(baseline: Path, candidate: Path, output: Path | None = None) -> dict:
    baseline, candidate = private_path(baseline), private_path(candidate)
    if baseline == candidate:
        raise ValueError("Choose two separate transcript versions.")
    if output is not None:
        output = private_path(output)
        if output.exists():
            raise FileExistsError("Comparison output exists; use a new private report path.")
    left, right = read_json(baseline), read_json(candidate)
    validations = [validate_payload(left), validate_payload(right)]
    if left.get("schema_version") != "transcription_raw.v1" or right.get("schema_version") != "transcription_raw.v1":
        raise ValueError("Comparison requires formal transcription_raw.v1 outputs.")
    a, b = left["metadata"], right["metadata"]
    audio_hash = a.get("audio_sha256")
    if not isinstance(audio_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", audio_hash):
        raise ValueError("Baseline requires a valid audio SHA-256.")
    if b.get("audio_sha256") != audio_hash or a["episode_id"] != b["episode_id"]:
        raise ValueError("Compare the same episode and audio SHA-256; different sources are not an A/B pair.")
    summaries = [_summary(left, sha256_file(baseline)), _summary(right, sha256_file(candidate))]
    controls = [_controls(a), _controls(b)]
    differences = {key: {"baseline": controls[0][key], "candidate": controls[1][key]}
                   for key in controls[0] if controls[0][key] != controls[1][key]}
    missing = {key for control in controls for key, value in control.items() if value is None or value == "" or value == {}}
    comparable = not differences and not missing
    times = [s["transcription_seconds"] for s in summaries]
    notes = [
        "Same audio is not a reference transcript. Text agreement, segment counts and time coverage are not word-error rate or content acceptance.",
        "Batched and sequential decoders differ in VAD chunking and fallback behavior. Review names, numbers, negation, omissions and timestamp alignment against audio.",
        "Timings are individual observations, not a multi-run benchmark; exclude setup, idle time and prior failed attempts unless explicitly recorded.",
        "Transcribed intervals measure timestamp spans, not proven speech recall. Use review-packet on both versions for listening checks.",
    ]
    if not comparable:
        notes.append("Settings/hardware/version controls differ or are missing; no batching-only speedup claim is available.")
    if not all(times):
        notes.append("Positive transcription timing is missing; no speedup can be computed.")
    result = {
        "schema_version": "transcription_comparison.v1", "created_at": utc_now(),
        "episode_id": a["episode_id"], "audio_sha256": audio_hash,
        "baseline": summaries[0], "candidate": summaries[1],
        "validation_warnings": {"baseline": validations[0]["warnings"], "candidate": validations[1]["warnings"]},
        "setup_controls_match": comparable, "control_differences": differences,
        "missing_controls": sorted(missing),
        "observed_transcription_speedup": times[0] / times[1] if comparable and all(times) else None,
        "text_identical_ignoring_whitespace": "".join("".join(s["text"].split()) for s in left["segments"]) == "".join("".join(s["text"].split()) for s in right["segments"]),
        "content_review_required": True, "ready_for_extraction": False,
        "notes": notes,
    }
    if output is not None:
        atomic_write_json(output, result, overwrite=False)
    return result
