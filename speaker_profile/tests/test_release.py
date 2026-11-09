"""Small synthetic scoped-use reviews; reviewer judgments are fictional, not approvals."""
import copy
import json
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest

from speaker_profile import native_synthesis as native, pipeline, release
from transcription.common import PROJECT_ROOT, atomic_write_json, read_json, utc_now
from .test_pipeline import cfg, hypothesis, synthesis_proposal
from .test_native_inventory import mixed, files
from .test_native_synthesis import ready, anchors, comparison, write
from .test_review import pin


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No real backend, process or network"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.CodexBackend.complete", forbidden)
    monkeypatch.setattr("knowledge.codex_backend.subprocess.Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(release, "load_project_dotenv", Mock())
    yield
    forbidden.assert_not_called()


@pytest.fixture
def adopted(ready):
    s = ready["submission"]
    s["content"] = synthesis_proposal(list(ready["catalog"].values()), ["SYN1:E3", "SYN1:E2"])
    h = s["content"]["hypotheses"][0]
    h["supporting_evidence"] = ["SYN1:E3"]
    h["counterevidence"] = ["SYN1:E2"]
    h["proposed_confidence"]["generalization_support"]["value"] = "low"
    h["conditions"] = []  # Missing precise conditions is retained, not an automatic rejection.
    s["hypothesis_anchors"][0]["source_anchors"] = anchors(ready, ["SYN1:E1", "SYN1:E2", "SYN1:E3"])
    other = hypothesis(["SYN2:E3"])
    other["hypothesis_id"] = "PROFILE:H2"
    s["content"]["hypotheses"].append(other)
    s["hypothesis_anchors"].append({"hypothesis_id": "PROFILE:H2",
        "source_anchors": anchors(ready, ["SYN2:E1", "SYN2:E3"]),
        "report_refs": s["hypothesis_anchors"][0]["report_refs"]})
    for row in s["content"]["evidence_accounting"]:
        if row["evidence_id"] == "SYN2:E3":
            row.update(disposition="in_hypotheses", hypothesis_ids=["PROFILE:H2"])
    comparison(ready, "unresolved-actions", ["SYN1:E2", "SYN2:E2"])
    comparison(ready, "pending-listener", ["SYN1:E1", "SYN3:E1"], inspected=False)
    ref = native.apply(write(ready), ready["output"])
    return ready, ref


def source_finding(context=False):
    return {"decision": "context_only" if context else "fit_for_scoped_use",
            "rationale": "Fictional source judgment: uncertain context retained, no episode acceptance."}


def semantic_finding(context=False):
    return {"decision": "context_only" if context else "supported_with_limits",
            "rationale": "Fictional narrow teaching judgment, not recurring personality or verified events."}


def author_review(packet, path):
    bundle = packet["bundle"]
    report = path.with_suffix(".md")
    report.write_text("Fictional source-use review: partial context retained; no actual semantic approval.\n")
    review = {"schema_version": release.REVIEW_VERSION, "scope": release.SCOPE, "decision": "accept",
        "candidate": packet["candidate"], "reviewer": {"identity": "Synthetic text reviewer", "session_id": "test-session"},
        "method": "Fictional source-grounded review fixture; no actual semantic approval",
        "independence": "nonblind", "reviewed_at": utc_now(), "reports": [pin(report)],
        "selected_hypothesis_ids": packet["selected_hypothesis_ids"], "bundle_sha256": packet["bundle_sha256"],
        "limitations": ["Synthetic test only; all source holds remain"],
        "hypotheses": [{"hypothesis_id": h["hypothesis_id"], "hypothesis_sha256": pipeline.digest(h),
            "source_fitness": source_finding(), "semantic_fidelity": semantic_finding(),
            "limitations": ["Low generalization support and missing conditions restrict this to a provisional example"]}
            for h in bundle["hypotheses"]],
        "observations": [{"evidence_id": rid, "observation_sha256": pipeline.digest(row),
            "source_fitness": source_finding(rid in bundle["context_only_evidence_ids"]),
            "semantic_fidelity": semantic_finding(rid in bundle["context_only_evidence_ids"]),
            "limitations": ["Inference and listener attribution remain distinct; no missing context supplied"]}
            for rid, row in bundle["observations"].items()],
        "context_findings": [{"context_ref": key, "context_sha256": pipeline.digest(row),
            "disposition": "retained_limitation", "reason": "Fictional partial context does not establish a contrary fact; keep pending status"}
            for key, row in release._context_refs(bundle).items()]}
    atomic_write_json(path, review)
    return review


@pytest.fixture
def reviewed(adopted, tmp_path):
    data, ref = adopted
    packet = release.prepare(ref, ["PROFILE:H1"])
    path = tmp_path / "scoped-review.json"
    review = author_review(packet, path)
    return data, ref, packet, path, review, tmp_path / "scoped-release.json"


def test_prepare_publish_read_once_per_operation_preserves_all_context(adopted, tmp_path, monkeypatch):
    data, ref = adopted
    before = files(tmp_path)
    replay = Mock(wraps=native.load_checked)
    monkeypatch.setattr(native, "load_checked", replay)
    packet = release.prepare(ref, ["PROFILE:H1"])
    assert replay.call_count == 1 and files(tmp_path) == before
    assert packet["status"] == "review_inputs_only"
    bundle = packet["bundle"]
    original = read_json(data["output"])
    assert bundle["hypotheses"] == original["content"]["hypotheses"][:1]
    assert bundle["claim_evidence_ids"] == ["SYN1:E1", "SYN1:E2", "SYN1:E3"]
    assert "SYN3:E1" in bundle["context_only_evidence_ids"]
    assert bundle["context"]["comparison_ledger"][0]["relationship"] == "unresolved"
    assert bundle["context"]["comparison_coverage"][1]["status"] == "pending"
    assert any(row["disposition"] == "pending" for row in bundle["context"]["known_context"])
    assert bundle["grounding"]["PROFILE:H1"]["independent_event_count"] is None
    assert bundle["grounding"]["PROFILE:H1"]["low_frequency_status"] == "single_source_group"
    assert bundle["observations"]["SYN3:E1"]["speaker_attribution"] == "listener"
    for rid, row in bundle["observations"].items():
        assert row == data["catalog"][rid]  # Exact quotes, tradeoffs, limitations, attribution and grounding.
    assert bundle["context"]["source_context"]["SYN3"] == original["source_context"]["SYN3"]
    assert bundle["context"]["source_context"]["SYN3"]["source"]["prior_source_decision"] == "uncertain"
    assert bundle["candidate_status"] == "unreviewed_candidate"
    assert bundle["candidate_policy"] == original["policy"]
    assert bundle["model_metadata"]["usage"] is None
    review_path = tmp_path / "review.json"
    author_review(packet, review_path)
    descriptor = release.publish(pin(review_path), tmp_path / "release.json")
    assert replay.call_count == 2
    value = release.load_checked(descriptor)
    assert replay.call_count == 3 and value["bundle"] == bundle
    assert value["policy"] == release.POLICY
    assert value["model_calls_this_operation"] == 0
    assert pin(review_path.with_suffix(".md")) in value["release"]["dependencies"]
    assert value["review"]["reports"] == [pin(review_path.with_suffix(".md"))]
    assert not value["policy"]["hypotheses_are_evidence"]
    assert all(path.read_bytes() == content for path, content in before.items())
    assert set(files(tmp_path)) - set(before) == {review_path, review_path.with_suffix(".md"), Path(descriptor["path"])}


@pytest.mark.parametrize("selected", [[], ["PROFILE:H99"], ["PROFILE:H1", "PROFILE:H1"], "PROFILE:H1"])
def test_invalid_selection_never_writes(adopted, tmp_path, selected):
    before = files(tmp_path)
    with pytest.raises(ValueError):
        release.prepare(adopted[1], selected)
    assert files(tmp_path) == before


@pytest.mark.parametrize("fault", ["reject", "revoked", "candidate", "bundle", "hypothesis", "observation", "missing_basis",
    "source_ambiguity", "unsupported", "context_only_support", "missing_context", "context_hash", "blocking_context",
    "unknown_selection", "duplicate_observation", "pre_candidate", "empty_rationale",
    "no_reports", "empty_reports", "report_hash", "duplicate_report", "public_report"])
def test_bad_review_fails_before_writes(reviewed, tmp_path, fault):
    _, _, _, path, review, output = reviewed
    if fault in ("reject", "revoked"): review["decision"] = fault
    elif fault == "candidate": review["candidate"]["sha256"] = "0" * 64
    elif fault == "bundle": review["bundle_sha256"] = "0" * 64
    elif fault == "hypothesis": review["hypotheses"][0]["hypothesis_sha256"] = "0" * 64
    elif fault == "observation": review["observations"][0]["observation_sha256"] = "0" * 64
    elif fault == "missing_basis": review["observations"] = [r for r in review["observations"] if r["evidence_id"] != "SYN1:E1"]
    elif fault == "source_ambiguity": review["observations"][0]["source_fitness"]["decision"] = "claim_bearing_ambiguity"
    elif fault == "unsupported": review["hypotheses"][0]["semantic_fidelity"]["decision"] = "unsupported"
    elif fault == "context_only_support": review["observations"][0]["source_fitness"]["decision"] = "context_only"
    elif fault == "missing_context": review["context_findings"].pop()
    elif fault == "context_hash": review["context_findings"][0]["context_sha256"] = "0" * 64
    elif fault == "blocking_context": review["context_findings"][0]["disposition"] = "blocks_selected_claims"
    elif fault == "unknown_selection": review["selected_hypothesis_ids"] = ["PROFILE:H99"]
    elif fault == "duplicate_observation": review["observations"].append(copy.deepcopy(review["observations"][0]))
    elif fault == "pre_candidate": review["reviewed_at"] = "2020-01-01T00:00:00Z"
    elif fault == "empty_rationale": review["hypotheses"][0]["semantic_fidelity"]["rationale"] = " "
    elif fault == "no_reports": review.pop("reports")
    elif fault == "empty_reports": review["reports"] = []
    elif fault == "report_hash": review["reports"][0]["sha256"] = "0" * 64
    elif fault == "duplicate_report": review["reports"].append(copy.deepcopy(review["reports"][0]))
    elif fault == "public_report": review["reports"] = [pin(PROJECT_ROOT / "README.md")]
    atomic_write_json(path, review)
    before = files(tmp_path)
    with pytest.raises(ValueError): release.publish(pin(path), output)
    assert files(tmp_path) == before and not output.exists()


@pytest.mark.parametrize("target", ["review", "candidate", "raw", "report", "review_report", "missing_review_report"])
def test_changed_dependency_rejected_on_read(reviewed, target):
    data, _, _, path, _, output = reviewed
    ref = release.publish(pin(path), output)
    target_path = {"review": path, "candidate": data["output"],
        "raw": Path(data["index"]["episodes"]["SYN3"]["native_lineage"]["source_files"]["raw"]["path"]),
        "report": Path(data["config"]["reports"][0]["ref"]["path"]),
        "review_report": path.with_suffix(".md"), "missing_review_report": path.with_suffix(".md")}[target]
    if target == "missing_review_report":
        target_path.unlink()
    else:
        target_path.write_bytes(target_path.read_bytes() + b"\n")
    with pytest.raises((ValueError, FileNotFoundError)): release.load_checked(ref)


@pytest.mark.parametrize("field", ["selected_hypothesis_ids", "dependencies", "bundle_sha256", "policy", "implementation_sha256"])
def test_rehashed_release_still_rejects_descriptor_tampering(reviewed, field):
    _, _, _, path, _, output = reviewed
    release.publish(pin(path), output)
    value = read_json(output)
    value[field] = {"scope": "global_persona"} if field == "policy" else (
        [] if field == "dependencies" else ["PROFILE:H2"] if field == "selected_hypothesis_ids" else "0" * 64)
    atomic_write_json(output, value)
    with pytest.raises(ValueError): release.load_checked(pin(output))


def test_public_paths_missing_review_and_overwrite_rejected(reviewed, tmp_path):
    _, ref, _, path, _, output = reviewed
    before = files(tmp_path)
    with pytest.raises(ValueError): release.publish(pin(path), PROJECT_ROOT / "forbidden-profile-release.json")
    with pytest.raises(ValueError): release.prepare({"path": str(PROJECT_ROOT / "README.md"), "sha256": "0" * 64}, ["PROFILE:H1"])
    with pytest.raises(FileNotFoundError): release.publish({"path": str(tmp_path / "missing.json"), "sha256": "0" * 64}, output)
    with pytest.raises(ValueError): release.publish(ref, output)  # A candidate is not a review.
    assert files(tmp_path) == before
    result = release.publish(pin(path), output)
    payload = output.read_bytes()
    with pytest.raises(FileExistsError): release.publish(pin(path), output)
    assert output.read_bytes() == payload and pin(output) == result


def test_cli_prepare_publish_validate_read_and_schema(reviewed, capsys, tmp_path):
    _, ref, packet, path, _, output = reviewed
    before = files(tmp_path)
    assert release.main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out) == release.REVIEW_SCHEMA
    assert release.main(["prepare", "--input", ref["path"], "--sha256", ref["sha256"], "--hypothesis", "PROFILE:H1"]) == 0
    assert json.loads(capsys.readouterr().out) == packet and files(tmp_path) == before
    review_ref = pin(path)
    assert release.main(["publish", "--input", review_ref["path"], "--sha256", review_ref["sha256"], "--output", str(output)]) == 0
    created = json.loads(capsys.readouterr().out)["release"]
    args = ["--input", created["path"], "--sha256", created["sha256"]]
    assert release.main(["validate", *args]) == 0
    assert json.loads(capsys.readouterr().out)["selected_hypothesis_ids"] == ["PROFILE:H1"]
    assert release.main(["read", *args]) == 0
    assert json.loads(capsys.readouterr().out)["bundle"] == packet["bundle"]
    assert release.main(["publish", "--input", review_ref["path"], "--sha256", review_ref["sha256"], "--output", str(output)]) == 2
    assert json.loads(capsys.readouterr().out)["error_type"] == "FileExistsError"


def test_cli_loads_only_private_path_environment_and_does_not_print_secrets(tmp_path, monkeypatch, capsys):
    def load(keys):
        assert keys == {"PODCAST_DATA_ROOT", "PIPELINE_DATA_ROOT", "PRIVATE_DOCS_ROOT"}
        monkeypatch.setenv("PRIVATE_DOCS_ROOT", str(tmp_path))
    loader = Mock(side_effect=load)
    monkeypatch.setattr(release, "load_project_dotenv", loader)
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-secret-must-not-print")
    assert release.main(["publish", "--input", "${PRIVATE_DOCS_ROOT}/missing-review.json", "--sha256", "0" * 64,
                         "--output", "${PRIVATE_DOCS_ROOT}/release.json"]) == 2
    loader.assert_called_once()
    output = capsys.readouterr().out
    assert json.loads(output)["error_type"] == "FileNotFoundError"
    assert "synthetic-secret-must-not-print" not in output and str(tmp_path) not in output
    assert not list(tmp_path.iterdir())
