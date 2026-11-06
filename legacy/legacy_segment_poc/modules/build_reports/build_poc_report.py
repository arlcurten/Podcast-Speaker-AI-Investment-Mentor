#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from modules.common import DATA, REPORTS


def read_json_opt(path: Path) -> Any | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def first_existing(paths: list[Path]) -> Any | None:
    for path in paths:
        data = read_json_opt(path)
        if data is not None:
            return data
    return None


def manifest_count() -> int:
    path = DATA / "rss_sources" / "episodes.jsonl"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def load_comparison() -> list[dict[str, Any]]:
    path = DATA / "legacy" / "evaluation" / "benchmark_comparison.json"
    return read_json_opt(path) or []


def read_yamlish_config() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "config" / "episodes.yaml"
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def fmt(value: Any) -> str:
    if value is None or value == "":
        return "not measured"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def main() -> int:
    env = read_json_opt(DATA / "environment_inspection.json") or {}
    ingestion = read_json_opt(DATA / "rss_sources" / "rss_ingestion_metadata.json") or {}
    ep_config = read_yamlish_config()
    manifest_path = DATA / "rss_sources" / "episodes.jsonl"
    if manifest_path.exists():
        ep672_found: str = "yes" if any("EP672" in line for line in manifest_path.read_text(encoding="utf-8").splitlines()) else "no"
    else:
        ep672_found = "not measured"
    downloads = sorted((DATA / "audio").glob("*/download_metadata.json"))
    audio_metas = sorted((DATA / "audio").glob("*/audio_metadata.json"))
    comparison = load_comparison()
    audit = read_json_opt(DATA / "evaluation" / "EP674" / "segment_audit.json") or {}
    merge = read_json_opt(DATA / "evaluation" / "EP674" / "merge_summary.json") or {}
    human_review_path = REPORTS / "EP674_human_review.md"
    lines: list[str] = []
    lines += [
        "# Gooaye Mentor Local POC Report",
        "",
        "This report is generated from local artifacts only. Items not present on disk are marked as not executed or not measured.",
        "",
        "## Environment",
        f"- OS: {fmt(env.get('operating_system'))}",
        f"- CPU: {fmt(env.get('cpu_model'))}",
        f"- CPU cores: logical={fmt(env.get('cpu_core_count_logical'))}, physical={fmt(env.get('cpu_core_count_physical'))}",
        f"- RAM: total={fmt(env.get('total_ram_gb'))} GB, available at inspection={fmt(env.get('available_ram_gb'))} GB",
        f"- GPU / VRAM: {fmt(env.get('gpus') or ('none' if env.get('has_nvidia_gpu') is False else None))}",
        f"- Disk: total={fmt(env.get('total_disk_gb'))} GB, available at inspection={fmt(env.get('available_disk_gb'))} GB",
        f"- Python: {fmt(env.get('python_version'))}",
        f"- ffmpeg: {fmt(env.get('ffmpeg_version'))}",
        f"- ffprobe: {fmt(env.get('ffprobe_version'))}",
        f"- Backend: {fmt(env.get('recommended_transcription_backend'))}",
        "",
        "## Data ingestion",
        f"- Requested RSS URL: {fmt(ep_config.get('requested_rss_url'))}",
        "- Requested RSS result: HTTP 404 during local execution; not treated as successful ingestion.",
        f"- Active RSS URL used for manifest: {fmt(ep_config.get('active_rss_url') or ingestion.get('rss_url'))}",
        f"- Resolved RSS URL: {fmt(ingestion.get('resolved_rss_url'))}",
        f"- Discovery method: {fmt(ingestion.get('discovery_method'))}",
        f"- Apple collection ID: {fmt(ingestion.get('apple_collection_id'))}",
        f"- Channel title: {fmt(ingestion.get('channel_title'))}",
        f"- Fetched at: {fmt(ingestion.get('fetched_at'))}",
        f"- RSS success: {'yes' if ingestion else 'not executed'}",
        f"- Manifest count: {manifest_count()}",
        f"- RSS snapshot: {fmt(ingestion.get('snapshot'))}",
        f"- EP672 found in manifest: {ep672_found}",
    ]
    for meta_path in downloads:
        dl = read_json_opt(meta_path) or {}
        audio = read_json_opt(meta_path.parent / "audio_metadata.json") or {}
        ep = dl.get("episode", {})
        lines += [
            "",
            f"### Downloaded episode: {meta_path.parent.name}",
            f"- Title: {fmt(ep.get('episode_title'))}",
            f"- Publication date: {fmt(ep.get('publication_date'))}",
            f"- MP3 downloaded: {'yes' if (meta_path.parent / 'episode.mp3').exists() else 'no'}",
            f"- Size: {fmt(dl.get('actual_size_bytes'))} bytes",
            f"- RSS enclosure length: {fmt(dl.get('rss_enclosure_length', dl.get('rss_size_bytes')))}",
            f"- RSS length valid: {fmt(dl.get('rss_length_valid'))}",
            f"- HTTP Content-Length: {fmt(dl.get('http_content_length', dl.get('http_content_length_bytes')))}",
            f"- Actual file size: {fmt(dl.get('actual_file_size', dl.get('actual_size_bytes')))}",
            f"- Duration: {fmt(audio.get('duration_seconds'))} seconds",
            f"- SHA-256: {fmt(dl.get('sha256'))}",
            f"- Content-Type valid: {fmt(dl.get('content_type_valid'))}",
            f"- RSS size matches actual: {fmt(dl.get('rss_size_matches_actual'))}",
            "- Integrity validation priority: HTTP Content-Length, actual file size, ffprobe, SHA-256. RSS enclosure length is advisory only.",
        ]
    lines += ["", "## Benchmark results"]
    if not comparison:
        lines.append("- No transcription benchmark completed.")
    for row in comparison:
        lines += [
            "",
            f"### {fmt(row.get('configuration'))}",
            f"- Model: {fmt(row.get('model'))}",
            f"- Benchmark scope: {'partial audio' if 'partial' in str(row.get('configuration')) else 'full episode'}",
            f"- Device: {fmt(row.get('device'))}",
            f"- Compute type: {fmt(row.get('compute_type'))}",
            f"- Audio duration: {fmt(row.get('audio_duration_seconds'))} seconds",
            f"- Runtime: {fmt(row.get('runtime_seconds'))} seconds",
            f"- Realtime factor: {fmt(row.get('realtime_factor'))}",
            f"- Peak RAM: {fmt(row.get('peak_ram_mb'))} MB",
            f"- Peak VRAM: {fmt(row.get('peak_vram_mb'))} MB",
            f"- Transcript output size: {fmt(row.get('transcript_length'))} chars",
            f"- Status: {fmt(row.get('status'))}",
            f"- Failure reason: {fmt(row.get('failure_reason'))}",
        ]
    if audit:
        ds = audit.get("duration_seconds", {})
        ts = audit.get("text_characters", {})
        counts = audit.get("counts", {})
        lines += [
            "",
            "## Segment audit",
            f"- Raw segment count: {fmt(audit.get('segment_count'))}",
            f"- Faster-whisper segment shape: {fmt(audit.get('is_faster_whisper_segment_shape'))}",
            f"- Appears to be word timestamps: {fmt(audit.get('appears_to_be_word_timestamps'))}",
            f"- Duration mean / median / p10 / p90 / max: {fmt(ds.get('mean'))} / {fmt(ds.get('median'))} / {fmt(ds.get('p10'))} / {fmt(ds.get('p90'))} / {fmt(ds.get('max'))} seconds",
            f"- Text length mean / median / p10 / p90: {fmt(ts.get('mean'))} / {fmt(ts.get('median'))} / {fmt(ts.get('p10'))} / {fmt(ts.get('p90'))} chars",
            f"- <0.5s: {fmt(counts.get('duration_lt_0_5s'))}; <1s: {fmt(counts.get('duration_lt_1s'))}; <3 CJK chars: {fmt(counts.get('cjk_chars_lt_3'))}; punctuation/blank only: {fmt(counts.get('punctuation_or_blank_only'))}",
            f"- Adjacent duplicate text: {fmt(counts.get('adjacent_duplicate_text'))}",
            f"- Overlap / same timestamp / non-monotonic: {fmt(counts.get('overlap_count'))} / {fmt(counts.get('same_timestamp_count'))} / {fmt(counts.get('non_monotonic_timestamp_count'))}",
            f"- Assessment: {fmt(audit.get('fragmentation_assessment'))}",
        ]
    if merge:
        raw = merge.get("raw_stats", {})
        merged = merge.get("merged_stats", {})
        files = merge.get("file_sizes_bytes", {})
        lines += [
            "",
            "## Merged transcript layer",
            "- Deterministic readability layer only; not semantic topic segmentation, no LLM, no text correction.",
            f"- Raw count: {fmt(raw.get('count'))}; merged count: {fmt(merged.get('count'))}",
            f"- Raw mean/median duration: {fmt(raw.get('mean_duration_seconds'))} / {fmt(raw.get('median_duration_seconds'))} seconds",
            f"- Merged mean/median duration: {fmt(merged.get('mean_duration_seconds'))} / {fmt(merged.get('median_duration_seconds'))} seconds",
            f"- Raw mean text length: {fmt(raw.get('mean_text_length'))}; merged mean text length: {fmt(merged.get('mean_text_length'))}",
            f"- Output sizes: {', '.join(f'{name}={size} bytes' for name, size in files.items()) if files else 'not measured'}",
        ]
    successful = [r for r in comparison if r.get("status") == "success" and r.get("realtime_factor")]
    rtf = min([float(r["realtime_factor"]) for r in successful], default=None)
    local_hours_500 = 500 * rtf if rtf else None
    local_hours_620 = 620 * rtf if rtf else None
    lines += [
        "",
        "## Quality assessment",
        "- Current human review file exists in `reports/EP674_human_review.md`.",
        f"- Human review package exists: {'yes' if human_review_path.exists() else 'no'}",
        "- Transcript has not completed human quality validation.",
        "- Chinese comprehensibility, company-name quality, ticker quality, financial terminology quality, timestamp quality, hallucination, repetition, and large-v3 vs turbo quality difference require human listening review. This script does not fabricate those judgments.",
        "- Completed raw transcript available: EP674 large-v3-turbo full episode.",
        "- Large-v3 GPU runs on this 4 GB RTX 3050 failed with CUDA out of memory in tested partial configurations; only a 60-second CPU int8 smoke test completed.",
        "- Existing peak_vram_mb values from completed runs are not reliable because the first script version used PyTorch CUDA counters, which do not capture CTranslate2 allocations. During the turbo full run, a spot check with nvidia-smi showed about 3454 MB used out of 4096 MB. The transcription script has been updated to poll nvidia-smi for future runs.",
        "- Turbo runtime may be affected by other GPU workload and should not be treated as a clean performance baseline.",
        "",
        "## Content-filtering feasibility",
        f"- Generated segment sample: {'yes' if (DATA / 'evaluation' / 'content_filtering_sample.csv').exists() else 'not executed'}",
        "- Middle-third market-analysis judgment requires manual review of sampled transcript and source audio.",
        "- No LLM classifier was created in this POC.",
        "- Do not proceed to a 672/674-episode full batch from current artifacts alone.",
        "",
        "## Cloud extrapolation",
        "- Known corpus assumption: 672 episodes, 500-620 audio hours, 20-35 GB MP3.",
        f"- Local full-corpus estimate from best measured RTF: {fmt(local_hours_500)} to {fmt(local_hours_620)} wall-clock hours, assuming serial processing and same audio/model mix.",
        "- Single AWS A10G or GCP L4 estimate is not locally measured. A reasonable planning range for faster-whisper large-v3/turbo is hours to a few days depending on model, batching, VAD, I/O, and CPU decode overhead; validate with a 20-episode cloud pilot before full processing.",
        "- Strategy recommendation should be based on measured quality: use turbo if semantic/ticker quality is sufficient; use large-v3 on difficult episodes or audit samples if it materially improves errors.",
        "- Cloud work disk suggestion: 100-200 GB for pilot/full batch staging, logs, and temporary decoded audio.",
        "- Object storage suggestion: 100 GB minimum for MP3, transcripts, metadata, and rerun headroom.",
        "- Before a 20-episode cloud pilot, complete basic manual review of the prepared windows and rerun clean GPU benchmarks when local GPU is idle.",
        "- Historical local GPU benchmark notes are documented in `reports/legacy/pending_gpu_benchmarks.md`.",
        "",
        "## Go / No-Go decision",
        f"- RSS ingestion: {'Go' if ingestion and manifest_count() > 0 else 'No-Go'}",
        f"- MP3 acquisition: {'Go' if downloads else 'No-Go'}",
        f"- Local transcription: {'Go' if successful else 'Conditional' if comparison else 'No-Go'}",
        "- Transcript quality: Conditional until manual review is completed.",
        f"- Content classification feasibility: {'Conditional' if (DATA / 'evaluation' / 'content_filtering_sample.csv').exists() else 'No-Go'}",
        f"- 20-episode cloud pilot: {'Proceed after manual review and clean benchmark' if successful else 'Needs more testing'}",
    ]
    out = REPORTS / "legacy" / "local_poc_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
