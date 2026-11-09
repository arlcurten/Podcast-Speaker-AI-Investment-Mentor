"""Synthetic native synthesis imports; no live providers, source data or workers."""
import copy
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import corpus_inventory as inventory, native_evidence, native_synthesis as native
from speaker_profile import pipeline, recover_synthesis
from transcription.common import atomic_write_json, read_json
from .test_pipeline import cfg, synthesis_proposal
from .test_review import pin
from .test_corpus_inventory import available, save, selection_for
from .test_native_inventory import mixed, files, native_candidate
from .test_recover_synthesis import failed


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No real backend, subprocess or network"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.CodexBackend.complete", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    yield
    forbidden.assert_not_called()


def build(index_ref, tmp_path, support=("SYN1:E2",)):
    index = inventory.load_checked(index_ref)
    catalog = pipeline._catalog([read_json(Path(e["evidence"]["input"]["artifact"]["path"]))
                                for e in index["episodes"].values() if e["evidence"]["status"] == "available"])
    report = tmp_path / "actual-report.md"
    report.write_text("Fictional prior native analysis; event independence is unknown.\n")
    actor = {"identity": "Synthetic existing agent", "session_id": "synthetic-session"}
    config = {"schema_version": native.CONFIG_VERSION, "inventory": index_ref, "actor": actor,
        "reports": [{"ref": pin(report), "authored_at": "2026-01-01T00:00:00Z",
                     "method": "Fictional prior report, structured afterwards"}],
        "reading": {"inspected_evidence_ids": sorted(catalog), "pending_evidence_ids": [],
                    "method": "Synthetic source inspection", "limitations": ["No audio or semantic approval"]},
        "comparison_coverage": []}
    cp = tmp_path / "native-synthesis-config.json"
    atomic_write_json(cp, config)
    submission = {"schema_version": native.SUBMISSION_VERSION, "config": pin(cp), "actor": actor,
        "authored_at": "2026-01-02T00:00:00Z", "method": "Later structuring of pinned prior report",
        "limitations": ["No exhaustive recall claim"], "model_metadata": copy.deepcopy(native_evidence.MODEL_METADATA),
        "content": synthesis_proposal(list(catalog.values()), list(support)),
        "hypothesis_anchors": [], "comparisons": [], "context_dispositions": []}
    data = {"config": config, "config_path": cp, "submission": submission, "path": tmp_path / "submission.json",
            "output": tmp_path / "new-native-synthesis/candidate.json", "catalog": catalog, "index": index}
    if support:
        ids = inventory._basis_closure(index["evidence"], support)
        submission["hypothesis_anchors"] = [{"hypothesis_id": "PROFILE:H1", "source_anchors": anchors(data, ids),
                                             "report_refs": [pin(report)]}]
    write(data)
    return data


def anchors(data, ids):
    return [{"evidence_id": rid, **data["catalog"][rid]["quotes"][0]} for rid in sorted(ids)]


def write(data):
    atomic_write_json(data["config_path"], data["config"])
    data["submission"]["config"] = pin(data["config_path"])
    atomic_write_json(data["path"], data["submission"])
    return pin(data["path"])


@pytest.fixture
def ready(mixed, tmp_path):
    data, candidate, _ = mixed
    ref = inventory.freeze(pin(data["path"]), data["output"])
    result = build(ref, tmp_path)
    result["packet"] = candidate["packet"]
    return result


def comparison(data, cid, ids, *, inspected=True):
    data["config"]["comparison_coverage"].append({"comparison_id": cid, "evidence_ids": ids,
        "status": "inspected" if inspected else "pending", "method": "Synthetic comparison of exact source contexts"})
    if inspected:
        data["submission"]["comparisons"].append({"comparison_id": cid,
            "endpoints": [{"evidence_id": rid, "evidence_kind": data["catalog"][rid]["evidence_kind"]} for rid in ids],
            "relationship": "unresolved", "reason": "Context and independent event identity are unresolved",
            "hypothesis_ids": [], "source_anchors": anchors(data, inventory._basis_closure(data["index"]["evidence"], ids)),
            "report_refs": [data["config"]["reports"][0]["ref"]]})


def test_mixed_prepare_apply_replay_once_preserves_sources_and_unknown_usage(ready, tmp_path, monkeypatch):
    before = files(tmp_path)
    packet = native_evidence.load_packet(ready["packet"])
    impl = native_evidence._implementation()
    replay = Mock(wraps=inventory.load_checked)
    derive = Mock(wraps=pipeline._derive_synthesis)
    monkeypatch.setattr(inventory, "load_checked", replay)
    monkeypatch.setattr(pipeline, "_derive_synthesis", derive)
    planned = native.prepare(pin(ready["path"]), ready["output"])
    assert replay.call_count == 1 and not ready["output"].exists()
    ref = native.apply(pin(ready["path"]), ready["output"])
    assert replay.call_count == 2
    checked_pins = {}
    result = native.load_checked(ref, checked_pins)
    assert replay.call_count == 3
    assert any(len(call.args[1]) == 3 for call in derive.call_args_list)
    assert result["content"] == planned["content"]
    assert result["adoption"]["model_calls_this_execution"] == 0
    assert result["adoption"]["model_call_performed"] is False
    assert result["model_metadata"] == native_evidence.MODEL_METADATA
    assert not {"request", "result", "manifest", "completed_call"} & result.keys()
    assert result["source_context"]["SYN3"]["original_manifest"] is None
    assert result["source_context"]["SYN3"]["source"]["prior_source_decision"] == "uncertain"
    assert result["source_context"]["SYN3"]["unselected_regions"]
    assert set(result["grounding"]["retained_evidence_ids"]) == set(ready["catalog"])
    assert len(result["grounding"]["ungrouped_evidence_ids"]) == 8
    assert result["grounding"]["hypotheses"]["PROFILE:H1"]["independent_event_count"] is None
    assert result["policy"]["preauthoring_freeze"] == "not_asserted"
    assert not result["policy"]["eligible_for_serving"] and not result["policy"]["publication_authorized"]
    assert result["prior_hypotheses"] == ready["index"]["hypotheses"]
    assert all(p.read_bytes() == value for p, value in before.items())
    assert set(files(tmp_path)) - set(before) == {ready["output"]}
    assert Path(ref["path"]) in checked_pins
    assert native_evidence._implementation() == impl
    assert native_evidence.load_packet(ready["packet"]) == packet


def test_counter_only_incident_retained_and_anchored_disposition(ready):
    value = native.prepare(pin(ready["path"]), ready["output"])
    row = value["known_context"][0]
    assert row["incident_evidence_ids"] == ["SYN1:E2"]
    assert row["required_evidence_ids"] == ["SYN1:E1", "SYN1:E2"]
    assert row["disposition"] == "pending"
    h = value["prior_hypotheses"][row["prior_hypothesis_key"]]
    assert h["counterevidence"] == ["SYN1:E2"] and h["statement_action_relationships"]
    ready["submission"]["context_dispositions"] = [{"claim_id": row["claim_id"],
        "prior_hypothesis_key": row["prior_hypothesis_key"], "disposition": "contextualized",
        "reason": "Fictional narrower context; original counter remains visible",
        "source_anchors": anchors(ready, row["required_evidence_ids"])}]
    result = native.prepare(write(ready), ready["output"])
    assert result["known_context"][0]["disposition"] == "contextualized"
    assert result["prior_hypotheses"] == value["prior_hypotheses"]
    ready["submission"]["context_dispositions"][0]["source_anchors"].pop()
    with pytest.raises(ValueError, match="Every claim reference"):
        native.apply(write(ready), ready["output"])
    assert not ready["output"].exists()


def test_general_same_kind_comparisons_and_explicit_pending_work(ready):
    comparison(ready, "statements", ["SYN1:E1", "SYN2:E1"])
    comparison(ready, "actions", ["SYN1:E2", "SYN2:E2"])
    comparison(ready, "not-read", ["SYN3:E1", "SYN3:E2"], inspected=False)
    ready["config"]["reading"]["inspected_evidence_ids"] = sorted(set(ready["catalog"]) - {"SYN3:E1", "SYN3:E2", "SYN3:E3"})
    ready["config"]["reading"]["pending_evidence_ids"] = ["SYN3:E1", "SYN3:E2", "SYN3:E3"]
    result = native.prepare(write(ready), ready["output"])
    assert len(result["comparison_ledger"]) == 2
    assert len(result["comparison_coverage"]) == 3
    assert len(result["grounding"]["retained_evidence_ids"]) == 9
    assert len(result["reading"]["inspected_evidence_ids"]) == 6
    assert not result["content"]["hypotheses"][0]["statement_action_relationships"]
    assert any(r["claim_id"] == "comparison:statements" for r in result["known_context"])


def test_inference_bases_are_anchored_not_counted_as_new_events(ready):
    ready["submission"]["content"] = synthesis_proposal(list(ready["catalog"].values()), ["SYN1:E3"])
    ready["submission"]["hypothesis_anchors"][0]["source_anchors"] = anchors(ready, ["SYN1:E1", "SYN1:E3"])
    value = native.prepare(write(ready), ready["output"])
    assert len(value["known_context"]) == 2
    stat = value["grounding"]["hypotheses"]["PROFILE:H1"]
    assert stat["underlying_observation_ids"] == ["SYN1:E1"] and stat["independent_event_count"] is None
    ready["submission"]["hypothesis_anchors"][0]["source_anchors"] = anchors(ready, ["SYN1:E3"])
    with pytest.raises(ValueError, match="inference basis"):
        native.apply(write(ready), ready["output"])


@pytest.mark.parametrize("fault", ["actor", "accounting", "reading_gap", "pending_claim", "anchor_text", "anchor_time",
    "anchor_region", "missing_anchor", "unknown_report", "fake_usage", "fake_freeze", "future", "duplicate_comparison",
    "wrong_kind", "missing_comparison", "pending_comparison", "wrong_endpoints", "wrong_hypothesis_link",
    "listener_support", "strict_bad_kind"])
def test_invalid_submission_fails_without_writes(ready, fault, tmp_path):
    s, c = ready["submission"], ready["config"]
    comparison(ready, "one", ["SYN1:E1", "SYN2:E1"])
    anchor = s["hypothesis_anchors"][0]["source_anchors"][0]
    if fault == "actor": s["actor"] = {**s["actor"], "identity": "Different author"}
    elif fault == "accounting": s["content"]["evidence_accounting"].pop()
    elif fault == "reading_gap": c["reading"]["inspected_evidence_ids"].pop()
    elif fault == "pending_claim":
        c["reading"]["inspected_evidence_ids"].remove("SYN1:E2")
        c["reading"]["pending_evidence_ids"] = ["SYN1:E2"]
    elif fault == "anchor_text": anchor["text"] += " fabricated"
    elif fault == "anchor_time": anchor["end"] += 1
    elif fault == "anchor_region": anchor["segment_id"] = 2
    elif fault == "missing_anchor": s["hypothesis_anchors"] = []
    elif fault == "unknown_report": s["hypothesis_anchors"][0]["report_refs"] = [s["config"]]
    elif fault == "fake_usage": s["model_metadata"]["usage"] = {"tokens": 0}
    elif fault == "fake_freeze": s["frozen_before_authoring"] = True
    elif fault == "future": s["authored_at"] = "2999-01-01T00:00:00Z"
    elif fault == "duplicate_comparison": s["comparisons"].append(copy.deepcopy(s["comparisons"][0]))
    elif fault == "wrong_kind": s["comparisons"][0]["endpoints"][0]["evidence_kind"] = "reported_action"
    elif fault == "missing_comparison": s["comparisons"] = []
    elif fault == "pending_comparison": c["comparison_coverage"][0]["status"] = "pending"
    elif fault == "wrong_endpoints": c["comparison_coverage"][0]["evidence_ids"][0] = "SYN1:E2"
    elif fault == "wrong_hypothesis_link": s["comparisons"][0]["hypothesis_ids"] = ["PROFILE:H1"]
    elif fault == "listener_support":
        s["content"] = synthesis_proposal(list(ready["catalog"].values()), ["SYN3:E1"])
        s["hypothesis_anchors"][0]["source_anchors"] = anchors(ready, ["SYN3:E1"])
    elif fault == "strict_bad_kind":
        s["content"]["hypotheses"][0]["statement_action_relationships"] = [{"statement_id": "SYN1:E2",
            "action_id": "SYN1:E2", "relationship": "unresolved", "reason": "An action cannot become a statement"}]
    ref = write(ready)
    before = files(tmp_path)
    with pytest.raises(ValueError): native.apply(ref, ready["output"])
    assert files(tmp_path) == before


@pytest.mark.parametrize("target", ["report", "config", "source", "inventory", "submission"])
def test_dependency_tampering_rejected(ready, target):
    ref = pin(ready["path"])
    paths = {"report": ready["config"]["reports"][0]["ref"]["path"], "config": ready["config_path"],
             "source": ready["index"]["episodes"]["SYN3"]["native_lineage"]["source_files"]["raw"]["path"],
             "inventory": ready["config"]["inventory"]["path"], "submission": ready["path"]}
    path = Path(paths[target])
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError): native.apply(ref, ready["output"])
    assert not ready["output"].exists()


@pytest.mark.parametrize("field", ["known_context", "prior_hypotheses", "source_context", "reading", "policy", "implementation"])
def test_rebound_candidate_hash_does_not_hide_tampering(ready, field):
    native.apply(pin(ready["path"]), ready["output"])
    value = read_json(ready["output"])
    value[field] = [] if isinstance(value[field], list) else {}
    atomic_write_json(ready["output"], value)
    with pytest.raises(ValueError, match="replayed adoption"):
        native.load_checked(pin(ready["output"]))


def test_output_collision_protected_path_and_source_race(ready, monkeypatch, tmp_path):
    native.apply(pin(ready["path"]), ready["output"])
    for call in (native.prepare, native.apply):
        with pytest.raises(FileExistsError): call(pin(ready["path"]), ready["output"])
        protected = Path(ready["index"]["protected_output_roots"][0]) / "new.json"
        with pytest.raises(ValueError, match="protected"):
            call(pin(ready["path"]), protected)
    old = native._check_anchors
    report = Path(ready["config"]["reports"][0]["ref"]["path"])
    def race(*args):
        old(*args)
        report.write_text("Changed while deriving")
    monkeypatch.setattr(native, "_check_anchors", race)
    output = tmp_path / "raced/candidate.json"
    with pytest.raises(ValueError, match="changed"):
        native.apply(pin(ready["path"]), output)
    assert not output.parent.exists()


def test_recovered_comparison_caveats_retained_from_checked_parent(failed, tmp_path):
    cfg, proposal, output = failed
    recovered = recover_synthesis.apply(proposal, output)
    data = selection_for(cfg, tmp_path, synthesis=False)
    data["selection"]["expected_synthesis_ids"] = ["recovered"]
    data["selection"]["syntheses"] = [{"synthesis_id": "recovered",
        "synthesis": available({"kind": "recovered", "artifact": recovered})}]
    index_ref = inventory.freeze(save(data), data["output"])
    ready = build(index_ref, tmp_path)
    result = native.prepare(pin(ready["path"]), ready["output"])
    prior = next(iter(result["prior_hypotheses"].values()))
    original = read_json(Path(recovered["path"]))
    assert len(prior["recovered_comparisons"]) == 2
    assert prior["limitations"] == original["content"]["hypotheses"][0]["limitations"]
    assert result["known_context"][0]["required_evidence_ids"] == ["SYN1:E1", "SYN1:E2", "SYN1:E3", "SYN1:E4"]


def test_no_hypotheses_all_pending_is_not_semantic_completion(ready):
    ready["submission"]["content"] = synthesis_proposal(list(ready["catalog"].values()), [])
    ready["submission"]["hypothesis_anchors"] = []
    ready["config"]["reading"]["pending_evidence_ids"] = sorted(ready["catalog"])
    ready["config"]["reading"]["inspected_evidence_ids"] = []
    result = native.prepare(write(ready), ready["output"])
    assert not result["known_context"] and result["prior_hypotheses"]
    assert len(result["grounding"]["ungrouped_evidence_ids"]) == 9
    assert result["status"] == "unreviewed_candidate"


def test_native_only_inventory_without_fake_synthesis_parent(mixed, tmp_path):
    data, _, _ = mixed
    data["selection"]["episodes"] = data["selection"]["episodes"][-1:]
    data["selection"]["expected_episode_ids"] = ["SYN3"]
    data["selection"]["syntheses"] = []
    data["selection"]["expected_synthesis_ids"] = []
    ref = inventory.freeze(save(data), data["output"])
    ready = build(ref, tmp_path, ["SYN3:E2"])
    candidate = native.load_checked(native.apply(pin(ready["path"]), ready["output"]))
    assert candidate["known_context"] == [] and candidate["prior_hypotheses"] == {}
    assert candidate["source_context"]["SYN3"]["original_manifest"] is None
    assert candidate["evidence"]["SYN3"]["kind"] == "native"
    assert len(candidate["grounding"]["retained_evidence_ids"]) == 3


def test_supplementary_context_cannot_replace_primary_hypothesis_anchors(cfg, tmp_path):
    candidate = native_candidate(cfg["episodes"][0], tmp_path / "native", annotated=True)
    submission = read_json(candidate["submission"])
    submission["content"]["items"][0]["speaker_attribution"] = "host"
    submission["content"]["items"][2]["speaker_attribution"] = "host"
    atomic_write_json(candidate["submission"], submission)
    ref = native_evidence.apply(pin(candidate["submission"]), tmp_path / "native/host-candidate.json")
    selection = {"schema_version": inventory.SELECTION_VERSION, "corpus_id": "synthetic-context",
        "expected_episode_ids": ["SYN1"], "reserved_episode_ids": [], "expected_synthesis_ids": [], "syntheses": [],
        "episodes": [{"episode_id": "SYN1", "evidence": available({"kind": "native", "artifact": ref})}]}
    path = tmp_path / "selection.json"
    atomic_write_json(path, selection)
    index_ref = inventory.freeze(pin(path), tmp_path / "inventory/index.json")
    ready = build(index_ref, tmp_path, ["SYN1:E1"])
    raw = read_json(Path(cfg["episodes"][0]["raw"]))["segments"][2]
    supplementary = {"evidence_id": "SYN1:E1", **raw}
    primary = copy.deepcopy(ready["submission"]["hypothesis_anchors"][0]["source_anchors"])
    ready["submission"]["hypothesis_anchors"][0]["source_anchors"] = [supplementary]
    with pytest.raises(ValueError, match="primary source evidence"):
        native.apply(write(ready), ready["output"])
    supplementary.update(scope="supplementary_context", authority="model_analyst_not_speaker", note_index=0)
    with pytest.raises(ValueError, match="contract"):
        native.apply(write(ready), ready["output"])
    ready["submission"]["hypothesis_anchors"][0]["source_anchors"] = primary
    comparison(ready, "context-qualified", ["SYN1:E1", "SYN1:E2"])
    ready["submission"]["comparisons"][0]["source_anchors"][0] = supplementary
    result = native.prepare(write(ready), ready["output"])
    assert result["comparison_ledger"][0]["source_anchors"][0]["authority"] == "model_analyst_not_speaker"
    assert result["hypothesis_anchors"][0]["source_anchors"] == primary
    supplementary["note_index"] = 99
    with pytest.raises(ValueError, match="exact analyst note"):
        native.apply(write(ready), ready["output"])
    supplementary["note_index"] = 0
    supplementary["authority"] = "speaker"
    with pytest.raises(ValueError, match="contract"):
        native.apply(write(ready), ready["output"])
    assert not ready["output"].exists()


def test_cli_default_readonly_and_schema(ready, capsys):
    assert native.main(["--input", str(ready["path"]), "--sha256", pin(ready["path"])["sha256"],
                        "--output", str(ready["output"])]) == 0
    assert '"status": "planned"' in capsys.readouterr().out and not ready["output"].exists()
    assert native.main(["schema", "--kind", "submission"]) == 0
    assert native.SUBMISSION_VERSION in capsys.readouterr().out
