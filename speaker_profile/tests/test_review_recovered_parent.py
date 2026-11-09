"""Fictional source-only revisions; no provider access or real private artifacts."""
import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import pipeline, recover_synthesis, review, review_recovered as revision
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_pipeline import cfg, root, synthesis_proposal
from .test_recover_evidence import ref
from .test_recover_synthesis import failed, no_provider
from .test_review import make_parent
from .test_recovered_synthesis import mixed
from .test_recovery import CompactModel


@pytest.fixture
def ready(failed, tmp_path):
    config, recovery_proposal, recovered_path = failed
    parent_ref = recover_synthesis.apply(recovery_proposal, recovered_path)
    parent = recover_synthesis.load_checked(parent_ref)
    raw = pipeline._sources(config)["SYN1"]["raw_segments"][0]
    proposal = {"schema_version": revision.PROPOSAL_VERSION, "parent_artifact": parent_ref,
                "reviewer": "Synthetic editorial reviewer", "method": "Fictional exact-text comparison, no audio",
                "rationale": "Narrow prose without changing evidence, recovery or approval status.",
                "edits": [{"hypothesis_id": "PROFILE:H1", "field": "/conditional_description",
                    "expected": parent["content"]["hypotheses"][0]["conditional_description"],
                    "value": "Only this fictional stated preference is supported; general behavior is unknown.",
                    "reason": "Keep the interpretation within the fictional statement.", "evidence_ids": ["SYN1:E1"],
                    "source_anchors": [{"episode_id": "SYN1", **{k: raw[k] for k in ("segment_id", "start", "end", "text")}}]}]}
    path, output = tmp_path / "revision-proposal.json", tmp_path / "revision.json"
    atomic_write_json(path, proposal)
    return {"cfg": config, "parent": parent, "proposal": proposal, "path": path, "output": output}


def apply_data(data):
    atomic_write_json(data["path"], data["proposal"])
    return revision.apply(data["path"], data["output"])


def test_revision_exact_changes_original_failure_and_ledger_preserved(ready, tmp_path, monkeypatch):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    forbidden = Mock(side_effect=AssertionError("Revision must not run the extraction pipeline"))
    monkeypatch.setattr(pipeline, "run", forbidden)
    planned = revision.prepare(ready["path"], ready["output"])
    assert not ready["output"].exists()
    artifact_ref = revision.apply(ready["path"], ready["output"])
    result = revision.load_checked(artifact_ref)
    parent = ready["parent"]
    expected = copy.deepcopy(parent["content"])
    expected["hypotheses"][0]["conditional_description"] = ready["proposal"]["edits"][0]["value"]
    assert result["content"] == expected == planned["content"]
    for key in ("parent", "original_fingerprint", "original_error", "original_model_usage",
                "comparison_ledger", "source", "evidence", "policy"):
        assert result[key] == parent[key]
    assert result["recovery_editorial_review"] == parent["editorial_review"]
    assert result["parent_artifact"] == ready["proposal"]["parent_artifact"]
    assert result["schema_version"] == revision.VERSION and result["status"] == "editorial_candidate"
    assert result["authority"] == review.AUTHORITY
    assert result["additional_model_calls"] == 0 and result["model_usage"] is None
    assert result["policy"] == pipeline.POLICY
    assert result["grounding"] == pipeline._derive_synthesis(expected, [read_json(Path(r["path"])) for r in parent["evidence"].values()])
    assert {p: p.read_bytes() for p in before} == before
    assert {p for p in tmp_path.rglob("*") if p.is_file()} - set(before) == {ready["output"]}
    assert not (root(ready["cfg"]) / "synthesis.json").exists()
    with pytest.raises(FileExistsError):
        revision.apply(ready["path"], ready["output"])
    assert revision.load_checked(artifact_ref) == result
    forbidden.assert_not_called()


@pytest.mark.parametrize("fault", ["wrong_schema", "legacy_parent_config", "missing_expected", "wrong_expected",
    "unknown_hypothesis", "evidence_ref", "quote", "time", "segment", "missing_anchor", "new_support",
    "new_counter", "accounting", "new_ledger", "relationship_upgrade", "duplicate_path", "overlap",
    "blank_reviewer", "blank_reason", "invalid_type"])
def test_invalid_edits_fail_without_output(ready, fault):
    proposal = ready["proposal"]
    edit = proposal["edits"][0]
    if fault == "wrong_schema": proposal["schema_version"] = "profile_synthesis_revision.v1"
    elif fault == "legacy_parent_config": proposal["parent_config"] = proposal["parent_artifact"]
    elif fault == "missing_expected": edit.pop("expected")
    elif fault == "wrong_expected": edit["expected"] = "Not the original"
    elif fault == "unknown_hypothesis": edit["hypothesis_id"] = "PROFILE:H99"
    elif fault == "evidence_ref": edit["evidence_ids"] = ["SYN1:E999"]
    elif fault == "quote": edit["source_anchors"][0]["text"] += " invented"
    elif fault == "time": edit["source_anchors"][0]["start"] += 1
    elif fault == "segment": edit["source_anchors"][0]["segment_id"] = 2
    elif fault == "missing_anchor": edit["source_anchors"] = []
    elif fault in {"new_support", "new_counter", "accounting", "new_ledger"}:
        edit["field"] = {"new_support": "/supporting_evidence", "new_counter": "/counterevidence",
                         "accounting": "/evidence_accounting", "new_ledger": "/comparison_ledger"}[fault]
    elif fault == "relationship_upgrade":
        edit.update(field="/statement_action_relationships/0/relationship", expected="agreement", value="apparent_mismatch")
    elif fault == "duplicate_path": proposal["edits"].append(copy.deepcopy(edit))
    elif fault == "overlap":
        values = ready["parent"]["content"]["hypotheses"][0]["limitations"]
        edit.update(field="/limitations", expected=values, value=values)
        proposal["edits"].append({**copy.deepcopy(edit), "field": "/limitations/0", "expected": values[0], "value": "New"})
    elif fault == "blank_reviewer": proposal["reviewer"] = " "
    elif fault == "blank_reason": edit["reason"] = " "
    else: edit["value"] = {"not": "prose"}
    with pytest.raises(ValueError): apply_data(ready)
    assert not ready["output"].exists()


@pytest.mark.parametrize("fault", ["index", "replace_list", "remove", "move", "duplicate"])
def test_recovery_caveats_cannot_be_changed_through_any_list_edit(ready, fault):
    values = copy.deepcopy(ready["parent"]["content"]["hypotheses"][0]["limitations"])
    limitation = ready["parent"]["comparison_ledger"][0]["preserved_limitation"]
    index = values.index(limitation)
    edit = ready["proposal"]["edits"][0]
    if fault == "index":
        edit.update(field=f"/limitations/{index}", expected=limitation, value="Erased caveat")
    else:
        revised = copy.deepcopy(values)
        if fault == "replace_list": revised[index] = "Erased caveat"
        elif fault == "remove": revised.pop(index)
        elif fault == "move": revised.insert(0, "Inserted before the preserved caveat")
        else: revised.append(limitation)
        edit.update(field="/limitations", expected=values, value=revised)
    with pytest.raises(ValueError, match="comparison limitations"):
        apply_data(ready)
    assert not ready["output"].exists()


def test_ordinary_limitations_and_relationship_downgrade_remain_editable(ready):
    edit = ready["proposal"]["edits"][0]
    values = copy.deepcopy(ready["parent"]["content"]["hypotheses"][0]["limitations"])
    edit.update(field="/limitations", expected=values, value=[*values, "Additional reviewer uncertainty"])
    ready["proposal"]["edits"].append({**copy.deepcopy(edit),
        "field": "/statement_action_relationships/0/relationship", "expected": "agreement", "value": "unresolved"})
    result = revision.load_checked(apply_data(ready))
    assert result["content"]["hypotheses"][0]["statement_action_relationships"][0]["relationship"] == "unresolved"
    assert result["comparison_ledger"] == ready["parent"]["comparison_ledger"]


@pytest.mark.parametrize("target", ["raw", "manifest", "request", "result", "evidence", "parent"])
def test_current_source_and_original_failed_lineage_replayed(ready, target):
    artifact_ref = revision.apply(ready["path"], ready["output"])
    parent = ready["parent"]
    path = Path(ready["cfg"]["episodes"][0]["raw"] if target == "raw" else
                parent["evidence"]["SYN1"]["path"] if target == "evidence" else
                ready["proposal"]["parent_artifact"]["path"] if target == "parent" else
                parent["parent"][target]["path"])
    value = read_json(path)
    value["synthetic_mutation"] = True
    atomic_write_json(path, value)
    with pytest.raises(ValueError): revision.load_checked(artifact_ref)


@pytest.mark.parametrize("field", ["content", "ledger", "policy", "error", "implementation", "accounting"])
def test_rebound_output_pin_does_not_hide_mutation(ready, field):
    artifact_ref = revision.apply(ready["path"], ready["output"])
    result = revision.load_checked(artifact_ref)
    if field == "content": result["content"]["hypotheses"][0]["conditional_description"] = "Invented certainty"
    elif field == "ledger": result["comparison_ledger"].pop()
    elif field == "policy": result["policy"]["eligible_for_serving"] = True
    elif field == "error": result["original_error"] = None
    elif field == "implementation": result.pop("implementation")
    else: result["content"]["evidence_accounting"].pop()
    atomic_write_json(ready["output"], result)
    with pytest.raises(ValueError): revision.load_checked(ref(ready["output"]))


def test_compatible_checker_update_keeps_lineage_but_uses_current_validation(ready, monkeypatch):
    artifact_ref = revision.apply(ready["path"], ready["output"])
    before = revision.load_checked(artifact_ref)
    changed = copy.deepcopy(revision._implementation())
    changed["revision_sha256"] = "c" * 64
    monkeypatch.setattr(revision, "_implementation", lambda: changed)
    assert revision.load_checked(artifact_ref) == before
    reject = Mock(side_effect=ValueError("Current validator rejects this source"))
    monkeypatch.setattr(recover_synthesis, "load_checked", reject)
    with pytest.raises(ValueError, match="Current validator"):
        revision.load_checked(artifact_ref)
    reject.assert_called_once()


def test_source_race_fails_before_output(ready, monkeypatch):
    original = review._anchors
    path = Path(ready["cfg"]["episodes"][0]["raw"])
    def racing(*args):
        result = original(*args)
        value = read_json(path)
        value["segments"][0]["text"] += " mutation"
        atomic_write_json(path, value)
        return result
    monkeypatch.setattr(review, "_anchors", racing)
    with pytest.raises(ValueError): revision.apply(ready["path"], ready["output"])
    assert not ready["output"].exists()


def test_separate_format_does_not_change_completed_revision_replay(cfg, tmp_path, monkeypatch):
    data = make_parent(cfg, tmp_path)
    old = review.apply(data["path"], data["output"])
    before = review._implementation()
    changed = copy.deepcopy(revision._implementation())
    changed["revision_sha256"] = "c" * 64
    monkeypatch.setattr(revision, "_implementation", lambda: changed)
    assert review._implementation() == before
    assert review.validate(data["path"], data["output"]) == old
    with pytest.raises(ValueError):
        revision.prepare(data["path"], tmp_path / "wrong-format.json")


@pytest.mark.parametrize("only_recovered", [False, True])
def test_recovered_and_mixed_upstream_evidence_remain_distinct(mixed, tmp_path, only_recovered):
    _, config = mixed
    if only_recovered:
        config.pop("reuse_evidence")
        config["episodes"] = config["episodes"][1:]
    class InvalidModel(CompactModel):
        def complete(self, *args):
            _, usage = super().complete(*args)
            items = self.contexts[-1]["evidence"]
            content = synthesis_proposal(items, [item["evidence_id"] for item in items])
            action = next(item for item in items if item["evidence_kind"] == "reported_action")
            content["hypotheses"][0]["statement_action_relationships"] = [{
                "statement_id": action["evidence_id"], "action_id": action["evidence_id"],
                "relationship": "unresolved", "reason": "Synthetic action is not an independent statement."}]
            return content, usage
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(config, backend=InvalidModel())
    run = root(config)
    content = read_json(run / "calls/cross_episode_synthesis_1.result.json")["response"]
    link = content["hypotheses"][0]["statement_action_relationships"][0]
    eid = link["action_id"].split(":")[0]
    raw = pipeline._sources(config)[eid]["raw_segments"][1]
    anchors = [{"episode_id": eid, **{key: raw[key] for key in ("segment_id", "start", "end", "text")}}]
    proposal = {"schema_version": "profile_synthesis_recovery.v1", "manifest": ref(run / "run_manifest.json"),
        "request": ref(run / "calls/cross_episode_synthesis_1.request.json"),
        "result": ref(run / "calls/cross_episode_synthesis_1.result.json"),
        "reviewer": "Synthetic reviewer", "method": "Exact fictional text", "reviewed_at": "2026-01-01T00:00:00Z",
        "rationale": "Preserve the single action without inventing an independent statement.", "corrections": [{
            "hypothesis_id": "PROFILE:H1", "relationship_index": 0, "expected": link,
            "reason": "One action remains one source observation.", "evidence_ids": [link["action_id"]],
            "source_anchors": anchors}]}
    recovery_path = tmp_path / "mixed-recovery-proposal.json"
    atomic_write_json(recovery_path, proposal)
    parent_ref = recover_synthesis.apply(recovery_path, tmp_path / "mixed-recovered.json")
    parent = recover_synthesis.load_checked(parent_ref)
    proposal = {"schema_version": revision.PROPOSAL_VERSION, "parent_artifact": parent_ref,
        "reviewer": "Synthetic reviewer", "method": "Exact fictional text", "rationale": "Narrow only the wording.",
        "edits": [{"hypothesis_id": "PROFILE:H1", "field": "/label", "expected": parent["content"]["hypotheses"][0]["label"],
            "value": "Context-dependent fictional observation", "reason": "Do not infer a universal rule.",
            "evidence_ids": [link["action_id"]], "source_anchors": anchors}]}
    path = tmp_path / "mixed-revision-proposal.json"
    atomic_write_json(path, proposal)
    result = revision.load_checked(revision.apply(path, tmp_path / "mixed-revision.json"))
    assert result["evidence"] == parent["evidence"]
    assert result["evidence"]["SYN2"] == config["reuse_recovered_evidence"]["SYN2"]
    assert result["content"]["evidence_accounting"] == parent["content"]["evidence_accounting"]
    assert result["comparison_ledger"] == parent["comparison_ledger"]
    assert result["policy"] == parent["policy"] == pipeline.POLICY


def test_private_boundaries_failed_root_and_cli(ready, capsys):
    with pytest.raises(ValueError, match="outside"):
        revision.apply(ready["path"], root(ready["cfg"]) / "revision.json")
    with pytest.raises(ValueError, match="public repository"):
        revision.apply(ready["path"], Path(revision.__file__).parent / "invalid.json")
    assert revision.main(["apply", "--proposal", str(ready["path"]), "--output", str(ready["output"])]) == 0
    assert revision.main(["validate", "--artifact", str(ready["output"]), "--sha256", sha256_file(ready["output"])]) == 0
    assert revision.main(["validate", "--artifact", str(ready["output"]), "--sha256", "a" * 64]) == 2
    logs = capsys.readouterr().out
    assert str(ready["path"].parent) not in logs and "Fictional host" not in logs
    assert '"additional_model_calls": 0' in logs
