from __future__ import annotations

import json
import os
import time

import requests
from jsonschema import Draft202012Validator


class ModelError(RuntimeError):
    def __init__(self, message, metadata=None):
        super().__init__(message)
        self.metadata = metadata or {}


class OpenRouter:
    def __init__(self, settings: dict):
        self.settings = settings
        self.key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not self.key:
            raise ValueError("Set OPENROUTER_API_KEY locally; never put it in a query/config.")

    def complete(self, prompt: str, context: dict, schema: dict) -> tuple[dict, dict]:
        serialized = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        if len(serialized) > self.settings["max_context_chars"]:
            raise ModelError("Evidence context exceeds limit; do not truncate conditions silently.")
        start = time.perf_counter()
        metadata = {"model": self.settings["model"], "input_chars": len(serialized), "cost_usd": None}
        try:
            response = requests.post("https://openrouter.ai/api/v1/chat/completions", timeout=(10, self.settings["timeout_seconds"]),
                headers={"Authorization": "Bearer " + self.key},
                json={"model": self.settings["model"], "messages": [{"role": "system", "content": prompt},
                       {"role": "user", "content": serialized}], "temperature": self.settings["temperature"],
                      "max_tokens": self.settings["max_output_tokens"],
                      "provider": {"require_parameters": True},
                      "response_format": {"type": "json_schema", "json_schema": {"name": "mentor_result", "strict": True, "schema": schema}}})
            metadata["latency_seconds"] = time.perf_counter() - start
            metadata["http_status"] = response.status_code
            response.raise_for_status()
            body = response.json()
            metadata["usage"] = body.get("usage")
            metadata["cost_usd"] = (body.get("usage") or {}).get("cost")
            metadata["response_id"] = body.get("id")
            choice = body["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("Incomplete model response")
            result = json.loads(choice["message"]["content"])
            Draft202012Validator(schema).validate(result)
            return result, metadata
        except Exception as exc:
            metadata.setdefault("latency_seconds", time.perf_counter() - start)
            # Never echo HTTP bodies, authorization headers or source context.
            raise ModelError(f"Structured Mentor call failed ({type(exc).__name__}).", metadata) from None
