"""Synthetic lossless serialization and explicit immutable evidence reuse tests."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import pipeline, serialization
from speaker_profile.schemas import PROMPTS
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_pipeline import SyntheticModel, cfg, mapped, root, synthesis_proposal


class CompactModel(SyntheticModel):
    def complete(self, prompt, context, output_schema):
        if context.get("context_format") == serialization.FORMAT:
            context = serialization.expand(context)
        return super().complete(prompt, context, output_schema)


def recovery_config(cfg):
    parent = root(cfg)
    result = copy.deepcopy(cfg)
    result.update(run_id="synthetic_revision", context_format=serialization.FORMAT, max_calls=1,
                  reuse_evidence={
                      "manifest": {"path": str(parent / "run_manifest.json"),
                                   "sha256": sha256_file(parent / "run_manifest.json")},
                      "artifacts": {e["episode_id"]: {"path": str(parent / e["episode_id"] / "evidence.json"),
                                                    "sha256": sha256_file(parent / e["episode_id"] / "evidence.json")}
                                    for e in cfg["episodes"]}})
    return result


@pytest.fixture
def ready(cfg):
    pipeline.run(cfg, through="evidence", backend=SyntheticModel())
    return cfg, recovery_config(cfg)


def test_compact_round_trip_preserves_every_field_and_source_note(mapped):
    source, content, _ = mapped
    raw = source["raw_segments"][0]
    source["annotations_sha256"] = "b" * 64
    source["reviewed_annotations"] = {"sha256": "b" * 64, "review": {"method": "Synthetic source review"},
        "annotations": [{"annotation_id": "synthetic:I1", "kind": "uncertainty", "severity": "critical",
                         "note": "Synthetic unresolved meaning", "anchors": [
                             {"segment_id": 0, "raw_text": raw["text"], "start": raw["start"], "end": raw["end"]}]}]}
    content["items"][0].update(conditions=["Only if time permits"], exceptions=["Unless circumstances change"],
                               uncertainty=["\u4e0d\u78ba\u5b9a"], outcome=None)
    artifact = {"source": pipeline._meta(source), "content": content,
                "grounding": pipeline._derive_evidence(content, source)}
    context = pipeline._context("synthesis", None, [artifact])
    original = copy.deepcopy(context)
    compact = serialization.compact(context)
    assert serialization.expand(compact) == original == context
    assert len(compact["evidence"]["rows"]) == len(content["items"])
    assert len(compact["quote_table"]["rows"]) == 2
    assert compact["sources"][0]["source"]["reviewed_annotations"]["review"] == source["reviewed_annotations"]["review"]
    expanded = serialization.expand(compact)
    assert expanded["evidence"][0]["grounding"]["source_issue_ids"] == ["synthetic:I1"]
    assert expanded["sources"][0]["unselected_regions"] == context["sources"][0]["unselected_regions"]
    assert len(json.dumps(compact)) < len(json.dumps(context))


def test_shared_columns_preserve_missing_null_and_repeated_values():
    rows = [{"a": ["Repeated synthetic note" * 30], "b": None}, {"a": ["Repeated synthetic note" * 30]},
            {"a": ["Repeated synthetic note" * 30], "b": False}]
    table = serialization._table(rows)
    assert "a" in table["shared_columns"]
    assert serialization._records(table) == rows
    assert serialization._records(serialization._table([])) == []


@pytest.mark.parametrize("fault", ["quote_conflict", "quote_extra", "range_extra", "timestamp_extra", "wrong_pin"])
def test_compact_rejects_inconsistent_anchors_without_dropping_fields(mapped, fault):
    _, _, artifact = mapped
    context = pipeline._context("synthesis", None, [artifact])
    item = context["evidence"][0]
    if fault == "quote_conflict": context["evidence"][2]["quotes"][0]["text"] += " Changed"
    elif fault == "quote_extra": item["quotes"][0]["unrecognized"] = "Must not disappear"
    elif fault == "range_extra": item["grounding"]["source_segment_id_ranges"] = [
        {"start_id": 0, "end_id": 0, "unrecognized": True}]
    elif fault == "timestamp_extra": item["grounding"]["timestamp_regions"][0]["unrecognized"] = True
    else: item["grounding"]["source_raw_sha256"] = "c" * 64
    with pytest.raises(ValueError): serialization.compact(context)


def test_recovery_uses_original_provenance_under_explicit_new_implementation(cfg, monkeypatch):
    implementation = pipeline._implementation()
    historical = copy.deepcopy(implementation)
    historical["code"]["profile_pipeline"] = "f" * 64
    historical["code"].pop("profile_serialization")
    monkeypatch.setattr(pipeline, "_implementation", lambda: historical)
    pipeline.run(cfg, through="evidence", backend=SyntheticModel())
    original_files = {p: p.read_bytes() for p in root(cfg).rglob("*") if p.is_file()}
    original_sources = {Path(e[k]): Path(e[k]).read_bytes() for e in cfg["episodes"] for k in ("raw", "draft")}
    recovery = recovery_config(cfg)
    monkeypatch.setattr(pipeline, "_implementation", lambda: implementation)
    with pytest.raises(ValueError, match="implementation changed"):
        pipeline.run(cfg, through="evidence", dry_run=True)
    forbidden = Mock(side_effect=AssertionError("No live model"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    plan = pipeline.run(recovery, dry_run=True)
    assert plan["estimated_new_calls"] == 1 and plan["model_calls"] == 0
    assert [r["action"] for r in plan["artifacts"]] == ["referenced"] * 4 + ["planned"]
    assert plan["artifacts"][-1]["input_fits"] is True
    assert not root(recovery).exists()
    assert pipeline.run(recovery, through="evidence", validate_only=True)["model_calls"] == 0
    assert not root(recovery).exists()
    model = CompactModel()
    result = pipeline.run(recovery, backend=model)
    assert model.calls == ["synthesis"] and result["model_calls"] == 1
    assert not list(root(recovery).glob("*/evidence.json"))
    request = read_json(root(recovery) / "calls/cross_episode_synthesis_1.request.json")
    assert pipeline._input_chars(request["prompt"], request["context"]) == plan["artifacts"][-1]["input_chars"]
    artifact = read_json(root(recovery) / "synthesis.json")
    assert artifact["reused_evidence"] == recovery["reuse_evidence"]
    assert artifact["policy"] == pipeline.POLICY
    assert read_json(root(recovery) / "run_manifest.json")["implementation"] == implementation
    assert pipeline.run(recovery, validate_only=True)["model_calls"] == 0
    assert pipeline.run(recovery)["model_calls"] == 0
    assert {p: p.read_bytes() for p in original_files} == original_files
    assert {p: p.read_bytes() for p in original_sources} == original_sources
    forbidden.assert_not_called()


@pytest.mark.parametrize("fault", ["artifact_pin", "manifest_pin", "artifact_bytes", "manifest_bytes", "call_request",
    "call_result", "missing_ref", "foreign_ref", "public_ref", "same_run", "nested_run", "source_pin", "source_bytes",
    "changed_selection", "changed_reserved", "forged_grounding", "forged_content", "forged_fingerprint"])
def test_recovery_rejects_bad_bindings_before_backend(ready, fault):
    cfg, recovery = ready
    refs = recovery["reuse_evidence"]
    ref = refs["artifacts"]["SYN1"]
    if fault == "artifact_pin": ref["sha256"] = "0" * 64
    elif fault == "manifest_pin": refs["manifest"]["sha256"] = "0" * 64
    elif fault in {"artifact_bytes", "manifest_bytes", "call_request", "call_result"}:
        path = (Path(ref["path"]) if fault == "artifact_bytes" else Path(refs["manifest"]["path"])
                if fault == "manifest_bytes" else root(cfg) / ("calls/SYN1_evidence_1.request.json"
                                                             if fault == "call_request" else "calls/SYN1_evidence_1.result.json"))
        value = read_json(path); value["tampered"] = True; atomic_write_json(path, value)
    elif fault == "missing_ref": refs["artifacts"].pop("SYN1")
    elif fault == "foreign_ref": ref["path"] = refs["artifacts"]["SYN2"]["path"]
    elif fault == "public_ref": ref["path"] = str(Path(pipeline.__file__).resolve())
    elif fault == "same_run": recovery["run_id"] = cfg["run_id"]
    elif fault == "nested_run": recovery["output_root"] = str(root(cfg))
    elif fault == "source_pin": recovery["episodes"][0]["raw_sha256"] = "0" * 64
    elif fault == "source_bytes":
        path = Path(cfg["episodes"][0]["raw"])
        value = read_json(path); value["tampered"] = True; atomic_write_json(path, value)
    elif fault == "changed_selection":
        recovery["episodes"].pop(); refs["artifacts"].pop("SYN4")
    elif fault == "changed_reserved": recovery["reserved_episode_ids"] = []
    else:
        path = Path(ref["path"]); value = read_json(path)
        if fault == "forged_grounding": value["grounding"]["scope"] = "Incorrect certification"
        elif fault == "forged_content": value["content"]["items"][0]["source_observation"] += " Changed"
        else: value["fingerprint"] = "0" * 64
        atomic_write_json(path, value); ref["sha256"] = sha256_file(path)
    backend = Mock()
    with pytest.raises(ValueError): pipeline.run(recovery, backend=backend)
    backend.complete.assert_not_called()


def test_source_change_with_rebound_pins_still_cannot_relabel_old_evidence(ready):
    cfg, recovery = ready
    entry = recovery["episodes"][0]
    path = Path(entry["raw"])
    value = read_json(path); value["metadata"]["model"] = "changed synthetic source"
    atomic_write_json(path, value)
    from transcription.prepare import prepare
    draft = path.with_name("new-draft.json")
    prepare(path, draft)
    entry.update(raw_sha256=sha256_file(path), draft=str(draft), draft_sha256=sha256_file(draft))
    backend = Mock()
    with pytest.raises(ValueError, match="source pins"):
        pipeline.run(recovery, backend=backend)
    backend.complete.assert_not_called()


def test_old_calls_rechecked_before_publishing_synthesis(ready):
    cfg, recovery = ready
    class Mutate(CompactModel):
        def complete(self, *args):
            output = super().complete(*args)
            path = root(cfg) / "calls/SYN1_evidence_1.result.json"
            value = read_json(path); value["tampered"] = True; atomic_write_json(path, value)
            return output
    with pytest.raises(ValueError): pipeline.run(recovery, backend=Mutate())
    assert not (root(recovery) / "synthesis.json").exists()
    result = read_json(root(recovery) / "calls/cross_episode_synthesis_1.result.json")
    assert result["error"]["phase"] == "provenance"


def test_input_failure_does_not_consume_repair_retry_or_repeat_on_resume(ready):
    _, recovery = ready
    recovery.update(max_input_chars=1, retry_count=1, max_calls=2)
    backend = Mock()
    plan = pipeline.run(recovery, dry_run=True)
    assert plan["artifacts"][-1]["input_fits"] is False
    assert not root(recovery).exists()
    for _ in range(2):
        with pytest.raises(ValueError, match="input"):
            pipeline.run(recovery, backend=backend)
    requests = list((root(recovery) / "calls").glob("*.request.json"))
    assert len(requests) == 1
    result = read_json(root(recovery) / "calls/cross_episode_synthesis_1.result.json")
    assert result["usage"] is result["response"] is None
    assert result["error"]["phase"] == "input"
    backend.complete.assert_not_called()


def test_character_measurement_matches_backend_transport_without_inference(tmp_path, monkeypatch):
    captured = []
    class Process:
        returncode = 1
        def communicate(self, message, timeout):
            captured.append(message)
            return "", ""
    monkeypatch.setattr(pipeline.codex_backend.subprocess, "Popen", lambda *a, **k: Process())
    backend = pipeline.CodexBackend("synthetic", tmp_path / "transport")
    context = {"items": ["\u4e0d\u78ba\u5b9a", "Synthetic note"], "nullable": None}
    with pytest.raises(Exception):
        backend.complete(PROMPTS["synthesis"], context, pipeline.schema("synthesis"))
    assert len(captured) == 1
    assert len(captured[0]) == pipeline._input_chars(PROMPTS["synthesis"], context)


class BadLinkModel(CompactModel):
    def complete(self, *args):
        content, usage = super().complete(*args)
        if self.calls[-1] == "synthesis":
            content = synthesis_proposal(self.contexts[-1]["evidence"], ["SYN1:E1", "SYN1:E2"])
            content["summary"] = f"Synthetic failed synthesis call {len(self.calls)}"
            content["hypotheses"][0]["statement_action_relationships"] = [
                {"statement_id": "SYN1:E2", "action_id": "SYN1:E2", "relationship": "agreement", "reason": "Synthetic"}]
        return content, usage


def failed_refs(cfg, attempt=1):
    parent = root(cfg)
    return {key: {"path": str(path), "sha256": sha256_file(path)} for key, path in {
        "manifest": parent / "run_manifest.json",
        "request": parent / f"calls/cross_episode_synthesis_{attempt}.request.json",
        "result": parent / f"calls/cross_episode_synthesis_{attempt}.result.json"}.items()}


@pytest.fixture
def failed_synthesis(ready):
    original, failed = ready
    base = pipeline.run(failed, dry_run=True)["artifacts"][-1]["input_chars"]
    failed.update(max_input_chars=base, retry_count=1, max_calls=2)
    model = BadLinkModel()
    with pytest.raises(ValueError, match="input exceeds"):
        pipeline.run(failed, backend=model)
    assert model.calls == ["synthesis"]
    assert read_json(root(failed) / "calls/cross_episode_synthesis_2.result.json")["error"]["phase"] == "input"
    # Simulate the less-specific diagnostic retained by the historical implementation.
    path = root(failed) / "calls/cross_episode_synthesis_1.result.json"
    result = read_json(path)
    result["error"]["message"] = "Statement/action relationship kinds disagree with evidence."
    atomic_write_json(path, result)
    recovery = copy.deepcopy(failed)
    recovery.update(run_id="synthetic_repair", max_input_chars=100000, retry_count=0, max_calls=1,
                    reuse_failed_synthesis=failed_refs(failed))
    return original, failed, recovery


def test_failed_response_recovery_seeds_first_call_preserves_originals_and_resumes(failed_synthesis, monkeypatch):
    original, failed, recovery = failed_synthesis
    originals = {p: p.read_bytes() for cfg in (original, failed) for p in root(cfg).rglob("*") if p.is_file()}
    forbidden = Mock(side_effect=AssertionError("No provider access"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    plan = pipeline.run(recovery, dry_run=True)
    stage = plan["artifacts"][-1]
    assert plan["estimated_new_calls"] == 1 and stage["seeded_validation_repair"]
    assert stage["input_chars"] == stage["base_input_chars"] + stage["repair_input_chars"]
    assert stage["repair_input_chars"] > 0 and not root(recovery).exists()
    recovery["max_input_chars"] = stage["input_chars"]  # Exact boundary, with no truncation.
    model = CompactModel()
    assert pipeline.run(recovery, backend=model)["model_calls"] == 1
    assert model.calls == ["synthesis"]
    request_path = root(recovery) / "calls/cross_episode_synthesis_1.request.json"
    request = read_json(request_path)
    assert pipeline._input_chars(request["prompt"], request["context"]) == stage["input_chars"]
    repair = request["context"]["validation_repair"]
    old_result = read_json(Path(recovery["reuse_failed_synthesis"]["result"]["path"]))
    assert repair["previous_response"] == old_result["response"]
    assert repair["error"] == old_result["error"]
    assert "PROFILE:H1[0] statement=SYN1:E2:reported_action" in repair["revalidated_error"]["message"]
    artifact = root(recovery) / "synthesis.json"
    assert read_json(artifact)["reused_failed_synthesis"] == recovery["reuse_failed_synthesis"]
    assert not list(root(recovery).glob("*/evidence.json"))
    assert pipeline.run(recovery, validate_only=True)["model_calls"] == 0
    assert pipeline.run(recovery)["model_calls"] == 0
    artifact.unlink()  # Simulate interruption after persisting the successful result.
    assert pipeline.run(recovery)["model_calls"] == 0
    assert read_json(artifact)["reused_failed_synthesis"] == recovery["reuse_failed_synthesis"]
    assert {p: p.read_bytes() for p in originals} == originals
    forbidden.assert_not_called()


def test_failed_response_from_original_evidence_run_is_supported(cfg):
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(cfg, backend=BadLinkModel())
    recovery = recovery_config(cfg)
    recovery["reuse_failed_synthesis"] = failed_refs(cfg)
    model = CompactModel()
    assert pipeline.run(recovery, backend=model)["model_calls"] == 1
    assert model.calls == ["synthesis"] and "validation_repair" in model.contexts[0]


def test_repair_overflow_is_measured_without_truncation_or_model_calls(failed_synthesis):
    _, _, recovery = failed_synthesis
    size = pipeline.run(recovery, dry_run=True)["artifacts"][-1]["input_chars"]
    recovery.update(max_input_chars=size - 1, retry_count=1, max_calls=2)
    assert pipeline.run(recovery, dry_run=True)["artifacts"][-1]["input_fits"] is False
    model = CompactModel()
    for _ in range(2):
        with pytest.raises(ValueError, match="input"):
            pipeline.run(recovery, backend=model)
    assert model.calls == []
    assert len(list((root(recovery) / "calls").glob("*.request.json"))) == 1
    request = read_json(root(recovery) / "calls/cross_episode_synthesis_1.request.json")
    assert pipeline._input_chars(request["prompt"], request["context"]) == size
    result = read_json(root(recovery) / "calls/cross_episode_synthesis_1.result.json")
    assert f"input_chars={size}, max_input_chars={size - 1}" in result["error"]["message"]


@pytest.mark.parametrize("retry_count,max_calls,expected", [(0, 1, 1), (1, 1, 1), (1, 2, 2)])
def test_seeded_repair_attempts_and_budget_remain_bounded_on_resume(failed_synthesis, retry_count, max_calls, expected):
    _, _, recovery = failed_synthesis
    recovery.update(retry_count=retry_count, max_calls=max_calls)
    model = BadLinkModel()
    for _ in range(2):
        with pytest.raises(ValueError, match="attempts exhausted|budget exhausted"):
            pipeline.run(recovery, backend=model)
    assert model.calls == ["synthesis"] * expected
    if expected == 2:
        first = read_json(root(recovery) / "calls/cross_episode_synthesis_1.result.json")
        second = read_json(root(recovery) / "calls/cross_episode_synthesis_2.request.json")
        assert second["context"]["validation_repair"]["previous_response"] == first["response"]


@pytest.mark.parametrize("fault", ["manifest_pin", "request_pin", "result_pin", "manifest_bytes", "request_bytes",
    "result_bytes", "missing_ref", "extra_ref", "public_ref", "same_run", "nested_run", "missing_evidence",
    "source_context", "request_fingerprint", "result_fingerprint", "request_hash", "status", "phase", "no_response",
    "valid_response", "config_digest", "source_selection", "evidence_lineage", "implementation", "attempt", "stage",
    "request_location", "result_location", "input_failure"])
def test_failed_response_reuse_rejects_tamper_and_provenance_before_backend(failed_synthesis, fault):
    _, failed, recovery = failed_synthesis
    refs = recovery["reuse_failed_synthesis"]
    if fault.endswith("_pin"):
        refs[fault.removesuffix("_pin")]["sha256"] = "0" * 64
    elif fault.endswith("_bytes"):
        path = Path(refs[fault.removesuffix("_bytes")]["path"])
        value = read_json(path); value["tampered"] = True; atomic_write_json(path, value)
    elif fault == "missing_ref": refs.pop("result")
    elif fault == "extra_ref": refs["extra"] = refs["request"]
    elif fault == "public_ref": refs["request"]["path"] = str(Path(pipeline.__file__).resolve())
    elif fault == "same_run": recovery["run_id"] = failed["run_id"]
    elif fault == "nested_run": recovery["output_root"] = str(root(failed))
    elif fault == "missing_evidence": recovery.pop("reuse_evidence")
    elif fault == "input_failure": recovery["reuse_failed_synthesis"] = failed_refs(failed, attempt=2)
    elif fault.endswith("_location"):
        ref = refs[fault.removesuffix("_location")]
        path = Path(ref["path"]).with_name("unrelated.json")
        atomic_write_json(path, read_json(Path(ref["path"])))
        ref.update(path=str(path), sha256=sha256_file(path))
    else:
        parent, request, result = [read_json(Path(refs[k]["path"])) for k in ("manifest", "request", "result")]
        if fault == "source_context": request["context"]["sources"][0]["source"]["raw_sha256"] = "0" * 64
        elif fault == "request_fingerprint": request["fingerprint"] = "0" * 64
        elif fault == "result_fingerprint": result["fingerprint"] = "0" * 64
        elif fault == "status": result["status"] = "completed"
        elif fault == "phase": result["error"]["phase"] = "model"
        elif fault == "no_response": result["response"] = None
        elif fault == "valid_response": result["response"]["hypotheses"][0]["statement_action_relationships"] = []
        elif fault == "config_digest": parent["config_digest"] = "0" * 64
        elif fault == "source_selection":
            parent["config"]["reserved_episode_ids"] = []
            parent["config_digest"] = pipeline.digest(parent["config"])
        elif fault == "evidence_lineage":
            parent["config"]["reuse_evidence"]["artifacts"]["SYN1"]["sha256"] = "0" * 64
            parent["config_digest"] = pipeline.digest(parent["config"])
        elif fault == "implementation": parent["implementation"] = {}
        elif fault == "attempt": request["attempt"] = 99
        elif fault == "stage": request["stage"] = "evidence"
        for key, value in (("manifest", parent), ("request", request), ("result", result)):
            if key == "result":
                value["request_sha256"] = "0" * 64 if fault == "request_hash" else refs["request"]["sha256"]
            atomic_write_json(Path(refs[key]["path"]), value)
            refs[key]["sha256"] = sha256_file(Path(refs[key]["path"]))
    backend = Mock()
    with pytest.raises(ValueError): pipeline.run(recovery, backend=backend)
    backend.complete.assert_not_called()
    if fault not in {"same_run"}:
        assert not root(recovery).exists()


@pytest.mark.parametrize("key", ["manifest", "request", "result"])
def test_failed_response_pins_rechecked_before_publishing(failed_synthesis, key):
    _, _, recovery = failed_synthesis
    class Mutate(CompactModel):
        def complete(self, *args):
            output = super().complete(*args)
            path = Path(recovery["reuse_failed_synthesis"][key]["path"])
            value = read_json(path); value["tampered"] = True; atomic_write_json(path, value)
            return output
    with pytest.raises(ValueError): pipeline.run(recovery, backend=Mutate())
    assert not (root(recovery) / "synthesis.json").exists()
    result = read_json(root(recovery) / "calls/cross_episode_synthesis_1.result.json")
    assert result["error"]["phase"] == "provenance"
