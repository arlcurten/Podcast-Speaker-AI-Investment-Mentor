"""Explicit synthesis over completed and recovered synthetic evidence only."""
import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import pipeline, recover_evidence, schemas, serialization
from transcription.common import atomic_write_json, read_json, sha256_file
from transcription.prepare import prepare
from .test_pipeline import SyntheticModel, cfg, root
from .test_recover_evidence import ref
from .test_recovery import BadLinkModel, CompactModel, failed_refs


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No provider access"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


@pytest.fixture
def mixed(cfg, tmp_path):
    cfg["episodes"] = cfg["episodes"][:2]
    class PartialModel(SyntheticModel):
        def complete(self, *args):
            content, usage = super().complete(*args)
            if content["episode_id"] == "SYN2":
                content["items"][0]["supplementary_notes"][0]["merged_ids"] = [2]
            return content, usage
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(cfg, through="evidence", backend=PartialModel())
    old_root = root(cfg)
    recover = {"schema_version": recover_evidence.CONFIG_VERSION, "commentary_scope": schemas.EPISODE_CONTEXT,
               "manifest": ref(old_root / "run_manifest.json"),
               "request": ref(old_root / "calls/SYN2_evidence_1.request.json"),
               "result": ref(old_root / "calls/SYN2_evidence_1.result.json")}
    candidate = recover_evidence.apply(recover, tmp_path / "recovered" / "candidate.json")
    combined = copy.deepcopy(cfg)
    combined.update(run_id="combined_synthesis", commentary_scope=schemas.EPISODE_CONTEXT,
                    context_format=serialization.FORMAT, max_calls=1,
                    reuse_evidence={"manifest": recover["manifest"],
                                    "artifacts": {"SYN1": ref(old_root / "SYN1/evidence.json")}},
                    reuse_recovered_evidence={"SYN2": candidate})
    return cfg, combined


def test_mixed_plan_synthesis_and_resume_preserve_both_original_artifact_types(mixed, tmp_path):
    original, config = mixed
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    recovered_ref = config["reuse_recovered_evidence"]["SYN2"]
    recovered = recover_evidence.load_checked(recovered_ref)
    completed = read_json(Path(config["reuse_evidence"]["artifacts"]["SYN1"]["path"]))
    plan = pipeline.run(config, dry_run=True)
    assert plan["model_calls"] == 0 and plan["estimated_new_calls"] == 1
    assert [s["action"] for s in plan["artifacts"]] == ["referenced", "referenced_recovered", "planned"]
    assert plan["artifacts"][-1]["input_fits"]
    assert pipeline.run(config, through="evidence", validate_only=True)["estimated_new_calls"] == 0
    assert not root(config).exists()
    model = CompactModel()
    result = pipeline.run(config, backend=model)
    assert result["model_calls"] == 1 and model.calls == ["synthesis"]
    assert [s["action"] for s in result["artifacts"]] == ["referenced", "referenced_recovered", "created"]
    assert not list(root(config).glob("*/evidence.json"))
    assert len(list((root(config) / "calls").glob("*.request.json"))) == 1
    assert {p: p.read_bytes() for p in before} == before
    artifact = read_json(root(config) / "synthesis.json")
    manifest = read_json(root(config) / "run_manifest.json")
    request = read_json(root(config) / "calls/cross_episode_synthesis_1.request.json")
    assert artifact["reused_evidence"] == config["reuse_evidence"]
    assert artifact["reused_recovered_evidence"] == config["reuse_recovered_evidence"]
    assert manifest["config"]["reuse_recovered_evidence"] == config["reuse_recovered_evidence"]
    assert manifest["implementation"]["recovered_evidence_checker_sha256"] == sha256_file(Path(recover_evidence.__file__))
    assert artifact["fingerprint"] == request["fingerprint"] == pipeline.digest({
        "run": manifest, "stage": "synthesis", "source": artifact["source"],
        "upstream": [pipeline.digest(completed), pipeline.digest(recovered)],
        "schema": schemas.schema("synthesis"), "prompt": request["prompt"]})
    context = serialization.expand(request["context"])
    assert {row["evidence_id"] for row in context["evidence"]} == {
        row["evidence_id"] for a in (completed, recovered) for row in a["content"]["items"]}
    note_item = next(row for row in context["evidence"] if row["evidence_id"] == "SYN2:E1")
    assert note_item["merged_ids"] == [0]
    assert note_item["grounding"]["supplementary_context"][0]["grounding"]["merged_ids"] == [2]
    assert note_item["supplementary_notes"] == recovered["content"]["items"][0]["supplementary_notes"]
    assert all(s["source"]["source_acceptance"] == "not_granted" for s in context["sources"])
    assert artifact["policy"] == pipeline.POLICY
    assert pipeline.run(config, validate_only=True)["model_calls"] == 0
    assert pipeline.run(config)["model_calls"] == 0
    assert recover_evidence.load_checked(recovered_ref) == recovered
    assert read_json(root(original) / "calls/SYN2_evidence_1.result.json")["status"] == "failed"


def test_recovered_only_selection_makes_no_new_evidence_calls(mixed):
    _, config = mixed
    config.pop("reuse_evidence")
    config["episodes"] = config["episodes"][1:]
    plan = pipeline.run(config, through="evidence", dry_run=True)
    assert plan["estimated_new_calls"] == 0
    assert [row["action"] for row in plan["artifacts"]] == ["referenced_recovered"]
    model = CompactModel()
    assert pipeline.run(config, backend=model)["model_calls"] == 1
    assert model.calls == ["synthesis"]
    artifact = read_json(root(config) / "synthesis.json")
    assert "reused_evidence" not in artifact
    assert artifact["reused_recovered_evidence"] == config["reuse_recovered_evidence"]
    assert pipeline.run(config, validate_only=True)["model_calls"] == 0


@pytest.mark.parametrize("fault", ["duplicate", "missing_episode", "missing_hash", "hash", "empty", "not_mapping",
    "unknown_episode", "wrong_schema", "no_scope", "wrong_scope", "public_path", "relative_path", "inside_output",
    "same_run", "nested_run", "reserved", "catalog", "malformed_ref", "missing_file"])
def test_bad_recovered_reuse_fails_before_backend_or_output(mixed, fault):
    original, config = mixed
    refs = config["reuse_recovered_evidence"]
    candidate = refs["SYN2"]
    if fault == "duplicate": config["reuse_evidence"]["artifacts"]["SYN2"] = candidate
    elif fault == "missing_episode": config.pop("reuse_evidence")
    elif fault == "missing_hash": candidate.pop("sha256")
    elif fault == "hash": candidate["sha256"] = "a" * 64
    elif fault == "empty": config["reuse_recovered_evidence"] = {}
    elif fault == "not_mapping": config["reuse_recovered_evidence"] = []
    elif fault == "unknown_episode": refs["OTHER"] = refs.pop("SYN2")
    elif fault == "wrong_schema": refs["SYN2"] = config["reuse_evidence"]["artifacts"]["SYN1"]
    elif fault == "no_scope": config.pop("commentary_scope")
    elif fault == "wrong_scope": config["commentary_scope"] = "item_only.v1"
    elif fault == "public_path": candidate["path"] = pipeline.__file__
    elif fault == "relative_path": candidate["path"] = "relative.json"
    elif fault == "inside_output": candidate["path"] = str(root(config) / "candidate.json")
    elif fault == "same_run": config["run_id"] = original["run_id"]
    elif fault == "nested_run": config["output_root"] = str(root(original))
    elif fault == "reserved": config["reserved_episode_ids"] = []
    elif fault == "catalog": config["catalog"] = ref(Path(candidate["path"]))
    elif fault == "malformed_ref": refs["SYN2"] = "not a pinned reference"
    else: candidate["path"] = str(Path(candidate["path"]).with_name("missing.json"))
    backend = Mock()
    before = {p: p.read_bytes() for p in root(original).rglob("*") if p.is_file()}
    with pytest.raises((ValueError, FileNotFoundError)):
        pipeline.run(config, backend=backend)
    backend.complete.assert_not_called()
    assert {p: p.read_bytes() for p in before} == before
    assert not (root(config) / "synthesis.json").exists()


def test_rebound_valid_but_different_source_cannot_claim_recovered_evidence(mixed, tmp_path):
    _, config = mixed
    entry = config["episodes"][1]
    raw = read_json(Path(entry["raw"]))
    raw["metadata"]["model"] = "different synthetic source version"
    raw_path, draft_path = tmp_path / "changed-raw.json", tmp_path / "changed-draft.json"
    atomic_write_json(raw_path, raw)
    prepare(raw_path, draft_path)
    entry.update(raw=str(raw_path), raw_sha256=sha256_file(raw_path), draft=str(draft_path), draft_sha256=sha256_file(draft_path))
    backend = Mock()
    with pytest.raises(ValueError, match="Recovered evidence selection/source pins"):
        pipeline.run(config, backend=backend)
    backend.complete.assert_not_called()


def test_episode_key_cannot_relabel_a_recovered_artifact(mixed):
    _, config = mixed
    config.pop("reuse_evidence")
    config["episodes"] = config["episodes"][:1]
    config["reuse_recovered_evidence"]["SYN1"] = config["reuse_recovered_evidence"].pop("SYN2")
    backend = Mock()
    with pytest.raises(ValueError, match="Recovered evidence selection/source pins"):
        pipeline.run(config, backend=backend)
    backend.complete.assert_not_called()


@pytest.mark.parametrize("target", ["candidate", "failed_call"])
def test_recovered_provenance_changed_during_synthesis_is_not_published(mixed, target):
    original, config = mixed
    path = (Path(config["reuse_recovered_evidence"]["SYN2"]["path"]) if target == "candidate"
            else root(original) / "calls/SYN2_evidence_1.result.json")
    class MutatingModel(CompactModel):
        def complete(self, *args):
            result = super().complete(*args)
            value = read_json(path)
            value["synthetic_mutation"] = True
            atomic_write_json(path, value)
            return result
    model = MutatingModel()
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(config, backend=model)
    assert model.calls == ["synthesis"]
    assert not (root(config) / "synthesis.json").exists()
    result = read_json(root(config) / "calls/cross_episode_synthesis_1.result.json")
    assert result["error"]["phase"] == "provenance"


def test_changed_recovery_reference_cannot_resume_existing_synthesis(mixed, tmp_path):
    _, config = mixed
    pipeline.run(config, backend=CompactModel())
    candidate = config["reuse_recovered_evidence"]["SYN2"]
    copied = tmp_path / "explicit-other-copy.json"
    atomic_write_json(copied, read_json(Path(candidate["path"])))
    config["reuse_recovered_evidence"]["SYN2"] = ref(copied)
    backend = Mock()
    with pytest.raises(ValueError, match="configuration/implementation changed"):
        pipeline.run(config, backend=backend)
    backend.complete.assert_not_called()


def test_missing_persisted_recovered_lineage_is_rejected(mixed):
    _, config = mixed
    pipeline.run(config, backend=CompactModel())
    path = root(config) / "synthesis.json"
    artifact = read_json(path)
    artifact.pop("reused_recovered_evidence")
    atomic_write_json(path, artifact)
    with pytest.raises(ValueError, match="recovered-evidence lineage"):
        pipeline.run(config, validate_only=True)


@pytest.mark.parametrize("only_recovered", [False, True])
def test_failed_synthesis_repair_pins_both_reuse_paths(mixed, only_recovered):
    _, failed = mixed
    if only_recovered:
        failed.pop("reuse_evidence")
        failed["episodes"] = failed["episodes"][1:]
    class InvalidSynthesis(CompactModel):
        def complete(self, *args):
            content, usage = super().complete(*args)
            content["hypotheses"][0]["supporting_evidence"] = ["MISSING:E1"]
            return content, usage
    model = InvalidSynthesis()
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(failed, backend=model)
    assert model.calls == ["synthesis"]
    repair = copy.deepcopy(failed)
    repair.update(run_id="explicit_synthesis_repair", reuse_failed_synthesis=failed_refs(failed))
    plan = pipeline.run(repair, dry_run=True)
    assert plan["estimated_new_calls"] == 1 and plan["artifacts"][-1]["seeded_validation_repair"]
    next_model = CompactModel()
    assert pipeline.run(repair, backend=next_model)["model_calls"] == 1
    assert next_model.calls == ["synthesis"]
    artifact = read_json(root(repair) / "synthesis.json")
    assert artifact["reused_recovered_evidence"] == failed["reuse_recovered_evidence"]
    assert artifact["reused_failed_synthesis"] == repair["reuse_failed_synthesis"]
    assert pipeline.run(repair, validate_only=True)["model_calls"] == 0


def test_failed_synthesis_cannot_swap_in_a_different_recovered_reference(mixed, tmp_path):
    _, failed = mixed
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(failed, backend=BadLinkModel())
    repair = copy.deepcopy(failed)
    repair.update(run_id="changed_recovery_repair", reuse_failed_synthesis=failed_refs(failed))
    old = Path(repair["reuse_recovered_evidence"]["SYN2"]["path"])
    copied = tmp_path / "same-content-different-reference.json"
    atomic_write_json(copied, read_json(old))
    repair["reuse_recovered_evidence"]["SYN2"] = ref(copied)
    backend = Mock()
    with pytest.raises(ValueError, match="evidence lineage differs"):
        pipeline.run(repair, backend=backend)
    backend.complete.assert_not_called()
