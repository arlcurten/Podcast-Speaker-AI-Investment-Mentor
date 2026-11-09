"""Editorial replay over fictional sources and synthetic provider records only."""
import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import pipeline, review
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_pipeline import cfg, hypothesis, root, synthesis_proposal
from .test_recovery import CompactModel, recovery_config


class ParentModel(CompactModel):
    def complete(self, *args):
        content, usage = super().complete(*args)
        if self.calls[-1] == "synthesis":
            content = synthesis_proposal(self.contexts[-1]["evidence"], ["SYN1:E1", "SYN1:E2"])
            first = content["hypotheses"][0]
            first.update(supporting_evidence=["SYN1:E1"], counterevidence=["SYN1:E2"],
                         statement_action_relationships=[{"statement_id": "SYN1:E1", "action_id": "SYN1:E2",
                             "relationship": "agreement", "reason": "Synthetic overstated relationship"}])
            for field in review.ARRAY_FIELDS:
                first[field] = ["Synthetic context boundary"]
            second = hypothesis(["SYN1:E3"])
            second["hypothesis_id"] = "PROFILE:H2"
            content["hypotheses"].append(second)
            content["evidence_accounting"][2].update(disposition="in_hypotheses", hypothesis_ids=["PROFILE:H2"])
        return content, usage


def pin(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def make_parent(cfg, tmp_path, reuse=False):
    cfg["episodes"] = cfg["episodes"][:1]
    cfg["max_calls"] = 2
    entry = cfg["episodes"][0]
    held = tmp_path / "held-review.json"
    atomic_write_json(held, {"schema_version": "transcript_review.v1", "episode_id": "SYN1",
        "transcript_sha256": entry["raw_sha256"], "decision": "uncertain", "reviewer": "Synthetic reviewer",
        "reviewed_at": "2026-01-01T00:00:00Z", "method": "Synthetic text-only review", "sampled_segment_ids": [0],
        "unresolved_critical_issues": ["Synthetic source uncertainty remains unresolved"]})
    entry.update(source_review=str(held), source_review_sha256=sha256_file(held))
    if reuse:
        pipeline.run(cfg, through="evidence", backend=ParentModel())
        cfg = recovery_config(cfg)
    pipeline.run(cfg, backend=ParentModel())
    config_path = tmp_path / "editorial-parent-config.json"
    atomic_write_json(config_path, cfg)
    parent_path = root(cfg) / "synthesis.json"
    parent = read_json(parent_path)
    raw = read_json(Path(cfg["episodes"][0]["raw"]))["segments"]
    anchors = [{"episode_id": "SYN1", **{k: row[k] for k in ("segment_id", "start", "end", "text")}} for row in raw]
    proposal = {"schema_version": "profile_synthesis_revision.v1", "parent_config": pin(config_path),
                "parent_artifact": pin(parent_path), "reviewer": "Synthetic editorial reviewer",
                "method": "Compare wording with the exact fictional source segments",
                "rationale": "Narrow the interpretation without changing evidence or approvals",
                "edits": [{"hypothesis_id": "PROFILE:H1", "field": "/conditional_description",
                           "expected": parent["content"]["hypotheses"][0]["conditional_description"],
                           "value": "In this fictional statement, time with friends is valued over the cheaper option.",
                           "reason": "Limit the wording to the quoted preference; do not infer a general rule.",
                           "evidence_ids": ["SYN1:E1"], "source_anchors": [anchors[0]]}]}
    proposal_path, output = tmp_path / "proposal.json", tmp_path / "reviewed.json"
    atomic_write_json(proposal_path, proposal)
    return {"cfg": cfg, "parent": parent, "proposal": proposal, "path": proposal_path,
            "output": output, "anchors": anchors}


@pytest.fixture
def ready(cfg, tmp_path, monkeypatch):
    data = make_parent(cfg, tmp_path)
    forbidden = Mock(side_effect=AssertionError("Editorial review must never call a backend"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr(pipeline.codex_backend.CodexBackend, "complete", forbidden)
    data["forbidden"] = forbidden
    return data


def apply_data(data):
    atomic_write_json(data["path"], data["proposal"])
    return review.apply(data["path"], data["output"])


def test_valid_revision_preserves_originals_holds_policy_and_accounting(ready, tmp_path, monkeypatch):
    originals = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    run = Mock(wraps=pipeline.run)
    monkeypatch.setattr(pipeline, "run", run)
    result = review.apply(ready["path"], ready["output"])
    assert review.validate(ready["path"], ready["output"]) == result
    assert all(c.kwargs == {"through": "synthesis", "validate_only": True} for c in run.call_args_list)
    assert run.call_count == 4
    assert {p: p.read_bytes() for p in originals} == originals
    assert {p for p in tmp_path.rglob("*") if p.is_file()} - set(originals) == {ready["output"]}
    assert result["schema_version"] == "profile_reviewed_revision.v1"
    assert result["authority"] == "agent_editorial_revision_not_source_evidence"
    assert result["additional_model_calls"] == 0 and result["model_usage"] is None
    assert not {"request_sha256", "result_sha256", "fingerprint", "model_metadata"} & result.keys()
    assert result["proposal"] == pin(ready["path"])
    assert result["implementation"]["code"]["profile_review"] == sha256_file(Path(review.__file__))
    assert result["policy"] == ready["parent"]["policy"] == pipeline.POLICY
    assert result["source"] == ready["parent"]["source"]
    assert result["source"]["episodes"][0]["source_acceptance"] == "not_granted"
    assert result["source"]["episodes"][0]["prior_review_limitations"] == ["Synthetic source uncertainty remains unresolved"]
    expected = copy.deepcopy(ready["parent"]["content"])
    expected["hypotheses"][0]["conditional_description"] = ready["proposal"]["edits"][0]["value"]
    assert result["content"] == expected
    evidence = [read_json(Path(ref["path"])) for ref in result["evidence"].values()]
    assert result["grounding"] == pipeline._derive("synthesis", expected, None, evidence)
    assert result["editorial_review"]["edits"][0]["source_anchors"] == [ready["anchors"][0]]
    ready["forbidden"].assert_not_called()
    before = ready["output"].read_bytes()
    with pytest.raises(FileExistsError):
        review.apply(ready["path"], ready["output"])
    assert ready["output"].read_bytes() == before


def test_reused_evidence_is_read_from_original_run(cfg, tmp_path, monkeypatch):
    data = make_parent(cfg, tmp_path, reuse=True)
    forbidden = Mock(side_effect=AssertionError("No inference"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = review.apply(data["path"], data["output"])
    assert result["evidence"] == data["cfg"]["reuse_evidence"]["artifacts"]
    assert not list(root(data["cfg"]).glob("*/evidence.json"))
    assert review.validate(data["path"], data["output"]) == result
    assert {p: p.read_bytes() for p in before} == before
    forbidden.assert_not_called()


@pytest.mark.parametrize("field", ["/label", "/conditional_description",
    *[f"/{name}" for name in sorted(review.ARRAY_FIELDS)],
    *[f"/{name}/0" for name in sorted(review.ARRAY_FIELDS)],
    "/statement_action_relationships/0/reason", "/statement_action_relationships/0/relationship"])
def test_every_allowed_field(ready, field):
    edit = ready["proposal"]["edits"][0]
    h = ready["parent"]["content"]["hypotheses"][0]
    container, key, _ = review._target(h, field)
    value = (["Only this fictional context"] if isinstance(container[key], list) else
             "unresolved" if field.endswith("/relationship") else "Limited to this fictional context")
    edit.update(field=field, expected=copy.deepcopy(container[key]), value=value)
    result = apply_data(ready)
    revised, key, _ = review._target(result["content"]["hypotheses"][0], field)
    assert revised[key] == value
    assert review.validate(ready["path"], ready["output"]) == result


@pytest.mark.parametrize("field", ["/hypothesis_id", "/supporting_evidence", "/supporting_evidence/0",
    "/counterevidence", "/hypothesis_type", "/proposed_confidence/evidence_fidelity/value",
    "/proposed_confidence", "/proposed_salience/value", "/domains", "/categories", "/evidence_accounting",
    "/policy", "/source", "/statement_action_relationships", "/statement_action_relationships/0/statement_id",
    "/statement_action_relationships/0/action_id", "/label/0", "/limitations/-", "/limitations/-1",
    "/limitations/01", "/limitations/99", "/statement_action_relationships/99/reason", "label", "", "/"])
def test_noneditorial_paths_are_forbidden(ready, field):
    ready["proposal"]["edits"][0]["field"] = field
    with pytest.raises(ValueError):
        apply_data(ready)
    assert not ready["output"].exists()
    ready["forbidden"].assert_not_called()


@pytest.mark.parametrize("fault", ["old_value", "missing_expected", "unknown_hypothesis", "wrong_hypothesis",
    "unreferenced_evidence", "missing_anchor", "uncovered_evidence", "quote", "start", "end", "segment",
    "episode", "outside_region", "duplicate_anchor", "duplicate_evidence", "metadata", "invalid_value",
    "relationship_upgrade", "duplicate_path", "overlap", "reverse_overlap", "extra_proposal", "nan"])
def test_invalid_proposals_fail_closed(ready, fault):
    proposal = ready["proposal"]
    edit = proposal["edits"][0]
    anchor = edit["source_anchors"][0]
    if fault == "old_value": edit["expected"] = "Not the parent wording"
    elif fault == "missing_expected": edit.pop("expected")
    elif fault == "unknown_hypothesis": edit["hypothesis_id"] = "PROFILE:H99"
    elif fault == "wrong_hypothesis":
        edit.update(hypothesis_id="PROFILE:H2", evidence_ids=["SYN1:E2"], source_anchors=[ready["anchors"][1]])
    elif fault == "unreferenced_evidence": edit["evidence_ids"] = ["SYN1:E3"]
    elif fault == "missing_anchor": edit["source_anchors"] = []
    elif fault == "uncovered_evidence": edit["evidence_ids"].append("SYN1:E2")
    elif fault == "quote": anchor["text"] += " Invented quotation"
    elif fault in {"start", "end"}: anchor[fault] += 1
    elif fault == "segment": anchor["segment_id"] = 999
    elif fault == "episode": anchor["episode_id"] = "SYN2"
    elif fault == "outside_region": edit["source_anchors"] = [ready["anchors"][1]]
    elif fault == "duplicate_anchor": edit["source_anchors"] *= 2
    elif fault == "duplicate_evidence": edit["evidence_ids"] *= 2
    elif fault == "metadata": anchor["speaker"] = "Invented metadata"
    elif fault == "invalid_value": edit["value"] = {"invented": "structure"}
    elif fault == "relationship_upgrade":
        edit.update(field="/statement_action_relationships/0/relationship", expected="agreement", value="apparent_mismatch")
    elif fault == "duplicate_path": proposal["edits"].append(copy.deepcopy(edit))
    elif fault in {"overlap", "reverse_overlap"}:
        edit.update(field="/limitations", expected=["Synthetic context boundary"], value=["Narrower statement"])
        second = {**copy.deepcopy(edit), "field": "/limitations/0", "expected": "Synthetic context boundary", "value": "Other"}
        proposal["edits"] = [edit, second] if fault == "overlap" else [second, edit]
    elif fault == "extra_proposal": proposal["policy"] = {"eligible_for_serving": True}
    else:
        # A hand-written JSON input may contain the nonstandard NaN token.
        ready["path"].write_text(ready["path"].read_text().replace('"expected": ', '"unexpected": NaN, "expected": ', 1))
        with pytest.raises(ValueError):
            review.apply(ready["path"], ready["output"])
        return
    with pytest.raises(ValueError):
        apply_data(ready)
    assert not ready["output"].exists()
    ready["forbidden"].assert_not_called()


def test_multiple_disjoint_edits_and_counterevidence(ready):
    edit = copy.deepcopy(ready["proposal"]["edits"][0])
    edit.update(field="/statement_action_relationships/0/relationship", expected="agreement", value="unresolved",
                evidence_ids=["SYN1:E1", "SYN1:E2"], source_anchors=ready["anchors"][:2])
    ready["proposal"]["edits"].append(edit)
    result = apply_data(ready)
    assert result["content"]["hypotheses"][0]["statement_action_relationships"][0]["relationship"] == "unresolved"
    assert result["content"]["evidence_accounting"] == ready["parent"]["content"]["evidence_accounting"]


@pytest.mark.parametrize("fault", ["config_hash", "artifact_hash", "config_bytes", "artifact_bytes",
    "wrong_artifact_path", "missing_evidence", "source_bytes", "request_bytes", "public_config", "public_parent",
    "public_proposal", "public_output", "symlink_public_output", "parent_output", "incomplete_parent"])
def test_parent_pins_validation_and_private_boundaries(ready, tmp_path, fault):
    proposal = ready["proposal"]
    parent_root = root(ready["cfg"])
    public = Path(review.__file__).resolve()
    if fault in {"config_hash", "artifact_hash"}:
        proposal["parent_config" if fault == "config_hash" else "parent_artifact"]["sha256"] = "0" * 64
    elif fault in {"config_bytes", "artifact_bytes", "source_bytes", "request_bytes"}:
        path = (Path(proposal["parent_config"]["path"]) if fault == "config_bytes" else
                Path(proposal["parent_artifact"]["path"]) if fault == "artifact_bytes" else
                Path(ready["cfg"]["episodes"][0]["raw"]) if fault == "source_bytes" else
                parent_root / "calls/SYN1_evidence_1.request.json")
        value = read_json(path)
        value["tampered"] = True
        atomic_write_json(path, value)
    elif fault == "wrong_artifact_path":
        other = tmp_path / "copied-synthesis.json"
        atomic_write_json(other, ready["parent"])
        proposal["parent_artifact"] = pin(other)
    elif fault == "missing_evidence": (parent_root / "SYN1/evidence.json").unlink()
    elif fault == "incomplete_parent": (parent_root / "calls/cross_episode_synthesis_1.result.json").unlink()
    elif fault in {"public_config", "public_parent"}:
        proposal["parent_config" if fault == "public_config" else "parent_artifact"] = pin(public)
    elif fault == "public_proposal":
        with pytest.raises(ValueError): review.apply(public, ready["output"])
        return
    elif fault == "public_output": ready["output"] = public.parent / "never-write-editorial.json"
    elif fault == "symlink_public_output":
        link = tmp_path / "public-link"
        link.symlink_to(public.parent, target_is_directory=True)
        ready["output"] = link / "never-write-editorial.json"
    else: ready["output"] = parent_root / "editorial.json"
    with pytest.raises((ValueError, OSError)):
        apply_data(ready)
    assert not ready["output"].exists()
    ready["forbidden"].assert_not_called()


@pytest.mark.parametrize("fault", ["content", "grounding", "policy", "source", "authority", "usage", "calls",
    "code_pin", "parent_pin", "proposal_pin", "extra_field", "proposal_bytes", "proposal_path", "output_path",
    "parent_config_bytes", "parent_artifact_bytes", "evidence_bytes", "code_changed"])
def test_validation_detects_mutations_and_path_changes(ready, tmp_path, monkeypatch, fault):
    result = review.apply(ready["path"], ready["output"])
    if fault == "content": result["content"]["hypotheses"][0]["label"] = "Forged label"
    elif fault == "grounding": result["grounding"]["hypotheses"]["PROFILE:H1"]["independent_event_count"] = 10
    elif fault == "policy": result["policy"]["eligible_for_serving"] = True
    elif fault == "source": result["source"]["episodes"][0]["source_acceptance"] = "granted"
    elif fault == "authority": result["authority"] = "source_evidence"
    elif fault == "usage": result["model_usage"] = {"input_tokens": 100}
    elif fault == "calls": result["additional_model_calls"] = False  # bool != exact integer zero
    elif fault == "code_pin": result["implementation"]["code"]["profile_review"] = "0" * 64
    elif fault == "parent_pin": result["parent_artifact"]["sha256"] = "0" * 64
    elif fault == "proposal_pin": result["proposal"]["sha256"] = "0" * 64
    elif fault == "extra_field": result["request_sha256"] = "0" * 64
    elif fault == "proposal_bytes":
        ready["proposal"]["rationale"] += " Changed"
        atomic_write_json(ready["path"], ready["proposal"])
    elif fault == "proposal_path":
        ready["path"] = tmp_path / "copied-proposal.json"
        atomic_write_json(ready["path"], ready["proposal"])
    elif fault == "output_path": ready["output"] = tmp_path / "relocated-output.json"
    elif fault.endswith("_bytes"):
        path = Path(result["evidence"]["SYN1"]["path"] if fault == "evidence_bytes" else
                    result[fault.removesuffix("_bytes")]["path"])
        value = read_json(path)
        value["tampered"] = True
        atomic_write_json(path, value)
    else:
        implementation = review._implementation()
        implementation["code"]["profile_review"] = "0" * 64
        monkeypatch.setattr(review, "_implementation", lambda: implementation)
    atomic_write_json(ready["output"], result)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        review.validate(ready["path"], ready["output"])
    assert {p: p.read_bytes() for p in before} == before
    ready["forbidden"].assert_not_called()


def test_input_mutation_during_reconstruction_blocks_output(ready, monkeypatch):
    derive = pipeline._derive
    def mutate(*args):
        result = derive(*args)
        ready["proposal"]["rationale"] += " Changed concurrently"
        atomic_write_json(ready["path"], ready["proposal"])
        return result
    monkeypatch.setattr(pipeline, "_derive", mutate)
    with pytest.raises(ValueError, match="hash or path changed"):
        review.apply(ready["path"], ready["output"])
    assert not ready["output"].exists()


def test_cli_is_read_only_on_validation_and_does_not_print_private_content(ready, capsys):
    args = ["--proposal", str(ready["path"]), "--output", str(ready["output"])]
    assert review.main(["apply", *args]) == 0
    assert '"additional_model_calls": 0' in capsys.readouterr().out
    assert review.main(["validate", *args]) == 0
    stdout = capsys.readouterr().out
    assert '"validated"' in stdout and str(ready["path"]) not in stdout
    assert review.main(["apply", *args]) == 2
    stdout = capsys.readouterr().out
    assert "Fictional" not in stdout and str(ready["path"]) not in stdout
    ready["forbidden"].assert_not_called()
