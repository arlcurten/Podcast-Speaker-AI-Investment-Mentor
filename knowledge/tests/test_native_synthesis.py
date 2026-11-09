"""Small synthetic native-adoption checks, never a real model or corpus replay."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge import corpus_inventory as inventory, native_synthesis as native, pipeline
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_candidate_extraction import candidate
from .test_corpus_inventory import selection_for, pin
from .test_pipeline import cfg as config_fixture, principle


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No real backend or subprocess permitted"))
    monkeypatch.setattr("knowledge.codex_backend.CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


@pytest.fixture
def data(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    config = candidate(config_fixture.__wrapped__(inputs))
    old = selection_for(config, inputs)
    inv = inventory.freeze(pin(old["path"]), tmp_path / "inventory.json")
    report = tmp_path / "report.txt"
    report.write_text("Synthetic explicitly authored source work; not acceptance.")
    cfg = {"schema_version": native.CONFIG_VERSION, "inventory": inv,
        "workset_record_ids": ["SYN1:R1", "SYN2:R1"],
        "actor": {"identity": "Synthetic native author", "session_id": "synthetic-session"},
        "reports": [pin(report)], "limits": {"max_packet_bytes": 500000,
            "max_submission_bytes": 100000, "max_candidate_bytes": 500000}}
    cfg_path = tmp_path / "native-config.json"
    atomic_write_json(cfg_path, cfg)
    packet = native.prepare(pin(cfg_path))
    p = principle("CROSS:P1", cfg["workset_record_ids"])
    p.update(derived_from=cfg["workset_record_ids"], shared_decision_logic="Synthetic conditional logic",
             context_differences=["Distinct synthetic situations"], principle_evolution=[],
             counterevidence_dispositions=[])
    # All ineligible L3 parents remain obligations, not fabricated eligible parents.
    for eid in ("SYN1", "SYN2"):
        for kind, field in (("counterexample", "counterexamples"), ("contradiction", "contradictions")):
            p[field].append({"record_id": f"{eid}:R2", "explanation": "Synthetic contrary context retained"})
            p["counterevidence_dispositions"].append({"source_principle_id": f"{eid}:P1",
                "record_id": f"{eid}:R2", "kind": kind, "disposition": "unresolved",
                "reason": "Synthetic material tension remains"})
    def anchor(rid):
        case = next(c for c in packet["bundle"]["cases"] if c["record_id"] == rid)
        ev = case["evidence"]
        sid = (ev["source_segment_ids"] or [ev["source_segment_id_ranges"][0]["start_id"]])[0]
        row = next(r for r in packet["bundle"]["raw_regions"][case["episode_id"]] if r["segment_id"] == sid)
        return {"episode_id": case["episode_id"], **row}
    sub = {"schema_version": native.SUBMISSION_VERSION, "config": pin(cfg_path),
        "packet_sha256": packet["packet_sha256"], "actor": cfg["actor"],
        "authored_at": "2026-01-01T00:00:00Z", "method": "Synthetic primary-source reading, not blind",
        "content": {"consolidation_reason": "Bounded synthetic workset, not global consolidation", "principles": [p]},
        "case_accounting": [{"record_id": rid, "status": "inspected", "reason": "Synthetic source inspected",
                             "source_anchors": [anchor(rid)]} for rid in packet["required_record_ids"]],
        "inherited_accounting": [{"principle_id": pid, "status": "inspected", "reason": "Prior hypothesis only"}
                                 for pid in packet["inherited_principle_ids"]],
        "grounding": [{"principle_id": "CROSS:P1", "record_id": rid, "source_anchors": [anchor(rid)]}
                      for rid in packet["required_record_ids"]]}
    sub_path = tmp_path / "submission.json"
    atomic_write_json(sub_path, sub)
    # After fixture construction no extraction or backend work may occur.
    monkeypatch.setattr(pipeline, "run", Mock(side_effect=AssertionError("No extraction")))
    return {"cfg": cfg, "cfg_path": cfg_path, "sub": sub, "sub_path": sub_path,
            "packet": packet, "old": old, "output": tmp_path / "candidate.json", "report": report}


def save_sub(data):
    atomic_write_json(data["sub_path"], data["sub"])
    return pin(data["sub_path"])


def refresh(data):
    atomic_write_json(data["cfg_path"], data["cfg"])
    packet = native.prepare(pin(data["cfg_path"]))
    data["sub"].update(config=pin(data["cfg_path"]), packet_sha256=packet["packet_sha256"])
    return packet


def test_prepare_preview_apply_replay_one_inventory_load_each(data, monkeypatch):
    before = {p: p.read_bytes() for p in data["output"].parent.rglob("*") if p.is_file()}
    original = inventory.load_checked
    load = Mock(side_effect=original)
    monkeypatch.setattr(inventory, "load_checked", load)
    assert native.prepare(pin(data["cfg_path"])) == data["packet"]
    assert load.call_count == 1
    preview = native.preview(pin(data["sub_path"]))
    assert load.call_count == 2 and not data["output"].exists()
    ref = native.apply(pin(data["sub_path"]), data["output"])
    assert load.call_count == 3
    checked = native.load_checked(ref, with_packet=True)
    assert load.call_count == 4 and checked["candidate"] == preview
    assert checked["packet"] == data["packet"]
    assert all(p.read_bytes() == value for p, value in before.items())
    assert preview["metadata"] == {"provider": "native_agent_candidate", "model": None, "usage": None,
        "cost_usd": None, "import_model_calls": 0, "reasoning_usage": "unknown_not_zero", "backend_receipt": None}
    assert not preview["policy"]["publication_authorized"] and not preview["policy"]["global_completion"]
    q = preview["qualification"]["CROSS:P1"]
    assert not q["eligible_for_online_reasoning"] and q["counterevidence_accounting"]["unresolved"] == 4
    assert q["provenance"]["independent_source_context_count"] == 2
    assert all(not p["qualification"]["eligible_for_level4"] for p in data["packet"]["bundle"]["principles"])


def test_counter_only_incident_parent_and_outside_scope(data):
    data["cfg"]["workset_record_ids"] = ["SYN1:R2"]
    packet = refresh(data)
    assert packet["required_record_ids"] == ["SYN1:R1", "SYN1:R2"]
    assert packet["inherited_principle_ids"] == ["SYN1:P1", "SYN1:P2"]
    assert packet["outside_record_ids"] == ["SYN2:R1", "SYN2:R2"]
    assert packet["outside_principle_ids"] == ["SYN2:P1", "SYN2:P2"]
    assert packet["bundle"]["sources"]["SYN1"]["episode_map"]["source_observations"] == []
    assert packet["bundle"]["sources"]["SYN1"]["thread_dispositions"]
    assert packet["bundle"]["cases"][0]["evidence"]["candidate_source_assessment"]["authority"] == "model_assessment_not_source"


def test_partial_l3_gap_does_not_discard_l2(data, tmp_path):
    old = data["old"]
    old["selection"]["episodes"][1]["stages"]["level3"] = {"status": "missing", "reason": "Not extracted", "record": None}
    atomic_write_json(old["path"], old["selection"])
    data["cfg"]["inventory"] = inventory.freeze(pin(old["path"]), tmp_path / "partial.json")
    packet = refresh(data)
    assert not packet["inventory_complete"] and len(packet["inventory_gaps"]) == 1
    assert "SYN2:R1" in packet["required_record_ids"] and "SYN2:R2" in packet["outside_record_ids"]
    data["sub"]["content"]["principles"][0]["counterevidence_dispositions"] = [
        r for r in data["sub"]["content"]["principles"][0]["counterevidence_dispositions"] if r["record_id"] != "SYN2:R2"]
    for field in ("counterexamples", "contradictions"):
        data["sub"]["content"]["principles"][0][field] = [
            r for r in data["sub"]["content"]["principles"][0][field] if r["record_id"] != "SYN2:R2"]
    data["sub"]["case_accounting"] = [r for r in data["sub"]["case_accounting"] if r["record_id"] != "SYN2:R2"]
    data["sub"]["grounding"] = [r for r in data["sub"]["grounding"] if r["record_id"] != "SYN2:R2"]
    data["sub"]["inherited_accounting"] = [r for r in data["sub"]["inherited_accounting"] if r["principle_id"].startswith("SYN1:")]
    assert not native.preview(save_sub(data))["scope"]["inventory_complete"]


def test_pending_and_zero_principles_remain_explicit_not_acceptance(data):
    data["sub"]["content"]["principles"] = []
    data["sub"]["grounding"] = []
    for row in data["sub"]["case_accounting"]:
        row.update(status="pending", reason="Not inspected yet", source_anchors=[])
    for row in data["sub"]["inherited_accounting"]:
        row.update(status="pending", reason="Comparison not completed")
    out = native.preview(save_sub(data))
    assert len(out["case_accounting"]) == 4 and len(out["inherited_accounting"]) == 4
    assert not out["content"]["principles"] and not out["policy"]["global_completion"]


@pytest.mark.parametrize("fault", ["missing_case", "duplicate_case", "missing_parent", "missing_anchor", "wrong_quote",
    "wrong_time", "wrong_episode", "context_anchor", "context_support", "pending_support", "missing_counter",
    "fake_parent", "ineligible_parent", "pair_missing", "duplicate_principle", "actor", "packet", "timestamp", "extra_field"])
def test_invalid_submission_rejected_before_write(data, fault):
    s = data["sub"]; p = s["content"]["principles"][0]
    if fault == "missing_case": s["case_accounting"].pop()
    elif fault == "duplicate_case": s["case_accounting"].append(copy.deepcopy(s["case_accounting"][0]))
    elif fault == "missing_parent": s["inherited_accounting"].pop()
    elif fault == "missing_anchor": s["grounding"].pop()
    elif fault == "wrong_quote": s["grounding"][0]["source_anchors"][0]["text"] = "Reviewer invented statement"
    elif fault == "wrong_time": s["grounding"][0]["source_anchors"][0]["end"] += 1
    elif fault == "wrong_episode": s["grounding"][0]["source_anchors"][0]["episode_id"] = "SYN2"
    elif fault == "context_anchor": s["grounding"][0]["source_anchors"] = s["grounding"][1]["source_anchors"]
    elif fault == "context_support": p["supporting_records"].append("SYN1:R2")
    elif fault == "pending_support": s["case_accounting"][0].update(status="pending", source_anchors=[])
    elif fault == "missing_counter": p["counterevidence_dispositions"].pop()
    elif fault == "fake_parent": p["counterevidence_dispositions"][0]["source_principle_id"] = "FAKE:P1"
    elif fault == "ineligible_parent": p["derived_from"] = ["SYN1:P1"]
    elif fault == "pair_missing": p["context_relationships"] = []
    elif fault == "duplicate_principle": s["content"]["principles"].append(copy.deepcopy(p))
    elif fault == "actor": s["actor"] = {"identity": "Someone else", "session_id": "other"}
    elif fault == "packet": s["packet_sha256"] = "a" * 64
    elif fault == "timestamp": s["authored_at"] = "2026-01-01"
    else: s["completed_backend_call"] = True
    with pytest.raises(ValueError): native.apply(save_sub(data), data["output"])
    assert not data["output"].exists()


def test_one_episode_cannot_masquerade_as_l4(data):
    p = data["sub"]["content"]["principles"][0]
    p.update(supporting_records=["SYN1:R1"], derived_from=["SYN1:R1"], context_relationships=[])
    data["sub"]["grounding"] = [r for r in data["sub"]["grounding"] if r["record_id"] != "SYN2:R1"]
    with pytest.raises(ValueError, match="Level 4 requires at least two"):
        native.apply(save_sub(data), data["output"])
    assert not data["output"].exists()


@pytest.mark.parametrize("target", ["outside_l3", "absent_l2", "pending_l3", "outside_counter_parent"])
def test_full_stage_visibility_is_not_packet_parent_authority(data, target):
    cfg, _, packet, upstream, _, _ = native._prepare(pin(data["cfg_path"]))
    # Unit-only synthetic full-stage additions: not a checked artifact or file.
    # Neither identity belongs to the bounded packet or its inherited accounting.
    l2 = next(a for a in upstream if a["stage"] == "level2" and a["content"]["episode_id"] == "SYN1")
    extra = copy.deepcopy(l2["content"]["cases"][1]); extra["record_id"] = "SYN1:R99"
    l2["content"]["cases"].append(extra)
    l2["evidence"]["SYN1:R99"] = copy.deepcopy(l2["evidence"]["SYN1:R2"])
    l3 = next(a for a in upstream if a["stage"] == "level3" and a["content"]["episode_id"] == "SYN1")
    extra = copy.deepcopy(l3["content"]["principles"][0])
    extra.update(principle_id="SYN1:P99", supporting_records=["SYN1:R99"], contradictions=[])
    l3["content"]["principles"].append(extra)
    l3["qualification"]["SYN1:P99"] = copy.deepcopy(l3["qualification"]["SYN1:P1"])
    s = data["sub"]; p = s["content"]["principles"][0]
    assert "SYN1:P99" not in packet["inherited_principle_ids"]
    assert "SYN1:R99" not in packet["required_record_ids"]
    if target == "outside_counter_parent":
        p["counterevidence_dispositions"].append({"source_principle_id": "SYN1:P99",
            "record_id": "SYN1:R2", "kind": "counterexample", "disposition": "unresolved",
            "reason": "Injected extra link from an uninspected same-episode parent"})
        # The ordinary validator permits additional real L3 links. The bounded
        # adapter must additionally require the referenced parent's inspection.
        pipeline.derive("level4", s["content"], None, upstream)
        message = "Counter dispositions require inspected"
    else:
        p["derived_from"] = [{"outside_l3": "SYN1:P99", "absent_l2": "SYN1:R99",
                              "pending_l3": "SYN1:P1"}[target]]
        if target == "pending_l3":
            s["inherited_accounting"][0]["status"] = "pending"
        message = "Derivation parents require inspected"
    with pytest.raises(ValueError, match=message):
        native._derive(s, cfg, packet, upstream)


@pytest.mark.parametrize("field", ["counterexamples", "contradictions", "counterevidence_dispositions", "context_relationships"])
def test_counter_and_relationship_references_cannot_escape_packet_support(data, field):
    p = data["sub"]["content"]["principles"][0]
    if field == "context_relationships":
        p[field][0]["record_ids"][0] = "SYN1:R2"  # Inspected context, not support.
        message = "supporting-case pairs"
    else:
        p[field][0]["record_id"] = "SYN1:R99"
        message = "inspected packet cases"
    with pytest.raises(ValueError, match=message):
        native.apply(save_sub(data), data["output"])
    assert not data["output"].exists()


def test_principle_is_not_an_additional_evidence_item(data):
    data["cfg"]["workset_record_ids"] = ["SYN1:P1", "SYN2:R1"]
    atomic_write_json(data["cfg_path"], data["cfg"])
    with pytest.raises(ValueError, match="L2 cases, not abstractions"):
        native.prepare(pin(data["cfg_path"]))
    assert not data["output"].exists()


def test_excluded_counter_requires_real_anchor_and_remains_context(data):
    p = data["sub"]["content"]["principles"][0]
    for r in p["counterevidence_dispositions"]:
        r.update(disposition="context_excluded", reason="Different synthetic decision conditions")
    p["counterexamples"] = []; p["contradictions"] = []
    out = native.preview(save_sub(data))
    assert out["qualification"]["CROSS:P1"]["counterevidence_accounting"]["context_excluded"] == 4
    assert len(out["grounding"]) == 4
    data["sub"]["grounding"] = [g for g in data["sub"]["grounding"] if not g["record_id"].endswith(":R2")]
    with pytest.raises(ValueError, match="Missing principle"): native.preview(save_sub(data))


@pytest.mark.parametrize("target", ["submission", "report", "raw", "config", "inventory", "level3", "candidate", "implementation"])
def test_stale_or_rebound_tampering_fails(data, monkeypatch, target):
    ref = native.apply(pin(data["sub_path"]), data["output"])
    if target == "implementation":
        old = native._implementation()
        monkeypatch.setattr(native, "_implementation", lambda: {**old, "native_synthesis_sha256": "b" * 64})
    elif target == "candidate":
        stored = read_json(data["output"]); stored["policy"]["publication_authorized"] = True
        atomic_write_json(data["output"], stored); ref = pin(data["output"])
    else:
        path = {"submission": data["sub_path"], "report": data["report"], "config": data["cfg_path"],
            "inventory": Path(data["cfg"]["inventory"]["path"]),
            "level3": Path(data["old"]["selection"]["episodes"][0]["stages"]["level3"]["artifact"]["path"]),
            "raw": Path(data["old"]["cfg"]["episodes"][0]["raw"])}[target]
        path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError): native.load_checked(ref)


@pytest.mark.parametrize("limit", ["max_packet_bytes", "max_submission_bytes", "max_candidate_bytes"])
def test_configured_limits_reject_not_truncate(data, limit):
    data["cfg"]["limits"][limit] = 1
    atomic_write_json(data["cfg_path"], data["cfg"])
    data["sub"]["config"] = pin(data["cfg_path"])
    if limit != "max_packet_bytes":
        data["sub"]["packet_sha256"] = native.prepare(pin(data["cfg_path"]))["packet_sha256"]
    with pytest.raises(ValueError, match="no truncation"): native.apply(save_sub(data), data["output"])
    assert not data["output"].exists()


def test_byte_limit_matches_immutable_writer(tmp_path):
    value = {"nested": [{"text": "synthetic text", "nullable": None}]}
    path = tmp_path / "sizing.json"
    atomic_write_json(path, value, overwrite=False)
    size = len(path.read_bytes())
    assert size > len(json.dumps(value).encode("utf-8"))
    native._bounded(value, size, "Synthetic")
    with pytest.raises(ValueError, match="no truncation"):
        native._bounded(value, size - 1, "Synthetic")


def test_public_protected_output_and_collision(data):
    for path in [Path(native.__file__).parent / "forbidden.json", Path(data["old"]["cfg"]["episodes"][0]["raw"]).parent / "forbidden.json"]:
        with pytest.raises(ValueError): native.apply(pin(data["sub_path"]), path)
        assert not path.exists()
    native.apply(pin(data["sub_path"]), data["output"])
    before = data["output"].read_bytes()
    with pytest.raises(FileExistsError): native.apply(pin(data["sub_path"]), data["output"])
    assert data["output"].read_bytes() == before


def test_pin_race_before_write(data, monkeypatch):
    original = native._derive
    def change(*args):
        result = original(*args)
        data["report"].write_text("changed after derive")
        return result
    monkeypatch.setattr(native, "_derive", change)
    with pytest.raises(ValueError): native.apply(pin(data["sub_path"]), data["output"])
    assert not data["output"].exists()


def test_cli_contract_and_safe_failure(data, capsys, monkeypatch):
    dotenv = Mock(); monkeypatch.setattr(native, "load_project_dotenv", dotenv)
    assert native.main(["schema", "--kind", "submission"]) == 0
    assert native.main(["prepare", "--input", str(data["cfg_path"]), "--sha256", sha256_file(data["cfg_path"])]) == 0
    out = capsys.readouterr().out
    assert "packet_sha256" in out and "source_anchors" in out
    assert native.main(["validate", "--input", str(data["sub_path"]), "--sha256", "a" * 64]) == 2
    out = capsys.readouterr().out
    assert str(data["sub_path"]) not in out and "Fictional host" not in out
    assert dotenv.call_count == 3
