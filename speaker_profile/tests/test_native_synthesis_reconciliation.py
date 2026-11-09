"""Fictional source tests in the canonical Profile suite; one real loader chain.

No corpus inputs, network, backend calls, subprocesses or semantic approvals.
"""
import copy
import json
from pathlib import Path

import pytest

from speaker_profile import native_synthesis_reconciliation as subject
from speaker_profile import release
from speaker_profile.tests.test_pipeline import item, synthesis_proposal
from speaker_profile.tests.test_native_synthesis import no_live_calls
from speaker_profile.tests.test_native_synthesis_extension import universe
from transcription.common import atomic_write_json, sha256_file


def pin(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def save(path, value):
    atomic_write_json(path, value)
    return pin(path)


def read(ref):
    return subject.native._read(ref, {})


def snapshots(root):
    return {str(p): sha256_file(p) for p in root.rglob("*") if p.is_file()}


def anchors(packet, ids):
    return [{"evidence_id": rid, **packet["observations"][rid]["item"]["quotes"][0]} for rid in sorted(ids)]


def submission(config_ref, cfg, packet):
    return {"schema_version": subject.SUBMISSION_VERSION, "config": config_ref,
        "actor": cfg["actor"], "packet_sha256": subject.pipeline.digest(packet),
        "authored_at": "2026-01-03T00:00:00Z", "method": "Fictional native comparison",
        "limitations": ["No semantic approval"], "model_metadata": subject.native.MODEL_METADATA,
        "content": synthesis_proposal([r["item"] for r in packet["observations"].values()], []),
        "hypothesis_anchors": [], "comparisons": [], "inherited_dispositions": [],
        "reading": {"inspected_evidence_ids": packet["scope"]["required_evidence_ids"],
            "pending_evidence_ids": [], "method": "Fictional full mapped closure read", "limitations": ["No audio"]},
        "context_reading": {"inspected_context_ids": sorted(packet["additional_source_context"]),
            "pending_context_ids": [], "method": "Fictional context read", "limitations": ["Not verified facts"]},
        "comparison_coverage": [{"comparison_id": t["comparison_id"], "evidence_ids": t["evidence_ids"],
            "status": "pending", "method": "Not adjudicated"} for t in cfg["comparison_tasks"]]}


def inspect_comparison(data):
    cfg, packet, s = data["config"], data["packet"], data["submission"]
    task = cfg["comparison_tasks"][0]
    ids = subject.inventory.base._basis_closure(data["index"]["evidence"], task["evidence_ids"])
    s["comparison_coverage"][0]["status"] = "inspected"
    s["comparisons"] = [{"comparison_id": task["comparison_id"],
        "endpoints": [{"evidence_id": rid, "evidence_kind": packet["observations"][rid]["item"]["evidence_kind"]}
                      for rid in task["evidence_ids"]],
        "relationship": "unresolved", "reason": "Strong preferences differ; no common prohibition inferred.",
        "hypothesis_ids": [], "parent_hypothesis_keys": task["parent_hypothesis_keys"],
        "source_anchors": anchors(packet, ids), "report_refs": [cfg["reports"][0]["ref"]],
        "context_citations": [{"context_id": cid, **packet["additional_source_context"][cid]["raw_regions"][0]}
                              for cid in task["context_ids"]]}]


@pytest.fixture
def ready(tmp_path, monkeypatch):
    root = tmp_path
    ext, inv, native = subject.extension, subject.inventory, subject.native
    raw_rows = [{"segment_id": i, "start": i * 10, "end": i * 10 + 9,
                 "text": "Fictional source row " + str(i)} for i in range(7)]
    raw_ref = save(root / "source/raw.json", {"segments": raw_rows})
    mapped = [{"merged_id": i, "source_segment_ids": [i], "raw_text": row["text"],
        "start": row["start"], "end": row["end"], "terminology_annotations": []} for i, row in enumerate(raw_rows)]
    norm_ref = save(root / "source/normalized.json", {"segments": mapped})
    source = {"episode_id": "SYN1", "segments": mapped, "raw_segments": {r["segment_id"]: r for r in raw_rows}}
    items = [item(source, i, "inferred_tendency" if i == 3 else "reported_action" if i == 2 else "direct_statement",
                  0 if i == 3 else i - 1) for i in range(1, 7)]
    items[-1]["speaker_attribution"] = "listener"
    groundings = {r["evidence_id"]: {"merged_ids": r["merged_ids"],
        "source_segment_ids": r["merged_ids"], "source_segment_id_ranges": [], "supplementary_context": [],
        "source_issues": [{"authority": "reviewer_commentary_not_source", "note": "Fictional uncertainty"}]}
        for r in items}
    artifact = {"source": {"episode_id": "SYN1", "publication_date": "2025-01-01",
            "source_acceptance": "not_granted", "reviewed_annotations": {"annotations": []}},
        "policy": subject.pipeline.POLICY, "content": {"items": items,
            "unselected_regions": [{"merged_ids": [6], "note": "Listener setting, not host evidence"}]},
        "grounding": {"items": groundings, "unselected_regions": []}, "authorship": {"method": "Fictional"},
        "model_metadata": native.MODEL_METADATA, "adoption": native.ADOPTION}
    evidence_ref = save(root / "source/evidence.json", artifact)
    descriptor = {"kind": "native", "artifact": evidence_ref}
    old_h = synthesis_proposal(items, ["SYN1:E3"])["hypotheses"][0]
    old_h["counterevidence"] = ["SYN1:E2"]
    pending_task = {"comparison_id": "P1", "evidence_ids": ["SYN1:E1", "SYN1:E6"],
                    "status": "pending", "method": "Fictional unresolved comparison"}
    old_value = {"schema_version": subject.parent.VERSION, "content": {"hypotheses": [old_h]},
                 "comparison_coverage": [pending_task], "known_context": [], "implementation": {"old": "frozen"}}
    old_ref = save(root / "old/candidate.json", old_value)
    old_key = old_ref["sha256"] + ":PROFILE:H1"
    index = {"schema_version": inv.VERSION, "episodes": {"SYN1": {
            "evidence": {"status": "available", "input": descriptor}, "original_manifest": None,
            "authority": native.AUTHORITY, "source_hashes": {"raw_sha256": raw_ref["sha256"],
                "normalized_sha256": norm_ref["sha256"]},
            "native_lineage": {"source_files": {"raw": raw_ref, "normalized": norm_ref}}}},
        "evidence": {r["evidence_id"]: {"episode_id": "SYN1", "artifact": evidence_ref,
            "evidence_key": evidence_ref["sha256"] + ":" + r["evidence_id"],
            "basis_evidence_ids": r["basis_evidence_ids"]} for r in items},
        "syntheses": {"old": {"synthesis": {"status": "available", "input": {"kind": "native", "artifact": old_ref}},
            "schema_version": subject.parent.VERSION}},
        "hypotheses": {old_key: {**old_h, "artifact": old_ref, "content_pointer": "/content/hypotheses/0",
            "recovered_comparisons": [], "authority": native.AUTHORITY,
            "anchor_binding": {"source_anchors": [{"evidence_id": "SYN1:E1", **raw_rows[0]}]}}},
        "native_comparisons": {}, "native_pending_comparisons": {old_ref["sha256"] + ":P1": {
            "artifact": old_ref, "pointer": "/comparison_coverage/0", "task": pending_task}},
        "native_context": {}, "inventory_complete": False, "gaps": [{"reason": "Outside scope"}],
        "dependencies": [evidence_ref, raw_ref, norm_ref, old_ref],
        "protected_output_roots": [str(root / "source")], "policy": inv.POLICY}
    index_ref = save(root / "inventory.json", index)
    selection_ref = save(root / "selection.json", {"synthetic": True})
    def checked(ref, selection):
        assert selection == selection_ref
        return native._read(ref, {})
    monkeypatch.setattr(inv, "load_checked", checked)
    report_ref = save(root / "report.json", {"method": "Fictional comparison source review"})
    actor = {"identity": "Fictional author", "session_id": "synthetic"}
    reports = [{"ref": report_ref, "authored_at": "2026-01-01T00:00:00Z", "method": "Fictional"}]
    limits = {"max_packet_bytes": 2_000_000, "max_submission_bytes": 1_000_000, "max_candidate_bytes": 2_000_000}
    ecfg = {"schema_version": ext.CONFIG_VERSION, "inventory": index_ref, "selection": selection_ref,
        "actor": actor, "reports": reports, "workset_evidence_ids": list(index["evidence"]), "limits": limits}
    ecfg_ref = save(root / "extension/config.json", ecfg)
    epacket = ext.prepare(ecfg_ref)["packet"]
    es = submission(ecfg_ref, {**ecfg, "comparison_tasks": []},
                    {**epacket, "additional_source_context": {}})
    es.pop("context_reading")
    es.update(schema_version=ext.SUBMISSION_VERSION, packet_sha256=subject.pipeline.digest(epacket))
    es["content"] = synthesis_proposal(items, ["SYN1:E5"])
    es["content"]["hypotheses"][0]["counterevidence"] = ["SYN1:E2"]
    for row in es["content"]["evidence_accounting"]:
        if row["evidence_id"] == "SYN1:E2":
            row.update(disposition="in_hypotheses", hypothesis_ids=["PROFILE:H1"])
    es["hypothesis_anchors"] = [{"hypothesis_id": "PROFILE:H1",
        "source_anchors": anchors(epacket, ["SYN1:E5", "SYN1:E2"]), "report_refs": [report_ref]}]
    # Basis-only incidence: E3's basis E1 connects this unowned comparison.
    es["comparison_coverage"] = [{"comparison_id": "C1", "evidence_ids": ["SYN1:E3", "SYN1:E4"],
                                "status": "inspected", "method": "Fictional comparison"}]
    es["comparisons"] = [{"comparison_id": "C1", "endpoints": [
        {"evidence_id": rid, "evidence_kind": epacket["observations"][rid]["item"]["evidence_kind"]}
        for rid in ["SYN1:E3", "SYN1:E4"]], "relationship": "unresolved", "reason": "Unknown context",
        "hypothesis_ids": [], "source_anchors": anchors(epacket, ["SYN1:E1", "SYN1:E3", "SYN1:E4"]),
        "report_refs": [report_ref]}]
    extension_ref = ext.apply(save(root / "extension/submission.json", es), root / "extension/candidate.json")
    new_key = extension_ref["sha256"] + ":PROFILE:H1"
    cfg = {"schema_version": subject.CONFIG_VERSION, "inventory": index_ref, "selection": selection_ref,
        "parents": [{"kind": "native", "artifact": old_ref}, {"kind": "native_extension", "artifact": extension_ref}],
        "target_hypothesis_keys": [old_key, new_key], "workset_evidence_ids": ["SYN1:E2"],
        "comparison_tasks": [{"comparison_id": "T1", "evidence_ids": ["SYN1:E3", "SYN1:E5"],
            "parent_hypothesis_keys": [old_key, new_key], "context_ids": ["setting"], "purpose": "Compare conditions"}],
        "additional_source_context": [{"context_id": "setting", "episode_id": "SYN1", "evidence": evidence_ref,
            "raw": raw_ref, "normalized": norm_ref, "merged_ids": [6], "purpose": "Listener situation only"}],
        "actor": actor, "reports": reports,
        "limits": {**limits, "max_parents": 2, "max_metadata_bytes": 2_000_000}}
    cfg_ref = save(root / "config.json", cfg)
    packet = subject.prepare(cfg_ref)["packet"]
    data = {"root": root, "config": cfg, "config_ref": cfg_ref, "index": index, "packet": packet,
        "submission": submission(cfg_ref, cfg, packet), "output": root / "result/candidate.json",
        "old": old_ref, "extension": extension_ref, "raw": raw_ref, "normalized": norm_ref}
    inspect_comparison(data)
    return data


def submit(data):
    return save(data["root"] / "submission.json", data["submission"])


def reconfigure(data):
    ref = save(data["root"] / "config.json", data["config"])
    data["config_ref"] = ref
    packet = subject.prepare(ref)["packet"]
    data["packet"] = packet
    data["submission"].update(config=ref, packet_sha256=subject.pipeline.digest(packet))
    return packet


def test_comparison_only_actual_extension_replay_context_and_immutable_originals(ready):
    packet = ready["packet"]
    assert packet["scope"]["required_evidence_ids"] == [f"SYN1:E{i}" for i in range(1, 7)]
    assert len(packet["target_hypothesis_keys"]) == 2  # Same H1 ID, distinct exact parent versions.
    assert {r["segment_id"] for r in packet["raw_regions"]["SYN1"]} == {0, 1, 3, 4, 5}
    assert packet["additional_source_context"]["setting"]["raw_regions"][0]["segment_id"] == 6
    assert packet["observations"]["SYN1:E6"]["item"]["speaker_attribution"] == "listener"
    assert packet["source_context"]["SYN1"]["source"]["source_acceptance"] == "not_granted"
    kinds = {r["kind"] for r in packet["inherited_obligations"]}
    assert kinds == {"prior_hypothesis", "native_comparison", "pending_comparison"}
    assert packet["parents"][1]["inherited_obligations"]
    assert all(r["disposition"] == "pending" for r in packet["parents"][1]["inherited_dispositions"])
    ref = submit(ready)
    before = snapshots(ready["root"])
    result = subject.preview(ref, ready["output"])
    assert snapshots(ready["root"]) == before
    assert result["authored"]["content"]["hypotheses"] == []
    assert result["grounding"]["retained_evidence_ids"] == packet["scope"]["required_evidence_ids"]
    adopted = subject.apply(ref, ready["output"])
    replay = subject.read(adopted)
    assert replay["packet"] == packet
    assert replay["candidate"]["authored"]["comparisons"][0]["relationship"] == "unresolved"
    assert not replay["candidate"]["policy"]["global_completion"]
    assert replay["candidate"]["model_metadata"]["usage"] is None
    assert all(sha256_file(Path(p)) == h for p, h in before.items())


@pytest.mark.parametrize("fault", ["packet", "actor", "accounting", "coverage", "endpoint", "kind", "parent_version",
    "context_quote", "context_id", "context_missing", "context_unread", "basis_anchor", "primary_from_context",
    "counter_unread", "report", "disposition", "model", "extra_field"])
def test_authored_tamper_fails_without_output(ready, fault):
    s, row = ready["submission"], ready["submission"]["comparisons"][0]
    if fault == "packet": s["packet_sha256"] = "0" * 64
    elif fault == "actor": s["actor"]["identity"] = "Other"
    elif fault == "accounting": s["content"]["evidence_accounting"].pop()
    elif fault == "coverage": s["comparison_coverage"] = []
    elif fault == "endpoint": row["endpoints"][0]["evidence_id"] = "SYN1:E4"
    elif fault == "kind": row["endpoints"][0]["evidence_kind"] = "reported_action"
    elif fault == "parent_version": row["parent_hypothesis_keys"] = ["0" * 64 + ":PROFILE:H1"]
    elif fault == "context_quote": row["context_citations"][0]["text"] = "Invented"
    elif fault == "context_id": row["context_citations"][0]["context_id"] = "unknown"
    elif fault == "context_missing": row["context_citations"] = []
    elif fault == "context_unread":
        s["context_reading"].update(inspected_context_ids=[], pending_context_ids=["setting"])
    elif fault == "basis_anchor": row["source_anchors"] = [r for r in row["source_anchors"] if r["evidence_id"] != "SYN1:E1"]
    elif fault == "primary_from_context":
        row["source_anchors"][0].update(ready["packet"]["additional_source_context"]["setting"]["raw_regions"][0])
    elif fault == "counter_unread":
        s["reading"]["inspected_evidence_ids"].remove("SYN1:E2")
        s["reading"]["pending_evidence_ids"] = ["SYN1:E2"]
    elif fault == "report": row["report_refs"] = [ready["config_ref"]]
    elif fault == "disposition":
        s["inherited_dispositions"] = [{"obligation_key": "missing", "disposition": "retained", "reason": "Fictional",
            "source_anchors": row["source_anchors"], "report_refs": row["report_refs"], "context_citations": []}]
    elif fault == "model": s["model_metadata"] = {**s["model_metadata"], "usage": {"tokens": 0}}
    else: s["approved"] = True
    ref = submit(ready)
    before = snapshots(ready["root"])
    with pytest.raises(ValueError): subject.apply(ref, ready["output"])
    assert snapshots(ready["root"]) == before


@pytest.mark.parametrize("fault", ["duplicate_parent", "duplicate_hash", "alias", "wrong_parent_kind", "nested",
    "target_version", "same_parent_targets", "unknown_evidence", "task_context", "duplicate_task", "duplicate_context",
    "wrong_raw", "wrong_normalized", "wrong_evidence", "missing_mid", "parent_inventory", "parent_selection"])
def test_config_identity_failures(ready, fault):
    cfg = ready["config"]
    if fault == "duplicate_parent": cfg["parents"][1] = cfg["parents"][0]
    elif fault == "duplicate_hash":
        other = save(ready["root"] / "same-parent.json", read(ready["old"]))
        cfg["parents"][1] = {"kind": "native", "artifact": other}
    elif fault == "alias":
        alias = ready["root"] / "alias.json"
        alias.symlink_to(ready["extension"]["path"])
        cfg["parents"][1]["artifact"] = {**ready["extension"], "path": str(alias)}
    elif fault == "wrong_parent_kind": cfg["parents"][1]["kind"] = "native"
    elif fault == "nested":
        ref = save(ready["root"] / "nested.json", {"schema_version": subject.VERSION})
        cfg["parents"][1]["artifact"] = ref
    elif fault == "target_version": cfg["target_hypothesis_keys"][1] = "0" * 64 + ":PROFILE:H1"
    elif fault == "same_parent_targets": cfg["target_hypothesis_keys"] = [cfg["target_hypothesis_keys"][0]] * 2
    elif fault == "unknown_evidence": cfg["workset_evidence_ids"].append("SYN1:MISSING")
    elif fault == "task_context": cfg["comparison_tasks"][0]["context_ids"] = ["unknown"]
    elif fault == "duplicate_task": cfg["comparison_tasks"].append(cfg["comparison_tasks"][0])
    elif fault == "duplicate_context": cfg["additional_source_context"].append(cfg["additional_source_context"][0])
    elif fault in {"wrong_raw", "wrong_normalized", "wrong_evidence"}:
        cfg["additional_source_context"][0][fault.removeprefix("wrong_")] = ready["old"]
    elif fault == "missing_mid": cfg["additional_source_context"][0]["merged_ids"] = [999]
    else:
        candidate = read(ready["extension"])
        candidate["inherited_inventory"][fault.removeprefix("parent_")] = ready["old"]
        cfg["parents"][1]["artifact"] = save(ready["root"] / "wrong-parent.json", candidate)
    with pytest.raises(ValueError): subject.prepare(save(ready["root"] / "bad-config.json", cfg))
    assert not ready["output"].exists()


@pytest.mark.parametrize("limit", ["max_parents", "max_metadata_bytes", "max_packet_bytes", "max_submission_bytes", "max_candidate_bytes"])
def test_limits_no_trimming(ready, limit):
    ready["config"]["limits"][limit] = 1
    if limit in {"max_submission_bytes", "max_candidate_bytes"}:
        reconfigure(ready)
        with pytest.raises(ValueError, match="byte limit"): subject.apply(submit(ready), ready["output"])
    else:
        with pytest.raises(ValueError): reconfigure(ready)
    assert not ready["output"].exists()


def test_candidate_rehash_and_input_mutation_fail_replay(ready):
    ref = subject.apply(submit(ready), ready["output"])
    value = read(ref)
    value["inherited_obligations"] = []
    bad_ref = save(ready["output"], value)
    with pytest.raises(ValueError, match="deterministic replay"): subject.read(bad_ref)
    with pytest.raises(ValueError): subject.read(ref)
    Path(ready["raw"]["path"]).write_text("{}")
    with pytest.raises(ValueError): subject.prepare(ready["config_ref"])


def test_collisions_and_pending_do_not_resolve_originals(ready):
    ref = submit(ready)
    with pytest.raises(FileExistsError): subject.preview(ref, ref["path"])
    with pytest.raises(ValueError, match="protected"):
        subject.apply(ref, ready["root"] / "source/new.json")
    obligation = next(r for r in ready["packet"]["inherited_obligations"] if r["kind"] == "pending_comparison")
    ready["submission"]["inherited_dispositions"] = [{"obligation_key": obligation["obligation_key"],
        "disposition": "unresolved", "reason": "Both sides inspected; no resolution",
        "source_anchors": anchors(ready["packet"], obligation["required_evidence_ids"]),
        "report_refs": [ready["config"]["reports"][0]["ref"]], "context_citations": []}]
    result = subject.preview(submit(ready), ready["output"])
    assert next(r for r in result["inherited_obligations"] if r["kind"] == "pending_comparison")["original"]["task"]["status"] == "pending"
    assert any(r["disposition"] == "unresolved" for r in result["inherited_dispositions"])
    ready["submission"]["comparisons"] = []
    ready["submission"]["comparison_coverage"][0]["status"] = "pending"
    result = subject.preview(submit(ready), ready["output"])
    assert result["authored"]["comparison_coverage"][0]["status"] == "pending"


def test_parent_version_binding_collision_even_with_same_item_id(ready):
    cfg = ready["config"]
    index = copy.deepcopy(ready["index"])
    index["evidence"]["SYN1:E1"]["evidence_key"] = "0" * 64 + ":SYN1:E1"
    parent = read(ready["extension"])
    packet = subject.extension.read(ready["extension"])["packet"]
    with pytest.raises(ValueError, match="ID/version"):
        subject._join_extension(index, cfg["parents"][1]["artifact"], parent, packet)
    index = copy.deepcopy(ready["index"])
    subject._join_extension(index, ready["extension"], parent, packet)
    with pytest.raises(ValueError, match="Duplicate version-qualified"):
        subject._join_extension(index, ready["extension"], parent, packet)


@pytest.mark.parametrize("fault", ["duplicate_merged", "duplicate_raw", "duplicate_mapping", "missing_raw", "bool_id"])
def test_additional_map_integrity(ready, fault):
    # Isolate the map validator using a freshly pinned synthetic source version.
    cfg, index = copy.deepcopy(ready["config"]), copy.deepcopy(ready["index"])
    raw, norm = read(ready["raw"]), read(ready["normalized"])
    if fault == "duplicate_merged": norm["segments"].append(norm["segments"][-1])
    elif fault == "duplicate_raw": raw["segments"].append(raw["segments"][-1])
    elif fault == "duplicate_mapping": norm["segments"][-1]["source_segment_ids"] = [6, 6]
    elif fault == "bool_id": raw["segments"][-1]["segment_id"] = True
    else: raw["segments"].pop()
    rr = save(ready["root"] / "map-test/raw.json", raw)
    nr = save(ready["root"] / "map-test/norm.json", norm)
    cfg["additional_source_context"][0].update(raw=rr, normalized=nr)
    entry = index["episodes"]["SYN1"]
    entry["source_hashes"].update(raw_sha256=rr["sha256"], normalized_sha256=nr["sha256"])
    entry["native_lineage"]["source_files"].update(raw=rr, normalized=nr)
    sources = copy.deepcopy(ready["packet"]["source_context"])
    sources["SYN1"]["raw"] = rr
    with pytest.raises(ValueError): subject._additional_context(cfg, index, sources, {})


def test_authored_expansion_cannot_omit_incident_obligations(ready):
    cfg, index, packet = subject._prepare(ready["config_ref"], {})
    # A claim incident to a newly discovered comparison must not use the old packet.
    index["native_pending_comparisons"]["new-obligation"] = {
        "artifact": ready["old"], "pointer": "/synthetic/pending",
        "task": {"comparison_id": "new", "evidence_ids": ["SYN1:E3", "SYN1:E4"],
                 "status": "pending", "method": "New incident synthetic context"}}
    with pytest.raises(ValueError, match="expand incident context"):
        subject._authored(ready["submission"], cfg, index, packet, {})


def test_removed_parent_counter_closure_rejected(ready):
    parent = read(ready["extension"])
    packet = subject.extension.read(ready["extension"])["packet"]
    packet["observations"].pop("SYN1:E2")
    with pytest.raises(ValueError, match="closure/accounting"):
        subject._join_extension(copy.deepcopy(ready["index"]), ready["extension"], parent, packet)


def test_rehashed_parent_obligation_tamper_rejected(ready):
    value = read(ready["extension"])
    value["inherited_obligations"] = []
    ready["config"]["parents"][1]["artifact"] = save(ready["root"] / "tampered-parent.json", value)
    with pytest.raises(ValueError, match="deterministic replay"):
        subject.prepare(save(ready["root"] / "tampered-config.json", ready["config"]))


def test_final_dependency_and_implementation_recheck(ready, monkeypatch):
    original = subject._authored
    def changed(*args):
        result = original(*args)
        Path(ready["config"]["reports"][0]["ref"]["path"]).write_text("{}")
        return result
    monkeypatch.setattr(subject, "_authored", changed)
    with pytest.raises(ValueError): subject.apply(submit(ready), ready["output"])
    assert not ready["output"].exists()
    monkeypatch.setattr(subject, "_implementation", lambda: {"changed": True})
    with pytest.raises(ValueError, match="implementation changed"):
        subject._finish({}, {"original": True})


def test_schema_cli_readonly(ready, capsys, monkeypatch):
    monkeypatch.setattr(subject, "load_project_dotenv", lambda: None)
    assert subject.main(["schema", "--kind", "submission"]) == 0
    assert json.loads(capsys.readouterr().out)["properties"]["schema_version"]["enum"] == [subject.SUBMISSION_VERSION]
    before = snapshots(ready["root"])
    assert subject.main(["prepare", "--input", ready["config_ref"]["path"], "--sha256", ready["config_ref"]["sha256"]]) == 0
    assert json.loads(capsys.readouterr().out)["packet"] == ready["packet"]
    assert snapshots(ready["root"]) == before


def test_real_loaders_and_existing_implementation_bytes_unchanged(universe, tmp_path):
    """No mocked loader: synthetic native evidence -> old H -> extension -> comparison."""
    ext = subject.extension
    original_modules = {str(Path(m.__file__)): sha256_file(Path(m.__file__))
                        for m in (ext, subject.inventory, subject.parent)}
    cfg = {"schema_version": ext.CONFIG_VERSION, "inventory": universe["inventory"],
        "selection": universe["selection"], "actor": universe["actor"], "reports": universe["reports"],
        "workset_evidence_ids": ["SYN1:E3", "SYN2:E2"],
        "limits": {"max_packet_bytes": 2_000_000, "max_submission_bytes": 1_000_000, "max_candidate_bytes": 2_000_000}}
    cfg_ref = save(tmp_path / "extension-config.json", cfg)
    ep = ext.prepare(cfg_ref)["packet"]
    s = submission(cfg_ref, {**cfg, "comparison_tasks": []}, {**ep, "additional_source_context": {}})
    s.pop("context_reading")
    s.update(schema_version=ext.SUBMISSION_VERSION, packet_sha256=subject.pipeline.digest(ep))
    s["content"] = synthesis_proposal([r["item"] for r in ep["observations"].values()], ["SYN2:E2"])
    s["hypothesis_anchors"] = [{"hypothesis_id": "PROFILE:H1", "source_anchors": anchors(ep, ["SYN2:E2"]),
                                "report_refs": [universe["reports"][0]["ref"]]}]
    new_ref = ext.apply(save(tmp_path / "extension-submission.json", s), tmp_path / "extension-candidate.json")
    keys = [universe["old"]["sha256"] + ":PROFILE:H1", new_ref["sha256"] + ":PROFILE:H1"]
    cfg.update(schema_version=subject.CONFIG_VERSION,
        parents=[{"kind": "native", "artifact": universe["old"]}, {"kind": "native_extension", "artifact": new_ref}],
        target_hypothesis_keys=keys, additional_source_context=[],
        comparison_tasks=[{"comparison_id": "T1", "evidence_ids": ["SYN1:E3", "SYN2:E2"],
            "parent_hypothesis_keys": keys, "context_ids": [], "purpose": "Compare fictional claims"}])
    cfg["limits"].update(max_parents=2, max_metadata_bytes=4_000_000)
    ref = save(tmp_path / "config.json", cfg)
    packet = subject.prepare(ref)["packet"]
    s = submission(ref, cfg, packet)
    data = {"config": cfg, "packet": packet, "submission": s,
            "index": subject.inventory.load_checked(universe["inventory"], universe["selection"])}
    inspect_comparison(data)
    adopted = subject.apply(save(tmp_path / "submission.json", s), tmp_path / "candidate.json")
    assert subject.read(adopted)["packet"] == packet
    assert subject.parent.load_checked(universe["old"])["content"]["hypotheses"]
    assert ext.load_checked(new_ref)["authored"]["content"]["hypotheses"]
    assert release.load_checked(universe["release"])["bundle"]["hypotheses"]
    assert snapshots(universe["root"]) == {str(p): h for p, h in universe["baseline"].items()}
    assert all(sha256_file(Path(p)) == h for p, h in original_modules.items())
