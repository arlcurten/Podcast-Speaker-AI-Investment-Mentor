"""Synthetic anchored editorial recovery; original validation and policy stay strict."""
import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import pipeline, recover_synthesis as recovery
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_pipeline import SyntheticModel, cfg, item, root, synthesis_proposal
from .test_recover_evidence import ref
from .test_recovered_synthesis import mixed
from .test_recovery import CompactModel


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No provider access"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


class InvalidComparisons(SyntheticModel):
    def complete(self, prompt, context, output_schema):
        content, usage = super().complete(prompt, context, output_schema)
        if self.calls[-1] == "evidence":
            source = {**context["source"], "segments": context["merged_segments"],
                      "raw_segments": {s["segment_id"]: s for s in context["raw_segments"]}}
            content["items"].append(item(source, 4))
        else:
            content = synthesis_proposal(context["evidence"], [r["evidence_id"] for r in context["evidence"]])
            content["hypotheses"][0]["statement_action_relationships"] = [
                {"statement_id": "SYN1:E1", "action_id": "SYN1:E2", "relationship": "agreement", "reason": "Valid synthetic pair"},
                {"statement_id": "SYN1:E2", "action_id": "SYN1:E2", "relationship": "unresolved", "reason": "Same reported action is not a separate statement."},
                {"statement_id": "SYN1:E1", "action_id": "SYN1:E4", "relationship": "unresolved", "reason": "Two statements do not establish completed action."}]
        return content, usage


@pytest.fixture
def failed(cfg, tmp_path):
    cfg["episodes"] = cfg["episodes"][:1]
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(cfg, backend=InvalidComparisons())
    parent = root(cfg)
    response = read_json(parent / "calls/cross_episode_synthesis_1.result.json")["response"]
    sources = pipeline._sources(cfg)
    source = sources["SYN1"]
    proposal = {"schema_version": "profile_synthesis_recovery.v1", "manifest": ref(parent / "run_manifest.json"),
                "request": ref(parent / "calls/cross_episode_synthesis_1.request.json"),
                "result": ref(parent / "calls/cross_episode_synthesis_1.result.json"),
                "reviewer": "Synthetic text reviewer", "method": "Synthetic exact-source reading; no audio review",
                "reviewed_at": "2026-01-01T00:00:00Z", "rationale": "Preserve comparisons without misstating evidence kinds.",
                "corrections": []}
    for index, sid in ((1, 1), (2, 0)):
        link = response["hypotheses"][0]["statement_action_relationships"][index]
        raw = source["raw_segments"][sid]
        proposal["corrections"].append({"hypothesis_id": "PROFILE:H1", "relationship_index": index,
            "expected": link, "reason": "Synthetic contextual comparison, not independent action evidence.",
            "evidence_ids": sorted({link["statement_id"], link["action_id"]}),
            "source_anchors": [{"episode_id": "SYN1", **{k: raw[k] for k in ("segment_id", "start", "end", "text")}}]})
    path = tmp_path / "proposal.json"
    atomic_write_json(path, proposal)
    return cfg, path, tmp_path / "editorial" / "candidate.json"


def test_replay_preserves_everything_except_two_typed_links_and_appended_limits(failed, tmp_path):
    cfg, proposal, output = failed
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    planned = recovery.prepare(proposal, output)
    assert not output.exists()
    artifact_ref = recovery.apply(proposal, output)
    artifact = recovery.load_checked(artifact_ref)
    original = read_json(root(cfg) / "calls/cross_episode_synthesis_1.result.json")
    expected = copy.deepcopy(original["response"])
    expected["hypotheses"][0]["statement_action_relationships"] = expected["hypotheses"][0]["statement_action_relationships"][:1]
    expected["hypotheses"][0]["limitations"].extend(row["preserved_limitation"] for row in artifact["comparison_ledger"])
    assert artifact["content"] == expected == planned["content"]
    assert artifact["content"]["evidence_accounting"] == original["response"]["evidence_accounting"]
    assert artifact["policy"] == pipeline.POLICY and artifact["status"] == "editorial_candidate"
    assert artifact["authority"] == recovery.AUTHORITY
    assert artifact["additional_model_calls"] == 0 and artifact["model_usage"] is None
    assert artifact["original_model_usage"] == original["usage"] and artifact["original_error"] == original["error"]
    assert artifact["original_fingerprint"] == original["fingerprint"] != artifact["fingerprint"]
    assert len(artifact["grounding"]["retained_evidence_ids"]) == 4
    assert artifact["grounding"]["hypotheses"]["PROFILE:H1"]["independent_event_count"] is None
    for row in artifact["comparison_ledger"]:
        assert row["expected"]["reason"] in row["preserved_limitation"]
    assert {p: p.read_bytes() for p in before} == before
    assert not (root(cfg) / "synthesis.json").exists()
    with pytest.raises(FileExistsError):
        recovery.apply(proposal, output)
    assert recovery.load_checked(artifact_ref) == artifact


@pytest.mark.parametrize("fault", ["missing", "duplicate", "valid_pair", "changed_expected", "foreign_id",
    "anchor_text", "anchor_time", "anchor_episode", "anchor_region", "no_anchor", "new_id",
    "extra_edit", "missing_reviewer", "blank_reason", "bad_time", "wrong_schema"])
def test_invalid_corrections_fail_before_output(failed, fault):
    _, path, output = failed
    value = read_json(path); edit = value["corrections"][0]
    if fault == "missing": value["corrections"].pop()
    elif fault == "duplicate": value["corrections"].append(copy.deepcopy(edit))
    elif fault == "valid_pair": edit["relationship_index"] = 0
    elif fault == "changed_expected": edit["expected"]["reason"] = "Changed"
    elif fault == "foreign_id": edit["hypothesis_id"] = "PROFILE:H99"
    elif fault == "anchor_text": edit["source_anchors"][0]["text"] = "Invented quote"
    elif fault == "anchor_time": edit["source_anchors"][0]["start"] += 1
    elif fault == "anchor_episode": edit["source_anchors"][0]["episode_id"] = "OTHER"
    elif fault == "anchor_region": edit["source_anchors"][0]["segment_id"] = 2
    elif fault == "no_anchor": edit["source_anchors"] = []
    elif fault == "new_id": edit["evidence_ids"].append("SYN1:E999")
    elif fault == "extra_edit": edit["new_content"] = {"summary": "Unauthorized prose"}
    elif fault == "missing_reviewer": value["reviewer"] = ""
    elif fault == "blank_reason": edit["reason"] = "  "
    elif fault == "bad_time": value["reviewed_at"] = "yesterday"
    else: value["schema_version"] = "profile.v1"
    atomic_write_json(path, value)
    with pytest.raises(ValueError): recovery.apply(path, output)
    assert not output.exists()


@pytest.mark.parametrize("target", ["manifest", "request", "result"])
def test_original_call_pin_tampering_rejected(failed, target):
    _, path, output = failed
    value = read_json(path)
    original = Path(value[target]["path"])
    data = read_json(original); data["synthetic_mutation"] = True; atomic_write_json(original, data)
    with pytest.raises(ValueError): recovery.apply(path, output)
    assert not output.exists()


@pytest.mark.parametrize("field", ["content", "accounting", "policy", "ledger", "fingerprint", "implementation"])
def test_rebound_output_pin_does_not_hide_editorial_tampering(failed, field):
    _, proposal, output = failed
    recovery.apply(proposal, output)
    artifact = read_json(output)
    if field == "content": artifact["content"]["hypotheses"][0]["conditional_description"] = "Invented certainty"
    elif field == "accounting": artifact["content"]["evidence_accounting"].pop()
    elif field == "policy": artifact["policy"]["eligible_for_serving"] = True
    elif field == "ledger": artifact["comparison_ledger"][0]["expected"]["reason"] = "Erased comparison"
    elif field == "fingerprint": artifact["fingerprint"] = "a" * 64
    else: artifact.pop("implementation")
    atomic_write_json(output, artifact)
    with pytest.raises(ValueError): recovery.load_checked(ref(output))


def test_source_race_is_detected_before_writing(failed, monkeypatch):
    cfg, proposal, output = failed
    raw = Path(cfg["episodes"][0]["raw"])
    original = recovery.review._anchors
    def racing(*args):
        result = original(*args)
        value = read_json(raw); value["segments"][0]["text"] += " changed"; atomic_write_json(raw, value)
        return result
    monkeypatch.setattr(recovery.review, "_anchors", racing)
    with pytest.raises(ValueError): recovery.apply(proposal, output)
    assert not output.exists()


def test_compatible_checker_update_does_not_relabel_historical_recovery(failed, monkeypatch):
    _, proposal, output = failed
    artifact_ref = recovery.apply(proposal, output)
    before = recovery.load_checked(artifact_ref)
    changed = copy.deepcopy(recovery._implementation()); changed["recovery_sha256"] = "c" * 64
    monkeypatch.setattr(recovery, "_implementation", lambda: changed)
    assert recovery.load_checked(artifact_ref) == before


@pytest.mark.parametrize("only_recovered", [False, True])
def test_failed_synthesis_over_recovered_inputs_preserves_distinct_lineage(mixed, tmp_path, only_recovered):
    _, cfg = mixed
    if only_recovered:
        cfg.pop("reuse_evidence")
        cfg["episodes"] = cfg["episodes"][1:]
    class InvalidRecovered(CompactModel):
        def complete(self, *args):
            _, usage = super().complete(*args)
            items = self.contexts[-1]["evidence"]
            response = synthesis_proposal(items, [r["evidence_id"] for r in items])
            action = next(r for r in items if r["evidence_kind"] == "reported_action")
            response["hypotheses"][0]["statement_action_relationships"] = [{
                "statement_id": action["evidence_id"], "action_id": action["evidence_id"],
                "relationship": "unresolved", "reason": "Synthetic action is not a separate statement."}]
            return response, usage
    model = InvalidRecovered()
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(cfg, backend=model)
    assert model.calls == ["synthesis"]
    parent = root(cfg)
    failed = read_json(parent / "calls/cross_episode_synthesis_1.result.json")
    link = failed["response"]["hypotheses"][0]["statement_action_relationships"][0]
    eid = link["statement_id"].split(":")[0]
    raw = pipeline._sources(cfg)[eid]["raw_segments"][1]
    proposal = {"schema_version": "profile_synthesis_recovery.v1", "manifest": ref(parent / "run_manifest.json"),
        "request": ref(parent / "calls/cross_episode_synthesis_1.request.json"),
        "result": ref(parent / "calls/cross_episode_synthesis_1.result.json"),
        "reviewer": "Synthetic reviewer", "method": "Exact synthetic text", "reviewed_at": "2026-01-01T00:00:00Z",
        "rationale": "Retain comparison, without creating another event or changing its kind.", "corrections": [{
            "hypothesis_id": "PROFILE:H1", "relationship_index": 0, "expected": link,
            "reason": "One action remains one source observation.", "evidence_ids": [link["statement_id"]],
            "source_anchors": [{"episode_id": eid, **{k: raw[k] for k in ("segment_id", "start", "end", "text")}}]}]}
    path, output = tmp_path / "recovered-input-proposal.json", tmp_path / "reviewed-candidate.json"
    atomic_write_json(path, proposal)
    artifact_ref = recovery.apply(path, output)
    artifact = recovery.load_checked(artifact_ref)
    assert artifact["evidence"]["SYN2"] == cfg["reuse_recovered_evidence"]["SYN2"]
    assert artifact["content"]["evidence_accounting"] == failed["response"]["evidence_accounting"]
    assert artifact["additional_model_calls"] == 0 and not (parent / "synthesis.json").exists()


def test_private_boundary_original_output_and_safe_cli(failed, capsys):
    cfg, proposal, output = failed
    with pytest.raises(ValueError, match="outside"):
        recovery.apply(proposal, root(cfg) / "recovered.json")
    with pytest.raises(ValueError, match="public repository"):
        recovery.apply(proposal, Path(recovery.__file__).parent / "invalid.json")
    assert recovery.main(["apply", "--proposal", str(proposal), "--output", str(output)]) == 0
    assert recovery.main(["validate", "--artifact", str(output), "--sha256", sha256_file(output)]) == 0
    assert recovery.main(["validate", "--artifact", str(output), "--sha256", "a" * 64]) == 2
    logs = capsys.readouterr().out
    assert str(proposal.parent) not in logs and "Fictional host" not in logs
    assert '"additional_model_calls": 0' in logs
