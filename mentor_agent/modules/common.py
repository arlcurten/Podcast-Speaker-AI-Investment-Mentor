from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent


def load_project_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


load_project_dotenv()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Missing data file: {path}. Set PIPELINE_DATA_ROOT to the private repo pipeline_data path."
        ) from exc


def read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def expand_env_path(value: str | Path) -> Path:
    raw = str(value)
    if "${" in raw:
        var_name = raw.split("${", 1)[1].split("}", 1)[0]
        if var_name and not os.environ.get(var_name):
            raise RuntimeError(
                f"Missing environment variable {var_name}. Production data must come from the private repo."
            )
    return Path(os.path.expandvars(raw))


def resolve_path(value: str | Path) -> Path:
    path = expand_env_path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def fmt_ts(seconds: float | int | None) -> str:
    if not isinstance(seconds, (int, float)):
        return "??:??"
    total = int(round(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"

