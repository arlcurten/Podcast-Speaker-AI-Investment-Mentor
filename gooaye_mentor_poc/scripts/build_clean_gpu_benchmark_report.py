#!/usr/bin/env python3
from __future__ import annotations

import json
import platform
import re
import statistics
from pathlib import Path
from typing import Any

from common import DATA, REPORTS, read_json, write_json


def read_json_opt(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def fmt(value: Any) -> str:
    if value is None or value == "":
        return "not measured"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def transcript_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    import hashlib

    payload = json.loads(path.read_text(encoding="utf-8"))
    text = "".join(seg.get("text", "") for seg in payload.get("segments", []))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_turbo_runs(base: Path) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for run_dir in sorted(base.glob("run-*")):
        meta_path = run_dir / "run_metadata.json"
        if not meta_path.exists():
            continue
        meta = read_json(meta_path)
        meta.setdefault("run_id", run_dir.name)
        meta.setdefault("transcript_text_sha256", transcript_hash(run_dir / "transcript.json"))
        runs.append(meta)
    return runs


def runtime_stats(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    runtimes = [float(r["runtime_seconds"]) for r in runs if r.get("runtime_seconds") is not None and r.get("status") == "success"]
    if not runtimes:
        return None
    variation = (max(runtimes) - min(runtimes)) / min(runtimes) * 100 if min(runtimes) else None
    if variation is None:
        classification = "not measured"
    elif variation <= 5:
        classification = "good controlled baseline"
    elif variation <= 10:
        classification = "usable with minor desktop interference"
    else:
        classification = "unstable; do not use as clean performance baseline"
    return {
        "min_runtime_seconds": min(runtimes),
        "max_runtime_seconds": max(runtimes),
        "mean_runtime_seconds": statistics.fmean(runtimes),
        "median_runtime_seconds": statistics.median(runtimes),
        "stddev_runtime_seconds": statistics.stdev(runtimes) if len(runtimes) > 1 else 0.0,
        "runtime_variation_percent": variation,
        "stability_classification": classification,
    }


def main() -> int:
    bench_dir = DATA / "benchmarks" / "EP674"
    clip_meta = read_json_opt(bench_dir / "benchmark_clip_metadata.json") or {}
    baseline = read_json_opt(bench_dir / "gpu_baseline_controlled_desktop.json") or {}
    turbo_runs = load_turbo_runs(bench_dir / "large-v3-turbo-clean")
    turbo_stats = runtime_stats(turbo_runs)
    large = read_json_opt(bench_dir / "large-v3-clean" / "run_metadata.json")
    if large:
        large.setdefault("transcript_text_sha256", transcript_hash(bench_dir / "large-v3-clean" / "transcript.json"))
    existing = read_json_opt(DATA / "evaluation" / "clean_gpu_benchmark.json") or {}
    status = "completed" if turbo_runs else existing.get("status", "pending")
    version_source = turbo_runs[0] if turbo_runs else large or {}
    nvidia_text = baseline.get("nvidia_smi", "")
    driver_match = re.search(r"Driver Version:\s*([^\s]+)", nvidia_text)
    cuda_match = re.search(r"CUDA Version:\s*([^\s]+)", nvidia_text)
    payload = {
        "status": status,
        "environment": {
            "gpu": baseline.get("gpu", existing.get("gpu", "NVIDIA GeForce RTX 3050 Laptop GPU")),
            "total_vram_mb": baseline.get("total_vram_mb", existing.get("total_vram_mb", 4096)),
            "driver_version": driver_match.group(1) if driver_match else None,
            "cuda_version": cuda_match.group(1) if cuda_match else None,
            "python_version": platform.python_version(),
            "package_versions": version_source.get("package_versions", {}),
        },
        "baseline": baseline,
        "clip": clip_meta,
        "large_v3_turbo_clean_runs": turbo_runs,
        "large_v3_turbo_stability": turbo_stats,
        "large_v3_clean": large,
        "manual_transcript_quality": "pending_manual_review",
    }
    if existing and not turbo_runs:
        payload.update({k: v for k, v in existing.items() if k not in payload or not payload[k]})
    write_json(DATA / "evaluation" / "clean_gpu_benchmark.json", payload)

    lines = [
        "# Clean GPU Benchmark",
        "",
        f"Status: {status}",
        "",
        "## Environment",
        "",
        f"- GPU: {payload['environment'].get('gpu')}",
        f"- Driver: {fmt(payload['environment'].get('driver_version'))}",
        f"- CUDA: {fmt(payload['environment'].get('cuda_version'))}",
        f"- Total VRAM: {payload['environment'].get('total_vram_mb')} MiB",
        f"- Python: {fmt(payload['environment'].get('python_version'))}",
        f"- faster-whisper: {fmt((payload['environment'].get('package_versions') or {}).get('faster_whisper'))}",
        f"- CTranslate2: {fmt((payload['environment'].get('package_versions') or {}).get('ctranslate2'))}",
        "- Measurement scope: total-device VRAM from `nvidia-smi`; Windows/WSL attribution may be incomplete.",
        "",
        "## Baseline",
        "",
        f"- Classification: {baseline.get('classification', 'not measured')}",
        f"- VRAM min/max/median: {fmt((baseline.get('stats') or {}).get('vram_mb', {}).get('min'))} / {fmt((baseline.get('stats') or {}).get('vram_mb', {}).get('max'))} / {fmt((baseline.get('stats') or {}).get('vram_mb', {}).get('median'))} MiB",
        f"- Utilization min/max/median: {fmt((baseline.get('stats') or {}).get('utilization_percent', {}).get('min'))} / {fmt((baseline.get('stats') or {}).get('utilization_percent', {}).get('max'))} / {fmt((baseline.get('stats') or {}).get('utilization_percent', {}).get('median'))} %",
        f"- Power min/max/median: {fmt((baseline.get('stats') or {}).get('power_w', {}).get('min'))} / {fmt((baseline.get('stats') or {}).get('power_w', {}).get('max'))} / {fmt((baseline.get('stats') or {}).get('power_w', {}).get('median'))} W",
        f"- Temperature min/max/median: {fmt((baseline.get('stats') or {}).get('temperature_c', {}).get('min'))} / {fmt((baseline.get('stats') or {}).get('temperature_c', {}).get('max'))} / {fmt((baseline.get('stats') or {}).get('temperature_c', {}).get('median'))} C",
        f"- P-states observed: {', '.join(baseline.get('pstates', [])) if baseline.get('pstates') else 'not measured'}",
        "- Visible processes: none reported by `nvidia-smi` / `pmon`" if baseline else "- Visible processes: not measured",
        "",
        "## Fixed Benchmark Clip",
        "",
        f"- Source: `{clip_meta.get('source_audio', 'not measured')}`",
        f"- Clip: `{clip_meta.get('clip_path', 'not measured')}`",
        f"- Requested start/end: {fmt(clip_meta.get('requested_start_seconds'))}s / {fmt(clip_meta.get('requested_end_seconds'))}s",
        f"- ffprobe duration: {fmt((clip_meta.get('ffprobe') or {}).get('format', {}).get('duration'))}s",
        f"- SHA-256: `{clip_meta.get('sha256', 'not measured')}`",
        "",
        "## large-v3-turbo Clean Runs",
    ]
    if turbo_runs:
        for run in turbo_runs:
            lines += [
                "",
                f"### {run.get('run_id')}",
                f"- Status: {fmt(run.get('status'))}",
                f"- Runtime: {fmt(run.get('runtime_seconds'))}s",
                f"- RTF: {fmt(run.get('realtime_factor'))}",
                f"- Model load: {fmt(run.get('model_load_seconds'))}s",
                f"- Baseline / peak VRAM: {fmt(run.get('baseline_vram_mb'))} / {fmt(run.get('peak_vram_mb'))} MiB",
                f"- Segments / chars: {fmt(run.get('segment_count'))} / {fmt(run.get('transcript_char_count'))}",
                f"- Transcript hash: `{fmt(run.get('transcript_text_sha256'))}`",
            ]
        if turbo_stats:
            lines += [
                "",
                "## Stability",
                "",
                f"- Mean runtime: {fmt(turbo_stats.get('mean_runtime_seconds'))}s",
                f"- Median runtime: {fmt(turbo_stats.get('median_runtime_seconds'))}s",
                f"- Stddev runtime: {fmt(turbo_stats.get('stddev_runtime_seconds'))}s",
                f"- Runtime variation: {fmt(turbo_stats.get('runtime_variation_percent'))}%",
                f"- Classification: {turbo_stats.get('stability_classification')}",
            ]
            hashes = sorted({str(r.get("transcript_text_sha256")) for r in turbo_runs})
            lines.append(f"- Transcript hashes identical: {'yes' if len(hashes) == 1 else 'no'}")
    else:
        lines.append("- Not run.")
    lines += ["", "## large-v3 Same Clip"]
    if large:
        lines += [
            f"- Status: {fmt(large.get('status'))}",
            f"- Failure stage: {fmt(large.get('failure_stage'))}",
            f"- Failure reason: {fmt(large.get('failure_reason'))}",
            f"- Runtime: {fmt(large.get('runtime_seconds'))}s",
            f"- RTF: {fmt(large.get('realtime_factor'))}",
            f"- Baseline / peak VRAM: {fmt(large.get('baseline_vram_mb'))} / {fmt(large.get('peak_vram_mb'))} MiB",
            f"- Segments / chars: {fmt(large.get('segment_count'))} / {fmt(large.get('transcript_char_count'))}",
            f"- Transcript hash: `{fmt(large.get('transcript_text_sha256'))}`",
        ]
    else:
        lines.append("- Not run.")
    if existing.get("reason") and not turbo_runs:
        lines += ["", "## Blocker", "", str(existing["reason"])]
    lines += [
        "",
        "## Quality",
        "",
        "No human transcript quality judgment is included in this benchmark. Transcript quality remains pending manual review.",
        "",
        "## Limitations",
        "",
        "- This is a controlled desktop baseline, not a perfect zero-background lab run.",
        "- Windows desktop / WSL GPU utilization attribution may be incomplete.",
        "- Transcript quality is not judged by this benchmark.",
    ]
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "clean_gpu_benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {DATA / 'evaluation' / 'clean_gpu_benchmark.json'}")
    print(f"Wrote {REPORTS / 'clean_gpu_benchmark.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
