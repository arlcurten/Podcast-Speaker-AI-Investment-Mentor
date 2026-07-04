from __future__ import annotations

import hashlib
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
    """Load the repository-root .env without overriding exported values."""
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
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def now_seconds() -> float:
    return time.perf_counter()


@dataclass
class ChatResult:
    content: str
    usage: dict[str, Any]
    latency_seconds: float
    retries: int
    raw_response: dict[str, Any]


class OpenAICompatibleClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.base_url = config["base_url"].rstrip("/")
        self.api_key_env = config["api_key_env"]
        self.model = resolve_env_value(config.get("model_id")) or os.environ.get("PHASE2_MODEL_ID")
        if not self.model:
            raise RuntimeError(
                "Missing model. Set PHASE2_MODEL_ID or set a literal model ID in config/provider.yaml."
            )
        self.temperature = config.get("temperature", 0)
        self.timeout_seconds = config.get("timeout_seconds", 120)
        self.max_output_tokens = config.get("max_output_tokens", 4096)
        self.max_retries = config.get("max_retries", 1)
        self.api_key = os.environ.get(self.api_key_env)
        if not self.api_key:
            raise RuntimeError(
                f"Missing API key. Set environment variable {self.api_key_env} "
                f"or change api_key_env in config/provider.yaml."
            )

    def chat_json(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": response_format or {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(provider_headers(self.config))
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            start = now_seconds()
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
                latency = now_seconds() - start
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return ChatResult(
                    content=content,
                    usage=data.get("usage", {}),
                    latency_seconds=latency,
                    retries=attempt,
                    raw_response=data,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(2**attempt)
        raise RuntimeError(f"Chat completion failed after retries: {last_error}") from last_error

    def request_payload(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": response_format or {"type": "json_object"},
        }

    def _openrouter_headers(self) -> dict[str, str]:
        # OpenRouter attribution headers are optional. Include them only when configured.
        return provider_headers(self.config)


def provider_headers(config: dict[str, Any]) -> dict[str, str]:
    headers_cfg = config.get("headers") or {}
    headers: dict[str, str] = {}
    if headers_cfg.get("http_referer"):
        headers["HTTP-Referer"] = str(headers_cfg["http_referer"])
    if headers_cfg.get("x_title"):
        headers["X-Title"] = str(headers_cfg["x_title"])
    return headers


def parse_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model output is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Model output must be a JSON object.")
    return data


def resolve_env_value(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1])
    return value


def estimate_cost(provider_config: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    pricing = provider_config.get("pricing_per_1m_tokens") or {}
    input_price = pricing.get("input_usd")
    output_price = pricing.get("output_usd")
    prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    if input_price is None or output_price is None:
        return {
            "estimated_cost_usd": None,
            "reason": "pricing_per_1m_tokens is not configured",
        }
    return {
        "estimated_cost_usd": (prompt_tokens / 1_000_000 * input_price)
        + (completion_tokens / 1_000_000 * output_price),
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "input_price_per_1m_usd": input_price,
        "output_price_per_1m_usd": output_price,
    }
