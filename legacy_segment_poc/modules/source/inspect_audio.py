#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from modules.common import DATA, path_for_report, project_relative_path, run_command, write_json


def inspect(path: Path) -> dict[str, Any]:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found")
    args = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration,bit_rate:stream=codec_name,codec_type,sample_rate,channels,bit_rate",
        "-of",
        "json",
        str(path),
    ]
    code, out, err = run_command(args, timeout=60)
    if code != 0:
        raise RuntimeError(f"ffprobe failed: {err}")
    data = json.loads(out)
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    stream = audio_streams[0] if audio_streams else {}
    decode_code, _, decode_err = run_command(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], timeout=180)
    fmt = data.get("format", {})
    return {
        "project_relative_path": path_for_report(path),
        "runtime_absolute_path": str(path.resolve()),
        "audio_path": path_for_report(path),
        "duration_seconds": float(fmt["duration"]) if fmt.get("duration") else None,
        "format": fmt.get("format_name"),
        "codec": stream.get("codec_name"),
        "bitrate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else fmt.get("bit_rate"),
        "stream_bitrate": int(stream["bit_rate"]) if str(stream.get("bit_rate", "")).isdigit() else stream.get("bit_rate"),
        "sample_rate": int(stream["sample_rate"]) if str(stream.get("sample_rate", "")).isdigit() else stream.get("sample_rate"),
        "channels": stream.get("channels"),
        "decodable": decode_code == 0,
        "decode_error": decode_err if decode_code != 0 else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", required=True)
    args = parser.parse_args()
    path = DATA / "audio" / args.episode / "episode.mp3"
    if not path.exists():
        print(f"Audio not found: {path}", file=sys.stderr)
        return 2
    try:
        metadata = inspect(path)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 3
    write_json(DATA / "audio" / args.episode / "audio_metadata.json", metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
