#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from modules.common import DATA, TERMINOLOGY, path_for_report, sha256_file, terminology_replacements, utc_now, write_json


def glossary_hash(rows: list[dict[str, str]]) -> str:
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_opencc(config_name: str) -> tuple[Any, str]:
    try:
        import opencc  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "OpenCC is required for deterministic Traditional Chinese normalization. "
            "Install requirements.txt or run: python3 -m pip install opencc-python-reimplemented"
        ) from exc
    version = getattr(opencc, "__version__", "unknown")
    return opencc.OpenCC(config_name), str(version)


def normalize_text(text: str, converter: Any, glossary: list[dict[str, str]]) -> str:
    normalized = converter.convert(text)
    for row in glossary:
        normalized = normalized.replace(row["source"], row["replacement"])
    return normalized


def normalize_merged_segments(
    segments: list[dict[str, Any]],
    converter: Any,
    glossary: list[dict[str, str]],
    method: str,
    version: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for seg in segments:
        raw_text = str(seg.get("text", ""))
        rows.append({
            "id": int(seg["merged_id"]),
            "merged_id": int(seg["merged_id"]),
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "raw_text": raw_text,
            "normalized_text_zh_tw": normalize_text(raw_text, converter, glossary),
            "normalization_method": method,
            "normalization_version": version,
            "source_segment_ids": list(seg.get("source_segment_ids", [])),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", default="EP674")
    parser.add_argument("--configuration", default="large-v3-turbo")
    parser.add_argument("--opencc-config", default="s2twp")
    parser.add_argument("--terminology", type=Path, default=TERMINOLOGY)
    args = parser.parse_args()

    base = DATA / "transcripts" / args.episode
    raw_path = base / f"raw_{args.configuration}_transcript.json"
    merged_path = base / f"derived_{args.configuration}_merged_transcript.json"
    normalized_path = base / f"derived_{args.configuration}_normalized_merged_transcript_zh_tw.json"
    merged_payload = json.loads(merged_path.read_text(encoding="utf-8"))

    converter, opencc_version = load_opencc(args.opencc_config)
    replacements = terminology_replacements(args.terminology)
    method = f"OpenCC {args.opencc_config} + docs/terminology.tsv replacements"
    version = f"opencc-python-reimplemented {opencc_version}"
    metadata = {
        "episode_id": args.episode,
        "configuration": args.configuration,
        "created_at": utc_now(),
        "source_raw_transcript": path_for_report(raw_path),
        "source_merged_transcript": path_for_report(merged_path),
        "source_raw_transcript_sha256": sha256_file(raw_path),
        "source_merged_transcript_sha256": sha256_file(merged_path),
        "normalization_method": method,
        "normalization_version": version,
        "opencc_config": args.opencc_config,
        "terminology_path": path_for_report(args.terminology),
        "terminology_replacements_sha256": glossary_hash(replacements),
        "terminology_replacement_count": len(replacements),
        "does_not_modify_raw_transcript": True,
        "uses_llm": False,
    }

    merged_rows = normalize_merged_segments(merged_payload.get("segments", []), converter, replacements, method, version)
    write_json(normalized_path, {"metadata": metadata, "segments": merged_rows})
    print(json.dumps({
        "merged_segments": len(merged_rows),
        "merged_output": path_for_report(normalized_path),
        "method": method,
        "version": version,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
