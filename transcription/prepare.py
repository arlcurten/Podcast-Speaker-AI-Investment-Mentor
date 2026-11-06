from __future__ import annotations

from typing import Any
import csv
from importlib.metadata import version
from pathlib import Path

from .common import TERMINOLOGY, atomic_write_json, private_path, read_json, sha256_file, utc_now
from .quality import quality_gate


def load_annotation_terms() -> list[dict[str, str]]:
    with TERMINOLOGY.open(encoding="utf-8", newline="") as stream:
        return [r for r in csv.DictReader(stream, delimiter="\t")
                if r.get("type") in {"alias", "correction"}
                and r.get("status") in {"active", "human_reviewed"} and r.get("source_text")]


def annotate_terms(text: str, normalized: str, terms: list[dict[str, str]], converter: Any) -> list[dict[str, Any]]:
    annotations = []
    for row in terms:
        views = []
        if row["source_text"].casefold() in text.casefold():
            views.append("raw_text")
        if converter.convert(row["source_text"]).casefold() in normalized.casefold():
            views.append("normalized_text_zh_tw")
        if views:
            annotations.append({
                "source_text": row["source_text"], "suggested_text": row["target_text"],
                "type": row["type"], "status": row["status"], "notes": row.get("notes", ""),
                "matched_views": views, "context_verification_required": True,
            })
    return annotations


def join_segment_texts(texts: list[Any]) -> str:
    return " ".join(str(text).strip() for text in texts if str(text).strip())


def should_merge(current: dict[str, Any], next_seg: dict[str, Any], max_gap: float, max_duration: float, max_chars: int) -> bool:
    gap = float(next_seg["start"]) - float(current["end"])
    if gap < 0:
        gap = 0
    merged_duration = float(next_seg["end"]) - float(current["start"])
    merged_chars = len(join_segment_texts([current["text"], next_seg.get("text", "")]))
    return gap <= max_gap and merged_duration <= max_duration and merged_chars <= max_chars


def merge_segments(segments: list[dict[str, Any]], max_gap: float, max_duration: float, max_chars: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for seg in segments:
        text = str(seg.get("text", ""))
        item = {
            "merged_id": 0,
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "text": text.strip(),
            "source_segment_ids": [seg.get("segment_id")],
            "source_segment_count": 1,
        }
        if current is None:
            current = item
            continue
        if should_merge(current, item, max_gap, max_duration, max_chars):
            current["end"] = max(current["end"], item["end"])
            current["text"] = join_segment_texts([current["text"], item["text"]])
            current["source_segment_ids"].extend(item["source_segment_ids"])
            current["source_segment_count"] = len(current["source_segment_ids"])
        else:
            current["merged_id"] = len(merged)
            merged.append(current)
            current = item
    if current is not None:
        current["merged_id"] = len(merged)
        merged.append(current)
    return merged


def prepare(source: Path, output: Path, review: Path | None = None) -> dict[str, Any]:
    source, output = private_path(source), private_path(output)
    if source == output or output.exists():
        raise ValueError("Derived output must be a new path; source and prior versions are never overwritten.")
    quality = quality_gate(source, review_path=review)
    payload = read_json(source)
    try:
        import opencc
    except ImportError as exc:
        raise RuntimeError("Install transcription/requirements.txt for OpenCC normalization.") from exc
    converter = opencc.OpenCC("s2twp")
    terms = load_annotation_terms()
    segments = merge_segments(payload["segments"], max_gap=1.2, max_duration=25.0, max_chars=220)
    mapped = [sid for seg in segments for sid in seg["source_segment_ids"]]
    if mapped != [seg["segment_id"] for seg in payload["segments"]]:
        raise ValueError("Incomplete raw-to-merged mapping.")
    rows = []
    for seg in segments:
        text = seg["text"]
        normalized = converter.convert(text)
        rows.append({
            "merged_id": seg["merged_id"], "start": seg["start"], "end": seg["end"],
            "raw_text": text, "normalized_text_zh_tw": normalized,
            "source_segment_ids": seg["source_segment_ids"],
            "terminology_annotations": annotate_terms(text, normalized, terms, converter),
        })
    result = {
        "schema_version": "transcription_normalized.v1",
        "metadata": {
            "episode_id": quality["episode_id"], "created_at": utc_now(),
            "source_raw_sha256": quality["transcript_sha256"],
            "source_raw_filename": source.name,
            "terminology_sha256": sha256_file(TERMINOLOGY),
            "annotation_matching_version": "normalized_suggestions.v2",
            "normalization": "OpenCC s2twp; terminology annotations only",
            "opencc_version": version("opencc-python-reimplemented"),
            "merge_parameters": {"max_gap": 1.2, "max_duration": 25.0, "max_chars": 220},
            "quality": quality, "review_sha256": sha256_file(review) if review else None,
            "source_mapping_complete": True, "not_semantic_segmentation": True,
        },
        "segments": rows,
    }
    if sha256_file(source) != quality["transcript_sha256"]:
        raise ValueError("Raw source changed during preparation; review the new version.")
    atomic_write_json(output, result, overwrite=False)
    return {"episode_id": quality["episode_id"], "merged_count": len(rows),
            "raw_segment_count": quality["segment_count"], "output_sha256": sha256_file(output),
            "ready_for_extraction": quality["ready_for_extraction"]}
