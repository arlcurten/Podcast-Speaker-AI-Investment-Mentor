"""Synthetic annotation propagation and immutable dependency checks."""
import copy
from pathlib import Path

import pytest

from knowledge import pipeline
from knowledge.provenance import evidence
from knowledge.release import load_release, publish
from knowledge.tests.test_pipeline import cfg, FakeModel
from knowledge.tests.test_release import acceptance
from transcription.common import atomic_write_json, read_json, sha256_file
from transcription.prepare import prepare
from transcription.tests.test_annotations import annotation_review, repin


@pytest.fixture
def annotated_cfg(cfg, tmp_path):
    entry = cfg["episodes"][0]
    review, sidecar, supporting = annotation_review(Path(entry["raw"]), tmp_path / "annotations")
    data = read_json(sidecar)
    raw = read_json(Path(entry["raw"]))
    for index, kind in ((1, "transcription_correction"), (2, "uncertainty")):
        segment = raw["segments"][index]
        item = {"annotation_id": f"SYN1:A{index + 1:02d}", "kind": kind, "severity": "noncritical",
                "anchors": [{"segment_id": segment["segment_id"], "raw_text": segment["text"],
                             "start": segment["start"], "end": segment["end"]}],
                "note": "Synthetic ambiguous wording; model agreement is not independent verification."}
        if kind == "transcription_correction":
            item["correction"] = "Synthetic reviewed terminology, not a replacement quote."
        data["annotations"].append(item)
    atomic_write_json(sidecar, data)
    repin(review, sidecar)
    normalized = tmp_path / "annotated-normalized.json"
    prepare(Path(entry["raw"]), normalized, review)
    entry.update(review=str(review), normalized=str(normalized))
    return cfg, sidecar, supporting


class CaptureModel(FakeModel):
    def __init__(self):
        super().__init__()
        self.contexts = []

    def complete(self, prompt, context, schema):
        result = super().complete(prompt, context, schema)
        self.contexts.append((self.calls[-1], copy.deepcopy(context)))
        return result


def test_annotations_reach_all_stages_without_changing_text_or_evidence_counts(annotated_cfg):
    cfg, sidecar, _ = annotated_cfg
    source = pipeline.sources(cfg)["SYN1"]
    checksum = sha256_file(sidecar)
    model = CaptureModel()
    pipeline.run(cfg, backend=model)
    contexts = {stage: context for stage, context in model.contexts
                if context.get("episode", {}).get("episode_id") == "SYN1"}
    for stage in ("level1", "level2"):
        context = contexts[stage]
        assert context["episode"]["annotations_sha256"] == checksum
        assert [row["normalized_text_zh_tw"] for row in context["transcript"]] == [s["normalized_text_zh_tw"] for s in source["segments"]]
        assert [note["merged_ids"] for note in context["reviewed_annotations"]["annotations"]] == [[0], [1], [2]]
        assert "not human listening" in context["reviewed_annotations"]["review"]["method"]
        assert "path" not in context["reviewed_annotations"]["review"]
    l3 = contexts["level3"]["cases"]
    l4 = next(context for stage, context in model.contexts if stage == "level4")["cases"]
    for cases in (l3, l4):
        first = next(c for c in cases if c["record_id"] == "SYN1:R1")
        second = next(c for c in cases if c["record_id"] == "SYN1:R2")
        assert first["evidence"]["reviewed_annotations"]["annotations"][0]["kind"] == "interpretive_caution"
        assert [a["kind"] for a in second["evidence"]["reviewed_annotations"]["annotations"]] == ["transcription_correction", "uncertainty"]
        assert first["evidence"]["source_segment_ids"] == [0]
        assert first["evidence"]["reviewed_annotations"]["sha256"] == checksum
    root = Path(cfg["output_root"]) / cfg["run_id"]
    principle = read_json(root / "cross_episode/level4.json")["qualification"]["CROSS:P1"]
    assert principle["provenance"]["supporting_record_count"] == 2
    assert principle["provenance"]["independent_source_context_count"] == 2
    assert pipeline.run(cfg, backend=model)["model_calls_this_execution"] == 0


def test_anchor_overlap_attaches_complete_note_without_expanding_case_evidence(annotated_cfg):
    cfg, _, _ = annotated_cfg
    source = pipeline.sources(cfg)["SYN1"]
    note = source["reviewed_annotations"]["annotations"][0]
    note["anchors"].append(copy.deepcopy(source["reviewed_annotations"]["annotations"][2]["anchors"][0]))
    result = evidence([0], source)
    assert result["source_segment_ids"] == [0]
    assert result["merged_ids"] == [0]
    assert len(result["reviewed_annotations"]["annotations"][0]["anchors"]) == 2
    result["reviewed_annotations"]["annotations"][0]["note"] = "changed copy"
    assert note["note"] != "changed copy"


@pytest.mark.parametrize("target", ["sidecar", "supporting", "normalized_binding", "critical_uncertainty"])
def test_annotation_failures_block_before_any_model_call(annotated_cfg, target):
    cfg, sidecar, supporting = annotated_cfg
    entry = cfg["episodes"][0]
    if target in {"sidecar", "supporting"}:
        path = sidecar if target == "sidecar" else supporting
        path.write_bytes(path.read_bytes() + b" ")
    elif target == "normalized_binding":
        path = Path(entry["normalized"])
        data = read_json(path)
        del data["metadata"]["annotations_sha256"]
        atomic_write_json(path, data)
    else:
        data = read_json(sidecar)
        data["annotations"][0]["kind"] = "uncertainty"
        atomic_write_json(sidecar, data)
        repin(Path(entry["review"]), sidecar)
    model = FakeModel()
    with pytest.raises(ValueError):
        pipeline.run(cfg, backend=model)
    assert model.calls == []


@pytest.mark.parametrize("target", ["sidecar", "supporting"])
def test_release_preserves_notes_and_revalidates_dependencies(annotated_cfg, tmp_path, target):
    cfg, sidecar, supporting = annotated_cfg
    semantic_review = acceptance(cfg, tmp_path)
    release = tmp_path / "release.json"
    publish(cfg, semantic_review, release)
    cases, _ = load_release(release)
    assert cases["SYN1:R1"]["evidence"]["reviewed_annotations"]["annotations"][0]["kind"] == "interpretive_caution"
    path = sidecar if target == "sidecar" else supporting
    path.unlink()
    with pytest.raises(ValueError, match="Missing"):
        load_release(release)


def test_annotation_revision_is_new_source_and_invalidates_reuse(annotated_cfg, tmp_path):
    cfg, sidecar, supporting = annotated_cfg
    pipeline.run(cfg, backend=FakeModel())
    entry = cfg["episodes"][0]
    old_hashes = {p: sha256_file(p) for p in (sidecar, supporting, Path(entry["review"]), Path(entry["normalized"]))}
    revised_review, revised_notes, _ = annotation_review(Path(entry["raw"]), tmp_path / "revision")
    data = read_json(revised_notes)
    data["annotations"][0]["note"] += " Revised synthetic scope."
    atomic_write_json(revised_notes, data)
    repin(revised_review, revised_notes)
    normalized = tmp_path / "revision" / "normalized.json"
    prepare(Path(entry["raw"]), normalized, revised_review)
    replacement = {**entry, "review": str(revised_review), "normalized": str(normalized)}
    changed = copy.deepcopy(cfg)
    changed["episodes"][0] = replacement
    model = FakeModel()
    with pytest.raises(ValueError, match="incompatible"):
        pipeline.run(changed, backend=model)
    assert not model.calls
    additions, output = tmp_path / "additions.json", tmp_path / "next-config.json"
    atomic_write_json(additions, [replacement])
    result = pipeline.extend(cfg, additions, "annotated_revision", output, replace_existing=True)
    assert result["replaced_episode_ids"] == ["SYN1"]
    assert result["referenced_artifacts"] == 3
    assert result["estimated_new_calls"] == 4
    next_cfg = pipeline.load_config(output)
    pipeline.run(next_cfg, backend=model)
    assert model.calls == ["level1", "level2", "level3", "level4"]
    assert all(sha256_file(p) == checksum for p, checksum in old_hashes.items())
    assert pipeline.run(cfg, validate_only=True)["model_calls_this_execution"] == 0


def test_support_change_during_extraction_never_publishes(annotated_cfg):
    cfg, _, supporting = annotated_cfg

    class ChangedSupport(FakeModel):
        def complete(self, *args):
            result = super().complete(*args)
            supporting.write_bytes(supporting.read_bytes() + b" ")
            return result

    model = ChangedSupport()
    with pytest.raises(ValueError, match="checksum"):
        pipeline.run(cfg, backend=model)
    assert model.calls == ["level1"]
    assert not (Path(cfg["output_root"]) / cfg["run_id"] / "SYN1" / "level1.json").exists()


def test_stored_note_tampering_fails_deterministic_reconstruction(annotated_cfg):
    cfg, _, _ = annotated_cfg
    pipeline.run(cfg, backend=FakeModel())
    artifact = Path(cfg["output_root"]) / cfg["run_id"] / "SYN1" / "level2.json"
    data = read_json(artifact)
    data["evidence"]["SYN1:R1"]["reviewed_annotations"]["annotations"] = []
    atomic_write_json(artifact, data)
    with pytest.raises(ValueError, match="deterministic reconstruction"):
        pipeline.run(cfg, validate_only=True)


def test_v1_sources_do_not_gain_annotation_metadata(cfg):
    source = pipeline.sources(cfg)["SYN1"]
    assert "annotations_sha256" not in pipeline._source_meta(source)
    assert "reviewed_annotations" not in pipeline.context("level1", source, [])
    assert "reviewed_annotations" not in evidence([0], source)
