"""One resumable offline pipeline; immutable stage artifacts with verified inputs."""
from __future__ import annotations

import fcntl
import os
import re
from pathlib import Path

import yaml

from transcription.common import atomic_write_json, private_path, child_path, read_json, sha256_file, safe_id, utc_now
from .schemas import VERSION, schema, validate_proposal
from .provenance import load_source, digest, evidence, case_catalog, qualify

PROMPTS = Path(__file__).parent / "prompts"
STAGES = ("level1", "level2", "level3", "level4")


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or cfg.get("schema_version") != "knowledge_config.v1":
        raise ValueError("Expected knowledge_config.v1.")
    safe_id(cfg["run_id"])
    cfg["output_root"] = str(private_path(cfg["output_root"]))
    cfg["model"] = os.environ.get("KNOWLEDGE_MODEL_ID") or cfg.get("model")
    if not cfg["model"]:
        raise ValueError("Set KNOWLEDGE_MODEL_ID or config.model.")
    if not isinstance(cfg.get("episodes"), list) or not cfg["episodes"]:
        raise ValueError("Configure explicit episode sources; no implicit corpus discovery.")
    ids = []
    for entry in cfg["episodes"]:
        ids.append(safe_id(entry["episode_id"]))
        for key in ("raw", "normalized", "review"):
            entry[key] = str(private_path(entry[key]))
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate configured episode.")
    cfg.setdefault("reasoning_effort", "medium")
    cfg.setdefault("timeout_seconds", 1800)
    cfg.setdefault("max_input_chars", 180000)
    cfg.setdefault("max_calls", 13)
    cfg.setdefault("retry_count", 0)
    if cfg["reasoning_effort"] not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("Unsupported reasoning effort.")
    for key in ("timeout_seconds", "max_input_chars", "max_calls"):
        if type(cfg[key]) is not int or cfg[key] <= 0:
            raise ValueError(f"{key} must be a positive integer.")
    if type(cfg["retry_count"]) is not int or not 0 <= cfg["retry_count"] <= 1:
        raise ValueError("retry_count must be 0 or 1; do not repeatedly pay to repair invalid abstractions.")
    return cfg


def sources(cfg: dict) -> dict:
    result = {}
    for entry in cfg["episodes"]:
        source = load_source(entry["raw"], entry["normalized"], entry["review"])
        if source["episode_id"] != entry["episode_id"]:
            raise ValueError("Configured/source episode identity mismatch.")
        result[source["episode_id"]] = source
    return result


def _source_meta(source: dict) -> dict:
    return {k: source[k] for k in ("episode_id", "raw_sha256", "normalized_sha256", "review_sha256", "publication_date")}


def derive(stage: str, content: dict, source: dict | None, upstream: list[dict]) -> tuple[dict, dict, list[str]]:
    validate_proposal(stage, content)
    warnings, resolved, qualifications = [], {}, {}
    if source and content.get("episode_id") != source["episode_id"]:
        raise ValueError("Model changed episode identity.")
    if stage in {"level1", "level2"}:
        rows = content["threads" if stage == "level1" else "cases"]
        key, letter = ("thread_id", "T") if stage == "level1" else ("record_id", "R")
        ids = [r[key] for r in rows]
        if len(ids) != len(set(ids)) or any(not re.fullmatch(re.escape(source["episode_id"]) + f":{letter}[0-9]+", i) for i in ids):
            raise ValueError("Duplicate or non-episode-qualified record/thread IDs.")
        thread_ids = set(ids) if stage == "level1" else {r["thread_id"] for r in upstream[0]["content"]["threads"]}
        for row in rows:
            refs = [r["thread_id"] for r in row["related_threads"]] if stage == "level1" else row["thread_ids"]
            if any(r not in thread_ids for r in refs):
                raise ValueError("Unresolved Level 1 thread reference.")
            resolved[row[key]] = evidence(row["merged_ids"], source)
        if not rows:
            warnings.append("No reasoning cases were extracted; this episode cannot support Mentor claims.")
    else:
        cases = case_catalog([a for a in upstream if a["stage"] == "level2"])
        eligible = {p["principle_id"]: p for a in upstream if a["stage"] == "level3"
                    for p in a["content"]["principles"] if a["qualification"][p["principle_id"]]["eligible_for_level4"]}
        ids = [p["principle_id"] for p in content["principles"]]
        prefix = source["episode_id"] if source else "CROSS"
        if len(ids) != len(set(ids)) or any(not re.fullmatch(re.escape(prefix) + ":P[0-9]+", i) for i in ids):
            raise ValueError("Duplicate or incorrectly scoped principle IDs.")
        if stage == "level3" and ((content["strategy"] == "bypass") != (not content["principles"])):
            raise ValueError("Bypass must be used exactly when no provisional principles are returned.")
        names = set()
        for p in content["principles"]:
            qualified = qualify(p, cases, stage, eligible)
            qualifications[p["principle_id"]] = {k: v for k, v in qualified.items() if k not in p or v != p[k]}
            name = re.sub(r"\W", "", p["core_preference"]).casefold()
            if name in names:
                warnings.append(f"Duplicate preference requires review: {p['principle_id']}")
                qualifications[p["principle_id"]].update(eligible_for_level4=False, eligible_for_online_reasoning=False,
                                                          runtime_mode_recommendation="not_eligible")
            names.add(name)
            if qualified["genericness_status"] != "specific":
                warnings.append(f"Abstraction quality needs review: {p['principle_id']}")
    return resolved, qualifications, warnings


def context(stage: str, source: dict | None, upstream: list[dict]) -> dict:
    if stage in {"level1", "level2"}:
        result = {"episode": _source_meta(source), "transcript": [
            {k: s[k] for k in ("merged_id", "start", "end", "normalized_text_zh_tw", "terminology_annotations")}
            for s in source["segments"]]}
        if stage == "level2":
            result["episode_map"] = upstream[0]["content"]
        return result
    result = {"cases": list(case_catalog([a for a in upstream if a["stage"] == "level2"]).values())}
    if stage == "level3":
        result["episode"] = _source_meta(source)
    else:
        result["eligible_provisional_principles"] = [
            {**p, **a["qualification"][p["principle_id"]]} for a in upstream if a["stage"] == "level3"
            for p in a["content"]["principles"] if a["qualification"][p["principle_id"]]["eligible_for_level4"]]
    return result


def run(cfg: dict, *, through: str = "level4", dry_run: bool = False, validate_only: bool = False, backend=None) -> dict:
    from .codex_backend import CodexBackend

    source_map = sources(cfg)  # Fail before any paid call if quality or provenance is invalid.
    root = child_path(private_path(cfg["output_root"]), cfg["run_id"])
    if dry_run:
        return {"dry_run": True, "episode_ids": list(source_map), "through": through,
                "maximum_initial_calls": len(source_map) * min(STAGES.index(through) + 1, 3) + int(through == "level4"),
                "output": str(root), "sources_accepted": True}
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        model = backend or CodexBackend(cfg["model"], root / "tmp", timeout_seconds=cfg["timeout_seconds"],
                                       reasoning_effort=cfg["reasoning_effort"], max_input_chars=cfg["max_input_chars"])
        outputs, records, calls = {}, [], []

        def stage_run(stage, source, upstream):
            identifier = source["episode_id"] if source else "cross_episode"
            path = child_path(root, identifier, stage + ".json")
            prompt = (PROMPTS / (stage + ".md")).read_text(encoding="utf-8")
            source_meta = _source_meta(source) if source else {"episodes": [_source_meta(s) for s in source_map.values()]}
            fingerprint = digest({"source": source_meta, "upstream": [digest(a) for a in upstream],
                                  "schema": schema(stage), "prompt": prompt, "model": cfg["model"],
                                  "reasoning_effort": cfg["reasoning_effort"], "schema_version": VERSION})
            if path.exists():
                artifact = read_json(path)
                if artifact.get("schema_version") != VERSION or artifact.get("fingerprint") != fingerprint or artifact.get("stage") != stage:
                    raise ValueError("Existing knowledge version is incompatible; use a new run_id.")
                ev, qual, warn = derive(stage, artifact["content"], source, upstream)
                if (artifact.get("evidence"), artifact.get("qualification"), artifact.get("warnings")) != (ev, qual, warn):
                    raise ValueError("Stored evidence/qualification differs from deterministic reconstruction.")
                action = "reused"
            else:
                if validate_only:
                    raise ValueError("Missing knowledge artifact; validation does not run a model.")
                for attempt in range(cfg["retry_count"] + 1):
                    if len(calls) >= cfg["max_calls"]:
                        raise ValueError("Configured model-call limit reached; completed stages remain resumable.")
                    call_id = f"{identifier}_{stage}_{len(list((root / 'calls').glob('*.json'))) + 1}"
                    request_context = context(stage, source, upstream)
                    usage, response = None, None
                    try:
                        response, usage = model.complete(prompt, request_context, schema(stage))
                        ev, qual, warn = derive(stage, response, source, upstream)
                    except (ValueError, RuntimeError) as exc:
                        usage = getattr(exc, "metadata", usage)
                        report = {"stage": stage, "episode_id": identifier, "status": "failed", "attempt": attempt + 1,
                                  "error_type": type(exc).__name__, "response": response, "usage": usage, "fingerprint": fingerprint}
                        calls.append(report)
                        atomic_write_json(root / "calls" / (call_id + ".json"), report, overwrite=False)
                        if attempt == cfg["retry_count"]:
                            raise ValueError(f"{stage} extraction/validation failed; inspect private call record, never publish invalid output.") from exc
                    else:
                        report = {"stage": stage, "episode_id": identifier, "status": "completed", "attempt": attempt + 1,
                                  "usage": usage, "fingerprint": fingerprint}
                        calls.append(report)
                        atomic_write_json(root / "calls" / (call_id + ".json"), report, overwrite=False)
                        break
                # Re-read source versions before publishing any derivative.
                if sources(cfg) != source_map:
                    raise ValueError("Source changed during extraction; result not published.")
                artifact = {"schema_version": VERSION, "stage": stage, "created_at": utc_now(),
                            "fingerprint": fingerprint, "source": source_meta, "content": response,
                            "evidence": ev, "qualification": qual, "warnings": warn, "model_metadata": usage,
                            "semantic_review_status": "pending", "evidence_authority": "level2"}
                atomic_write_json(path, artifact, overwrite=False)
                action = "created"
            records.append({"path": str(path.relative_to(root)), "stage": stage, "action": action,
                            "sha256": sha256_file(path), "warnings": artifact["warnings"]})
            return artifact

        for eid, source in source_map.items():
            l1 = stage_run("level1", source, [])
            if through == "level1":
                continue
            l2 = stage_run("level2", source, [l1])
            outputs[eid] = [l2]
            if through == "level2":
                continue
            outputs[eid].append(stage_run("level3", source, [l2]))
        if through == "level4":
            stage_run("level4", None, [a for row in outputs.values() for a in row])
        report = {"schema_version": "knowledge_run.v1", "run_id": cfg["run_id"], "artifacts": records,
                  "model_calls_this_execution": len(calls), "ready_for_serving": False,
                  "note": "Structural/evidence validation is not semantic certification. Publish only after source-grounded review."}
        if not validate_only:
            atomic_write_json(root / "run_report.json", report)
        return report
