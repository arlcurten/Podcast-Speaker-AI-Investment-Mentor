#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from modules.common import DATA, REPORTS, path_for_report, write_json


def join_segment_texts(texts: list[Any]) -> str:
    return " ".join(str(text).strip() for text in texts if str(text).strip())


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_merged(raw_segments: list[dict[str, Any]], merged_segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_by_id = {int(seg["segment_id"]): seg for seg in raw_segments}
    mismatches: list[dict[str, Any]] = []
    covered_ids: list[int] = []
    previous_end = -1.0

    for merged in merged_segments:
        merged_id = int(merged["merged_id"])
        source_ids = [int(value) for value in merged.get("source_segment_ids", [])]
        issue: dict[str, Any] = {"merged_id": merged_id, "source_segment_ids": source_ids, "errors": []}
        source_rows = [raw_by_id.get(source_id) for source_id in source_ids]
        missing_ids = [source_id for source_id, row in zip(source_ids, source_rows, strict=False) if row is None]
        if missing_ids:
            issue["errors"].append({"type": "missing_raw_segment_ids", "ids": missing_ids})
        rows = [row for row in source_rows if row is not None]
        if rows:
            expected_ids = [int(row["segment_id"]) for row in rows]
            expected_start = float(rows[0]["start"])
            expected_end = float(rows[-1]["end"])
            expected_text = join_segment_texts([row.get("text", "") for row in rows])
            if source_ids != expected_ids:
                issue["errors"].append({"type": "source_id_order_mismatch", "expected": expected_ids, "actual": source_ids})
            if float(merged["start"]) != expected_start:
                issue["errors"].append({"type": "start_mismatch", "expected": expected_start, "actual": float(merged["start"])})
            if float(merged["end"]) != expected_end:
                issue["errors"].append({"type": "end_mismatch", "expected": expected_end, "actual": float(merged["end"])})
            if str(merged.get("text", "")) != expected_text:
                issue["errors"].append({
                    "type": "text_mismatch",
                    "expected_preview": expected_text[:240],
                    "actual_preview": str(merged.get("text", ""))[:240],
                })
            if expected_start < previous_end:
                issue["errors"].append({"type": "non_monotonic_merged_time", "previous_end": previous_end, "actual_start": expected_start})
            previous_end = expected_end
            covered_ids.extend(expected_ids)
        if issue["errors"]:
            mismatches.append(issue)

    duplicate_ids = sorted({source_id for source_id in covered_ids if covered_ids.count(source_id) > 1})
    raw_ids = [int(seg["segment_id"]) for seg in raw_segments]
    missing_from_merge = sorted(set(raw_ids) - set(covered_ids))
    extra_in_merge = sorted(set(covered_ids) - set(raw_ids))
    coverage = {
        "raw_segment_count": len(raw_segments),
        "merged_segment_count": len(merged_segments),
        "covered_raw_segment_count": len(set(covered_ids)),
        "missing_raw_segment_ids": missing_from_merge[:50],
        "missing_raw_segment_count": len(missing_from_merge),
        "duplicate_source_segment_ids": duplicate_ids[:50],
        "duplicate_source_segment_count": len(duplicate_ids),
        "extra_source_segment_ids": extra_in_merge[:50],
        "extra_source_segment_count": len(extra_in_merge),
    }
    return mismatches, coverage


def inspect_review_windows(raw_segments: list[dict[str, Any]], merged_segments: list[dict[str, Any]], review_path: Path) -> dict[str, Any]:
    if not review_path.exists():
        return {"review_windows_path": path_for_report(review_path), "exists": False}
    payload = load_json(review_path)
    windows = []
    for window in payload.get("windows", []):
        start = float(window["start"])
        end = float(window["end"])
        raw_ids = [int(seg["segment_id"]) for seg in raw_segments if float(seg["end"]) > start and float(seg["start"]) < end]
        merged_ids = [int(seg["merged_id"]) for seg in merged_segments if float(seg["end"]) > start and float(seg["start"]) < end]
        merged_source_ids: list[int] = []
        for seg in merged_segments:
            if int(seg["merged_id"]) in merged_ids:
                merged_source_ids.extend(int(value) for value in seg.get("source_segment_ids", []))
        windows.append({
            "window_type": window.get("window_type"),
            "start": start,
            "end": end,
            "raw_segment_count": len(raw_ids),
            "merged_segment_count": len(merged_ids),
            "merged_source_ids_cover_window_raw_ids": set(raw_ids).issubset(set(merged_source_ids)),
        })
    return {"review_windows_path": path_for_report(review_path), "exists": True, "windows": windows}


def write_report(path: Path, result: dict[str, Any]) -> None:
    lines = [
        f"# {result['episode_id']} Merge Integrity",
        "",
        f"- Status: `{result['status']}`",
        f"- Raw transcript: `{result['raw_transcript']}`",
        f"- Merged transcript: `{result['merged_transcript']}`",
        f"- Mismatch count: {result['mismatch_count']}",
        f"- Missing raw segment count: {result['coverage']['missing_raw_segment_count']}",
        f"- Duplicate source segment count: {result['coverage']['duplicate_source_segment_count']}",
        "",
        "## Review Windows",
        "",
    ]
    review = result["review_windows"]
    if not review.get("exists"):
        lines.append(f"- Review windows not found: `{review['review_windows_path']}`")
    else:
        lines.append("| Window | Start | End | Raw segments | Merged segments | Merged sources cover raw window |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for window in review.get("windows", []):
            lines.append(
                f"| {window['window_type']} | {window['start']:.2f} | {window['end']:.2f} | "
                f"{window['raw_segment_count']} | {window['merged_segment_count']} | "
                f"{window['merged_source_ids_cover_window_raw_ids']} |"
            )
    lines.extend(["", "## Mismatch Sample", ""])
    if result["mismatch_sample"]:
        lines.append("```json")
        lines.append(json.dumps(result["mismatch_sample"], ensure_ascii=False, indent=2))
        lines.append("```")
    else:
        lines.append("No mismatches found.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_validation_result(episode: str, configuration: str, sample_size: int = 10) -> dict[str, Any]:
    base = DATA / "transcripts" / episode / configuration
    raw_path = base / "transcript.json"
    merged_path = base / "merged_transcript.json"
    raw_segments = load_json(raw_path)["segments"]
    merged_segments = load_json(merged_path)["segments"]
    mismatches, coverage = validate_merged(raw_segments, merged_segments)
    review = inspect_review_windows(raw_segments, merged_segments, DATA / "evaluation" / episode / "review_windows.json")
    status = "pass" if not mismatches and coverage["missing_raw_segment_count"] == 0 and coverage["duplicate_source_segment_count"] == 0 else "fail"
    return {
        "episode_id": episode,
        "configuration": configuration,
        "status": status,
        "raw_transcript": path_for_report(raw_path),
        "merged_transcript": path_for_report(merged_path),
        "deterministic_separator": "single ASCII space between non-empty stripped source texts",
        "coverage": coverage,
        "mismatch_count": len(mismatches),
        "mismatch_sample": mismatches[:sample_size],
        "review_windows": review,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", default="EP674")
    parser.add_argument("--configuration", default="large-v3-turbo")
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--write-report", action="store_true", help="Also write a Markdown report. JSON is the canonical output.")
    args = parser.parse_args()

    result = build_validation_result(args.episode, args.configuration, args.sample_size)
    json_path = DATA / "evaluation" / args.episode / "merge_integrity.json"
    report_path = REPORTS / f"{args.episode}_merge_integrity.md"
    write_json(json_path, result)
    output = {"status": status, "json": path_for_report(json_path)}
    if args.write_report:
        write_report(report_path, result)
        output["report"] = path_for_report(report_path)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
