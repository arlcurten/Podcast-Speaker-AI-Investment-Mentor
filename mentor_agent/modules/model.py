from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class ChatResult:
    content: str
    usage: dict[str, Any]
    latency_seconds: float
    retries: int
    model: str


class OpenAICompatibleClient:
    def __init__(self, config: dict[str, Any], model_env: str = "MENTOR_MODEL_ID"):
        self.config = config
        self.base_url = str(config["base_url"]).rstrip("/")
        self.api_key_env = str(config.get("api_key_env", "OPENROUTER_API_KEY"))
        self.model = os.environ.get(model_env) or config.get("model_id")
        if not self.model:
            raise RuntimeError(f"Missing model. Set {model_env} or model.model_id.")
        self.api_key = os.environ.get(self.api_key_env)
        if not self.api_key:
            raise RuntimeError(f"Missing API key environment variable: {self.api_key_env}")
        self.temperature = config.get("temperature", 0.2)
        self.timeout_seconds = config.get("timeout_seconds", 120)
        self.max_output_tokens = config.get("max_output_tokens", 1800)
        self.max_retries = int(config.get("max_retries", 1))

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
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
                    model=self.model,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(2**attempt)
        raise RuntimeError(f"Chat completion failed after retries: {last_error}") from last_error

    def json_chat(self, messages: list[dict[str, str]]) -> tuple[dict[str, Any], ChatResult]:
        result = self.chat(messages, json_mode=True)
        try:
            return json.loads(result.content), result
        except json.JSONDecodeError as exc:
            raise ValueError(f"Model did not return valid JSON: {exc}") from exc


def provider_headers(config: dict[str, Any]) -> dict[str, str]:
    headers_cfg = config.get("headers") or {}
    headers: dict[str, str] = {}
    if headers_cfg.get("http_referer"):
        headers["HTTP-Referer"] = str(headers_cfg["http_referer"])
    if headers_cfg.get("x_title"):
        headers["X-Title"] = str(headers_cfg["x_title"])
    return headers

