#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from common import DATA, write_json


def srt_time(value: float) -> str:
    millis = int(round(value * 1000))
    h, rem = divmod(millis, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


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


def write_outputs(out_dir: Path, merged: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    write_json(out_dir / "merged_transcript.json", {"metadata": metadata, "segments": merged})
    srt_lines: list[str] = []
    md_lines = [f"# Merged Transcript {metadata['episode_id']} {metadata['configuration']}", ""]
    for idx, seg in enumerate(merged, start=1):
        srt_lines.extend([str(idx), f"{srt_time(seg['start'])} --> {srt_time(seg['end'])}", seg["text"].strip(), ""])
        md_lines.append(f"[{seg['start']:.2f} - {seg['end']:.2f}] {seg['text'].strip()}")
    (out_dir / "merged_transcript.srt").write_text("\n".join(srt_lines), encoding="utf-8")
    (out_dir / "merged_transcript.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", default="EP674")
    parser.add_argument("--configuration", default="large-v3-turbo")
    parser.add_argument("--max-gap", type=float, default=1.2)
    parser.add_argument("--max-duration", type=float, default=25.0)
    parser.add_argument("--max-chars", type=int, default=220)
    args = parser.parse_args()
    out_dir = DATA / "transcripts" / args.episode / args.configuration
    payload = json.loads((out_dir / "transcript.json").read_text(encoding="utf-8"))
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
    write_outputs(out_dir, merged, metadata)
    file_sizes = {
        name: (out_dir / name).stat().st_size
        for name in ["transcript.json", "transcript.srt", "transcript.md", "merged_transcript.json", "merged_transcript.srt", "merged_transcript.md"]
        if (out_dir / name).exists()
    }
    summary = {**metadata, "file_sizes_bytes": file_sizes}
    write_json(DATA / "evaluation" / f"{args.episode}_merge_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
