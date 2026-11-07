"""One resumable offline pipeline; immutable stage artifacts with verified inputs."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path

import yaml

from transcription.common import atomic_write_json, private_path, child_path, sha256_file, safe_id, utc_now
from .schemas import VERSION, schema, validate_proposal
from .provenance import load_source, digest, evidence, case_catalog, qualify
from .coverage import coverage

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
        reuse = entry.get("reuse_artifacts", {})
        if not isinstance(reuse, dict) or set(reuse) != set(STAGES[:len(reuse)]):
            raise ValueError("reuse_artifacts must be a contiguous Level 1-3 prefix.")
        if "level4" in reuse:
            raise ValueError("Level 4 must be reconsolidated for the new episode selection.")
        for reference in reuse.values():
            if (not isinstance(reference, dict) or set(reference) != {"path", "sha256"}
                    or not re.fullmatch(r"[a-f0-9]{64}", str(reference.get("sha256", "")))):
                raise ValueError("Reused artifacts require a private path and SHA-256.")
            reference["path"] = str(private_path(reference["path"]))
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


def stage_location(cfg: dict, stage: str, episode_id: str = "cross_episode") -> tuple[Path, str | None]:
    root = child_path(private_path(cfg["output_root"]), safe_id(cfg["run_id"]))
    entry = next((e for e in cfg["episodes"] if e["episode_id"] == episode_id), {})
    reference = entry.get("reuse_artifacts", {}).get(stage)
    if reference:
        path = private_path(reference["path"])
        if path.is_relative_to(root):
            raise ValueError("Reused artifacts must belong to an earlier run, not the new output directory.")
        return path, reference["sha256"]
    return child_path(root, episode_id, stage + ".json"), None


def release_locations(cfg: dict) -> list[dict]:
    return [{"logical_path": f"{eid}/{stage}.json", "stage": stage,
             "path": str(stage_location(cfg, stage, eid)[0])}
            for eid, stage in [(e["episode_id"], "level2") for e in cfg["episodes"]] + [("cross_episode", "level4")]]


def stage_inputs(cfg, source_map, stage, source, upstream, prompt_override=None):
    prompt = prompt_override if prompt_override is not None else (PROMPTS / (stage + ".md")).read_text(encoding="utf-8")
    meta = _source_meta(source) if source else {"episodes": [_source_meta(s) for s in source_map.values()]}
    fingerprint = digest({"source": meta, "upstream": [digest(a) for a in upstream],
                          "schema": schema(stage), "prompt": prompt, "model": cfg["model"],
                          "reasoning_effort": cfg["reasoning_effort"], "schema_version": VERSION})
    return prompt, meta, fingerprint


def checked_artifact(cfg, source_map, stage, source, upstream, path, expected_sha=None, *, allow_pinned_prompts=False):
    payload = path.read_bytes()
    file_hash = hashlib.sha256(payload).hexdigest()
    if expected_sha is not None and file_hash != expected_sha:
        raise ValueError("Reused artifact checksum changed; no automatic regeneration or silent replacement.")
    artifact = json.loads(payload)
    if not isinstance(artifact, dict):
        raise ValueError("Knowledge artifact must be a JSON object.")
    if artifact.get("schema_version") != VERSION:
        raise ValueError("Incompatible knowledge schema; preserve old outputs and regenerate with a new run_id without reuse_artifacts.")
    pinned_prompt = artifact.get("generation_prompt")
    if pinned_prompt is not None and (not isinstance(pinned_prompt, str) or not pinned_prompt.strip()):
        raise ValueError("Malformed pinned generation prompt.")
    if (not allow_pinned_prompts and pinned_prompt is not None
            and pinned_prompt != (PROMPTS / (stage + ".md")).read_text(encoding="utf-8")):
        raise ValueError("Existing knowledge prompt is incompatible; use a new run_id.")
    _, meta, fingerprint = stage_inputs(cfg, source_map, stage, source, upstream,
                                       pinned_prompt if allow_pinned_prompts else None)
    if (artifact.get("schema_version") != VERSION or artifact.get("fingerprint") != fingerprint
            or artifact.get("stage") != stage or artifact.get("source") != meta
            or artifact.get("evidence_authority") != "level2"):
        raise ValueError("Existing knowledge version is incompatible; use a new run_id and review reuse settings.")
    ev, qual, warn = derive(stage, artifact["content"], source, upstream)
    if (artifact.get("evidence"), artifact.get("qualification"), artifact.get("warnings")) != (ev, qual, warn):
        raise ValueError("Stored evidence/qualification differs from deterministic reconstruction.")
    return artifact, file_hash


def plan_stages(cfg: dict, source_map: dict, through: str, *, allow_pinned_prompts=False) -> list[dict]:
    """Validate every existing/reference stage before admitting any model call."""
    records, outputs = [], []

    def visit(stage, source, upstream):
        eid = source["episode_id"] if source else "cross_episode"
        path, expected = stage_location(cfg, stage, eid)
        artifact, file_hash = None, None
        if expected and not path.is_file():
            raise ValueError("Missing pinned artifact; restore its source instead of silently paying to recreate it.")
        if path.exists():
            if any(a is None for a in upstream):
                raise ValueError("Existing artifact has missing upstream stages; restore the complete version.")
            artifact, file_hash = checked_artifact(cfg, source_map, stage, source, upstream, path, expected,
                                                  allow_pinned_prompts=allow_pinned_prompts)
        records.append({"path": f"{eid}/{stage}.json", "artifact_path": str(path), "stage": stage,
                        "action": "referenced" if expected else ("reused" if artifact else "planned"),
                        "sha256": file_hash, "warnings": artifact["warnings"] if artifact else []})
        return artifact

    for source in source_map.values():
        l1 = visit("level1", source, [])
        if through == "level1":
            continue
        l2 = visit("level2", source, [l1])
        outputs.append(l2)
        if through != "level2":
            outputs.append(visit("level3", source, [l2]))
    if through == "level4":
        visit("level4", None, outputs)
    return records


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
        qualifications["coverage"] = coverage(stage, content, source, upstream[0]["content"] if upstream else None)
        if stage == "level1" and content["excluded_regions"]:
            warnings.append("Excluded source regions require semantic review; full accounting is not proof of completeness.")
        if stage == "level2" and any(d["unextracted_regions"] for d in content["thread_dispositions"]):
            warnings.append("Unextracted thread regions require source-grounded coverage review.")
        if not rows:
            warnings.append("No reasoning cases were extracted; this episode cannot support Mentor claims.")
    else:
        cases = case_catalog([a for a in upstream if a["stage"] == "level2"])
        provisional = {p["principle_id"]: {**p, **a["qualification"][p["principle_id"]]}
                       for a in upstream if a["stage"] == "level3" for p in a["content"]["principles"]}
        eligible = {pid: p for pid, p in provisional.items() if p["eligible_for_level4"]}
        ids = [p["principle_id"] for p in content["principles"]]
        prefix = source["episode_id"] if source else "CROSS"
        if len(ids) != len(set(ids)) or any(not re.fullmatch(re.escape(prefix) + ":P[0-9]+", i) for i in ids):
            raise ValueError("Duplicate or incorrectly scoped principle IDs.")
        if stage == "level3" and ((content["strategy"] == "bypass") != (not content["principles"])):
            raise ValueError("Bypass must be used exactly when no provisional principles are returned.")
        names = set()
        for p in content["principles"]:
            qualified = qualify(p, cases, stage, eligible, provisional)
            qualifications[p["principle_id"]] = {k: v for k, v in qualified.items() if k not in p or v != p[k]}
            name = re.sub(r"\W", "", p["core_preference"]).casefold()
            if name in names:
                warnings.append(f"Duplicate preference requires review: {p['principle_id']}")
                qualifications[p["principle_id"]].update(eligible_for_level4=False, eligible_for_online_reasoning=False,
                                                          runtime_mode_recommendation="not_eligible")
            names.add(name)
            if qualified["genericness_status"] != "specific":
                warnings.append(f"Abstraction quality needs review: {p['principle_id']}")
            if qualified["provenance"]["unknown_context_pairs"] or qualified["provenance"]["overridden_independence_pairs"]:
                warnings.append(f"Source independence is uncertain or constrained: {p['principle_id']}")
            accounting = qualified["counterevidence_accounting"]
            if accounting.get("context_excluded") or accounting.get("unresolved"):
                warnings.append(f"Counterevidence disposition requires semantic review: {p['principle_id']}")
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
        result["upstream_counterevidence"] = [
            {"source_principle_id": p["principle_id"], "supporting_records": p["supporting_records"],
             "decision_context": p["decision_context"], "conditions": p["conditions"],
             "counterexamples": p["counterexamples"], "contradictions": p["contradictions"]}
            for a in upstream if a["stage"] == "level3" for p in a["content"]["principles"]
            if p["counterexamples"] or p["contradictions"]]
    return result


def run(cfg: dict, *, through: str = "level4", dry_run: bool = False, validate_only: bool = False,
        backend=None, allow_pinned_prompts: bool = False) -> dict:
    from .codex_backend import CodexBackend

    if allow_pinned_prompts and not validate_only:
        raise ValueError("Pinned prompts are for reviewed release validation, never new extraction/reuse planning.")
    source_map = sources(cfg)  # Fail before any paid call if quality or provenance is invalid.
    root = child_path(private_path(cfg["output_root"]), cfg["run_id"])
    planned = plan_stages(cfg, source_map, through, allow_pinned_prompts=allow_pinned_prompts)
    if dry_run:
        return {"dry_run": True, "episode_ids": list(source_map), "through": through,
                "maximum_initial_calls": len(source_map) * min(STAGES.index(through) + 1, 3) + int(through == "level4"),
                "estimated_new_calls": sum(r["action"] == "planned" for r in planned),
                "artifacts": planned, "output": str(root), "sources_accepted": True}
    if validate_only:
        if any(r["action"] == "planned" for r in planned):
            raise ValueError("Missing knowledge artifact; validation does not run a model.")
        return {"schema_version": "knowledge_run.v1", "run_id": cfg["run_id"], "artifacts": planned,
                "model_calls_this_execution": 0, "ready_for_serving": False}
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        model = backend or CodexBackend(cfg["model"], root / "tmp", timeout_seconds=cfg["timeout_seconds"],
                                       reasoning_effort=cfg["reasoning_effort"], max_input_chars=cfg["max_input_chars"])
        outputs, records, calls = {}, [], []

        def stage_run(stage, source, upstream):
            identifier = source["episode_id"] if source else "cross_episode"
            path, pinned_hash = stage_location(cfg, stage, identifier)
            prompt, source_meta, fingerprint = stage_inputs(cfg, source_map, stage, source, upstream)
            if pinned_hash and not path.is_file():
                raise ValueError("Missing pinned artifact; no automatic regeneration.")
            if path.exists():
                artifact, _ = checked_artifact(cfg, source_map, stage, source, upstream, path, pinned_hash)
                action = "referenced" if pinned_hash else "reused"
            else:
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
                for item in planned:
                    if item["action"] == "referenced" and sha256_file(Path(item["artifact_path"])) != item["sha256"]:
                        raise ValueError("Pinned artifact changed during extraction; result not published.")
                artifact = {"schema_version": VERSION, "stage": stage, "created_at": utc_now(),
                            "fingerprint": fingerprint, "source": source_meta, "content": response,
                            "generation_prompt": prompt,
                            "evidence": ev, "qualification": qual, "warnings": warn, "model_metadata": usage,
                            "semantic_review_status": "pending", "evidence_authority": "level2"}
                atomic_write_json(path, artifact, overwrite=False)
                action = "created"
            records.append({"path": f"{identifier}/{stage}.json", "artifact_path": str(path), "stage": stage, "action": action,
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
        atomic_write_json(root / "run_report.json", report)
        return report


def extend(cfg: dict, additions_path: Path, run_id: str, output: Path, *, replace_existing: bool = False) -> dict:
    """Prepare an explicit update; reference prior artifacts without copying them."""
    output = private_path(output)
    new_root = child_path(private_path(cfg["output_root"]), safe_id(run_id))
    if run_id == cfg["run_id"] or output.exists() or new_root.exists():
        raise ValueError("Extension needs an unused run_id and a new private configuration path.")
    base_report = run(cfg, validate_only=True)
    additions = yaml.safe_load(private_path(additions_path).read_text(encoding="utf-8"))
    if not isinstance(additions, list) or not additions:
        raise ValueError("New sources must be a nonempty list of explicit episode/raw/normalized/review entries.")
    existing = {e["episode_id"]: e for e in cfg["episodes"]}
    seen, replacements = set(), {}
    for entry in additions:
        if not isinstance(entry, dict) or set(entry) != {"episode_id", "raw", "normalized", "review"}:
            raise ValueError("Each new source requires only episode_id, raw, normalized and review.")
        safe_id(entry["episode_id"])
        eid = entry["episode_id"]
        if eid in seen or (eid in existing and not replace_existing):
            raise ValueError("Duplicate episode in extension; replacement requires an explicit new source configuration.")
        seen.add(eid)
        for key in ("raw", "normalized", "review"):
            entry[key] = str(private_path(entry[key]))
        if eid in existing:
            old = existing[eid]
            previous = load_source(old["raw"], old["normalized"], old["review"])
            revised = load_source(entry["raw"], entry["normalized"], entry["review"])
            if _source_meta(previous) == _source_meta(revised):
                raise ValueError("Replacement source is unchanged; reuse it instead of repeating extraction.")
            replacements[eid] = entry
    result = copy.deepcopy(cfg)
    result["run_id"] = run_id
    by_logical_path = {a["path"]: a for a in base_report["artifacts"]}
    for index, entry in enumerate(result["episodes"]):
        if entry["episode_id"] in replacements:
            result["episodes"][index] = replacements[entry["episode_id"]]
            continue
        entry["reuse_artifacts"] = {}
        for stage in STAGES[:3]:
            artifact = by_logical_path[f"{entry['episode_id']}/{stage}.json"]
            entry["reuse_artifacts"][stage] = {"path": artifact["artifact_path"], "sha256": artifact["sha256"]}
    result["episodes"].extend(e for e in additions if e["episode_id"] not in existing)
    result["parent_run"] = {"run_id": cfg["run_id"], "config_sha256": digest(cfg)}
    planned = run(result, dry_run=True)
    atomic_write_json(output, result, overwrite=False)
    return {"config": str(output), "run_id": run_id,
            "new_episode_ids": [e["episode_id"] for e in additions if e["episode_id"] not in existing],
            "replaced_episode_ids": list(replacements),
            "referenced_artifacts": sum(a["action"] == "referenced" for a in planned["artifacts"]),
            "estimated_new_calls": planned["estimated_new_calls"], "model_calls": 0,
            "note": "No extraction or release activation performed. Review the new knowledge before publication."}
