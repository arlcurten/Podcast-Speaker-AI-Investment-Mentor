#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from typing import Any

from modules.common import DATA, write_json
from modules.transcript.validate_merged_transcript import build_validation_result


def duration_stats(items: list[dict[str, Any]]) -> dict[str, float | int | None]:
    durations = [float(item["end"]) - float(item["start"]) for item in items]
    lengths = [len(str(item.get("text", "")).strip()) for item in items]
    return {
        "count": len(items),
        "mean_duration_seconds": statistics.fmean(durations) if durations else None,
        "median_duration_seconds": statistics.median(durations) if durations else None,
        "mean_text_length": statistics.fmean(lengths) if lengths else None,
    }


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
            current["end"] = item["end"]
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", default="EP674")
    parser.add_argument("--configuration", default="large-v3-turbo")
    parser.add_argument("--max-gap", type=float, default=1.2)
    parser.add_argument("--max-duration", type=float, default=25.0)
    parser.add_argument("--max-chars", type=int, default=220)
    args = parser.parse_args()
    transcript_dir = DATA / "transcripts" / args.episode
    raw_path = transcript_dir / f"raw_{args.configuration}_transcript.json"
    merged_path = transcript_dir / f"derived_{args.configuration}_merged_transcript.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_segments = payload.get("segments", [])
    merged = merge_segments(raw_segments, args.max_gap, args.max_duration, args.max_chars)
    metadata = {
        "episode_id": args.episode,
        "configuration": args.configuration,
        "source": "deterministic merge of raw ASR segments",
        "does_not_modify_text": True,
        "not_topic_segmentation": True,
        "merge_parameters": {
            "max_gap_seconds": args.max_gap,
            "max_duration_seconds": args.max_duration,
            "max_chars": args.max_chars,
        },
        "raw_stats": duration_stats(raw_segments),
        "merged_stats": duration_stats(merged),
    }
    write_json(merged_path, {"metadata": metadata, "segments": merged})
    validation = build_validation_result(args.episode, args.configuration)
    evaluation_dir = DATA / "evaluation" / args.episode
    write_json(evaluation_dir / "merge_integrity.json", validation)
    if validation["status"] != "pass":
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 1
    file_sizes = {
        raw_path.name: raw_path.stat().st_size,
        merged_path.name: merged_path.stat().st_size,
    }
    summary = {**metadata, "file_sizes_bytes": file_sizes, "merge_integrity_status": validation["status"]}
    write_json(evaluation_dir / "merge_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
