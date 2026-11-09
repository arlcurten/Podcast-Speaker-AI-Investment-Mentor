"""Synthetic corpus inventories; no provider access, real sources or live jobs."""
import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import corpus_inventory as inventory, evidence_revision, pipeline, recover_evidence, recover_synthesis, review, review_recovered, schemas
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_pipeline import SyntheticModel, cfg, root
from .test_review import ParentModel, make_parent, pin
from .test_recover_evidence import ContextModel
from .test_recover_synthesis import failed


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Inventory must never construct a model or subprocess"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


def available(descriptor):
    return {"status": "available", "input": descriptor}


def selection_for(config, tmp_path, *, synthesis=True):
    parent = root(config)
    manifest = pin(parent / "run_manifest.json")
    selection = {"schema_version": inventory.SELECTION_VERSION, "corpus_id": "synthetic_profile_inventory",
        "expected_episode_ids": [e["episode_id"] for e in config["episodes"]], "reserved_episode_ids": ["RESERVED"],
        "episodes": [{"episode_id": e["episode_id"], "evidence": available({"kind": "completed", "manifest": manifest,
            "artifact": pin(parent / e["episode_id"] / "evidence.json")})} for e in config["episodes"]],
        "expected_synthesis_ids": ["prior"] if synthesis else [],
        "syntheses": [{"synthesis_id": "prior", "synthesis": available({"kind": "completed", "manifest": manifest,
            "artifact": pin(parent / "synthesis.json")})}] if synthesis else []}
    path = tmp_path / "selection.json"
    atomic_write_json(path, selection)
    return {"cfg": config, "selection": selection, "path": path, "output": tmp_path / "inventory/index.json"}


def save(data):
    atomic_write_json(data["path"], data["selection"])
    return pin(data["path"])


@pytest.fixture
def ready(cfg, tmp_path):
    cfg["episodes"] = cfg["episodes"][:2]
    pipeline.run(cfg, backend=ParentModel())
    return selection_for(cfg, tmp_path)


def test_complete_inventory_keeps_every_item_and_original_pointers(ready, tmp_path, monkeypatch):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    forbidden = Mock(side_effect=AssertionError("Inventory must not resume an original completed run"))
    monkeypatch.setattr(pipeline, "run", forbidden)
    inspected = inventory.inspect(pin(ready["path"]))
    assert not ready["output"].exists()
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    result = inventory.load_checked(ref)
    assert result == inspected
    assert result["inventory_complete"] and result["gaps"] == []
    assert set(result["evidence"]) == {f"SYN{e}:E{i}" for e in (1, 2) for i in (1, 2, 3)}
    assert result["evidence"]["SYN1:E3"]["basis_evidence_ids"] == ["SYN1:E1"]
    assert result["evidence"]["SYN1:E3"]["evidence_kind"] == "inferred_tendency"
    assert {row["evidence_kind"] for row in result["evidence"].values()} == {
        "direct_statement", "reported_action", "inferred_tendency"}
    for rid, row in result["evidence"].items():
        artifact = read_json(Path(row["artifact"]["path"]))
        item = artifact["content"]["items"][int(row["content_pointer"].split("/")[-1])]
        assert item["evidence_id"] == rid and row["domains"] == item["domains"]
        assert "source_observation" not in row and "quotes" not in row
    for row in result["hypotheses"].values():
        artifact = read_json(Path(row["artifact"]["path"]))
        hypothesis = artifact["content"]["hypotheses"][int(row["content_pointer"].split("/")[-1])]
        assert row["hypothesis_type"] == hypothesis["hypothesis_type"]
        assert row["categories"] == hypothesis["categories"] and row["domains"] == hypothesis["domains"]
        assert row["grounding"]["independent_event_count"] is None
    assert result["policy"] == inventory.POLICY and result["policy"]["independent_event_count"] is None
    assert not result["policy"]["publication_authorized"] and not result["policy"]["eligible_for_serving"]
    assert {p: p.read_bytes() for p in before} == before
    assert {p for p in tmp_path.rglob("*") if p.is_file()} - set(before) == {ready["output"]}
    forbidden.assert_not_called()
    with pytest.raises(FileExistsError): inventory.freeze(pin(ready["path"]), ready["output"])


def test_known_counter_comparison_and_inferred_basis_closure(ready):
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    result = inventory.known_closure(ref, ["SYN1:E3"])
    assert set(result["required_evidence_ids"]) == {"SYN1:E1", "SYN1:E2", "SYN1:E3"}
    assert {r["kind"] for r in result["obligations"]} == {"counterevidence", "statement_action_comparison"}
    assert len(result["matched_hypothesis_keys"]) == 2
    assert all(r["status"] == "requires_scope_review" for r in result["obligations"])
    assert all("disposition" not in r for r in result["obligations"])
    assert result["policy"]["independent_event_count"] is None
    for row in result["obligations"]:
        assert row["hypothesis_key"].startswith(row["parent_artifact"]["sha256"] + ":")


def test_checked_index_closure_matches_wrapper_without_replay_or_mutation(ready, monkeypatch):
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    catalog = inventory.load_checked(ref)
    expected = inventory.known_closure(ref, ["SYN1:E3"])
    before = copy.deepcopy(catalog)
    forbidden = Mock(side_effect=AssertionError("Checked closure must not replay sources"))
    monkeypatch.setattr(inventory, "load_checked", forbidden)
    assert inventory.checked_index_closure(catalog, ref, ["SYN1:E3"]) == expected
    assert catalog == before
    for ids in ([], ["UNKNOWN"], ["SYN1:E3", "SYN1:E3"]):
        with pytest.raises(ValueError): inventory.checked_index_closure(catalog, ref, ids)
    forbidden.assert_not_called()


@pytest.mark.parametrize("kind", ["completed", "recovered", "editorial"])
def test_explicit_evidence_formats_and_commentary_authority(cfg, tmp_path, monkeypatch, kind):
    cfg["episodes"] = cfg["episodes"][:1]
    if kind == "completed":
        pipeline.run(cfg, through="evidence", backend=SyntheticModel())
        descriptor = {"kind": "completed", "manifest": pin(root(cfg) / "run_manifest.json"),
                      "artifact": pin(root(cfg) / "SYN1/evidence.json")}
    else:
        with pytest.raises(ValueError, match="attempts exhausted"):
            pipeline.run(cfg, through="evidence", backend=ContextModel())
        recovery = {"schema_version": recover_evidence.CONFIG_VERSION, "commentary_scope": "episode_context.v1",
                    "manifest": pin(root(cfg) / "run_manifest.json"),
                    "request": pin(root(cfg) / "calls/SYN1_evidence_1.request.json"),
                    "result": pin(root(cfg) / "calls/SYN1_evidence_1.result.json")}
        descriptor = {"kind": "recovered", "artifact": recover_evidence.apply(recovery, tmp_path / "recovered/evidence.json")}
        if kind == "editorial":
            original = read_json(Path(descriptor["artifact"]["path"]))
            source = pipeline._sources(cfg)["SYN1"]
            proposal = {"schema_version": evidence_revision.CONFIG_VERSION, "commentary_scope": "episode_context.v1",
                "parent": descriptor, "reviewer": "Synthetic reviewer", "rationale": "Keep the missing outcome visible.",
                "edits": [{"evidence_id": "SYN1:E1", "field": "/supplementary_notes",
                    "expected": original["content"]["items"][0]["supplementary_notes"],
                    "value": original["content"]["items"][0]["supplementary_notes"] + [{"merged_ids": [2], "note": "Reviewer: outcome not established."}],
                    "reason": "Source uncertainty is not a speaker statement.", "source_anchors": [source["raw_segments"][2]]}]}
            path = tmp_path / "editorial-config.json"
            atomic_write_json(path, proposal)
            descriptor = {"kind": "editorial", "artifact": evidence_revision.apply(pin(path), tmp_path / "editorial/evidence.json")}
    selection = {"schema_version": inventory.SELECTION_VERSION, "corpus_id": "formats",
                 "expected_episode_ids": ["SYN1"], "reserved_episode_ids": ["SYN2"],
                 "episodes": [{"episode_id": "SYN1", "evidence": available(descriptor)}],
                 "expected_synthesis_ids": [], "syntheses": []}
    path = tmp_path / "selection.json"
    atomic_write_json(path, selection)
    forbidden = Mock(side_effect=AssertionError("Evidence inventory must not run the pipeline"))
    monkeypatch.setattr(pipeline, "run", forbidden)
    ref = inventory.freeze(pin(path), tmp_path / "inventory/index.json")
    result = inventory.load_checked(ref)
    assert result["inventory_complete"] and len(result["evidence"]) == 3
    assert {r["artifact_kind"] for r in result["evidence"].values()} == {kind}
    assert result["episodes"]["SYN1"]["evidence"]["input"] == descriptor
    artifact = read_json(Path(descriptor["artifact"]["path"]))
    assert result["episodes"]["SYN1"]["policy"] == artifact["policy"]
    if kind == "editorial":
        assert artifact["grounding"]["items"]["SYN1:E1"]["supplementary_context"][-1]["authority"] == evidence_revision.NOTE_AUTHORITY
    if kind != "completed":
        assert pin(root(cfg) / "calls/SYN1_evidence_1.result.json") in result["dependencies"]
    forbidden.assert_not_called()


@pytest.mark.parametrize("status", ["missing", "failed", "held"])
def test_declared_missing_episodes_and_syntheses_never_auto_discovered(ready, status, tmp_path):
    receipt = tmp_path / "gap.txt"
    receipt.write_text("Synthetic gap record; not acceptance")
    state = {"status": status, "reason": "Selected artifact remains unavailable", "record": pin(receipt)}
    ready["selection"]["episodes"][1]["evidence"] = state
    ready["selection"]["syntheses"][0]["synthesis"] = state
    result = inventory.load_checked(inventory.freeze(save(ready), ready["output"]))
    assert not result["inventory_complete"] and len(result["gaps"]) == 2
    assert len(result["evidence"]) == 3 and result["hypotheses"] == {}
    assert result["episodes"]["SYN2"]["evidence_ids"] == []
    assert pin(receipt) in result["dependencies"]


def test_zero_items_is_complete_not_missing(cfg, tmp_path):
    pipeline.run(cfg, through="evidence", backend=SyntheticModel(empty=True))
    data = selection_for(cfg, tmp_path, synthesis=False)
    result = inventory.inspect(pin(data["path"]))
    assert result["inventory_complete"] and result["evidence"] == {}


@pytest.mark.parametrize("fault", ["missing_episode", "duplicate_episode", "extra_episode", "missing_synthesis",
    "reserved_selected", "reserved_ancestor", "wrong_episode", "unknown_kind", "relabel", "wrong_sha",
    "blank_gap", "extra_field", "wrong_schema"])
def test_invalid_selection_and_reserved_sources_fail_closed(ready, fault, monkeypatch):
    s = ready["selection"]
    state = s["episodes"][0]["evidence"]
    if fault == "missing_episode": s["episodes"].pop()
    elif fault == "duplicate_episode": s["episodes"].append(copy.deepcopy(s["episodes"][0]))
    elif fault == "extra_episode": s["expected_episode_ids"].pop()
    elif fault == "missing_synthesis": s["syntheses"] = []
    elif fault == "reserved_selected": s["reserved_episode_ids"] = ["SYN1"]
    elif fault == "reserved_ancestor":
        s["expected_episode_ids"] = ["SYN1"]; s["episodes"] = s["episodes"][:1]
        s["reserved_episode_ids"] = ["SYN2"]
        s["expected_synthesis_ids"] = []; s["syntheses"] = []
        monkeypatch.setattr(pipeline, "_sources", Mock(side_effect=AssertionError("Reserved raw must not be loaded")))
    elif fault == "wrong_episode": s["episodes"][0]["episode_id"] = "OTHER"; s["expected_episode_ids"][0] = "OTHER"
    elif fault == "unknown_kind": state["input"]["kind"] = "accepted"
    elif fault == "relabel": state["input"] = {"kind": "recovered", "artifact": state["input"]["artifact"]}
    elif fault == "wrong_sha": state["input"]["artifact"]["sha256"] = "a" * 64
    elif fault == "blank_gap": s["episodes"][0]["evidence"] = {"status": "held", "reason": " ", "record": None}
    elif fault == "extra_field": s["eligible_for_serving"] = True
    else: s["schema_version"] = "knowledge_corpus_selection.v1"
    with pytest.raises(ValueError): inventory.freeze(save(ready), ready["output"])
    assert not ready["output"].exists()


@pytest.mark.parametrize("target", ["raw", "draft", "manifest", "result", "evidence", "synthesis", "selection"])
def test_changed_inputs_invalidate_freeze(ready, target):
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    parent = root(ready["cfg"])
    path = {"raw": Path(ready["cfg"]["episodes"][0]["raw"]), "draft": Path(ready["cfg"]["episodes"][0]["draft"]),
            "manifest": parent / "run_manifest.json", "result": parent / "calls/SYN1_evidence_1.result.json",
            "evidence": parent / "SYN1/evidence.json", "synthesis": parent / "synthesis.json", "selection": ready["path"]}[target]
    value = read_json(path); value["changed"] = True; atomic_write_json(path, value)
    with pytest.raises(ValueError): inventory.load_checked(ref)


@pytest.mark.parametrize("target", ["item", "basis", "counter", "policy", "complete", "dependencies"])
def test_rebound_inventory_hash_cannot_hide_index_mutation(ready, target):
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    value = inventory.load_checked(ref)
    if target == "item": value["evidence"].pop("SYN1:E3")
    elif target == "basis": value["evidence"]["SYN1:E3"]["basis_evidence_ids"] = []
    elif target == "counter": next(iter(value["hypotheses"].values()))["counterevidence"] = []
    elif target == "policy": value["policy"]["eligible_for_serving"] = True
    elif target == "complete": value["inventory_complete"] = False
    else: value["dependencies"].pop()
    atomic_write_json(ready["output"], value)
    with pytest.raises(ValueError): inventory.load_checked(pin(ready["output"]))


@pytest.mark.parametrize("support", [[], ["SYN1:E1", "SYN1:E1"], ["PROFILE:H1"], [False], "SYN1:E1"])
def test_closure_invalid_support_ids(ready, support):
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    with pytest.raises(ValueError): inventory.known_closure(ref, support)


def test_completed_editorial_synthesis_preserves_distinct_hypothesis_versions(cfg, tmp_path):
    data = make_parent(cfg, tmp_path)
    data["output"] = tmp_path / "editorial/synthesis.json"
    review.apply(data["path"], data["output"])
    inventory_data = selection_for(data["cfg"], tmp_path)
    selection = inventory_data["selection"]
    selection["expected_synthesis_ids"].append("editorial")
    selection["syntheses"].append({"synthesis_id": "editorial", "synthesis": available({
        "kind": "editorial_completed", "artifact": pin(data["output"])})})
    result = inventory.load_checked(inventory.freeze(save(inventory_data), inventory_data["output"]))
    assert len(result["hypotheses"]) == 4
    assert len({h["hypothesis_id"] for h in result["hypotheses"].values()}) == 2
    assert result["episodes"]["SYN1"]["source_pointer"] == "/source"
    assert result["syntheses"]["editorial"]["authority"] == review.AUTHORITY
    assert pin(data["path"]) in result["dependencies"]


def test_recovered_and_editorial_recovered_synthesis_keep_comparison_caveats(failed, tmp_path):
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
    ref = inventory.freeze(save(data), data["output"])
    result = inventory.load_checked(ref)
    assert len(result["evidence"]) == 4 and len(result["hypotheses"]) == 2
    for h in result["hypotheses"].values():
        assert h["grounding"]["independent_event_count"] is None
        assert len(h["recovered_comparisons"]) == 2
        for row in h["recovered_comparisons"]:
            assert row["preserved_limitation"] in h["limitations"]
    closure = inventory.known_closure(ref, ["SYN1:E1"])
    caveats = [row for row in closure["obligations"] if row["kind"] == "recovered_comparison_caveat"]
    assert len(caveats) == 4
    assert all(row["comparison"]["original_evidence_kinds"] for row in caveats)
    assert parent_ref in result["dependencies"] and pin(recovery_path) in result["dependencies"]


def test_synthesis_requires_exact_selected_evidence_version(ready, tmp_path):
    cfg = copy.deepcopy(ready["cfg"])
    cfg["run_id"] = "other_version"
    pipeline.run(cfg, through="evidence", backend=SyntheticModel())
    ready["selection"]["episodes"][0]["evidence"]["input"] = {
        "kind": "completed", "manifest": pin(root(cfg) / "run_manifest.json"),
        "artifact": pin(root(cfg) / "SYN1/evidence.json")}
    with pytest.raises(ValueError, match="exact selected evidence versions"):
        inventory.freeze(save(ready), ready["output"])


def test_input_race_and_changed_implementation_block_freeze(ready, monkeypatch):
    original = evidence_revision._parent
    path = Path(ready["cfg"]["episodes"][0]["raw"])
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        value = read_json(path); value["mutation"] = True; atomic_write_json(path, value)
        return result
    monkeypatch.setattr(evidence_revision, "_parent", race)
    with pytest.raises(ValueError): inventory.freeze(pin(ready["path"]), ready["output"])
    assert not ready["output"].exists()


def test_current_implementation_is_not_silently_relabelled(ready, monkeypatch):
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    changed = copy.deepcopy(inventory._implementation())
    changed["code"]["profile_inventory"] = "c" * 64
    monkeypatch.setattr(inventory, "_implementation", lambda: changed)
    with pytest.raises(ValueError, match="reconstruction"):
        inventory.load_checked(ref)


def test_call_change_during_synthesis_validation_cannot_be_pinned_as_original(ready, monkeypatch):
    original = pipeline._derive_synthesis
    path = root(ready["cfg"]) / "calls/cross_episode_synthesis_1.result.json"
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        value = read_json(path); value["mutation"] = True; atomic_write_json(path, value)
        return result
    monkeypatch.setattr(pipeline, "_derive_synthesis", race)
    with pytest.raises(ValueError): inventory.inspect(pin(ready["path"]))
    assert not ready["output"].exists()


def test_transitive_reserved_manifest_fails_before_source_loading(ready, tmp_path, monkeypatch):
    original = pin(root(ready["cfg"]) / "run_manifest.json")
    cfg = copy.deepcopy(ready["cfg"])
    cfg.update(run_id="selected_only", episodes=cfg["episodes"][:1],
               reuse_evidence={"manifest": original, "artifacts": {"SYN1": pin(root(ready["cfg"]) / "SYN1/evidence.json")}})
    manifest = read_json(Path(original["path"]))
    manifest.update(config=cfg, config_digest=pipeline.digest(cfg))
    path = root(cfg) / "run_manifest.json"
    atomic_write_json(path, manifest)
    forbidden = Mock(side_effect=AssertionError("Reserved ancestor sources must not be loaded"))
    monkeypatch.setattr(pipeline, "_sources", forbidden)
    with pytest.raises(ValueError, match="reserved sources"):
        inventory._manifest(pin(path), {}, ["SYN2"], set())
    forbidden.assert_not_called()


def test_duplicate_json_keys_and_private_output_constraints(ready):
    with pytest.raises(ValueError, match="public repository"):
        inventory.freeze(pin(ready["path"]), Path(inventory.__file__).parent / "invalid.json")
    with pytest.raises(ValueError, match="outside"):
        inventory.freeze(pin(ready["path"]), root(ready["cfg"]) / "inventory.json")
    ready["path"].write_text(ready["path"].read_text().replace("{", '{"corpus_id":"duplicate",', 1))
    with pytest.raises(ValueError, match="Duplicate"):
        inventory.inspect(pin(ready["path"]))


def test_safe_cli_only_emits_counts(ready, capsys):
    args = ["--input", str(ready["path"]), "--sha256", sha256_file(ready["path"])]
    assert inventory.main(["inspect", *args]) == 0
    assert inventory.main(["freeze", *args, "--output", str(ready["output"])]) == 0
    args = ["--input", str(ready["output"]), "--sha256", sha256_file(ready["output"])]
    assert inventory.main(["validate", *args]) == 0
    assert inventory.main(["closure", *args, "--support", "SYN1:E1"]) == 0
    text = capsys.readouterr().out
    assert str(ready["path"].parent) not in text and "Fictional" not in text
    assert '"model_calls": 0' in text


def test_completed_synthesis_preserves_historical_runtime_without_resuming(ready, monkeypatch, tmp_path):
    old_manifest = pin(root(ready["cfg"]) / "run_manifest.json")
    changed = copy.deepcopy(pipeline._implementation())
    changed["code"]["profile_pipeline"] = "c" * 64
    monkeypatch.setattr(pipeline, "_implementation", lambda: changed)
    with pytest.raises(ValueError, match="implementation changed"):
        pipeline.run(ready["cfg"], through="synthesis", validate_only=True)
    forbidden = Mock(side_effect=AssertionError("Historical inventory must not resume a run"))
    monkeypatch.setattr(pipeline, "run", forbidden)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = inventory.load_checked(inventory.freeze(pin(ready["path"]), ready["output"]))
    assert result["inventory_complete"] and len(result["hypotheses"]) == 2
    assert result["syntheses"]["prior"]["original_manifest"] == old_manifest
    assert result["implementation"]["profile"] == changed
    assert {p: p.read_bytes() for p in before} == before
    assert {p for p in tmp_path.rglob("*") if p.is_file()} - set(before) == {ready["output"]}
    forbidden.assert_not_called()


@pytest.mark.parametrize("fault", ["grounding", "obsolete_content", "schema", "context", "usage",
                                   "response", "fingerprint", "reuse_lineage", "failed_status"])
def test_completed_historical_replay_rejects_rebound_corruption(ready, fault, monkeypatch):
    parent = root(ready["cfg"])
    path = parent / "synthesis.json"
    artifact = read_json(path)
    request_path = parent / "calls/cross_episode_synthesis_1.request.json"
    result_path = parent / "calls/cross_episode_synthesis_1.result.json"
    request, result = read_json(request_path), read_json(result_path)
    if fault == "grounding": artifact["grounding"]["retained_evidence_ids"] = []
    elif fault == "obsolete_content": artifact["content"]["unknown_old_field"] = "Not silently migrated"
    elif fault == "schema": request["schema"] = {"type": "object"}
    elif fault == "context": request["context"]["unbound_context"] = True
    elif fault == "usage": artifact["model_metadata"] = {"changed": True}
    elif fault == "response": result["response"]["summary"] = "Changed synthetic model response"
    elif fault == "fingerprint": artifact["fingerprint"] = "d" * 64
    elif fault == "reuse_lineage": artifact["reused_evidence"] = {"changed": True}
    else: result["status"] = "failed"
    atomic_write_json(request_path, request)
    result["request_sha256"] = sha256_file(request_path)
    atomic_write_json(result_path, result)
    artifact.update(request_sha256=sha256_file(request_path), result_sha256=sha256_file(result_path))
    atomic_write_json(path, artifact)
    ready["selection"]["syntheses"][0]["synthesis"]["input"]["artifact"] = pin(path)
    changed = copy.deepcopy(pipeline._implementation())
    changed["code"]["profile_pipeline"] = "c" * 64
    monkeypatch.setattr(pipeline, "_implementation", lambda: changed)
    with pytest.raises(ValueError):
        inventory.inspect(save(ready))
    assert not ready["output"].exists()


def test_completed_synthesis_checks_original_repair_chain(cfg, tmp_path, monkeypatch):
    class RepairModel(ParentModel):
        def complete(self, *args):
            content, usage = super().complete(*args)
            if self.calls[-1] == "synthesis" and self.calls.count("synthesis") == 1:
                content["unknown_field"] = "Synthetic validation failure"
            return content, usage
    cfg.update(episodes=cfg["episodes"][:1], retry_count=1)
    pipeline.run(cfg, backend=RepairModel())
    data = selection_for(cfg, tmp_path)
    forbidden = Mock(side_effect=AssertionError("Read-only inventory must not resume"))
    monkeypatch.setattr(pipeline, "run", forbidden)
    assert inventory.inspect(pin(data["path"]))["inventory_complete"]
    path = root(cfg) / "calls/cross_episode_synthesis_1.result.json"
    value = read_json(path)
    value["error"]["message"] = "Rebound diagnostic no longer matches the original repair input"
    atomic_write_json(path, value)
    with pytest.raises(ValueError, match="repair provenance"):
        inventory.inspect(pin(data["path"]))
    forbidden.assert_not_called()


def safe_source_run(cfg, tmp_path):
    safe = copy.deepcopy(cfg)
    raw, draft = tmp_path / "SYN4-raw.json", tmp_path / "SYN4-draft.json"
    safe.update(run_id="unrelated_allowed_source", episodes=[{
        "episode_id": "SYN4", "raw": str(raw), "draft": str(draft),
        "raw_sha256": sha256_file(raw), "draft_sha256": sha256_file(draft),
        "source_review": None, "source_review_sha256": None, "prior_findings": []}])
    pipeline.run(safe, through="evidence", backend=SyntheticModel())
    return {"kind": "completed", "manifest": pin(root(safe) / "run_manifest.json"),
            "artifact": pin(root(safe) / "SYN4/evidence.json")}


def guarded_selection(descriptor, stage, safe, tmp_path, monkeypatch, *, reserved=True):
    value = {"schema_version": inventory.SELECTION_VERSION, "corpus_id": "reserved_lineage_guard",
        "expected_episode_ids": ["SYN4"], "reserved_episode_ids": ["SYN1"] if reserved else [],
        "episodes": [{"episode_id": "SYN4", "evidence": available(safe)}],
        "expected_synthesis_ids": [], "syntheses": []}
    if stage == "evidence":
        value["expected_episode_ids"].append("SYN3")
        value["episodes"].append({"episode_id": "SYN3", "evidence": available(descriptor)})
    else:
        value["expected_synthesis_ids"] = ["derived"]
        value["syntheses"] = [{"synthesis_id": "derived", "synthesis": available(descriptor)}]
    path = tmp_path / "guarded-selection.json"
    atomic_write_json(path, value)
    forbidden = Mock(side_effect=AssertionError("Metadata preflight must precede every source load"))
    monkeypatch.setattr(pipeline, "_sources", forbidden)
    with pytest.raises(ValueError, match="reserved sources" if reserved else "manifest header"):
        inventory.inspect(pin(path))
    forbidden.assert_not_called()


@pytest.mark.parametrize("kind,reserved", [("completed", True), ("recovered", True),
    ("editorial_completed", True), ("editorial_completed", False),
    ("editorial_recovered", True), ("editorial_recovered", False)])
def test_evidence_authoritative_parent_preflight(cfg, tmp_path, monkeypatch, kind, reserved):
    cfg["episodes"] = cfg["episodes"][:1]
    if kind in {"completed", "editorial_completed"}:
        pipeline.run(cfg, through="evidence", backend=SyntheticModel())
        parent = {"kind": "completed", "manifest": pin(root(cfg) / "run_manifest.json"),
                  "artifact": pin(root(cfg) / "SYN1/evidence.json")}
    else:
        with pytest.raises(ValueError, match="attempts exhausted"):
            pipeline.run(cfg, through="evidence", backend=ContextModel())
        recovery = {"schema_version": recover_evidence.CONFIG_VERSION, "commentary_scope": schemas.EPISODE_CONTEXT,
                    "manifest": pin(root(cfg) / "run_manifest.json"),
                    "request": pin(root(cfg) / "calls/SYN1_evidence_1.request.json"),
                    "result": pin(root(cfg) / "calls/SYN1_evidence_1.result.json")}
        parent = {"kind": "recovered", "artifact": recover_evidence.apply(recovery, tmp_path / "recovered/evidence.json")}
    safe = safe_source_run(cfg, tmp_path)
    descriptor = parent
    if kind.startswith("editorial"):
        row = read_json(Path(parent["artifact"]["path"]))["content"]["items"][0]
        source = pipeline._sources(cfg)["SYN1"]
        config = {"schema_version": evidence_revision.CONFIG_VERSION, "commentary_scope": schemas.EPISODE_CONTEXT,
            "parent": parent, "reviewer": "Synthetic reviewer", "rationale": "Keep uncertainty explicit",
            "edits": [{"evidence_id": row["evidence_id"], "field": "/uncertainty", "expected": row["uncertainty"],
                "value": row["uncertainty"] + ["Synthetic unresolved context"], "reason": "No invented outcome",
                "source_anchors": [source["raw_segments"][0]]}]}
        config_path, output = tmp_path / "revision-config.json", tmp_path / "revision/evidence.json"
        atomic_write_json(config_path, config)
        evidence_revision.apply(pin(config_path), output)
        artifact = read_json(output)
        artifact["parent_manifest"] = safe["manifest"]
        atomic_write_json(output, artifact)
        descriptor = {"kind": "editorial", "artifact": pin(output)}
    guarded_selection(descriptor, "evidence", safe, tmp_path, monkeypatch, reserved=reserved)


@pytest.mark.parametrize("editorial,reserved", [(False, True), (True, True), (True, False)])
def test_completed_synthesis_reserved_preflight_before_source_loading(cfg, tmp_path, monkeypatch, editorial, reserved):
    data = make_parent(cfg, tmp_path)
    safe = safe_source_run(data["cfg"], tmp_path)
    if editorial:
        review.apply(data["path"], data["output"])
        artifact = read_json(data["output"])
        artifact["parent_manifest"] = safe["manifest"]
        atomic_write_json(data["output"], artifact)
        descriptor = {"kind": "editorial_completed", "artifact": pin(data["output"])}
    else:
        descriptor = {"kind": "completed", "manifest": pin(root(data["cfg"]) / "run_manifest.json"),
                      "artifact": pin(root(data["cfg"]) / "synthesis.json")}
    guarded_selection(descriptor, "synthesis", safe, tmp_path, monkeypatch, reserved=reserved)


@pytest.mark.parametrize("kind", ["recovered", "editorial_recovered", "nested_recovered_header"])
@pytest.mark.parametrize("reserved", [True, False])
def test_recovered_synthesis_authoritative_proposal_preflight(failed, tmp_path, monkeypatch, kind, reserved):
    cfg, recovery_path, output = failed
    parent_ref = recover_synthesis.apply(recovery_path, output)
    parent = read_json(output)
    safe = safe_source_run(cfg, tmp_path)
    descriptor = {"kind": "recovered", "artifact": parent_ref}
    if kind != "recovered":
        raw = pipeline._sources(cfg)["SYN1"]["raw_segments"][0]
        proposal = {"schema_version": review_recovered.PROPOSAL_VERSION, "parent_artifact": parent_ref,
            "reviewer": "Synthetic reviewer", "method": "Exact fictional source reading", "rationale": "Narrow wording",
            "edits": [{"hypothesis_id": "PROFILE:H1", "field": "/label",
                "expected": parent["content"]["hypotheses"][0]["label"], "value": "Synthetic contextual comparison",
                "reason": "Do not generalize", "evidence_ids": ["SYN1:E1"],
                "source_anchors": [{"episode_id": "SYN1", **{k: raw[k] for k in ("segment_id", "start", "end", "text")}}]}]}
        path, edited = tmp_path / "revision-proposal.json", tmp_path / "revision/synthesis.json"
        atomic_write_json(path, proposal)
        review_recovered.apply(path, edited)
        if kind == "nested_recovered_header":
            parent["parent"]["manifest"] = safe["manifest"]
            atomic_write_json(output, parent)
            proposal["parent_artifact"] = pin(output)
            atomic_write_json(path, proposal)
        output = edited
        artifact = read_json(output)
        artifact["proposal"] = pin(path)
        descriptor = {"kind": "editorial_recovered", "artifact": pin(output)}
    else:
        artifact = parent
    artifact["parent"]["manifest"] = safe["manifest"]
    atomic_write_json(output, artifact)
    descriptor["artifact"] = pin(output)
    guarded_selection(descriptor, "synthesis", safe, tmp_path, monkeypatch, reserved=reserved)
