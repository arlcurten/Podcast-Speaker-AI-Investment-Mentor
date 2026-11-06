from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TERMINOLOGY = PROJECT_ROOT / "docs" / "terminology.tsv"


def load_project_dotenv() -> None:
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in {"PODCAST_DATA_ROOT", "PIPELINE_DATA_ROOT", "PRIVATE_DOCS_ROOT"}:
            os.environ.setdefault(key, value)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: Any, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temp, path)
        else:
            # Publish complete raw/derived files without replacing an existing version.
            os.link(temp, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def private_path(value: str | Path) -> Path:
    expanded = os.path.expandvars(str(value))
    if "$" in expanded:
        raise ValueError("Unresolved data path variable; configure PODCAST_DATA_ROOT / PIPELINE_DATA_ROOT.")
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        raise ValueError("Data paths must be absolute and outside the public repository.")
    path = path.resolve()
    if path == PROJECT_ROOT or PROJECT_ROOT in path.parents:
        raise ValueError("Production data cannot use the public repository or public samples.")
    return path


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError("Invalid episode/batch identifier; use letters, numbers, dot, underscore or hyphen.")
    return value


def child_path(root: Path, *parts: str) -> Path:
    result = root.joinpath(*parts).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Output path escapes its configured data root.")
    return private_path(result)


def load_manifest(path: Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        raise ValueError("Set manifest_path to a private episode metadata JSON or JSONL file.")
    path = private_path(path)
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        rows = read_json(path)
    if isinstance(rows, dict) and rows.get("schema_version") == "episode_catalog.v1":
        rows = rows.get("episodes")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Episode manifest must contain a list of objects.")
    return rows


def find_episode(rows: list[dict[str, Any]], episode_id: str) -> dict[str, Any]:
    query = episode_id.strip().casefold()
    if not query:
        raise ValueError("Episode query is empty.")
    number = query.removeprefix("ep")
    groups = [
        [r for r in rows if str(r.get("episode_id", "")).strip().casefold() == query],
        [r for r in rows if number.isdigit() and str(r.get("episode_number", "")).strip() == number],
        [r for r in rows if str(r.get("guid", "")).strip().casefold() == query],
    ]
    # Numeric requests must never fall through to EP57 matching EP579's title.
    if not number.isdigit():
        groups.append([r for r in rows if query in str(r.get("episode_title", "")).casefold()])
    for matches in groups:
        if len(matches) > 1:
            raise ValueError(f"Ambiguous episode query: {episode_id}")
        if matches:
            safe_id(str(matches[0]["episode_id"]))
            return matches[0]
    raise ValueError(f"Episode not found: {episode_id}")


def run_command(args: list[str], timeout: int | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def terminology_prompt() -> str:
    import csv

    with TERMINOLOGY.open(encoding="utf-8", newline="") as stream:
        rows = csv.DictReader(stream, delimiter="\t")
        return "\n".join(dict.fromkeys(
            row.get("target_text") or row["source_text"] for row in rows
            if row.get("status") in {"active", "human_reviewed"}
            and row.get("type") in {"preserve", "alias", "correction"}
        ))
