"""One whole-episode text review, with immutable private records and no retries."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from importlib.metadata import version
from pathlib import Path

from jsonschema import Draft202012Validator

from .annotations import load_annotations
from .common import TERMINOLOGY, atomic_write_json, private_path, read_json, sha256_file, utc_now
from .prepare import annotate_terms, load_annotation_terms, merge_segments
from .quality import quality_gate, validate_payload
from .review import METRICS, REVIEW_THRESHOLDS, review_signals


PROMPT = Path(__file__).with_name("prompts") / "source_review.md"
SCOPE = ("Whole-episode TEXT-BASED semantic-readiness review only. No audio was available or "
         "listened to; ASR accuracy and omitted speech cannot be certified. Historical material, "
         "not current investment advice. Coverage is a model declaration, not proof of attention.")
TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
ID = {"type": "integer", "minimum": 0}


def _object(**fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


def _array(item):
    return {"type": "array", "items": item}


ANCHOR = _object(segment_id=ID, raw_text=TEXT, start={"type": "number"}, end={"type": "number"})
ISSUE = _object(issue_id=TEXT, severity={"enum": ["critical", "noncritical"]},
                disposition={"enum": ["unresolved", "caution"]}, explanation=TEXT,
                anchors={**_array(ANCHOR), "minItems": 1})
SCHEMA = _object(
    schema_version={"enum": ["source_review_result.v1"]}, episode_id=TEXT,
    transcript_sha256=TEXT, draft_sha256=TEXT,
    decision={"enum": ["accept", "uncertain", "retranscribe"]}, rationale=TEXT,
    coverage=_object(complete_episode={"type": "boolean"}, raw_segment_count=ID,
                     source_segment_id_ranges=_array(_object(start_id=ID, end_id=ID))),
    issues=_array(ISSUE), annotations_reviewed={"type": "boolean"},
    method=_object(basis={"enum": ["text_only_semantic_readiness"]},
                   audio_available={"enum": [False]}, audio_listened={"enum": [False]},
                   description=TEXT, limitations={**_array(TEXT), "minItems": 1}),
)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _validate(value, schema):
    if next(Draft202012Validator(schema).iter_errors(value), None) is not None:
        raise ValueError("Source review does not conform to its structured schema.")


def _check_pins(pins):
    if any(sha256_file(path) != checksum for path, checksum in pins.items()):
        raise ValueError("Source, draft or review implementation changed; use a new output version.")


def _coverage(raw):
    ranges = []
    for segment in raw["segments"]:
        sid = segment["segment_id"]
        if ranges and sid == ranges[-1]["end_id"] + 1:
            ranges[-1]["end_id"] = sid
        else:
            ranges.append({"start_id": sid, "end_id": sid})
    return {"complete_episode": True, "raw_segment_count": len(raw["segments"]),
            "source_segment_id_ranges": ranges}


def _issues(items, raw):
    _validate(items, _array(ISSUE))
    originals = {s["segment_id"]: s for s in raw["segments"]}
    seen = set()
    for issue in items:
        if issue["issue_id"] in seen:
            raise ValueError("Duplicate source-review issue ID.")
        seen.add(issue["issue_id"])
        if issue["severity"] == "critical" and issue["disposition"] != "unresolved":
            raise ValueError("Critical issues cannot be downgraded to cautions.")
        anchored = set()
        for anchor in issue["anchors"]:
            sid = anchor["segment_id"]
            if type(sid) is not int or sid not in originals or sid in anchored:
                raise ValueError("Unresolved or duplicate raw issue anchor.")
            anchored.add(sid)
            source = originals[sid]
            if (anchor["raw_text"] != source["text"]
                    or any(type(anchor[k]) not in (int, float) or not math.isfinite(anchor[k])
                           or anchor[k] != source[k] for k in ("start", "end"))):
                raise ValueError("Issue anchors must preserve exact raw text and timestamps.")


def _source(raw_path, draft_path, pins):
    raw, draft = read_json(raw_path), read_json(draft_path)
    validation = validate_payload(raw)
    if not isinstance(draft, dict) or draft.get("schema_version") != "transcription_normalized.v1":
        raise ValueError("Expected transcription_normalized.v1 draft.")
    import opencc

    parameters = {"max_gap": 1.2, "max_duration": 25.0, "max_chars": 220}
    expected = {
        "episode_id": validation["episode_id"], "source_raw_sha256": pins[raw_path],
        "source_raw_filename": raw_path.name, "terminology_sha256": pins[TERMINOLOGY],
        "annotation_matching_version": "normalized_suggestions.v2",
        "normalization": "OpenCC s2twp; terminology annotations only",
        "opencc_version": version("opencc-python-reimplemented"), "merge_parameters": parameters,
        "source_mapping_complete": True, "not_semantic_segmentation": True,
    }
    metadata = draft.get("metadata")
    if (not isinstance(metadata, dict)
            or _digest({key: metadata.get(key) for key in expected}) != _digest(expected)):
        raise ValueError("Draft normalizer provenance does not match the exact source and current normalizer.")
    converter, terms = opencc.OpenCC("s2twp"), load_annotation_terms()
    rows = []
    for row in merge_segments(raw["segments"], **parameters):
        converted = converter.convert(row["text"])
        rows.append({"merged_id": row["merged_id"], "start": row["start"], "end": row["end"],
                     "raw_text": row["text"], "normalized_text_zh_tw": converted,
                     "source_segment_ids": row["source_segment_ids"],
                     "terminology_annotations": annotate_terms(row["text"], converted, terms, converter)})
    if _digest(draft.get("segments")) != _digest(rows):
        raise ValueError("Draft text, timestamps, annotations or raw mapping differ from the exact normalizer.")
    return raw, draft, validation


def _result(result, raw, fingerprint, prior):
    _validate(result, SCHEMA)
    if (result["episode_id"] != raw["metadata"]["episode_id"]
            or result["transcript_sha256"] != fingerprint["transcript_sha256"]
            or result["draft_sha256"] != fingerprint["draft_sha256"]):
        raise ValueError("Model review is not bound to these exact source versions.")
    if _digest(result["coverage"]) != _digest(_coverage(raw)):
        raise ValueError("Model must explicitly declare complete, exact whole-episode coverage.")
    _issues(result["issues"], raw)
    if (result["issues"] or prior) and not result["annotations_reviewed"]:
        raise ValueError("Model did not explicitly review the retained annotations.")
    blockers = []
    if any(item["severity"] == "critical" for item in result["issues"]):
        blockers.append("unresolved_critical_model_findings")
    if any(item["severity"] == "critical" for item in prior):
        blockers.append("known_critical_prior_findings_require_separate_resolution")
    if raw["metadata"].get("source_recovery"):
        blockers.append("source_recovery_requires_audio_review_unavailable_to_this_reviewer")
    return ("uncertain" if blockers else result["decision"]), blockers


def _artifacts(report, report_path, raw, prior):
    result = report["model_result"]
    reference = {"path": report_path.name, "sha256": sha256_file(report_path)}
    method = SCOPE + " Model method: " + result["method"]["description"]
    method += " Limits: " + "; ".join(result["method"]["limitations"])
    common = {"episode_id": raw["metadata"]["episode_id"],
              "transcript_sha256": report["fingerprint"]["transcript_sha256"],
              "reviewer": "source_review:" + str(report["model_metadata"].get("model", "injected_backend")),
              "reviewed_at": report["created_at"], "method": method}
    notes = [{"annotation_id": f"{origin}:{item['issue_id']}",
              "kind": "uncertainty" if item["disposition"] == "unresolved" else "interpretive_caution",
              "severity": item["severity"], "anchors": item["anchors"], "note": item["explanation"]}
             for origin, items in (("prior", prior), ("model", result["issues"])) for item in items]
    sidecar = ({"schema_version": "transcript_annotations.v1", **common,
                "decision": "accept_annotations", "supporting_reviews": [reference], "annotations": notes}
               if notes else None)
    review = {"schema_version": "transcript_review.v2" if notes else "transcript_review.v1", **common,
              "decision": report["decision"], "supporting_reviews": [reference],
              "sampled_segment_ids": [s["segment_id"] for s in raw["segments"]],
              "coverage": result["coverage"], "unresolved_critical_issues": report["blockers"]}
    return review, sidecar


def _reuse(folder, raw_path, raw, prior, fingerprint, pins):
    marker = folder / "complete.json"
    if not marker.is_file():
        raise ValueError("Incomplete source-review output; provider request will not be repeated. Use a new version explicitly.")
    completion = read_json(marker)
    files = completion.get("files", {})
    if (completion.get("schema_version") != "source_review_completion.v1"
            or completion.get("fingerprint") != fingerprint
            or not isinstance(files, dict)
            or set(files) not in ({"report.json", "review.json"}, {"report.json", "review.json", "annotations.json"})):
        raise ValueError("Saved source review does not match the exact current context.")
    saved = {folder / name: checksum for name, checksum in files.items()}
    saved[marker] = sha256_file(marker)
    for path in saved:
        if private_path(path).parent != folder:
            raise ValueError("Saved source review escaped its immutable output directory.")
    _check_pins(saved)
    report_path, review_path = folder / "report.json", folder / "review.json"
    report = read_json(report_path)
    decision, blockers = _result(report["model_result"], raw, fingerprint, prior)
    if (report.get("status") != "reviewed" or report.get("fingerprint") != fingerprint
            or report.get("prior_findings") != prior or report.get("decision") != decision
            or report.get("blockers") != blockers or not isinstance(report.get("model_metadata"), dict)):
        raise ValueError("Saved report and model decision disagree.")
    expected, sidecar = _artifacts(report, report_path, raw, prior)
    if sidecar is not None:
        if read_json(folder / "annotations.json") != sidecar:
            raise ValueError("Saved annotations differ from reviewed findings.")
        expected["annotations"] = {"path": "annotations.json", "sha256": sha256_file(folder / "annotations.json")}
    if read_json(review_path) != expected or (sidecar is not None) != ("annotations.json" in files):
        raise ValueError("Saved formal review differs from the model-backed review.")
    accepted = quality_gate(raw_path, review_path=review_path)["ready_for_extraction"]
    if accepted != (decision == "accept"):
        raise ValueError("Saved quality gate and review decision disagree.")
    _check_pins({**pins, **saved})
    return {"review_path": str(review_path), "report_path": str(report_path),
            "accepted": accepted, "model_metadata": report["model_metadata"]}


def review_source(raw_path, draft_path, output_dir, backend, prior_findings=None):
    """Review one private episode using injected complete(prompt, context, schema).

    Returns review_path (str or None), report_path (str), accepted (bool), and
    model_metadata (dict). Backend returns (structured_result, metadata).
    prior_findings is None or a list of ISSUE-shaped exact raw-anchored records;
    every prior note is retained and every prior critical finding blocks accept.
    Drafts need not be accepted, but must reproduce prepare() exactly. Accepted
    sources still need a NEW prepare(raw, normalized, review_path) for extraction.
    Complete outputs are revalidated/reused; incomplete or changed outputs fail
    without inference. Failures after inference retain result/usage in report.json
    and return accepted=False. No retry, audio access, corpus scan or publication.
    """
    raw_path, draft_path, folder = map(private_path, (raw_path, draft_path, output_dir))
    pins = {p: sha256_file(p) for p in (raw_path, draft_path, TERMINOLOGY, PROMPT,
                                       Path(__file__), Path(__file__).with_name("prepare.py"))}
    raw, draft, validation = _source(raw_path, draft_path, pins)
    prior = copy.deepcopy([] if prior_findings is None else prior_findings)
    _issues(prior, raw)
    # Column tables avoid repeating field names thousands of times; no source
    # text, precision, IDs, flags or episode regions are truncated to fit a budget.
    columns = ("merged_id", "start", "end", "normalized_text_zh_tw", "source_segment_ids", "terminology_annotations")
    context = {
        "episode_id": validation["episode_id"], "transcript_sha256": pins[raw_path], "draft_sha256": pins[draft_path],
        "scope": SCOPE, "metadata": raw["metadata"], "normalizer_metadata": draft["metadata"],
        "structural_warnings": validation["warnings"], "asr_flag_thresholds": REVIEW_THRESHOLDS,
        "merged_columns": list(columns), "merged_segments": [[r[k] for k in columns] for r in draft["segments"]],
        "raw_columns": ["segment_id", "start", "end", "raw_text", *METRICS, "asr_flags"],
        "raw_segments": [[s["segment_id"], s["start"], s["end"], s["text"],
                          *(s.get(k) for k in METRICS), review_signals(s)] for s in raw["segments"]],
        "required_coverage": _coverage(raw), "prior_findings": prior,
    }
    fingerprint = {"transcript_sha256": pins[raw_path], "draft_sha256": pins[draft_path],
                   "terminology_sha256": pins[TERMINOLOGY], "prompt_sha256": pins[PROMPT],
                   "implementation_sha256": pins[Path(__file__)],
                   "normalizer_sha256": pins[Path(__file__).with_name("prepare.py")],
                   "schema_sha256": _digest(SCHEMA), "context_sha256": _digest(context)}
    prompt = PROMPT.read_text(encoding="utf-8")
    _check_pins(pins)
    if folder.exists():
        return _reuse(folder, raw_path, raw, prior, fingerprint, pins)
    folder.mkdir(parents=True, exist_ok=False)
    report_path, review_path = folder / "report.json", folder / "review.json"
    response, metadata, failure = None, {}, None
    try:
        response, metadata = backend.complete(prompt, copy.deepcopy(context), copy.deepcopy(SCHEMA))
    except Exception as exc:
        response = getattr(exc, "response", getattr(exc, "doc", None))
        metadata = getattr(exc, "metadata", {})
        failure = {"stage": "backend", "error_type": type(exc).__name__,
                   "message": "Review backend failed; inspect safe model metadata. No automatic retry."}
    decision, blockers = "uncertain", []
    try:
        _check_pins(pins)
        if failure is None:
            if not isinstance(metadata, dict):
                raise ValueError("Backend metadata must be a dictionary.")
            if isinstance(response, str):
                response = json.loads(response)
            decision, blockers = _result(response, raw, fingerprint, prior)
    except Exception as exc:
        failure = {"stage": "validation", "error_type": type(exc).__name__,
                   "message": "Source pins or structured review failed validation; no acceptance granted.",
                   "backend_error": failure}
    # Invalid JSON numbers remain visible as diagnostic tags, rather than
    # preventing preservation of an otherwise structured rejected response.
    retained = json.loads(json.dumps({"model_result": response, "model_metadata": metadata}, default=repr),
                          parse_constant=lambda value: {"invalid_json_number": value})
    report = {"schema_version": "source_review_report.v1", "created_at": utc_now(), "scope": SCOPE,
              "fingerprint": fingerprint, "prior_findings": prior, **retained,
              "status": "failed" if failure else "reviewed", "decision": decision,
              "blockers": blockers, "error": failure}
    atomic_write_json(report_path, report, overwrite=False)
    if failure:
        return {"review_path": None, "report_path": str(report_path), "accepted": False,
                "model_metadata": retained["model_metadata"] if isinstance(metadata, dict) else {}}
    review, sidecar = _artifacts(report, report_path, raw, prior)
    files = {"report.json": sha256_file(report_path)}
    if sidecar is not None:
        path = folder / "annotations.json"
        atomic_write_json(path, sidecar, overwrite=False)
        files[path.name] = sha256_file(path)
        review["annotations"] = {"path": path.name, "sha256": files[path.name]}
        load_annotations(review, review_path, raw, pins[raw_path])
    _check_pins(pins)
    atomic_write_json(review_path, review, overwrite=False)
    files[review_path.name] = sha256_file(review_path)
    accepted = quality_gate(raw_path, review_path=review_path)["ready_for_extraction"]
    if accepted != (decision == "accept"):
        raise ValueError("Formal quality gate disagrees with source review; output remains incomplete.")
    _check_pins(pins)
    atomic_write_json(folder / "complete.json", {"schema_version": "source_review_completion.v1",
                                               "fingerprint": fingerprint, "files": files}, overwrite=False)
    return {"review_path": str(review_path), "report_path": str(report_path),
            "accepted": accepted, "model_metadata": retained["model_metadata"]}
