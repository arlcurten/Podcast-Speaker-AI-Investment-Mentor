"""Synthetic transport checks; no provider, subprocess, or source-file writes."""
import copy
import inspect
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge import transport
from knowledge.codex_backend import CodexBackend, ModelCallError
from knowledge.provenance import digest
from knowledge.schemas import schema
from transcription.common import sha256_file


@pytest.fixture(autouse=True)
def no_process(monkeypatch):
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", Mock(side_effect=AssertionError("No API calls")))


@pytest.fixture
def context():
    note = {"annotation_id": "SYN:I1", "kind": "uncertainty", "severity": "critical",
            "note": "Synthetic historical review: retain every condition. " * 30,
            "anchors": [{"segment_id": 4, "start": 1.0, "end": 2.0,
                         "raw_text": "Synthetic raw quote; do not correct.", "future_anchor_field": None}],
            "future_note_field": {"keep": [False, None, "\u4e0d\u78ba\u5b9a"]}}
    issue = {"issue_id": "SYN:I1", "merged_ids": [2, 7], "note": note["note"],
             "authority": "reviewer_commentary_not_source", "future_issue_field": 1}
    evidence = {"episode_id": "SYN", "source_raw_sha256": "a" * 64,
                "source_normalized_sha256": "b" * 64, "merged_ids": [2, 7],
                "source_segment_id_ranges": [{"start_id": 4, "end_id": 8}],
                "timestamp_regions": [{"start": 1.0, "end": 4.0, "merged_ids": [2]},
                                      {"start": 9.0, "end": 12.0, "merged_ids": [7]}],
                "reviewed_annotations": {"sha256": "c" * 64, "review": {"decision": "uncertain"},
                                         "annotations": [note], "future_metadata_field": [None, False]},
                "candidate_source_assessment": {
                    "status": "unresolved", "eligible_for_online_reasoning": False,
                    "source_issues": [issue], "supplementary_notes": ["Synthetic analyst commentary"],
                    "source_issue_dispositions": [{"issue_id": "SYN:I1", "disposition": "retained_uncertainty"}]},
                "future_evidence_field": {"keep": True}}
    cases = [{"record_id": f"SYN:R{i}", "episode_id": "SYN", "claims": ["Synthetic conditional claim"],
              "conditions": ["Only under the stated condition"], "exceptions": ["An explicit exception"],
              "uncertainty": ["Unresolved meaning"], "decision_context": {"event_time": None},
              "evidence": copy.deepcopy(evidence), "future_case_field": [False, None]} for i in range(3)]
    return {"episode": {"episode_id": "SYN", "source_acceptance": "not_granted"}, "cases": cases,
            "future_context_field": {"retain": "Synthetic extra context"},
            "validation_repair": {"previous_proposal": {"principles": []}, "validation_error": "Synthetic error"}}


def test_lossless_round_trip_keeps_cases_anchors_issues_and_unknown_fields(context):
    before = copy.deepcopy(context)
    compact = transport.compact_level3_context(context)
    assert transport.expand_level3_context(compact) == before == context
    assert digest(transport.expand_level3_context(compact)) == digest(context)
    assert len(compact["cases"]) == 3
    assert len(compact["annotation_catalog"]) == len(compact["source_issue_catalog"]) == 1
    assert compact["annotation_catalog"]["SYN/SYN:I1"] == context["cases"][0]["evidence"]["reviewed_annotations"]["annotations"][0]
    assert len(json.dumps(compact)) < len(json.dumps(context))
    compact["annotation_catalog"]["SYN/SYN:I1"]["anchors"][0]["raw_text"] = "Changed copy"
    assert context == before


@pytest.mark.parametrize("cases", [[], [{"episode_id": "SYN", "evidence": {"episode_id": "SYN"}}]])
def test_empty_and_unannotated_context(cases):
    context = {"episode": {"episode_id": "SYN"}, "cases": cases}
    assert transport.expand_level3_context(transport.compact_level3_context(context)) == context


@pytest.mark.parametrize("mode", ["empty", "null", "missing"])
def test_missing_null_and_empty_annotations_and_issues_remain_distinct(context, mode):
    for case in context["cases"]:
        ev = case["evidence"]
        if mode == "empty":
            ev["reviewed_annotations"]["annotations"] = []
            ev["candidate_source_assessment"]["source_issues"] = []
        elif mode == "null":
            ev["reviewed_annotations"] = None
            ev["candidate_source_assessment"]["source_issues"] = None
        else:
            del ev["reviewed_annotations"]
            del ev["candidate_source_assessment"]["source_issues"]
    assert transport.expand_level3_context(transport.compact_level3_context(context)) == context


def test_reference_order_and_multiplicity_are_preserved(context):
    notes = context["cases"][0]["evidence"]["reviewed_annotations"]["annotations"]
    other = {**copy.deepcopy(notes[0]), "annotation_id": "SYN:I2"}
    notes[:] = [other, notes[0], other]
    compact = transport.compact_level3_context(context)
    assert compact["cases"][0]["evidence"]["reviewed_annotations"]["annotation_refs"] == [
        "SYN/SYN:I2", "SYN/SYN:I1", "SYN/SYN:I2"]
    assert transport.expand_level3_context(compact) == context


@pytest.mark.parametrize("fault", ["note", "anchor", "metadata", "issue", "typed_value", "episode"])
def test_conflicting_references_fail_closed(context, fault):
    ev = context["cases"][1]["evidence"]
    if fault == "note": ev["reviewed_annotations"]["annotations"][0]["note"] = "Different"
    elif fault == "anchor": ev["reviewed_annotations"]["annotations"][0]["anchors"][0]["raw_text"] = "Different"
    elif fault == "metadata": ev["reviewed_annotations"]["sha256"] = "d" * 64
    elif fault == "issue": ev["candidate_source_assessment"]["source_issues"][0]["note"] = "Different"
    elif fault == "typed_value": ev["candidate_source_assessment"]["source_issues"][0]["future_issue_field"] = True
    else: ev["episode_id"] = "OTHER"
    with pytest.raises(ValueError): transport.compact_level3_context(context)


@pytest.mark.parametrize("field", sorted(transport.RESERVED))
def test_reserved_fields_never_overwrite_existing_context(context, field):
    context[field] = {"existing": "Must remain untouched"}
    before = copy.deepcopy(context)
    with pytest.raises(ValueError, match="reserved"):
        transport.compact_level3_context(context)
    assert context == before


@pytest.mark.parametrize("ref", ["OTHER/SYN:I1", "SYN/missing"])
def test_expansion_rejects_cross_episode_or_missing_references(context, ref):
    compact = transport.compact_level3_context(context)
    compact["cases"][0]["evidence"]["reviewed_annotations"]["annotation_refs"] = [ref]
    with pytest.raises(ValueError, match="reference"):
        transport.expand_level3_context(compact)


@pytest.fixture
def backend(monkeypatch):
    delegate = Mock()
    factory = Mock(return_value=delegate)
    monkeypatch.setattr(transport, "CodexBackend", factory)
    wrapper = transport.L3ReferenceBackend("synthetic", Path("synthetic-work-root"), timeout_seconds=123,
                                          reasoning_effort="xhigh", max_input_chars=500000)
    return wrapper, delegate, factory


def test_constructor_signature_and_options_match_codex_backend(backend):
    assert inspect.signature(transport.L3ReferenceBackend) == inspect.signature(CodexBackend)
    backend[2].assert_called_once_with("synthetic", Path("synthetic-work-root"), timeout_seconds=123,
                                       reasoning_effort="xhigh", max_input_chars=500000)


def test_wrapper_records_actual_transport_without_relabeling_usage(context, backend):
    wrapper, delegate, _ = backend
    response = {"synthetic_response": True}
    retained = {}
    def complete(prompt, sent, output_schema):
        retained.update(provider="synthetic", model="synthetic", reasoning_effort="xhigh",
                        usage={"input_tokens": 11, "output_tokens": 3}, latency_seconds=1.25, cost_usd=None,
                        input_characters=len(prompt) + len(json.dumps(sent)), context_characters=len(json.dumps(sent)))
        return response, retained
    delegate.complete.side_effect = complete
    output_schema = schema("level3", candidate=True)
    before = copy.deepcopy(context)
    result, metadata = wrapper.complete("Unchanged synthetic prompt", context, output_schema)
    prompt, sent, sent_schema = delegate.complete.call_args.args
    assert prompt == "Unchanged synthetic prompt" and sent_schema is output_schema
    assert transport.expand_level3_context(sent) == before == context
    assert result is response and "transport" not in retained
    assert {k: v for k, v in metadata.items() if k != "transport"} == retained
    assert metadata["transport"] == {
        "format": transport.FORMAT, "original_context_sha256": digest(context),
        "transport_context_sha256": digest(sent), "code_sha256": sha256_file(Path(transport.__file__))}
    assert digest(context) != digest(sent)


@pytest.mark.parametrize("stage", ["level1", "level2", "level4"])
def test_non_l3_calls_are_exact_passthrough(context, backend, stage):
    wrapper, delegate, _ = backend
    expected = ({"unchanged": True}, {"usage": None})
    delegate.complete.return_value = expected
    output_schema = schema(stage, candidate=True)
    assert wrapper.complete("Same prompt", context, output_schema) is expected
    args = delegate.complete.call_args.args
    assert args[1] is context and args[2] is output_schema


def test_schema_strategy_without_episode_is_not_l3(backend):
    wrapper, delegate, _ = backend
    context = {"other": "Synthetic non-L3 data"}
    wrapper.complete("Same prompt", context, schema("level3"))
    assert delegate.complete.call_args.args[1] is context


def test_validation_repair_is_preserved_across_calls(context, backend):
    wrapper, delegate, _ = backend
    delegate.complete.return_value = ({}, {"usage": None})
    output_schema = schema("level3", candidate=True)
    for attempt in range(2):
        context["validation_repair"] = {"previous_proposal": {"principles": [{"synthetic": attempt}]},
                                        "validation_error": "Synthetic validator feedback"}
        wrapper.complete("Same prompt", context, output_schema)
        sent = delegate.complete.call_args.args[1]
        assert transport.expand_level3_context(sent) == context


def test_failed_model_call_preserves_metadata_and_adds_transport(context, backend):
    wrapper, delegate, _ = backend
    original = {"provider": "synthetic", "usage": None, "latency_seconds": 2.0}
    error = ModelCallError("Synthetic provider failure", original)
    delegate.complete.side_effect = error
    with pytest.raises(ModelCallError) as caught:
        wrapper.complete("Prompt", context, schema("level3"))
    assert caught.value is error and "transport" not in original
    assert error.metadata["usage"] is None and error.metadata["latency_seconds"] == 2.0
    assert error.metadata["transport"]["original_context_sha256"] == digest(context)


def test_conflict_prevents_backend_call(context, backend):
    context["cases"][1]["evidence"]["reviewed_annotations"]["annotations"][0]["note"] = "Conflict"
    with pytest.raises(ValueError):
        backend[0].complete("Prompt", context, schema("level3"))
    backend[1].complete.assert_not_called()


def test_backend_input_error_is_not_retried_or_truncated(context, backend):
    backend[1].complete.side_effect = ValueError("Synthetic input limit")
    with pytest.raises(ValueError, match="input limit"):
        backend[0].complete("Prompt", context, schema("level3"))
    assert backend[1].complete.call_count == 1
    assert transport.expand_level3_context(backend[1].complete.call_args.args[1]) == context
