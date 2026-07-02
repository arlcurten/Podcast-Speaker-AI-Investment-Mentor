from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import requests

from modules.common import ROOT, provider_headers, read_yaml, resolve_env_value


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify OpenRouter auth without sending transcript text.")
    parser.add_argument("--provider", default="config/provider.yaml")
    args = parser.parse_args()

    provider_config = read_yaml(ROOT / args.provider)
    base_url = str(provider_config.get("base_url", "")).rstrip("/")
    api_key_env = provider_config.get("api_key_env")
    model_id = resolve_env_value(provider_config.get("model_id")) or os.environ.get("PHASE2_MODEL_ID")
    api_key = os.environ.get(api_key_env or "")

    result: dict[str, Any] = {
        "provider": provider_config.get("provider"),
        "base_url": base_url,
        "api_key_env": api_key_env,
        "api_key_present": bool(api_key),
        "model_id": model_id,
        "model_id_present": bool(model_id),
        "would_send_transcript": False,
        "paid_inference_request_made": False,
        "auth_succeeded": False,
        "errors": [],
        "warnings": [],
    }

    if not base_url.startswith(("https://", "http://")):
        result["errors"].append("Invalid base_url")
    if not api_key_env:
        result["errors"].append("Missing api_key_env")
    if not api_key:
        result["errors"].append(f"Missing API key environment variable: {api_key_env}")
    if result["errors"]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    headers = {"Authorization": f"Bearer {api_key}"}
    headers.update(provider_headers(provider_config))
    try:
        response = requests.get(f"{base_url}/key", headers=headers, timeout=30)
        result["status_code"] = response.status_code
        if response.status_code == 200:
            data = response.json().get("data", {})
            result["auth_succeeded"] = True
            # Do not print key material. Label is masked by OpenRouter, but keep it out anyway.
            safe_fields = {
                k: data.get(k)
                for k in [
                    "is_free_tier",
                    "is_management_key",
                    "limit",
                    "limit_remaining",
                    "limit_reset",
                    "usage",
                    "usage_daily",
                    "usage_weekly",
                    "usage_monthly",
                ]
                if k in data
            }
            result["key_info"] = safe_fields
        else:
            result["errors"].append(f"OpenRouter auth check failed with HTTP {response.status_code}")
    except Exception as exc:
        result["errors"].append(f"OpenRouter auth check request failed: {type(exc).__name__}: {exc}")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["auth_succeeded"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
