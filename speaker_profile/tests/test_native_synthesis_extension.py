"""Tiny genuine loader/replay integration using fictional sources and authorship.

No patched loaders, backend stubs, model calls, real data or network are used.
The pure obligation tests also cover historical/context records not produced by
the native-only integration fixture. Synthetic reviews are not semantic approval.
"""
import copy
import json
from pathlib import Path

import pytest

from speaker_profile import inventory_extension as inventory, native_evidence as evidence
from speaker_profile import native_synthesis as old, native_synthesis_extension as consumer, release
from transcription.common import PROJECT_ROOT, atomic_write_json, read_json, sha256_file, utc_now
from transcription.prepare import prepare as normalize
from transcription.tests.test_annotations import annotation_review
from .test_pipeline import evidence_proposal, synthesis_proposal
from .test_native_synthesis import build, write
from .test_release import author_review


def pin(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def save(path, value):
    atomic_write_json(path, value)
    return pin(path)


def snapshots(root):
    return {p: sha256_file(p) for p in root.rglob("*") if p.is_file()}


@pytest.fixture(scope="module")
def universe(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic-synthesis-extension")
    actor = {"identity": "Fictional existing author", "session_id": "synthetic-session"}
    entries = []
    for n in range(1, 4):
        eid = f"SYN{n}"
        directory = root / eid
        directory.mkdir()
        raw, draft = directory / "raw.json", directory / "draft.json"
        save(raw, {"metadata": {"episode_id": eid, "model": "synthetic", "audio_sha256": "a" * 64,
            "language": "en", "audio_duration_seconds": 150,
            "source_episode_metadata": {"episode_id": eid, "publication_date": f"202{n}-01-01"}},
            "segments": [{"segment_id": i, "start": i * 50, "end": i * 50 + 45, "text": text}
                for i, text in enumerate(("Fictional host: I prefer time with friends over saving money.",
                    "Fictional host: I chose to wait; the outcome is not reported.",
                    "Fictional programme logistics, not speaker reasoning."))]})
        normalize(raw, draft)
        review_ref = None
        if n == 1:
            review, _, _ = annotation_review(raw, directory / "reviews")
            value = read_json(review)
            value.update(decision="uncertain", unresolved_critical_issues=["Fictional unresolved source limitation"])
            review_ref = save(review, value)
        config = save(directory / "config.json", {"schema_version": evidence.CONFIG_VERSION,
            "packet_id": eid, "actor": actor, "episode": {"episode_id": eid, "raw": pin(raw),
                "normalized": pin(draft), "source_review": review_ref}, "reserved_episode_ids": ["RESERVED"],
            "commentary_scope": inventory.base.schemas.EPISODE_CONTEXT,
            "max_packet_bytes": 1_000_000, "max_submission_bytes": 200_000})
        packet_ref = evidence.freeze(config, directory / "packet.json")
        context = read_json(Path(packet_ref["path"]))["context"]
        source = {**context["source"], "segments": context["merged_segments"],
                  "raw_segments": {r["segment_id"]: r for r in context["raw_segments"]}}
        content = evidence_proposal(source)
        content["items"][0]["supplementary_notes"] = [{"merged_ids": [2], "note": "Fictional analyst logistics note."}]
        if n == 3:
            content["items"][1]["speaker_attribution"] = "listener"
        submission = save(directory / "submission.json", {"schema_version": evidence.SUBMISSION_VERSION,
            "packet": packet_ref, "actor": actor, "authored_at": utc_now(),
            "method": "Synthetic authored fixture; not actual source review.", "complete_episode_read": True,
            "limitations": ["No source or semantic approval"], "model_metadata": evidence.MODEL_METADATA,
            "content": content})
        ref = evidence.apply(submission, directory / "candidate.json")
        entries.append({"episode_id": eid, "evidence": {"status": "available",
                        "input": {"kind": "native", "artifact": ref}}})
    entries.append({"episode_id": "SYN4", "evidence": {"status": "missing",
        "reason": "Fictional episode has not been extracted", "record": None}})
    selection = save(root / "base-selection.json", {"schema_version": inventory.base.SELECTION_VERSION,
        "corpus_id": "fictional", "expected_episode_ids": [r["episode_id"] for r in entries],
        "reserved_episode_ids": ["RESERVED"], "episodes": entries, "expected_synthesis_ids": [], "syntheses": []})
    base_ref = inventory.base.freeze(selection, root / "base/index.json")
    directory = root / "old-synthesis"
    directory.mkdir()
    data = build(base_ref, directory, support=("SYN1:E3",))
    s = data["submission"]
    template = s["content"]["hypotheses"][0]
    template["counterevidence"] = ["SYN2:E2"]
    template["conditions"] = ["Only the fictional circumstance; not unconditional"]
    template["exceptions"] = ["Unresolved difference in the reported action"]
    s["content"]["hypotheses"] = []
    s["hypothesis_anchors"] = []
    anchors = lambda ids: [{"evidence_id": rid, **data["catalog"][rid]["quotes"][0]} for rid in ids]
    reports = [data["config"]["reports"][0]["ref"]]
    for n in range(1, 10):
        h = copy.deepcopy(template)
        h["hypothesis_id"] = f"PROFILE:H{n}"
        s["content"]["hypotheses"].append(h)
        s["hypothesis_anchors"].append({"hypothesis_id": h["hypothesis_id"],
            "source_anchors": anchors(["SYN1:E1", "SYN1:E3", "SYN2:E2"]), "report_refs": reports})
    for row in s["content"]["evidence_accounting"]:
        used = row["evidence_id"] in {"SYN1:E3", "SYN2:E2"}
        row.update(disposition="in_hypotheses" if used else "retained_without_hypothesis",
                   hypothesis_ids=[h["hypothesis_id"] for h in s["content"]["hypotheses"]] if used else [])
    for cid, ids, status in (("statements", ["SYN1:E1", "SYN2:E1"], "inspected"),
                             ("actions", ["SYN1:E2", "SYN2:E2"], "inspected"),
                             ("pending", ["SYN1:E1", "SYN2:E3"], "pending"),
                             ("outside", ["SYN3:E1", "SYN3:E2"], "pending")):
        data["config"]["comparison_coverage"].append({"comparison_id": cid, "evidence_ids": ids,
            "status": status, "method": "Fictional bounded comparison; not independent events."})
        if status == "inspected":
            s["comparisons"].append({"comparison_id": cid, "endpoints": [{"evidence_id": rid,
                "evidence_kind": data["catalog"][rid]["evidence_kind"]} for rid in ids],
                "relationship": "unresolved", "reason": "Fictional contexts may differ", "hypothesis_ids": [],
                "source_anchors": anchors(ids), "report_refs": reports})
    old_ref = old.apply(write(data), data["output"])
    extension_selection = save(root / "extension-selection.json", {"schema_version": inventory.SELECTION_VERSION,
        "corpus_id": "fictional-extended", "base_inventory": base_ref,
        "limits": {"max_nodes": 200, "max_depth": 32, "max_metadata_bytes": 4_000_000},
        "reserved_episode_ids": ["RESERVED"], "expected_synthesis_ids": ["prior"],
        "native_syntheses": [{"synthesis_id": "prior", "kind": "native", "artifact": old_ref}]})
    ref = inventory.freeze(extension_selection, root / "extension/index.json")
    review_packet = release.prepare(old_ref, ["PROFILE:H1"])
    review_path = root / "fictional-review.json"
    author_review(review_packet, review_path)
    old_release = release.publish(pin(review_path), root / "old-release.json")
    return {"root": root, "inventory": ref, "selection": extension_selection, "actor": actor,
            "reports": data["config"]["reports"], "old": old_ref, "release": old_release,
            "baseline": snapshots(root)}


@pytest.fixture
def ready(universe, tmp_path):
    config = {"schema_version": consumer.CONFIG_VERSION, "inventory": universe["inventory"],
        "selection": universe["selection"], "actor": universe["actor"], "reports": universe["reports"],
        "workset_evidence_ids": ["SYN1:E3", "SYN2:E2"],
        "limits": {"max_packet_bytes": 1_000_000, "max_submission_bytes": 200_000, "max_candidate_bytes": 1_000_000}}
    config_ref = save(tmp_path / "config.json", config)
    prepared = consumer.prepare(config_ref)
    packet = prepared["packet"]
    content = synthesis_proposal([r["item"] for r in packet["observations"].values()], ["SYN1:E3", "SYN2:E3"])
    content["hypotheses"][0]["counterevidence"] = ["SYN2:E2"]
    for row in content["evidence_accounting"]:
        if row["evidence_id"] == "SYN2:E2":
            row.update(disposition="in_hypotheses", hypothesis_ids=["PROFILE:H1"])
    data = {"config": config, "config_ref": config_ref, "packet": packet, "root": tmp_path,
            "output": tmp_path / "candidate.json", "universe": universe}
    data["submission"] = {"schema_version": consumer.SUBMISSION_VERSION, "config": config_ref,
        "packet_sha256": prepared["packet_sha256"], "actor": universe["actor"], "authored_at": utc_now(),
        "method": "Fictional native synthesis", "limitations": ["Not semantic approval"],
        "model_metadata": evidence.MODEL_METADATA, "content": content,
        "reading": {"inspected_evidence_ids": packet["scope"]["required_evidence_ids"],
                    "pending_evidence_ids": [], "method": "Fictional inspection", "limitations": ["Unknown events"]},
        "hypothesis_anchors": [{"hypothesis_id": "PROFILE:H1", "report_refs": [universe["reports"][0]["ref"]],
            "source_anchors": claim_anchors(data, ["SYN1:E1", "SYN1:E3", "SYN2:E1", "SYN2:E2", "SYN2:E3"])}],
        "comparison_coverage": [], "comparisons": [], "inherited_dispositions": []}
    return data


def claim_anchors(data, ids):
    return [{"evidence_id": rid, **data["packet"]["observations"][rid]["item"]["quotes"][0]} for rid in ids]


def submit(data):
    return save(data["root"] / "submission.json", data["submission"])


def test_actual_prepare_adopt_read_retains_all_lineage_and_old_release(ready):
    packet = ready["packet"]
    assert len(packet["inherited_inventory"]["hypotheses_keys"]) == 9
    assert len(packet["inherited_inventory"]["native_pending_comparisons_keys"]) == 2
    assert packet["scope"]["outside_evidence_ids"] == ["SYN3:E1", "SYN3:E2", "SYN3:E3"]
    assert len(packet["observations"]) == 6
    assert {r["kind"] for r in packet["inherited_obligations"]} == {
        "prior_hypothesis", "native_comparison", "pending_comparison"}
    assert all(row["source_hypothesis"]["conditions"] for row in packet["inherited_obligations"]
               if row["kind"] == "prior_hypothesis")
    assert {r["segment_id"] for r in packet["raw_regions"]["SYN1"]} == {0, 1, 2}
    assert packet["source_context"]["SYN1"]["source"]["source_acceptance"] == "not_granted"
    assert packet["source_context"]["SYN1"]["original_manifest"] is None
    assert not packet["scope"]["inventory_complete"] and packet["scope"]["inventory_gaps"]
    inherited = packet["inherited_obligations"]
    assert {r["original"]["comparison"]["comparison_id"] for r in inherited
            if r["kind"] == "native_comparison"} == {"statements", "actions"}
    assert all(not r["original"]["comparison"]["hypothesis_ids"] for r in inherited
               if r["kind"] == "native_comparison")
    assert [r["original"]["task"]["comparison_id"] for r in inherited
            if r["kind"] == "pending_comparison"] == ["pending"]
    context = packet["source_context"]["SYN1"]
    original = read_json(Path(context["evidence"]["artifact"]["path"]))
    assert context["source"] == original["source"]
    assert context["source"]["reviewed_annotations"]["annotations"]
    assert context["source"]["prior_review_limitations"] == ["Fictional unresolved source limitation"]
    assert context["unselected_region_grounding"] == original["grounding"]["unselected_regions"]
    assert context["unselected_region_grounding"][0]["authority"] == "model_analyst_not_source"
    observation = packet["observations"]["SYN1:E1"]
    assert observation["grounding"] == original["grounding"]["items"]["SYN1:E1"]
    assert observation["grounding"]["source_issues"][0]["authority"] == "reviewer_commentary_not_source"
    assert observation["grounding"]["supplementary_context"][0]["authority"] == "model_analyst_not_speaker"
    ref = submit(ready)
    before = snapshots(ready["root"])
    preview = consumer.preview(ref, ready["output"])
    assert snapshots(ready["root"]) == before
    adopted = consumer.apply(ref, ready["output"])
    result = consumer.read(adopted)
    assert result["packet"] == packet
    candidate = result["candidate"]
    assert candidate["authored"]["content"] == preview["authored"]["content"]
    assert candidate["grounding"]["hypotheses"]["PROFILE:H1"]["independent_event_count"] is None
    assert len(candidate["grounding"]["retained_evidence_ids"]) == 6
    assert all(r["disposition"] == "pending" for r in candidate["inherited_dispositions"])
    assert candidate["adoption"]["model_calls_this_execution"] == 0
    assert candidate["model_metadata"]["model"] is candidate["model_metadata"]["usage"] is None
    assert not candidate["policy"]["eligible_for_serving"] and not candidate["policy"]["global_completion"]
    assert not {"manifest", "request", "result", "completed_call"} & candidate.keys()
    assert old.load_checked(ready["universe"]["old"])["content"]["hypotheses"]
    assert release.load_checked(ready["universe"]["release"])["bundle"]["hypotheses"]
    assert snapshots(ready["universe"]["root"]) == ready["universe"]["baseline"]
    assert not list(ready["root"].rglob("*.request.json"))


def test_counter_only_seed_keeps_prior_support_and_basis(ready):
    config = copy.deepcopy(ready["config"])
    config["workset_evidence_ids"] = ["SYN2:E2"]
    result = consumer.prepare(save(ready["root"] / "counter-config.json", config))["packet"]
    assert {"SYN1:E1", "SYN1:E3", "SYN2:E2"} <= result["observations"].keys()
    assert len([r for r in result["inherited_obligations"] if r["kind"] == "prior_hypothesis"]) == 9


def test_authored_same_kind_comparisons_and_pending_remain_distinct(ready):
    s = ready["submission"]
    for cid, ids, inspected in (("statements", ["SYN1:E1", "SYN2:E1"], True),
                                ("actions", ["SYN1:E2", "SYN2:E2"], True),
                                ("later", ["SYN1:E3", "SYN2:E3"], False)):
        s["comparison_coverage"].append({"comparison_id": cid, "evidence_ids": ids,
            "status": "inspected" if inspected else "pending", "method": "Fictional comparison"})
        if inspected:
            s["comparisons"].append({"comparison_id": cid, "endpoints": [{"evidence_id": rid,
                "evidence_kind": ready["packet"]["observations"][rid]["item"]["evidence_kind"]} for rid in ids],
                "relationship": "unresolved", "reason": "Different contexts", "hypothesis_ids": [],
                "source_anchors": claim_anchors(ready, ids), "report_refs": [ready["universe"]["reports"][0]["ref"]]})
    result = consumer.preview(submit(ready), ready["output"])
    assert len(result["authored"]["comparisons"]) == 2
    assert len(result["authored"]["comparison_coverage"]) == 3
    assert result["authored"]["content"]["hypotheses"][0]["statement_action_relationships"] == []


def test_anchored_context_disposition_does_not_rewrite_inherited_pending(ready):
    row = next(r for r in ready["packet"]["inherited_obligations"] if r["kind"] == "pending_comparison")
    ready["submission"]["inherited_dispositions"] = [{"obligation_key": row["obligation_key"],
        "disposition": "unresolved", "reason": "Read both sides but cannot decide the relationship",
        "source_anchors": claim_anchors(ready, row["required_evidence_ids"]),
        "report_refs": [ready["universe"]["reports"][0]["ref"]]}]
    result = consumer.preview(submit(ready), ready["output"])
    assert next(r for r in result["inherited_obligations"] if r["kind"] == "pending_comparison")["original"]["task"]["status"] == "pending"
    assert next(r for r in result["inherited_dispositions"] if r["obligation_key"] == row["obligation_key"])["disposition"] == "unresolved"


@pytest.mark.parametrize("fault", ["packet", "actor", "accounting", "outside", "pending_claim", "basis_anchor",
    "quote", "time", "supplementary_support", "fake_model", "extra_receipt", "unknown_disposition", "unknown_report"])
def test_bad_submission_rejected_without_writes(ready, fault):
    s = ready["submission"]
    if fault == "packet": s["packet_sha256"] = "0" * 64
    elif fault == "actor": s["actor"] = {"identity": "Other", "session_id": "other"}
    elif fault == "accounting": s["content"]["evidence_accounting"].pop()
    elif fault == "outside": s["reading"]["inspected_evidence_ids"] = [*s["reading"]["inspected_evidence_ids"], "SYN3:E1"]
    elif fault == "pending_claim":
        s["reading"]["inspected_evidence_ids"] = [rid for rid in s["reading"]["inspected_evidence_ids"] if rid != "SYN1:E1"]
        s["reading"]["pending_evidence_ids"] = ["SYN1:E1"]
    elif fault == "basis_anchor": s["hypothesis_anchors"][0]["source_anchors"].pop(0)
    elif fault == "quote": s["hypothesis_anchors"][0]["source_anchors"][0]["text"] = "Invented"
    elif fault == "time": s["hypothesis_anchors"][0]["source_anchors"][0]["start"] = 99
    elif fault == "supplementary_support":
        s["hypothesis_anchors"][0]["source_anchors"][0].update(ready["packet"]["raw_regions"]["SYN1"][2])
    elif fault == "fake_model": s["model_metadata"] = {**s["model_metadata"], "usage": {"tokens": 0}}
    elif fault == "extra_receipt": s["completed_call"] = True
    elif fault == "unknown_report": s["hypothesis_anchors"][0]["report_refs"] = [ready["config_ref"]]
    else:
        s["inherited_dispositions"] = [{"obligation_key": "invented", "disposition": "retained", "reason": "Fictional",
            "source_anchors": claim_anchors(ready, ["SYN1:E1"]), "report_refs": [ready["universe"]["reports"][0]["ref"]]}]
    ref = submit(ready)
    before = snapshots(ready["root"])
    with pytest.raises(ValueError): consumer.apply(ref, ready["output"])
    assert snapshots(ready["root"]) == before


def test_all_pending_zero_claims_is_not_global_completion(ready):
    s = ready["submission"]
    s["content"] = synthesis_proposal([r["item"] for r in ready["packet"]["observations"].values()], [])
    s["hypothesis_anchors"] = []
    s["reading"]["pending_evidence_ids"] = s["reading"]["inspected_evidence_ids"]
    s["reading"]["inspected_evidence_ids"] = []
    result = consumer.preview(submit(ready), ready["output"])
    assert not result["policy"]["global_completion"] and len(result["grounding"]["retained_evidence_ids"]) == 6


@pytest.mark.parametrize("limit", ["max_packet_bytes", "max_submission_bytes", "max_candidate_bytes"])
def test_configured_limits_fail_without_truncation_or_output(ready, limit):
    config = copy.deepcopy(ready["config"])
    config["limits"][limit] = 1
    ref = save(ready["root"] / "small-config.json", config)
    if limit == "max_packet_bytes":
        with pytest.raises(ValueError, match="no truncation"): consumer.prepare(ref)
    else:
        packet = consumer.prepare(ref)
        ready["submission"].update(config=ref, packet_sha256=packet["packet_sha256"])
        with pytest.raises(ValueError, match="no truncation"): consumer.apply(submit(ready), ready["output"])
    assert not ready["output"].exists()


def test_expanding_claim_cannot_skip_new_incident_context(ready):
    config = copy.deepcopy(ready["config"])
    config["workset_evidence_ids"] = ["SYN2:E2"]
    ref = save(ready["root"] / "narrow-config.json", config)
    packet = consumer.prepare(ref)
    ready["packet"] = packet["packet"]
    s = ready["submission"]
    s.update(config=ref, packet_sha256=packet["packet_sha256"])
    s["content"] = synthesis_proposal([r["item"] for r in ready["packet"]["observations"].values()], ["SYN1:E3"])
    s["hypothesis_anchors"][0]["source_anchors"] = claim_anchors(ready, ["SYN1:E1", "SYN1:E3"])
    s["reading"]["inspected_evidence_ids"] = ready["packet"]["scope"]["required_evidence_ids"]
    with pytest.raises(ValueError, match="expand incident context"):
        consumer.preview(submit(ready), ready["output"])


def test_rehashed_candidate_tamper_and_output_collisions(ready):
    ref = consumer.apply(submit(ready), ready["output"])
    with pytest.raises(FileExistsError): consumer.apply(submit(ready), ready["output"])
    with pytest.raises(ValueError, match="protected"):
        consumer.preview(submit(ready), ready["universe"]["root"] / "SYN1/forbidden.json")
    value = read_json(ready["output"])
    value["inherited_obligations"] = []
    changed = save(ready["output"], value)
    with pytest.raises(ValueError, match="deterministic replay"): consumer.load_checked(changed)
    with pytest.raises(ValueError): consumer.load_checked(ref)


def test_wrong_selection_old_contract_and_public_paths_rejected(ready):
    config = copy.deepcopy(ready["config"])
    config["selection"] = ready["config_ref"]
    with pytest.raises(ValueError): consumer.prepare(save(ready["root"] / "wrong-selection.json", config))
    with pytest.raises(ValueError): consumer.load_checked(ready["universe"]["old"])
    with pytest.raises(ValueError): old.load_checked(consumer.apply(submit(ready), ready["output"]))
    with pytest.raises(ValueError): consumer.prepare(pin(PROJECT_ROOT / "speaker_profile/native_synthesis.py"))
    with pytest.raises(ValueError): consumer.preview(submit(ready), PROJECT_ROOT / "forbidden-candidate.json")


def test_report_pin_change_is_not_accepted(ready):
    config = copy.deepcopy(ready["config"])
    report = ready["root"] / "report.md"
    report.write_text("Fictional original report")
    config["reports"] = [{**config["reports"][0], "ref": pin(report)}]
    ref = save(ready["root"] / "report-config.json", config)
    report.write_text("Changed report")
    with pytest.raises(ValueError): consumer.prepare(ref)


def test_prior_context_and_recovered_caveats_keep_both_sides_and_bases():
    h = {"hypothesis_id": "PROFILE:H1", "supporting_evidence": ["X:I"], "counterevidence": ["X:C"],
         "statement_action_relationships": [], "recovered_comparisons": [
             {"evidence_ids": ["X:C", "X:R"], "preserved_limitation": "Unresolved historical comparison"}]}
    evidence_rows = {rid: {"basis_evidence_ids": ["X:B"] if rid == "X:I" else []}
                     for rid in ["X:I", "X:B", "X:C", "X:R", "X:K"]}
    closure = {"hypotheses": {"prior-key": h}, "obligations": [{"kind": "prior_context", "key": "context-key",
        "prior_hypothesis": h, "context": {"required_evidence_ids": ["X:K"], "disposition": "pending"}}]}
    rows = consumer._obligations({"evidence": evidence_rows}, closure)
    assert rows[0]["required_evidence_ids"] == ["X:B", "X:C", "X:I", "X:R"]
    assert rows[1]["required_evidence_ids"] == ["X:B", "X:C", "X:I", "X:K", "X:R"]
    assert rows[1]["original"]["context"]["disposition"] == "pending"
    assert rows[0]["original"]["recovered_comparisons"] == h["recovered_comparisons"]


def test_schema_and_cli_default_prepare_is_readonly(ready, capsys):
    assert consumer.main(["schema", "--kind", "submission"]) == 0
    assert json.loads(capsys.readouterr().out)["properties"]["schema_version"]["enum"] == [consumer.SUBMISSION_VERSION]
    before = snapshots(ready["root"])
    assert consumer.main(["--input", ready["config_ref"]["path"], "--sha256", ready["config_ref"]["sha256"]]) == 0
    assert json.loads(capsys.readouterr().out)["packet"]["schema_version"] == consumer.PACKET_VERSION
    assert snapshots(ready["root"]) == before
