#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from modules.common import DATA, REPORTS, path_for_report, write_json


CJK_RE = re.compile(r"[\u4e00-\u9fff]")
PUNCT_ONLY_RE = re.compile(r"^[\s\W_]+$", re.UNICODE)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def stats(values: list[float]) -> dict[str, float | None]:
    return {
        "mean": statistics.fmean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "p10": percentile(values, 0.10),
        "p90": percentile(values, 0.90),
        "max": max(values) if values else None,
    }


def text_len(text: str) -> int:
    return len(text.strip())


def cjk_len(text: str) -> int:
    return len(CJK_RE.findall(text))


def audit(transcript_path: Path) -> dict[str, Any]:
    payload = json.loads(transcript_path.read_text(encoding="utf-8"))
    segments = payload.get("segments", [])
    durations = [max(0.0, float(seg["end"]) - float(seg["start"])) for seg in segments]
    char_lengths = [text_len(str(seg.get("text", ""))) for seg in segments]
    raw_ids = [seg.get("segment_id") for seg in segments]
    word_like_keys = {"word", "words", "probability", "tokens"}
    repeated_adjacent = []
    overlap_count = 0
    same_timestamp_count = 0
    non_monotonic_count = 0
    max_overlap = 0.0
    gap_values: list[float] = []
    for idx in range(1, len(segments)):
        prev = segments[idx - 1]
        cur = segments[idx]
        prev_text = str(prev.get("text", "")).strip()
        cur_text = str(cur.get("text", "")).strip()
        if prev_text and prev_text == cur_text:
            repeated_adjacent.append({"index": idx, "text": cur_text, "previous_segment_id": prev.get("segment_id"), "segment_id": cur.get("segment_id")})
        prev_end = float(prev["end"])
        cur_start = float(cur["start"])
        gap = cur_start - prev_end
        gap_values.append(gap)
        if cur_start < prev_end:
            overlap_count += 1
            max_overlap = max(max_overlap, prev_end - cur_start)
        if cur_start == float(prev["start"]) and float(cur["end"]) == prev_end:
            same_timestamp_count += 1
        if cur_start < float(prev["start"]) or float(cur["end"]) < prev_end:
            non_monotonic_count += 1
    id_counts = Counter(raw_ids)
    duplicate_ids = [seg_id for seg_id, count in id_counts.items() if count > 1]
    has_word_timestamp_shape = bool(segments) and all(("start" in seg and "end" in seg and "word" in seg) for seg in segments[: min(20, len(segments))])
    has_segment_shape = bool(segments) and all(("segment_id" in seg and "text" in seg and "start" in seg and "end" in seg) for seg in segments[: min(20, len(segments))])
    report = {
        "transcript_path": path_for_report(transcript_path),
        "project_relative_path": path_for_report(transcript_path),
        "runtime_absolute_path": str(transcript_path.resolve()),
        "segment_count": len(segments),
        "is_faster_whisper_segment_shape": has_segment_shape,
        "appears_to_be_word_timestamps": has_word_timestamp_shape,
        "segment_keys_sample": sorted(segments[0].keys()) if segments else [],
        "duration_seconds": stats(durations),
        "text_characters": stats([float(v) for v in char_lengths]),
        "counts": {
            "duration_lt_0_5s": sum(1 for v in durations if v < 0.5),
            "duration_lt_1s": sum(1 for v in durations if v < 1.0),
            "cjk_chars_lt_3": sum(1 for seg in segments if cjk_len(str(seg.get("text", ""))) < 3),
            "punctuation_or_blank_only": sum(1 for seg in segments if not str(seg.get("text", "")).strip() or bool(PUNCT_ONLY_RE.match(str(seg.get("text", "")).strip()))),
            "duplicate_segment_ids": len(duplicate_ids),
            "adjacent_duplicate_text": len(repeated_adjacent),
            "overlap_count": overlap_count,
            "same_timestamp_count": same_timestamp_count,
            "non_monotonic_timestamp_count": non_monotonic_count,
        },
        "max_overlap_seconds": max_overlap,
        "gap_seconds": stats(gap_values),
        "adjacent_duplicate_examples": repeated_adjacent[:20],
        "srt_file_size_bytes": (transcript_path.parent / "transcript.srt").stat().st_size if (transcript_path.parent / "transcript.srt").exists() else None,
        "markdown_file_size_bytes": (transcript_path.parent / "transcript.md").stat().st_size if (transcript_path.parent / "transcript.md").exists() else None,
        "fragmentation_assessment": "over-fragmented for SRT/Markdown readability" if len(segments) > 1000 and (statistics.median(durations) if durations else 999) < 2.0 else "not severely fragmented",
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    d = report["duration_seconds"]
    t = report["text_characters"]
    c = report["counts"]
    return "\n".join([
        "# EP674 Segment Audit",
        "",
        f"- Transcript: `{report['transcript_path']}`",
        f"- Segment count: {report['segment_count']}",
        f"- Faster-whisper segment shape: {report['is_faster_whisper_segment_shape']}",
        f"- Appears to be word timestamps: {report['appears_to_be_word_timestamps']}",
        f"- Segment keys: {', '.join(report['segment_keys_sample'])}",
        "",
        "## Duration Stats",
        f"- mean: {d['mean']:.3f}s",
        f"- median: {d['median']:.3f}s",
        f"- p10: {d['p10']:.3f}s",
        f"- p90: {d['p90']:.3f}s",
        f"- max: {d['max']:.3f}s",
        "",
        "## Text Length Stats",
        f"- mean: {t['mean']:.3f} chars",
        f"- median: {t['median']:.3f} chars",
        f"- p10: {t['p10']:.3f} chars",
        f"- p90: {t['p90']:.3f} chars",
        "",
        "## Counts",
        f"- duration < 0.5s: {c['duration_lt_0_5s']}",
        f"- duration < 1s: {c['duration_lt_1s']}",
        f"- fewer than 3 Chinese characters: {c['cjk_chars_lt_3']}",
        f"- punctuation or blank only: {c['punctuation_or_blank_only']}",
        f"- adjacent duplicate text: {c['adjacent_duplicate_text']}",
        f"- overlap count: {c['overlap_count']}",
        f"- same timestamp count: {c['same_timestamp_count']}",
        f"- non-monotonic timestamp count: {c['non_monotonic_timestamp_count']}",
        f"- max overlap: {report['max_overlap_seconds']:.3f}s",
        "",
        "## Readability",
        f"- SRT size: {report['srt_file_size_bytes']} bytes",
        f"- Markdown size: {report['markdown_file_size_bytes']} bytes",
        f"- Assessment: {report['fragmentation_assessment']}",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", default="EP674")
    parser.add_argument("--configuration", default="large-v3-turbo")
    args = parser.parse_args()
    transcript_path = DATA / "transcripts" / args.episode / args.configuration / "transcript.json"
    if not transcript_path.exists():
        raise SystemExit(f"Transcript not found: {transcript_path}")
    report = audit(transcript_path)
    out_json = DATA / "evaluation" / args.episode / "segment_audit.json"
    out_md = REPORTS / f"{args.episode}_segment_audit.md"
    write_json(out_json, report)
    out_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
