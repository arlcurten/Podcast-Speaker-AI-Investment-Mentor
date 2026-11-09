"""Synthetic native/mixed inventory integration; no real sources or inference."""
import copy
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import corpus_discovery as discovery, corpus_inventory as inventory
from speaker_profile import evidence_revision, native_evidence as native, pipeline, recover_evidence, schemas
from transcription.common import atomic_write_json, read_json
from transcription.tests.test_annotations import annotation_review
from .test_pipeline import cfg, root
from .test_review import ParentModel, pin
from .test_corpus_inventory import available, selection_for, save
from .test_native_evidence import submit
from .test_recover_evidence import ContextModel


@pytest.fixture(autouse=True)
def no_backend(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No live inference, subprocess or network"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.CodexBackend.complete", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    yield
    forbidden.assert_not_called()


def native_candidate(entry, directory, *, annotated=False):
    value = {"schema_version": native.CONFIG_VERSION, "packet_id": "synthetic-native",
        "actor": {"identity": "Synthetic native author", "session_id": "synthetic-existing-session"},
        "episode": {"episode_id": entry["episode_id"], "raw": pin(Path(entry["raw"])),
                    "normalized": pin(Path(entry["draft"])), "source_review": None},
        "reserved_episode_ids": ["RESERVED"], "commentary_scope": schemas.EPISODE_CONTEXT,
        "max_packet_bytes": 1_000_000, "max_submission_bytes": 200_000}
    dependencies = []
    if annotated:
        dependencies = list(annotation_review(Path(entry["raw"]), directory / "source-review"))
        review = read_json(dependencies[0])
        review.update(decision="uncertain", unresolved_critical_issues=["Synthetic ambiguity remains unresolved"])
        atomic_write_json(dependencies[0], review)
        value["episode"]["source_review"] = pin(dependencies[0])
    config = directory / "config.json"
    atomic_write_json(config, value)
    packet_ref = native.freeze(pin(config), directory / "packet.json")
    submission = submit(packet_ref, directory)
    data = read_json(submission)
    data["content"]["items"][0]["speaker_attribution"] = "listener"
    data["content"]["items"][2]["speaker_attribution"] = "listener"
    data["content"]["items"][0]["supplementary_notes"] = [
        {"merged_ids": [2], "note": "Synthetic source context, not an independent observation."}]
    atomic_write_json(submission, data)
    artifact = native.apply(pin(submission), directory / "candidate.json")
    return {"descriptor": {"kind": "native", "artifact": artifact}, "config": config,
            "packet": packet_ref, "submission": submission, "dependencies": dependencies}


@pytest.fixture
def mixed(cfg, tmp_path):
    original = copy.deepcopy(cfg)
    cfg["episodes"] = cfg["episodes"][:2]
    pipeline.run(cfg, backend=ParentModel())
    data = selection_for(cfg, tmp_path)
    candidate = native_candidate(original["episodes"][2], tmp_path / "native", annotated=True)
    data["selection"]["expected_episode_ids"].append("SYN3")
    data["selection"]["episodes"].append({"episode_id": "SYN3", "evidence": available(candidate["descriptor"])})
    save(data)
    return data, candidate, original


def files(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_native_mixed_index_reuses_old_artifacts_and_packet_pins(mixed, tmp_path, monkeypatch):
    data, n, _ = mixed
    before = files(tmp_path)
    native_impl = native._implementation()
    packet = native.load_packet(n["packet"])
    monkeypatch.setattr(pipeline, "run", Mock(side_effect=AssertionError("No pipeline resume")))
    inspected = inventory.inspect(pin(data["path"]))
    ref = inventory.freeze(pin(data["path"]), data["output"])
    result = inventory.load_checked(ref)
    assert result == inspected and result["schema_version"] == "profile_corpus_inventory.v2"
    assert len(result["evidence"]) == 9 and result["hypotheses"]
    entry = result["episodes"]["SYN3"]
    assert entry["original_manifest"] is None
    assert entry["authority"] == native.AUTHORITY
    assert entry["model_metadata"] == native.MODEL_METADATA
    assert entry["adoption"]["model_calls_this_execution"] == 0
    assert entry["native_lineage"]["config"] == pin(n["config"])
    assert entry["native_lineage"]["packet"] == n["packet"]
    assert entry["native_lineage"]["submission"] == pin(n["submission"])
    assert result["evidence"]["SYN3:E3"]["basis_evidence_ids"] == ["SYN3:E1"]
    assert result["evidence"]["SYN3:E1"]["speaker_attribution"] == "listener"
    assert all(result["evidence"][rid]["artifact_kind"] == "native" for rid in entry["evidence_ids"])
    assert not entry["policy"]["eligible_for_serving"] and entry["policy"]["review_status"] == "unreviewed"
    assert result["policy"]["independent_event_count"] is None
    deps = {r["path"] for r in result["dependencies"]}
    assert all(str(p) in deps for p in [n["config"], n["submission"], *n["dependencies"]])
    assert inventory.known_closure(ref, ["SYN3:E3"])["required_evidence_ids"] == ["SYN3:E1", "SYN3:E3"]
    assert all(p.read_bytes() == value for p, value in before.items())
    assert native._implementation() == native_impl
    assert native.load_packet(n["packet"]) == packet
    assert native.load_checked(n["descriptor"]["artifact"])["model_metadata"]["usage"] is None


def test_direct_native_materialization_and_lossless_discovery_context(mixed, tmp_path, monkeypatch):
    data, n, _ = mixed
    ref = inventory.freeze(pin(data["path"]), data["output"])
    catalog = inventory.load_checked(ref)
    prompt = discovery.PROMPT_PATH.read_text()
    materials = discovery._Materials(catalog, {})
    original_read = discovery.shared._read
    def read(ref, pins):
        assert ref is not None, "Native materialization tried to read a backend manifest"
        return original_read(ref, pins)
    monkeypatch.setattr(discovery.shared, "_read", read)
    context = discovery._expand(discovery._context(["SYN3:E3"], catalog, materials, ref,
                                prompt=prompt, max_chars=1_000_000))
    assert {r["evidence_id"] for r in context["evidence"]} == {"SYN3:E1", "SYN3:E3"}
    assert {r["segment_id"] for r in context["raw_regions"]["SYN3"]} == {0, 2}
    source = context["sources"]["SYN3"]
    assert source["source"]["prior_source_decision"] == "uncertain"
    assert source["source"]["source_acceptance"] == "not_granted"
    provenance = source["native_provenance"]
    artifact = native.load_checked(n["descriptor"]["artifact"])
    assert provenance == {k: artifact[k] for k in ("authority", "authorship", "model_metadata", "adoption")}
    assert provenance["model_metadata"]["model"] is provenance["model_metadata"]["usage"] is None
    assert source["unselected_regions"] == artifact["grounding"]["unselected_regions"]
    for row in context["evidence"]:
        assert row["grounding"] == artifact["grounding"]["items"][row["evidence_id"]]
    assert discovery._expand(discovery._pack(context)) == context
    config = {"schema_version": discovery.CONFIG_VERSION, "run_id": "new-contract",
        "output_root": str(tmp_path / "discovery"), "inventory": ref, "model": "synthetic",
        "reasoning_effort": "high", "timeout_seconds": 10, "max_input_chars": 1_000_000,
        "max_response_chars": 100_000, "max_calls": 1, "retry_count": 0}
    path = tmp_path / "new-discovery-config.json"
    atomic_write_json(path, config)
    before = files(tmp_path)
    plan = discovery.execute(pin(path), command="plan")
    assert plan["model_calls_this_execution"] == 0 and plan["admitted_calls"] == 0
    assert {rid for p in plan["packet_plan"] for rid in p["owned_evidence_ids"]} == set(catalog["evidence"])
    assert files(tmp_path) == before
    assert not root(config).exists()


@pytest.mark.parametrize("reserved", [True, False])
def test_authoritative_native_config_preflight_before_any_source_reads(mixed, tmp_path, monkeypatch, reserved):
    data, n, original = mixed
    decoy = native_candidate(original["episodes"][3], tmp_path / "decoy")
    path = Path(n["descriptor"]["artifact"]["path"])
    artifact = read_json(path)
    artifact["source"]["episode_id"] = "SYN4"
    artifact["packet"] = decoy["packet"]
    artifact["original_manifest"] = pin(root(data["cfg"]) / "run_manifest.json")
    atomic_write_json(path, artifact)
    data["selection"]["expected_episode_ids"][-1] = "SYN4"
    entry = data["selection"]["episodes"][-1]
    entry["episode_id"] = "SYN4"
    entry["evidence"]["input"]["artifact"] = pin(path)
    data["selection"]["reserved_episode_ids"] = ["SYN3"] if reserved else ["RESERVED"]
    selection_ref = save(data)
    forbidden = Mock(side_effect=AssertionError("Source replay started before all authoritative preflights"))
    monkeypatch.setattr(evidence_revision, "_sources", forbidden)
    monkeypatch.setattr(pipeline, "_sources", forbidden)
    monkeypatch.setattr(native, "load_checked", forbidden)
    with pytest.raises(ValueError, match="reserved" if reserved else "authoritative"):
        inventory.inspect(selection_ref)
    forbidden.assert_not_called()


def test_wrong_selected_episode_fails_in_metadata_preflight(mixed, monkeypatch):
    data, _, _ = mixed
    data["selection"]["expected_episode_ids"][-1] = "SYN4"
    data["selection"]["episodes"][-1]["episode_id"] = "SYN4"
    ref = save(data)
    forbidden = Mock(side_effect=AssertionError("No source reads on identity mismatch"))
    monkeypatch.setattr(evidence_revision, "_sources", forbidden)
    with pytest.raises(ValueError, match="config episode"):
        inventory.inspect(ref)
    forbidden.assert_not_called()


@pytest.mark.parametrize("fault", ["native_as_completed", "native_as_synthesis", "completed_as_native",
                                  "fake_manifest", "invented_usage", "duplicate_native", "changed_packet"])
def test_native_contracts_cannot_be_relabelled_or_promoted(mixed, fault):
    data, n, _ = mixed
    entry = data["selection"]["episodes"][-1]
    if fault == "native_as_completed":
        entry["evidence"]["input"] = {**n["descriptor"], "kind": "completed",
                                      "manifest": pin(root(data["cfg"]) / "run_manifest.json")}
    elif fault == "native_as_synthesis":
        data["selection"]["syntheses"][0]["synthesis"] = available(n["descriptor"])
    elif fault == "completed_as_native":
        data["selection"]["episodes"][0]["evidence"]["input"].pop("manifest")
        data["selection"]["episodes"][0]["evidence"]["input"]["kind"] = "native"
    elif fault == "duplicate_native":
        data["selection"]["episodes"].append(copy.deepcopy(entry))
    else:
        path = Path(n["descriptor"]["artifact"]["path"])
        artifact = read_json(path)
        if fault == "fake_manifest": artifact["original_manifest"] = pin(root(data["cfg"]) / "run_manifest.json")
        elif fault == "invented_usage": artifact["model_metadata"]["usage"] = {"input_tokens": 0}
        else: artifact["packet"]["sha256"] = "a" * 64
        atomic_write_json(path, artifact)
        entry["evidence"]["input"]["artifact"] = pin(path)
    with pytest.raises(ValueError):
        inventory.inspect(save(data))


@pytest.mark.parametrize("target", ["packet", "submission", "config", "raw", "annotation", "support"])
def test_native_dependency_tamper_invalidates_new_inventory(mixed, target):
    data, n, _ = mixed
    result = inventory.freeze(pin(data["path"]), data["output"])
    path = {"packet": Path(n["packet"]["path"]), "submission": n["submission"], "config": n["config"],
            "raw": Path(read_json(n["config"])["episode"]["raw"]["path"]),
            "annotation": n["dependencies"][1], "support": n["dependencies"][2]}[target]
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        inventory.load_checked(result)


@pytest.mark.parametrize("field", ["source_files", "original_manifest", "metadata", "policy"])
def test_repinned_index_cannot_forge_native_provenance(mixed, field):
    data, _, _ = mixed
    inventory.freeze(pin(data["path"]), data["output"])
    index = read_json(data["output"])
    row = index["episodes"]["SYN3"]
    if field == "source_files": row["native_lineage"]["source_files"]["raw"]["sha256"] = "a" * 64
    elif field == "original_manifest": row["original_manifest"] = index["episodes"]["SYN1"]["original_manifest"]
    elif field == "metadata": row["model_metadata"]["model"] = "guessed"
    else: row["policy"]["eligible_for_serving"] = True
    atomic_write_json(data["output"], index)
    with pytest.raises(ValueError, match="reconstruction"):
        inventory.load_checked(pin(data["output"]))


def test_native_only_zero_items_and_explicit_absence_of_synthesis(cfg, tmp_path):
    n = native_candidate(cfg["episodes"][0], tmp_path / "native")
    value = read_json(n["submission"])
    value["content"].update(items=[], unselected_regions=[{"merged_ids": [0, 1, 2], "note": "Synthetic no supported observation"}])
    atomic_write_json(n["submission"], value)
    descriptor = {"kind": "native", "artifact": native.apply(pin(n["submission"]), tmp_path / "native/zero.json")}
    selection = {"schema_version": inventory.SELECTION_VERSION, "corpus_id": "native-zero",
        "expected_episode_ids": ["SYN1"], "reserved_episode_ids": ["RESERVED"],
        "episodes": [{"episode_id": "SYN1", "evidence": available(descriptor)}],
        "expected_synthesis_ids": [], "syntheses": []}
    path = tmp_path / "selection.json"
    atomic_write_json(path, selection)
    result = inventory.load_checked(inventory.freeze(pin(path), tmp_path / "index/inventory.json"))
    assert result["inventory_complete"] and result["evidence"] == {} and result["hypotheses"] == {}
    assert result["episodes"]["SYN1"]["original_manifest"] is None


@pytest.mark.parametrize("kind", ["recovered", "editorial"])
def test_mixed_native_and_historical_derivatives_keep_original_lineage(cfg, tmp_path, kind):
    native_entry = copy.deepcopy(cfg["episodes"][1])
    cfg["episodes"] = cfg["episodes"][:1]
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(cfg, through="evidence", backend=ContextModel())
    recovery = {"schema_version": recover_evidence.CONFIG_VERSION, "commentary_scope": schemas.EPISODE_CONTEXT,
        "manifest": pin(root(cfg) / "run_manifest.json"),
        "request": pin(root(cfg) / "calls/SYN1_evidence_1.request.json"),
        "result": pin(root(cfg) / "calls/SYN1_evidence_1.result.json")}
    descriptor = {"kind": "recovered", "artifact": recover_evidence.apply(recovery, tmp_path / "recovered/evidence.json")}
    if kind == "editorial":
        old = read_json(Path(descriptor["artifact"]["path"]))["content"]["items"][0]
        source = pipeline._sources(cfg)["SYN1"]
        config = {"schema_version": evidence_revision.CONFIG_VERSION, "commentary_scope": schemas.EPISODE_CONTEXT,
            "parent": descriptor, "reviewer": "Synthetic reviewer", "rationale": "Keep uncertainty explicit",
            "edits": [{"evidence_id": old["evidence_id"], "field": "/uncertainty",
                "expected": old["uncertainty"], "value": old["uncertainty"] + ["Synthetic outcome remains unknown"],
                "reason": "No added facts", "source_anchors": [source["raw_segments"][0]]}]}
        config_path = tmp_path / "editorial-config.json"
        atomic_write_json(config_path, config)
        descriptor = {"kind": "editorial", "artifact": evidence_revision.apply(pin(config_path), tmp_path / "editorial/evidence.json")}
    n = native_candidate(native_entry, tmp_path / "native")
    selection = {"schema_version": inventory.SELECTION_VERSION, "corpus_id": "mixed-derivatives",
        "expected_episode_ids": ["SYN1", "SYN2"], "reserved_episode_ids": ["RESERVED"],
        "episodes": [{"episode_id": "SYN1", "evidence": available(descriptor)},
                     {"episode_id": "SYN2", "evidence": available(n["descriptor"])}],
        "expected_synthesis_ids": [], "syntheses": []}
    path = tmp_path / "selection.json"
    atomic_write_json(path, selection)
    before = files(tmp_path)
    result = inventory.load_checked(inventory.freeze(pin(path), tmp_path / "index/inventory.json"))
    assert result["episodes"]["SYN1"]["original_manifest"] == recovery["manifest"]
    assert result["episodes"]["SYN2"]["original_manifest"] is None
    assert len(result["evidence"]) == 6
    assert {row["artifact_kind"] for row in result["evidence"].values()} == {kind, "native"}
    assert recovery["result"] in result["dependencies"]
    assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize("old", ["selection", "inventory", "discovery"])
def test_old_contracts_rejected_with_regeneration_guidance_before_replay(mixed, tmp_path, monkeypatch, old):
    data, _, _ = mixed
    if old == "selection":
        value = read_json(data["path"])
        value["schema_version"] = "profile_corpus_selection.v1"
        path = tmp_path / "old-selection.json"
        loader = inventory.inspect
    elif old == "inventory":
        value = inventory.inspect(pin(data["path"]))
        value["schema_version"] = "profile_corpus_inventory.v1"
        path = tmp_path / "old-index.json"
        loader = inventory.load_checked
    else:
        value = {"schema_version": "profile_discovery_config.v1"}
        path = tmp_path / "old-discovery-config.json"
        loader = discovery._prepare
    atomic_write_json(path, value)
    before = files(tmp_path)
    forbidden = Mock(side_effect=AssertionError("Old contract must fail before replay"))
    monkeypatch.setattr(evidence_revision, "_sources", forbidden)
    with pytest.raises(ValueError, match="8c91e87.*no automatic migration"):
        loader(pin(path))
    assert files(tmp_path) == before
    forbidden.assert_not_called()
