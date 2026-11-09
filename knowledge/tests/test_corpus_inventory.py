"""Synthetic corpus inventory and known-link checks; no real sources or inference."""
import copy
import gc
import weakref
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge import corpus_inventory as inventory, pipeline
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_annotations import annotated_cfg, repin
from .test_candidate_extraction import CandidateModel, candidate
from .test_pipeline import cfg


def pin(path):
    return {"path": str(path), "sha256": sha256_file(path)}


class InventoryModel(CandidateModel):
    def complete(self, *args):
        value, usage = super().complete(*args)
        if "strategy" in value:
            eid = value["episode_id"]
            first = value["principles"][0]
            first.update(supporting_records=[f"{eid}:R1"], context_relationships=[],
                         principle_type="case_derived_hypothesis",
                         counterexamples=[{"record_id": f"{eid}:R2", "explanation": "Synthetic exception in another group"}],
                         contradictions=[{"record_id": f"{eid}:R2", "explanation": "Synthetic unresolved opposing account"}])
            first["proposed_assessment"]["contradiction_severity"] = "major"
            second = copy.deepcopy(first)
            second.update(principle_id=f"{eid}:P2", supporting_records=[f"{eid}:R2"],
                          core_preference="Another synthetic scope, not a new support for the first pattern",
                          counterexamples=[], contradictions=[{"record_id": f"{eid}:R1", "explanation": "Other scope's counter"}])
            value["principles"].append(second)
        return value, usage


def selection_for(config, tmp_path):
    pipeline.run(config, through="level3", backend=InventoryModel())
    config_path = tmp_path / "input-config.json"
    atomic_write_json(config_path, config)
    selection = {"schema_version": inventory.SELECTION_VERSION, "corpus_id": "synthetic_inventory",
        "expected_episode_ids": [entry["episode_id"] for entry in config["episodes"]], "episodes": []}
    for entry in config["episodes"]:
        eid = entry["episode_id"]
        selection["episodes"].append({"episode_id": eid, "config": pin(config_path), "stages": {
            stage: {"status": "available", "artifact": pin(pipeline.stage_location(config, stage, eid)[0])}
            for stage in pipeline.STAGES[:3]}})
    path = tmp_path / "selection.json"
    atomic_write_json(path, selection)
    return {"cfg": config, "path": path, "selection": selection, "output": tmp_path / "inventory.json"}


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Inventory must not invoke a provider"))
    monkeypatch.setattr("knowledge.codex_backend.CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


@pytest.fixture
def ready(cfg, tmp_path):
    return selection_for(cfg, tmp_path)


def save_selection(data):
    atomic_write_json(data["path"], data["selection"])
    return pin(data["path"])


def test_complete_freeze_replays_without_changes_or_pipeline_execution(ready, tmp_path, monkeypatch):
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    forbidden = Mock(side_effect=AssertionError("Inventory must only call read-only loaders"))
    monkeypatch.setattr(pipeline, "run", forbidden)
    inspected = inventory.inspect(pin(ready["path"]))
    assert {p: p.read_bytes() for p in before} == before and not ready["output"].exists()
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    frozen = inventory.load_checked(ref)
    assert frozen == inspected
    assert frozen["inventory_complete"] and frozen["gaps"] == []
    assert set(frozen["cases"]) == {"SYN1:R1", "SYN1:R2", "SYN2:R1", "SYN2:R2"}
    assert set(frozen["principles"]) == {"SYN1:P1", "SYN1:P2", "SYN2:P1", "SYN2:P2"}
    assert frozen["policy"] == inventory.POLICY
    assert not frozen["policy"]["publication_authorized"] and not frozen["policy"]["eligible_for_serving"]
    assert frozen["policy"]["semantic_coverage"] == "not_assessed"
    for episode in frozen["episodes"].values():
        assert len(episode["level2_record_ids"]) == len(episode["level3_principle_ids"]) == 2
    for rid, record in frozen["cases"].items():
        source = read_json(Path(record["artifact"]["path"]))
        assert source["content"]["cases"][int(record["content_pointer"].split("/")[-1])]["record_id"] == rid
        assert "claims" not in record  # The index is not a duplicate canonical evidence artifact.
    assert {p: p.read_bytes() for p in before} == before
    assert {p for p in tmp_path.rglob("*") if p.is_file()} - set(before) == {ready["output"]}
    with pytest.raises(FileExistsError): inventory.freeze(pin(ready["path"]), ready["output"])
    assert inventory.load_checked(ref) == frozen
    forbidden.assert_not_called()


@pytest.mark.parametrize("mode", ["reviewed", "candidate"])
def test_episode_sources_and_full_artifacts_are_freed_before_next_load(cfg, tmp_path, monkeypatch, mode):
    data = selection_for(candidate(cfg) if mode == "candidate" else cfg, tmp_path)
    original_sources, original_checked, original_read = pipeline.sources, pipeline.checked_artifact, inventory._read
    tracked, loaded = [], []

    class TrackedDict(dict):
        pass

    class TrackedList(list):
        pass

    def track(value):
        value = TrackedDict(value) if isinstance(value, dict) else TrackedList(value)
        tracked.append(weakref.ref(value))
        return value

    def sources(config):
        ids = [entry["episode_id"] for entry in config["episodes"]]
        assert len(ids) == 1
        if loaded and loaded[-1] != ids[0]:
            gc.collect()
            assert all(ref() is None for ref in tracked), "Previous episode's raw sources/artifacts are still retained"
        loaded.append(ids[0])
        result = track(original_sources(config))
        for eid, source in result.items():
            result[eid] = track(source)
            result[eid]["segments"] = track(source["segments"])
            result[eid]["raw_segments"] = track(source["raw_segments"])
        return result

    def checked(*args, **kwargs):
        artifact, checksum = original_checked(*args, **kwargs)
        artifact = track(artifact)
        artifact["content"] = track(artifact["content"])
        return artifact, checksum

    def read(*args, **kwargs):
        value = original_read(*args, **kwargs)
        return track(value) if isinstance(value, dict) and value.get("stage") in pipeline.STAGES[:3] else value

    monkeypatch.setattr(pipeline, "sources", sources)
    monkeypatch.setattr(pipeline, "checked_artifact", checked)
    monkeypatch.setattr(inventory, "_read", read)
    result = inventory.inspect(pin(data["path"]))
    gc.collect()
    assert all(ref() is None for ref in tracked)
    assert loaded == ["SYN1", "SYN1", "SYN2", "SYN2"]
    assert len(result["cases"]) == len(result["principles"]) == 4


def test_subset_source_load_uses_full_original_config_for_artifact_validation(ready, monkeypatch):
    expected = pipeline.load_config(Path(ready["selection"]["episodes"][0]["config"]["path"]))
    original_sources, original_checked, original_load = pipeline.sources, pipeline.checked_artifact, pipeline.load_config
    loads, source_calls, stages = [], [], []

    def load(path):
        loads.append(path)
        return original_load(path)

    def sources(config):
        assert len(config["episodes"]) == 1
        assert {k: v for k, v in config.items() if k != "episodes"} == {
            k: v for k, v in expected.items() if k != "episodes"}
        assert config["episodes"][0] in expected["episodes"]
        source_calls.append(config["episodes"][0]["episode_id"])
        return original_sources(config)

    def checked(config, source_map, stage, source, *args, **kwargs):
        assert config == expected and len(config["episodes"]) == 2
        assert list(source_map) == [source["episode_id"]] and source_map[source["episode_id"]] is source
        stages.append((source["episode_id"], stage))
        return original_checked(config, source_map, stage, source, *args, **kwargs)

    monkeypatch.setattr(pipeline, "load_config", load)
    monkeypatch.setattr(pipeline, "sources", sources)
    monkeypatch.setattr(pipeline, "checked_artifact", checked)
    result = inventory.inspect(pin(ready["path"]))
    assert len(loads) == 1  # Only parsed config is cached across the two episodes.
    assert source_calls == ["SYN1", "SYN1", "SYN2", "SYN2"]
    assert stages == [(eid, stage) for eid in ("SYN1", "SYN2") for stage in pipeline.STAGES[:3]]
    assert result["inventory_complete"]


def test_unselected_configured_episode_source_is_never_loaded(ready, monkeypatch):
    ready["selection"]["expected_episode_ids"] = ["SYN1"]
    ready["selection"]["episodes"] = ready["selection"]["episodes"][:1]
    excluded = Path(ready["cfg"]["episodes"][1]["raw"])
    excluded.write_text("Not JSON; this unselected source must not be opened")
    original = pipeline.sources
    def sources(config):
        assert [entry["episode_id"] for entry in config["episodes"]] == ["SYN1"]
        return original(config)
    monkeypatch.setattr(pipeline, "sources", sources)
    result = inventory.inspect(save_selection(ready))
    assert set(result["episodes"]) == {"SYN1"}
    assert set(result["cases"]) == {"SYN1:R1", "SYN1:R2"}
    assert str(excluded) not in {ref["path"] for ref in result["dependencies"]}


def test_prior_episode_drift_after_local_recheck_fails_final_global_pins(ready, monkeypatch):
    original = pipeline.sources
    prior = Path(ready["cfg"]["episodes"][0]["raw"])
    loaded = []
    def sources(config):
        ids = [entry["episode_id"] for entry in config["episodes"]]
        assert len(ids) == 1
        if ids[0] == "SYN2" and "SYN2" not in loaded:
            assert loaded == ["SYN1", "SYN1"]
            value = read_json(prior); value["late_mutation"] = True; atomic_write_json(prior, value)
        loaded.append(ids[0])
        return original(config)
    monkeypatch.setattr(pipeline, "sources", sources)
    with pytest.raises(ValueError, match="changed"):
        inventory.freeze(pin(ready["path"]), ready["output"])
    assert loaded == ["SYN1", "SYN1", "SYN2", "SYN2"]
    assert not ready["output"].exists()


@pytest.mark.parametrize("kind", ["case", "principle"])
def test_incremental_indexes_reject_duplicate_global_identity(ready, kind):
    entry = ready["selection"]["episodes"][0]
    cfg = pipeline.load_config(Path(entry["config"]["path"]))
    cases = {"SYN1:R1": {}} if kind == "case" else {}
    principles = {"SYN1:P1": {}} if kind == "principle" else {}
    with pytest.raises(ValueError, match="Duplicate"):
        inventory._inspect_episode(entry, entry["config"], cfg, {}, cases, principles, [])


def test_known_closure_includes_all_overlapping_ineligible_parents_not_counter_as_support(ready):
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    closure = inventory.known_closure(ref, ["SYN1:R1", "SYN2:R1"])
    assert len(closure["obligations"]) == 4
    assert closure["counter_record_ids"] == ["SYN1:R2", "SYN2:R2"]
    assert {row["source_principle_id"] for row in closure["obligations"]} == {"SYN1:P1", "SYN2:P1"}
    assert all(not row["parent_eligible_for_level4"] for row in closure["obligations"])
    assert all(row["status"] == "requires_scope_review" for row in closure["obligations"])
    assert all("disposition" not in row for row in closure["obligations"])
    assert closure["policy"] == inventory.POLICY
    for row in closure["obligations"]:
        parent = read_json(Path(row["parent_artifact"]["path"]))
        parts = row["link_pointer"].split("/")
        link = parent["content"]["principles"][int(parts[3])][parts[4]][int(parts[5])]
        assert link == {"record_id": row["record_id"], "explanation": row["explanation"]}


@pytest.mark.parametrize("status", ["missing", "failed", "held"])
def test_explicit_gaps_freeze_as_partial_without_silently_discovering_files(ready, status, tmp_path):
    receipt = tmp_path / "diagnostic.txt"
    receipt.write_text("Synthetic diagnostic, not a model request or source acceptance")
    ready["selection"]["episodes"][0]["stages"]["level3"] = {
        "status": status, "reason": "Explicit synthetic stage gap", "record": pin(receipt)}
    ref = inventory.freeze(save_selection(ready), ready["output"])
    data = inventory.load_checked(ref)
    assert not data["inventory_complete"] and len(data["gaps"]) == 1
    assert data["gaps"][0]["status"] == status
    assert data["episodes"]["SYN1"]["level3_principle_ids"] == []
    assert set(data["cases"]) == {"SYN1:R1", "SYN1:R2", "SYN2:R1", "SYN2:R2"}
    closure = inventory.known_closure(ref, ["SYN1:R1"])
    assert closure["obligations"] == [] and not closure["inventory_complete"]
    assert pin(receipt) in data["dependencies"]


def test_expected_missing_episode_remains_in_inventory(ready):
    ready["selection"]["expected_episode_ids"].append("SYN3")
    ready["selection"]["episodes"].append({"episode_id": "SYN3", "config": None, "stages": {
        stage: {"status": "missing", "reason": "Not yet extracted", "record": None} for stage in pipeline.STAGES[:3]}})
    result = inventory.inspect(save_selection(ready))
    assert not result["inventory_complete"] and len(result["gaps"]) == 3
    assert result["episodes"]["SYN3"]["source"] is None
    assert result["episodes"]["SYN3"]["level2_record_ids"] == []


def test_candidate_source_hold_does_not_masquerade_as_missing_stage(cfg, tmp_path):
    config = candidate(cfg)
    for entry in config["episodes"]:
        path = Path(entry["review"])
        review = read_json(path)
        review.update(decision="uncertain", unresolved_critical_issues=["Synthetic local ambiguity remains"])
        atomic_write_json(path, review)
    data = selection_for(config, tmp_path)
    result = inventory.load_checked(inventory.freeze(pin(data["path"]), data["output"]))
    assert result["inventory_complete"]
    for entry in result["episodes"].values():
        assert entry["source"]["prior_source_decision"] == "uncertain"
        assert entry["source"]["prior_review_limitations"] == ["Synthetic local ambiguity remains"]
        assert entry["source"]["source_acceptance"] == "not_granted"
    assert result["policy"]["source_acceptance"] == "not_granted"


def test_empty_l3_bypass_is_complete_not_a_gap(cfg, tmp_path):
    class Bypass(InventoryModel):
        def complete(self, *args):
            value, usage = super().complete(*args)
            if "strategy" in value:
                value.update(strategy="bypass", strategy_reason="No synthetic abstraction justified", principles=[])
            return value, usage
    pipeline.run(cfg, through="level3", backend=Bypass())
    data = selection_for(cfg, tmp_path)
    result = inventory.inspect(pin(data["path"]))
    assert result["inventory_complete"] and result["principles"] == {} and len(result["cases"]) == 4


@pytest.mark.parametrize("fault", ["omitted_episode", "extra_episode", "duplicate_episode", "duplicate_expected",
    "missing_stage", "nonprefix", "missing_config", "wrong_episode", "wrong_stage_path", "wrong_hash",
    "blank_reason", "unknown_status", "unknown_field", "wrong_schema"])
def test_invalid_selection_fails_closed(ready, fault):
    selection = ready["selection"]
    entry = selection["episodes"][0]
    if fault == "omitted_episode": selection["episodes"].pop()
    elif fault == "extra_episode": selection["expected_episode_ids"].pop()
    elif fault == "duplicate_episode": selection["episodes"].append(copy.deepcopy(entry))
    elif fault == "duplicate_expected": selection["expected_episode_ids"].append("SYN1")
    elif fault == "missing_stage": entry["stages"].pop("level3")
    elif fault == "nonprefix": entry["stages"]["level1"] = {"status": "missing", "reason": "Synthetic gap", "record": None}
    elif fault == "missing_config": entry["config"] = None
    elif fault == "wrong_episode":
        entry["episode_id"] = "OTHER"; selection["expected_episode_ids"][0] = "OTHER"
    elif fault == "wrong_stage_path": entry["stages"]["level3"]["artifact"] = entry["stages"]["level2"]["artifact"]
    elif fault == "wrong_hash": entry["stages"]["level2"]["artifact"]["sha256"] = "a" * 64
    elif fault in {"blank_reason", "unknown_status"}:
        entry["stages"]["level3"] = {"status": "held" if fault == "blank_reason" else "accepted",
                                     "reason": " " if fault == "blank_reason" else "Synthetic", "record": None}
    elif fault == "unknown_field": selection["semantic_coverage"] = "complete"
    else: selection["schema_version"] = "profile.v1"
    with pytest.raises(ValueError): inventory.freeze(save_selection(ready), ready["output"])
    assert not ready["output"].exists()


@pytest.mark.parametrize("target", ["raw", "normalized", "review", "config", "level2", "level3", "selection"])
def test_frozen_dependency_changes_are_rejected(ready, target):
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    entry = ready["selection"]["episodes"][0]
    path = (ready["path"] if target == "selection" else Path(entry["config"]["path"]) if target == "config" else
            Path(entry["stages"][target]["artifact"]["path"]) if target in {"level2", "level3"} else
            Path(ready["cfg"]["episodes"][0][target]))
    value = read_json(path); value["synthetic_mutation"] = True; atomic_write_json(path, value)
    with pytest.raises(ValueError): inventory.load_checked(ref)


@pytest.mark.parametrize("target", ["complete", "case", "counter", "policy", "dependencies"])
def test_rebound_freeze_hash_cannot_hide_index_tampering(ready, target):
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    result = inventory.load_checked(ref)
    if target == "complete": result["inventory_complete"] = False
    elif target == "case": result["cases"].pop("SYN1:R2")
    elif target == "counter": result["principles"]["SYN1:P1"]["contradictions"] = []
    elif target == "policy": result["policy"]["publication_authorized"] = True
    else: result["dependencies"].pop()
    atomic_write_json(ready["output"], result)
    with pytest.raises(ValueError): inventory.load_checked(pin(ready["output"]))


@pytest.mark.parametrize("support", [[], ["SYN1:R1", "SYN1:R1"], ["OTHER:R1"], ["SYN1:P1"], [False], "SYN1:R1"])
def test_closure_rejects_unknown_duplicate_or_noncase_support(ready, support):
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    with pytest.raises(ValueError): inventory.known_closure(ref, support)


def test_environment_cannot_substitute_historical_model(ready, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_MODEL_ID", "different-synthetic-model")
    with pytest.raises(ValueError, match="environment"):
        inventory.inspect(pin(ready["path"]))


def test_source_race_fails_before_write(ready, monkeypatch):
    original = pipeline.checked_artifact
    path = Path(ready["cfg"]["episodes"][0]["raw"])
    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        value = read_json(path); value["segments"][0]["text"] += " mutation"; atomic_write_json(path, value)
        return result
    monkeypatch.setattr(pipeline, "checked_artifact", racing)
    with pytest.raises(ValueError): inventory.freeze(pin(ready["path"]), ready["output"])
    assert not ready["output"].exists()


def test_source_annotation_dependencies_are_frozen(annotated_cfg, tmp_path):
    config, sidecar, supporting = annotated_cfg
    data = selection_for(candidate(config), tmp_path)
    ref = inventory.freeze(pin(data["path"]), data["output"])
    result = inventory.load_checked(ref)
    assert pin(sidecar) in result["dependencies"]
    assert pin(supporting) in result["dependencies"]
    value = read_json(supporting); value["synthetic_mutation"] = True; atomic_write_json(supporting, value)
    with pytest.raises(ValueError): inventory.load_checked(ref)


def test_plaintext_review_attachment_is_pinned_without_parsing(annotated_cfg, tmp_path):
    config, sidecar, _ = annotated_cfg
    report = tmp_path / "synthetic-review.txt"
    report.write_text("Fictional source-reading note, not audio review or acceptance.")
    notes = read_json(sidecar)
    notes["supporting_reviews"].append(pin(report))
    atomic_write_json(sidecar, notes)
    repin(Path(config["episodes"][0]["review"]), sidecar)
    data = selection_for(candidate(config), tmp_path)
    ref = inventory.freeze(pin(data["path"]), data["output"])
    assert pin(report) in inventory.load_checked(ref)["dependencies"]
    report.write_text("Changed fictional review")
    with pytest.raises(ValueError): inventory.load_checked(ref)


def test_reused_stage_references_remain_in_original_run(ready, tmp_path):
    config = copy.deepcopy(ready["cfg"])
    config["run_id"] = "reused_synthetic"
    for entry, original in zip(config["episodes"], ready["selection"]["episodes"]):
        entry["reuse_artifacts"] = {stage: copy.deepcopy(original["stages"][stage]["artifact"])
                                   for stage in pipeline.STAGES[:3]}
    data = selection_for(config, tmp_path)
    old_root = Path(ready["cfg"]["output_root"]) / ready["cfg"]["run_id"]
    with pytest.raises(ValueError, match="outside"):
        inventory.freeze(pin(data["path"]), old_root / "inventory.json")
    result = inventory.load_checked(inventory.freeze(pin(data["path"]), data["output"]))
    assert result["inventory_complete"]
    for entry in config["episodes"]:
        for stage in pipeline.STAGES[:3]:
            assert result["episodes"][entry["episode_id"]]["stages"][stage]["artifact"] == entry["reuse_artifacts"][stage]
            assert not (Path(config["output_root"]) / config["run_id"] / entry["episode_id"] / (stage + ".json")).exists()


def test_current_implementation_is_not_silently_relabelled(ready, monkeypatch):
    ref = inventory.freeze(pin(ready["path"]), ready["output"])
    changed = copy.deepcopy(inventory._implementation())
    changed["prompts"]["level3"] = "c" * 64
    monkeypatch.setattr(inventory, "_implementation", lambda: changed)
    with pytest.raises(ValueError, match="reconstruction"):
        inventory.load_checked(ref)


@pytest.mark.parametrize("token", ['"corpus_id": "duplicate",', '"unrecognized": NaN,'])
def test_duplicate_keys_and_nonfinite_json_are_rejected(ready, token):
    ready["path"].write_text(ready["path"].read_text().replace("{", "{" + token, 1))
    with pytest.raises(ValueError): inventory.inspect(pin(ready["path"]))


def test_private_boundaries_original_run_and_safe_cli(ready, capsys):
    with pytest.raises(ValueError, match="public repository"):
        inventory.freeze(pin(ready["path"]), Path(inventory.__file__).parent / "invalid.json")
    with pytest.raises(ValueError, match="outside"):
        inventory.freeze(pin(ready["path"]), Path(ready["cfg"]["output_root"]) / ready["cfg"]["run_id"] / "inventory.json")
    args = ["--input", str(ready["path"]), "--sha256", sha256_file(ready["path"])]
    assert inventory.main(["inspect", *args]) == 0
    assert inventory.main(["freeze", *args, "--output", str(ready["output"])]) == 0
    args = ["--input", str(ready["output"]), "--sha256", sha256_file(ready["output"])]
    assert inventory.main(["validate", *args]) == 0
    assert inventory.main(["closure", *args, "--support", "SYN1:R1"]) == 0
    assert inventory.main(["validate", "--input", str(ready["output"]), "--sha256", "a" * 64]) == 2
    text = capsys.readouterr().out
    assert str(ready["path"].parent) not in text and "Synthetic exception" not in text
    assert '"model_calls": 0' in text
