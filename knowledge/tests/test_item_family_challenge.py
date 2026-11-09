"""Synthetic global item sweep and source verification; never admit real providers."""
import copy
import itertools
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge import item_family_challenge as challenge, item_family_verification as verification
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_family_reconciliation import setup, anchor_for
from .test_corpus_inventory import pin
from .test_pipeline import cfg


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Synthetic tests only"))
    monkeypatch.setattr(challenge.runner.codex_backend, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


def make_ready(cfg, tmp_path, mode=None):
    parent = setup(cfg, tmp_path, mode)
    config = {**parent["config"], "schema_version": "knowledge_item_family_challenge_config.v2",
        "run_id": "challenge", "output_root": str(tmp_path / "challenge"), "max_calls": 100,
        "max_items_per_page": 2, "max_families_per_page": 3, "max_response_entries": 3, "audit_pairs": [],
        "pair_review_followups": []}
    config.pop("max_pairs")
    path = tmp_path / "challenge-config.json"
    atomic_write_json(path, config)
    return {"config": config, "path": path, "root": Path(config["output_root"]) / config["run_id"], "parent": parent}


@pytest.fixture
def ready(cfg, tmp_path):
    return make_ready(cfg, tmp_path)


def save(data):
    atomic_write_json(data["path"], data["config"])
    return pin(data["path"])


def context_for(data, adapter=challenge):
    cfg, _, catalog, packets, manifest, pins, prompt = adapter._prepare(pin(data["path"]))
    packet = next(iter(packets))
    return adapter._materialize(packet, catalog, pins, prompt, cfg["max_input_chars"])


class Inspect:
    def __init__(self, transform=None, disposition="no_link_found"):
        self.transform, self.disposition, self.calls = transform, disposition, 0

    def complete(self, prompt, ctx, schema):
        self.calls += 1
        value = {"cell_sha256": ctx["cell_sha256"], "pair_dispositions": [
            {"record_id": p["record_id"], "family_key": p["family_key"], "disposition": self.disposition,
             "reason": "Synthetic scoped inspection, not evidence of absence",
             "source_anchors": [anchor_for(ctx["evidence_bundle"], p["record_id"])],
             "obligation_dispositions": [{"obligation_id": o["obligation_id"],
                "disposition": "contested_for_verification", "reason": "Reopen original sources"}
                for o in p["known_obligations"]]} for p in ctx["pairs"]]}
        return self.transform(value, ctx) if self.transform else value, {"provider": "synthetic"}


class Verify:
    def __init__(self, transform=None):
        self.transform, self.calls = transform, 0

    def complete(self, prompt, ctx, schema):
        self.calls += 1
        bundle = ctx["evidence_bundle"]

        def row(identity, key, rid):
            return {identity: key, "disposition": "retained", "reason": "Synthetic source comparison",
                    "source_anchors": [anchor_for(bundle, rid)]}

        value = {k: ctx[k] for k in ("context_sha256", "record_id", "family_key")}
        value.update(relationship="context_difference", reason="Synthetic condition differs", conditions=["Fictional context"],
            limitations=["Not a principle or accepted source"],
            case_dispositions=[row("record_id", c["record_id"], c["record_id"]) for c in bundle["cases"]],
            counter_dispositions=[row("obligation_id", o["obligation_id"], o["record_id"]) for o in bundle["known_counter_obligations"]],
            known_dispositions=[row("obligation_id", o["obligation_id"], ctx["record_id"]) for o in ctx["known_obligations"]])
        return self.transform(value, ctx) if self.transform else value, {"provider": "synthetic"}


def verification_config(ready, tmp_path):
    config = {k: v for k, v in ready["config"].items()
              if k not in ("max_items_per_page", "max_families_per_page", "max_response_entries", "audit_pairs", "pair_review_followups")}
    config.update(schema_version="knowledge_item_family_verification_config.v2", run_id="verify",
                  output_root=str(tmp_path / "verification"), selection=pin(ready["root"] / "discovery.json"), max_pairs=100)
    path = tmp_path / "verify-config.json"
    atomic_write_json(path, config)
    return {"config": config, "path": path, "root": Path(config["output_root"]) / config["run_id"]}


def test_exact_every_item_cartesian_readonly_plan_and_source_scope(ready, tmp_path):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = challenge.execute(pin(ready["path"]))
    cfg, _, catalog, packets, manifest, pins, prompt = challenge._prepare(pin(ready["path"]))
    assert not isinstance(packets, list) and result["pair_count"] == 16
    assert manifest["implementation"]["runner_sha256"] == sha256_file(Path(challenge.runner.__file__))
    pairs = []
    for packet in packets:
        assert "evidence_bundle" not in packet
        ctx = challenge._materialize(packet, catalog, pins, prompt, cfg["max_input_chars"])
        assert len(ctx["pairs"]) <= 3 and challenge._size(prompt, ctx) <= cfg["max_input_chars"]
        assert ctx["evidence_bundle"]["raw_regions"]
        assert any(not p["qualification"]["eligible_for_level4"] for p in ctx["evidence_bundle"]["principles"])
        assert all(s["source"]["prior_source_decision"] == "uncertain" for s in ctx["evidence_bundle"]["sources"].values())
        pairs.extend((p["record_id"], p["family_key"]) for p in ctx["pairs"])
    assert len(pairs) == len(set(pairs)) == 16
    assert set(pairs) == set(itertools.product(catalog["inventory"]["cases"], catalog["families"]))
    assert {p: p.read_bytes() for p in before} == before and not ready["root"].exists()


def test_negative_cannot_drop_forced_counters_and_verification_both_sides(ready, tmp_path):
    model = Inspect()
    ref = pin(ready["path"])
    assert challenge.execute(ref, command="run", backend=model)["status"] == "completed"
    value, catalog, cells, histories, manifest = challenge.load_completed(pin(ready["root"] / "discovery.json"), {})
    assert value["disposition_counts"]["no_link_found"] == 16 and value["source_verified_pair_count"] == 0
    selected = list(challenge.selected_rows(catalog, cells, histories, manifest))
    assert selected and len(selected) == value["selected_count"]
    assert any(o["obligation"].get("parent_eligible_for_level4") is False for r in selected
               for o in r["origins"] if o["kind"] == "forced_known_obligation")
    assert any(not catalog["parent"]["case_accounting"][r["record_id"]]["memberships"] for r in selected)
    v = verification_config(ready, tmp_path)
    cfg, _, cat, packets, _, pins, prompt = verification._prepare(pin(v["path"]))
    for packet in packets:
        ctx = verification._materialize(packet, cat, pins, prompt, cfg["max_input_chars"])
        members = set(ctx["family"]["memberships"])
        assert members | {ctx["record_id"]} <= {c["record_id"] for c in ctx["evidence_bundle"]["cases"]}
        assert ctx["direct_L2_provenance"]["artifact_sha256"] and ctx["direct_L2_provenance"]["content_pointer"]
        assert "source_principle_id" not in ctx["direct_L2_provenance"]
    assert verification.execute(pin(v["path"]), command="run", backend=Verify())["status"] == "completed"
    assert verification.execute(pin(v["path"]), command="validate")["status"] == "validated"
    final = read_json(v["root"] / "discovery.json")
    assert final["case_accounting"] == value["case_accounting"]
    assert final["source_verified_pair_count"] == len(selected) and final["pending_selected_pair_count"] == 0
    assert final["policy"]["independent_event_count"] is None and not final["policy"]["publication_authorized"]
    assert not {"merged_families", "principles", "new_memberships"} & final.keys()


@pytest.mark.parametrize("disposition", challenge.DISPOSITIONS[:-1])
def test_all_positive_uncertain_kinds_select_exact_universe(ready, disposition):
    challenge.execute(pin(ready["path"]), command="run", backend=Inspect(disposition=disposition))
    value = read_json(ready["root"] / "discovery.json")
    assert value["selected_count"] == value["schedule"]["pair_count"] == 16


@pytest.mark.parametrize("fault", ["missing", "duplicate", "foreign", "cell", "anchor", "obligation", "extra"])
def test_exact_pair_obligation_and_anchor_partition(ready, fault):
    ctx = context_for(ready)
    value, _ = Inspect().complete("", ctx, {})
    if fault == "missing": value["pair_dispositions"].pop()
    elif fault == "duplicate": value["pair_dispositions"].append(copy.deepcopy(value["pair_dispositions"][0]))
    elif fault == "foreign": value["pair_dispositions"][0]["family_key"] = "old-version:F1"
    elif fault == "cell": value["cell_sha256"] = "0" * 64
    elif fault == "anchor": value["pair_dispositions"][0]["source_anchors"][0]["text"] += " invented"
    elif fault == "obligation":
        row = next(r for r in value["pair_dispositions"] if r["obligation_dispositions"])
        row["obligation_dispositions"].pop()
    elif fault == "extra": value["approved"] = True
    with pytest.raises(ValueError):
        challenge._derive(value, ctx)


@pytest.mark.parametrize("fault", ["case", "counter", "known", "anchor", "unresolved", "context", "nonhost"])
def test_verification_exact_closure_and_unresolved(ready, tmp_path, fault):
    challenge.execute(pin(ready["path"]), command="run", backend=Inspect())
    data = verification_config(ready, tmp_path)
    ctx = context_for(data, verification)
    value, _ = Verify().complete("", ctx, {})
    if fault == "case": value["case_dispositions"].pop()
    elif fault == "counter": value["counter_dispositions"].pop()
    elif fault == "known": value["known_dispositions"].pop()
    elif fault == "anchor": value["case_dispositions"][0]["source_anchors"][0]["start"] += 1
    elif fault == "unresolved": value["case_dispositions"][0]["disposition"] = "unresolved"
    elif fault == "context": value["context_sha256"] = "0" * 64
    elif fault == "nonhost":
        next(c for c in ctx["evidence_bundle"]["cases"] if c["record_id"] == ctx["record_id"])["speaker_attribution"] = "listener"
        value["relationship"] = "support"
    with pytest.raises(ValueError):
        verification._derive(value, ctx)


def test_no_targets_keeps_every_item_and_zero_calls(cfg, tmp_path):
    data = make_ready(cfg, tmp_path, "zero")
    backend = Inspect()
    assert challenge.execute(pin(data["path"]), command="run", backend=backend)["coverage"] == "no_targets"
    value = read_json(data["root"] / "discovery.json")
    assert len(value["case_accounting"]) == 4 and value["selected_count"] == 0 and backend.calls == 0
    v = verification_config(data, tmp_path)
    model = Verify()
    assert verification.execute(pin(v["path"]), command="run", backend=model)["coverage"] == "no_targets"
    assert model.calls == 0 and read_json(v["root"] / "discovery.json")["status"] == "no_targets"


def test_audit_negative_union_and_no_target_change_reuse(ready):
    _, _, catalog, _, _, _, _ = challenge._prepare(pin(ready["path"]))
    pair = next((rid, key) for rid in catalog["inventory"]["cases"] for key in catalog["families"]
                if not challenge._known(rid, key, catalog))
    ready["config"]["audit_pairs"] = [{"record_id": pair[0], "family_key": pair[1]}]
    ref = save(ready)
    model = Inspect()
    challenge.execute(ref, command="run", backend=model)
    value, cat, cells, history, manifest = challenge.load_completed(pin(ready["root"] / "discovery.json"), {})
    row = next(r for r in challenge.selected_rows(cat, cells, history, manifest) if (r["record_id"], r["family_key"]) == pair)
    assert row["origins"] == [{"kind": "explicit_audit", "config": ref}]
    assert challenge.execute(ref, command="run", backend=model)["model_calls_this_execution"] == 0
    ready["config"]["max_response_entries"] += 1
    with pytest.raises(ValueError, match="new run"):
        challenge.execute(save(ready), command="run", backend=model)


def test_partial_budget_interrupt_retry_and_receipt_only_history(ready):
    ref = pin(ready["path"])
    model = Inspect()
    assert challenge.execute(ref, command="run", backend=model, max_packets=1)["pending_cells"] > 0
    with pytest.raises(ValueError, match="incomplete"):
        challenge.execute(ref, command="validate")
    _, root, cat, packets, manifest, pins, prompt = challenge._prepare(ref)
    history, admissions = challenge.runner.read_history(challenge, root, manifest, packets, prompt, cat, pins)
    assert admissions == 1 and all("context" not in entry and "response" not in entry for rows in history.values() for entry in rows)
    assert challenge.execute(ref, command="run", backend=model)["status"] == "completed"
    assert challenge.execute(ref, command="validate")["status"] == "validated"


def test_lifetime_budget_never_reset(ready):
    ready["config"]["max_calls"] = 1
    ref = save(ready)
    model = Inspect()
    for _ in range(2):
        result = challenge.execute(ref, command="run", backend=model)
        assert result["status"] == "call_budget_exhausted" and result["admitted_calls"] == 1
    assert model.calls == 1 and result["pending_cells"] > 0


def test_interrupted_request_requires_inspection(ready):
    class Interrupted:
        def complete(self, *args):
            raise KeyboardInterrupt()
    ref = pin(ready["path"])
    with pytest.raises(KeyboardInterrupt):
        challenge.execute(ref, command="run", backend=Interrupted())
    model = Inspect()
    assert challenge.execute(ref, command="run", backend=model, retry_failed=True)["status"] == "interrupted_needs_inspection"
    assert model.calls == 0


@pytest.mark.parametrize("fault", ["selection", "receipt", "source"])
def test_tamper_cannot_rebind_selection(ready, tmp_path, fault):
    challenge.execute(pin(ready["path"]), command="run", backend=Inspect())
    path = ready["root"] / "discovery.json"
    if fault == "selection":
        value = read_json(path); value["selected_count"] -= 1; atomic_write_json(path, value)
    elif fault == "receipt":
        receipt = ready["root"] / "packet_000000001/attempt_0001.result.json"
        value = read_json(receipt); value["response"]["pair_dispositions"].pop(); atomic_write_json(receipt, value)
    else:
        source = Path(ready["parent"]["parent"]["source_cfg"]["episodes"][0]["raw"])
        value = read_json(source); value["segments"][0]["text"] += " drift"; atomic_write_json(source, value)
    v = verification_config(ready, tmp_path)
    with pytest.raises(ValueError):
        verification.execute(pin(v["path"]))
    assert not v["root"].exists()


def test_overflow_exact_cap_and_complete_closure(ready, tmp_path):
    cfg, _, cat, packets, _, pins, prompt = challenge._prepare(pin(ready["path"]))
    sizes = [challenge._size(prompt, challenge._context({"item_ids": [rid], "family_keys": [key]}, cat, pins))
             for rid in cat["inventory"]["cases"] for key in cat["families"]]
    ready["config"].update(max_input_chars=max(sizes), max_response_entries=1)
    assert challenge.execute(save(ready))["status"] == "planned"
    ready["config"]["max_input_chars"] = max(sizes) - 1
    assert challenge.execute(save(ready))["status"] == "incomplete_input_overflow"
    assert not ready["root"].exists()
    ready["config"]["max_input_chars"] = 200000
    challenge.execute(save(ready), command="run", backend=Inspect())
    v = verification_config(ready, tmp_path)
    v["config"]["max_pairs"] = 1
    assert verification.execute(save(v))["status"] == "incomplete_selected_pair_limit"
    v["config"].update(max_pairs=100, max_input_chars=100)
    blocked = verification.execute(save(v))
    assert blocked["status"] == "incomplete_input_overflow" and blocked["required_case_ids"]
    assert not v["root"].exists()


def test_failed_response_retry_and_output_cap(ready):
    ref = pin(ready["path"])
    bad = Inspect(lambda value, ctx: {**value, "pair_dispositions": []})
    assert challenge.execute(ref, command="run", backend=bad)["status"] == "failed_needs_explicit_retry"
    path = ready["root"] / "packet_000000001/attempt_0001.result.json"
    before = path.read_bytes()
    assert challenge.execute(ref, command="run", backend=Inspect(), retry_failed=True)["status"] == "completed"
    assert path.read_bytes() == before
    cfg = {**ready["config"], "max_response_chars": 1}
    ctx = context_for(ready)
    value, _ = Inspect().complete("", ctx, {})
    with pytest.raises(ValueError, match="response exceeds"):
        challenge.runner.check_response(challenge, value, ctx, cfg)


def test_closure_follows_counter_only_and_transitive_ineligible_links():
    catalog = {"inventory": {"cases": {k: {} for k in "abc"}, "principles": {
        "P1": {"supporting_records": ["a"], "counterexamples": [{"record_id": "b"}], "contradictions": []},
        "P2": {"supporting_records": ["b"], "counterexamples": [], "contradictions": [{"record_id": "c"}]}}},
        "principles_by_case": {"a": {"P1"}, "b": {"P1", "P2"}, "c": {"P2"}}}
    assert challenge._closure(["c"], catalog) == ["a", "b", "c"]


def test_known_obligations_include_reverse_counter_membership():
    catalog = {"families": {"target": {"memberships": {"counter": {"role": "potential_counter"}}}},
        "inventory": {"principles": {"P1": {"supporting_records": ["support"],
            "counterexamples": [{"record_id": "counter", "reason": "Opposite context"}],
            "contradictions": [], "qualification": {"eligible_for_level4": False}}}},
        "principles_by_case": {"support": {"P1"}, "counter": {"P1"}}}
    rows = challenge._known("support", "target", catalog)
    assert len(rows) == 1 and rows[0]["source_principle_id"] == "P1"
    assert rows[0]["target_linked_case_ids"] == ["counter"] and not rows[0]["parent_eligible_for_level4"]


def test_orphan_personal_listener_without_L3_is_still_inspected(ready):
    _, _, catalog, _, _, pins, _ = challenge._prepare(pin(ready["path"]))
    catalog = copy.deepcopy(catalog)
    catalog["inventory"]["principles"] = {}
    catalog["parent"]["principle_accounting"] = {}
    challenge._index(catalog)
    rid = next(r for r, row in catalog["parent"]["case_accounting"].items() if not row["memberships"])
    key = next(iter(catalog["families"]))
    materials = challenge.discovery._materials(catalog["inventory"], pins)

    class PersonalListener:
        def episode(self, eid):
            cases, principles, source, raw = copy.deepcopy(materials.episode(eid))
            if rid in cases:
                cases[rid]["speaker_attribution"] = "listener"
                cases[rid]["domains"] = ["personal"]
            return cases, principles, source, raw

    ctx = challenge._context({"item_ids": [rid], "family_keys": [key]}, catalog, pins, PersonalListener())
    assert ctx["item_ids"] == [rid] and ctx["pairs"][0]["known_obligations"] == []
    assert ctx["evidence_bundle"]["principles"] == []
    assert ctx["evidence_bundle"]["cases"][0]["speaker_attribution"] == "listener"
    assert ctx["evidence_bundle"]["cases"][0]["domains"] == ["personal"]
    value, _ = Inspect(disposition="uncertain").complete("", ctx, {})
    assert challenge._derive(value, ctx) == value


def test_schedule_has_no_whole_universe_pair_ceiling_or_matrix(monkeypatch):
    catalog = {"inventory": {"cases": {f"R{i:04d}": {} for i in range(1000)}},
               "families": {f"F{i}": {} for i in range(2)}}
    cfg = {"max_items_per_page": 10, "max_families_per_page": 2,
           "max_response_entries": 20, "max_input_chars": 100000}
    monkeypatch.setattr(challenge, "_context", lambda packet, *args: packet)
    packets = challenge.CellSchedule(catalog, {}, "", cfg)
    assert len(packets) == 100
    assert sum(len(p["item_ids"]) * len(p["family_keys"]) for p in packets) == 2000
    assert set(vars(packets)) == {"catalog", "pins", "prompt", "cfg", "item_pages", "family_pages",
                                  "count", "largest", "context_digest"}


def test_verification_partial_resume_and_unresolved_are_not_acceptance(ready, tmp_path):
    challenge.execute(pin(ready["path"]), command="run", backend=Inspect())
    data = verification_config(ready, tmp_path)
    ref = pin(data["path"])

    def unresolved(value, ctx):
        value["case_dispositions"][0]["disposition"] = "unresolved"
        value["relationship"] = "unresolved"
        return value

    model = Verify(unresolved)
    part = verification.execute(ref, command="run", backend=model, max_packets=1)
    assert part["pending_selected_pair_count"] > 0 and part["source_verified_pair_count"] == 1
    with pytest.raises(ValueError, match="incomplete"):
        verification.execute(ref, command="validate")
    final = verification.execute(ref, command="run", backend=model)
    assert final["pending_selected_pair_count"] == 0
    value = read_json(data["root"] / "discovery.json")
    assert value["status"] == "selected_verification_unresolved"
    assert value["unresolved_pair_count"] == value["selected_count"]
    assert not value["policy"]["eligible_for_serving"]
    assert verification.execute(ref, command="run", backend=model)["model_calls_this_execution"] == 0


def test_descriptor_mutation_and_implementation_pin_are_not_reused(ready, monkeypatch):
    ref = pin(ready["path"])
    challenge.execute(ref, command="run", backend=Inspect())
    original = challenge._implementation
    monkeypatch.setattr(challenge, "_implementation", lambda: {**original(), "changed": True})
    with pytest.raises(ValueError, match="new run"):
        challenge.execute(ref)
    monkeypatch.setattr(challenge, "_implementation", original)
    parent_path = Path(ready["config"]["parent"]["artifact"]["path"])
    parent = read_json(parent_path)
    parent["families"][0]["conditions"].append("Changed descriptor meaning")
    atomic_write_json(parent_path, parent)
    ready["config"]["parent"]["artifact"] = pin(parent_path)
    with pytest.raises(ValueError, match="reconstructed"):
        challenge.execute(save(ready))


@pytest.mark.parametrize("field,value", [("max_response_entries", 0), ("max_items_per_page", 0),
    ("max_families_per_page", 0), ("max_calls", 0), ("max_input_chars", True)])
def test_invalid_bounds_rejected_without_loading_sources(field, value):
    config = {"schema_version": "knowledge_item_family_challenge_config.v2", "run_id": "synthetic",
        "output_root": "/synthetic", "parent": {k: {"path": "/synthetic", "sha256": "0" * 64}
        for k in ("config", "artifact")}, "model": "synthetic", "reasoning_effort": "low",
        "timeout_seconds": 1, "max_input_chars": 100, "max_response_chars": 100, "max_calls": 1,
        "retry_count": 0, "max_response_entries": 1, "max_items_per_page": 1,
        "max_families_per_page": 1, "audit_pairs": [], "pair_review_followups": []}
    config[field] = value
    with pytest.raises(ValueError):
        challenge.discovery._validate(config, challenge.CONFIG)


def test_cli_both_stages_with_synthetic_models(ready, tmp_path, monkeypatch, capsys):
    original = challenge.execute
    monkeypatch.setattr(challenge, "execute", lambda *a, **kw: original(*a, **kw, backend=Inspect()))
    args = ["--config", str(ready["path"]), "--sha256", pin(ready["path"])["sha256"]]
    for command in ("plan", "run", "validate"):
        assert challenge.main([command, *args]) == 0
    v = verification_config(ready, tmp_path)
    original_v = verification.execute
    monkeypatch.setattr(verification, "execute", lambda *a, **kw: original_v(*a, **kw, backend=Verify()))
    args = ["--config", str(v["path"]), "--sha256", pin(v["path"])["sha256"]]
    for command in ("plan", "run", "validate"):
        assert verification.main([command, *args]) == 0
    assert "validated" in capsys.readouterr().out
