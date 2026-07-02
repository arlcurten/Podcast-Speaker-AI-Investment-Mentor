from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from modules.common import ROOT, read_json


REQUIRED_INPUTS = [
    "raw_large-v3-turbo_transcript.json",
    "derived_large-v3-turbo_normalized_merged_transcript_zh_tw.json",
    "merge_integrity.json",
]


def discover_episodes() -> list[str]:
    base = ROOT / "data" / "phase1_inputs"
    episodes = []
    for path in sorted(base.iterdir()):
        if not path.is_dir():
            continue
        if all((path / name).exists() for name in REQUIRED_INPUTS):
            episodes.append(path.name)
    return episodes


def stage_cost(metadata: dict[str, Any]) -> float:
    total = 0.0
    for stage in metadata.get("stages", []):
        total += float((stage.get("usage") or {}).get("cost") or 0)
    return total


def automated_review(
    episode_id: str,
    out_dir: Path,
) -> list[str]:
    warnings: list[str] = []
    level1 = read_json(out_dir / "level1_episode_semantic_map.json")
    level2 = read_json(out_dir / "level2_reasoning_records.json")
    level3 = read_json(out_dir / "level3_optional_consolidation.json")
    validations = [
        read_json(out_dir / "level1_validation.json"),
        read_json(out_dir / "level2_validation.json"),
        read_json(out_dir / "level3_validation.json"),
    ]
    validation_warnings = sum(len(v.get("warnings", [])) for v in validations)
    if validation_warnings:
        warnings.append(f"{validation_warnings} validation warnings need review")
    if len(level1.get("main_topic_threads", [])) < 3:
        warnings.append("Level 1 found fewer than 3 topic threads; possible topic omission")
    if len(level2.get("reasoning_records", [])) < 3:
        warnings.append("Level 2 found fewer than 3 reasoning records; possible under-extraction")
    if level3.get("selected_strategy") != level1.get("recommended_level3_strategy"):
        warnings.append("Level 3 strategy differs from Level 1 recommendation")
    for record in level2.get("reasoning_records", []):
        if not record.get("source_evidence"):
            warnings.append(f"{record.get('reasoning_record_id')} has no source evidence")
        if not record.get("uncertainty") and not record.get("risks"):
            warnings.append(f"{record.get('reasoning_record_id')} has no explicit risk/uncertainty fields")
    if not warnings:
        warnings.append("No major automated review warnings")
    return warnings


def result_from_output(episode_id: str) -> dict[str, Any] | None:
    out_dir = ROOT / "data" / "outputs" / episode_id
    required = [
        "level1_episode_semantic_map.json",
        "level2_reasoning_records.json",
        "level3_optional_consolidation.json",
        "run_metadata.json",
    ]
    if not all((out_dir / name).exists() for name in required):
        return None
    metadata = read_json(out_dir / "run_metadata.json")
    level1 = read_json(out_dir / "level1_episode_semantic_map.json")
    level2 = read_json(out_dir / "level2_reasoning_records.json")
    level3 = read_json(out_dir / "level3_optional_consolidation.json")
    return {
        "episode_id": episode_id,
        "status": "success",
        "structure": level1.get("episode_structure_type"),
        "level3_strategy": level3.get("selected_strategy"),
        "reasoning_record_count": len(level2.get("reasoning_records", [])),
        "total_tokens": (metadata.get("usage") or {}).get("total_tokens", 0),
        "latency_seconds": metadata.get("total_latency_seconds", 0.0),
        "cost_usd": stage_cost(metadata),
        "warnings": automated_review(episode_id, out_dir),
    }


def write_pilot_report(path: Path, episodes: list[str], results: list[dict[str, Any]]) -> None:
    lines = [
        "# Phase 4 Semantic Extraction Pilot Summary",
        "",
        "Scope: small pilot using available Phase 1 transcripts only. No full corpus processing was started.",
        "",
        f"- Available Phase 1 episodes discovered: {', '.join(episodes) if episodes else 'none'}",
        f"- Episodes processed: {', '.join(r['episode_id'] for r in results if r['status'] == 'success') or 'none'}",
        "",
        "## Results",
        "",
        "| Episode | Status | Structure | Level 3 | Records | Tokens | Latency | Cost | Warnings |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for result in results:
        if result["status"] != "success":
            lines.append(
                f"| {result['episode_id']} | failed | - | - | - | - | - | - | {result.get('error', '')} |"
            )
            continue
        lines.append(
            f"| {result['episode_id']} | success | `{result['structure']}` | `{result['level3_strategy']}` | "
            f"{result['reasoning_record_count']} | {result['total_tokens']} | {result['latency_seconds']:.1f}s | "
            f"${result['cost_usd']:.5f} | {'; '.join(result['warnings'])} |"
        )
    total_cost = sum(float(r.get("cost_usd") or 0) for r in results)
    lines.extend(
        [
            "",
            f"- Total pilot cost: `${total_cost:.5f}`",
            "",
            "## MVP Readiness",
            "",
        ]
    )
    successes = [r for r in results if r["status"] == "success"]
    if len(successes) >= 3:
        lines.append("- Enough episode diversity is available to begin evaluating a minimal Mentor Agent MVP.")
    elif successes:
        lines.append("- Output format is usable for a minimal Mentor Agent prototype, but episode diversity is not yet sufficient.")
    else:
        lines.append("- No successful semantic extraction output is available yet.")
    lines.extend(
        [
            "- Before a real 3-20 episode pilot, add more Phase 1 normalized transcripts.",
            "- Keep evidence compact with `merged_ids`, `source_segment_id_ranges`, and isolated `source_segment_ids`.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a small semantic extraction pilot over available Phase 1 inputs.")
    parser.add_argument("--model-id", default="qwen/qwen3-235b-a22b-2507")
    parser.add_argument("--max-episodes", type=int, default=5)
    parser.add_argument("--max-cost-usd", type=float, default=2.0)
    parser.add_argument("--report-only", action="store_true", help="Rebuild the pilot summary from existing outputs without API calls.")
    args = parser.parse_args()

    episodes = discover_episodes()
    selected = episodes[: args.max_episodes]
    results: list[dict[str, Any]] = []
    total_cost = 0.0
    for episode_id in selected:
        if total_cost >= args.max_cost_usd:
            break
        if args.report_only:
            result = result_from_output(episode_id)
            if result:
                total_cost += float(result.get("cost_usd") or 0)
                results.append(result)
            else:
                results.append({"episode_id": episode_id, "status": "failed", "error": "missing canonical output"})
            continue
        cmd = [
            sys.executable,
            "main.py",
            "run-episode",
            "--episode",
            episode_id,
            "--model-id",
            args.model_id,
        ]
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            results.append({"episode_id": episode_id, "status": "failed", "error": proc.stderr.strip() or proc.stdout.strip()})
            continue
        out_dir = ROOT / "data" / "outputs" / episode_id
        metadata = read_json(out_dir / "run_metadata.json")
        level1 = read_json(out_dir / "level1_episode_semantic_map.json")
        level2 = read_json(out_dir / "level2_reasoning_records.json")
        level3 = read_json(out_dir / "level3_optional_consolidation.json")
        cost = stage_cost(metadata)
        total_cost += cost
        results.append(
            {
                "episode_id": episode_id,
                "status": "success",
                "structure": level1.get("episode_structure_type"),
                "level3_strategy": level3.get("selected_strategy"),
                "reasoning_record_count": len(level2.get("reasoning_records", [])),
                "total_tokens": (metadata.get("usage") or {}).get("total_tokens", 0),
                "latency_seconds": metadata.get("total_latency_seconds", 0.0),
                "cost_usd": cost,
                "warnings": automated_review(episode_id, out_dir),
            }
        )
    report_path = ROOT / "reports" / "phase4_pilot_summary.md"
    write_pilot_report(report_path, episodes, results)
    print(json.dumps({"episodes": selected, "results": results, "report": str(report_path.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
