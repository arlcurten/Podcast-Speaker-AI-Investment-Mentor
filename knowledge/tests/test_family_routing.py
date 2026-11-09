"""Synthetic descriptor routing and selected source verification; no live calls."""
import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge import corpus_discovery as discovery, family_reconciliation as verifier
from knowledge import family_routing as routing, family_verification as selected
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_family_reconciliation import setup, Model
from .test_corpus_discovery import make_ready, FakeBackend
from .test_corpus_inventory import pin
from .test_pipeline import cfg


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Synthetic tests only"))
    monkeypatch.setattr(routing.runner.codex_backend, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


def make_routing(cfg, tmp_path, mode=None):
    data = setup(cfg, tmp_path, mode)
    config = {**data["config"], "schema_version": "knowledge_family_routing_config.v1",
              "run_id": "routing", "output_root": str(tmp_path / "routing"),
              "max_response_entries": 2, "audit_pairs": []}
    config.pop("max_pairs")
    path = tmp_path / "routing-config.json"
    atomic_write_json(path, config)
    return {"config": config, "path": path, "root": Path(config["output_root"]) / config["run_id"], "original": data}


@pytest.fixture
def ready(cfg, tmp_path):
    return make_routing(cfg, tmp_path)


def save(data):
    atomic_write_json(data["path"], data["config"])
    return pin(data["path"])


class RouteModel:
    def __init__(self, transform=None, negative=False):
        self.calls, self.transform, self.negative = [], transform, negative

    def complete(self, prompt, context, schema):
        self.calls.append(copy.deepcopy(context))
        value = {"cell_sha256": context["cell_sha256"], "pair_dispositions": [
            {"family_keys": list(pair), "disposition": "routing_negative" if self.negative else
             ("proposed_link" if index == 0 and len(self.calls) == 1 else "uncertain" if index == 1 and len(self.calls) == 1 else "routing_negative"),
             "reason": "Synthetic descriptor comparison; not source evidence"}
            for index, pair in enumerate(context["family_pairs"])]}
        return self.transform(value, context) if self.transform else value, {"provider": "synthetic"}


def selection_config(data, tmp_path):
    config = {**data["original"]["config"], "schema_version": "knowledge_reconciliation_config.v2",
              "run_id": "selected", "output_root": str(tmp_path / "selected"),
              "selection": pin(data["root"] / "discovery.json")}
    path = tmp_path / "selected-config.json"
    atomic_write_json(path, config)
    return {"config": config, "path": path, "root": Path(config["output_root"]) / config["run_id"]}


def context_for(data):
    config, _, catalog, packets, _, pins, prompt = routing._prepare(pin(data["path"]))
    return routing._materialize(next(iter(packets)), catalog, pins, prompt, config["max_input_chars"])


def test_plan_bounded_pages_full_descriptors_and_exact_coverage(ready, tmp_path):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    plan = routing.execute(pin(ready["path"]))
    config, _, catalog, packets, manifest, pins, prompt = routing._prepare(pin(ready["path"]))
    assert not isinstance(packets, list) and len(packets) > 1
    assert plan["planned_first_attempt_calls"] == len(packets)
    assert plan["packet_plan"]["potential_calls_with_retries"] == len(packets) * 2
    assert plan["packet_plan"]["first_pass_fits_call_budget"]
    pairs = []
    for packet in packets:
        context = routing._materialize(packet, catalog, pins, prompt, config["max_input_chars"])
        assert len(context["family_pairs"]) <= 2 and routing._size(prompt, context) <= config["max_input_chars"]
        assert "raw_regions" not in context and "evidence_bundle" not in context
        for family in context["families"]:
            original = catalog["families"][family["family_key"]]
            for key in ("label", "decision_process", "conditions", "accepted_costs", "limitations"):
                assert family[key] == original[key]
            assert family["memberships_sha256"] == routing.provenance.digest(original["memberships"])
        pairs.extend(map(tuple, context["family_pairs"]))
    expected = {tuple(p["family_keys"]) for p in verifier.PairSchedule(catalog["families"])}
    assert len(pairs) == len(set(pairs)) == 4 and set(pairs) == expected
    assert manifest["implementation"]["verifier"]["runner_sha256"] == sha256_file(Path(routing.runner.__file__))
    assert not ready["root"].exists() and {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_input_bound_splits_cells_without_losing_pairs(ready):
    _, _, catalog, packets, _, _, prompt = routing._prepare(pin(ready["path"]))
    pairs = [p["family_keys"] for p in verifier.PairSchedule(catalog["families"])]
    cap = max(routing._size(prompt, routing._context(pair, [pair], catalog)) for pair in pairs)
    split = routing.CellSchedule(catalog, prompt, cap, 100)
    assert split.pair_count == 4 and split.count == 4
    assert all(routing._size(prompt, split.context(p)) <= cap for p in split)
    with pytest.raises(verifier.IncompletePlan):
        routing.CellSchedule(catalog, prompt, cap - 1, 100)


def test_complete_routing_selected_run_only_union_and_no_allpair_preflight(ready, tmp_path, monkeypatch):
    model = RouteModel()
    assert routing.execute(pin(ready["path"]), command="run", backend=model)["status"] == "completed"
    value = read_json(ready["root"] / "discovery.json")
    assert value["selected_count"] == 2 and value["routing_counts"]["routing_negative"] == 2
    assert value["future_challenge"]["ungrouped_case_ids"] and value["future_challenge"]["known_item_family_obligations"]
    assert value["future_challenge"]["status"] == "mandatory_not_performed"
    assert routing.execute(pin(ready["path"]), command="validate")["status"] == "validated"
    assert routing.execute(pin(ready["path"]), command="run", backend=model)["model_calls_this_execution"] == 0
    data = selection_config(ready, tmp_path)
    data["config"]["max_pairs"] = 2
    ref = save(data)
    forbidden = Mock(side_effect=AssertionError("v2 must not use all-pair source schedule"))
    monkeypatch.setattr(verifier, "PairSchedule", forbidden)
    actual = verifier._context
    observed = []

    def track(packet, *args, **kwargs):
        observed.append(packet["family_keys"])
        return actual(packet, *args, **kwargs)

    monkeypatch.setattr(verifier, "_context", track)
    plan = verifier.execute(ref)
    assert plan["packet_count"] == 2 and observed == [row["family_keys"] for row in value["selection"]]
    source_model = Model()
    assert verifier.execute(ref, command="run", backend=source_model)["status"] == "completed"
    assert len(source_model.calls) == 2
    final = read_json(data["root"] / "discovery.json")
    assert final["schema_version"] == "knowledge_family_reconciliation.v2"
    assert final["case_accounting"] == value["case_accounting"] and final["principle_accounting"] == value["principle_accounting"]
    assert final["future_challenge"] == value["future_challenge"] and final["source_verified_pair_count"] == 2
    assert final["policy"]["global_counter_review"] == "not_performed" and not final["policy"]["publication_authorized"]
    assert verifier.execute(ref, command="validate")["status"] == "validated"
    assert verifier.execute(ref, command="run", backend=source_model)["model_calls_this_execution"] == 0
    assert read_json(data["root"] / "packet_000000001/attempt_0001.request.json")["schema_version"] == selected.REQUEST_VERSION
    forbidden.assert_not_called()


def test_explicit_audit_adds_negative_edge_and_preserves_origin(ready, tmp_path):
    ctx = context_for(ready)
    ready["config"]["audit_pairs"] = [ctx["family_pairs"][0]]
    routing.execute(save(ready), command="run", backend=RouteModel(negative=True))
    value = read_json(ready["root"] / "discovery.json")
    assert value["selected_count"] == 1 and value["routing_counts"]["routing_negative"] == 4
    assert value["selection"][0]["origins"] == [{"kind": "explicit_audit", "config": pin(ready["path"])}]
    data = selection_config(ready, tmp_path)
    assert verifier.execute(pin(data["path"]), command="run", backend=Model())["packet_count"] == 1


def test_forced_known_counter_union_even_when_routing_negative(ready):
    _, _, catalog, packets, manifest, _, _ = routing._prepare(pin(ready["path"]))
    families = catalog["families"]
    first, last = sorted(families)[0], sorted(families)[-1]
    principle = next(p for p in catalog["inventory"]["principles"].values() if p["counterexamples"])
    supporter, counter = principle["supporting_records"][0], principle["counterexamples"][0]["record_id"]
    families[first]["memberships"][supporter] = {"role": "potential_support"}
    families[last]["memberships"][counter] = {"role": "potential_counter"}
    links = list(routing._known_links(catalog))
    assert any(pair == (first, last) and origin["kind"] == "known_counter" for pair, origin in links)
    assert any(pair is None for pair, origin in links)


def test_known_counter_selection_is_source_verified_end_to_end(cfg, tmp_path):
    parent = make_ready(cfg, tmp_path)
    prompt = discovery.PROMPT_PATH.read_text()
    parent["config"]["max_input_chars"] = max(discovery._size(prompt, discovery._context(
        [rid], parent["catalog"], parent["materials"], parent["owners"])) for rid in parent["catalog"]["cases"])
    atomic_write_json(parent["path"], parent["config"])

    def assign_counters(value, context):
        for index, row in enumerate(value["case_dispositions"]):
            row["disposition"] = "assigned"
            row["memberships"] = [{"family_id": f"F{index + 1}", "role": "context_only", "reason": "Keep synthetic counter context"}]
        return value

    result = discovery.execute(pin(parent["path"]), command="run", backend=FakeBackend(assign_counters))
    assert result["status"] == "completed" and result["packet_count"] > 2
    config = {k: v for k, v in parent["config"].items() if k != "inventory"}
    config.update(schema_version="knowledge_family_routing_config.v1", run_id="forced_routing",
                  output_root=str(tmp_path / "forced"), max_input_chars=200000, max_calls=100,
                  max_response_entries=4, audit_pairs=[],
                  parent={"config": pin(parent["path"]), "artifact": pin(parent["root"] / "discovery.json")})
    path = tmp_path / "forced-routing.json"
    atomic_write_json(path, config)
    assert routing.execute(pin(path), command="run", backend=RouteModel(negative=True))["status"] == "completed"
    artifact = Path(config["output_root"]) / config["run_id"] / "discovery.json"
    value = read_json(artifact)
    assert value["selected_count"] > 0 and value["routing_counts"]["proposed_link"] == value["routing_counts"]["uncertain"] == 0
    assert all(origin["kind"] == "known_counter" for row in value["selection"] for origin in row["origins"])
    selected_config = {k: v for k, v in config.items() if k not in {"max_response_entries", "audit_pairs"}}
    selected_config.update(schema_version="knowledge_reconciliation_config.v2", run_id="forced_selected",
                           selection=pin(artifact), max_pairs=value["selected_count"])
    selected_path = tmp_path / "forced-selected.json"
    atomic_write_json(selected_path, selected_config)
    model = Model()
    done = verifier.execute(pin(selected_path), command="run", backend=model)
    assert done["status"] == "completed" and len(model.calls) == value["selected_count"]
    assert all(ctx["evidence_bundle"]["raw_regions"] and ctx["evidence_bundle"]["known_counter_obligations"] for ctx in model.calls)
    assert verifier.execute(pin(selected_path), command="validate")["status"] == "validated"


@pytest.mark.parametrize("mode", ["zero", "one", "same_packet", None])
def test_zero_selected_pairs_retains_items_no_global_claim(cfg, tmp_path, mode):
    data = make_routing(cfg, tmp_path, mode)
    model = RouteModel(negative=True)
    assert routing.execute(pin(data["path"]), command="run", backend=model)["status"] == "completed"
    value = read_json(data["root"] / "discovery.json")
    assert value["selected_count"] == 0
    chosen = selection_config(data, tmp_path)
    source_model = Model()
    result = verifier.execute(pin(chosen["path"]), command="run", backend=source_model)
    assert result["pair_coverage"] == "no_selected_pairs" and not source_model.calls
    final = read_json(chosen["root"] / "discovery.json")
    assert len(final["case_accounting"]) == 4 and final["future_challenge"]["status"] == "mandatory_not_performed"
    assert all(row["disposition"] == "retained_without_selected_comparison" for row in final["family_accounting"].values())
    assert verifier.execute(pin(chosen["path"]), command="validate")["status"] == "validated"


@pytest.mark.parametrize("fault", ["missing", "duplicate", "reverse", "foreign", "same_packet", "cell", "unknown_field"])
def test_routing_response_exact_partition(ready, fault):
    ctx = context_for(ready)
    value, _ = RouteModel().complete("", ctx, {})
    rows = value["pair_dispositions"]
    if fault == "missing": rows.pop()
    elif fault == "duplicate": rows.append(copy.deepcopy(rows[0]))
    elif fault == "reverse": rows[0]["family_keys"].reverse()
    elif fault == "foreign": rows[0]["family_keys"][0] = "unknown"
    elif fault == "same_packet": rows[0]["family_keys"] = [ctx["families"][0]["family_key"]] * 2
    elif fault == "cell": value["cell_sha256"] = "wrong"
    elif fault == "unknown_field": value["source_verified"] = True
    with pytest.raises(ValueError):
        routing._derive(value, ctx)


@pytest.mark.parametrize("fault", ["selection", "origins", "accounting", "parent", "future", "receipt", "count_type"])
def test_rebound_selection_and_receipt_tampering_rejected(ready, tmp_path, fault):
    routing.execute(pin(ready["path"]), command="run", backend=RouteModel())
    path = ready["root"] / "discovery.json"
    value = read_json(path)
    if fault == "selection": value["selection"].pop()
    elif fault == "origins": value["selection"][0]["origins"] = []
    elif fault == "accounting": value["case_accounting"].pop(next(iter(value["case_accounting"])))
    elif fault == "parent": value["parent"]["artifact"]["sha256"] = "0" * 64
    elif fault == "future": value["future_challenge"]["known_item_family_obligations"] = []
    elif fault == "count_type": value["selected_count"] = float(value["selected_count"])
    elif fault == "receipt":
        receipt = ready["root"] / "packet_000000001/attempt_0001.result.json"
        receipt_value = read_json(receipt)
        receipt_value["response"]["pair_dispositions"].pop()
        atomic_write_json(receipt, receipt_value)
    atomic_write_json(path, value)
    data = selection_config(ready, tmp_path)
    with pytest.raises(ValueError):
        verifier.execute(pin(data["path"]))
    assert not data["root"].exists()


def test_routing_partial_resume_lifetime_budget_and_interruption(ready):
    ready["config"]["max_calls"] = 1
    ref = save(ready)
    assert not routing.execute(ref)["packet_plan"]["first_pass_fits_call_budget"]
    model = RouteModel()
    assert routing.execute(ref, command="run", backend=model)["status"] == "call_budget_exhausted"
    assert routing.execute(ref, command="run", backend=model)["status"] == "call_budget_exhausted"
    assert len(model.calls) == 1
    with pytest.raises(ValueError, match="incomplete"):
        routing.execute(ref, command="validate")
    ready["config"]["max_calls"] = 8
    with pytest.raises(ValueError, match="new run"):
        routing.execute(save(ready), command="run", backend=model)


def test_interrupted_routing_is_not_blindly_retried(ready):
    class Interrupted:
        def complete(self, *args):
            raise KeyboardInterrupt()
    ref = pin(ready["path"])
    with pytest.raises(KeyboardInterrupt):
        routing.execute(ref, command="run", backend=Interrupted())
    model = RouteModel()
    result = routing.execute(ref, command="run", backend=model, retry_failed=True)
    assert result["status"] == "interrupted_needs_inspection" and result["admitted_calls"] == 1
    assert not model.calls


@pytest.mark.parametrize("field,value", [("max_response_entries", 0), ("max_response_entries", None), ("audit_pairs", [["unknown", "unknown2"]])])
def test_explicit_response_limit_and_audit_contract(ready, field, value):
    if value is None:
        ready["config"].pop(field)
    else:
        ready["config"][field] = value
    with pytest.raises(ValueError):
        routing.execute(save(ready))
    assert not ready["root"].exists()


def test_routing_overflows_fail_without_truncation(ready):
    ready["config"]["max_input_chars"] = 100
    assert routing.execute(save(ready))["status"] == "incomplete_descriptor_overflow"
    assert not ready["root"].exists()
    ready["config"]["max_input_chars"] = 200000
    ready["config"]["max_response_chars"] = 100
    assert routing.execute(save(ready), command="run", backend=RouteModel())["status"] == "failed_needs_explicit_retry"
    receipt = read_json(ready["root"] / "packet_000000001/attempt_0001.result.json")
    assert receipt["error"]["phase"] == "validation" and receipt["response"]["pair_dispositions"]
    assert not (ready["root"] / "discovery.json").exists()


def test_selected_source_drift_and_protected_routing_output(ready, tmp_path):
    routing.execute(pin(ready["path"]), command="run", backend=RouteModel())
    data = selection_config(ready, tmp_path)
    data["config"]["output_root"] = str(ready["root"])
    with pytest.raises(ValueError, match="overlap"):
        verifier.execute(save(data))
    data["config"]["output_root"] = str(tmp_path / "selected")
    source = Path(ready["original"]["parent"]["source_cfg"]["episodes"][0]["raw"])

    def mutate(value, context):
        raw = read_json(source)
        raw["segments"][0]["text"] += " synthetic drift"
        atomic_write_json(source, raw)
        return value

    assert verifier.execute(save(data), command="run", backend=Model(mutate))["status"] == "failed_needs_explicit_retry"
    result = read_json(data["root"] / "packet_000000001/attempt_0001.result.json")
    assert result["error"]["phase"] == "source_validation" and result["response"]
    assert not (data["root"] / "discovery.json").exists()


def test_failed_routing_retry_preserves_receipt(ready):
    model = RouteModel(lambda value, ctx: {**value, "pair_dispositions": []})
    ref = pin(ready["path"])
    assert routing.execute(ref, command="run", backend=model)["status"] == "failed_needs_explicit_retry"
    receipt = ready["root"] / "packet_000000001/attempt_0001.result.json"
    before = receipt.read_bytes()
    assert routing.execute(ref, command="run", backend=RouteModel())["status"] == "failed_needs_explicit_retry"
    assert routing.execute(ref, command="run", backend=RouteModel(), retry_failed=True)["status"] == "completed"
    assert receipt.read_bytes() == before


def test_selected_partial_resume_and_overflow(ready, tmp_path):
    routing.execute(pin(ready["path"]), command="run", backend=RouteModel())
    data = selection_config(ready, tmp_path)
    data["config"]["max_input_chars"] = 100
    result = verifier.execute(save(data))
    assert result["status"] == "incomplete_input_overflow" and result["blocked_pair_count"] == 2
    assert result["selection"] == data["config"]["selection"] and not data["root"].exists()
    data["config"]["max_input_chars"] = 200000
    data["config"]["max_pairs"] = 1
    assert verifier.execute(save(data))["status"] == "incomplete_pair_limit"
    data["config"]["max_pairs"] = 2
    ref = save(data)
    model = Model()
    result = verifier.execute(ref, command="run", backend=model, max_packets=1)
    assert result["pair_coverage"] == "partial" and result["pending_selected_pair_count"] == 1
    assert verifier.execute(ref, command="run", backend=model)["pair_coverage"] == "selected_pairs_complete"
    assert len(model.calls) == 2


def test_cli_real_plan_run_validate_synthetic_backends(ready, tmp_path, capsys, monkeypatch):
    original = routing.execute
    monkeypatch.setattr(routing, "execute", lambda *a, **kw: original(*a, **kw, backend=RouteModel()))
    args = ["--config", str(ready["path"]), "--sha256", pin(ready["path"])["sha256"]]
    for command in ("plan", "run", "validate"):
        assert routing.main([command, *args]) == 0
    data = selection_config(ready, tmp_path)
    original_selected = selected.execute
    monkeypatch.setattr(selected, "execute", lambda *a, **kw: original_selected(*a, **{**kw, "backend": Model()}))
    args = ["--config", str(data["path"]), "--sha256", pin(data["path"])["sha256"]]
    for command in ("plan", "run", "validate"):
        assert verifier.main([command, *args]) == 0
    assert "validated" in capsys.readouterr().out
