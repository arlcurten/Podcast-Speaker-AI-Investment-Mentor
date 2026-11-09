from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .common import private_path, read_json, sha256_file, utc_now
from .annotations import load_annotations


class TranscriptValidationError(ValueError):
    """A source-independent diagnostic that is safe to include in job reports."""


def validate_payload(payload: Any, expected_episode: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TranscriptValidationError("Transcript must be a JSON object.")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("episode_id"), str) or not metadata["episode_id"].strip():
        raise TranscriptValidationError("Transcript metadata.episode_id is required.")
    episode = metadata["episode_id"]
    if expected_episode is not None and episode != expected_episode:
        raise TranscriptValidationError("Transcript episode ID mismatch.")
    source = metadata.get("source_episode_metadata") or {}
    if not isinstance(source, dict):
        raise TranscriptValidationError("Source episode metadata must be an object.")
    if source.get("episode_id", episode) != episode:
        raise TranscriptValidationError("Source metadata episode ID mismatch.")
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise TranscriptValidationError("Transcript has no segments.")
    duration = metadata.get("audio_duration_seconds")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))
                                 or not math.isfinite(duration) or duration <= 0):
        raise TranscriptValidationError("Invalid audio duration.")
    previous_start = -1.0
    seen: set[int] = set()
    last_end = 0.0
    for segment in segments:
        if not isinstance(segment, dict):
            raise TranscriptValidationError("Segment must be an object.")
        sid = segment.get("segment_id")
        if type(sid) is not int or sid < 0 or sid in seen:
            raise TranscriptValidationError("Segment IDs must be unique nonnegative integers.")
        seen.add(sid)
        if not isinstance(segment.get("text"), str) or not segment["text"].strip():
            raise TranscriptValidationError(f"Empty text in segment {sid}.")
        start, end = segment.get("start"), segment.get("end")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)):
            raise TranscriptValidationError(f"Non-finite or nonnumeric timestamp in segment {sid}.")
        if start < 0 or end < start or start < previous_start:
            raise TranscriptValidationError(f"Invalid or unsorted timestamps in segment {sid}.")
        if duration is not None and end > duration + 1.0:
            raise TranscriptValidationError(f"Timestamp exceeds audio duration in segment {sid}.")
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
    if metadata.get("source_recovery"):
        warnings.append("Recovered decoder tail requires source-audio review; see source_recovery metadata.")
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


def recover_padding_tail(path: Path, expected_episode: str, expected_sha256: str, *,
                         maximum_tail_lead_seconds: float = 1.0) -> dict[str, Any]:
    """Explicit recovery of one padding-like tail, not transcript quality approval.

    Return a new payload; never overwrite the rejected decoder output. Callers
    must publish into a new output version and retain the rejected source.
    """
    if (type(maximum_tail_lead_seconds) not in (int, float)
            or not math.isfinite(maximum_tail_lead_seconds)
            or not 0 < maximum_tail_lead_seconds <= 2):
        raise ValueError("Explicit tail-recovery lead must be greater than zero and at most two seconds.")
    path = private_path(path)
    digest = sha256_file(path)
    if digest != expected_sha256:
        raise ValueError("Rejected output checksum mismatch.")
    payload = read_json(path)
    try:
        validate_payload(payload, expected_episode)
    except TranscriptValidationError:
        pass
    else:
        raise ValueError("Valid output does not need tail recovery.")
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    segments = payload.get("segments", []) if isinstance(payload, dict) else []
    if not isinstance(metadata, dict):
        raise ValueError("Tail recovery requires transcript metadata.")
    duration = metadata.get("audio_duration_seconds")
    if (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0
            or not isinstance(segments, list) or len(segments) < 2
            or not isinstance(segments[-1], dict)):
        raise ValueError("Tail recovery requires valid duration and retained speech segments.")
    tail = segments[-1]
    start, end, silence = (tail.get(k) for k in ("start", "end", "no_speech_prob"))
    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end, silence))
            or not duration - maximum_tail_lead_seconds <= start <= duration
            or not duration + 1 < end <= duration + 30
            or not 0.8 <= silence <= 1):
        raise ValueError("Tail does not meet bounded padding-recovery criteria; inspect or retranscribe.")
    if type(tail.get("segment_id")) is not int or tail["segment_id"] < 0:
        raise ValueError("Invalid tail segment identity.")
    if any(s.get("segment_id") == tail["segment_id"] for s in segments[:-1] if isinstance(s, dict)):
        raise ValueError("Duplicate tail segment identity.")
    if metadata.get("source_recovery"):
        raise ValueError("Repeated tail recovery is not supported.")
    recovered = {**payload, "segments": segments[:-1], "metadata": {
        **metadata,
        "source_recovery": {
            "method": "quarantine_padding_tail.v1",
            "maximum_tail_lead_seconds": maximum_tail_lead_seconds,
            "rejected_output_sha256": digest,
            "excluded_segments": [tail],
            "recovered_at": utc_now(),
            "content_review_accepted": False,
            "warning": "One out-of-audio, high-no-speech tail quarantined; review final audio region. Text and retained source IDs/timestamps are unchanged.",
        },
    }}
    validate_payload(recovered, expected_episode)
    # Guard against a source changing between checksum and read.
    if sha256_file(path) != digest:
        raise ValueError("Rejected output changed during recovery.")
    return recovered


def quality_gate(path: Path, expected_episode: str | None = None, review_path: Path | None = None) -> dict[str, Any]:
    result = validate_transcript(path, expected_episode)
    digest = sha256_file(path)
    accepted = False
    annotation_meta = {}
    if review_path is not None:
        review_hash = sha256_file(review_path)
        review = read_json(review_path)
        if (not isinstance(review, dict) or not isinstance(review.get("schema_version"), str)
                or review["schema_version"] not in {"transcript_review.v1", "transcript_review.v2"}):
            raise ValueError("Expected transcript_review.v1 or transcript_review.v2 quality review.")
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
        if review["schema_version"] == "transcript_review.v2":
            raw = read_json(path)
            validate_payload(raw, result["episode_id"])
            notes = load_annotations(review, review_path, raw, digest)
            critical = sum(a["kind"] == "uncertainty" and a["severity"] == "critical" for a in notes["annotations"])
            annotation_meta = {"annotations_sha256": notes["sha256"], "annotation_count": len(notes["annotations"]),
                               "unresolved_critical_annotation_count": critical}
            accepted = accepted and not critical
            if sha256_file(path) != digest or sha256_file(review_path) != review_hash:
                raise ValueError("Source/review changed while validating annotations.")
    return {
        "schema_version": "transcript_quality.v2" if annotation_meta else "transcript_quality.v1", **result,
        **annotation_meta,
        "transcript_sha256": digest,
        "content_quality": "accepted_review" if accepted else "review_required",
        "ready_for_extraction": accepted,
        "scope": "Structural checks plus explicit source-version review; not automatic ASR accuracy certification.",
    }
