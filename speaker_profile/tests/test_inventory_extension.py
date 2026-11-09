"""Synthetic metadata/join/closure tests; checked parent replay is mocked.

These exercise the adapter, not source fitness or unchanged parent validators.
No real corpus, source replay, network, model workers or external processes.
"""
import copy
import json
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import inventory_extension as extension
from .test_pipeline import item, synthesis_proposal

base, native, synthesis = extension.base, extension.native, extension.synthesis
LIMITS = {"max_nodes": 100, "max_depth": 24, "max_metadata_bytes": 2_000_000}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return {"path": str(path), "sha256": extension.sha256_file(path)}


def read(ref):
    return json.loads(Path(ref["path"]).read_text())


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No source loader, model, network or subprocess"))
    monkeypatch.setattr(extension.pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr(extension.pipeline, "_sources", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


@pytest.fixture
def data(tmp_path, monkeypatch):
    source = {"episode_id": "SYN1", "segments": [{"source_segment_ids": [0]}, {"source_segment_ids": [1]}],
              "raw_segments": {i: {"segment_id": i, "start": i, "end": i + 1, "text": "Synthetic quote " + str(i)} for i in range(2)}}
    items = [item(source, i, "inferred_tendency" if i == 3 else "reported_action" if i == 2 else "direct_statement")
             for i in range(1, 7)]
    report = save(tmp_path / "report.json", {"method": "synthetic inspection, no approval"})
    raw = save(tmp_path / "raw.json", {"synthetic": True})
    norm = save(tmp_path / "normalized.json", {"synthetic": True})
    actor = {"identity": "Synthetic existing actor", "session_id": "synthetic"}
    cfg = {"schema_version": native.CONFIG_VERSION, "packet_id": "synthetic", "actor": actor,
           "episode": {"episode_id": "SYN1", "raw": raw, "normalized": norm, "source_review": None},
           "reserved_episode_ids": ["RESERVED"], "commentary_scope": base.schemas.EPISODE_CONTEXT,
           "max_packet_bytes": 100000, "max_submission_bytes": 100000}
    cfg_ref = save(tmp_path / "evidence/config.json", cfg)
    packet = save(tmp_path / "evidence/packet.json", {"schema_version": native.PACKET_VERSION, "config": cfg_ref})
    content = {"episode_id": "SYN1", "summary": "Synthetic", "items": items, "unselected_regions": []}
    submission = save(tmp_path / "evidence/submission.json", {"schema_version": native.SUBMISSION_VERSION,
        "packet": packet, "actor": actor, "authored_at": "2026-01-01T00:00:00Z", "method": "Synthetic",
        "complete_episode_read": True, "limitations": ["No semantic certification"],
        "model_metadata": native.MODEL_METADATA, "content": content})
    evidence_ref = save(tmp_path / "evidence/candidate.json", {"schema_version": native.VERSION, "stage": "evidence",
        "submission": submission, "packet": packet, "source": {"episode_id": "SYN1"}})
    descriptor = {"kind": "native", "artifact": evidence_ref}
    selection = {"schema_version": base.SELECTION_VERSION, "corpus_id": "synthetic", "expected_episode_ids": ["SYN1"],
                 "reserved_episode_ids": ["RESERVED"], "episodes": [{"episode_id": "SYN1", "evidence": {
                     "status": "available", "input": descriptor}}], "expected_synthesis_ids": [], "syntheses": []}
    base_selection = save(tmp_path / "base/selection.json", selection)
    old = synthesis_proposal(items, ["SYN1:E2"])["hypotheses"][0]
    old.update(hypothesis_id="PROFILE:OLD", counterevidence=["SYN1:E4"], artifact=evidence_ref,
               recovered_comparisons=[{"evidence_ids": ["SYN1:E2", "SYN1:E5"], "preserved_limitation": "Historical caveat"}])
    old_key = evidence_ref["sha256"] + ":PROFILE:OLD"
    index = {"schema_version": base.VERSION, "corpus_id": "synthetic", "selection": base_selection,
        "expected_episode_ids": ["SYN1"], "reserved_episode_ids": ["RESERVED"], "expected_synthesis_ids": [],
        "episodes": {"SYN1": {"evidence": {"status": "available", "input": descriptor},
                     "evidence_ids": [r["evidence_id"] for r in items], "original_manifest": None,
                     "policy": {"source_acceptance": "not_granted"}, "source_hashes": {"raw_sha256": raw["sha256"]}}},
        "evidence": {r["evidence_id"]: {"evidence_key": evidence_ref["sha256"] + ":" + r["evidence_id"],
                     "episode_id": "SYN1", "artifact": evidence_ref, "basis_evidence_ids": r["basis_evidence_ids"]} for r in items},
        "syntheses": {}, "hypotheses": {old_key: old}, "gaps": [], "inventory_complete": True,
        "dependencies": [base_selection, raw, norm], "implementation": {"historical": "unchanged"},
        "protected_output_roots": [str(tmp_path / "evidence")], "policy": base.POLICY}
    parent_ref = save(tmp_path / "base/inventory.json", index)
    scfg = {"schema_version": synthesis.CONFIG_VERSION, "inventory": parent_ref, "actor": actor,
        "reports": [{"ref": report, "authored_at": "2026-01-01T00:00:00Z", "method": "Synthetic"}],
        "reading": {"inspected_evidence_ids": [r["evidence_id"] for r in items], "pending_evidence_ids": [],
                    "method": "Synthetic", "limitations": ["Unknown event independence"]},
        "comparison_coverage": [{"comparison_id": "C1", "evidence_ids": ["SYN1:E1", "SYN1:E2"], "status": "inspected", "method": "Synthetic"},
                                {"comparison_id": "C2", "evidence_ids": ["SYN1:E2", "SYN1:E6"], "status": "inspected", "method": "Not transitive"},
                                {"comparison_id": "P1", "evidence_ids": ["SYN1:E1", "SYN1:E4"], "status": "pending", "method": "Not inspected"}]}
    scfg_ref = save(tmp_path / "synthesis/config.json", scfg)
    scontent = synthesis_proposal(items, ["SYN1:E3"])
    template = scontent["hypotheses"][0]
    scontent["hypotheses"] = []
    for i in range(1, 10):
        h = copy.deepcopy(template)
        h.update(hypothesis_id=f"PROFILE:H{i}", counterevidence=["SYN1:E2"], conditions=["Only fictional scope"])
        scontent["hypotheses"].append(h)
    quotes = [{"evidence_id": r["evidence_id"], **r["quotes"][0]} for r in items]
    comparisons = [{"comparison_id": row["comparison_id"],
        "endpoints": [{"evidence_id": rid, "evidence_kind": next(i["evidence_kind"] for i in items if i["evidence_id"] == rid)} for rid in row["evidence_ids"]],
        "relationship": "unresolved", "reason": "Unknown context; not an event count", "hypothesis_ids": [],
        "source_anchors": [a for a in quotes if a["evidence_id"] in row["evidence_ids"]], "report_refs": [report]}
        for row in scfg["comparison_coverage"] if row["status"] == "inspected"]
    anchors = [{"hypothesis_id": h["hypothesis_id"], "source_anchors": quotes[:3], "report_refs": [report]} for h in scontent["hypotheses"]]
    ssubmission = save(tmp_path / "synthesis/submission.json", {"schema_version": synthesis.SUBMISSION_VERSION, "config": scfg_ref,
        "actor": actor, "authored_at": "2026-01-02T00:00:00Z", "method": "Synthetic later structuring",
        "limitations": ["Nonblind synthetic fixture"], "model_metadata": native.MODEL_METADATA,
        "content": scontent, "hypothesis_anchors": anchors, "comparisons": comparisons, "context_dispositions": []})
    candidate = {"schema_version": synthesis.VERSION, "stage": "synthesis", "config": scfg_ref, "submission": ssubmission,
        "inventory": parent_ref, "content": scontent, "grounding": {"retained_evidence_ids": sorted(index["evidence"]),
            "hypotheses": {h["hypothesis_id"]: {"independent_event_count": None} for h in scontent["hypotheses"]}},
        "evidence": {"SYN1": descriptor}, "authorship": {"actor": actor, "method": "Synthetic"},
        "reports": scfg["reports"], "reading": scfg["reading"], "comparison_coverage": scfg["comparison_coverage"],
        "comparison_ledger": comparisons, "known_context": [{"claim_id": "comparison:C1", "prior_hypothesis_key": old_key,
            "required_evidence_ids": ["SYN1:E2", "SYN1:E4", "SYN1:E5"], "incident_evidence_ids": ["SYN1:E2"],
            "disposition": "pending", "reason": "Unadjudicated prior context", "source_anchors": []}],
        "prior_hypotheses": {old_key: old}, "hypothesis_anchors": anchors, "source_context": {"SYN1": {"source_acceptance": "not_granted"}},
        "inventory_gaps": [], "model_metadata": native.MODEL_METADATA, "adoption": native.ADOPTION,
        "policy": {**base.pipeline.POLICY, "semantic_entailment": "not_certified"}, "authority": native.AUTHORITY,
        "implementation": {"historical": "unchanged"}}
    candidate_ref = save(tmp_path / "synthesis/candidate.json", candidate)
    selection = {"schema_version": extension.SELECTION_VERSION, "corpus_id": "extended", "limits": copy.deepcopy(LIMITS),
        "base_inventory": parent_ref, "reserved_episode_ids": ["RESERVED"], "expected_synthesis_ids": ["native_new"],
        "native_syntheses": [{"synthesis_id": "native_new", "kind": "native", "artifact": candidate_ref}]}
    selection_ref = save(tmp_path / "extension-selection.json", selection)
    base_loader = Mock(side_effect=read)
    def checked_candidate(ref, pins):
        value = read(ref)
        extension.recovery._pin(pins, Path(ref["path"]), ref["sha256"])
        return value
    native_loader = Mock(side_effect=checked_candidate)
    monkeypatch.setattr(base, "load_checked", base_loader)
    monkeypatch.setattr(synthesis, "load_checked", native_loader)
    monkeypatch.setattr(extension, "_implementation", lambda: {"synthetic_extension": "unchanged"})
    return {"selection": selection, "ref": selection_ref, "index": index, "parent_ref": parent_ref,
            "candidate": candidate, "candidate_ref": candidate_ref, "base_loader": base_loader,
            "native_loader": native_loader, "root": tmp_path, "evidence_config": cfg_ref,
            "evidence_submission": submission, "report": report, "raw": raw}


def resave_selection(data):
    data["ref"] = save(Path(data["ref"]["path"]), data["selection"])
    return data["ref"]


def resave_candidate(data):
    data["candidate_ref"] = save(Path(data["candidate_ref"]["path"]), data["candidate"])
    data["selection"]["native_syntheses"][0]["artifact"] = data["candidate_ref"]
    return resave_selection(data)


def assert_before_replay(data):
    data["base_loader"].assert_not_called()
    data["native_loader"].assert_not_called()


def test_inspect_retains_all_nine_and_every_comparison(data):
    before = {p: p.read_bytes() for p in data["root"].rglob("*") if p.is_file()}
    value = extension.inspect(data["ref"])
    assert value["schema_version"] == extension.VERSION
    assert value["evidence"] == data["index"]["evidence"] and len(value["evidence"]) == 6
    assert len(value["hypotheses"]) == 10  # Nine native plus unchanged synthetic historical context.
    retained = value["syntheses"]["native_new"]
    for key in ("comparison_ledger", "known_context", "prior_hypotheses", "reports", "reading", "source_context", "model_metadata", "adoption"):
        assert retained[key] == data["candidate"][key]
    assert retained["original_manifest"] is None
    assert len(value["native_comparisons"]) == 2 and len(value["native_pending_comparisons"]) == 1
    assert not value["policy"]["publication_authorized"] and value["policy"]["independent_event_count"] is None
    assert value["episodes"] == data["index"]["episodes"]
    assert all(p.read_bytes() == v for p, v in before.items())
    assert set(before) == {p for p in data["root"].rglob("*") if p.is_file()}
    assert data["base_loader"].call_count == data["native_loader"].call_count == 1


def test_closure_both_sides_bases_pending_unowned_context_not_transitive(data):
    index = extension.inspect(data["ref"])
    result = extension.checked_index_closure(index, data["ref"], ["SYN1:E3"])
    assert result["required_evidence_ids"] == ["SYN1:E1", "SYN1:E2", "SYN1:E3", "SYN1:E4", "SYN1:E5"]
    assert {r["kind"] for r in result["obligations"]} == {"native_comparison", "pending_comparison", "prior_context"}
    assert "SYN1:E6" not in result["required_evidence_ids"]
    assert all(h["grounding"]["independent_event_count"] is None for h in result["hypotheses"].values())
    assert all(h["anchor_binding"] and h["conditions"] for h in result["hypotheses"].values())
    context = next(r for r in result["obligations"] if r["kind"] == "prior_context")
    assert context["context"]["disposition"] == "pending"
    assert context["prior_hypothesis"]["recovered_comparisons"][0]["preserved_limitation"] == "Historical caveat"


def test_counter_only_seed_keeps_support_and_basis(data):
    result = extension.checked_index_closure(extension.inspect(data["ref"]), data["ref"], ["SYN1:E2"])
    assert len(result["hypotheses"]) == 10
    assert set(result["required_evidence_ids"]) == set(data["index"]["evidence"])


@pytest.mark.parametrize("fault", ["version", "descriptor", "source", "accounting"])
def test_exact_version_source_and_complete_accounting(data, fault):
    if fault in {"version", "descriptor", "source"}:
        new = copy.deepcopy(data["index"])
        if fault == "version":
            new["episodes"]["SYN1"]["evidence"]["input"]["artifact"]["sha256"] = "0" * 64
        elif fault == "descriptor":
            new["episodes"]["SYN1"]["evidence"]["input"]["kind"] = "completed"
        else:
            new["episodes"]["SYN1"]["source_hashes"]["raw_sha256"] = "0" * 64
        data["selection"]["base_inventory"] = save(data["root"] / "new-base/inventory.json", new)
        ref = resave_selection(data)
    else:
        data["candidate"]["grounding"]["retained_evidence_ids"].pop()
        ref = resave_candidate(data)
    with pytest.raises(ValueError, match="evidence version/source|accounting"):
        extension.inspect(ref)


@pytest.mark.parametrize("fault", ["duplicate_artifact", "duplicate_id", "wrong_kind", "unknown_version", "fake_header"])
def test_bad_metadata_before_source_replay(data, fault):
    if fault == "duplicate_artifact":
        row = copy.deepcopy(data["selection"]["native_syntheses"][0])
        row["synthesis_id"] = "duplicate"
        data["selection"]["native_syntheses"].append(row)
        data["selection"]["expected_synthesis_ids"].append("duplicate")
    elif fault == "duplicate_id":
        data["selection"]["native_syntheses"] *= 2
    elif fault == "wrong_kind":
        data["selection"]["native_syntheses"][0]["kind"] = "completed"
    elif fault == "unknown_version":
        data["candidate"]["schema_version"] = "unsupported.v99"
        resave_candidate(data)
    else:
        data["candidate"]["config"] = data["report"]
        resave_candidate(data)
    with pytest.raises(ValueError):
        extension.inspect(resave_selection(data))
    assert_before_replay(data)


def test_reserved_authoritative_config_not_duplicated_header(data):
    data["selection"]["reserved_episode_ids"].append("SYN1")
    with pytest.raises(ValueError, match="Reserved episode"):
        extension.inspect(resave_selection(data))
    assert_before_replay(data)


def test_hidden_reserved_parent_inventory_before_replay(data):
    parent = read(data["parent_ref"])
    sel = read(parent["selection"])
    sel["expected_episode_ids"].append("RESERVED")
    sel["episodes"].append({"episode_id": "RESERVED", "evidence": {"status": "held", "reason": "Not available", "record": None}})
    parent["selection"] = save(data["root"] / "hidden/selection.json", sel)
    hidden = save(data["root"] / "hidden/inventory.json", parent)
    cfg = read(data["candidate"]["config"])
    cfg["inventory"] = hidden
    cfg_ref = save(data["root"] / "hidden/config.json", cfg)
    sub = read(data["candidate"]["submission"])
    sub["config"] = cfg_ref
    data["candidate"].update(config=cfg_ref, inventory=hidden, submission=save(data["root"] / "hidden/submission.json", sub))
    with pytest.raises(ValueError, match="Reserved episode"):
        extension.inspect(resave_candidate(data))
    assert_before_replay(data)


def test_historical_config_reserved_id_blocks_another_branch(data, monkeypatch):
    graph = extension._Graph(LIMITS)
    graph.walk(data["ref"], "extension")
    # Configuration schema checks belong to the existing loader; exercise the
    # cross-branch reservation propagation after that loader has returned.
    monkeypatch.setattr(extension.pipeline, "_config", lambda cfg: cfg)
    graph._config({"reserved_episode_ids": ["SYN1"], "run_id": "historical",
                   "output_root": str(data["root"] / "historical"),
                   "episodes": [{"episode_id": "SYN2", "raw": None,
                                 "draft": None, "source_review": None}]})
    with pytest.raises(ValueError, match="Reserved episode"):
        graph.finish()
    assert_before_replay(data)


def test_cycles_use_active_paths_before_digest_or_cache(data, monkeypatch):
    # Fictional coordinated cycle: isolate graph detection from impossible self-hashes.
    a, b = data["root"] / "a.json", data["root"] / "b.json"
    refs = [{"path": str(p), "sha256": "a" * 64} for p in (a, b)]
    a.write_text("{}")
    b.write_text("{}")
    graph = extension._Graph(LIMITS)
    monkeypatch.setattr(native, "_read", lambda ref, pins, limit: {"next": refs[1] if ref["path"] == str(a) else refs[0]})
    monkeypatch.setattr(graph, "_visit", lambda value, role, descriptor: graph.walk(value["next"], "synthetic"))
    with pytest.raises(ValueError, match="Cyclic"):
        graph.walk(refs[0], "synthetic")
    assert_before_replay(data)


@pytest.mark.parametrize("key,value", [("max_nodes", 1), ("max_depth", 1), ("max_metadata_bytes", 1)])
def test_explicit_resource_limits_fail_before_replay(data, key, value):
    data["selection"]["limits"][key] = value
    with pytest.raises(ValueError, match="limit|byte"):
        extension.inspect(resave_selection(data))
    assert_before_replay(data)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_resource_limits_are_positive_integers(data, value):
    data["selection"]["limits"]["max_nodes"] = value
    with pytest.raises(ValueError):
        extension.inspect(resave_selection(data))
    assert_before_replay(data)


def test_alias_public_root_and_conflicting_pins_rejected(data):
    alias = data["root"] / "alias.json"
    alias.symlink_to(data["ref"]["path"])
    with pytest.raises(ValueError, match="aliases"):
        extension.inspect({"path": str(alias), "sha256": data["ref"]["sha256"]})
    with pytest.raises(ValueError, match="public"):
        extension.freeze(data["ref"], Path(extension.__file__).parent / "must-not-create.json")
    graph = extension._Graph(LIMITS)
    graph.leaf(data["ref"])
    with pytest.raises(ValueError, match="Conflicting"):
        graph.leaf({**data["ref"], "sha256": "0" * 64})
    assert_before_replay(data)


@pytest.mark.parametrize("which", ["candidate_ref", "report", "raw"])
def test_tamper_rejected_before_replay(data, which):
    Path(data[which]["path"]).write_text('{}')
    with pytest.raises(ValueError):
        extension.inspect(data["ref"])
    assert_before_replay(data)


def test_mutation_during_replay_fails_before_output(data):
    def mutate(ref, pins):
        Path(data["report"]["path"]).write_text('{}')
        return read(ref)
    data["native_loader"].side_effect = mutate
    out = data["root"] / "output/inventory.json"
    with pytest.raises(ValueError, match="changed"):
        extension.freeze(data["ref"], out)
    assert not out.exists()


def test_freeze_replay_collision_and_selection_binding(data):
    out = data["root"] / "output/inventory.json"
    ref = extension.freeze(data["ref"], out)
    assert extension.load_checked(ref, data["ref"])["schema_version"] == extension.VERSION
    with pytest.raises(FileExistsError):
        extension.freeze(data["ref"], out)
    other = copy.deepcopy(data["selection"])
    other["corpus_id"] = "other"
    with pytest.raises(ValueError, match="selection differs"):
        extension.load_checked(ref, save(data["root"] / "other.json", other))
    stored = read(ref)
    stored["native_pending_comparisons"] = {}
    wrong = save(data["root"] / "tampered-output.json", stored)
    with pytest.raises(ValueError, match="independent reconstruction"):
        extension.load_checked(wrong, data["ref"])


def test_protected_output_and_unresolved_basis_fail(data):
    with pytest.raises(ValueError, match="protected"):
        extension.freeze(data["ref"], data["root"] / "evidence/new.json")
    index = extension.inspect(data["ref"])
    index["evidence"]["SYN1:E3"]["basis_evidence_ids"] = ["missing"]
    with pytest.raises(ValueError, match="Unresolved"):
        extension.checked_index_closure(index, data["ref"], ["SYN1:E3"])


@pytest.mark.parametrize("fault", ["dependency", "implementation"])
def test_final_read_rechecks_dependencies_and_implementation(data, monkeypatch, fault):
    ref = extension.freeze(data["ref"], data["root"] / "output/inventory.json")
    inspect = extension.inspect
    def changed_after_reconstruction(selection):
        result = inspect(selection)
        if fault == "dependency":
            Path(data["raw"]["path"]).write_text('{}')
        else:
            monkeypatch.setattr(extension, "_implementation", lambda: {"changed": True})
        return result
    monkeypatch.setattr(extension, "inspect", changed_after_reconstruction)
    with pytest.raises(ValueError, match="changed"):
        extension.load_checked(ref, data["ref"])


def test_cli_inspect_and_validate_require_explicit_budget_selection(data, capsys):
    args = ["--input", data["ref"]["path"], "--sha256", data["ref"]["sha256"]]
    assert extension.main(["inspect", *args]) == 0
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
    with pytest.raises(SystemExit):
        extension.main(["validate", *args])
