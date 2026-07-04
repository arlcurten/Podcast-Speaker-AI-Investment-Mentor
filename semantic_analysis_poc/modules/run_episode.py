from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from modules.common import (
    ROOT,
    OpenAICompatibleClient,
    estimate_cost,
    parse_json_object,
    read_json,
    read_yaml,
    resolve_env_value,
    sha256_file,
    write_json,
)
from modules.evidence import normalize_evidence_refs
from modules.prompts import level1_prompt, level2_prompt, level3_prompt
from modules.reporting import write_review_report
from modules.schemas import LEVEL1_RESPONSE_FORMAT, LEVEL2_RESPONSE_FORMAT, LEVEL3_RESPONSE_FORMAT
from modules.validation import validate_level1, validate_level2, validate_level3


def load_terminology(path: Path, max_chars: int = 8000) -> str:
    text = path.read_text(encoding="utf-8")
    return text[:max_chars]


def input_path(config: dict[str, Any], key: str) -> Path:
    return ROOT / config["inputs"][key]


def standard_episode_config(episode_id: str, base_config: dict[str, Any]) -> dict[str, Any]:
    episode_dir = ROOT / "data" / "phase1_inputs" / episode_id
    if not episode_dir.exists():
        raise SystemExit(f"Configured episode_id {base_config.get('episode_id')} does not match --episode {episode_id}")
    config = dict(base_config)
    config["episode_id"] = episode_id
    config["inputs"] = dict(base_config["inputs"])
    config["inputs"].update(
        {
            "raw_transcript": f"data/phase1_inputs/{episode_id}/raw_large-v3-turbo_transcript.json",
            "raw_run_metadata": f"data/phase1_inputs/{episode_id}/raw_large-v3-turbo_run_metadata.json",
            "merged_transcript": f"data/phase1_inputs/{episode_id}/derived_large-v3-turbo_merged_transcript.json",
            "normalized_merged_transcript": f"data/phase1_inputs/{episode_id}/derived_large-v3-turbo_normalized_merged_transcript_zh_tw.json",
            "merge_integrity": f"data/phase1_inputs/{episode_id}/merge_integrity.json",
            "audio_metadata": f"data/phase1_inputs/{episode_id}/audio_metadata.json",
            "download_metadata": f"data/phase1_inputs/{episode_id}/download_metadata.json",
        }
    )
    return config


def run_stage(
    name: str,
    client: OpenAICompatibleClient,
    messages: list[dict[str, str]],
    output_path: Path,
    response_format: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = client.chat_json(messages, response_format=response_format)
    try:
        data = normalize_evidence_refs(parse_json_object(result.content))
    except ValueError as exc:
        failure_path = output_path.with_name(f"{output_path.stem}_failure.json")
        write_json(
            failure_path,
            {
                "stage": name,
                "error": str(exc),
                "usage": result.usage,
                "latency_seconds": result.latency_seconds,
                "retries": result.retries,
                "raw_content": result.content,
            },
        )
        raise
    write_json(output_path, data)
    return data, {
        "stage": name,
        "output_path": str(output_path.relative_to(ROOT)),
        "usage": result.usage,
        "latency_seconds": result.latency_seconds,
        "retries": result.retries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 2 semantic extraction on one episode.")
    parser.add_argument("--episode", default="EP674")
    parser.add_argument("--inputs", default="config/phase2_inputs.yaml")
    parser.add_argument("--provider", default="config/provider.yaml")
    parser.add_argument("--model-id", help="Override config/provider.yaml and PHASE2_MODEL_ID for this run.")
    parser.add_argument("--output-root", default="data/outputs", help="Output root relative to semantic_analysis_poc.")
    parser.add_argument("--output-dir", help="Exact output directory relative to semantic_analysis_poc.")
    parser.add_argument("--report-dir", default="reports", help="Report directory relative to semantic_analysis_poc.")
    parser.add_argument("--dry-run", action="store_true", help="Run preflight and assemble Level 1 request without calling the API.")
    parser.add_argument("--preflight", action="store_true", help="Alias for --dry-run.")
    args = parser.parse_args()

    config = read_yaml(ROOT / args.inputs)
    provider_config = read_yaml(ROOT / args.provider)
    provider_config["model_id"] = args.model_id or resolve_env_value(provider_config.get("model_id")) or os.environ.get("PHASE2_MODEL_ID")
    episode_id = args.episode
    if config.get("episode_id") != episode_id:
        config = standard_episode_config(episode_id, config)

    normalized_path = input_path(config, "normalized_merged_transcript")
    raw_path = input_path(config, "raw_transcript")
    manifest_path = input_path(config, "merge_integrity")
    terminology_path = ROOT / config["inputs"]["terminology"]
    preflight_errors: list[str] = []
    preflight_warnings: list[str] = []

    base_url = provider_config.get("base_url")
    if not isinstance(base_url, str) or not base_url.startswith(("https://", "http://")):
        preflight_errors.append("Invalid or missing base_url in provider config.")
    if not provider_config.get("api_key_env"):
        preflight_errors.append("Missing api_key_env in provider config.")
    elif not os.environ.get(provider_config["api_key_env"]):
        preflight_errors.append(f"Missing API key environment variable: {provider_config['api_key_env']}")
    if not provider_config.get("model_id"):
        preflight_errors.append("Missing model ID. Set PHASE2_MODEL_ID or config/provider.yaml model_id.")

    for label, path in [
        ("normalized_merged_transcript", normalized_path),
        ("raw_transcript", raw_path),
        ("merge_integrity", manifest_path),
        ("terminology", terminology_path),
        ("semantic_design", ROOT / config["design_contract"]),
    ]:
        if not path.exists():
            preflight_errors.append(f"Missing required {label}: {path}")

    out_dir = ROOT / args.output_dir if args.output_dir else ROOT / args.output_root / episode_id
    report_dir = ROOT / args.report_dir
    for label, path in [("output parent", out_dir.parent), ("report parent", report_dir.parent)]:
        existing = path if path.exists() else path.parent
        if not existing.exists():
            preflight_errors.append(f"Missing {label}: {existing}")
        elif not os.access(existing, os.W_OK):
            preflight_errors.append(f"{label} is not writable: {existing}")

    if preflight_errors and not (args.dry_run or args.preflight):
        raise SystemExit("Preflight failed before API call:\n- " + "\n- ".join(preflight_errors))
    if (args.dry_run or args.preflight) and any(e.startswith("Missing required") for e in preflight_errors):
        print(json.dumps({
            "mode": "dry_run",
            "would_call_api": False,
            "provider": provider_config.get("provider"),
            "base_url": provider_config.get("base_url"),
            "api_key_env": provider_config.get("api_key_env"),
            "api_key_present": bool(os.environ.get(provider_config.get("api_key_env", ""))),
            "model_id": provider_config.get("model_id"),
            "model_id_present": bool(provider_config.get("model_id")),
            "episode_id": episode_id,
            "errors": preflight_errors,
            "warnings": preflight_warnings,
        }, ensure_ascii=False, indent=2))
        return 1

    phase1_hash_before = sha256_file(raw_path) if raw_path.exists() else None

    normalized = read_json(normalized_path)
    raw = read_json(raw_path)
    _merge_integrity = read_json(manifest_path)
    segments = normalized["segments"]
    episode_duration_seconds = raw.get("metadata", {}).get("audio_duration_seconds")
    valid_merged_ids = {int(s.get("merged_id", s.get("id"))) for s in segments}
    valid_source_ids: set[int] = set()
    for s in raw.get("segments", []):
        if "id" in s:
            valid_source_ids.add(int(s["id"]))
        elif "segment_id" in s:
            valid_source_ids.add(int(s["segment_id"]))
    if not valid_source_ids:
        for s in segments:
            valid_source_ids.update(int(x) for x in s.get("source_segment_ids", []))

    terminology = load_terminology(terminology_path)
    level1_messages = level1_prompt(episode_id, segments, terminology)
    approx_level1_chars = sum(len(m["content"]) for m in level1_messages)

    if args.dry_run or args.preflight:
        request_payload_summary = {
            "model_present": bool(provider_config.get("model_id")),
            "message_count": len(level1_messages),
            "has_system_prompt": any(m.get("role") == "system" for m in level1_messages),
            "has_user_prompt": any(m.get("role") == "user" for m in level1_messages),
            "temperature": provider_config.get("temperature"),
            "max_tokens": provider_config.get("max_output_tokens"),
            "response_format": {"type": "json_schema"},
            "optional_header_names": [
                name
                for name, value in {
                    "HTTP-Referer": (provider_config.get("headers") or {}).get("http_referer"),
                    "X-Title": (provider_config.get("headers") or {}).get("x_title"),
                }.items()
                if value
            ],
        }
        print(json.dumps({
            "mode": "dry_run",
            "would_call_api": False,
            "provider": provider_config.get("provider"),
            "base_url": provider_config.get("base_url"),
            "api_key_env": provider_config.get("api_key_env"),
            "api_key_present": bool(os.environ.get(provider_config.get("api_key_env", ""))),
            "model_id": provider_config.get("model_id"),
            "model_id_present": bool(provider_config.get("model_id")),
            "episode_id": episode_id,
            "normalized_segment_count": len(segments),
            "episode_duration_seconds": episode_duration_seconds,
            "level1_prompt_approx_chars": approx_level1_chars,
            "request_payload_summary": request_payload_summary,
            "output_path": str(out_dir.relative_to(ROOT)),
            "report_path": str(report_dir.relative_to(ROOT)),
            "output_parent_writable": os.access(out_dir.parent if out_dir.parent.exists() else out_dir.parent.parent, os.W_OK),
            "report_parent_writable": os.access(report_dir.parent, os.W_OK),
            "errors": preflight_errors,
            "warnings": preflight_warnings,
        }, ensure_ascii=False, indent=2))
        return 1 if preflight_errors else 0

    client = OpenAICompatibleClient(provider_config)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, Any] = {
        "episode_id": episode_id,
        "provider": provider_config.get("provider"),
        "base_url": provider_config.get("base_url"),
        "model": provider_config.get("model_id"),
        "temperature": provider_config.get("temperature"),
        "max_output_tokens": provider_config.get("max_output_tokens"),
        "phase1_raw_transcript_sha256_before": phase1_hash_before,
        "stages": [],
    }

    level1, stage = run_stage(
        "level1",
        client,
        level1_messages,
        out_dir / "level1_episode_semantic_map.json",
        LEVEL1_RESPONSE_FORMAT,
    )
    v1 = validate_level1(level1, episode_id, valid_merged_ids, valid_source_ids, episode_duration_seconds)
    write_json(out_dir / "level1_validation.json", v1)
    if not v1["ok"]:
        raise SystemExit(f"Level 1 validation failed: {json.dumps(v1, ensure_ascii=False)}")
    stage["validation"] = v1
    metadata["stages"].append(stage)

    level2, stage = run_stage(
        "level2",
        client,
        level2_prompt(episode_id, segments, level1, terminology),
        out_dir / "level2_reasoning_records.json",
        LEVEL2_RESPONSE_FORMAT,
    )
    level1_thread_ids = {
        t.get("topic_thread_id")
        for t in level1.get("main_topic_threads", [])
        if t.get("topic_thread_id")
    }
    v2 = validate_level2(level2, episode_id, valid_merged_ids, valid_source_ids, level1_thread_ids, episode_duration_seconds)
    write_json(out_dir / "level2_validation.json", v2)
    if not v2["ok"]:
        raise SystemExit(f"Level 2 validation failed: {json.dumps(v2, ensure_ascii=False)}")
    stage["validation"] = v2
    metadata["stages"].append(stage)

    level2_ids = {
        r.get("reasoning_record_id")
        for r in level2.get("reasoning_records", [])
        if r.get("reasoning_record_id")
    }
    level3, stage = run_stage(
        "level3",
        client,
        level3_prompt(episode_id, level1, level2),
        out_dir / "level3_optional_consolidation.json",
        LEVEL3_RESPONSE_FORMAT,
    )
    v3 = validate_level3(level3, episode_id, level1["recommended_level3_strategy"], level2_ids)
    write_json(out_dir / "level3_validation.json", v3)
    if not v3["ok"]:
        raise SystemExit(f"Level 3 validation failed: {json.dumps(v3, ensure_ascii=False)}")
    stage["validation"] = v3
    metadata["stages"].append(stage)

    phase1_hash_after = sha256_file(raw_path)
    metadata["phase1_raw_transcript_sha256_after"] = phase1_hash_after
    metadata["phase1_raw_transcript_unchanged"] = phase1_hash_before == phase1_hash_after
    metadata["total_latency_seconds"] = sum(s["latency_seconds"] for s in metadata["stages"])
    metadata["total_retries"] = sum(s["retries"] for s in metadata["stages"])
    metadata["usage"] = {
        "prompt_tokens": sum((s.get("usage") or {}).get("prompt_tokens", 0) for s in metadata["stages"]),
        "completion_tokens": sum((s.get("usage") or {}).get("completion_tokens", 0) for s in metadata["stages"]),
        "total_tokens": sum((s.get("usage") or {}).get("total_tokens", 0) for s in metadata["stages"]),
    }
    metadata.update(estimate_cost(provider_config, metadata["usage"]))

    write_json(out_dir / "run_metadata.json", metadata)
    write_review_report(report_dir / f"{episode_id}_phase2_human_review.md", episode_id, level1, level2, level3, metadata)
    print(json.dumps({
        "episode_id": episode_id,
        "level1": str((out_dir / "level1_episode_semantic_map.json").relative_to(ROOT)),
        "level2": str((out_dir / "level2_reasoning_records.json").relative_to(ROOT)),
        "level3": str((out_dir / "level3_optional_consolidation.json").relative_to(ROOT)),
        "review": str((report_dir / f"{episode_id}_phase2_human_review.md").relative_to(ROOT)),
        "metadata": str((out_dir / "run_metadata.json").relative_to(ROOT)),
        "strategy": level3.get("selected_strategy"),
        "phase1_unchanged": metadata["phase1_raw_transcript_unchanged"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
