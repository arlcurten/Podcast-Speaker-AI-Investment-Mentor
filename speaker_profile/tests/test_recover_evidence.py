"""Synthetic failed-evidence recovery; no provider access or corpus fixtures."""
import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import pipeline, recover_evidence as recovery, schemas
from transcription.common import atomic_write_json, read_json, sha256_file
from transcription.tests.test_annotations import annotation_review
from .test_pipeline import SyntheticModel, cfg, root, synthesis_proposal


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No provider access"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


class ContextModel(SyntheticModel):
    def complete(self, *args):
        content, usage = super().complete(*args)
        content["items"][0]["supplementary_notes"][0]["merged_ids"] = [2]
        return content, usage


def ref(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def config_for(cfg, attempt=1):
    run = root(cfg)
    return {"schema_version": recovery.CONFIG_VERSION, "commentary_scope": schemas.EPISODE_CONTEXT,
            "manifest": ref(run / "run_manifest.json"),
            "request": ref(run / "calls" / f"SYN1_evidence_{attempt}.request.json"),
            "result": ref(run / "calls" / f"SYN1_evidence_{attempt}.result.json")}


@pytest.fixture
def failed(cfg):
    cfg["episodes"] = cfg["episodes"][:1]
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(cfg, through="evidence", backend=ContextModel())
    return cfg, config_for(cfg)


def replace(config, kind, mutate):
    path = Path(config[kind]["path"])
    value = read_json(path)
    mutate(value)
    atomic_write_json(path, value)
    config[kind] = ref(path)
    return value


def test_apply_recomputes_without_mutating_content_sources_or_failure(failed, tmp_path):
    cfg, config = failed
    paths = [Path(r["path"]) for k, r in config.items() if isinstance(r, dict)]
    paths += [Path(cfg["episodes"][0][k]) for k in ("raw", "draft")]
    before = {p: p.read_bytes() for p in paths}
    output = tmp_path / "recovered" / "evidence.json"
    artifact_ref = recovery.apply(config, output)
    pins = {}
    artifact = recovery.load_checked(artifact_ref, pins)
    old = read_json(Path(config["result"]["path"]))
    assert artifact["schema_version"] == recovery.VERSION != schemas.VERSION
    assert artifact["status"] == "recovered_candidate" and artifact["recovery"]["model_calls"] == 0
    assert artifact["content"] == old["response"] and artifact["model_metadata"] == old["usage"]
    assert artifact["policy"] == pipeline.POLICY
    assert artifact["source"]["source_acceptance"] == "not_granted"
    assert artifact["original_error"] == old["error"] and old["status"] == "failed"
    assert artifact["original_fingerprint"] == old["fingerprint"] != artifact["fingerprint"]
    first = artifact["content"]["items"][0]
    assert first["merged_ids"] == [0] and first["supplementary_notes"][0]["merged_ids"] == [2]
    ground = artifact["grounding"]["items"]["SYN1:E1"]
    assert ground["merged_ids"] == [0] and ground["supplementary_context"][0]["grounding"]["merged_ids"] == [2]
    assert ground["supplementary_context"][0]["authority"] == "model_analyst_not_speaker"
    proposal = synthesis_proposal(artifact["content"]["items"])
    stats = pipeline._derive_synthesis(proposal, [artifact])["hypotheses"]["PROFILE:H1"]
    assert stats["source_region_count"] == 1 and stats["independent_event_count"] is None
    assert stats["source_group_count"] == 1 and stats["low_frequency_status"] == "single_source_group"
    assert {p: p.read_bytes() for p in before} == before
    assert all(p in pins for p in paths) and output in pins
    assert not (root(cfg) / "SYN1/evidence.json").exists()
    assert len(list((root(cfg) / "calls").glob("*.request.json"))) == 1
    with pytest.raises(FileExistsError):
        recovery.apply(config, output)


def test_prepare_is_read_only_and_requires_explicit_opt_in(failed, tmp_path):
    _, config = failed
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert recovery.prepare(config)["recovery"]["model_calls"] == 0
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    for value in (None, "item_only.v1", "episode_context.v2"):
        bad = {**config, "commentary_scope": value}
        with pytest.raises(ValueError, match="explicit episode-context"):
            recovery.prepare(bad)


@pytest.mark.parametrize("kind", ["manifest", "request", "result"])
def test_missing_or_unpinned_inputs_fail(failed, kind):
    _, config = failed
    path = Path(config[kind]["path"])
    payload = path.read_bytes()
    path.unlink()
    with pytest.raises(FileNotFoundError):
        recovery.prepare(config)
    atomic_write_json(path, {"synthetic": "tampered"})
    with pytest.raises(ValueError, match="Pinned profile provenance changed"):
        recovery.prepare(config)
    assert path.read_bytes() != payload


@pytest.mark.parametrize("kind,fault", [
    ("manifest", "code_missing"), ("manifest", "code_changed"), ("manifest", "config_digest"),
    ("request", "source_context"), ("request", "episode"), ("request", "prompt"),
    ("request", "schema"), ("request", "fingerprint"), ("result", "request_hash"),
    ("result", "status"), ("result", "phase"), ("result", "missing_response"),
    ("result", "false_error"), ("result", "content_episode"), ("result", "unknown_note"),
    ("result", "duplicate_note"), ("result", "quote"), ("result", "eligibility"),
])
def test_repinned_inconsistent_records_still_fail(failed, kind, fault):
    _, config = failed
    def mutate(value):
        if fault == "code_missing": value["implementation"]["code"].pop("profile_pipeline")
        elif fault == "code_changed": value["implementation"]["code"]["profile_pipeline"] = "a" * 64
        elif fault == "config_digest": value["config_digest"] = "a" * 64
        elif fault == "source_context": value["context"]["raw_segments"][0]["text"] = "changed"
        elif fault == "episode": value["episode_id"] = "SYN2"
        elif fault == "prompt": value["prompt"] += " changed"
        elif fault == "schema": value["schema"] = {}
        elif fault == "fingerprint": value["fingerprint"] = "a" * 64
        elif fault == "request_hash": value["request_sha256"] = "a" * 64
        elif fault == "status": value["status"] = "completed"
        elif fault == "phase": value["error"]["phase"] = "input"
        elif fault == "missing_response": value["response"] = None
        elif fault == "false_error": value["error"]["message"] = "Different validation error"
        elif fault == "content_episode": value["response"]["episode_id"] = "SYN2"
        elif fault == "unknown_note": value["response"]["items"][0]["supplementary_notes"][0]["merged_ids"] = [999]
        elif fault == "duplicate_note": value["response"]["items"][0]["supplementary_notes"][0]["merged_ids"] = [2, 2]
        elif fault == "quote": value["response"]["items"][1]["quotes"][0]["text"] = "Invented"
        else: value["response"]["eligible_for_serving"] = True
    replace(config, kind, mutate)
    with pytest.raises(ValueError):
        recovery.prepare(config)


def test_changed_raw_source_pin_fails_before_recovery(failed):
    cfg, config = failed
    path = Path(cfg["episodes"][0]["raw"])
    raw = read_json(path)
    raw["segments"][0]["text"] += " mutated"
    atomic_write_json(path, raw)
    with pytest.raises(ValueError):
        recovery.prepare(config)


@pytest.fixture
def annotated_failure(cfg, tmp_path):
    cfg["episodes"] = cfg["episodes"][:1]
    entry = cfg["episodes"][0]
    review, sidecar, supporting = annotation_review(Path(entry["raw"]), tmp_path / "review")
    value = read_json(review)
    value.update(decision="uncertain", unresolved_critical_issues=["Synthetic unresolved limitation remains"])
    atomic_write_json(review, value)
    entry.update(source_review=str(review), source_review_sha256=sha256_file(review))
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(cfg, through="evidence", backend=ContextModel())
    return cfg, config_for(cfg), (review, sidecar, supporting)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_review_annotation_and_supporting_pins_rechecked(annotated_failure, tmp_path, index):
    _, config, paths = annotated_failure
    artifact_ref = recovery.apply(config, tmp_path / "recovered.json")
    pins = {}
    artifact = recovery.load_checked(artifact_ref, pins)
    assert all(p in pins for p in paths)
    assert artifact["source"]["prior_source_decision"] == "uncertain"
    assert artifact["source"]["prior_review_limitations"] == ["Synthetic unresolved limitation remains"]
    assert artifact["source"]["reviewed_annotations"]["annotations"]
    value = read_json(paths[index])
    value["synthetic_mutation"] = True
    atomic_write_json(paths[index], value)
    with pytest.raises(ValueError):
        recovery.load_checked(artifact_ref)


@pytest.mark.parametrize("index", [1, 2])
def test_transitive_annotation_race_fails_before_write(annotated_failure, tmp_path, monkeypatch, index):
    _, config, paths = annotated_failure
    original = pipeline._derive_evidence
    def racing(content, source, scope=None):
        result = original(content, source, scope)
        if scope == schemas.EPISODE_CONTEXT:
            value = read_json(paths[index])
            value["synthetic_mutation"] = True
            atomic_write_json(paths[index], value)
        return result
    monkeypatch.setattr(pipeline, "_derive_evidence", racing)
    output = tmp_path / "recovered.json"
    with pytest.raises(ValueError, match="changed during validation"):
        recovery.apply(config, output)
    assert not output.exists()


def test_same_named_call_from_another_run_is_not_original_lineage(failed, tmp_path):
    _, config = failed
    other = tmp_path / "other" / "calls" / "SYN1_evidence_1.result.json"
    atomic_write_json(other, read_json(Path(config["result"]["path"])))
    config["result"] = ref(other)
    with pytest.raises(ValueError, match="do not belong"):
        recovery.prepare(config)


@pytest.mark.parametrize("kind", ["manifest", "request", "result"])
def test_malformed_pinned_record_shapes_fail_clearly(failed, kind):
    _, config = failed
    path = Path(config[kind]["path"])
    atomic_write_json(path, [])
    config[kind] = ref(path)
    with pytest.raises(ValueError, match="object-shaped"):
        recovery.prepare(config)


def test_recorded_failure_must_reproduce_not_just_claim_failure(failed):
    _, config = failed
    replace(config, "result", lambda value: value["response"]["items"][0]["supplementary_notes"][0].update(merged_ids=[0]))
    with pytest.raises(ValueError, match="does not reproduce"):
        recovery.prepare(config)


def test_second_failed_attempt_binds_earlier_repair_chain(cfg, tmp_path):
    cfg["episodes"] = cfg["episodes"][:1]
    cfg["retry_count"] = 1
    with pytest.raises(ValueError, match="attempts exhausted"):
        pipeline.run(cfg, through="evidence", backend=ContextModel())
    artifact_ref = recovery.apply(config_for(cfg, 2), tmp_path / "recovery.json")
    artifact = recovery.load_checked(artifact_ref)
    assert len(artifact["previous_calls"]) == 1
    prior_result = Path(artifact["previous_calls"][0]["result"]["path"])
    value = read_json(prior_result)
    value["error"]["message"] = "changed prior diagnostic"
    atomic_write_json(prior_result, value)
    with pytest.raises(ValueError):
        recovery.load_checked(artifact_ref)


def test_newer_success_does_not_erase_or_override_original_failed_choice(cfg, tmp_path):
    cfg["episodes"] = cfg["episodes"][:1]
    cfg["retry_count"] = 1
    class RepairModel(SyntheticModel):
        def complete(self, *args):
            content, usage = super().complete(*args)
            if len(self.calls) == 1:
                content["items"][0]["supplementary_notes"][0]["merged_ids"] = [2]
            return content, usage
    pipeline.run(cfg, through="evidence", backend=RepairModel())
    success = root(cfg) / "SYN1/evidence.json"
    before = success.read_bytes()
    artifact_ref = recovery.apply(config_for(cfg), tmp_path / "recovered.json")
    recovered = recovery.load_checked(artifact_ref)
    assert recovered["content"] != read_json(success)["content"]
    assert success.read_bytes() == before
    assert read_json(Path(config_for(cfg)["result"]["path"]))["status"] == "failed"


@pytest.mark.parametrize("field", ["content", "grounding", "policy", "implementation", "error", "extra", "fingerprint"])
def test_recovered_artifact_rechecked_even_if_repinned(failed, tmp_path, field):
    _, config = failed
    output = tmp_path / "recovered.json"
    recovery.apply(config, output)
    artifact = read_json(output)
    if field == "content": artifact["content"]["summary"] = "Changed interpretation"
    elif field == "grounding": artifact["grounding"]["items"]["SYN1:E1"]["merged_ids"] = [0, 2]
    elif field == "policy": artifact["policy"]["eligible_for_serving"] = True
    elif field == "implementation": artifact["recovery"]["implementation"]["recovery_code_sha256"] = "a" * 64
    elif field == "error": artifact["original_error"]["message"] = "removed original failure"
    elif field == "fingerprint": artifact["fingerprint"] = "a" * 64
    else: artifact["request_sha256"] = config["request"]["sha256"]
    atomic_write_json(output, artifact)
    with pytest.raises(ValueError, match="differs"):
        recovery.load_checked(ref(output))


def test_compatible_checker_code_update_retains_historical_recovery_lineage(failed, tmp_path, monkeypatch):
    _, config = failed
    artifact_ref = recovery.apply(config, tmp_path / "recovered.json")
    before = recovery.load_checked(artifact_ref)
    current = copy.deepcopy(recovery._implementation())
    current["profile"]["code"]["profile_pipeline"] = "b" * 64
    current["recovery_code_sha256"] = "c" * 64
    monkeypatch.setattr(recovery, "_implementation", lambda: current)
    checked = recovery.load_checked(artifact_ref)
    assert checked == before
    assert checked["recovery"]["implementation"] != current
    assert recovery.prepare(config)["recovery"]["implementation"] == current


@pytest.mark.parametrize("field", ["implementation", "profile_pipeline", "recovery_code_sha256"])
def test_missing_historical_recovery_code_lineage_rejected(failed, tmp_path, field):
    _, config = failed
    output = tmp_path / "recovered.json"
    recovery.apply(config, output)
    value = read_json(output)
    if field == "implementation":
        del value["recovery"][field]
    elif field == "profile_pipeline":
        del value["recovery"]["implementation"]["profile"]["code"][field]
    else:
        del value["recovery"]["implementation"][field]
    atomic_write_json(output, value)
    with pytest.raises(ValueError, match="lineage"):
        recovery.load_checked(ref(output))


def test_shared_validation_pins_cannot_be_silently_replaced(failed, tmp_path):
    _, config = failed
    artifact_ref = recovery.apply(config, tmp_path / "recovered.json")
    pins = {Path(config["result"]["path"]): "a" * 64}
    with pytest.raises(ValueError, match="Conflicting"):
        recovery.load_checked(artifact_ref, pins)


def test_source_race_is_caught_before_write(failed, tmp_path, monkeypatch):
    cfg, config = failed
    original = pipeline._derive_evidence
    raw = Path(cfg["episodes"][0]["raw"])
    def racing(content, source, scope=None):
        result = original(content, source, scope)
        if scope == schemas.EPISODE_CONTEXT:
            value = read_json(raw)
            value["segments"][0]["text"] += " changed during validation"
            atomic_write_json(raw, value)
        return result
    monkeypatch.setattr(pipeline, "_derive_evidence", racing)
    output = tmp_path / "recovered.json"
    with pytest.raises(ValueError, match="changed during validation"):
        recovery.apply(config, output)
    assert not output.exists()


def test_private_boundary_and_original_run_are_enforced(failed, tmp_path):
    cfg, config = failed
    with pytest.raises(ValueError, match="distinct output"):
        recovery.apply(config, root(cfg) / "recovered.json")
    with pytest.raises(ValueError, match="public repository"):
        recovery.apply(config, Path(recovery.__file__).parent / "should-not-exist.json")
    with pytest.raises(ValueError, match="absolute"):
        recovery.apply(config, Path("relative.json"))
    bad = copy.deepcopy(config)
    bad["result"]["path"] = recovery.__file__
    with pytest.raises(ValueError, match="public repository"):
        recovery.prepare(bad)


def test_cli_validate_apply_and_readback_do_not_print_source_paths(failed, tmp_path, capsys):
    _, config = failed
    path, output = tmp_path / "recovery-config.json", tmp_path / "recovered.json"
    atomic_write_json(path, config)
    assert recovery.main(["validate", "--config", str(path)]) == 0
    assert not output.exists()
    assert recovery.main(["apply", "--config", str(path), "--output", str(output)]) == 0
    assert recovery.main(["validate", "--artifact", str(output), "--sha256", sha256_file(output)]) == 0
    assert recovery.main(["validate", "--artifact", str(output), "--sha256", "a" * 64]) == 2
    logs = capsys.readouterr().out
    assert str(tmp_path) not in logs and "Fictional host" not in logs
    assert '"model_calls": 0' in logs and '"eligible_for_serving": false' in logs
