"""Versioned, lossless L3 input transport; original pipeline artifacts stay intact."""
from __future__ import annotations

import copy
from pathlib import Path

from transcription.common import sha256_file
from .codex_backend import CodexBackend, ModelCallError
from .provenance import digest

FORMAT = "knowledge_l3_reference.v1"
CODE_SHA256 = sha256_file(Path(__file__))
CATALOGS = ("annotation_metadata_catalog", "annotation_catalog", "source_issue_catalog")
RESERVED = {*CATALOGS, "context_format", "context_legend"}
LEGEND = (
    "This is a lossless reference representation of the complete L3 case context. "
    "Each evidence.reviewed_annotations.metadata_ref resolves to annotation_metadata_catalog; "
    "annotation_refs resolve in order to full annotation_catalog objects, including every raw_text anchor. "
    "Each candidate_source_assessment.source_issues.issue_refs resolves in order to full source_issue_catalog "
    "objects. Catalog keys are episode-scoped. All cases, assessments, dispositions, unknown fields, "
    "source text, IDs, timestamps and hashes remain unchanged. References preserve order and multiplicity. "
    "Notes and assessments remain reviewer/model commentary, not speaker evidence. Shared objects do not "
    "represent additional observations or establish independence. validation_repair, if present, remains "
    "the previous model proposal and validator feedback, not source evidence. No source acceptance is granted."
)


def _identifier(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Transport references require nonempty string identifiers.")
    return value


def _evidence(context):
    if not isinstance(context, dict) or not isinstance(context.get("episode"), dict):
        raise ValueError("L3 transport requires explicit episode metadata.")
    eid = _identifier(context["episode"].get("episode_id"))
    if not isinstance(context.get("cases"), list):
        raise ValueError("L3 transport requires a complete case list.")
    for case in context["cases"]:
        if (not isinstance(case, dict) or case.get("episode_id") != eid
                or not isinstance(case.get("evidence"), dict) or case["evidence"].get("episode_id") != eid):
            raise ValueError("L3 case evidence must belong to the context episode.")
        yield eid, case["evidence"]


def _register(catalog, key, value):
    if key in catalog and digest(catalog[key]) != digest(value):
        raise ValueError("Conflicting L3 transport catalog reference.")
    catalog[key] = copy.deepcopy(value)
    return key


def compact_level3_context(context: dict) -> dict:
    """Deduplicate full objects, then verify exact JSON-value reconstruction."""
    if not isinstance(context, dict) or RESERVED.intersection(context):
        raise ValueError("L3 transport reserved fields must not overwrite existing context.")
    result = copy.deepcopy(context)
    metadata, annotations, issues = {}, {}, {}
    for eid, evidence in _evidence(result):
        notes = evidence.get("reviewed_annotations")
        if notes is not None:
            if not isinstance(notes, dict) or not isinstance(notes.get("annotations"), list):
                raise ValueError("Malformed reviewed annotation catalog.")
            meta = {key: value for key, value in notes.items() if key != "annotations"}
            refs = [_register(annotations, eid + "/" + _identifier(note.get("annotation_id")), note)
                    for note in notes["annotations"]]
            evidence["reviewed_annotations"] = {
                "metadata_ref": _register(metadata, eid, meta), "annotation_refs": refs}
        assessment = evidence.get("candidate_source_assessment")
        if assessment is not None:
            if not isinstance(assessment, dict):
                raise ValueError("Malformed candidate source assessment.")
            local = assessment.get("source_issues")
            if local is not None:
                if not isinstance(local, list):
                    raise ValueError("Source issues must be a list, not pre-existing references.")
                assessment["source_issues"] = {"issue_refs": [
                    _register(issues, eid + "/" + _identifier(issue.get("issue_id")), issue) for issue in local]}
    result.update(annotation_metadata_catalog=metadata, annotation_catalog=annotations,
                  source_issue_catalog=issues, context_format=FORMAT, context_legend=LEGEND)
    if digest(expand_level3_context(result)) != digest(context):
        raise ValueError("L3 transport failed exact round-trip validation.")
    return result


def expand_level3_context(context: dict) -> dict:
    """Restore the full context, including unknown fields and null/missing values."""
    result = copy.deepcopy(context)
    if result.pop("context_format", None) != FORMAT or result.pop("context_legend", None) != LEGEND:
        raise ValueError("Unsupported L3 transport format or legend.")
    metadata, annotations, issues = (result.pop(key) for key in CATALOGS)

    def resolve(catalog, key, eid):
        if not isinstance(key, str) or not key.startswith(eid + "/") or key not in catalog:
            raise ValueError("Unresolved or cross-episode L3 transport reference.")
        return copy.deepcopy(catalog[key])

    for eid, evidence in _evidence(result):
        notes = evidence.get("reviewed_annotations")
        if notes is not None:
            if set(notes) != {"metadata_ref", "annotation_refs"} or notes["metadata_ref"] != eid:
                raise ValueError("Invalid annotation metadata reference.")
            evidence["reviewed_annotations"] = {
                **copy.deepcopy(metadata[eid]),
                "annotations": [resolve(annotations, ref, eid) for ref in notes["annotation_refs"]]}
        assessment = evidence.get("candidate_source_assessment")
        if assessment is not None and assessment.get("source_issues") is not None:
            refs = assessment["source_issues"]
            if set(refs) != {"issue_refs"}:
                raise ValueError("Invalid source issue references.")
            assessment["source_issues"] = [resolve(issues, ref, eid) for ref in refs["issue_refs"]]
    return result


class L3ReferenceBackend:
    """CodexBackend-compatible factory; only L3 calls receive a new transport."""

    def __init__(self, model: str, work_root: Path, *, timeout_seconds: int = 1800,
                 reasoning_effort: str = "medium", max_input_chars: int = 180000):
        self._backend = CodexBackend(model, work_root, timeout_seconds=timeout_seconds,
                                     reasoning_effort=reasoning_effort, max_input_chars=max_input_chars)

    def complete(self, prompt: str, context: dict, schema: dict) -> tuple[dict, dict]:
        if "strategy" not in schema.get("properties", {}) or "episode" not in context:
            return self._backend.complete(prompt, context, schema)
        compact = compact_level3_context(context)
        record = {"format": FORMAT, "original_context_sha256": digest(context),
                  "transport_context_sha256": digest(compact), "code_sha256": CODE_SHA256}

        def metadata(value):
            if not isinstance(value, dict) or "transport" in value:
                raise ValueError("Backend metadata must leave the transport namespace available.")
            return {**value, "transport": record}

        try:
            response, usage = self._backend.complete(prompt, compact, schema)
        except ModelCallError as exc:
            exc.metadata = metadata(exc.metadata)
            raise
        return response, metadata(usage)
