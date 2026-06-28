#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from modules.common import DATA, write_csv, write_json


TERMS = ["台積電", "TSMC", "NVIDIA", "NVDA", "Intel", "INTC", "AMD", "ASML", "CoWoS", "NVLink", "聯準會", "升息", "降息", "本益比", "財報"]


def load_runs(episode: str) -> list[dict[str, Any]]:
    base = DATA / "transcripts" / episode
    rows: list[dict[str, Any]] = []
    if not base.exists():
        return rows
    for meta_path in sorted(base.glob("*/run_metadata.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        transcript_path = meta_path.parent / "transcript.json"
        transcript = json.loads(transcript_path.read_text(encoding="utf-8")) if transcript_path.exists() else {"segments": []}
        text = "".join(seg.get("text", "") for seg in transcript.get("segments", []))
        rows.append({
            "episode": episode,
            "configuration": meta.get("configuration_name"),
            "status": meta.get("status"),
            "failure_reason": meta.get("failure_reason"),
            "model": meta.get("model"),
            "device": meta.get("device"),
            "compute_type": meta.get("compute_type"),
            "audio_duration_seconds": meta.get("audio_duration_seconds"),
            "runtime_seconds": meta.get("runtime_seconds"),
            "realtime_factor": meta.get("realtime_factor"),
            "peak_ram_mb": meta.get("peak_ram_mb"),
            "peak_vram_mb": meta.get("peak_vram_mb"),
            "output_segment_count": meta.get("segment_count"),
            "transcript_length": len(text),
            "recognized_terms": ", ".join([term for term in TERMS if term.lower() in text.lower()]),
            "company_name_errors": "",
            "ticker_errors": "",
            "financial_term_errors": "",
            "hallucinations": "",
            "repetitions": "",
            "timestamp_usability": "manual review required",
            "overall_semantic_comprehensibility": "manual review required",
        })
    return rows


def build_filtering_segments(episode: str, configuration: str) -> list[dict[str, Any]]:
    transcript_path = DATA / "transcripts" / episode / configuration / "transcript.json"
    if not transcript_path.exists():
        matches = sorted((DATA / "transcripts" / episode).glob(f"{configuration}*/transcript.json"))
        if not matches:
            return []
        transcript_path = matches[0]
    payload = json.loads(transcript_path.read_text(encoding="utf-8"))
    duration = float(payload.get("metadata", {}).get("audio_duration_seconds") or 0)
    rows = []
    bucket: list[dict[str, Any]] = []
    bucket_start = 0.0
    for seg in payload.get("segments", []):
        if not bucket:
            bucket_start = float(seg["start"])
        bucket.append(seg)
        end = float(seg["end"])
        if end - bucket_start >= 60:
            rel = bucket_start / duration if duration else None
            if rel is None:
                third = ""
            elif rel < 1 / 3:
                third = "front"
            elif rel < 2 / 3:
                third = "middle"
            else:
                third = "back"
            rows.append({
                "episode": episode,
                "configuration": payload.get("metadata", {}).get("configuration_name"),
                "start_time": round(bucket_start, 2),
                "end_time": round(end, 2),
                "relative_position": rel,
                "episode_third": third,
                "label": "",
                "text": " ".join(s.get("text", "").strip() for s in bucket),
            })
            bucket = []
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", required=True)
    parser.add_argument("--configuration", default="large-v3-turbo")
    args = parser.parse_args()
    rows = load_runs(args.episode)
    out_dir = DATA / "legacy" / "evaluation"
    out_csv = out_dir / "benchmark_comparison.csv"
    fields = [
        "episode", "configuration", "status", "failure_reason", "model", "device", "compute_type",
        "audio_duration_seconds", "runtime_seconds", "realtime_factor", "peak_ram_mb", "peak_vram_mb",
        "output_segment_count", "transcript_length", "recognized_terms", "company_name_errors",
        "ticker_errors", "financial_term_errors", "hallucinations", "repetitions",
        "timestamp_usability", "overall_semantic_comprehensibility",
    ]
    write_csv(out_csv, rows, fields)
    filtering = build_filtering_segments(args.episode, args.configuration)
    if filtering:
        write_csv(out_dir / "content_filtering_segments.csv", filtering, list(filtering[0].keys()))
        sample = filtering[:3] + filtering[len(filtering)//2:len(filtering)//2+3] + filtering[-3:]
        write_csv(out_dir / "content_filtering_sample.csv", sample, list(filtering[0].keys()))
    write_json(out_dir / "benchmark_comparison.json", rows)
    print(f"Wrote {out_csv}")
    print(f"Runs compared: {len(rows)}")
    print(f"Content-filtering segments: {len(filtering)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
