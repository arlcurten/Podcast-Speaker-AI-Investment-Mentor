"""Small, reproducible audio-review queues, not automatic ASR acceptance."""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
from typing import Any

from .common import TERMINOLOGY, atomic_write_json, private_path, read_json, sha256_file, utc_now
from .prepare import annotate_terms, load_annotation_terms
from .quality import validate_payload


METRICS = ("avg_logprob", "no_speech_prob", "compression_ratio")
REVIEW_THRESHOLDS = {"avg_logprob_below": -1.0, "no_speech_prob_above": 0.6, "compression_ratio_above": 2.4}


def review_signals(segment: dict[str, Any]) -> list[str]:
    signals = []
    for metric, direction in (("avg_logprob", "below"), ("no_speech_prob", "above"), ("compression_ratio", "above")):
        limit = REVIEW_THRESHOLDS[f"{metric}_{direction}"]
        value = segment.get(metric)
        if type(value) in (int, float) and math.isfinite(value) and (value < limit if direction == "below" else value > limit):
            signals.append(f"review_{metric}")
    return signals


def create_review_packet(source: Path, output: Path, *, audio: Path | None = None,
                         sample_count: int = 8, context_seconds: float = 12.0,
                         segment_ids: list[int] | None = None) -> dict[str, Any]:
    source, output = private_path(source), private_path(output)
    if output.exists() or source == output:
        raise ValueError("Review packet requires a new output path.")
    if type(sample_count) is not int or not 1 <= sample_count <= 32:
        raise ValueError("sample_count must be between 1 and 32.")
    if not math.isfinite(context_seconds) or not 0 <= context_seconds <= 60:
        raise ValueError("context_seconds must be between 0 and 60.")
    digest = sha256_file(source)
    payload = read_json(source)
    validation = validate_payload(payload)
    segments = payload["segments"]
    by_id = {s["segment_id"]: i for i, s in enumerate(segments)}
    requested = list(dict.fromkeys(segment_ids or []))
    if len(requested) > sample_count or any(type(sid) is not int or sid not in by_id for sid in requested):
        raise ValueError("Requested segment IDs must exist and fit sample_count.")
    try:
        import opencc
    except ImportError as exc:
        raise RuntimeError("Install transcription/requirements.txt for review preparation.") from exc
    converter = opencc.OpenCC("s2twp")
    terms = load_annotation_terms()
    annotations = [annotate_terms(s["text"], converter.convert(s["text"]), terms, converter) for s in segments]
    signals = [review_signals(s) + (["terminology_suggestion"] if notes else [])
               for s, notes in zip(segments, annotations)]
    selected: dict[int, list[str]] = {by_id[sid]: ["requested_segment"] for sid in requested}

    def add(index: int, reason: str) -> None:
        if index in selected:
            if reason not in selected[index]:
                selected[index].append(reason)
        elif len(selected) < sample_count and all(
            abs(segments[index]["start"] - segments[other]["start"]) >= max(1.0, 2 * context_seconds)
            for other in selected
        ):
            selected[index] = [reason]

    # Spread part of the listening budget across the episode; risk signals use
    # the rest. This samples audio quality, not semantic importance or topics.
    coverage_count = max(1, sample_count // 2)
    start, end = segments[0]["start"], validation["last_timestamp"]
    for slot in range(coverage_count):
        target = start + (end - start) * (slot + 0.5) / coverage_count
        add(min(range(len(segments)), key=lambda i: abs(segments[i]["start"] - target)), "coverage_sample")
    for index in sorted(range(len(segments)), key=lambda i: (-len(signals[i]), i)):
        if signals[index]:
            add(index, "risk_signal_sample")
    if len(selected) < sample_count:
        # Distance-prioritized fill avoids repeatedly sampling one nearby discussion.
        for index in sorted(range(len(segments)), key=lambda i: -min(
            abs(segments[i]["start"] - segments[j]["start"]) for j in selected
        )):
            add(index, "coverage_fill")

    audio_info = None
    if audio is not None:
        audio = private_path(audio)
        current_hash = sha256_file(audio)
        recorded_hash = payload["metadata"].get("audio_sha256")
        if recorded_hash and recorded_hash != current_hash:
            raise ValueError("Audio does not match the transcript's recorded audio hash.")
        audio_info = {"filename": audio.name, "sha256": current_hash,
                      "source_binding": "recorded_hash_match" if recorded_hash else "unverified_retrospective_association",
                      "listened": False}
    samples = []
    for index, reasons in sorted(selected.items()):
        center = segments[index]
        context = [s for s in segments if s["end"] >= center["start"] - context_seconds
                   and s["start"] <= center["end"] + context_seconds]
        samples.append({
            "focus_segment_id": center["segment_id"], "selection_reasons": reasons,
            "signals": signals[index], "terminology_suggestions": annotations[index],
            "start": context[0]["start"], "end": max(s["end"] for s in context),
            "segments": [{k: s[k] for k in ("segment_id", "start", "end", "text", *METRICS) if k in s} for s in context],
            "review_status": "pending_audio_review",
        })
    packet = {
        "schema_version": "transcript_review_packet.v1", "created_at": utc_now(),
        "episode_id": validation["episode_id"], "transcript_sha256": digest,
        "source_filename": source.name, "structural_validation": validation,
        "terminology_sha256": sha256_file(TERMINOLOGY), "audio": audio_info,
        "selection": {"requested_count": sample_count, "context_seconds": context_seconds,
                      "requested_segment_ids": requested, "heuristic_thresholds": REVIEW_THRESHOLDS},
        "diagnostics": {
            "metric_coverage": {key: sum(type(s.get(key)) in (int, float) and math.isfinite(s[key]) for s in segments) for key in METRICS},
            "candidate_segment_counts": dict(Counter(signal for row in signals for signal in row)),
        },
        "scope": "Review candidates, not confirmed errors, WER estimates or extraction approval. Confidence can be high for incorrect words. Check names, numbers, negation, attribution and omissions against audio.",
        "ready_for_extraction": False, "samples": samples,
    }
    if sha256_file(source) != digest:
        raise ValueError("Transcript changed while preparing review; retry on a stable source.")
    atomic_write_json(output, packet, overwrite=False)
    return {"episode_id": validation["episode_id"], "sample_count": len(samples),
            "transcript_sha256": digest, "ready_for_extraction": False,
            "diagnostics": packet["diagnostics"], "output_sha256": sha256_file(output)}
