#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from common import DATA, REPORTS, path_for_report, run_command


KEYWORD_RE = re.compile(r"[A-Za-z]|NVIDIA|NVDA|AMD|TSMC|Intel|INTC|ASML|財報|毛利率|營收|伺服器|晶片|供應鏈", re.I)
FIELDS = [
    "episode",
    "window_type",
    "timestamp_start",
    "timestamp_end",
    "model",
    "expected_text",
    "transcribed_text",
    "error_category",
    "severity",
    "semantic_impact",
    "notes",
]


def collect_text(items: list[dict[str, Any]], start: float, end: float) -> str:
    parts = []
    for item in items:
        if float(item["end"]) > start and float(item["start"]) < end:
            parts.append(str(item.get("text", "")).strip())
    return " ".join(p for p in parts if p)


def find_keyword_window(items: list[dict[str, Any]], duration: float, audio_duration: float) -> tuple[float, float, str]:
    best_start = audio_duration / 3
    best_score = -1
    best_reason = "fallback middle-analysis candidate; no strong keyword cluster found"
    step = 60
    start = audio_duration * 0.20
    end_limit = audio_duration * 0.85
    while start + duration <= end_limit:
        text = collect_text(items, start, start + duration)
        score = len(KEYWORD_RE.findall(text))
        if score > best_score:
            best_score = score
            best_start = start
            best_reason = f"keyword candidate score={score}"
        start += step
    return round(best_start, 2), round(best_start + duration, 2), best_reason


def ffmpeg_clip_command(audio_path: Path, start: float, end: float, out_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.2f}",
        "-to",
        f"{end:.2f}",
        "-i",
        path_for_report(audio_path),
        "-c",
        "copy",
        path_for_report(out_path),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", default="EP674")
    parser.add_argument("--configuration", default="large-v3-turbo")
    parser.add_argument("--create-clips", action="store_true", help="Create MP3 review clips with stream copy; no re-encoding.")
    args = parser.parse_args()
    base = DATA / "transcripts" / args.episode / args.configuration
    raw_payload = json.loads((base / "transcript.json").read_text(encoding="utf-8"))
    merged_payload = json.loads((base / "merged_transcript.json").read_text(encoding="utf-8"))
    raw_segments = raw_payload["segments"]
    merged_segments = merged_payload["segments"]
    audio_meta = json.loads((DATA / "audio" / args.episode / "audio_metadata.json").read_text(encoding="utf-8"))
    audio_duration = float(audio_meta["duration_seconds"])
    model = raw_payload.get("metadata", {}).get("model", args.configuration)
    english_start, english_end, english_reason = find_keyword_window(merged_segments, 240.0, audio_duration)
    windows = [
        {"window_type": "opening_general", "start": 180.0, "end": 420.0, "selection_reason": "opening content after intro/ad lead-in"},
        {"window_type": "middle_analysis", "start": round(audio_duration * 0.45, 2), "end": round(audio_duration * 0.45 + 300.0, 2), "selection_reason": "fixed middle-region analysis candidate"},
        {"window_type": "english_terms_candidate", "start": english_start, "end": english_end, "selection_reason": english_reason},
        {"window_type": "late_qa_candidate", "start": round(audio_duration * 0.78, 2), "end": round(audio_duration * 0.78 + 240.0, 2), "selection_reason": "late-episode candidate; verify manually whether this is QA"},
    ]
    audio_path = DATA / "audio" / args.episode / "episode.mp3"
    clip_dir = DATA / "evaluation" / args.episode / "review_clips"
    rows = []
    md = ["# EP674 Manual Review Package", "", "No human listening judgment has been performed. Fill expected_text and error fields after listening to source audio.", ""]
    for window in windows:
        start = float(window["start"])
        end = min(float(window["end"]), audio_duration)
        raw_text = collect_text(raw_segments, start, end)
        merged_text = collect_text(merged_segments, start, end)
        clip_path = clip_dir / f"{window['window_type']}_{int(start)}_{int(end)}.mp3"
        cmd = ffmpeg_clip_command(audio_path, start, end, clip_path)
        if args.create_clips:
            clip_dir.mkdir(parents=True, exist_ok=True)
            code, _, err = run_command(cmd, timeout=60)
            if code != 0:
                raise SystemExit(f"ffmpeg clip failed for {window['window_type']}: {err}")
        rows.append({
            "episode": args.episode,
            "window_type": window["window_type"],
            "timestamp_start": f"{start:.2f}",
            "timestamp_end": f"{end:.2f}",
            "model": model,
            "expected_text": "",
            "transcribed_text": merged_text,
            "error_category": "",
            "severity": "",
            "semantic_impact": "unknown",
            "notes": window["selection_reason"],
        })
        md.extend([
            f"## {window['window_type']}",
            f"- Purpose: {window['selection_reason']}",
            f"- Start: {start:.2f}",
            f"- End: {end:.2f}",
            f"- Selection reason: {window['selection_reason']}",
            f"- Review clip: `{path_for_report(clip_path)}`",
            f"- Play clip: `ffplay {path_for_report(clip_path)}`",
            f"- Play source window: `ffplay -ss {start:.2f} -to {end:.2f} {path_for_report(audio_path)}`",
            f"- Extract command: `{' '.join(cmd)}`",
            "- Manual review CSV: `data/evaluation/manual_review.csv`",
            f"- Clip created: {'yes' if args.create_clips else 'no'}",
            "",
            "### Raw Transcript",
            raw_text,
            "",
            "### Merged Transcript",
            merged_text,
            "",
        ])
    out_csv = DATA / "evaluation" / "manual_review.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    package_json = DATA / "evaluation" / args.episode / "review_windows.json"
    package_json.parent.mkdir(parents=True, exist_ok=True)
    package_json.write_text(json.dumps({"windows": windows, "manual_review_rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md = REPORTS / f"{args.episode}_manual_review_package.md"
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {out_csv}")
    print(f"Wrote {package_json}")
    print(f"Wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
