"""Synthetic review contracts; no audio or real-source acceptance."""
import copy

import pytest

from transcription.common import PROJECT_ROOT, atomic_write_json, read_json, sha256_file
from transcription.main import main
from transcription.prepare import prepare
from transcription.quality import quality_gate


def annotation_review(raw_path, directory):
    """Original synthetic review metadata, reusable by knowledge contract tests."""
    raw = read_json(raw_path)
    supporting = directory / "evidence" / "review.json"
    atomic_write_json(supporting, {"scope": "Synthetic model-assisted review fixture; no audio was heard."})
    sidecar = directory / "annotations.json"
    data = {
        "schema_version": "transcript_annotations.v1", "episode_id": raw["metadata"]["episode_id"],
        "transcript_sha256": sha256_file(raw_path), "decision": "accept_annotations",
        "reviewer": "synthetic reviewing system", "reviewed_at": "2026-11-08T00:00:00Z",
        "method": "Synthetic model-assisted review; not human listening or corpus approval.",
        "supporting_reviews": [{"path": "evidence/review.json", "sha256": sha256_file(supporting)}],
        "annotations": [{"annotation_id": "SYN:A01", "kind": "interpretive_caution", "severity": "critical",
                         "anchors": [{"segment_id": s["segment_id"], "raw_text": s["text"],
                                      "start": s["start"], "end": s["end"]} for s in raw["segments"][:1]],
                         "note": "Synthetic spoken inconsistency: preserve both quantities; intended wording is not established."}],
    }
    atomic_write_json(sidecar, data)
    review = directory / "review.json"
    atomic_write_json(review, {"schema_version": "transcript_review.v2", "episode_id": data["episode_id"],
                              "transcript_sha256": data["transcript_sha256"], "decision": "accept",
                              "reviewer": "synthetic source reviewer", "reviewed_at": data["reviewed_at"],
                              "method": "Synthetic fixture only; not real source acceptance.",
                              "sampled_segment_ids": [raw["segments"][0]["segment_id"]], "unresolved_critical_issues": [],
                              "annotations": {"path": sidecar.name, "sha256": sha256_file(sidecar)}})
    return review, sidecar, supporting


def repin(review, sidecar):
    data = read_json(review)
    data["annotations"]["sha256"] = sha256_file(sidecar)
    atomic_write_json(review, data)


@pytest.fixture
def annotated(tmp_path):
    raw = tmp_path / "raw.json"
    atomic_write_json(raw, {"metadata": {"episode_id": "SYN", "model": "synthetic", "language": "en",
                                         "audio_duration_seconds": 60},
                            "segments": [
                                {"segment_id": 0, "start": 0.0, "end": 10.0,
                                 "text": "Synthetic speaker says eight units, then eight percent."},
                                {"segment_id": 1, "start": 11.0, "end": 20.0,
                                 "text": "Synthetic listener's quantities are inconsistent."},
                                {"segment_id": 2, "start": 50.0, "end": 60.0,
                                 "text": "Synthetic uncertain homophone; agreement does not resolve meaning."}]})
    return raw, *annotation_review(raw, tmp_path / "reviews")


def test_reviewed_spoken_caution_preserves_text_and_mapping(annotated, tmp_path):
    raw, review, sidecar, supporting = annotated
    before = {p: sha256_file(p) for p in annotated}
    quality = quality_gate(raw, review_path=review)
    assert quality["ready_for_extraction"] and quality["schema_version"] == "transcript_quality.v2"
    assert quality["annotations_sha256"] == sha256_file(sidecar)
    assert quality["unresolved_critical_annotation_count"] == 0
    normalized, draft = tmp_path / "normalized.json", tmp_path / "draft.json"
    prepare(raw, normalized, review)
    prepare(raw, draft)
    result = read_json(normalized)
    assert result["segments"] == read_json(draft)["segments"]
    assert result["segments"][0]["source_segment_ids"] == [0, 1]
    assert result["metadata"]["annotations_sha256"] == sha256_file(sidecar)
    assert result["metadata"]["review_sha256"] == sha256_file(review)
    assert all(sha256_file(p) == checksum for p, checksum in before.items())


def test_v1_keeps_original_behavior_and_output_shape(annotated):
    raw, review, _, _ = annotated
    data = read_json(review)
    data.update(schema_version="transcript_review.v1", annotations={"ignored_legacy_extra": True})
    atomic_write_json(review, data)
    result = quality_gate(raw, review_path=review)
    assert result["ready_for_extraction"] and result["schema_version"] == "transcript_quality.v1"
    assert not any("annotation" in k for k in result)


def test_v2_cannot_omit_its_required_sidecar_reference(annotated):
    raw, review, _, _ = annotated
    data = read_json(review)
    del data["annotations"]
    atomic_write_json(review, data)
    with pytest.raises(ValueError, match="annotation fields"):
        quality_gate(raw, review_path=review)


def test_correction_remains_sidecar_only(annotated, tmp_path):
    raw, review, sidecar, _ = annotated
    before = sha256_file(raw)
    data = read_json(sidecar)
    replacement = "Synthetic separately reviewed reading, never substituted."
    data["annotations"][0].update(kind="transcription_correction", correction=replacement)
    atomic_write_json(sidecar, data)
    repin(review, sidecar)
    normalized, original_view = tmp_path / "normalized.json", tmp_path / "original-view.json"
    prepare(raw, normalized, review)
    prepare(raw, original_view)
    assert read_json(normalized)["segments"] == read_json(original_view)["segments"]
    assert sha256_file(raw) == before
    assert replacement not in normalized.read_text()


@pytest.mark.parametrize("severity,ready", [("critical", False), ("noncritical", True)])
def test_uncertainty_never_becomes_a_correction(annotated, tmp_path, severity, ready):
    raw, review, sidecar, _ = annotated
    data = read_json(sidecar)
    data["annotations"][0].update(kind="uncertainty", severity=severity,
                                  note="Synthetic model repeats an ambiguous homophone; meaning is unresolved.")
    atomic_write_json(sidecar, data)
    repin(review, sidecar)
    assert quality_gate(raw, review_path=review)["ready_for_extraction"] is ready
    result = prepare(raw, tmp_path / "draft.json", review)
    assert result["ready_for_extraction"] is ready


@pytest.mark.parametrize("decision,issues", [("uncertain", []), ("retranscribe", []), ("accept", ["unresolved negation"])])
def test_annotations_never_override_quality_decision(annotated, decision, issues):
    raw, review, _, _ = annotated
    data = read_json(review)
    data.update(decision=decision, unresolved_critical_issues=issues)
    atomic_write_json(review, data)
    assert not quality_gate(raw, review_path=review)["ready_for_extraction"]


@pytest.mark.parametrize("change", ["episode", "raw_hash", "schema", "pending", "method", "extra_field",
                                    "empty_notes", "duplicate_id", "kind", "severity", "blank_note",
                                    "empty_anchors", "unknown_id", "duplicate_anchor", "bool_id", "text",
                                    "start", "bool_end", "caution_correction", "missing_correction",
                                    "conflicting_corrections", "missing_support", "duplicate_support"])
def test_malformed_or_unreviewed_sidecars_rejected(annotated, change):
    raw, review, sidecar, _ = annotated
    data = read_json(sidecar)
    note = data["annotations"][0]
    if change == "episode": data["episode_id"] = "OTHER"
    elif change == "raw_hash": data["transcript_sha256"] = "a" * 64
    elif change == "schema": data["schema_version"] = "unknown"
    elif change == "pending": data["decision"] = "pending"
    elif change == "method": data["method"] = " "
    elif change == "extra_field": data["ready_for_extraction"] = True
    elif change == "empty_notes": data["annotations"] = []
    elif change == "duplicate_id": data["annotations"].append(copy.deepcopy(note))
    elif change == "kind": note["kind"] = "model_agreement"
    elif change == "severity": note["severity"] = "safe"
    elif change == "blank_note": note["note"] = " "
    elif change == "empty_anchors": note["anchors"] = []
    elif change == "unknown_id": note["anchors"][0]["segment_id"] = 999
    elif change == "duplicate_anchor": note["anchors"].append(copy.deepcopy(note["anchors"][0]))
    elif change == "bool_id": note["anchors"][0]["segment_id"] = False
    elif change == "text": note["anchors"][0]["raw_text"] += " "
    elif change == "start": note["anchors"][0]["start"] = 1
    elif change == "bool_end": note["anchors"][0]["end"] = True
    elif change == "caution_correction": note["correction"] = "invented repair"
    elif change == "missing_correction": note["kind"] = "transcription_correction"
    elif change == "conflicting_corrections":
        note.update(kind="transcription_correction", correction="Synthetic first reading")
        other = copy.deepcopy(note)
        other.update(annotation_id="SYN:A02", correction="Synthetic incompatible reading")
        data["annotations"].append(other)
    elif change == "missing_support": data["supporting_reviews"] = []
    else: data["supporting_reviews"] *= 2
    atomic_write_json(sidecar, data)
    repin(review, sidecar)
    with pytest.raises(ValueError):
        quality_gate(raw, review_path=review)


@pytest.mark.parametrize("target", ["sidecar", "supporting"])
@pytest.mark.parametrize("change", ["missing", "changed"])
def test_dependency_hashes_are_enforced(annotated, target, change):
    raw, review, sidecar, supporting = annotated
    path = sidecar if target == "sidecar" else supporting
    if change == "missing": path.unlink()
    else: path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum|Missing"):
        quality_gate(raw, review_path=review)


def test_absolute_references_and_cli_metadata_only(annotated, capsys):
    raw, review, sidecar, supporting = annotated
    data = read_json(sidecar)
    data["supporting_reviews"][0]["path"] = str(supporting)
    data["annotations"][0].update(kind="transcription_correction", correction="Synthetic reviewed reading")
    atomic_write_json(sidecar, data)
    repin(review, sidecar)
    data = read_json(review)
    data["annotations"]["path"] = str(sidecar)
    atomic_write_json(review, data)
    assert main(["validate-transcript", "--input", str(raw), "--review", str(review), "--require-ready"]) == 0
    output = capsys.readouterr().out
    assert '"annotation_count": 1' in output
    assert "Synthetic reviewed reading" not in output and "Synthetic speaker" not in output
    assert str(sidecar.parent) not in output


@pytest.mark.parametrize("target", ["sidecar", "supporting"])
def test_reference_cannot_point_through_symlink_to_public_repo(annotated, tmp_path, target):
    raw, review, sidecar, _ = annotated
    link = tmp_path / "public-link"
    link.symlink_to(PROJECT_ROOT, target_is_directory=True)
    path = link / "README.md"
    if target == "sidecar":
        data = read_json(review)
        data["annotations"] = {"path": str(path), "sha256": sha256_file(path)}
        atomic_write_json(review, data)
    else:
        data = read_json(sidecar)
        data["supporting_reviews"] = [{"path": str(path), "sha256": sha256_file(path)}]
        atomic_write_json(sidecar, data)
        repin(review, sidecar)
    with pytest.raises(ValueError, match="public"):
        quality_gate(raw, review_path=review)


def test_prepare_rechecks_support_before_publishing(annotated, tmp_path, monkeypatch):
    from transcription import prepare as preparation

    raw, review, _, supporting = annotated
    original = preparation.annotate_terms

    def changed(*args):
        result = original(*args)
        supporting.write_bytes(supporting.read_bytes() + b" ")
        return result

    monkeypatch.setattr(preparation, "annotate_terms", changed)
    output = tmp_path / "normalized.json"
    with pytest.raises(ValueError, match="checksum"):
        prepare(raw, output, review)
    assert not output.exists()
