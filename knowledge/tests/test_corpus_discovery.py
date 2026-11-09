"""Synthetic family discovery, receipt replay and bounded admission; no real model calls."""
import copy
import fcntl
import gc
import json
import weakref
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge import corpus_discovery as discovery, corpus_inventory as inventory, pipeline
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_candidate_extraction import candidate
from .test_corpus_inventory import InventoryModel, pin, selection_for
from .test_pipeline import cfg


def response_for(context, *, empty=False):
    cases = context["cases"]
    families = [] if empty else [{"family_id": "F1", "label": "Synthetic information-versus-opportunity choice",
        "decision_process": "Compare observations before committing exposure, retaining exceptions.",
        "conditions": ["Only the fictional situations supplied"], "accepted_costs": ["Possible missed opportunity"],
        "limitations": ["A routing proposal, not a recurring personality or investment principle"]},
        {"family_id": "F2", "label": "Synthetic low-frequency exception",
         "decision_process": "Keep an isolated exception visible without assuming recurrence.",
         "conditions": [], "accepted_costs": [], "limitations": ["Source-limited synthetic observation"]}]
    dispositions = []
    for index, case in enumerate(cases):
        members = [] if empty or index else [
            {"family_id": fid, "role": "potential_support" if case["speaker_attribution"] == "host" else "context_only",
             "reason": "Fictional comparison grounded in this exact case"} for fid in ("F1", "F2")]
        dispositions.append({"record_id": case["record_id"], "disposition": "assigned" if members else "retained_without_family",
                             "memberships": members, "reason": "Keep the original item even without a family"})
    return {"families": families, "case_dispositions": dispositions,
            "principle_dispositions": [{"principle_id": p["principle_id"], "disposition": "retained_without_family",
                                        "family_ids": [], "reason": "Navigation remains provisional; not extra evidence"}
                                       for p in context["principles"]]}


class FakeBackend:
    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform

    def complete(self, prompt, context, schema):
        self.calls.append(copy.deepcopy(context))
        value = response_for(context)
        if self.transform:
            value = self.transform(value, context)
        return value, {"provider": "synthetic", "usage": None, "cost_usd": None}


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Tests must not invoke a real backend"))
    monkeypatch.setattr(discovery.codex_backend, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


def make_ready(cfg, tmp_path, model=None):
    config = candidate(cfg)
    for entry in config["episodes"]:
        path = Path(entry["review"])
        review = read_json(path)
        review.update(decision="uncertain", unresolved_critical_issues=["Synthetic uncertainty retained"])
        atomic_write_json(path, review)
    if model is not None:
        pipeline.run(config, through="level3", backend=model)
    data = selection_for(config, tmp_path)
    frozen = inventory.freeze(pin(data["path"]), data["output"])
    catalog = inventory.load_checked(frozen)
    materials = discovery._materials(catalog, {})
    owners = {pid: min(p["supporting_records"]) for pid, p in catalog["principles"].items()}
    prompt = discovery.PROMPT_PATH.read_text()
    cap = max(discovery._size(prompt, discovery._context(entry["level2_record_ids"], catalog, materials, owners))
              for entry in catalog["episodes"].values()) + 1000
    cfg = {"schema_version": "knowledge_discovery_config.v1", "run_id": "synthetic_discovery",
           "output_root": str(tmp_path / "discovery_runs"), "inventory": frozen, "model": "synthetic",
           "reasoning_effort": "low", "timeout_seconds": 30, "max_input_chars": cap,
           "max_response_chars": 100000, "max_calls": 4, "retry_count": 1}
    path = tmp_path / "discovery-config.json"
    atomic_write_json(path, cfg)
    return {"config": cfg, "path": path, "root": Path(cfg["output_root"]) / cfg["run_id"],
            "catalog": catalog, "source_cfg": config, "materials": materials, "owners": owners}


@pytest.fixture
def ready(cfg, tmp_path):
    return make_ready(cfg, tmp_path)


def save(data):
    atomic_write_json(data["path"], data["config"])
    return pin(data["path"])


def packet_context(ready, packet=None):
    config, _, catalog, packets, _, pins, prompt = discovery._prepare(pin(ready["path"]))
    return discovery._materialize(packet or packets[0], catalog, pins, prompt, config["max_input_chars"])


def test_plan_read_only_complete_owned_coverage_raw_regions_and_known_counters(ready, tmp_path, monkeypatch):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    forbidden = Mock(side_effect=AssertionError("No extraction pipeline"))
    monkeypatch.setattr(pipeline, "run", forbidden)
    result = discovery.execute(pin(ready["path"]))
    assert result["packet_count"] == 2 and result["model_calls_this_execution"] == 0
    assert result["owned_cases"] == 4 and result["owned_principles"] == 4
    assert {p: p.read_bytes() for p in before} == before and not ready["root"].exists()
    _, _, _, packets, _, _, prompt = discovery._prepare(pin(ready["path"]))
    assert sorted(rid for p in packets for rid in p["owned_case_ids"]) == sorted(ready["catalog"]["cases"])
    assert sorted(pid for p in packets for pid in p["owned_principle_ids"]) == sorted(ready["catalog"]["principles"])
    for packet in packets:
        assert "context" not in packet
        ctx = packet_context(ready, packet)
        assert discovery._size(prompt, ctx) <= ready["config"]["max_input_chars"]
        assert ctx["known_counter_obligations"]
        assert any(not r["parent_eligible_for_level4"] for r in ctx["known_counter_obligations"])
        assert all(s["source"]["prior_source_decision"] == "uncertain" for s in ctx["sources"].values())
        assert all(c["evidence"]["candidate_source_assessment"]["authority"] == "model_assessment_not_source"
                   for c in ctx["cases"])
        for eid, raw in ctx["raw_regions"].items():
            originals = read_json(Path(next(e["raw"] for e in ready["source_cfg"]["episodes"] if e["episode_id"] == eid)))["segments"]
            assert all(row == originals[row["segment_id"]] for row in raw)
    forbidden.assert_not_called()


def test_run_multimembership_retains_all_items_and_resumes_without_new_calls(ready, tmp_path):
    originals = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    model = FakeBackend()
    result = discovery.execute(pin(ready["path"]), command="run", backend=model)
    assert result["status"] == "completed" and result["admitted_calls"] == 2
    artifact = read_json(ready["root"] / "discovery.json")
    assert len(artifact["families"]) == 4
    assert len({f["family_id"] for f in artifact["families"]}) == 4
    assert set(artifact["case_accounting"]) == set(ready["catalog"]["cases"])
    assert set(artifact["principle_accounting"]) == set(ready["catalog"]["principles"])
    assert any(len(c["memberships"]) == 2 for c in artifact["case_accounting"].values())
    assert any(c["disposition"] == "retained_without_family" for c in artifact["case_accounting"].values())
    assert artifact["policy"] == discovery.POLICY
    assert artifact["policy"]["cross_packet_reconciliation"] == "not_performed"
    assert not artifact["policy"]["publication_authorized"]
    before = {p: p.read_bytes() for p in ready["root"].rglob("*") if p.is_file()}
    resumed = discovery.execute(pin(ready["path"]), command="run", backend=model)
    assert resumed["model_calls_this_execution"] == 0 and len(model.calls) == 2
    assert discovery.execute(pin(ready["path"]), command="validate")["status"] == "validated"
    assert {p: p.read_bytes() for p in before} == before
    assert {p: p.read_bytes() for p in originals} == originals


def test_explicit_packet_limit_then_resume(ready):
    model = FakeBackend()
    first = discovery.execute(pin(ready["path"]), command="run", backend=model, max_packets=1)
    assert first["status"] == "paused_at_packet_limit" and len(model.calls) == 1
    assert not (ready["root"] / "discovery.json").exists()
    with pytest.raises(ValueError, match="incomplete"):
        discovery.execute(pin(ready["path"]), command="validate")
    assert discovery.execute(pin(ready["path"]), command="run", backend=model)["status"] == "completed"
    assert len(model.calls) == 2


def test_lifetime_call_budget_does_not_reset_on_resume(ready):
    ready["config"]["max_calls"] = 1
    ref = save(ready)
    model = FakeBackend()
    assert discovery.execute(ref, command="run", backend=model)["status"] == "call_budget_exhausted"
    assert discovery.execute(ref, command="run", backend=model)["status"] == "call_budget_exhausted"
    assert len(model.calls) == 1 and not (ready["root"] / "discovery.json").exists()
    ready["config"]["max_calls"] = 4
    with pytest.raises(ValueError, match="new run"):
        discovery.execute(save(ready), command="run", backend=model)


def test_failed_provider_stops_and_needs_explicit_bounded_retry(ready):
    class Failing(FakeBackend):
        def complete(self, *args):
            self.calls.append("failed")
            raise RuntimeError("Synthetic provider refusal")
    failed = Failing()
    result = discovery.execute(pin(ready["path"]), command="run", backend=failed)
    assert result["status"] == "failed_needs_explicit_retry" and len(failed.calls) == 1
    receipt_path = ready["root"] / "packet_000001/attempt_0001.result.json"
    receipt = receipt_path.read_bytes()
    model = FakeBackend()
    assert discovery.execute(pin(ready["path"]), command="run", backend=model)["status"] == "failed_needs_explicit_retry"
    assert not model.calls
    result = discovery.execute(pin(ready["path"]), command="run", backend=model, retry_failed=True)
    assert result["status"] == "completed" and result["admitted_calls"] == 3
    assert receipt_path.read_bytes() == receipt
    assert len(model.calls) == 2


def test_dangling_request_is_preserved_with_inspection_instructions(ready):
    class Interrupted:
        def complete(self, *args):
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        discovery.execute(pin(ready["path"]), command="run", backend=Interrupted())
    model = FakeBackend()
    result = discovery.execute(pin(ready["path"]), command="run", backend=model, retry_failed=True)
    assert result["status"] == "interrupted_needs_inspection" and result["admitted_calls"] == 1
    assert "duplicate-call risk" in result["inspection"] and not model.calls
    assert (ready["root"] / "packet_000001/attempt_0001.request.json").exists()
    assert not (ready["root"] / "packet_000001/attempt_0001.result.json").exists()


@pytest.mark.parametrize("fault", ["missing_case", "missing_principle", "duplicate_case", "foreign_case",
    "unknown_family", "duplicate_membership", "orphan_family", "bad_disposition", "principle_as_evidence", "bad_family_id"])
def test_response_accounting_fails_closed(ready, fault):
    context = packet_context(ready)
    value = response_for(context)
    if fault == "missing_case": value["case_dispositions"].pop()
    elif fault == "missing_principle": value["principle_dispositions"].pop()
    elif fault == "duplicate_case": value["case_dispositions"].append(copy.deepcopy(value["case_dispositions"][0]))
    elif fault == "foreign_case": value["case_dispositions"][0]["record_id"] = "OTHER:R1"
    elif fault == "unknown_family": value["case_dispositions"][0]["memberships"][0]["family_id"] = "F99"
    elif fault == "duplicate_membership": value["case_dispositions"][0]["memberships"].append(copy.deepcopy(value["case_dispositions"][0]["memberships"][0]))
    elif fault == "orphan_family": value["families"].append({**copy.deepcopy(value["families"][0]), "family_id": "F3"})
    elif fault == "bad_disposition": value["case_dispositions"][0]["disposition"] = "retained_without_family"
    elif fault == "principle_as_evidence": value["families"][0]["supporting_principles"] = [context["principles"][0]["principle_id"]]
    else: value["families"][0]["family_id"] = "GLOBAL:confirmed"
    with pytest.raises(ValueError): discovery._derive(value, context)


def test_non_host_is_context_not_host_support(ready):
    context = packet_context(ready)
    context["cases"][0]["speaker_attribution"] = "listener"
    value = response_for(context)
    assert discovery._derive(value, context) == value
    value["case_dispositions"][0]["memberships"][0]["role"] = "potential_counter"
    with pytest.raises(ValueError, match="Non-host"): discovery._derive(value, context)


def test_zero_families_preserves_all_cases_and_patterns(ready):
    model = FakeBackend(lambda value, context: response_for(context, empty=True))
    discovery.execute(pin(ready["path"]), command="run", backend=model)
    value = read_json(ready["root"] / "discovery.json")
    assert value["families"] == [] and len(value["case_accounting"]) == 4
    assert all(r["disposition"] == "retained_without_family" for r in value["case_accounting"].values())


def test_oversized_bundle_has_actionable_size_error_before_admission(ready):
    ready["config"]["max_input_chars"] = 100
    with pytest.raises(ValueError, match="needs .*input characters.*cap=100"):
        discovery.execute(save(ready), command="run", backend=FakeBackend())
    assert not ready["root"].exists()


def test_response_overflow_is_retained_but_not_published(ready):
    ready["config"]["max_response_chars"] = 100
    result = discovery.execute(save(ready), command="run", backend=FakeBackend())
    assert result["status"] == "failed_needs_explicit_retry"
    receipt = read_json(ready["root"] / "packet_000001/attempt_0001.result.json")
    assert receipt["error"]["phase"] == "validation" and receipt["response"]["families"]
    assert not (ready["root"] / "discovery.json").exists()


def test_repair_overflow_stops_without_another_admission(ready):
    def invalid(value, context):
        value["case_dispositions"][0]["reason"] = "Synthetic long rejected reason " * 300
        value["case_dispositions"].pop()
        return value
    first = FakeBackend(invalid)
    discovery.execute(pin(ready["path"]), command="run", backend=first)
    second = FakeBackend()
    result = discovery.execute(pin(ready["path"]), command="run", backend=second, retry_failed=True)
    assert result["status"] == "repair_input_overflow" and result["admitted_calls"] == 1
    assert not second.calls


@pytest.mark.parametrize("target", ["manifest", "request", "result", "artifact", "inventory"])
def test_replay_rejects_mutated_receipts_or_dependencies(ready, target):
    discovery.execute(pin(ready["path"]), command="run", backend=FakeBackend())
    path = {"manifest": ready["root"] / "run_manifest.json",
            "request": ready["root"] / "packet_000001/attempt_0001.request.json",
            "result": ready["root"] / "packet_000001/attempt_0001.result.json",
            "artifact": ready["root"] / "discovery.json",
            "inventory": Path(ready["config"]["inventory"]["path"])}[target]
    value = read_json(path); value["synthetic_mutation"] = True; atomic_write_json(path, value)
    with pytest.raises(ValueError): discovery.execute(pin(ready["path"]), command="validate")


def test_source_mutation_during_call_preserves_rejected_response(ready):
    path = Path(ready["source_cfg"]["episodes"][0]["raw"])
    def mutate(value, context):
        raw = read_json(path); raw["segments"][0]["text"] += " changed"; atomic_write_json(path, raw)
        return value
    result = discovery.execute(pin(ready["path"]), command="run", backend=FakeBackend(mutate))
    assert result["status"] == "failed_needs_explicit_retry"
    receipt = read_json(ready["root"] / "packet_000001/attempt_0001.result.json")
    assert receipt["error"]["phase"] == "source_validation" and receipt["response"]
    assert not (ready["root"] / "discovery.json").exists()


def test_cli_plan_and_errors_do_not_leak_source_text_or_paths(ready, capsys):
    args = ["--config", str(ready["path"]), "--sha256", sha256_file(ready["path"])]
    assert discovery.main(["plan", *args]) == 0
    assert discovery.main(["validate", *args]) == 2
    text = capsys.readouterr().out
    assert str(ready["path"].parent) not in text and "Fictional host" not in text
    assert '"model_calls_this_execution": 0' in text


def test_one_episode_cache_evicts_raw_payload_and_rereads_evicted_pins(ready, monkeypatch):
    class TrackedRow(dict):
        pass

    raw_paths = {entry["raw"] for entry in ready["source_cfg"]["episodes"]}
    reads, rows = [], []
    original = inventory._read

    def tracked(ref, pins):
        value = original(ref, pins)
        if ref["path"] in raw_paths:
            reads.append(ref["path"])
            value["segments"] = [TrackedRow(row) for row in value["segments"]]
            rows.extend(weakref.ref(row) for row in value["segments"])
        return value

    monkeypatch.setattr(inventory, "_read", tracked)
    materials = discovery._materials(ready["catalog"], {})
    assert materials.cached is None
    materials.episode("SYN1")
    first_rows = list(rows)
    assert first_rows and all(ref() is not None for ref in first_rows)
    materials.episode("SYN1")
    assert len(reads) == 1
    materials.episode("SYN2")
    gc.collect()
    assert all(ref() is None for ref in first_rows)
    assert materials.episode_id == "SYN2" and len(reads) == 2
    materials.episode("SYN1")
    assert len(reads) == 3
    path = Path(ready["source_cfg"]["episodes"][1]["raw"])
    value = read_json(path)
    value["segments"][0]["text"] += " synthetic mutation after eviction"
    atomic_write_json(path, value)
    with pytest.raises(ValueError, match="changed"):
        materials.episode("SYN2")
    assert materials.cached is None


def test_planning_releases_contexts_and_history_keeps_only_receipt_references(ready, monkeypatch):
    class TrackedContext(dict):
        pass

    contexts = []
    original = discovery._context

    def tracked(*args, **kwargs):
        result = TrackedContext(original(*args, **kwargs))
        contexts.append(weakref.ref(result))
        return result

    monkeypatch.setattr(discovery, "_context", tracked)
    config, root, catalog, packets, manifest, pins, prompt = discovery._prepare(pin(ready["path"]))
    gc.collect()
    assert contexts and all(ref() is None for ref in contexts)
    assert all(set(packet) == {"packet_id", "context_sha256", "input_chars", "owned_case_ids", "owned_principle_ids"}
               for packet in packets)
    discovery.execute(pin(ready["path"]), command="run", backend=FakeBackend())
    histories, calls = discovery._history(root, manifest, packets, prompt, catalog, pins)
    assert calls == 2
    assert all(set(entry) == {"status", "request", "result", "result_sha256"}
               for history in histories.values() for entry in history)
    gc.collect()
    assert all(ref() is None for ref in contexts)


def test_shared_runner_pins_and_original_knowledge_receipt_contract(ready, monkeypatch):
    from knowledge import discovery_runner

    ref = pin(ready["path"])
    discovery.execute(ref, command="run", backend=FakeBackend())
    config, root, catalog, packets, manifest, pins, prompt = discovery._prepare(ref)
    assert manifest["implementation"]["runner_sha256"] == sha256_file(Path(discovery_runner.__file__))
    request_path = root / "packet_000001/attempt_0001.request.json"
    result = read_json(root / "packet_000001/attempt_0001.result.json")
    context = discovery._materialize(packets[0], catalog, pins, prompt, config["max_input_chars"])
    assert read_json(request_path) == {
        "schema_version": "knowledge_discovery_request.v1", "run_sha256": discovery.provenance.digest(manifest),
        "packet_id": packets[0]["packet_id"], "attempt": 1, "prompt": prompt,
        "context": context, "schema": discovery.PROPOSAL, "previous_result_digest": None}
    assert set(result) == {"schema_version", "request_sha256", "status", "response", "usage", "error"}
    assert result["schema_version"] == "knowledge_discovery_result.v1"
    assert result["request_sha256"] == sha256_file(request_path)
    old = copy.deepcopy(discovery._implementation())
    old.pop("runner_sha256")
    monkeypatch.setattr(discovery, "_implementation", lambda: old)
    with pytest.raises(ValueError, match="new run version"):
        discovery.execute(ref, command="validate")


def test_history_drops_previous_packet_payload_before_next_materialization(ready, monkeypatch):
    from knowledge import discovery_runner

    class Tracked(dict): pass
    discovery.execute(pin(ready["path"]), command="run", backend=FakeBackend())
    _, root, catalog, packets, manifest, pins, prompt = discovery._prepare(pin(ready["path"]))
    refs = []
    original_read, original_materialize = discovery_runner._read_json, discovery._materialize
    def read(path, pins=None):
        value = original_read(path, pins)
        if path.name.startswith("attempt_"):
            value = Tracked(value)
            refs.append(weakref.ref(value))
        return value
    def materialize(*args):
        gc.collect()
        assert all(ref() is None for ref in refs)
        return original_materialize(*args)
    monkeypatch.setattr(discovery_runner, "_read_json", read)
    monkeypatch.setattr(discovery, "_materialize", materialize)
    discovery._history(root, manifest, packets, prompt, catalog, pins)
    assert refs and all(ref() is None for ref in refs)


def test_rematerialization_rereads_exact_sources_and_rejects_descriptor_or_source_drift(ready, monkeypatch):
    config, _, catalog, packets, _, pins, prompt = discovery._prepare(pin(ready["path"]))
    raw_paths = {entry["raw"] for entry in ready["source_cfg"]["episodes"]}
    reads = []
    original = inventory._read

    def tracked(ref, checked):
        if ref["path"] in raw_paths:
            reads.append(ref["path"])
        return original(ref, checked)

    monkeypatch.setattr(inventory, "_read", tracked)
    first = discovery._materialize(packets[0], catalog, pins, prompt, config["max_input_chars"])
    second = discovery._materialize(packets[0], catalog, pins, prompt, config["max_input_chars"])
    assert first == second and len(reads) == 2
    with pytest.raises(ValueError, match="pinned plan"):
        discovery._materialize({**packets[0], "context_sha256": "0" * 64}, catalog, pins, prompt, config["max_input_chars"])
    path = Path(reads[0])
    raw = read_json(path)
    raw["segments"][0]["text"] += " synthetic drift"
    atomic_write_json(path, raw)
    with pytest.raises(ValueError, match="changed"):
        discovery._materialize(packets[0], catalog, pins, prompt, config["max_input_chars"])


def test_bypass_cases_are_still_owned_and_retained_without_forced_families(cfg, tmp_path):
    class Bypass(InventoryModel):
        def complete(self, *args):
            value, usage = super().complete(*args)
            if "strategy" in value and value["episode_id"] == "SYN1":
                value.update(strategy="bypass", strategy_reason="Isolated fictional cases; no episode-level abstraction", principles=[])
            return value, usage

    data = make_ready(cfg, tmp_path, Bypass())
    model = FakeBackend(lambda value, context: response_for(context, empty=True))
    result = discovery.execute(pin(data["path"]), command="run", backend=model)
    assert result["status"] == "completed" and result["owned_cases"] == 4 and result["owned_principles"] == 2
    artifact = read_json(data["root"] / "discovery.json")
    assert artifact["families"] == []
    assert all(artifact["case_accounting"][rid]["disposition"] == "retained_without_family" for rid in ("SYN1:R1", "SYN1:R2"))
    assert any("SYN1:R1" in context["owned_case_ids"] for context in model.calls)


def test_supplementary_commentary_survives_separately_from_case_and_raw_claims(cfg, tmp_path):
    note = "Synthetic reviewer caution, not a host quotation or independent case."

    class WithNotes(InventoryModel):
        def complete(self, *args):
            value, usage = super().complete(*args)
            if "case_assessments" in value:
                value["case_assessments"][0]["supplementary_notes"] = [
                    {"kind": "interpretive_caution", "merged_ids": value["cases"][0]["merged_ids"], "note": note}]
            return value, usage

    data = make_ready(cfg, tmp_path, WithNotes())
    context = packet_context(data)
    first = context["cases"][0]
    assessment = first["evidence"]["candidate_source_assessment"]
    assert assessment["supplementary_notes"][0]["note"] == note
    assert assessment["authority"] == "model_assessment_not_source"
    assert note not in json.dumps({key: value for key, value in first.items() if key != "evidence"})
    assert note not in json.dumps(context["raw_regions"])


def test_validation_retry_preserves_rejected_response_and_uses_bound_feedback(ready):
    ready["config"]["max_input_chars"] = 1000000
    ref = save(ready)

    def invalid(value, context):
        value["case_dispositions"].pop()
        return value

    assert discovery.execute(ref, command="run", backend=FakeBackend(invalid))["status"] == "failed_needs_explicit_retry"
    path = ready["root"] / "packet_000001/attempt_0001.result.json"
    original = path.read_bytes()
    failed = read_json(path)
    model = FakeBackend()
    assert discovery.execute(ref, command="run", backend=model)["model_calls_this_execution"] == 0
    result = discovery.execute(ref, command="run", backend=model, retry_failed=True)
    assert result["status"] == "completed" and result["admitted_calls"] == 2 and len(model.calls) == 1
    feedback = model.calls[0]["validation_repair"]
    assert feedback["previous_response"] == failed["response"] and feedback["error"] == failed["error"]
    assert feedback["authority"] == "validator_feedback_not_source" and path.read_bytes() == original
    request = read_json(ready["root"] / "packet_000001/attempt_0002.request.json")
    assert request["previous_result_digest"] == discovery.provenance.digest(failed)
    assert discovery.execute(ref, command="validate")["status"] == "validated"


def test_retry_exhaustion_and_lock_do_not_admit_calls(ready):
    ready["config"]["max_input_chars"] = 1000000
    model = FakeBackend(lambda value, context: {**value, "case_dispositions": []})
    ref = save(ready)
    for retry in (False, True, True):
        assert discovery.execute(ref, command="run", backend=model, retry_failed=retry)["status"] == "failed_needs_explicit_retry"
    assert len(model.calls) == 2
    with (ready["root"] / ".run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            discovery.execute(ref, command="run", backend=model, retry_failed=True)
    assert len(model.calls) == 2


def test_output_cannot_be_nested_in_original_extraction_run(ready):
    ready["config"]["output_root"] = str(Path(ready["source_cfg"]["output_root"]) / ready["source_cfg"]["run_id"])
    with pytest.raises(ValueError, match="outside extraction runs"):
        discovery.execute(save(ready), command="run", backend=FakeBackend())
