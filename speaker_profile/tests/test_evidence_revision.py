"""Fictional, deterministic editorial replay. No providers or corpus inputs."""
import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import evidence_revision as revision, pipeline, recover_evidence
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_pipeline import SyntheticModel, cfg, root
from .test_recover_evidence import ContextModel, annotated_failure, config_for, ref


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No provider access"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield
    forbidden.assert_not_called()


@pytest.fixture(params=["completed", "recovered"])
def parent(cfg, tmp_path, request):
    cfg["episodes"] = cfg["episodes"][:1]
    if request.param == "completed":
        pipeline.run(cfg, through="evidence", backend=SyntheticModel())
        descriptor = {"kind": "completed", "artifact": ref(root(cfg) / "SYN1/evidence.json"),
                      "manifest": ref(root(cfg) / "run_manifest.json")}
    else:
        with pytest.raises(ValueError, match="attempts exhausted"):
            pipeline.run(cfg, through="evidence", backend=ContextModel())
        descriptor = {"kind": "recovered", "artifact": recover_evidence.apply(
            config_for(cfg), tmp_path / "recovered.json")}
    return cfg, descriptor


def proposal(parent, tmp_path):
    cfg, descriptor = parent
    content = read_json(Path(descriptor["artifact"]["path"]))["content"]
    source = pipeline._sources(cfg)["SYN1"]
    row = content["items"][0]
    anchor = {k: source["raw_segments"][2][k] for k in ("segment_id", "start", "end", "text")}
    added = {"merged_ids": [2], "note": "Reviewer: scheduling is unspecified, not a demonstrated outcome."}
    value = {"schema_version": revision.CONFIG_VERSION, "commentary_scope": "episode_context.v1",
             "parent": descriptor, "reviewer": "Synthetic reviewer", "rationale": "Keep source limits local.",
             "edits": [{"evidence_id": row["evidence_id"], "field": "/supplementary_notes",
                        "expected": row["supplementary_notes"], "value": row["supplementary_notes"] + [added],
                        "reason": "Explicit reviewer caution", "source_anchors": [anchor]}]}
    path = tmp_path / "revision-config.json"
    atomic_write_json(path, value)
    return value, path, source


def test_roundtrip_preserves_originals_and_note_authorship(parent, tmp_path):
    cfg, descriptor = parent
    _, path, _ = proposal(parent, tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    output = tmp_path / "revision/evidence.json"
    candidate = revision.prepare(ref(path), output)
    assert not output.exists()
    artifact_ref = revision.apply(ref(path), output)
    pins = {}
    artifact = revision.load_checked(artifact_ref, pins)
    assert artifact == candidate
    assert all(p.read_bytes() == data for p, data in before.items())
    assert artifact["additional_model_calls"] == 0 and artifact["model_usage"] is None
    assert artifact["policy"] == pipeline.POLICY and not artifact["policy"]["eligible_for_serving"]
    assert artifact["source"]["source_acceptance"] == "not_granted"
    assert artifact["accounting"]["before"] == artifact["accounting"]["after"]
    assert artifact["accounting"]["after"]["independent_event_count"] is None
    notes = artifact["grounding"]["items"]["SYN1:E1"]["supplementary_context"]
    assert notes[0]["authority"] == "model_analyst_not_speaker" and "authorship" not in notes[0]
    assert notes[1]["authority"] == revision.NOTE_AUTHORITY
    assert notes[1]["authorship"]["reviewer"] == "Synthetic reviewer"
    assert notes[1]["grounding"]["merged_ids"] == [2]
    assert artifact["editorial_review"]["operations"][0]["path"] == "/content/items/0/supplementary_notes"
    assert artifact["schema_version"] != "profile.v1"
    assert "request_sha256" not in artifact and "model_metadata" not in artifact
    assert path in pins and Path(descriptor["artifact"]["path"]) in pins
    assert Path(cfg["episodes"][0]["raw"]) in pins
    with pytest.raises(FileExistsError):
        revision.apply(ref(path), output)


@pytest.mark.parametrize("fault", ["confidence", "attribution", "new_item", "basis", "policy", "old_value",
                                    "duplicate", "overlap", "unknown_id", "bad_note_id", "duplicate_note_id",
                                    "raw_text", "raw_time", "unrelated_anchor", "drop_model_note", "no_op"])
def test_forbidden_and_ungrounded_edits_fail_before_write(parent, tmp_path, fault):
    value, path, source = proposal(parent, tmp_path)
    edit = value["edits"][0]
    if fault == "confidence": edit.update(field="/proposed_confidence", expected={}, value={})
    elif fault == "attribution": edit.update(field="/speaker_attribution", expected="host", value="guest")
    elif fault == "new_item": edit["field"] = "/items/3"
    elif fault == "basis": edit["field"] = "/basis_evidence_ids"
    elif fault == "policy": value["eligible_for_serving"] = True
    elif fault == "old_value": edit["expected"] = []
    elif fault == "duplicate": value["edits"].append(copy.deepcopy(edit))
    elif fault == "overlap":
        edit.update(field="/uncertainty", expected=["Event and outcome unknown"], value=["Outcome unknown"])
        value["edits"].append({**edit, "field": "/uncertainty/0", "expected": "Outcome unknown", "value": "Unknown"})
    elif fault == "unknown_id": edit["evidence_id"] = "SYN2:E1"
    elif fault == "bad_note_id": edit["value"][-1]["merged_ids"] = [999]
    elif fault == "duplicate_note_id": edit["value"][-1]["merged_ids"] = [2, 2]
    elif fault == "raw_text": edit["source_anchors"][0]["text"] = "Invented"
    elif fault == "raw_time": edit["source_anchors"][0]["start"] += 1
    elif fault == "unrelated_anchor": edit["source_anchors"] = [source["raw_segments"][0]]
    elif fault == "drop_model_note": edit["value"] = edit["value"][1:]
    else: edit["value"] = copy.deepcopy(edit["expected"])
    atomic_write_json(path, value)
    output = tmp_path / "revision.json"
    with pytest.raises(ValueError):
        revision.apply(ref(path), output)
    assert not output.exists()


def test_primary_addition_recomputes_overlap_preserves_quotes_and_inferred_basis(parent, tmp_path):
    value, path, source = proposal(parent, tmp_path)
    old = read_json(Path(value["parent"]["artifact"]["path"]))["content"]["items"][0]
    quote = source["raw_segments"][1]
    value["edits"] = [
        {"evidence_id": "SYN1:E1", "field": "/merged_ids", "expected": [0], "value": [0, 1],
         "reason": "Explicit adjacent source for reviewer wording", "source_anchors": [quote]},
        {"evidence_id": "SYN1:E1", "field": "/quotes", "expected": old["quotes"], "value": old["quotes"] + [quote],
         "reason": "Add exact quotation without altering the original", "source_anchors": [quote]}]
    atomic_write_json(path, value)
    output = tmp_path / "revision.json"
    artifact = revision.prepare(ref(path), output)
    before, after = artifact["accounting"]["before"], artifact["accounting"]["after"]
    assert before["primary_region_count"] == after["primary_region_count"] == 2
    assert len(before["direct_action_overlap_components"]) == 2
    assert after["direct_action_overlap_components"] == [["SYN1:E1", "SYN1:E2"]]
    assert after["item_count"] == 3 and after["direct_action_count"] == 2
    assert artifact["content"]["items"][2]["basis_evidence_ids"] == ["SYN1:E1"]
    value["edits"][1]["value"] = [quote]
    atomic_write_json(path, value)
    with pytest.raises(ValueError, match="Preserve every original quote"):
        revision.prepare(ref(path), output)


def test_note_context_cannot_become_inferred_primary_basis(parent, tmp_path):
    value, path, source = proposal(parent, tmp_path)
    inferred = read_json(Path(value["parent"]["artifact"]["path"]))["content"]["items"][2]
    value["edits"] = [{"evidence_id": "SYN1:E3", "field": "/merged_ids", "expected": [0], "value": [0, 2],
                       "reason": "Invalid use of commentary", "source_anchors": [source["raw_segments"][2]]}]
    assert inferred["basis_evidence_ids"] == ["SYN1:E1"]
    atomic_write_json(path, value)
    with pytest.raises(ValueError, match="basis observations"):
        revision.prepare(ref(path), tmp_path / "revision.json")


@pytest.mark.parametrize("field", ["content", "grounding", "policy", "authorship", "accounting", "fingerprint", "implementation"])
def test_repinned_derivative_tampering_rejected(parent, tmp_path, field):
    _, path, _ = proposal(parent, tmp_path)
    output = tmp_path / "revision.json"
    revision.apply(ref(path), output)
    artifact = read_json(output)
    if field == "content": artifact["content"]["items"][0]["source_observation"] = "Invented"
    elif field == "grounding": artifact["grounding"]["items"]["SYN1:E1"]["merged_ids"] = [0, 2]
    elif field == "policy": artifact["policy"]["eligible_for_serving"] = True
    elif field == "authorship": artifact["grounding"]["items"]["SYN1:E1"]["supplementary_context"][1]["authority"] = "host"
    elif field == "accounting": artifact["accounting"]["after"]["independent_event_count"] = 3
    elif field == "implementation": artifact["implementation"]["revision_code_sha256"] = "a" * 64
    else: artifact["fingerprint"] = "a" * 64
    atomic_write_json(output, artifact)
    with pytest.raises(ValueError, match="independently replayed"):
        revision.load_checked(ref(output))


@pytest.mark.parametrize("target", ["config", "parent", "raw"])
def test_changed_inputs_rejected(parent, tmp_path, target):
    cfg, descriptor = parent
    _, path, _ = proposal(parent, tmp_path)
    output_ref = revision.apply(ref(path), tmp_path / "revision.json")
    changed = {"config": path, "parent": Path(descriptor["artifact"]["path"]),
               "raw": Path(cfg["episodes"][0]["raw"])}[target]
    value = read_json(changed)
    value["synthetic_mutation"] = True
    atomic_write_json(changed, value)
    with pytest.raises(ValueError):
        revision.load_checked(output_ref)


def test_output_boundaries_and_cli(parent, tmp_path, capsys):
    cfg, _ = parent
    _, path, _ = proposal(parent, tmp_path)
    with pytest.raises(ValueError, match="outside the original run"):
        revision.prepare(ref(path), root(cfg) / "revision.json")
    with pytest.raises(ValueError, match="public repository"):
        revision.prepare(ref(path), Path(revision.__file__).with_name("forbidden.json"))
    with pytest.raises(ValueError, match="overlaps an input"):
        revision.prepare(ref(path), path)
    output = tmp_path / "revision.json"
    args = ["--config", str(path), "--sha256", sha256_file(path), "--output", str(output)]
    assert revision.main(["validate", *args]) == 0 and not output.exists()
    assert revision.main(["apply", *args]) == 0
    assert revision.main(["validate", "--artifact", str(output), "--sha256", sha256_file(output)]) == 0
    assert revision.main(["validate", "--artifact", str(output), "--sha256", "a" * 64]) == 2
    assert str(tmp_path) not in capsys.readouterr().out


def test_narrowing_changes_only_primary_coverage_not_context_or_counts(cfg, tmp_path):
    cfg["episodes"] = cfg["episodes"][:1]
    class BroadModel(SyntheticModel):
        def complete(self, *args):
            content, usage = super().complete(*args)
            content["items"][0]["merged_ids"] = [0, 2]
            content["items"][0]["supplementary_notes"][0]["merged_ids"] = [2]
            return content, usage
    pipeline.run(cfg, through="evidence", backend=BroadModel())
    descriptor = {"kind": "completed", "artifact": ref(root(cfg) / "SYN1/evidence.json"),
                  "manifest": ref(root(cfg) / "run_manifest.json")}
    value, path, source = proposal((cfg, descriptor), tmp_path)
    value["edits"] = [{"evidence_id": "SYN1:E1", "field": "/merged_ids", "expected": [0, 2], "value": [0],
                       "reason": "Separate logistics commentary from primary observation",
                       "source_anchors": [source["raw_segments"][2]]}]
    atomic_write_json(path, value)
    artifact = revision.prepare(ref(path), tmp_path / "revision.json")
    assert artifact["accounting"]["before"]["primary_region_count"] == 3
    assert artifact["accounting"]["after"]["primary_region_count"] == 2
    assert artifact["accounting"]["after"]["item_count"] == 3
    original = read_json(Path(descriptor["artifact"]["path"]))["content"]
    expected = copy.deepcopy(original)
    expected["items"][0]["merged_ids"] = [0]
    assert artifact["content"] == expected
    note = artifact["grounding"]["items"]["SYN1:E1"]["supplementary_context"][0]
    assert note["grounding"]["merged_ids"] == [2] and note["authority"] == "model_analyst_not_speaker"


def test_prose_edit_authorship_is_pointer_specific_and_source_relevant(parent, tmp_path):
    value, path, source = proposal(parent, tmp_path)
    value["edits"] = [{"evidence_id": "SYN1:E1", "field": "/uncertainty/0",
                       "expected": "Event and outcome unknown", "value": "No outcome is established here",
                       "reason": "Explicit limitation", "source_anchors": [source["raw_segments"][0]]}]
    atomic_write_json(path, value)
    artifact = revision.prepare(ref(path), tmp_path / "revision.json")
    assert artifact["editorial_review"]["operations"] == [{"path": "/content/items/0/uncertainty/0",
                                                          "edit_index": 0, "authority": revision.AUTHORITY}]
    assert artifact["grounding"]["items"]["SYN1:E1"]["supplementary_context"][0]["authority"] == "model_analyst_not_speaker"
    value["edits"][0]["source_anchors"] = [source["raw_segments"][1]]
    atomic_write_json(path, value)
    with pytest.raises(ValueError, match="relevant item's"):
        revision.prepare(ref(path), tmp_path / "revision.json")


def test_source_race_fails_before_writing(parent, tmp_path, monkeypatch):
    cfg, _ = parent
    _, path, _ = proposal(parent, tmp_path)
    raw = Path(cfg["episodes"][0]["raw"])
    original = revision._accounting
    def racing(content):
        result = original(content)
        value = read_json(raw)
        value["segments"][0]["text"] += " mutated during replay"
        atomic_write_json(raw, value)
        return result
    monkeypatch.setattr(revision, "_accounting", racing)
    output = tmp_path / "revision.json"
    with pytest.raises(ValueError, match="changed during validation"):
        revision.apply(ref(path), output)
    assert not output.exists()


def test_historical_parent_identity_not_replaced_by_new_validator(parent, tmp_path, monkeypatch):
    _, path, _ = proposal(parent, tmp_path)
    implementation = copy.deepcopy(pipeline._implementation())
    implementation["code"]["profile_pipeline"] = "f" * 64
    monkeypatch.setattr(pipeline, "_implementation", lambda: implementation)
    output_ref = revision.apply(ref(path), tmp_path / "revision.json")
    checked = revision.load_checked(output_ref)
    assert checked["implementation"]["profile"] == implementation
    old = read_json(Path(checked["parent_manifest"]["path"]))
    assert old["implementation"] != implementation
    assert checked["parent"]["artifact"] == parent[1]["artifact"]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_source_holds_and_transitive_review_pins(annotated_failure, tmp_path, index):
    cfg, recovery_config, paths = annotated_failure
    descriptor = {"kind": "recovered", "artifact": recover_evidence.apply(recovery_config, tmp_path / "recovered.json")}
    _, path, _ = proposal((cfg, descriptor), tmp_path)
    output_ref = revision.apply(ref(path), tmp_path / "revision.json")
    pins = {}
    artifact = revision.load_checked(output_ref, pins)
    assert artifact["source"]["prior_source_decision"] == "uncertain"
    assert artifact["source"]["prior_review_limitations"] == ["Synthetic unresolved limitation remains"]
    assert all(path in pins for path in paths)
    changed = read_json(paths[index])
    changed["synthetic_mutation"] = True
    atomic_write_json(paths[index], changed)
    with pytest.raises(ValueError):
        revision.load_checked(output_ref)


def test_completed_repair_keeps_and_rechecks_failed_attempt_chain(cfg, tmp_path):
    cfg["episodes"] = cfg["episodes"][:1]
    cfg["retry_count"] = 1
    class RepairModel(SyntheticModel):
        def complete(self, *args):
            content, usage = super().complete(*args)
            if len(self.calls) == 1:
                content["items"][0]["supplementary_notes"][0]["merged_ids"] = [2]
            return content, usage
    pipeline.run(cfg, through="evidence", backend=RepairModel())
    descriptor = {"kind": "completed", "artifact": ref(root(cfg) / "SYN1/evidence.json"),
                  "manifest": ref(root(cfg) / "run_manifest.json")}
    _, path, _ = proposal((cfg, descriptor), tmp_path)
    output = tmp_path / "revision.json"
    artifact_ref = revision.apply(ref(path), output)
    pins = {}
    revision.load_checked(artifact_ref, pins)
    first = root(cfg) / "calls/SYN1_evidence_1.result.json"
    assert first in pins
    failed = read_json(first)
    assert failed["status"] == "failed"
    failed["response"]["items"][0]["source_observation"] = "Tampered prior response"
    atomic_write_json(first, failed)
    with pytest.raises(ValueError, match="repair chain differs"):
        revision.load_checked(artifact_ref)


def test_source_anchor_must_cover_each_added_primary_region(parent, tmp_path):
    value, path, source = proposal(parent, tmp_path)
    value["edits"] = [{"evidence_id": "SYN1:E1", "field": "/merged_ids", "expected": [0], "value": [0, 1, 2],
                       "reason": "Invalid incomplete source anchors", "source_anchors": [source["raw_segments"][1]]}]
    atomic_write_json(path, value)
    with pytest.raises(ValueError, match="Each changed primary region"):
        revision.prepare(ref(path), tmp_path / "revision.json")


def test_shared_pins_cannot_be_replaced(parent, tmp_path):
    _, path, _ = proposal(parent, tmp_path)
    artifact_ref = revision.apply(ref(path), tmp_path / "revision.json")
    with pytest.raises(ValueError, match="Conflicting"):
        revision.load_checked(artifact_ref, {path: "a" * 64})


def test_validator_upgrade_requires_explicit_new_derivative(parent, tmp_path, monkeypatch):
    _, path, _ = proposal(parent, tmp_path)
    artifact_ref = revision.apply(ref(path), tmp_path / "revision.json")
    implementation = copy.deepcopy(revision._implementation())
    implementation["revision_code_sha256"] = "e" * 64
    monkeypatch.setattr(revision, "_implementation", lambda: implementation)
    with pytest.raises(ValueError, match="independently replayed"):
        revision.load_checked(artifact_ref)
