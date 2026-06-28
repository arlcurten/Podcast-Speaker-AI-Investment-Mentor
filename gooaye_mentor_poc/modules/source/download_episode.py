#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import requests

from modules.common import DATA, find_episode, load_manifest, path_for_report, sha256_file, utc_now, write_json


MIN_REASONABLE_AUDIO_BYTES = 256 * 1024


def parse_int(value: object) -> int | None:
    return int(value) if str(value or "").isdigit() else None


def rss_length_valid(value: int | None) -> bool:
    return value is not None and value >= MIN_REASONABLE_AUDIO_BYTES


def download(url: str, dest: Path, timeout: int, retries: int) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    headers = {"User-Agent": "gooaye-mentor-poc/0.1"}
    last_exc: Exception | None = None
    start = time.perf_counter()
    http_status = None
    content_type = None
    content_length = None
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout, headers=headers) as response:
                http_status = response.status_code
                response.raise_for_status()
                content_type = response.headers.get("Content-Type")
                content_length = response.headers.get("Content-Length")
                with part.open("wb") as fh:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            fh.write(chunk)
            part.replace(dest)
            break
        except Exception as exc:
            last_exc = exc
            if part.exists():
                part.unlink()
            if attempt == retries:
                raise
            sleep_s = min(2 ** attempt, 30)
            logging.warning("Download failed on attempt %s/%s: %s; sleeping %ss", attempt, retries, exc, sleep_s)
            time.sleep(sleep_s)
    elapsed = time.perf_counter() - start
    return {
        "http_status": http_status,
        "http_content_type": content_type,
        "http_content_length": int(content_length) if content_length and content_length.isdigit() else content_length,
        "download_runtime_seconds": elapsed,
        "last_exception": str(last_exc) if last_exc and not dest.exists() else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", required=True, help="episode_id, number, guid, or title substring")
    parser.add_argument("--manifest", default=str(DATA / "manifests" / "episodes.jsonl"))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        episode = find_episode(load_manifest(Path(args.manifest)), args.episode)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    url = episode.get("audio_url")
    if not url:
        print("Episode has no audio_url", file=sys.stderr)
        return 3
    episode_id = str(episode["episode_id"])
    out_dir = DATA / "audio" / episode_id
    dest = out_dir / "episode.mp3"
    metadata_path = out_dir / "download_metadata.json"
    skipped = False
    if dest.exists() and dest.stat().st_size > 0:
        skipped = True
        result: dict[str, Any] = {"download_runtime_seconds": 0.0, "skipped_existing": True}
    else:
        try:
            result = download(url, dest, args.timeout, args.retries)
        except Exception as exc:
            print(f"Failed to download episode: {exc}", file=sys.stderr)
            return 4
    actual_size = dest.stat().st_size
    rss_size_raw = episode.get("rss_enclosure_length", episode.get("audio_length_from_rss"))
    rss_size = parse_int(rss_size_raw)
    rss_valid = rss_length_valid(rss_size)
    http_len = result.get("http_content_length")
    metadata = {
        "episode": episode,
        "project_relative_path": path_for_report(dest),
        "runtime_absolute_path": str(dest.resolve()),
        "audio_path": path_for_report(dest),
        "downloaded_at": utc_now(),
        "skipped_existing": skipped,
        "actual_file_size": actual_size,
        "actual_size_bytes": actual_size,
        "sha256": sha256_file(dest),
        "rss_enclosure_length": rss_size,
        "rss_size_bytes": rss_size,
        "rss_length_valid": rss_valid,
        "http_content_length": http_len,
        "http_content_length_bytes": http_len,
        "rss_size_matches_actual": rss_size == actual_size if rss_valid else None,
        "http_size_matches_actual": http_len == actual_size if isinstance(http_len, int) else None,
        "content_type_valid": str(result.get("http_content_type", "")).lower().startswith(("audio/", "application/octet-stream")) if result.get("http_content_type") else None,
        "integrity_validation_priority": ["http_content_length", "actual_file_size", "ffprobe", "sha256"],
        "rss_length_note": "RSS enclosure length is treated as advisory only; invalid if below minimum reasonable audio size.",
        **result,
    }
    write_json(metadata_path, metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
