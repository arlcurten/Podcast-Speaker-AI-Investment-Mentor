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

from transcription.common import atomic_write_json, private_path, child_path, sha256_file, safe_id, utc_now, read_json
from .schemas import VERSION, schema, validate_proposal
from .provenance import load_source, digest, evidence, case_catalog, qualify
from .coverage import coverage

PROMPTS = Path(__file__).parent / "prompts"
STAGES = ("level1", "level2", "level3", "level4")
COMPACT_CONTEXT = "compact_candidate.v1"


def candidate_mode(cfg):
    mode = cfg.get("extraction_mode", "reviewed")
    if mode not in {"reviewed", "candidate"}:
        raise ValueError("extraction_mode must be reviewed or candidate.")
    return mode == "candidate"


def _context_format(cfg):
    if "context_format" not in cfg:
        return None
    if cfg["context_format"] != COMPACT_CONTEXT or not candidate_mode(cfg):
        raise ValueError("context_format must be compact_candidate.v1 and requires candidate extraction.")
    return cfg["context_format"]


def artifact_version(cfg):
    if candidate_mode(cfg):
        from .candidates import VERSION as candidate_version
        return candidate_version
    return VERSION


def generation_prompt(cfg, stage):
    prompt = (PROMPTS / (stage + ".md")).read_text(encoding="utf-8")
    if candidate_mode(cfg):
        prompt += "\n\n" + (PROMPTS / "candidate.md").read_text(encoding="utf-8")
    return prompt


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or cfg.get("schema_version") != "knowledge_config.v1":
        raise ValueError("Expected knowledge_config.v1.")
    safe_id(cfg["run_id"])
    candidate = candidate_mode(cfg)
    _context_format(cfg)
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
            if key == "review" and candidate and not entry.get(key):
                entry[key] = None
                continue
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
        if candidate_mode(cfg):
            from .candidates import load_candidate_source
            source = load_candidate_source(entry["raw"], entry["normalized"], entry.get("review"))
        else:
            source = load_source(entry["raw"], entry["normalized"], entry["review"])
        if source["episode_id"] != entry["episode_id"]:
            raise ValueError("Configured/source episode identity mismatch.")
        for key in ("raw_sha256", "normalized_sha256", "review_sha256"):
            if key in entry and source[key] != entry[key]:
                raise ValueError("Pinned source version changed; use a new configuration.")
        result[source["episode_id"]] = source
    return result


def _source_meta(source: dict) -> dict:
    result = {k: source[k] for k in ("episode_id", "raw_sha256", "normalized_sha256", "review_sha256", "publication_date")}
    if "annotations_sha256" in source:
        result["annotations_sha256"] = source["annotations_sha256"]
    if source.get("extraction_mode") == "candidate":
        result.update(extraction_mode="candidate", source_acceptance="not_granted")
        for key in ("source_recovery", "source_limitations", "prior_source_decision", "prior_review_limitations"):
            if key in source:
                result[key] = source[key]
    return result


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
    prompt = prompt_override if prompt_override is not None else generation_prompt(cfg, stage)
    meta = _source_meta(source) if source else {"episodes": [_source_meta(s) for s in source_map.values()]}
    inputs = {"source": meta, "upstream": [digest(a) for a in upstream],
              "schema": schema(stage, candidate=candidate_mode(cfg)), "prompt": prompt, "model": cfg["model"],
              "reasoning_effort": cfg["reasoning_effort"], "schema_version": artifact_version(cfg)}
    if stage == "level4" and _context_format(cfg) is not None:
        inputs["context_format"] = cfg["context_format"]
    fingerprint = digest(inputs)
    return prompt, meta, fingerprint


def checked_artifact(cfg, source_map, stage, source, upstream, path, expected_sha=None, *, allow_pinned_prompts=False):
    payload = path.read_bytes()
    file_hash = hashlib.sha256(payload).hexdigest()
    if expected_sha is not None and file_hash != expected_sha:
        raise ValueError("Reused artifact checksum changed; no automatic regeneration or silent replacement.")
    artifact = json.loads(payload)
    if not isinstance(artifact, dict):
        raise ValueError("Knowledge artifact must be a JSON object.")
    if artifact.get("schema_version") != artifact_version(cfg):
        raise ValueError("Incompatible knowledge schema; preserve old outputs and regenerate with a new run_id without reuse_artifacts.")
    pinned_prompt = artifact.get("generation_prompt")
    if pinned_prompt is not None and (not isinstance(pinned_prompt, str) or not pinned_prompt.strip()):
        raise ValueError("Malformed pinned generation prompt.")
    if (not allow_pinned_prompts and pinned_prompt is not None
            and pinned_prompt != generation_prompt(cfg, stage)):
        raise ValueError("Existing knowledge prompt is incompatible; use a new run_id.")
    if stage == "level4" and artifact.get("context_format") != _context_format(cfg):
        raise ValueError("Existing knowledge context format is incompatible; use a new run_id.")
    _, meta, fingerprint = stage_inputs(cfg, source_map, stage, source, upstream,
                                       pinned_prompt if allow_pinned_prompts else None)
    if (artifact.get("schema_version") != artifact_version(cfg) or artifact.get("fingerprint") != fingerprint
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
    candidate = (source or {}).get("extraction_mode") == "candidate" or any(
        a.get("schema_version") == "knowledge_candidate.v1" for a in upstream)
    validate_proposal(stage, content, candidate=candidate)
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
    if candidate:
        from .candidates import derive_candidate
        derive_candidate(stage, content, source, upstream, resolved, qualifications, warnings)
    return resolved, qualifications, warnings


def _compact_candidate_context(result: dict, upstream: list[dict]) -> dict:
    if any(a.get("schema_version") != "knowledge_candidate.v1" for a in upstream):
        raise ValueError("Compact candidate context requires candidate upstream artifacts.")
    result = copy.deepcopy(result)
    sources, issues = {}, {}

    def register(catalog, reference, value):
        previous = catalog.get(reference, {})
        if any(previous[key] != value[key] for key in previous.keys() & value.keys()):
            raise ValueError("Conflicting compact-context catalog reference.")
        catalog[reference] = {**previous, **value}
        return reference

    for artifact in upstream:
        if artifact["stage"] != "level2":
            continue
        eid = artifact["source"]["episode_id"]
        if eid in sources:
            raise ValueError("Duplicate episode in compact context.")
        sources[eid] = {"source": copy.deepcopy(artifact["source"]),
                        "thread_dispositions": copy.deepcopy(artifact["content"]["thread_dispositions"])}
    for case in result["cases"]:
        eid = case["episode_id"]
        ev = case["evidence"]
        meta = sources[eid]["source"]
        if (ev["episode_id"] != eid or ev["source_raw_sha256"] != meta["raw_sha256"]
                or ev["source_normalized_sha256"] != meta["normalized_sha256"]):
            raise ValueError("Compact case evidence disagrees with its source reference.")
        ev["source_ref"] = eid
        for key in ("episode_id", "source_raw_sha256", "source_normalized_sha256"):
            del ev[key]
        notes = ev.pop("reviewed_annotations", None)
        if notes is not None:
            if notes["sha256"] != meta.get("annotations_sha256"):
                raise ValueError("Compact annotation/source hash mismatch.")
            binding = {"sha256": notes["sha256"],
                       "supporting_review_sha256": copy.deepcopy(notes["review"].get("supporting_review_sha256", []))}
            register(sources[eid], "annotation_provenance", binding)
            ev["reviewed_annotation_refs"] = []
            for note in notes["annotations"]:
                compact_note = {**note, "episode_id": eid, "issue_id": note["annotation_id"],
                                "authority": "reviewer_commentary_not_source",
                                "anchors": [{k: v for k, v in anchor.items() if k != "raw_text"}
                                            for anchor in note["anchors"]]}
                reference = eid + "/" + note["annotation_id"]
                ev["reviewed_annotation_refs"].append(register(issues, reference, compact_note))
        assessment = ev["candidate_source_assessment"]
        local_issues = {}
        for issue in assessment.pop("source_issues"):
            reference = eid + "/" + issue["issue_id"]
            if issue["issue_id"] in local_issues:
                raise ValueError("Duplicate compact case source issue.")
            local_issues[issue["issue_id"]] = register(issues, reference, {**issue, "episode_id": eid})
        assessment["source_issue_refs"] = list(local_issues.values())
        dispositions = assessment["source_issue_dispositions"]
        if (len(dispositions) != len(local_issues)
                or {d["issue_id"] for d in dispositions} != set(local_issues)):
            raise ValueError("Compact case dispositions require exact source-issue references.")
        for disposition in dispositions:
            disposition["source_issue_ref"] = local_issues[disposition["issue_id"]]
    result.update(context_format=COMPACT_CONTEXT, sources=sources, source_issues=issues, context_legend={
                      "cases": "Every L2 case is retained verbatim; evidence source_ref resolves to sources[episode_id].source hashes. "
                               "Raw ID ranges are inclusive; timestamp_regions retain separate cited regions.",
                      "notes": "reviewed_annotation_refs and source_issue_refs both resolve to the top-level source_issues catalog. "
                               "Keys are episode-scoped. Notes and candidate assessments are commentary, not speaker evidence.",
                      "omissions": "sources[episode_id].thread_dispositions retains every omission reason, including ambiguity "
                                   "inside used regions. Omitted subclaims must not be treated as case support.",
                      "provenance": "Only repeated raw anchor text and annotation-review blobs are omitted from this model view. "
                                    "Exact note text, corrections, raw anchor IDs/times and source/review hashes are retained. "
                                    "Full provenance remains in the immutable upstream artifacts; no source acceptance is granted."})
    return result


def context(stage: str, source: dict | None, upstream: list[dict], context_format=None) -> dict:
    if context_format is not None and context_format != COMPACT_CONTEXT:
        raise ValueError("Unsupported context_format.")
    if stage in {"level1", "level2"}:
        result = {"episode": _source_meta(source), "transcript": [
            {k: s[k] for k in ("merged_id", "start", "end", "normalized_text_zh_tw", "terminology_annotations")}
            for s in source["segments"]]}
        if "reviewed_annotations" in source:
            result["reviewed_annotations"] = copy.deepcopy(source["reviewed_annotations"])
            for note in result["reviewed_annotations"]["annotations"]:
                ids = {a["segment_id"] for a in note["anchors"]}
                note["merged_ids"] = [s["merged_id"] for s in source["segments"] if ids.intersection(s["source_segment_ids"])]
        if stage == "level2":
            result["episode_map"] = upstream[0]["content"]
        if source.get("extraction_mode") == "candidate":
            from .candidates import source_issues
            result["source_issues"] = source_issues(source, upstream[0] if stage == "level2" else None)
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
    if stage == "level4" and context_format == COMPACT_CONTEXT:
        return _compact_candidate_context(result, upstream)
    return result


def run(cfg: dict, *, through: str = "level4", dry_run: bool = False, validate_only: bool = False,
        backend=None, allow_pinned_prompts: bool = False) -> dict:
    from .codex_backend import CodexBackend

    context_format = _context_format(cfg)
    if allow_pinned_prompts and not validate_only:
        raise ValueError("Pinned prompts are for reviewed release validation, never new extraction/reuse planning.")
    source_map = sources(cfg)  # Fail before any paid call if quality or provenance is invalid.
    root = child_path(private_path(cfg["output_root"]), cfg["run_id"])
    planned = plan_stages(cfg, source_map, through, allow_pinned_prompts=allow_pinned_prompts)
    if dry_run:
        return {"dry_run": True, "episode_ids": list(source_map), "through": through,
                "maximum_initial_calls": len(source_map) * min(STAGES.index(through) + 1, 3) + int(through == "level4"),
                "estimated_new_calls": sum(r["action"] == "planned" for r in planned),
                "artifacts": planned, "output": str(root), "sources_accepted": not candidate_mode(cfg),
                "extraction_mode": "candidate" if candidate_mode(cfg) else "reviewed"}
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
                    request_context = context(stage, source, upstream, context_format=context_format)
                    recovered = None
                    # Resume/one bounded retry can repair a rejected proposal using
                    # the real validator error, without overwriting that attempt.
                    previous_calls = sorted((root / "calls").glob(f"{identifier}_{stage}_*.json"),
                                            key=lambda p: int(p.stem.rsplit("_", 1)[1]))
                    if previous_calls:
                        previous = read_json(previous_calls[-1])
                        if (previous.get("status") == "failed" and previous.get("fingerprint") == fingerprint
                                and isinstance(previous.get("response"), dict)):
                            try:
                                derive(stage, previous["response"], source, upstream)
                            except ValueError as invalid:
                                request_context["validation_repair"] = {
                                    "previous_proposal": previous["response"],
                                    "validation_error": str(invalid)[:2000],
                                    "instruction": "Correct the invalid structure/references against the supplied source. "
                                                   "Do not invent context, evidence or speaker claims to satisfy validation.",
                                    "previous_call_sha256": sha256_file(previous_calls[-1])}
                            else:
                                # A candidate rejected by an older validator may now
                                # pass. Preserve that call and revalidate its exact
                                # proposal, rather than paying to recreate it.
                                if candidate_mode(cfg):
                                    recovered = (previous, sha256_file(previous_calls[-1]))
                    usage, response = None, None
                    try:
                        if recovered is not None:
                            response = recovered[0]["response"]
                            usage = {"provider": "candidate_revalidation", "model": cfg["model"],
                                     "model_call_performed": False, "usage": None, "cost_usd": None,
                                     "latency_seconds": 0, "recovered_call_sha256": recovered[1],
                                     "note": "No new inference; original model usage remains in the immutable failed call."}
                        else:
                            response, usage = model.complete(prompt, request_context, schema(stage, candidate=candidate_mode(cfg)))
                        ev, qual, warn = derive(stage, response, source, upstream)
                    except (ValueError, RuntimeError) as exc:
                        usage = getattr(exc, "metadata", usage)
                        report = {"stage": stage, "episode_id": identifier, "status": "failed", "attempt": attempt + 1,
                                  "error_type": type(exc).__name__, "response": response, "usage": usage, "fingerprint": fingerprint,
                                  "request_context_sha256": digest(request_context),
                                  "repair_from_sha256": request_context.get("validation_repair", {}).get("previous_call_sha256")}
                        calls.append(report)
                        atomic_write_json(root / "calls" / (call_id + ".json"), report, overwrite=False)
                        if attempt == cfg["retry_count"]:
                            raise ValueError(f"{stage} extraction/validation failed; inspect private call record, never publish invalid output.") from exc
                    else:
                        report = {"stage": stage, "episode_id": identifier,
                                  "status": "revalidated" if recovered is not None else "completed", "attempt": attempt + 1,
                                  "usage": usage, "fingerprint": fingerprint,
                                  "request_context_sha256": digest(request_context),
                                  "repair_from_sha256": request_context.get("validation_repair", {}).get("previous_call_sha256")}
                        calls.append(report)
                        atomic_write_json(root / "calls" / (call_id + ".json"), report, overwrite=False)
                        break
                # Re-read source versions before publishing any derivative.
                if sources(cfg) != source_map:
                    raise ValueError("Source changed during extraction; result not published.")
                for item in planned:
                    if item["action"] == "referenced" and sha256_file(Path(item["artifact_path"])) != item["sha256"]:
                        raise ValueError("Pinned artifact changed during extraction; result not published.")
                artifact = {"schema_version": artifact_version(cfg), "stage": stage, "created_at": utc_now(),
                            "fingerprint": fingerprint, "source": source_meta, "content": response,
                            "generation_prompt": prompt,
                            "evidence": ev, "qualification": qual, "warnings": warn, "model_metadata": usage,
                            "semantic_review_status": "pending", "evidence_authority": "level2"}
                if stage == "level4" and context_format is not None:
                    artifact["context_format"] = context_format
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
                  "model_calls_this_execution": sum(c.get("usage", {}).get("model_call_performed", True)
                                                     if isinstance(c.get("usage"), dict) else 1 for c in calls),
                  "revalidated_candidate_stages": sum(c["status"] == "revalidated" for c in calls),
                  "ready_for_serving": False,
                  "note": "Structural/evidence validation is not semantic certification. Publish only after source-grounded review."}
        atomic_write_json(root / "run_report.json", report)
        return report


def extend(cfg: dict, additions_path: Path, run_id: str, output: Path, *, replace_existing: bool = False) -> dict:
    """Prepare an explicit update; reference prior artifacts without copying them."""
    if candidate_mode(cfg):
        raise ValueError("Candidate extension uses a new explicit batch/configuration, not reviewed-source promotion.")
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
