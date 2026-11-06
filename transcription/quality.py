from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .common import read_json, sha256_file, utc_now


def validate_payload(payload: Any, expected_episode: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Transcript must be a JSON object.")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("episode_id"), str) or not metadata["episode_id"].strip():
        raise ValueError("Transcript metadata.episode_id is required.")
    episode = metadata["episode_id"]
    if expected_episode is not None and episode != expected_episode:
        raise ValueError("Transcript episode ID mismatch.")
    source = metadata.get("source_episode_metadata") or {}
    if not isinstance(source, dict):
        raise ValueError("Source episode metadata must be an object.")
    if source.get("episode_id", episode) != episode:
        raise ValueError("Source metadata episode ID mismatch.")
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("Transcript has no segments.")
    duration = metadata.get("audio_duration_seconds")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))
                                 or not math.isfinite(duration) or duration <= 0):
        raise ValueError("Invalid audio duration.")
    previous_start = -1.0
    seen: set[int] = set()
    last_end = 0.0
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Segment must be an object.")
        sid = segment.get("segment_id")
        if type(sid) is not int or sid < 0 or sid in seen:
            raise ValueError("Segment IDs must be unique nonnegative integers.")
        seen.add(sid)
        if not isinstance(segment.get("text"), str) or not segment["text"].strip():
            raise ValueError(f"Empty text in segment {sid}.")
        start, end = segment.get("start"), segment.get("end")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)):
            raise ValueError(f"Non-finite or nonnumeric timestamp in segment {sid}.")
        if start < 0 or end < start or start < previous_start:
            raise ValueError(f"Invalid or unsorted timestamps in segment {sid}.")
        if duration is not None and end > duration + 1.0:
            raise ValueError(f"Timestamp exceeds audio duration in segment {sid}.")
        previous_start = start
        last_end = max(last_end, end)
    warnings = []
    if duration is None:
        warnings.append("Audio duration unavailable; coverage cannot be verified.")
    elif last_end < duration * 0.8:
        warnings.append("Transcript ends well before audio; review missing speech versus silence.")
    if not (metadata.get("language") or metadata.get("detected_language")):
        warnings.append("Language metadata missing.")
    if not metadata.get("model"):
        warnings.append("Model metadata missing.")
    return {
        "episode_id": episode, "segment_count": len(segments), "last_timestamp": last_end,
        "audio_duration_seconds": duration, "warnings": warnings,
        "structurally_valid": True, "validated_at": utc_now(),
    }


def validate_transcript(path: Path, expected_episode: str | None = None) -> dict[str, Any]:
    try:
        payload = read_json(path)
    except (ValueError, OSError) as exc:
        raise ValueError("Invalid or missing transcript JSON.") from exc
    return validate_payload(payload, expected_episode)


def quality_gate(path: Path, expected_episode: str | None = None, review_path: Path | None = None) -> dict[str, Any]:
    result = validate_transcript(path, expected_episode)
    digest = sha256_file(path)
    accepted = False
    if review_path is not None:
        review = read_json(review_path)
        if not isinstance(review, dict) or review.get("schema_version") != "transcript_review.v1":
            raise ValueError("Expected transcript_review.v1 quality review.")
        if review.get("episode_id") != result["episode_id"] or review.get("transcript_sha256") != digest:
            raise ValueError("Quality review does not match this transcript version.")
        if review.get("decision") not in {"accept", "retranscribe", "uncertain"}:
            raise ValueError("Unknown transcript review decision.")
        for key in ("reviewer", "reviewed_at", "method"):
            if not isinstance(review.get(key), str) or not review[key].strip():
                raise ValueError(f"Quality review requires {key}.")
        if not isinstance(review.get("unresolved_critical_issues"), list):
            raise ValueError("Quality review requires unresolved_critical_issues.")
        sampled = review.get("sampled_segment_ids")
        valid_ids = {s["segment_id"] for s in read_json(path)["segments"]}
        if not isinstance(sampled, list) or not sampled or any(type(i) is not int or i not in valid_ids for i in sampled):
            raise ValueError("Quality review must identify valid sampled segment IDs.")
        accepted = review["decision"] == "accept" and not review["unresolved_critical_issues"]
    return {
        "schema_version": "transcript_quality.v1", **result,
        "transcript_sha256": digest,
        "content_quality": "accepted_review" if accepted else "review_required",
        "ready_for_extraction": accepted,
        "scope": "Structural checks plus explicit source-version review; not automatic ASR accuracy certification.",
    }
