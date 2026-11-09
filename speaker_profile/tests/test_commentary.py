"""Synthetic episode-context commentary; no providers or private corpus inputs."""
import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import batch, pipeline, schemas, serialization
from transcription.common import read_json
from .test_pipeline import SyntheticModel, cfg, mapped, root, synthesis_proposal
from .test_recovery import recovery_config


SCOPE = schemas.EPISODE_CONTEXT


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No provider access"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr(batch, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)


def test_disjoint_context_is_separate_and_does_not_change_accounting(mapped):
    source, content, original = mapped
    original = copy.deepcopy(original)
    for row in content["items"]:
        row["supplementary_notes"][0]["merged_ids"] = [2]
    before = copy.deepcopy(content)
    grounding = pipeline._derive_evidence(content, source, SCOPE)
    assert content == before
    assert grounding["commentary_scope"] == SCOPE
    assert grounding["unselected_regions"] == original["grounding"]["unselected_regions"]
    for row in content["items"]:
        primary = copy.deepcopy(grounding["items"][row["evidence_id"]])
        notes = primary.pop("supplementary_context")
        assert primary == original["grounding"]["items"][row["evidence_id"]]
        assert notes == [{"grounding": {**pipeline.evidence([2], source), "source_issues": []},
                          "note": row["supplementary_notes"][0]["note"], "authority": "model_analyst_not_speaker"}]
        assert row["quotes"] == next(r for r in original["content"]["items"] if r["evidence_id"] == row["evidence_id"])["quotes"]
    artifact = {**original, "content": content, "grounding": grounding}
    proposal = synthesis_proposal(content["items"], [r["evidence_id"] for r in content["items"]])
    assert pipeline._derive_synthesis(proposal, [artifact]) == pipeline._derive_synthesis(proposal, [original])
    content["unselected_regions"] = []
    with pytest.raises(ValueError, match="Every source region"):
        pipeline._derive_evidence(content, source, SCOPE)


@pytest.mark.parametrize("ids", [[], [999], [0, 999], [2, 2], [0, 0], [-1], [True], ["2"]])
def test_invalid_note_ids_fail_independently(mapped, ids):
    source, content, _ = mapped
    content["items"][0]["supplementary_notes"][0]["merged_ids"] = ids
    with pytest.raises(ValueError):
        pipeline._derive_evidence(content, source, SCOPE)


def test_context_ids_resolve_only_in_the_items_episode(mapped):
    source, content, _ = mapped
    content["items"][0]["supplementary_notes"][0]["merged_ids"] = [2]
    other = copy.deepcopy(source)
    other["episode_id"] = "OTHER"
    source["segments"] = [s for s in source["segments"] if s["merged_id"] != 2]
    content["unselected_regions"] = []
    assert pipeline.evidence([2], other)["episode_id"] == "OTHER"
    with pytest.raises(ValueError, match="Unresolved/duplicate"):
        pipeline._derive_evidence(content, source, SCOPE)


@pytest.mark.parametrize("fault", ["uncovered_anchor", "missing_basis", "note_as_basis"])
def test_context_notes_cannot_supply_inference_basis(mapped, fault):
    source, content, _ = mapped
    content["items"][0]["supplementary_notes"][0]["merged_ids"] = [2]
    inferred = content["items"][2]
    inferred["supplementary_notes"][0]["merged_ids"] = [2]
    if fault == "uncovered_anchor":
        inferred["merged_ids"] = [2]
        inferred["quotes"] = [{k: source["raw_segments"][2][k] for k in ("segment_id", "start", "end", "text")}]
    elif fault == "missing_basis":
        inferred["basis_evidence_ids"] = []
    else:
        inferred["basis_evidence_ids"] = ["SYN1:E1:supplementary_context:0"]
    with pytest.raises(ValueError, match="Inferred"):
        pipeline._derive_evidence(content, source, SCOPE)


def test_context_warnings_and_annotations_survive_synthesis_and_compaction(mapped):
    source, content, _ = mapped
    raw = source["raw_segments"][2]
    source["annotations_sha256"] = "b" * 64
    source["reviewed_annotations"] = {"sha256": "b" * 64, "annotations": [
        {"annotation_id": "synthetic:I2", "kind": "uncertainty", "severity": "critical",
         "note": "Synthetic context ambiguity remains unresolved", "anchors": [
             {"segment_id": 2, "raw_text": raw["text"], "start": raw["start"], "end": raw["end"]}]}]}
    content["items"][0]["supplementary_notes"][0]["merged_ids"] = [2]
    grounding = pipeline._derive_evidence(content, source, SCOPE)
    primary = grounding["items"]["SYN1:E1"]
    assert primary["source_issues"] == []
    note_grounding = primary["supplementary_context"][0]["grounding"]
    assert note_grounding["reviewed_annotations"] == source["reviewed_annotations"]
    assert note_grounding["source_issues"][0]["severity"] == "critical"
    artifact = {"source": pipeline._meta(source), "content": content, "grounding": grounding}
    before = copy.deepcopy(artifact)
    context = pipeline._context("synthesis", None, [artifact])
    assert artifact == before
    refs = context["evidence"][0]["grounding"]["supplementary_context"][0]["grounding"]
    assert refs["source_issue_ids"] == refs["reviewed_annotation_ids"] == ["synthetic:I2"]
    assert "source_issues" not in refs and "reviewed_annotations" not in refs
    assert context["sources"][0]["source"]["reviewed_annotations"] == source["reviewed_annotations"]
    assert serialization.expand(serialization.compact(context)) == context
    assert context["policy"] == pipeline.POLICY
    assert context["sources"][0]["source"]["source_acceptance"] == "not_granted"


def test_default_config_prompt_context_and_grounding_remain_v1(cfg, mapped):
    source, content, original = mapped
    assert pipeline._config(cfg) == cfg and "commentary_scope" not in cfg
    assert pipeline._derive_evidence(content, source) == original["grounding"]
    assert set(original["grounding"]) == {"items", "unselected_regions", "scope"}
    for row in content["items"]:
        assert original["grounding"]["items"][row["evidence_id"]] == {
            **pipeline.evidence(row["merged_ids"], source), "interpretation_authority": "model_analyst_not_speaker",
            "supplementary_notes_authority": "model_analyst_not_speaker", "source_issues": []}
    assert set(pipeline._context("evidence", source, [])) == {"source", "merged_segments", "raw_segments", "source_issues"}
    assert all(schemas.prompt(stage) == text for stage, text in schemas.PROMPTS.items())
    content["items"][0]["supplementary_notes"][0]["merged_ids"] = [2]
    with pytest.raises(ValueError, match="within its item's anchors"):
        pipeline._derive_evidence(content, source)


@pytest.mark.parametrize("scope", [None, "item_only.v1", "episode_context.v2", True, {}])
def test_unknown_explicit_scope_rejected(cfg, scope):
    cfg["commentary_scope"] = scope
    with pytest.raises(ValueError, match="commentary scope"):
        pipeline._config(cfg)


def test_mode_is_persisted_fingerprinted_and_checked_on_resume(cfg):
    cfg["episodes"] = cfg["episodes"][:1]
    cfg["commentary_scope"] = SCOPE
    pipeline.run(cfg, backend=SyntheticModel())
    manifest = read_json(root(cfg) / "run_manifest.json")
    request = read_json(root(cfg) / "calls/SYN1_evidence_1.request.json")
    artifact = read_json(root(cfg) / "SYN1/evidence.json")
    assert manifest["config"]["commentary_scope"] == request["context"]["commentary_scope"] == SCOPE
    assert request["prompt"] == schemas.prompt("evidence", SCOPE) != schemas.PROMPTS["evidence"]
    expected = {"run": manifest, "stage": "evidence", "source": artifact["source"], "upstream": [],
                "schema": schemas.schema("evidence"), "prompt": request["prompt"]}
    assert artifact["fingerprint"] == request["fingerprint"] == pipeline.digest(expected)
    assert artifact["grounding"]["commentary_scope"] == SCOPE and artifact["policy"] == pipeline.POLICY
    before = {p: p.read_bytes() for p in root(cfg).rglob("*") if p.is_file()}
    assert pipeline.run(cfg, validate_only=True)["model_calls"] == pipeline.run(cfg)["model_calls"] == 0
    changed = copy.deepcopy(cfg)
    changed.pop("commentary_scope")
    with pytest.raises(ValueError, match="new run_id"):
        pipeline.run(changed, dry_run=True)
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("original_scope", [None, SCOPE])
def test_evidence_reuse_preserves_original_scope_not_new_scope(cfg, original_scope):
    cfg["episodes"] = cfg["episodes"][:1]
    if original_scope:
        cfg["commentary_scope"] = original_scope
    pipeline.run(cfg, through="evidence", backend=SyntheticModel())
    before = {p: p.read_bytes() for p in root(cfg).rglob("*") if p.is_file()}
    recovery = recovery_config(cfg)
    if original_scope:
        recovery.pop("commentary_scope")
    else:
        recovery["commentary_scope"] = SCOPE
    assert pipeline.run(recovery, through="evidence", validate_only=True)["model_calls"] == 0
    sources = pipeline._sources(recovery)
    reused = pipeline._reused_evidence(pipeline._config(recovery), sources, {})
    assert reused["SYN1"] == read_json(root(cfg) / "SYN1/evidence.json")
    assert reused["SYN1"]["grounding"].get("commentary_scope") == original_scope
    assert {p: p.read_bytes() for p in before} == before


def test_batch_propagates_mode_to_immutable_episode_config_and_prompt(cfg):
    settings = {k: v for k, v in cfg.items() if k in batch.SOURCE_OPTIONS}
    settings.update(schema_version="profile_batch.v1", batch_id="synthetic_context_batch",
                    output_root=cfg["output_root"], commentary_scope=SCOPE, episodes=cfg["episodes"][:1])
    settings = batch._config(settings)
    result = batch.execute(settings, command="run", backend_factory=lambda *a, **k: SyntheticModel())
    assert result["counts"] == {"completed_candidate": 1}
    episode_cfg = read_json(Path(result["episodes"][0]["config"]["path"]))
    assert episode_cfg["commentary_scope"] == SCOPE
    request = read_json(root(episode_cfg) / "calls/SYN1_evidence_1.request.json")
    assert request["context"]["commentary_scope"] == SCOPE
    assert request["prompt"] == schemas.prompt("evidence", SCOPE)
    assert batch.execute(settings, command="validate")["model_calls"] == 0
    changed = copy.deepcopy(settings)
    changed.pop("commentary_scope")
    with pytest.raises(batch.IncompatibleBatch, match="new batch_id"):
        batch.execute(changed)
