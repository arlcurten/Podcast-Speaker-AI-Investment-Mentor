"""Historical editorial inventory replay over synthetic sources only."""
import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import corpus_inventory as inventory, pipeline, review
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_corpus_inventory import available, save, selection_for
from .test_pipeline import cfg, root
from .test_review import make_parent, pin


@pytest.fixture
def historical(cfg, tmp_path, monkeypatch):
    implementation = pipeline._implementation
    editorial = review._implementation
    with monkeypatch.context() as old:
        def previous_pipeline():
            result = implementation()
            result["code"]["profile_pipeline"] = "a" * 64
            return result

        def previous_review():
            result = editorial()
            result["code"]["profile_review"] = "b" * 64
            return result

        old.setattr(pipeline, "_implementation", previous_pipeline)
        old.setattr(review, "_implementation", previous_review)
        data = make_parent(cfg, tmp_path)
        data["output"] = tmp_path / "editorial/reviewed.json"
        data["stored"] = review.apply(data["path"], data["output"])
    forbidden = Mock(side_effect=AssertionError("Historical replay must not invoke a backend or subprocess"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr(pipeline.codex_backend.CodexBackend, "complete", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    yield data
    forbidden.assert_not_called()


def load(data):
    pins = {}
    value, refs, manifest = inventory._load_synthesis(
        {"kind": "editorial_completed", "artifact": pin(data["output"])}, pins, {"RESERVED"}, set())
    return value, pins


def test_historical_replay_preserves_identity_without_resuming_and_strict_paths_stay_strict(
        historical, monkeypatch, tmp_path):
    data = historical
    with pytest.raises(ValueError, match="implementation changed"):
        review.validate(data["path"], data["output"])
    with pytest.raises(ValueError, match="implementation changed"):
        review.apply(data["path"], tmp_path / "no-new-revision.json")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    forbidden = Mock(side_effect=AssertionError("Never resume a historical run"))
    monkeypatch.setattr(pipeline, "run", forbidden)
    checked, pins = load(data)
    assert checked == data["stored"]
    assert checked["implementation"] != review._implementation()
    assert checked["implementation"]["code"]["profile_review"] == "b" * 64
    assert checked["content"]["evidence_accounting"] == data["parent"]["content"]["evidence_accounting"]
    assert checked["source"]["episodes"][0]["source_acceptance"] == "not_granted"
    assert checked["policy"] == pipeline.POLICY
    assert checked["additional_model_calls"] == 0 and checked["model_usage"] is None
    for name in ("raw", "draft", "source_review"):
        entry = data["cfg"]["episodes"][0]
        assert pins[Path(entry[name])] == entry[name + "_sha256"]
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    forbidden.assert_not_called()


def test_inventory_pins_current_validator_separately_from_historical_revision(historical, tmp_path, monkeypatch):
    data = historical
    selected = selection_for(data["cfg"], tmp_path, synthesis=False)
    selected["selection"].update(expected_synthesis_ids=["editorial"], syntheses=[{
        "synthesis_id": "editorial", "synthesis": available({
            "kind": "editorial_completed", "artifact": pin(data["output"])})}])
    forbidden = Mock(side_effect=AssertionError("No strict runtime resume for inventory"))
    monkeypatch.setattr(pipeline, "run", forbidden)
    ref = inventory.freeze(save(selected), selected["output"])
    result = inventory.load_checked(ref)
    assert result["inventory_complete"] and len(result["hypotheses"]) == 2
    assert result["implementation"]["code"]["review"] == sha256_file(Path(review.__file__))
    assert result["implementation"]["profile"] == pipeline._implementation()
    assert result["implementation"]["code"]["review"] != data["stored"]["implementation"]["code"]["profile_review"]
    assert pin(data["path"]) in result["dependencies"]
    assert pin(data["output"]) in result["dependencies"]
    assert not result["policy"]["eligible_for_serving"]
    forbidden.assert_not_called()


@pytest.mark.parametrize("target", ["raw", "draft", "source_review", "manifest", "evidence",
                                    "evidence_request", "synthesis_request", "synthesis_result", "parent"])
def test_parent_is_validated_before_editorial_replay(historical, monkeypatch, target):
    data = historical
    run = root(data["cfg"])
    entry = data["cfg"]["episodes"][0]
    paths = {**{name: Path(entry[name]) for name in ("raw", "draft", "source_review")},
             "manifest": run / "run_manifest.json", "evidence": run / "SYN1/evidence.json",
             "evidence_request": run / "calls/SYN1_evidence_1.request.json",
             "synthesis_request": run / "calls/cross_episode_synthesis_1.request.json",
             "synthesis_result": run / "calls/cross_episode_synthesis_1.result.json",
             "parent": run / "synthesis.json"}
    path = paths[target]
    value = read_json(path)
    value["tampered"] = True
    atomic_write_json(path, value)
    replay = Mock(side_effect=AssertionError("Invalid parent must fail before editorial replay"))
    monkeypatch.setattr(review, "_historical_completed", replay)
    with pytest.raises(ValueError):
        load(data)
    replay.assert_not_called()


@pytest.mark.parametrize("fault", ["content", "grounding", "accounting", "policy", "source", "authority",
    "usage", "calls", "evidence", "extra", "output", "missing_implementation", "malformed_code"])
def test_rebound_revision_hash_cannot_hide_stored_changes(historical, fault):
    data = historical
    value = copy.deepcopy(data["stored"])
    if fault == "content": value["content"]["hypotheses"][0]["label"] = "Forged label"
    elif fault == "grounding": value["grounding"]["hypotheses"]["PROFILE:H1"]["independent_event_count"] = 3
    elif fault == "accounting": value["content"]["evidence_accounting"].pop()
    elif fault == "policy": value["policy"]["eligible_for_serving"] = True
    elif fault == "source": value["source"]["episodes"][0]["source_acceptance"] = "granted"
    elif fault == "authority": value["authority"] = "source_evidence"
    elif fault == "usage": value["model_usage"] = {"input_tokens": 10}
    elif fault == "calls": value["additional_model_calls"] = False
    elif fault == "evidence": value["evidence"]["SYN1"]["sha256"] = "0" * 64
    elif fault == "extra": value["request_sha256"] = "0" * 64
    elif fault == "output": value["output_path"] = str(data["output"].with_name("different.json"))
    elif fault == "missing_implementation": value.pop("implementation")
    else: value["implementation"]["code"]["profile_review"] = "invalid"
    atomic_write_json(data["output"], value)
    with pytest.raises(ValueError):
        load(data)


@pytest.mark.parametrize("fault, message", [
    ("old_value", "Expected old value"), ("unknown_hypothesis", "Unknown hypothesis"),
    ("allowlist", "allowlist"), ("quote", "Anchor must exactly"), ("episode", "Anchor must exactly"),
    ("uncovered", "Every cited evidence"), ("duplicate_path", "conflicting editorial paths"),
    ("relationship_upgrade", "downgrade to unresolved"), ("invalid_schema", "input contract"),
])
def test_historical_replay_enforces_edit_contract_even_with_rebound_proposal(historical, fault, message):
    data = historical
    proposal = copy.deepcopy(data["proposal"])
    edit = proposal["edits"][0]
    if fault == "old_value": edit["expected"] = "Not the original"
    elif fault == "unknown_hypothesis": edit["hypothesis_id"] = "PROFILE:H99"
    elif fault == "allowlist": edit["field"] = "/supporting_evidence"
    elif fault == "quote": edit["source_anchors"][0]["text"] += " Fabricated"
    elif fault == "episode": edit["source_anchors"][0]["episode_id"] = "OTHER"
    elif fault == "uncovered": edit["evidence_ids"].append("SYN1:E2")
    elif fault == "duplicate_path": proposal["edits"].append(copy.deepcopy(edit))
    elif fault == "relationship_upgrade":
        edit.update(field="/statement_action_relationships/0/relationship", expected="agreement", value="apparent_mismatch")
    else: edit.pop("expected")
    atomic_write_json(data["path"], proposal)
    stored = copy.deepcopy(data["stored"])
    stored["proposal"] = pin(data["path"])
    stored["editorial_review"] = {k: proposal[k] for k in ("reviewer", "method", "rationale", "edits")}
    atomic_write_json(data["output"], stored)
    with pytest.raises(ValueError, match=message):
        load(data)


@pytest.mark.parametrize("target", ["proposal", "source", "result", "revision", "validator"])
def test_historical_replay_rechecks_pins_after_edits(historical, monkeypatch, target):
    data = historical
    edit = review._edited_content
    def change_after_edits(*args):
        result = edit(*args)
        if target == "validator":
            changed = review._implementation()
            changed["code"]["profile_review"] = "c" * 64
            monkeypatch.setattr(review, "_implementation", lambda: changed)
        else:
            path = {"proposal": data["path"], "revision": data["output"],
                    "source": Path(data["cfg"]["episodes"][0]["raw"]),
                    "result": root(data["cfg"]) / "calls/cross_episode_synthesis_1.result.json"}[target]
            value = read_json(path)
            value["concurrent_change"] = True
            atomic_write_json(path, value)
        return result
    monkeypatch.setattr(review, "_edited_content", change_after_edits)
    with pytest.raises(ValueError):
        load(data)
