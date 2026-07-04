from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
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
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def fmt_ts(seconds: float | int | None) -> str:
    if not isinstance(seconds, (int, float)):
        return "??:??"
    total = int(round(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


@dataclass
class ChatResult:
    content: str
    usage: dict[str, Any]
    latency_seconds: float
    retries: int


class OpenAICompatibleClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.base_url = str(config["base_url"]).rstrip("/")
        self.api_key_env = str(config["api_key_env"])
        self.model = os.environ.get("MENTOR_MODEL_ID") or config.get("model_id")
        if not self.model:
            raise RuntimeError("Missing model. Set MENTOR_MODEL_ID or config/provider.yaml model_id.")
        self.api_key = os.environ.get(self.api_key_env)
        if not self.api_key:
            raise RuntimeError(f"Missing API key environment variable: {self.api_key_env}")
        self.temperature = config.get("temperature", 0.2)
        self.timeout_seconds = config.get("timeout_seconds", 120)
        self.max_output_tokens = config.get("max_output_tokens", 1200)
        self.max_retries = config.get("max_retries", 1)

    def chat(self, messages: list[dict[str, str]]) -> ChatResult:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(provider_headers(self.config))
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            start = time.perf_counter()
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                latency = time.perf_counter() - start
                resp.raise_for_status()
                data = resp.json()
                return ChatResult(
                    content=data["choices"][0]["message"]["content"],
                    usage=data.get("usage", {}),
                    latency_seconds=latency,
                    retries=attempt,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(2**attempt)
        raise RuntimeError(f"Chat completion failed after retries: {last_error}") from last_error


def provider_headers(config: dict[str, Any]) -> dict[str, str]:
    headers_cfg = config.get("headers") or {}
    headers: dict[str, str] = {}
    if headers_cfg.get("http_referer"):
        headers["HTTP-Referer"] = str(headers_cfg["http_referer"])
    if headers_cfg.get("x_title"):
        headers["X-Title"] = str(headers_cfg["x_title"])
    return headers
