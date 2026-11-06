from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from knowledge.release import load_release
from transcription.common import private_path, sha256_file
from .contracts import ANALYSIS, ASSESSMENT, ANSWER
from .model import OpenRouter, ModelError
from .retrieval import KnowledgeIndex

PROMPTS = Path(__file__).parent / "prompts"
CURRENT = re.compile(r"現在|今天|最新|目前股價|今日|current|today|latest|right now", re.I)
OVERREACH = re.compile(r"他一定|講者一定|永遠都|保證獲利|穩賺|speaker always|guaranteed profit|must buy now", re.I)


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or cfg.get("schema_version") != "mentor_config.v1":
        raise ValueError("Expected mentor_config.v1.")
    cfg["release"] = str(private_path(os.environ.get("MENTOR_RELEASE") or cfg["release"]))
    cfg["model"] = os.environ.get("MENTOR_MODEL_ID") or cfg["model"]
    if not cfg["model"]:
        raise ValueError("Configure MENTOR_MODEL_ID.")
    for key in ("max_output_tokens", "max_context_chars", "timeout_seconds", "max_query_chars", "minimum_host_cases", "maximum_answer_chars"):
        if type(cfg.get(key)) is not int or cfg[key] < 1:
            raise ValueError(f"Invalid {key}.")
    if cfg.get("retry_count") not in (0, 1):
        raise ValueError("At most one structured retry is allowed.")
    r = cfg["retrieval"]
    for key in ("candidate_limit", "case_limit", "max_per_episode", "search_query_limit", "principle_limit"):
        if type(r.get(key)) is not int or r[key] < 1:
            raise ValueError(f"Invalid retrieval.{key}.")
    if not 0 < r["minimum_score"] <= 1 or r["bm25_scale"] <= 0:
        raise ValueError("Invalid retrieval thresholds.")
    if any(r[k] < 0 for k in ("lexical_weight", "cosine_weight", "metadata_weight")):
        raise ValueError("Retrieval weights must be nonnegative.")
    return cfg


def timestamp(seconds: float) -> str:
    value = int(seconds)
    return f"{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}"


def citation(case: dict) -> dict:
    evidence = case["evidence"]
    regions = evidence["timestamp_regions"]
    if evidence["episode_id"] != case["episode_id"] or not regions:
        raise ValueError("Case citation/source episode mismatch.")
    return {"episode_id": case["episode_id"], "record_id": case["record_id"], "regions": regions,
            "source_raw_sha256": evidence["source_raw_sha256"],
            "knowledge_artifact_sha256": case["knowledge_artifact_sha256"],
            "label": f"Source: {case['episode_id']}, " + "; ".join(timestamp(r["start"]) + "-" + timestamp(r["end"]) for r in regions) + f", record {case['record_id']}"}


def compact_case(case: dict) -> dict:
    fields = ("record_id", "episode_id", "publication_date", "title", "speaker_attribution", "claims", "reasoning_steps",
              "assumptions", "conditions", "risks", "exceptions", "uncertainty", "contradictions",
              "preferred_actions", "decision_preferences", "view_change_conditions", "speaker_behavior", "domains")
    return {**{k: case[k] for k in fields}, "evidence": case["evidence"],
            "candidate_roles": case["retrieval"]["roles"], "role_reasons": case["retrieval"]["role_reasons"]}


def assessment_references(assessment: dict) -> set[str]:
    ids = set(assessment["supporting_cases"])
    for key in ("conflicting_evidence", "context_differences", "exceptions_triggered", "unresolved_conflicts"):
        for row in assessment[key]:
            if len(row["record_ids"]) != len(set(row["record_ids"])):
                raise ValueError("Evidence comparison repeats the same case as two sides.")
            ids.update(row["record_ids"])
    return ids


def finalize_route(analysis: dict, retrieved: dict, assessment: dict, minimum_host_cases: int) -> str:
    cases = {c["record_id"]: c for c in retrieved["cases"]}
    principles = {p["principle_id"]: p for p in retrieved["principles"]}
    if not assessment_references(assessment) <= cases.keys():
        raise ValueError("Assessment cites cases outside retrieved evidence.")
    if not set(assessment["applicable_principles"]) <= principles.keys():
        raise ValueError("Assessment uses an unavailable principle.")
    rejected = {r["principle_id"] for r in assessment["rejected_principles"]}
    if not rejected <= principles.keys() or rejected & set(assessment["applicable_principles"]):
        raise ValueError("Inconsistent principle acceptance/rejection.")
    assessment["missing_information"] = list(dict.fromkeys(analysis["missing_information"] + assessment["missing_information"]))
    assessment["current_data_required"] |= analysis["current_data_required"]
    host_support = {rid for rid in assessment["supporting_cases"] if cases[rid]["speaker_attribution"] == "host"}
    route = assessment["selected_route"]
    if len(host_support) < minimum_host_cases:
        route = "insufficient_evidence"
    elif route == "principle_assisted" and not assessment["applicable_principles"]:
        route = "case_only"
    if assessment["unresolved_conflicts"]:
        assessment["confidence"] = "low"
        if route == "principle_assisted":
            route = "case_only"
        if any(c["severity"] == "major" and c["missing_information"] for c in assessment["unresolved_conflicts"]):
            route = "insufficient_evidence"
        assessment["missing_information"] = list(dict.fromkeys(assessment["missing_information"] + [
            s for c in assessment["unresolved_conflicts"] for s in c["missing_information"]]))
    if route != "principle_assisted":
        for pid in assessment["applicable_principles"]:
            assessment["rejected_principles"].append({"principle_id": pid, "reason": "Final evidence/conflict guard downgraded the route"})
        assessment["applicable_principles"] = []
    assessment["selected_route"] = route
    return route


def validate_answer(answer: dict, assessment: dict, cases: list[dict], settings: dict) -> list[dict]:
    by_id = {c["record_id"]: c for c in cases}
    cited = set()
    for claim in answer["historical_evidence"]:
        if not claim["record_ids"] or any(r not in by_id for r in claim["record_ids"]):
            raise ValueError("Historical claim lacks retrieved citation.")
        if any(by_id[r]["speaker_attribution"] != "host" for r in claim["record_ids"]):
            raise ValueError("Non-host evidence cannot ground a host-position claim.")
        cited.update(claim["record_ids"])
    if not cited:
        raise ValueError("A substantive Mentor answer needs source cases.")
    required = set()
    for key in ("conflicting_evidence", "context_differences", "unresolved_conflicts", "exceptions_triggered"):
        required.update(r for row in assessment[key] for r in row["record_ids"])
    if not required <= cited:
        raise ValueError("Answer omitted a conflict/context/exception side citation.")
    if required and not answer["conflicts_and_exceptions"]:
        raise ValueError("Answer silently dropped classified evidence differences.")
    if assessment["current_data_required"] and not answer["current_data_limitation"].strip():
        raise ValueError("Current data limitation is required.")
    if assessment["missing_information"] and not answer["missing_information"]:
        raise ValueError("Answer dropped missing scenario information.")
    text = json.dumps(answer, ensure_ascii=False)
    if OVERREACH.search(text):
        raise ValueError("Unconditional speaker policy or investment direction rejected.")
    if len(text) > settings["maximum_answer_chars"]:
        raise ValueError("Answer exceeds concise output budget.")
    if assessment["unresolved_conflicts"] and not answer["missing_information"]:
        raise ValueError("Unresolved conflict needs explicit uncertainty/missing information.")
    return [citation(by_id[rid]) for rid in sorted(cited)]


class Mentor:
    def __init__(self, settings: dict, *, backend=None):
        self.settings = settings
        self.release_path = private_path(settings["release"])
        self.release_hash = sha256_file(self.release_path)
        cases, principles = load_release(self.release_path)
        self.index = KnowledgeIndex(cases, principles, settings["retrieval"])
        self.backend = backend

    def call(self, stage: str, context: dict, schema: dict, calls: list) -> dict:
        if self.backend is None:
            self.backend = OpenRouter(self.settings)
        prompt = (PROMPTS / f"{stage}.md").read_text(encoding="utf-8")
        for attempt in range(self.settings["retry_count"] + 1):
            metadata = None
            try:
                result, metadata = self.backend.complete(prompt, context, schema)
                Draft202012Validator(schema).validate(result)
                calls.append({"stage": stage, "attempt": attempt + 1, "status": "completed", "metadata": metadata})
                return result
            except Exception as exc:
                calls.append({"stage": stage, "attempt": attempt + 1, "status": "failed", "error_type": type(exc).__name__,
                              "metadata": getattr(exc, "metadata", metadata)})
                if attempt == self.settings["retry_count"]:
                    raise ModelError(f"{stage} structured call failed; safe fallback required.") from None
        raise AssertionError("Unreachable")

    def ask(self, query: str, *, no_llm: bool = False) -> dict:
        if not isinstance(query, str) or not query.strip() or len(query) > self.settings["max_query_chars"]:
            raise ValueError("Query must be nonempty and within the configured length limit.")
        # Review revocation and changes to pinned artifacts invalidate the service,
        # even if an earlier request loaded a valid in-memory index.
        if sha256_file(self.release_path) != self.release_hash:
            raise ValueError("Knowledge release changed; restart with the reviewed version.")
        load_release(self.release_path)
        result = {"query": query, "route": "insufficient_evidence", "analysis": None, "retrieval": None,
                  "assessment": None, "answer": None, "citations": [], "calls": [], "guards": [],
                  "release_sha256": self.release_hash}
        try:
            if no_llm:
                result["retrieval"] = {"diagnostic_hits": self.index.search(query, []), "model_used": False}
                return self.fallback(result, "No-LLM mode: evidence search only, not a speaker answer.")
            analysis = self.call("analysis", {"query": query, "principle_index": self.index.principle_index(),
                                             "available_domains": sorted({d for c in self.index.cases.values() for d in c["domains"]})}, ANALYSIS, result["calls"])
            analysis["current_data_required"] |= bool(CURRENT.search(query))
            result["analysis"] = analysis
            retrieval = self.index.select(query, analysis)
            result["retrieval"] = retrieval
            hosts = [c for c in retrieval["cases"] if c["speaker_attribution"] == "host"]
            if len(hosts) < self.settings["minimum_host_cases"]:
                return self.fallback(result, "No sufficient relevant host-attributed historical cases were retrieved.")
            result["candidate_route"] = "principle_assisted" if retrieval["principles"] else "case_only"
            compact = [compact_case(c) for c in retrieval["cases"]]
            assessment = self.call("applicability", {"query": query, "analysis": analysis,
                                   "principles": retrieval["principles"], "cases": compact}, ASSESSMENT, result["calls"])
            result["assessment"] = assessment
            result["route"] = finalize_route(analysis, retrieval, assessment, self.settings["minimum_host_cases"])
            if result["route"] == "insufficient_evidence":
                return self.fallback(result, "Historical evidence/conflicts do not support a safe conditional speaker conclusion.")
            # Full principles and retrieval diagnostics are not repeated in the answer call.
            answer = self.call("answer", {"query": query, "scenario_facts": analysis["scenario_facts"],
                                           "assessment": assessment, "cases": compact}, ANSWER, result["calls"])
            result["citations"] = validate_answer(answer, assessment, retrieval["cases"], self.settings)
            if assessment["current_data_required"]:
                answer["current_data_limitation"] = "Current prices/news/company conditions were not verified. Historical evidence is not a current buy/sell recommendation."
            answer["missing_information"] = list(dict.fromkeys(assessment["missing_information"] + answer["missing_information"]))
            result["answer"] = answer
            result["guards"] = [{"status": "passed", "scope": "Schema, retrieved IDs, source timestamps, conflict-side coverage and rule-based safety; NOT semantic entailment proof."}]
            return result
        except (ModelError, ValueError, KeyError) as exc:
            return self.fallback(result, f"Validated answer unavailable ({type(exc).__name__}); no unverified speaker answer was emitted.")

    def fallback(self, result: dict, reason: str) -> dict:
        result["route"] = "insufficient_evidence"
        assessment = result["assessment"]
        citations = []
        if assessment and result["retrieval"] and "cases" in result["retrieval"]:
            cases = {c["record_id"]: c for c in result["retrieval"]["cases"]}
            # Preserve both sides in a non-directional fallback, not the failed answer.
            refs = {rid for k in ("conflicting_evidence", "unresolved_conflicts") for c in assessment[k] for rid in c["record_ids"]}
            citations = [citation(cases[r]) for r in sorted(refs) if r in cases]
        result["citations"] = citations
        result["answer"] = {"conditional_answer": "Evidence is insufficient for a speaker-specific decision. " + reason,
                            "historical_evidence": [], "interpretation": "No current trading conclusion is supported.",
                            "conditions_and_risks": [], "conflicts_and_exceptions": ["Unresolved evidence differences remain; do not choose a side."] if citations else [],
                            "missing_information": (assessment or result["analysis"] or {}).get("missing_information", []),
                            "current_data_limitation": "No live market or company information is available."}
        result["guards"] = [{"status": "fallback", "reason": reason}]
        return result
