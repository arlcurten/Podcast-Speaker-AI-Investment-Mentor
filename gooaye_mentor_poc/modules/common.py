from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
DATA = ROOT / "data"
CONFIG = ROOT / "config"
REPORTS = ROOT / "reports"
TERMINOLOGY = PROJECT_ROOT / "docs" / "terminology.tsv"


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def project_relative_path(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return None


def path_for_report(path: Path) -> str:
    return project_relative_path(path) or str(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def load_terminology(path: Path | None = None) -> list[dict[str, str]]:
    source = path or TERMINOLOGY
    rows: list[dict[str, str]] = []
    with source.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            source_text = str(row.get("source_text", "")).strip()
            target_text = str(row.get("target_text", "")).strip()
            term_type = str(row.get("type", "")).strip()
            status = str(row.get("status", "")).strip()
            if source_text:
                rows.append({
                    "source_text": source_text,
                    "target_text": target_text,
                    "type": term_type,
                    "category": str(row.get("category", "")).strip(),
                    "status": status,
                    "notes": str(row.get("notes", "")).strip(),
                })
    return rows


def terminology_replacements(path: Path | None = None) -> list[dict[str, str]]:
    rows = []
    for row in load_terminology(path):
        if row["type"] not in {"alias", "correction"}:
            continue
        if row["status"] not in {"active", "human_reviewed"}:
            continue
        if row["source_text"] and row["target_text"]:
            rows.append({
                "source": row["source_text"],
                "replacement": row["target_text"],
                "category": row["category"],
                "notes": row["notes"],
            })
    return sorted(rows, key=lambda item: len(item["source"]), reverse=True)


def terminology_prompt(path: Path | None = None) -> str:
    terms: list[str] = []
    seen: set[str] = set()
    for row in load_terminology(path):
        if row["status"] not in {"active", "human_reviewed"}:
            continue
        candidates = []
        if row["type"] == "preserve":
            candidates = [row["target_text"] or row["source_text"]]
        elif row["type"] in {"alias", "correction"}:
            candidates = [row["target_text"]]
        for term in candidates:
            term = term.strip()
            if term and term not in seen:
                terms.append(term)
                seen.add(term)
    return "\n".join(terms)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_command(args: list[str], timeout: int | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def slugify_episode(value: str) -> str:
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return value[:120] or "episode"


def parse_duration_to_seconds(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    parts = value.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(value)
    except ValueError:
        return None


def load_manifest(path: Path | None = None) -> list[dict[str, Any]]:
    manifest = path or DATA / "rss_sources" / "episodes.jsonl"
    rows: list[dict[str, Any]] = []
    with manifest.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def find_episode(rows: list[dict[str, Any]], episode_id: str) -> dict[str, Any]:
    query = episode_id.lower()
    for row in rows:
        candidates = [
            str(row.get("episode_id", "")),
            str(row.get("episode_number", "")),
            str(row.get("guid", "")),
            str(row.get("episode_title", "")),
        ]
        if any(query == c.lower() or query in c.lower() for c in candidates):
            return row
    raise KeyError(f"Episode not found in manifest: {episode_id}")
