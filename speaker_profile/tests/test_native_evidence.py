"""Native import contracts over invented text only; no backend or real episode."""
import copy
import json
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import native_evidence as native, pipeline, schemas
from transcription.common import atomic_write_json, read_json, sha256_file, utc_now
from transcription.tests.test_annotations import annotation_review
from .test_pipeline import cfg, evidence_proposal


def ref(path):
    return {"path": str(path), "sha256": sha256_file(path)}


@pytest.fixture(autouse=True)
def no_backend(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No backend, subprocess or network"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr(pipeline, "run", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.CodexBackend.complete", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    yield
    forbidden.assert_not_called()


@pytest.fixture
def config(cfg, tmp_path):
    entry = cfg["episodes"][0]
    value = {"schema_version": native.CONFIG_VERSION, "packet_id": "synthetic-native-v1",
             "actor": {"identity": "Synthetic test author", "session_id": "synthetic-existing-session"},
             "episode": {"episode_id": entry["episode_id"],
                         "raw": ref(Path(entry["raw"])), "normalized": ref(Path(entry["draft"])),
                         "source_review": None},
             "reserved_episode_ids": ["RESERVED1"], "commentary_scope": schemas.EPISODE_CONTEXT,
             "max_packet_bytes": 1_000_000, "max_submission_bytes": 200_000}
    path = tmp_path / "native-config.json"
    atomic_write_json(path, value)
    return path


def submit(packet_ref, tmp_path, *, empty=False):
    packet = read_json(Path(packet_ref["path"]))
    context = packet["context"]
    source = {**context["source"], "segments": context["merged_segments"],
              "raw_segments": {r["segment_id"]: r for r in context["raw_segments"]}}
    value = {"schema_version": native.SUBMISSION_VERSION, "packet": packet_ref,
             "actor": packet["actor"], "authored_at": utc_now(),
             "method": "Invented synthetic content, not an actual agent extraction or source review.",
             "complete_episode_read": True, "limitations": ["Synthetic declaration, not proof of reading."],
             "model_metadata": copy.deepcopy(native.MODEL_METADATA), "content": evidence_proposal(source, empty)}
    path = tmp_path / "submission.json"
    atomic_write_json(path, value)
    return path


@pytest.fixture
def ready(config, tmp_path):
    packet = native.freeze(ref(config), tmp_path / "packet.json")
    return config, packet, submit(packet, tmp_path)


def snapshot(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_full_packet_and_import_replay_without_receipts(ready, tmp_path):
    config, packet_ref, submission = ready
    before = snapshot(tmp_path)
    packet = native.load_packet(packet_ref)
    assert len(packet["context"]["raw_segments"]) == len(packet["context"]["merged_segments"]) == 3
    assert packet["prompt"] == schemas.prompt("evidence", schemas.EPISODE_CONTEXT)
    assert packet["schema"] == schemas.schema("evidence")
    assert packet["submission_schema"] == native.submission_schema()
    assert packet["config"] == ref(config)
    assert {Path(p["path"]) for p in packet["dependencies"]} >= {
        Path(read_json(config)["episode"][k]["path"]) for k in ("raw", "normalized")}
    assert native.plan(ref(config), tmp_path / "another-packet.json")["context"] == packet["context"]
    output = tmp_path / "adopted/candidate.json"
    preview = native.prepare(ref(submission), output)
    assert snapshot(tmp_path) == before
    result_ref = native.apply(ref(submission), output)
    pins = {}
    result = native.load_checked(result_ref, pins)
    assert result["content"] == preview["content"] == read_json(submission)["content"]
    assert result["model_metadata"] == native.MODEL_METADATA
    assert result["model_metadata"]["usage"] is result["model_metadata"]["model"] is None
    assert result["adoption"]["model_calls_this_execution"] == 0
    assert result["adoption"]["model_call_performed"] is False
    assert result["source"]["source_acceptance"] == "not_granted"
    assert result["policy"] == pipeline.POLICY and not result["policy"]["eligible_for_serving"]
    assert result["accounting"]["independent_event_count"] is None
    assert result["accounting"]["direct_action_count"] == 2
    assert result["accounting"]["direct_action_overlap_components"] == [["SYN1:E1"], ["SYN1:E2"]]
    assert result["schema_version"] != schemas.VERSION
    assert not {"request_sha256", "result_sha256", "model_calls"} & result.keys()
    assert not list(tmp_path.rglob("*.request.json")) and not list(tmp_path.rglob("*.result.json"))
    assert {config, submission, Path(packet_ref["path"])} <= pins.keys()
    assert all(p.read_bytes() == v for p, v in before.items())
    with pytest.raises(FileExistsError):
        native.freeze(ref(config), Path(packet_ref["path"]))
    with pytest.raises(FileExistsError):
        native.apply(ref(submission), output)


@pytest.mark.parametrize("fault", ["reserved", "wrong_episode", "raw_pin", "normalized_pin", "public_source",
                                  "missing_actor", "blank_actor", "model_config", "extra_episode", "bad_scope"])
def test_config_fails_closed(config, tmp_path, fault, monkeypatch):
    value = read_json(config)
    if fault == "reserved":
        value["reserved_episode_ids"] = ["SYN1"]
        monkeypatch.setattr(native.evidence_revision, "_sources", Mock(side_effect=AssertionError("Reserved source read")))
    elif fault == "wrong_episode": value["episode"]["episode_id"] = "SYN2"
    elif fault in {"raw_pin", "normalized_pin"}: value["episode"][fault[:-4]]["sha256"] = "a" * 64
    elif fault == "public_source": value["episode"]["raw"] = ref(Path(native.__file__))
    elif fault == "missing_actor": del value["actor"]["session_id"]
    elif fault == "blank_actor": value["actor"]["identity"] = " "
    elif fault == "model_config": value["model"] = "pretend-backend"
    elif fault == "extra_episode": value["episodes"] = [value["episode"]]
    else: value["commentary_scope"] = "invented"
    atomic_write_json(config, value)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        native.freeze(ref(config), tmp_path / "rejected-packet.json")
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("fault", ["actor", "predates_packet", "future", "naive_time", "unread", "model",
                                  "usage", "fake_receipt", "extra_call_count", "wrong_packet_hash"])
def test_authorship_and_unknown_metadata_contract(ready, tmp_path, fault):
    _, _, path = ready
    value = read_json(path)
    if fault == "actor": value["actor"]["session_id"] = "unassigned-session"
    elif fault == "predates_packet": value["authored_at"] = "2000-01-01T00:00:00Z"
    elif fault == "future": value["authored_at"] = "2999-01-01T00:00:00Z"
    elif fault == "naive_time": value["authored_at"] = "2000-01-01T00:00:00"
    elif fault == "unread": value["complete_episode_read"] = False
    elif fault == "model": value["model_metadata"]["model"] = "guessed"
    elif fault == "usage": value["model_metadata"]["usage"] = {"input_tokens": 0}
    elif fault == "fake_receipt": value["status"] = "completed"
    elif fault == "extra_call_count": value["model_calls_this_execution"] = 1
    else: value["packet"]["sha256"] = "a" * 64
    atomic_write_json(path, value)
    with pytest.raises(ValueError):
        native.apply(ref(path), tmp_path / "rejected.json")
    assert not (tmp_path / "rejected.json").exists()


@pytest.mark.parametrize("fault", ["quote_text", "timestamp", "raw_id", "merged_id", "coverage", "other_episode",
                                  "listener_basis", "recursive_basis", "basis_anchor", "hypothetical_action",
                                  "commentary_as_observation", "foreign_note"])
def test_unchanged_grounding_validator_rejects_invalid_content(ready, tmp_path, fault):
    _, _, path = ready
    value = read_json(path)
    content = value["content"]
    first, action, inferred = content["items"]
    if fault == "quote_text": first["quotes"][0]["text"] = "Invented"
    elif fault == "timestamp": first["quotes"][0]["end"] += 1
    elif fault == "raw_id": first["quotes"][0]["segment_id"] = 999
    elif fault == "merged_id": first["merged_ids"] = [999]
    elif fault == "coverage": content["unselected_regions"] = []
    elif fault == "other_episode": content["episode_id"] = "SYN2"
    elif fault == "listener_basis": first["speaker_attribution"] = "listener"
    elif fault == "recursive_basis": inferred["basis_evidence_ids"] = [inferred["evidence_id"]]
    elif fault == "basis_anchor": inferred["merged_ids"] = [2]
    elif fault == "hypothetical_action": action["decision_context"]["mode"] = "hypothetical"
    elif fault == "commentary_as_observation": first["source_observation"] = "[Agent clarification] Not source"
    else: first["supplementary_notes"][0]["merged_ids"] = [999]
    atomic_write_json(path, value)
    with pytest.raises(ValueError):
        native.apply(ref(path), tmp_path / "rejected.json")
    assert not (tmp_path / "rejected.json").exists()


@pytest.mark.parametrize("field", ["prompt", "schema", "context", "implementation", "dependencies",
                                  "config_digest", "actor", "output_path", "contract_sha256"])
def test_repinned_packet_tampering_rejected(ready, field):
    _, packet_ref, _ = ready
    path = Path(packet_ref["path"])
    packet = read_json(path)
    if field == "context": packet[field]["raw_segments"].pop()
    elif field == "dependencies": packet[field].pop()
    elif field in {"implementation", "schema", "actor"}: packet[field] = {}
    else: packet[field] = "tampered"
    atomic_write_json(path, packet)
    with pytest.raises(ValueError):
        native.load_packet(ref(path))


@pytest.mark.parametrize("field", ["content", "grounding", "source", "policy", "accounting", "authorship",
                                  "implementation", "adoption", "model_metadata", "fingerprint", "fake_receipt"])
def test_repinned_candidate_tampering_rejected(ready, tmp_path, field):
    _, _, submission = ready
    output = tmp_path / "candidate.json"
    native.apply(ref(submission), output)
    value = read_json(output)
    if field == "content": value[field]["summary"] = "Changed after authoring"
    elif field == "policy": value[field]["eligible_for_serving"] = True
    elif field == "accounting": value[field]["independent_event_count"] = 3
    elif field == "adoption": value[field]["model_calls_this_execution"] = 1
    elif field == "model_metadata": value[field]["usage"] = {"input_tokens": 0}
    elif field == "fake_receipt": value["request_sha256"] = "a" * 64
    else: value[field] = "tampered"
    atomic_write_json(output, value)
    with pytest.raises(ValueError):
        native.load_checked(ref(output))


@pytest.mark.parametrize("target", ["config", "raw", "normalized", "submission"])
def test_input_drift_is_not_silently_adopted(ready, tmp_path, target):
    config, _, submission = ready
    candidate = native.apply(ref(submission), tmp_path / "candidate.json")
    path = config if target == "config" else submission if target == "submission" else Path(read_json(config)["episode"][target]["path"])
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        native.load_checked(candidate)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_annotation_dependencies_and_source_hold_preserved(config, tmp_path, index):
    value = read_json(config)
    paths = annotation_review(Path(value["episode"]["raw"]["path"]), tmp_path / "review")
    review_value = read_json(paths[0])
    review_value.update(decision="uncertain", unresolved_critical_issues=["Synthetic unresolved source limitation"])
    atomic_write_json(paths[0], review_value)
    value["episode"]["source_review"] = ref(paths[0])
    atomic_write_json(config, value)
    packet_ref = native.freeze(ref(config), tmp_path / "packet.json")
    submission = submit(packet_ref, tmp_path)
    candidate = native.apply(ref(submission), tmp_path / "candidate.json")
    pins = {}
    artifact = native.load_checked(candidate, pins)
    assert all(p in pins for p in paths)
    assert artifact["source"]["prior_source_decision"] == "uncertain"
    assert artifact["source"]["prior_review_limitations"] == ["Synthetic unresolved source limitation"]
    issue = artifact["grounding"]["items"]["SYN1:E1"]["source_issues"][0]
    assert issue["severity"] == "critical" and issue["authority"] == "reviewer_commentary_not_source"
    paths[index].write_bytes(paths[index].read_bytes() + b"\n")
    with pytest.raises(ValueError):
        native.load_checked(candidate)


def test_zero_items_and_episode_context_do_not_invent_coverage(ready, tmp_path):
    _, packet_ref, _ = ready
    submission = submit(packet_ref, tmp_path, empty=True)
    zero = native.prepare(ref(submission), tmp_path / "zero.json")
    assert zero["accounting"]["item_count"] == 0
    submission = submit(packet_ref, tmp_path)
    value = read_json(submission)
    value["content"]["items"][0]["supplementary_notes"][0]["merged_ids"] = [2]
    atomic_write_json(submission, value)
    artifact = native.prepare(ref(submission), tmp_path / "notes.json")
    note = artifact["grounding"]["items"]["SYN1:E1"]["supplementary_context"][0]
    assert note["grounding"]["merged_ids"] == [2] and note["authority"] == "model_analyst_not_speaker"
    assert artifact["accounting"]["primary_region_count"] == 2
    value["content"]["unselected_regions"] = []
    atomic_write_json(submission, value)
    with pytest.raises(ValueError, match="Every source region"):
        native.prepare(ref(submission), tmp_path / "invalid.json")


def test_limits_are_exact_not_truncation(config, tmp_path):
    output = tmp_path / "packet.json"
    value = read_json(config)
    value["max_packet_bytes"] = 1
    atomic_write_json(config, value)
    with pytest.raises(ValueError, match="no truncation"):
        native.freeze(ref(config), output)
    assert not output.exists()
    value.update(max_packet_bytes=1_000_000, max_submission_bytes=1)
    atomic_write_json(config, value)
    packet = native.freeze(ref(config), output)
    assert output.stat().st_size == native._size(read_json(output))
    submission = submit(packet, tmp_path)
    with pytest.raises(ValueError, match="max_submission_bytes"):
        native.prepare(ref(submission), tmp_path / "candidate.json")


@pytest.mark.parametrize("text", ['{"duplicate":1,"duplicate":2}', '{"number":NaN}', '{"number":1e999}', '[]'])
def test_strict_json_rejected(tmp_path, text):
    path = tmp_path / "invalid.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        native.prepare(ref(path), tmp_path / "candidate.json")


def test_output_boundaries_shared_pins_and_code_upgrade(ready, tmp_path, monkeypatch):
    config, packet, submission = ready
    for output in (config, submission, packet["path"], read_json(config)["episode"]["raw"]["path"]):
        with pytest.raises(ValueError, match="overlaps an input"):
            native.prepare(ref(submission), output)
    with pytest.raises(ValueError, match="public repository"):
        native.prepare(ref(submission), Path(native.__file__).with_name("forbidden.json"))
    link = tmp_path / "public-link"
    link.symlink_to(Path(native.__file__).parent, target_is_directory=True)
    with pytest.raises(ValueError, match="public repository"):
        native.prepare(ref(submission), link / "forbidden.json")
    candidate = native.apply(ref(submission), tmp_path / "candidate.json")
    with pytest.raises(ValueError, match="Conflicting"):
        native.load_checked(candidate, {config: "a" * 64})
    changed = native._implementation()
    changed["code"]["native_evidence"] = "a" * 64
    monkeypatch.setattr(native, "_implementation", lambda: changed)
    with pytest.raises(ValueError, match="reconstruction"):
        native.load_checked(candidate)


def test_racing_source_rejected_before_write(ready, tmp_path, monkeypatch):
    config, _, submission = ready
    raw = Path(read_json(config)["episode"]["raw"]["path"])
    original = pipeline._derive_evidence
    def racing(*args, **kwargs):
        value = original(*args, **kwargs)
        raw.write_bytes(raw.read_bytes() + b"\n")
        return value
    monkeypatch.setattr(pipeline, "_derive_evidence", racing)
    output = tmp_path / "candidate.json"
    with pytest.raises(ValueError, match="changed during validation"):
        native.apply(ref(submission), output)
    assert not output.exists()


def test_hard_read_cap_and_packet_rebinding(ready, tmp_path, monkeypatch):
    config, packet, submission = ready
    with pytest.raises(ValueError, match="byte limit"):
        native._read(ref(submission), {}, limit=1)
    # Another frozen assignment does not authorize rewriting the submission's actor.
    value = read_json(config)
    value["actor"]["session_id"] = "different-synthetic-session"
    other_config = tmp_path / "other-config.json"
    atomic_write_json(other_config, value)
    other_packet = native.freeze(ref(other_config), tmp_path / "other-packet.json")
    value = read_json(submission)
    value["packet"] = other_packet
    value["authored_at"] = utc_now()
    atomic_write_json(submission, value)
    with pytest.raises(ValueError, match="actor"):
        native.prepare(ref(submission), tmp_path / "rejected.json")


def test_candidate_size_and_implementation_race_fail_closed(ready, tmp_path, monkeypatch):
    _, _, submission = ready
    original_size = native._size
    def oversized(value):
        return native.MAX_JSON_BYTES + 1 if value["schema_version"] == native.VERSION else original_size(value)
    monkeypatch.setattr(native, "_size", oversized)
    output = tmp_path / "candidate.json"
    with pytest.raises(ValueError, match="replay byte limit"):
        native.apply(ref(submission), output)
    assert not output.exists()
    monkeypatch.setattr(native, "_size", original_size)
    original_accounting = native.evidence_revision._accounting
    implementation = native._implementation()
    def racing(content):
        changed = copy.deepcopy(implementation)
        changed["code"]["native_evidence"] = "f" * 64
        monkeypatch.setattr(native, "_implementation", lambda: changed)
        return original_accounting(content)
    monkeypatch.setattr(native.evidence_revision, "_accounting", racing)
    with pytest.raises(ValueError, match="implementation changed"):
        native.apply(ref(submission), output)
    assert not output.exists()


def test_cli_plan_freeze_prepare_apply_validate(config, tmp_path, capsys):
    packet = tmp_path / "packet.json"
    args = ["--input", str(config), "--sha256", sha256_file(config), "--output", str(packet)]
    assert native.main(["plan", *args]) == 0 and not packet.exists()
    assert native.main(["freeze", *args]) == 0
    submission = submit(ref(packet), tmp_path)
    output = tmp_path / "candidate.json"
    args = ["--input", str(submission), "--sha256", sha256_file(submission), "--output", str(output)]
    assert native.main(["prepare", *args]) == 0 and not output.exists()
    assert native.main(["apply", *args]) == 0
    for path in (packet, output):
        assert native.main(["validate", "--input", str(path), "--sha256", sha256_file(path)]) == 0
    assert native.main(["validate", "--input", str(output), "--sha256", "a" * 64]) == 2
    assert str(tmp_path) not in capsys.readouterr().out
    assert native.main(["schema", "--kind", "config"]) == 0
    assert json.loads(capsys.readouterr().out) == native.CONFIG_SCHEMA
    assert native.main(["schema", "--kind", "submission"]) == 0
    assert json.loads(capsys.readouterr().out) == native.submission_schema()
