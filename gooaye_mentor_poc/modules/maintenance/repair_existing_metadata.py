#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from modules.common import DATA, ROOT, path_for_report, project_relative_path, write_json


OLD_ROOT = "/home/g9161/projects/gooaye_mentor_poc"
MOVED_NOTE = "Repository moved under /home/g9161/projects/Podcast-Speaker-AI-Investment-Mentor; project-relative paths are now preferred."


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def repair_run_metadata() -> list[str]:
    changed: list[str] = []
    for path in sorted((DATA / "transcripts").glob("*/**/run_metadata.json")):
        meta = load(path)
        if meta.get("backend") == "faster-whisper":
            source_audio = meta.get("source_audio")
            if isinstance(source_audio, str) and source_audio.startswith(OLD_ROOT):
                rel = source_audio.removeprefix(OLD_ROOT).lstrip("/")
                meta["original_source_audio_path"] = source_audio
                meta["source_audio"] = rel
                meta["source_audio_project_relative_path"] = rel
                meta["source_audio_runtime_absolute_path"] = str((ROOT / rel).resolve())
                meta["path_migration_note"] = MOVED_NOTE
            meta["peak_vram_mb"] = None
            meta["vram_measurement_reliable"] = False
            meta["vram_measurement_note"] = (
                "Original run used PyTorch CUDA counters, which do not capture CTranslate2 allocations. "
                "During EP674 large-v3-turbo full transcription, a manual nvidia-smi spot check showed "
                "about 3454 MiB used out of 4096 MiB, but this is not a reliable peak measurement and may "
                "include other GPU workloads."
            )
            meta.setdefault("vram_measurement_scope", "unknown for original run")
            meta.setdefault("vram_sampling_interval_seconds", None)
            meta.setdefault("baseline_vram_mb", None)
            write_json(path, meta)
            transcript_path = path.parent / "transcript.json"
            if transcript_path.exists():
                payload = json.loads(transcript_path.read_text(encoding="utf-8"))
                if isinstance(payload.get("metadata"), dict):
                    tmeta = payload["metadata"]
                    source_audio = tmeta.get("source_audio")
                    if isinstance(source_audio, str) and source_audio.startswith(OLD_ROOT):
                        rel = source_audio.removeprefix(OLD_ROOT).lstrip("/")
                        tmeta["original_source_audio_path"] = source_audio
                        tmeta["source_audio"] = rel
                        tmeta["source_audio_project_relative_path"] = rel
                        tmeta["source_audio_runtime_absolute_path"] = str((ROOT / rel).resolve())
                        tmeta["path_migration_note"] = MOVED_NOTE
                    payload["metadata"].update({
                        "peak_vram_mb": None,
                        "vram_measurement_reliable": False,
                        "vram_measurement_note": meta["vram_measurement_note"],
                        "vram_measurement_scope": meta.get("vram_measurement_scope"),
                        "vram_sampling_interval_seconds": meta.get("vram_sampling_interval_seconds"),
                        "baseline_vram_mb": meta.get("baseline_vram_mb"),
                    })
                    write_json(transcript_path, payload)
            changed.append(str(path))
    return changed


def repair_download_metadata() -> str | None:
    path = DATA / "audio" / "EP674" / "download_metadata.json"
    if not path.exists():
        return None
    meta = load(path)
    audio_path = meta.get("audio_path")
    if isinstance(audio_path, str) and audio_path.startswith(OLD_ROOT):
        rel = audio_path.removeprefix(OLD_ROOT).lstrip("/")
        meta["original_audio_path"] = audio_path
        meta["audio_path"] = rel
        meta["project_relative_path"] = rel
        meta["runtime_absolute_path"] = str((ROOT / rel).resolve())
        meta["path_migration_note"] = MOVED_NOTE
    rss_len = meta.get("rss_enclosure_length", meta.get("rss_size_bytes"))
    try:
        rss_len_int = int(rss_len) if rss_len is not None else None
    except (TypeError, ValueError):
        rss_len_int = None
    actual = meta.get("actual_file_size", meta.get("actual_size_bytes"))
    http = meta.get("http_content_length", meta.get("http_content_length_bytes"))
    meta.update({
        "rss_enclosure_length": rss_len_int,
        "http_content_length": http,
        "actual_file_size": actual,
        "rss_length_valid": bool(rss_len_int is not None and rss_len_int >= 256 * 1024),
        "rss_size_matches_actual": bool(rss_len_int == actual) if rss_len_int is not None and rss_len_int >= 256 * 1024 else None,
        "integrity_validation_priority": ["http_content_length", "actual_file_size", "ffprobe", "sha256"],
        "rss_length_note": "RSS enclosure length is advisory only. SoundOn returned 1 for EP674, so it is marked invalid and not used as expected size.",
    })
    write_json(path, meta)
    return str(path)


def repair_audio_metadata() -> str | None:
    path = DATA / "audio" / "EP674" / "audio_metadata.json"
    if not path.exists():
        return None
    meta = load(path)
    audio_path = meta.get("audio_path")
    if isinstance(audio_path, str) and audio_path.startswith(OLD_ROOT):
        rel = audio_path.removeprefix(OLD_ROOT).lstrip("/")
        meta["original_audio_path"] = audio_path
        meta["audio_path"] = rel
        meta["project_relative_path"] = rel
        meta["runtime_absolute_path"] = str((ROOT / rel).resolve())
        meta["path_migration_note"] = MOVED_NOTE
        write_json(path, meta)
    return str(path)


def repair_segment_audit() -> str | None:
    path = DATA / "evaluation" / "EP674_segment_audit.json"
    if not path.exists():
        return None
    meta = load(path)
    transcript_path = meta.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path.startswith(OLD_ROOT):
        rel = transcript_path.removeprefix(OLD_ROOT).lstrip("/")
        meta["original_transcript_path"] = transcript_path
        meta["transcript_path"] = rel
        meta["project_relative_path"] = rel
        meta["runtime_absolute_path"] = str((ROOT / rel).resolve())
        meta["path_migration_note"] = MOVED_NOTE
        write_json(path, meta)
    return str(path)


def repair_rss_metadata() -> str | None:
    path = DATA / "manifests" / "rss_ingestion_metadata.json"
    if not path.exists():
        return None
    meta = load(path)
    meta.setdefault("requested_rss_url", "https://feeds.soundon.fm/podcasts/4f2a74ec-cc7a-4284-be4b-74b882da701c")
    meta.setdefault("resolved_rss_url", "https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml")
    meta.setdefault("rss_url", meta["resolved_rss_url"])
    meta.setdefault("discovery_method", "apple_lookup_manual_after_requested_url_404")
    meta.setdefault("requested_rss_error", "404 Client Error: Not Found for requested RSS during local POC")
    meta.setdefault("apple_collection_id", "1500839292")
    meta.setdefault("channel_title", meta.get("podcast_title"))
    meta.setdefault("fetched_at", meta.get("ingestion_timestamp"))
    write_json(path, meta)
    return str(path)


def main() -> int:
    changed = {
        "run_metadata": repair_run_metadata(),
        "audio_metadata": repair_audio_metadata(),
        "download_metadata": repair_download_metadata(),
        "segment_audit": repair_segment_audit(),
        "rss_metadata": repair_rss_metadata(),
    }
    print(json.dumps(changed, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
