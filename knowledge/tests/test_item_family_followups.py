"""Synthetic pair provenance and relationship consistency; no live inference."""
import copy
from pathlib import Path

import pytest

from knowledge import family_reconciliation as pairs, family_routing, family_verification
from knowledge import item_family_challenge as challenge, item_family_verification as verification
from knowledge.tests.test_item_family_challenge import (
    ready, save, Inspect, Verify, context_for, verification_config, no_provider)
from knowledge.tests.test_family_reconciliation import Model
from knowledge.tests.test_family_routing import RouteModel
from knowledge.tests.test_corpus_inventory import pin
from knowledge.tests.test_pipeline import cfg
from transcription.common import atomic_write_json, read_json


def add_followup(data, tmp_path, *, selected=False):
    parent = data["parent"]
    config, path, root, adapter = parent["config"], parent["path"], parent["root"], pairs
    if selected:
        routing_cfg = {k: v for k, v in config.items() if k != "max_pairs"}
        routing_cfg.update(schema_version="knowledge_family_routing_config.v1", run_id="routing",
                           output_root=str(tmp_path / "routing"), max_response_entries=2, audit_pairs=[])
        routing_path = tmp_path / "routing.json"
        atomic_write_json(routing_path, routing_cfg)
        family_routing.execute(pin(routing_path), command="run", backend=RouteModel())
        config = {**config, "schema_version": "knowledge_reconciliation_config.v2", "run_id": "selected",
                  "output_root": str(tmp_path / "selected"),
                  "selection": pin(tmp_path / "routing/routing/discovery.json")}
        path, root, adapter = tmp_path / "pair-selected.json", tmp_path / "selected/selected", family_verification
        atomic_write_json(path, config)
    assert adapter.execute(pin(path), command="run", backend=Model())["status"] == "completed"
    _, _, catalog, _, _, _, _ = challenge._prepare(pin(data["path"]))
    for request_path in sorted(root.glob("packet_*/attempt_0001.request.json")):
        request = read_json(request_path)
        result_path = request_path.with_name("attempt_0001.result.json")
        response = read_json(result_path)["response"]
        for row in response["case_dispositions"]:
            for key in request["context"]["family_keys"]:
                if not challenge._known(row["record_id"], key, catalog):
                    followup = {"config": pin(path), "artifact": pin(root / "discovery.json"),
                                "result": pin(result_path), "record_id": row["record_id"], "family_key": key}
                    data["config"]["pair_review_followups"] = [followup]
                    save(data)
                    return followup, catalog
    raise AssertionError("Synthetic pair fixture must have a cross-family item without an L3 link")


@pytest.fixture
def followup(ready, tmp_path):
    row, catalog = add_followup(ready, tmp_path)
    return ready, row, catalog


@pytest.mark.parametrize("selected", [False, True])
def test_negative_followup_forced_union_real_pair_provenance(ready, tmp_path, selected):
    row, _ = add_followup(ready, tmp_path, selected=selected)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert challenge.execute(pin(ready["path"]))["status"] == "planned"
    assert all(p.read_bytes() == b for p, b in before.items()) and not ready["root"].exists()
    challenge.execute(pin(ready["path"]), command="run", backend=Inspect())
    value, cat, cells, histories, manifest = challenge.load_completed(pin(ready["root"] / "discovery.json"), {})
    chosen = next(r for r in challenge.selected_rows(cat, cells, histories, manifest)
                  if (r["record_id"], r["family_key"]) == (row["record_id"], row["family_key"]))
    assert chosen["challenge_disposition"] == "no_link_found"
    assert len(chosen["origins"]) == 1 and chosen["origins"][0]["kind"] == "forced_pair_review_followup"
    origin = chosen["origins"][0]["obligation"]
    assert origin["review"] == row and "not_model_requested" in origin["authority"]
    response = read_json(Path(row["result"]["path"]))["response"]
    assert origin["case_disposition"] == next(r for r in response["case_dispositions"] if r["record_id"] == row["record_id"])
    assert origin["relationship"] == response["relationship"] and "source_principle_id" not in origin
    verify = verification_config(ready, tmp_path)
    cfg, _, catalog, packets, _, pins, prompt = verification._prepare(pin(verify["path"]))
    packet = next(p for p in packets if (p["record_id"], p["family_key"]) == (row["record_id"], row["family_key"]))
    ctx = verification._materialize(packet, catalog, pins, prompt, cfg["max_input_chars"])
    assert any(o["kind"] == "pair_review_followup" for o in ctx["known_obligations"])
    answer, _ = Verify().complete("", ctx, {})
    assert verification._derive(answer, ctx) == answer
    answer["known_dispositions"] = []
    with pytest.raises(ValueError, match="Missing reopened"):
        verification._derive(answer, ctx)
    assert value["schema_version"] == "knowledge_item_family_selection.v2"
    assert verification.execute(pin(verify["path"]), command="run", backend=Verify())["status"] == "completed"
    assert verification.execute(pin(verify["path"]), command="validate")["status"] == "validated"
    assert verification.execute(pin(verify["path"]), command="run", backend=Verify())["model_calls_this_execution"] == 0


@pytest.mark.parametrize("fault", ["duplicate", "parent", "result_pin", "result_content", "artifact",
    "wrong_result_location", "target_not_reviewed", "item_not_reviewed", "overlap", "uncompleted"])
def test_followups_reject_unbound_or_altered_pair_provenance(followup, tmp_path, fault):
    data, row, catalog = followup
    if fault == "duplicate": data["config"]["pair_review_followups"].append(copy.deepcopy(row))
    elif fault == "parent":
        config = read_json(Path(row["config"]["path"]))
        config["parent"]["artifact"]["sha256"] = "0" * 64
        path = tmp_path / "wrong-parent.json"
        atomic_write_json(path, config)
        row["config"] = pin(path)
    elif fault in {"result_pin", "result_content"}:
        path = Path(row["result"]["path"])
        result = read_json(path)
        result["response"]["reason"] += " Altered original judgment"
        atomic_write_json(path, result)
        if fault == "result_content": row["result"] = pin(path)
    elif fault == "artifact":
        path = Path(row["artifact"]["path"])
        value = read_json(path)
        value["unresolved_pair_count"] += 1
        atomic_write_json(path, value)
        row["artifact"] = pin(path)
    elif fault == "wrong_result_location":
        path = tmp_path / "copied-result.json"
        atomic_write_json(path, read_json(Path(row["result"]["path"])))
        row["result"] = pin(path)
    elif fault == "target_not_reviewed":
        keys = read_json(Path(row["result"]["path"]))["response"]["family_keys"]
        row["family_key"] = next(k for k in catalog["families"] if k not in keys)
    elif fault == "item_not_reviewed":
        row["record_id"] = "FOREIGN:R1"
    elif fault == "overlap":
        data["config"]["output_root"] = str(Path(row["artifact"]["path"]).parent)
    else:
        root = Path(row["artifact"]["path"]).parent
        path = sorted(root.glob("packet_*/attempt_0001.result.json"))[-1]
        path.unlink()
    save(data)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError): challenge.execute(pin(data["path"]))
    assert all(p.read_bytes() == b for p, b in before.items()) and not data["root"].exists()


def relation_context():
    def case(rid, sid):
        return {"record_id": rid, "episode_id": "SYN", "speaker_attribution": "host",
                "evidence": {"source_segment_ids": [sid], "source_segment_id_ranges": []}}
    return {"record_id": "SYN:item", "family_key": "family", "context_sha256": "a" * 64,
            "family": {"memberships": {"SYN:target": {"role": "potential_support"}}},
            "known_obligations": [], "evidence_bundle": {
                "cases": [case("SYN:item", 0), case("SYN:target", 1), case("SYN:closure", 2)],
                "raw_regions": {"SYN": [{"segment_id": i, "start": i, "end": i + 1,
                                          "text": f"Synthetic conditional statement {i}"} for i in range(3)]},
                "known_counter_obligations": []}}


@pytest.mark.parametrize("relation", ["support", "counter", "retelling", "evolution"])
@pytest.mark.parametrize("excluded", ["SYN:item", "SYN:target"])
def test_positive_relation_requires_item_and_actual_member_retained(relation, excluded):
    ctx = relation_context()
    value, _ = Verify().complete("", ctx, {})
    value["relationship"] = relation
    assert verification._derive(value, ctx) == value
    next(r for r in value["case_dispositions"] if r["record_id"] == excluded)["disposition"] = "context_excluded"
    with pytest.raises(ValueError, match="actual target member retained"):
        verification._derive(value, ctx)


@pytest.mark.parametrize("fault", ["counter_only", "nonhost", "closure_not_member"])
def test_support_needs_actual_host_support_not_just_retained_closure(fault):
    ctx = relation_context()
    if fault == "counter_only": ctx["family"]["memberships"]["SYN:target"]["role"] = "potential_counter"
    elif fault == "nonhost": ctx["evidence_bundle"]["cases"][1]["speaker_attribution"] = "listener"
    value, _ = Verify().complete("", ctx, {})
    value["relationship"] = "support"
    if fault == "closure_not_member": value["case_dispositions"][1]["disposition"] = "context_excluded"
    with pytest.raises(ValueError): verification._derive(value, ctx)


def test_context_difference_can_explain_exclusion_without_a_positive_link():
    ctx = relation_context()
    value, _ = Verify().complete("", ctx, {})
    for row in value["case_dispositions"]: row["disposition"] = "context_excluded"
    assert verification._derive(value, ctx) == value


@pytest.mark.parametrize("adapter,version", [(challenge, "knowledge_item_family_challenge_config.v1"),
                                            (verification, "knowledge_item_family_verification_config.v1")])
def test_old_config_rejected_with_migration_guidance_before_loading(ready, adapter, version):
    ready["config"]["schema_version"] = version
    with pytest.raises(ValueError, match="historical code.*new v2"):
        adapter.execute(save(ready))
    assert not ready["root"].exists()
