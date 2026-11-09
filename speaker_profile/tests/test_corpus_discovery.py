"""Synthetic native Profile packets and receipts; no providers or private corpus."""
import copy
import gc
import json
import weakref
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge import discovery_runner
from speaker_profile import corpus_discovery as discovery, corpus_inventory as inventory, pipeline, schemas
from speaker_profile import evidence_revision, recover_evidence, recover_synthesis, review, review_recovered
from transcription.common import atomic_write_json, read_json
from .test_pipeline import cfg, root, SyntheticModel, synthesis_proposal
from .test_review import ParentModel, make_parent, pin
from .test_corpus_inventory import selection_for, available, save as save_selection
from .test_recover_evidence import ContextModel
from .test_recover_synthesis import failed


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No provider or subprocess permitted"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr(discovery.codex_backend, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


class ContextParent(ParentModel):
    def complete(self, *args):
        value, usage = super().complete(*args)
        if self.calls[-1] == "evidence":
            for row in value["items"]:
                row["supplementary_notes"] = [{"merged_ids": [2], "note": "Fictional analyst context, not primary evidence"}]
            if value["episode_id"] == "SYN2":
                value["items"][1]["speaker_attribution"] = "guest"
        return value, usage


def configure(data, tmp_path):
    ref = inventory.freeze(pin(data["path"]), data["output"])
    catalog = inventory.load_checked(ref)
    prompt = discovery.PROMPT_PATH.read_text()
    materials = discovery._Materials(catalog, {})
    sizes = [discovery._size(prompt, discovery._context(entry["evidence_ids"], catalog, materials, ref,
                prompt=prompt, max_chars=1000000)) for entry in catalog["episodes"].values() if entry["evidence_ids"]]
    config = {"schema_version": "profile_discovery_config.v1", "run_id": "synthetic_discovery",
              "output_root": str(tmp_path / "discovery"), "inventory": ref, "model": "synthetic",
              "reasoning_effort": "high", "timeout_seconds": 10, "max_input_chars": max(sizes, default=10000) + 500,
              "max_response_chars": 100000, "max_calls": 5, "retry_count": 1}
    path = tmp_path / "discovery-config.json"
    atomic_write_json(path, config)
    return {"config": config, "path": path, "catalog": catalog, "source_cfg": data["cfg"]}


@pytest.fixture
def ready(cfg, tmp_path):
    cfg.update(episodes=cfg["episodes"][:2], commentary_scope=schemas.EPISODE_CONTEXT)
    pipeline.run(cfg, backend=ContextParent())
    return configure(selection_for(cfg, tmp_path), tmp_path)


def save(ready):
    atomic_write_json(ready["path"], ready["config"])
    return pin(ready["path"])


def proposal(context, families=True):
    values = [{"family_id": f"F{n}", "label": "Fictional conditional family", "conditional_pattern": "Limited fictional preference",
               "conditions": [], "accepted_costs": [], "limitations": ["Not independent events"]} for n in (1, 2)] if families else []
    first = context["evidence"][0]["evidence_id"]
    return {"families": values,
        "evidence_dispositions": [{"evidence_id": row["evidence_id"],
            "disposition": "assigned" if values and row["evidence_id"] == first else "retained_without_family",
            "memberships": [{"family_id": f["family_id"], "role": "uncertain", "reason": "Synthetic conditional relation"}
                            for f in values] if row["evidence_id"] == first else [], "reason": "Retain all supplied observations"}
            for row in context["evidence"]],
        "hypothesis_dispositions": [{"hypothesis_key": row["hypothesis_key"], "disposition": "retained_without_family",
                                     "family_ids": [], "reason": "Navigation only"} for row in context["hypotheses"]]}


class SyntheticDiscovery:
    def __init__(self, mutate=None):
        self.calls, self.mutate = 0, mutate

    def complete(self, prompt, context, schema):
        self.calls += 1
        assert schema == discovery.PROPOSAL
        context = discovery._expand(context)
        response = proposal(context)
        return self.mutate(response, context) if self.mutate else response, {"provider": "synthetic", "model_call_performed": False}


def packet_context(ready):
    cfg, _, catalog, packets, _, pins, prompt = discovery._prepare(pin(ready["path"]))
    return discovery._expand(discovery._materialize(packets[0], catalog, pins, prompt, cfg["max_input_chars"]))


def test_plan_owns_every_item_and_preserves_authority_closure_and_notes(ready, tmp_path, monkeypatch):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(pipeline, "run", Mock(side_effect=AssertionError("No extraction replay")))
    check = Mock(wraps=inventory.load_checked)
    monkeypatch.setattr(inventory, "load_checked", check)
    cfg, _, catalog, packets, manifest, pins, prompt = discovery._prepare(pin(ready["path"]))
    assert check.call_count == 1
    assert len(packets) == 2
    owned = [rid for p in packets for rid in p["owned_evidence_ids"]]
    navigation = [key for p in packets for key in p["owned_hypothesis_keys"]]
    assert len(owned) == len(set(owned)) == len(catalog["evidence"]) == 6
    assert set(navigation) == set(catalog["hypotheses"]) and len(navigation) == 2
    assert manifest["implementation"]["runner_sha256"] == discovery.sha256_file(Path(discovery_runner.__file__))
    for packet in packets:
        context = discovery._expand(discovery._materialize(packet, catalog, pins, prompt, cfg["max_input_chars"]))
        for row in context["evidence"]:
            index = catalog["evidence"][row["evidence_id"]]
            artifact = read_json(Path(index["artifact"]["path"]))
            assert row["grounding"] == artifact["grounding"]["items"][row["evidence_id"]]
            assert row["grounding"]["supplementary_context"][0]["authority"] == "model_analyst_not_speaker"
            assert row["grounding"]["supplementary_context"][0]["grounding"]["merged_ids"] == [2]
            assert row["merged_ids"] != [2]
            assert set(r["segment_id"] for r in context["raw_regions"][row["episode_id"]]) >= {2}
        assert context["policy"]["independent_event_count"] is None
        assert all(s["source"]["source_acceptance"] == "not_granted" for s in context["sources"].values())
    first = discovery._expand(discovery._materialize(packets[0], catalog, pins, prompt, cfg["max_input_chars"]))
    assert {o["kind"] for o in first["known_relationship_closure"]["obligations"]} == {
        "counterevidence", "statement_action_comparison"}
    assert any(row["evidence_kind"] == "inferred_tendency" for row in first["evidence"])
    assert all(row["domains"] == ["personal"] for row in first["evidence"])
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_run_resume_validate_exact_accounting_and_native_receipts(ready):
    model = SyntheticDiscovery()
    ref = pin(ready["path"])
    assert discovery.execute(ref, command="run", max_packets=1, backend=model)["status"] == "paused_at_packet_limit"
    result = discovery.execute(ref, command="run", backend=model)
    assert result["status"] == "completed" and model.calls == 2
    directory = root(ready["config"])
    artifact = read_json(directory / "discovery.json")
    assert set(artifact["evidence_accounting"]) == set(ready["catalog"]["evidence"])
    assert set(artifact["hypothesis_accounting"]) == set(ready["catalog"]["hypotheses"])
    assert all(row["owner_packet_id"] for row in artifact["evidence_accounting"].values())
    assert artifact["policy"]["independent_event_count"] is None
    assert artifact["policy"]["global_counter_review"] == "not_performed"
    assert not artifact["policy"]["eligible_for_serving"]
    request = read_json(directory / "packet_000001/attempt_0001.request.json")
    receipt = read_json(directory / "packet_000001/attempt_0001.result.json")
    assert request["schema_version"] == discovery.REQUEST_VERSION
    assert receipt["schema_version"] == discovery.RESULT_VERSION
    assert receipt["request_sha256"] == discovery.sha256_file(directory / "packet_000001/attempt_0001.request.json")
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    assert discovery.execute(ref, command="run")["model_calls_this_execution"] == 0
    assert discovery.execute(ref, command="validate")["status"] == "validated"
    assert {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("fault", ["missing", "duplicate", "foreign", "missing_hypothesis", "duplicate_hypothesis",
    "local_hypothesis_id", "unknown_family", "orphan_family", "disposition", "nonhost", "independence"])
def test_invalid_model_accounting_fails_closed(ready, fault):
    context = packet_context(ready)
    value = proposal(context)
    rows = value["evidence_dispositions"]
    if fault == "missing": rows.pop()
    elif fault == "duplicate": rows.append(copy.deepcopy(rows[0]))
    elif fault == "foreign": rows[0]["evidence_id"] = "OTHER:E1"
    elif fault == "missing_hypothesis": value["hypothesis_dispositions"].pop()
    elif fault == "duplicate_hypothesis": value["hypothesis_dispositions"].append(copy.deepcopy(value["hypothesis_dispositions"][0]))
    elif fault == "local_hypothesis_id": value["hypothesis_dispositions"][0]["hypothesis_key"] = "PROFILE:H1"
    elif fault == "unknown_family": rows[0]["memberships"][0]["family_id"] = "F99"
    elif fault == "orphan_family": rows[0]["memberships"].pop()
    elif fault == "disposition": rows[0]["disposition"] = "retained_without_family"
    elif fault == "nonhost":
        context["evidence"][0]["speaker_attribution"] = "guest"
        rows[0]["memberships"][0]["role"] = "potential_support"
    else: value["families"][0]["independent_event_count"] = 3
    with pytest.raises(ValueError): discovery._derive(value, discovery._pack(context))


def test_zero_families_and_zero_evidence_are_valid(ready, cfg, tmp_path):
    ref = pin(ready["path"])
    discovery.execute(ref, command="run", backend=SyntheticDiscovery(lambda v, c: proposal(c, False)))
    result = read_json(root(ready["config"]) / "discovery.json")
    assert result["families"] == [] and len(result["evidence_accounting"]) == 6
    cfg.update(run_id="empty_profile", episodes=cfg["episodes"][:1])
    pipeline.run(cfg, through="evidence", backend=SyntheticModel(empty=True))
    data = selection_for(cfg, tmp_path, synthesis=False)
    data["output"] = tmp_path / "empty-inventory/index.json"
    empty = configure(data, tmp_path)
    empty["config"]["run_id"] = "empty_discovery"
    result = discovery.execute(save(empty), command="run")
    assert result["status"] == "completed" and result["admitted_calls"] == result["packet_count"] == 0


def test_overflow_never_trims_relationship_context_or_admits(ready):
    ready["config"]["max_input_chars"] = 100
    with pytest.raises(ValueError, match="complete relationship/context closure.*no truncation"):
        discovery.execute(save(ready), command="run")
    assert not root(ready["config"]).exists()


def test_failed_validation_explicit_retry_and_lifetime_budget(ready):
    ready["config"].update(max_input_chars=1000000, max_calls=2)
    ref = save(ready)
    bad = SyntheticDiscovery(lambda v, c: {**v, "evidence_dispositions": []})
    assert discovery.execute(ref, command="run", backend=bad)["status"] == "failed_needs_explicit_retry"
    assert discovery.execute(ref, command="run")["model_calls_this_execution"] == 0
    receipt = root(ready["config"]) / "packet_000001/attempt_0001.result.json"
    original = receipt.read_bytes()
    good = SyntheticDiscovery()
    result = discovery.execute(ref, command="run", backend=good, retry_failed=True)
    assert result["status"] == "completed" and result["admitted_calls"] == 2 and good.calls == 1
    repair = read_json(receipt.with_name("attempt_0002.request.json"))
    assert repair["context"]["validation_repair"]["previous_response"]["evidence_dispositions"] == []
    assert receipt.read_bytes() == original


def test_interrupted_request_never_retries_or_resets_budget(ready):
    class Interrupted:
        def complete(self, *args):
            raise KeyboardInterrupt()
    ref = pin(ready["path"])
    with pytest.raises(KeyboardInterrupt): discovery.execute(ref, command="run", backend=Interrupted())
    result = discovery.execute(ref, command="run", retry_failed=True)
    assert result["status"] == "interrupted_needs_inspection" and result["admitted_calls"] == 1
    assert result["model_calls_this_execution"] == 0


@pytest.mark.parametrize("fault", ["request", "result", "artifact", "runner_pin", "unknown_receipt"])
def test_tampering_or_old_code_pins_never_resume(ready, monkeypatch, fault):
    ref = pin(ready["path"])
    discovery.execute(ref, command="run", backend=SyntheticDiscovery())
    folder = root(ready["config"])
    if fault == "runner_pin":
        changed = copy.deepcopy(discovery._implementation())
        changed.pop("runner_sha256")
        monkeypatch.setattr(discovery, "_implementation", lambda: changed)
    else:
        path = {"request": folder / "packet_000001/attempt_0001.request.json",
                "result": folder / "packet_000001/attempt_0001.result.json",
                "artifact": folder / "discovery.json", "unknown_receipt": folder / "packet_999999/attempt_0001.result.json"}[fault]
        value = read_json(path) if path.exists() else {}
        value["mutation"] = True
        atomic_write_json(path, value)
    with pytest.raises(ValueError): discovery.execute(ref, command="validate")


def test_cache_evicts_episode_payload_and_rereads_hashes(ready, monkeypatch):
    class Tracked(dict): pass
    refs, reads = [], []
    raw_paths = {e["raw"] for e in ready["source_cfg"]["episodes"]}
    original = discovery.shared._read
    def tracked(ref, pins):
        value = original(ref, pins)
        if ref["path"] in raw_paths:
            reads.append(ref["path"])
            value["segments"] = [Tracked(r) for r in value["segments"]]
            refs.extend(weakref.ref(r) for r in value["segments"])
        return value
    monkeypatch.setattr(discovery.shared, "_read", tracked)
    materials = discovery._Materials(ready["catalog"], {})
    materials.episode("SYN1")
    first = list(refs)
    materials.episode("SYN1")
    assert len(reads) == 1
    materials.episode("SYN2")
    gc.collect()
    assert all(ref() is None for ref in first)
    materials.episode("SYN1")
    assert len(reads) == 3
    path = Path(ready["source_cfg"]["episodes"][1]["raw"])
    value = read_json(path); value["mutation"] = True
    atomic_write_json(path, value)
    with pytest.raises(ValueError, match="changed"): materials.episode("SYN2")
    assert materials.cached is None


def test_plan_releases_contexts_and_history_keeps_refs_only(ready, monkeypatch):
    class Tracked(dict): pass
    refs = []
    original = discovery._context
    def tracked(*args, **kwargs):
        gc.collect()
        assert all(ref() is None for ref in refs)
        value = Tracked(original(*args, **kwargs))
        refs.append(weakref.ref(value))
        return value
    monkeypatch.setattr(discovery, "_context", tracked)
    cfg, directory, catalog, packets, manifest, pins, prompt = discovery._prepare(pin(ready["path"]))
    assert all(ref() is None for ref in refs)
    assert all(set(p) == {"packet_id", "inventory", "context_sha256", "input_chars", "owned_evidence_ids", "owned_hypothesis_keys"}
               for p in packets)
    monkeypatch.setattr(discovery, "_context", original)
    discovery.execute(pin(ready["path"]), command="run", backend=SyntheticDiscovery())
    histories, count = discovery_runner.read_history(discovery, directory, manifest, packets, prompt, catalog, pins)
    assert count == 2 and all(set(row) == {"request", "result", "result_sha256", "status"}
                             for history in histories.values() for row in history)
    with pytest.raises(ValueError, match="pinned plan"):
        discovery._materialize({**packets[0], "context_sha256": "0" * 64}, catalog, pins, prompt, cfg["max_input_chars"])


def test_cli_plan_and_failure_do_not_leak_private_paths_or_source(ready, capsys):
    args = ["--config", str(ready["path"]), "--sha256", pin(ready["path"])["sha256"]]
    assert discovery.main(["plan", *args]) == 0
    assert discovery.main(["validate", *args]) == 2
    output = capsys.readouterr().out
    assert str(ready["path"].parent) not in output and "Fictional" not in output


def test_original_and_editorial_hypotheses_have_distinct_navigation_ownership(cfg, tmp_path):
    data = make_parent(cfg, tmp_path)
    data["output"] = tmp_path / "revision/synthesis.json"
    review.apply(data["path"], data["output"])
    selected = selection_for(data["cfg"], tmp_path)
    selected["selection"]["expected_synthesis_ids"].append("editorial")
    selected["selection"]["syntheses"].append({"synthesis_id": "editorial", "synthesis": available({
        "kind": "editorial_completed", "artifact": pin(data["output"])})})
    save_selection(selected)
    ready = configure(selected, tmp_path)
    context = packet_context(ready)
    assert len(context["hypotheses"]) == len(context["owned_hypothesis_keys"]) == 4
    assert len({h["hypothesis_id"] for h in context["hypotheses"]}) == 2
    assert len({h["hypothesis_key"] for h in context["hypotheses"]}) == 4
    assert {h["authority"] for h in context["hypotheses"]} == {"model_synthesis_candidate", review.AUTHORITY}
    discovery.execute(pin(ready["path"]), command="run", backend=SyntheticDiscovery())
    assert len(read_json(root(ready["config"]) / "discovery.json")["hypothesis_accounting"]) == 4


def test_recovered_comparison_sides_caveats_and_reviewer_authority_survive(failed, tmp_path):
    cfg, recovery_path, recovered_path = failed
    parent_ref = recover_synthesis.apply(recovery_path, recovered_path)
    parent = recover_synthesis.load_checked(parent_ref)
    raw = pipeline._sources(cfg)["SYN1"]["raw_segments"][0]
    proposal = {"schema_version": review_recovered.PROPOSAL_VERSION, "parent_artifact": parent_ref,
        "reviewer": "Synthetic reviewer", "method": "Exact fictional source reading", "rationale": "Narrow wording only",
        "edits": [{"hypothesis_id": "PROFILE:H1", "field": "/label", "expected": parent["content"]["hypotheses"][0]["label"],
            "value": "Limited fictional comparison", "reason": "Do not generalize", "evidence_ids": ["SYN1:E1"],
            "source_anchors": [{"episode_id": "SYN1", **{k: raw[k] for k in ("segment_id", "start", "end", "text")}}]}]}
    path = tmp_path / "revision-proposal.json"
    atomic_write_json(path, proposal)
    edited = review_recovered.apply(path, tmp_path / "revision/synthesis.json")
    data = selection_for(cfg, tmp_path, synthesis=False)
    data["selection"].update(expected_synthesis_ids=["recovered", "editorial"], syntheses=[
        {"synthesis_id": "recovered", "synthesis": available({"kind": "recovered", "artifact": parent_ref})},
        {"synthesis_id": "editorial", "synthesis": available({"kind": "editorial_recovered", "artifact": edited})}])
    save_selection(data)
    ready = configure(data, tmp_path)
    context = packet_context(ready)
    caveats = [o for o in context["known_relationship_closure"]["obligations"] if o["kind"] == "recovered_comparison_caveat"]
    assert len(caveats) == 4
    for row in caveats:
        assert set(row["evidence_ids"]) <= {r["evidence_id"] for r in context["evidence"]}
        assert row["comparison"]["original_evidence_kinds"]
    assert len(context["hypotheses"]) == 2
    for h in context["hypotheses"]:
        assert len(h["recovered_comparisons"]) == 2
        assert all(c["preserved_limitation"] in h["limitations"] for c in h["recovered_comparisons"])
        assert h["grounding"]["independent_event_count"] is None


@pytest.mark.parametrize("editorial", [False, True])
def test_recovered_evidence_and_editorial_notes_keep_original_authorities(cfg, tmp_path, editorial):
    cfg["episodes"] = cfg["episodes"][:1]
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(cfg, through="evidence", backend=ContextModel())
    recovery = {"schema_version": recover_evidence.CONFIG_VERSION, "commentary_scope": schemas.EPISODE_CONTEXT,
        "manifest": pin(root(cfg) / "run_manifest.json"), "request": pin(root(cfg) / "calls/SYN1_evidence_1.request.json"),
        "result": pin(root(cfg) / "calls/SYN1_evidence_1.result.json")}
    descriptor = {"kind": "recovered", "artifact": recover_evidence.apply(recovery, tmp_path / "recovered/evidence.json")}
    if editorial:
        parent = read_json(Path(descriptor["artifact"]["path"]))
        notes = parent["content"]["items"][0]["supplementary_notes"]
        raw = pipeline._sources(cfg)["SYN1"]["raw_segments"][2]
        config = {"schema_version": evidence_revision.CONFIG_VERSION, "commentary_scope": schemas.EPISODE_CONTEXT,
            "parent": descriptor, "reviewer": "Synthetic reviewer", "rationale": "Preserve uncertainty",
            "edits": [{"evidence_id": "SYN1:E1", "field": "/supplementary_notes", "expected": notes,
                "value": notes + [{"merged_ids": [2], "note": "Reviewer: outcome unspecified"}],
                "reason": "Not a new observation", "source_anchors": [raw]}]}
        path = tmp_path / "editorial-config.json"
        atomic_write_json(path, config)
        descriptor = {"kind": "editorial", "artifact": evidence_revision.apply(pin(path), tmp_path / "revision/evidence.json")}
    data = {"cfg": cfg, "path": tmp_path / "selection.json", "output": tmp_path / "inventory/index.json",
            "selection": {"schema_version": inventory.SELECTION_VERSION, "corpus_id": "recovered_context",
                "expected_episode_ids": ["SYN1"], "reserved_episode_ids": ["RESERVED"],
                "episodes": [{"episode_id": "SYN1", "evidence": available(descriptor)}],
                "expected_synthesis_ids": [], "syntheses": []}}
    save_selection(data)
    ready = configure(data, tmp_path)
    context = packet_context(ready)
    original = read_json(Path(descriptor["artifact"]["path"]))
    row = context["evidence"][0]
    assert row["grounding"] == original["grounding"]["items"]["SYN1:E1"]
    assert row["grounding"]["supplementary_context"][-1]["authority"] == (
        evidence_revision.NOTE_AUTHORITY if editorial else "model_analyst_not_speaker")
    assert context["owned_hypothesis_keys"] == []


def test_cross_episode_relationship_closure_is_indivisible(cfg, tmp_path):
    class CrossEpisode(ParentModel):
        def complete(self, *args):
            value, usage = super().complete(*args)
            if self.calls[-1] == "synthesis":
                value = synthesis_proposal(self.contexts[-1]["evidence"], ["SYN1:E1", "SYN2:E1", "SYN2:E2"])
                value["hypotheses"][0].update(supporting_evidence=["SYN1:E1", "SYN2:E1"], counterevidence=["SYN2:E2"],
                    statement_action_relationships=[{"statement_id": "SYN1:E1", "action_id": "SYN2:E2",
                                                     "relationship": "unresolved", "reason": "Different unknown event contexts"}])
            return value, usage
    cfg["episodes"] = cfg["episodes"][:2]
    pipeline.run(cfg, backend=CrossEpisode())
    ready = configure(selection_for(cfg, tmp_path), tmp_path)
    catalog, ref = ready["catalog"], ready["config"]["inventory"]
    prompt = discovery.PROMPT_PATH.read_text()
    context = discovery._context(["SYN1:E3"], catalog, discovery._Materials(catalog, {}), ref, prompt=prompt, max_chars=1000000)
    assert set(context["known_relationship_closure"]["required_evidence_ids"]) == {"SYN1:E1", "SYN1:E3", "SYN2:E1", "SYN2:E2"}
    assert set(discovery._expand(context)["sources"]) == {"SYN1", "SYN2"}
    with pytest.raises(discovery._PacketTooLarge):
        discovery._context(["SYN1:E3"], catalog, discovery._Materials(catalog, {}), ref,
                           prompt=prompt, max_chars=discovery._size(prompt, context) - 1)


def test_lifetime_budget_stops_before_second_packet(ready):
    ready["config"]["max_calls"] = 1
    ref = save(ready)
    result = discovery.execute(ref, command="run", backend=SyntheticDiscovery())
    assert result["status"] == "call_budget_exhausted" and result["admitted_calls"] == 1
    result = discovery.execute(ref, command="run")
    assert result["status"] == "call_budget_exhausted" and result["model_calls_this_execution"] == 0


def test_incomplete_inventory_fails_without_output(cfg, tmp_path):
    cfg["episodes"] = cfg["episodes"][:1]
    pipeline.run(cfg, through="evidence", backend=SyntheticModel())
    data = selection_for(cfg, tmp_path, synthesis=False)
    data["selection"]["episodes"][0]["evidence"] = {"status": "held", "reason": "Synthetic unavailable input", "record": None}
    save_selection(data)
    ref = inventory.freeze(pin(data["path"]), data["output"])
    config = {"schema_version": "profile_discovery_config.v1", "run_id": "incomplete", "output_root": str(tmp_path / "discovery"),
              "inventory": ref, "model": "synthetic", "reasoning_effort": "high", "timeout_seconds": 10,
              "max_input_chars": 1000000, "max_response_chars": 100000, "max_calls": 1, "retry_count": 0}
    path = tmp_path / "discovery-config.json"
    atomic_write_json(path, config)
    with pytest.raises(ValueError, match="complete explicit inventory"):
        discovery.execute(pin(path), command="run")
    assert not root(config).exists()


def test_compact_transport_roundtrip_preserves_notes_closure_missing_and_null(ready):
    plain = packet_context(ready)
    plain["evidence"][0]["optional_context"] = None
    plain["evidence"][2]["optional_context"] = False
    eid = next(iter(plain["sources"]))
    raw = plain["raw_regions"][eid][0]
    annotations = {"sha256": "c" * 64, "review": {"method": "Synthetic context review"}, "annotations": [
        {"annotation_id": "synthetic:I1", "severity": "critical", "note": "Unresolved synthetic source meaning",
         "anchors": [{"segment_id": raw["segment_id"], "start": raw["start"], "end": raw["end"], "raw_text": raw["text"]}]}]}
    plain["sources"][eid]["source"]["reviewed_annotations"] = annotations
    for row in plain["evidence"]:
        row["grounding"]["reviewed_annotations"] = copy.deepcopy(annotations)
        row["uncertainty"] = ["Synthetic shared uncertainty; not speaker evidence. " * 100]
    before = copy.deepcopy(plain)
    packed = discovery._pack(plain)
    expanded = discovery._expand(packed)
    assert expanded == before == plain
    assert expanded["evidence"][0]["optional_context"] is None
    assert "optional_context" not in expanded["evidence"][1]
    assert expanded["evidence"][2]["optional_context"] is False
    assert expanded["known_relationship_closure"] == before["known_relationship_closure"]
    assert packed["context_format"] == discovery.CONTEXT_FORMAT == "compact_profile_discovery.v2"
    assert packed["base_context_format"] == discovery.serialization.FORMAT
    assert "missing" in packed["serialization_legend"] and "not extra observations" in packed["serialization_legend"]
    response = proposal(plain)
    assert discovery._derive(response, packed) == discovery._derive_plain(response, plain)
    assert len(json.dumps(packed)) < len(json.dumps(plain))


def test_incremental_planning_and_admission_sizes_are_actual_packed_characters(ready, monkeypatch):
    plain = packet_context(ready)
    prompt = discovery.PROMPT_PATH.read_text()
    assert discovery._size(prompt, plain) > ready["config"]["max_input_chars"]
    sizes = []
    original = discovery._size
    def checked(prompt, context):
        assert context["context_format"] == discovery.CONTEXT_FORMAT
        assert isinstance(context["sources"], list) and isinstance(context["evidence"], dict)
        size = original(prompt, context)
        sizes.append(size)
        return size
    monkeypatch.setattr(discovery, "_size", checked)
    plan = discovery.execute(pin(ready["path"]))
    assert len(sizes) > len(plan["packet_plan"])
    assert plan["owned_evidence"] == 6 and plan["owned_hypotheses"] == 2
    assert all(p["input_chars"] <= ready["config"]["max_input_chars"] for p in plan["packet_plan"])
    result = discovery.execute(pin(ready["path"]), command="run", backend=SyntheticDiscovery())
    assert result["status"] == "completed"
    request = read_json(root(ready["config"]) / "packet_000001/attempt_0001.request.json")
    assert original(prompt, request["context"]) == plan["packet_plan"][0]["input_chars"]


def test_final_only_packing_accepts_nonmonotone_partial_size_and_exact_cap(ready, monkeypatch):
    catalog, ref = ready["catalog"], ready["config"]["inventory"]
    eid = next(iter(catalog["episodes"]))
    owned = catalog["episodes"][eid]["evidence_ids"]
    materials = discovery._Materials(catalog, {})
    artifact, _ = materials.episode(eid)
    # At two rows these columns are literal; the third row enables shared values.
    for rid in owned:
        row = discovery._pointer(artifact, catalog["evidence"][rid]["content_pointer"])
        row.update({f"synthetic_extension_{i}": "x" * 80 for i in range(40)})
    prompt = discovery.PROMPT_PATH.read_text()
    def candidate(cap):
        return discovery._context(owned, catalog, materials, ref, prompt=prompt, max_chars=cap)
    expected = candidate(1000000)
    plain = discovery._expand(expected)
    assert len(plain["evidence"]) == 3
    partial = copy.deepcopy(plain)
    partial["evidence"] = partial["evidence"][:2]
    partial["raw_regions"] = {}
    cap = discovery._size(prompt, expected)
    assert discovery._size(prompt, discovery._pack(partial)) > cap
    pack, size = Mock(wraps=discovery._pack), Mock(wraps=discovery._size)
    monkeypatch.setattr(discovery, "_pack", pack)
    monkeypatch.setattr(discovery, "_size", size)
    for limit in (cap, cap + 1, cap - 1):
        pack.reset_mock()
        size.reset_mock()
        if limit < cap:
            with pytest.raises(discovery._PacketTooLarge, match=f"requires {cap} input characters"):
                candidate(limit)
        else:
            assert candidate(limit) == expected
        pack.assert_called_once()
        size.assert_called_once()
        complete = pack.call_args.args[0]
        assert complete == plain
        assert [r["evidence_id"] for r in complete["evidence"]] == sorted(owned)
        assert complete["known_relationship_closure"]["obligations"]
        assert complete["raw_regions"] and complete["sources"] and complete["hypotheses"]


@pytest.mark.parametrize("fault", ["format", "legend", "quote_index", "quote_range", "duplicate_source", "missing_table"])
def test_noncanonical_compact_transport_is_rejected(ready, fault):
    plain = packet_context(ready)
    packed = discovery._pack(plain)
    if fault == "format": packed["context_format"] = "compact_profile.v999"
    elif fault == "legend": packed["serialization_legend"] = "Changed decoding instructions"
    elif fault in {"quote_index", "quote_range"}:
        table = packed["evidence"]
        assert "quotes" not in table["shared_columns"]
        table["rows"][0][table["columns"].index("quotes")] = [-1 if fault == "quote_index" else 999999]
    elif fault == "duplicate_source": packed["sources"].append(copy.deepcopy(packed["sources"][0]))
    else: packed["evidence"]["missing"]["999999"] = [0]
    with pytest.raises(ValueError): discovery._derive(proposal(plain), packed)


@pytest.mark.parametrize("pin_name", ["codec_sha256", "context_format", "annotation_refs_sha256",
                                    "annotation_reference_format", "base_context_format"])
def test_codec_and_format_pins_require_new_runs(ready, monkeypatch, pin_name):
    ref = pin(ready["path"])
    discovery.execute(ref, command="run", backend=SyntheticDiscovery())
    directory = root(ready["config"])
    manifest = read_json(directory / "run_manifest.json")
    assert manifest["schema_version"] == "profile_discovery_run.v4"
    assert manifest["implementation"]["codec_sha256"] == discovery.sha256_file(Path(discovery.serialization.__file__))
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    changed = copy.deepcopy(discovery._implementation())
    changed[pin_name] = "0" * 64 if pin_name.endswith("sha256") else "compact_profile.v999"
    monkeypatch.setattr(discovery, "_implementation", lambda: changed)
    for command in ("plan", "run", "validate"):
        with pytest.raises(ValueError, match="new run version"):
            discovery.execute(ref, command=command)
    assert {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()} == before


def test_old_planning_run_version_cannot_resume(ready):
    ref = pin(ready["path"])
    discovery.execute(ref, command="run", backend=SyntheticDiscovery())
    directory = root(ready["config"])
    request = read_json(directory / "packet_000001/attempt_0001.request.json")
    assert request["schema_version"] == "profile_discovery_request.v4"
    path = directory / "run_manifest.json"
    manifest = read_json(path)
    assert manifest["schema_version"] == "profile_discovery_run.v4"
    manifest["schema_version"] = "profile_discovery_run.v3"
    atomic_write_json(path, manifest)
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    for command in ("plan", "run", "validate"):
        with pytest.raises(ValueError, match="new run version"):
            discovery.execute(ref, command=command)
    assert {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()} == before


def test_compact_request_tampering_cannot_change_anchors_or_closure(ready):
    ref = pin(ready["path"])
    discovery.execute(ref, command="run", backend=SyntheticDiscovery())
    path = root(ready["config"]) / "packet_000001/attempt_0001.request.json"
    original = path.read_bytes()
    for fault in ("quote", "counter", "notes", "plain_context"):
        request = json.loads(original)
        plain = discovery._expand(request["context"])
        if fault == "quote":
            plain["evidence"][0]["quotes"][0]["text"] += " Synthetic tampering"
            # Repack remains internally consistent; the receipt must still fail its source binding.
            plain["evidence"][2]["quotes"][0]["text"] = plain["evidence"][0]["quotes"][0]["text"]
        elif fault == "counter": plain["known_relationship_closure"]["obligations"] = []
        elif fault == "notes": plain["evidence"][0]["grounding"]["supplementary_context"] = []
        request["context"] = plain if fault == "plain_context" else discovery._pack(plain)
        atomic_write_json(path, request)
        with pytest.raises(ValueError, match="exact planned input"):
            discovery.execute(ref, command="validate")


def test_history_releases_expanded_plain_packets(ready, monkeypatch):
    class Tracked(dict): pass
    refs = []
    original = discovery._expand
    def expand(context):
        gc.collect()
        assert all(ref() is None for ref in refs)
        result = Tracked(original(context))
        refs.append(weakref.ref(result))
        return result
    ref = pin(ready["path"])
    discovery.execute(ref, command="run", backend=SyntheticDiscovery())
    _, directory, catalog, packets, manifest, pins, prompt = discovery._prepare(ref)
    monkeypatch.setattr(discovery, "_expand", expand)
    histories, count = discovery_runner.read_history(discovery, directory, manifest, packets, prompt, catalog, pins)
    gc.collect()
    assert len(refs) == count == 2 and all(ref() is None for ref in refs)
    assert all(set(row) == {"status", "request", "result", "result_sha256"} for rows in histories.values() for row in rows)


def test_annotation_subset_receipts_resume_and_reject_valid_but_changed_ids(ready, monkeypatch):
    original = discovery._Materials.episode
    def annotated_episode(self, eid):
        artifact, raw = original(self, eid)
        artifact = copy.deepcopy(artifact)
        anchor = next(iter(raw.values()))
        envelope = {"sha256": "c" * 64, "review": {"method": "Synthetic metadata fixture"}, "annotations": [
            {"annotation_id": f"A{i}", "note": f"Fictional reviewer context {i}",
             "anchors": [{"segment_id": anchor["segment_id"], "start": anchor["start"],
                          "end": anchor["end"], "raw_text": anchor["text"]}]} for i in (1, 2)]}
        artifact["source"]["reviewed_annotations"] = envelope
        for grounding in artifact["grounding"]["items"].values():
            grounding["reviewed_annotations"] = copy.deepcopy(envelope)
        return artifact, raw
    monkeypatch.setattr(discovery._Materials, "episode", annotated_episode)
    ready["config"]["max_input_chars"] = 1000000
    ref = save(ready)
    model = SyntheticDiscovery()
    assert discovery.execute(ref, command="run", backend=model)["status"] == "completed"
    directory = root(ready["config"])
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    assert discovery.execute(ref, command="run")["model_calls_this_execution"] == 0
    assert discovery.execute(ref, command="validate")["status"] == "validated"
    assert {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()} == before
    path = directory / "packet_000001/attempt_0001.request.json"
    request = read_json(path)
    assert discovery.discovery_annotations.MARKER in json.dumps(request["context"])
    plain = discovery._expand(request["context"])
    plain["evidence"][0]["grounding"]["reviewed_annotations"]["annotations"].reverse()
    request["context"] = discovery._pack(plain)
    # This remains a valid reference to exact catalog records, but is not the planned subset.
    assert discovery._expand(request["context"]) == plain
    atomic_write_json(path, request)
    for command in ("validate", "run"):
        with pytest.raises(ValueError, match="exact planned input"):
            discovery.execute(ref, command=command)
