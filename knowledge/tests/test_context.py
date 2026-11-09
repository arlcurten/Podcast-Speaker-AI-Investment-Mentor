"""Compact candidate views preserve reasoning while full provenance stays private."""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from knowledge import pipeline
from knowledge.provenance import case_catalog, digest
from knowledge.schemas import schema
from knowledge.tests.test_annotations import annotated_cfg
from knowledge.tests.test_candidate_extraction import CandidateModel
from knowledge.tests.test_pipeline import cfg
from transcription.common import atomic_write_json, read_json, sha256_file


class ContextModel(CandidateModel):
    def __init__(self):
        super().__init__()
        self.contexts = []

    def complete(self, prompt, context, schema):
        self.contexts.append(copy.deepcopy(context))
        result, usage = super().complete(prompt, context, schema)
        if "source_observations" in result:
            result["source_observations"] = [{"kind": "interpretive_caution", "merged_ids": [0, 1, 2],
                                              "note": result["episode_id"] + ": Synthetic local caution, not speaker evidence."}]
        if "thread_dispositions" in result:
            result["thread_dispositions"][0]["unextracted_regions"] = [
                {"merged_ids": [0], "reason": "Synthetic omitted subclaim in a used region; do not reconstruct it."}]
        return result, usage


@pytest.fixture
def context_data(annotated_cfg):
    settings, _, _ = annotated_cfg
    settings["extraction_mode"] = "candidate"
    report = pipeline.run(settings, through="level3", backend=ContextModel())
    artifacts = [read_json(Path(r["artifact_path"])) for r in report["artifacts"]]
    return settings, pipeline.sources(settings), artifacts, report


def stage_args(stage, source_map, artifacts):
    source = source_map["SYN1"] if stage != "level4" else None
    if stage == "level1":
        upstream = []
    elif stage in {"level2", "level3"}:
        previous = "level1" if stage == "level2" else "level2"
        upstream = [a for a in artifacts if a["stage"] == previous and a["source"]["episode_id"] == "SYN1"]
    else:
        upstream = [a for a in artifacts if a["stage"] in {"level2", "level3"}]
    return source, upstream


@pytest.mark.parametrize("mode, value", [("reviewed", pipeline.COMPACT_CONTEXT), ("candidate", None),
                                        ("candidate", ""), ("candidate", "compact_candidate.v2"),
                                        ("candidate", True), ("candidate", [])])
def test_context_format_is_opt_in_candidate_only(cfg, tmp_path, mode, value):
    cfg.update(extraction_mode=mode, context_format=value)
    path = tmp_path / "context-config.json"
    atomic_write_json(path, cfg)
    with pytest.raises(ValueError, match="context_format"):
        pipeline.load_config(path)
    with pytest.raises(ValueError, match="context_format"):
        pipeline.run(cfg, dry_run=True)


def test_load_config_does_not_add_default_format(cfg, tmp_path):
    path = tmp_path / "context-config.json"
    atomic_write_json(path, cfg)
    assert "context_format" not in pipeline.load_config(path)
    cfg.update(extraction_mode="candidate", context_format=pipeline.COMPACT_CONTEXT)
    atomic_write_json(path, cfg)
    assert pipeline.load_config(path)["context_format"] == pipeline.COMPACT_CONTEXT


@pytest.mark.parametrize("stage", pipeline.STAGES)
def test_default_fingerprint_is_exactly_legacy_and_only_l4_format_separates(context_data, stage):
    settings, sources, artifacts, _ = context_data
    source, upstream = stage_args(stage, sources, artifacts)
    prompt, meta, fingerprint = pipeline.stage_inputs(settings, sources, stage, source, upstream)
    assert fingerprint == digest({"source": meta, "upstream": [digest(a) for a in upstream],
                                  "schema": schema(stage, candidate=True), "prompt": prompt,
                                  "model": settings["model"], "reasoning_effort": settings["reasoning_effort"],
                                  "schema_version": "knowledge_candidate.v1"})
    changed = {**settings, "context_format": pipeline.COMPACT_CONTEXT}
    compact = pipeline.stage_inputs(changed, sources, stage, source, upstream)
    assert compact[:2] == (prompt, meta)
    assert (compact[2] != fingerprint) == (stage == "level4")
    if stage != "level4":
        assert pipeline.context(stage, source, upstream, pipeline.COMPACT_CONTEXT) == pipeline.context(stage, source, upstream)


@pytest.mark.parametrize("stage", ["level3", "level4"])
def test_default_context_is_unchanged(context_data, stage):
    _, sources, artifacts, _ = context_data
    source, upstream = stage_args(stage, sources, artifacts)
    expected = {"cases": list(case_catalog([a for a in upstream if a["stage"] == "level2"]).values())}
    if stage == "level3":
        expected["episode"] = pipeline._source_meta(source)
    else:
        expected["eligible_provisional_principles"] = [
            {**p, **a["qualification"][p["principle_id"]]} for a in upstream if a["stage"] == "level3"
            for p in a["content"]["principles"] if a["qualification"][p["principle_id"]]["eligible_for_level4"]]
        expected["upstream_counterevidence"] = []
    assert pipeline.context(stage, source, upstream) == expected
    assert pipeline.context(stage, source, upstream, None) == expected


def test_compact_retains_every_case_field_anchor_hash_assessment_and_exact_note(context_data):
    stage = "level4"
    _, sources, artifacts, _ = context_data
    source, upstream = stage_args(stage, sources, artifacts)
    before = copy.deepcopy(upstream)
    full = pipeline.context(stage, source, upstream)
    compact = pipeline.context(stage, source, upstream, pipeline.COMPACT_CONTEXT)
    assert upstream == before
    assert compact["context_format"] == pipeline.COMPACT_CONTEXT
    assert len(compact["cases"]) == len(full["cases"])
    originals = {c["record_id"]: c for a in upstream if a["stage"] == "level2" for c in a["content"]["cases"]}
    case_schema = schema("level2", candidate=True)["properties"]["cases"]["items"]
    for old, new in zip(full["cases"], compact["cases"]):
        raw_case = originals[new["record_id"]]
        retained = {key: new[key] for key in raw_case}
        assert retained == raw_case
        Draft202012Validator(case_schema).validate(retained)
        ev = new["evidence"]
        binding = compact["sources"][ev["source_ref"]]["source"]
        assert binding["episode_id"] == old["episode_id"]
        assert binding["raw_sha256"] == old["evidence"]["source_raw_sha256"]
        assert binding["normalized_sha256"] == old["evidence"]["source_normalized_sha256"]
        for key in ("merged_ids", "source_segment_ids", "source_segment_id_ranges", "timestamp_regions", "extraction_mode"):
            assert ev[key] == old["evidence"][key]
        assessment = ev["candidate_source_assessment"]
        prior = old["evidence"]["candidate_source_assessment"]
        assert len(assessment["source_issue_refs"]) == len(prior["source_issues"])
        assert len(assessment["source_issue_dispositions"]) == len(prior["source_issue_dispositions"])
        for key in prior.keys() - {"source_issues", "source_issue_dispositions"}:
            assert assessment[key] == prior[key]
        for issue, ref in zip(prior["source_issues"], assessment["source_issue_refs"]):
            catalog_note = compact["source_issues"][ref]
            assert {key: catalog_note[key] for key in issue} == issue
            assert catalog_note["episode_id"] == old["episode_id"]
        for old_disposition, disposition in zip(prior["source_issue_dispositions"], assessment["source_issue_dispositions"]):
            assert {k: v for k, v in disposition.items() if k != "source_issue_ref"} == old_disposition
            assert disposition["source_issue_ref"] in assessment["source_issue_refs"]
            assert compact["source_issues"][disposition["source_issue_ref"]]["issue_id"] == disposition["issue_id"]
        notes = old["evidence"].get("reviewed_annotations")
        if notes is not None:
            assert "reviewed_annotations" not in ev
            assert len(ev["reviewed_annotation_refs"]) == len(notes["annotations"])
            provenance = compact["sources"][ev["source_ref"]]["annotation_provenance"]
            assert provenance["sha256"] == notes["sha256"]
            assert provenance["supporting_review_sha256"] == notes["review"]["supporting_review_sha256"]
            for note, ref in zip(notes["annotations"], ev["reviewed_annotation_refs"]):
                rendered = compact["source_issues"][ref]
                assert {k: rendered[k] for k in note if k != "anchors"} == {k: v for k, v in note.items() if k != "anchors"}
                assert rendered["anchors"] == [{k: v for k, v in anchor.items() if k != "raw_text"} for anchor in note["anchors"]]
    for artifact in upstream:
        if artifact["stage"] == "level2":
            entry = compact["sources"][artifact["source"]["episode_id"]]
            assert entry["source"] == artifact["source"]
            assert entry["thread_dispositions"] == artifact["content"]["thread_dispositions"]
    payload = json.dumps(compact)
    assert '"raw_text"' not in payload and '"reviewer"' not in payload
    if stage == "level4":
        assert "SYN1/L1:O1" in compact["source_issues"] and "SYN2/L1:O1" in compact["source_issues"]
        assert compact["source_issues"]["SYN1/L1:O1"]["note"] != compact["source_issues"]["SYN2/L1:O1"]["note"]
        assert sum("SYN1/L1:O1" in c["evidence"]["candidate_source_assessment"]["source_issue_refs"] for c in compact["cases"]) == 2


@pytest.mark.parametrize("fault", ["unresolved_disposition", "conflicting_note", "duplicate_issue", "source_hash", "annotation_hash", "reviewed_artifact"])
def test_compact_rejects_invalid_references_or_catalog_collisions(context_data, fault):
    _, sources, artifacts, _ = context_data
    source, upstream = stage_args("level3", sources, artifacts)
    upstream = copy.deepcopy(upstream)
    ev = upstream[0]["evidence"]["SYN1:R1"]
    assessment = ev["candidate_source_assessment"]
    if fault == "unresolved_disposition":
        assessment["source_issue_dispositions"][0]["issue_id"] = "missing"
    elif fault == "conflicting_note":
        next(i for i in assessment["source_issues"] if i["issue_id"] == "L1:O1")["note"] = "A conflicting synthetic note"
    elif fault == "duplicate_issue":
        assessment["source_issues"].append(copy.deepcopy(assessment["source_issues"][0]))
    elif fault == "source_hash":
        ev["source_raw_sha256"] = "0" * 64
    elif fault == "annotation_hash":
        ev["reviewed_annotations"]["sha256"] = "0" * 64
    else:
        upstream[0]["schema_version"] = "knowledge.v2"
    with pytest.raises(ValueError):
        pipeline.context("level4", None, upstream, pipeline.COMPACT_CONTEXT)


def test_compact_retains_ineligible_upstream_counterevidence(context_data):
    _, sources, artifacts, _ = context_data
    _, upstream = stage_args("level4", sources, copy.deepcopy(artifacts))
    l3 = next(a for a in upstream if a["stage"] == "level3")
    principle = l3["content"]["principles"][0]
    principle["counterexamples"] = ["SYN2:R2"]
    principle["contradictions"] = ["SYN1:R2"]
    l3["qualification"][principle["principle_id"]]["eligible_for_level4"] = False
    full = pipeline.context("level4", None, upstream)
    compact = pipeline.context("level4", None, upstream, pipeline.COMPACT_CONTEXT)
    assert compact["eligible_provisional_principles"] == full["eligible_provisional_principles"]
    assert compact["upstream_counterevidence"] == full["upstream_counterevidence"]
    assert compact["upstream_counterevidence"][0]["counterexamples"] == ["SYN2:R2"]
    assert {c["record_id"] for c in compact["cases"]} == {"SYN1:R1", "SYN1:R2", "SYN2:R1", "SYN2:R2"}


@pytest.mark.parametrize("reuse_count", [2, 3])
def test_new_compact_run_reuses_old_stages_and_preserves_full_artifacts(context_data, reuse_count):
    settings, _, _, report = context_data
    changed = copy.deepcopy(settings)
    changed.update(run_id="compact_new", context_format=pipeline.COMPACT_CONTEXT, max_calls=3 if reuse_count == 2 else 1)
    pinned = {Path(r["artifact_path"]): Path(r["artifact_path"]).read_bytes() for r in report["artifacts"]}
    for entry in changed["episodes"]:
        entry["reuse_artifacts"] = {r["stage"]: {"path": r["artifact_path"], "sha256": r["sha256"]}
                                    for r in report["artifacts"] if r["stage"] in pipeline.STAGES[:reuse_count]
                                    and r["path"].startswith(entry["episode_id"] + "/")}
    assert pipeline.run(changed, dry_run=True)["estimated_new_calls"] == changed["max_calls"]
    model = ContextModel()
    result = pipeline.run(changed, backend=model)
    assert model.calls == (["level3", "level3", "level4"] if reuse_count == 2 else ["level4"])
    assert model.contexts[-1]["context_format"] == pipeline.COMPACT_CONTEXT
    assert all("context_format" not in context for context in model.contexts[:-1])
    assert not result["ready_for_serving"]
    root = Path(settings["output_root"]) / changed["run_id"]
    l4 = root / "cross_episode/level4.json"
    assert read_json(l4)["context_format"] == pipeline.COMPACT_CONTEXT
    for item in result["artifacts"]:
        if item["stage"] != "level4":
            assert "context_format" not in read_json(Path(item["artifact_path"]))
    assert all(path.read_bytes() == payload for path, payload in pinned.items())
    assert pipeline.run(changed, validate_only=True)["model_calls_this_execution"] == 0
    without_format = {k: v for k, v in changed.items() if k != "context_format"}
    with pytest.raises(ValueError, match="context format"):
        pipeline.run(without_format, validate_only=True)
    artifact = read_json(l4)
    artifact.pop("context_format")
    atomic_write_json(l4, artifact)
    with pytest.raises(ValueError, match="context format"):
        pipeline.run(changed, validate_only=True)
