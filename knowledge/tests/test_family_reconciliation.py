"""Synthetic source-grounded pair reconciliation; never invoke a real provider."""
import copy
import itertools
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge import corpus_discovery as discovery, family_reconciliation as reconcile
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_corpus_discovery import FakeBackend, make_ready, response_for
from .test_corpus_inventory import pin
from .test_pipeline import cfg


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Synthetic reconciliation only"))
    monkeypatch.setattr(reconcile.runner.codex_backend, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


def setup(cfg, tmp_path, mode=None):
    parent = make_ready(cfg, tmp_path)
    if mode == "same_packet":
        parent["config"]["max_input_chars"] = 1000000
        atomic_write_json(parent["path"], parent["config"])
    calls = 0

    def transform(value, context):
        nonlocal calls
        calls += 1
        if mode == "zero" or mode == "one" and calls > 1:
            return response_for(context, empty=True)
        if mode == "one":
            value["families"] = value["families"][:1]
            value["case_dispositions"][0]["memberships"] = value["case_dispositions"][0]["memberships"][:1]
        return value

    discovery.execute(pin(parent["path"]), command="run", backend=FakeBackend(transform))
    config = {"schema_version": "knowledge_reconciliation_config.v1", "run_id": "synthetic_reconciliation",
        "output_root": str(tmp_path / "reconciliation"),
        "parent": {"config": pin(parent["path"]), "artifact": pin(parent["root"] / "discovery.json")},
        "model": "synthetic", "reasoning_effort": "low", "timeout_seconds": 30,
        "max_input_chars": 200000, "max_response_chars": 100000, "max_calls": 8, "retry_count": 1, "max_pairs": 8}
    path = tmp_path / "reconciliation-config.json"
    atomic_write_json(path, config)
    return {"config": config, "path": path, "root": Path(config["output_root"]) / config["run_id"], "parent": parent}


@pytest.fixture
def ready(cfg, tmp_path):
    return setup(cfg, tmp_path)


def save(data):
    atomic_write_json(data["path"], data["config"])
    return pin(data["path"])


def context_for(data):
    cfg, _, catalog, packets, _, pins, prompt = reconcile._prepare(pin(data["path"]))
    return reconcile._materialize(next(iter(packets)), catalog, pins, prompt, cfg["max_input_chars"])


def anchor_for(bundle, rid):
    case = next(row for row in bundle["cases"] if row["record_id"] == rid)
    ev = case["evidence"]
    sid = ev["source_segment_ids"][0] if ev["source_segment_ids"] else ev["source_segment_id_ranges"][0]["start_id"]
    raw = next(row for row in bundle["raw_regions"][case["episode_id"]] if row["segment_id"] == sid)
    return {"episode_id": case["episode_id"], **raw}


def proposal(context, relation="same"):
    bundle = context["evidence_bundle"]
    return {"family_keys": list(context["family_keys"]), "relationship": relation,
        "reason": "Synthetic source-level comparison, not a principle or event count.",
        "conditions": ["Fictional scope differs under an explicit condition"] if relation == "conditional_different" else [],
        "limitations": ["Synthetic unresolved comparison"] if relation == "unresolved" else [],
        "case_dispositions": [{"record_id": case["record_id"], "disposition": "relevant",
                               "reason": "Keep this original source context visible", "source_anchors": [anchor_for(bundle, case["record_id"])]}
                              for case in bundle["cases"]],
        "counter_dispositions": [{"obligation_id": row["obligation_id"], "disposition": "retained",
                                  "reason": "Original counter remains a scope limitation",
                                  "source_anchors": [anchor_for(bundle, row["record_id"])]}
                                 for row in bundle["known_counter_obligations"]]}


class Model:
    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform

    def complete(self, prompt, context, schema):
        self.calls.append(copy.deepcopy(context))
        value = proposal(context, reconcile.RELATIONS[(len(self.calls) - 1) % 4])
        return (self.transform(value, context) if self.transform else value), {"provider": "synthetic", "cost_usd": None}


def test_plan_is_read_only_lazy_and_reopens_complete_ineligible_counter_bundles(ready, tmp_path):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = reconcile.execute(pin(ready["path"]))
    assert result["status"] == "planned" and result["packet_count"] == 4
    assert result["family_count"] == 4 and result["retained_ungrouped_cases"] == 2
    assert result["model_calls_this_execution"] == 0 and not ready["root"].exists()
    assert isinstance(result["packet_plan"], dict) and result["packet_plan"]["ordered_context_digest"]
    cfg, _, catalog, packets, manifest, pins, prompt = reconcile._prepare(pin(ready["path"]))
    assert isinstance(packets, reconcile.PairSchedule) and not isinstance(packets, list)
    ctx = reconcile._materialize(next(iter(packets)), catalog, pins, prompt, cfg["max_input_chars"])
    bundle = ctx["evidence_bundle"]
    assert {c["record_id"] for c in bundle["cases"]} == set(ready["parent"]["catalog"]["cases"])
    assert any(not row["parent_eligible_for_level4"] for row in bundle["known_counter_obligations"])
    assert all(source["source"]["prior_source_decision"] == "uncertain" for source in bundle["sources"].values())
    assert all(c["evidence"]["candidate_source_assessment"]["authority"] == "model_assessment_not_source" for c in bundle["cases"])
    assert all(f["authority"] == "parent_discovery_routing_commentary_not_evidence" for f in ctx["families"])
    assert all(key.startswith(ready["config"]["parent"]["artifact"]["sha256"] + ":") for key in ctx["family_keys"])
    assert manifest["implementation"]["runner_sha256"] == sha256_file(Path(reconcile.runner.__file__))
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_run_all_relations_keeps_original_accounting_edges_not_transitive_groups(ready, tmp_path):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    model = Model()
    done = reconcile.execute(pin(ready["path"]), command="run", backend=model)
    assert done["status"] == "completed" and len(model.calls) == 4
    value = read_json(ready["root"] / "discovery.json")
    parent = read_json(Path(ready["config"]["parent"]["artifact"]["path"]))
    assert value["case_accounting"] == parent["case_accounting"]
    assert value["principle_accounting"] == parent["principle_accounting"]
    assert value["schema_version"] == "knowledge_family_reconciliation.v1"
    assert value["edge_ledger"]["receipt_count"] == 4 and value["unresolved_pair_count"] == 1
    assert len(value["family_accounting"]) == 4 and all(row["reviewed_pairs"] == 2 for row in value["family_accounting"].values())
    assert not {"merged_families", "edges", "principles"} & value.keys()
    assert value["policy"]["global_counter_review"] == value["policy"]["ungrouped_evidence_sweep"] == "not_performed"
    assert value["policy"]["independent_event_count"] is None and not value["policy"]["eligible_for_serving"]
    assert reconcile.execute(pin(ready["path"]), command="validate")["status"] == "validated"
    assert reconcile.execute(pin(ready["path"]), command="run", backend=model)["model_calls_this_execution"] == 0
    assert len(model.calls) == 4 and {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("mode,count", [("zero", 0), ("one", 1), ("same_packet", 2)])
def test_zero_pair_cases_keep_all_family_and_ungrouped_accounting(cfg, tmp_path, mode, count):
    data = setup(cfg, tmp_path, mode)
    model = Model()
    plan = reconcile.execute(pin(data["path"]))
    assert plan["packet_count"] == 0 and plan["family_count"] == count
    assert reconcile.execute(pin(data["path"]), command="run", backend=model)["status"] == "completed"
    assert not model.calls
    value = read_json(data["root"] / "discovery.json")
    assert value["status"] == "no_cross_packet_pairs" and len(value["case_accounting"]) == 4
    assert len(value["family_accounting"]) == count
    assert all(row["disposition"] == "retained_without_cross_packet_peer" for row in value["family_accounting"].values())
    assert value["policy"]["global_counter_review"] == "not_performed"
    assert reconcile.execute(pin(data["path"]), command="validate")["status"] == "validated"


def test_large_pair_schedule_is_lazy():
    families = {f"family{i:04d}": {"packet_id": str(i)} for i in range(1000)}
    schedule = reconcile.PairSchedule(families)
    assert len(schedule) == 499500 and set(vars(schedule)) == {"families", "ids", "count"}
    assert len(list(itertools.islice(schedule, 3))) == 3
    assert list(itertools.islice(schedule, 2)) == list(itertools.islice(schedule, 2))


def test_pair_ceiling_preflight_precedes_pair_context_and_has_no_big_default(ready, monkeypatch):
    ready["config"]["max_pairs"] = 3
    ready["config"]["max_calls"] = 1
    ref = save(ready)
    forbidden = Mock(side_effect=AssertionError("Do not open source pair bundles beyond explicit pair ceiling"))
    monkeypatch.setattr(reconcile, "_context", forbidden)
    for command in ("plan", "run", "validate"):
        result = reconcile.execute(ref, command=command, backend=Model())
        assert result["status"] == result["pair_coverage"] == "incomplete_pair_limit"
        assert result["planned_first_attempt_calls"] == 4 and result["max_pairs"] == 3
        assert not result["packet_plan"]["first_pass_fits_call_budget"]
        assert result["packet_plan"]["potential_calls_with_retries"] == 8
        assert len(result["packet_plan"]["family_order"]) == 4 and result["parent"] == ready["config"]["parent"]
    assert not ready["root"].exists()
    forbidden.assert_not_called()
    ready["config"].pop("max_pairs")
    with pytest.raises(ValueError, match="schema"):
        reconcile.execute(save(ready))


@pytest.mark.parametrize("offset", [-200, 1000000])
def test_source_size_precheck_rebases_discovery_schema_overhead(ready, monkeypatch, offset):
    cfg, _, catalog, packets, _, pins, prompt = reconcile._prepare(pin(ready["path"]))
    packet = next(iter(packets))
    expected = reconcile._materialize(packet, catalog, pins, prompt, cfg["max_input_chars"])
    exact_cap = reconcile._size(prompt, expected)
    original = discovery._size
    monkeypatch.setattr(discovery, "_size", lambda prompt, context: original(prompt, context) + offset)
    assert reconcile._materialize(packet, catalog, pins, prompt, exact_cap) == expected
    with pytest.raises(discovery._PacketTooLarge):
        reconcile._materialize(packet, catalog, pins, prompt, exact_cap - 1)


def test_oversize_is_explicit_incomplete_with_all_refs_and_no_admission(ready):
    ready["config"]["max_input_chars"] = 100
    ref, model = save(ready), Model()
    for command in ("plan", "run", "validate"):
        result = reconcile.execute(ref, command=command, backend=model)
        assert result["status"] == "incomplete_input_overflow" and result["blocked_pair_count"] == 4
        assert result["packet_plan"]["pair_count"] == 4 and len(result["packet_plan"]["family_order"]) == 4
        assert len(result["first_blocked_pair"]["required_case_ids"]) == 4
        assert result["parent"] == ready["config"]["parent"] and result["retained_ungrouped_cases"] == 2
    assert not model.calls and not ready["root"].exists()


@pytest.mark.parametrize("fault", ["wrong_pair", "missing_case", "duplicate_case", "missing_counter", "unknown_counter",
                                  "wrong_quote", "wrong_episode", "wrong_region", "conditional", "unresolved", "settled_with_uncertainty"])
def test_response_rejects_missing_accounting_or_unbound_sources(ready, fault):
    ctx = context_for(ready)
    value = proposal(ctx)
    if fault == "wrong_pair": value["family_keys"].reverse()
    elif fault == "missing_case": value["case_dispositions"].pop()
    elif fault == "duplicate_case": value["case_dispositions"].append(copy.deepcopy(value["case_dispositions"][0]))
    elif fault == "missing_counter": value["counter_dispositions"].pop()
    elif fault == "unknown_counter": value["counter_dispositions"][0]["obligation_id"] = "invented"
    elif fault == "wrong_quote": value["case_dispositions"][0]["source_anchors"][0]["text"] += " invented"
    elif fault == "wrong_episode": value["case_dispositions"][0]["source_anchors"][0]["episode_id"] = "OTHER"
    elif fault == "wrong_region": value["case_dispositions"][0]["source_anchors"] = [anchor_for(ctx["evidence_bundle"], "SYN1:R2")]
    elif fault == "conditional": value["relationship"] = "conditional_different"
    elif fault == "unresolved": value["relationship"] = "unresolved"
    else: value["counter_dispositions"][0]["disposition"] = "unresolved"
    with pytest.raises(ValueError):
        reconcile._derive(value, ctx)


def test_partial_limit_resume_and_durable_budget(ready):
    model = Model()
    ref = pin(ready["path"])
    first = reconcile.execute(ref, command="run", backend=model, max_packets=1)
    assert first["status"] == "paused_at_packet_limit" and first["completed_packets"] == 1
    assert first["pair_coverage"] == "partial"
    assert not (ready["root"] / "discovery.json").exists()
    with pytest.raises(ValueError, match="incomplete"):
        reconcile.execute(ref, command="validate")
    assert reconcile.execute(ref, command="run", backend=model)["status"] == "completed"
    assert len(model.calls) == 4


def test_call_budget_cannot_be_reset(ready):
    ready["config"]["max_calls"] = 1
    ref, model = save(ready), Model()
    for _ in range(2):
        result = reconcile.execute(ref, command="run", backend=model)
        assert result["status"] == "call_budget_exhausted" and result["admitted_calls"] == 1
    assert len(model.calls) == 1
    ready["config"]["max_calls"] = 8
    with pytest.raises(ValueError, match="new run"):
        reconcile.execute(save(ready), command="run", backend=model)


def test_failure_requires_explicit_retry_and_preserves_prior_receipt(ready):
    bad = Model(lambda value, context: {**value, "case_dispositions": []})
    ref = pin(ready["path"])
    assert reconcile.execute(ref, command="run", backend=bad)["status"] == "failed_needs_explicit_retry"
    before = (ready["root"] / "packet_000000001/attempt_0001.result.json").read_bytes()
    good = Model()
    assert reconcile.execute(ref, command="run", backend=good)["model_calls_this_execution"] == 0
    assert reconcile.execute(ref, command="run", backend=good, retry_failed=True)["status"] == "completed"
    assert (ready["root"] / "packet_000000001/attempt_0001.result.json").read_bytes() == before
    assert reconcile.execute(ref, command="validate")["status"] == "validated"


def test_interrupted_request_preserved(ready):
    class Interrupted:
        def complete(self, *args):
            raise KeyboardInterrupt()
    ref = pin(ready["path"])
    with pytest.raises(KeyboardInterrupt):
        reconcile.execute(ref, command="run", backend=Interrupted())
    model = Model()
    result = reconcile.execute(ref, command="run", backend=model, retry_failed=True)
    assert result["status"] == "interrupted_needs_inspection" and result["admitted_calls"] == 1
    assert not model.calls and "duplicate-call risk" in result["inspection"]


@pytest.mark.parametrize("target", ["parent", "parent_receipt", "source", "result", "output"])
def test_parent_and_reconciliation_versions_fail_closed_on_drift(ready, target):
    reconcile.execute(pin(ready["path"]), command="run", backend=Model())
    paths = {"parent": Path(ready["config"]["parent"]["artifact"]["path"]),
             "parent_receipt": ready["parent"]["root"] / "packet_000001/attempt_0001.result.json",
             "source": Path(ready["parent"]["source_cfg"]["episodes"][0]["raw"]),
             "result": ready["root"] / "packet_000000001/attempt_0001.result.json",
             "output": ready["root"] / "discovery.json"}
    path = paths[target]
    value = read_json(path)
    value["tampered"] = True
    atomic_write_json(path, value)
    if target == "parent":
        ready["config"]["parent"]["artifact"] = pin(path)
        save(ready)
    with pytest.raises(ValueError):
        reconcile.execute(pin(ready["path"]), command="validate")


def test_source_mutation_during_call_retains_failed_response(ready):
    path = Path(ready["parent"]["source_cfg"]["episodes"][0]["raw"])
    def mutate(value, context):
        raw = read_json(path)
        raw["segments"][0]["text"] += " synthetic mutation"
        atomic_write_json(path, raw)
        return value
    result = reconcile.execute(pin(ready["path"]), command="run", backend=Model(mutate))
    assert result["status"] == "failed_needs_explicit_retry"
    receipt = read_json(ready["root"] / "packet_000000001/attempt_0001.result.json")
    assert receipt["error"]["phase"] == "source_validation" and receipt["response"]
    assert not (ready["root"] / "discovery.json").exists()


def test_incomplete_parent_is_rejected_before_any_comparison(ready):
    path = ready["parent"]["root"] / "packet_000002/attempt_0001.result.json"
    path.unlink()
    model = Model()
    with pytest.raises(ValueError, match="completed discovery"):
        reconcile.execute(pin(ready["path"]), command="run", backend=model)
    assert not model.calls and not ready["root"].exists()


def test_oversized_response_is_preserved_not_truncated(ready):
    ready["config"]["max_response_chars"] = 100
    result = reconcile.execute(save(ready), command="run", backend=Model())
    assert result["status"] == "failed_needs_explicit_retry"
    receipt = read_json(ready["root"] / "packet_000000001/attempt_0001.result.json")
    assert receipt["error"]["phase"] == "validation" and len(receipt["response"]["case_dispositions"]) == 4
    assert not (ready["root"] / "discovery.json").exists()


def test_validator_drift_during_call_is_not_published(ready, monkeypatch):
    original = reconcile._implementation
    def change(value, context):
        changed = original()
        changed["runner_sha256"] = "0" * 64
        monkeypatch.setattr(reconcile, "_implementation", lambda: changed)
        return value
    result = reconcile.execute(pin(ready["path"]), command="run", backend=Model(change))
    assert result["status"] == "failed_needs_explicit_retry"
    receipt = read_json(ready["root"] / "packet_000000001/attempt_0001.result.json")
    assert receipt["error"]["phase"] == "source_validation" and receipt["response"]
    assert not (ready["root"] / "discovery.json").exists()


def test_cli_plan_run_validate_and_parent_output_protection(ready, capsys, monkeypatch):
    args = ["--config", str(ready["path"]), "--sha256", sha256_file(ready["path"])]
    assert reconcile.main(["plan", *args]) == 0
    actual = reconcile.execute
    model = Model()
    monkeypatch.setattr(reconcile, "execute", lambda *a, **kw: actual(*a, **kw, backend=model))
    assert reconcile.main(["run", *args, "--max-packets", "1"]) == 0
    assert reconcile.main(["run", *args]) == 0
    assert reconcile.main(["validate", *args]) == 0
    assert str(ready["path"].parent) not in capsys.readouterr().out
    ready["config"]["output_root"] = str(ready["parent"]["root"])
    with pytest.raises(ValueError, match="overlap"):
        actual(save(ready))
