"""Deterministic source-bound profile tests. All observations below are fictional."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge.codex_backend import ModelCallError
from speaker_profile import pipeline
from speaker_profile.__main__ import main
from speaker_profile.schemas import schema
from transcription.common import atomic_write_json, read_json, sha256_file
from transcription.prepare import prepare


@pytest.fixture
def cfg(tmp_path):
    entries = []
    for n in range(1, 5):
        eid = f"SYN{n}"
        raw, draft = tmp_path / f"{eid}-raw.json", tmp_path / f"{eid}-draft.json"
        texts = ["Fictional host: I value time with friends even if the cheaper option takes longer.",
                 "Fictional host: I reported choosing to wait; I have not described the later outcome.",
                 "Fictional programme logistics: the next recording has not been scheduled."]
        atomic_write_json(raw, {"metadata": {"episode_id": eid, "model": "synthetic", "audio_sha256": "a" * 64,
            "language": "en", "audio_duration_seconds": 150,
            "source_episode_metadata": {"episode_id": eid, "publication_date": f"202{n}-01-01"}},
            "segments": [{"segment_id": i, "start": i * 50, "end": i * 50 + 45, "text": text}
                         for i, text in enumerate(texts)]})
        prepare(raw, draft)
        entries.append({"episode_id": eid, "raw": str(raw), "draft": str(draft),
                        "raw_sha256": sha256_file(raw), "draft_sha256": sha256_file(draft),
                        "source_review": None, "source_review_sha256": None, "prior_findings": []})
    value = {"schema_version": "profile_config.v1", "run_id": "synthetic_profile", "output_root": str(tmp_path / "outputs"),
             "model": "synthetic", "reasoning_effort": "high", "timeout_seconds": 20,
             "max_input_chars": 100000, "max_calls": 5, "retry_count": 0,
             "reserved_episode_ids": ["RESERVED1", "RESERVED2"], "episodes": entries}
    path = tmp_path / "config.json"
    atomic_write_json(path, value)
    return pipeline.load_config(path)


def dim(value="unknown"):
    return {"value": value, "reason": "Synthetic proposed judgment, not source approval"}


def item(source, number=1, kind="direct_statement", mid=0):
    segment = source["segments"][mid]
    raw = source["raw_segments"][segment["source_segment_ids"][0]]
    return {"evidence_id": f"{source['episode_id']}:E{number}", "evidence_kind": kind, "speaker_attribution": "host",
            "categories": ["values_and_beliefs", "general_decision_preference"], "domains": ["personal"],
            "merged_ids": [mid], "quotes": [{k: raw[k] for k in ("segment_id", "start", "end", "text")}],
            "source_observation": raw["text"], "interpretation": "Synthetic tentative interpretation" if kind == "inferred_tendency" else None,
            "basis_evidence_ids": [f"{source['episode_id']}:E1"] if kind == "inferred_tendency" else [],
            "decision_context": {"mode": "unknown", "description": "Unspecified fictional context", "event_reference": None, "event_time": None},
            "investment_relevance": {"status": "not_claimed", "explanation": "No investment link claimed"},
            "tradeoff": {"considerations": ["time", "cost", "uncertainty"], "preferred_option": None,
                         "accepted_cost": None, "switch_conditions": []},
            "action": None, "outcome": None, "proposed_salience": dim("high"),
            "conditions": [], "exceptions": [], "uncertainty": ["Event and outcome unknown"], "alternative_explanations": [],
            "supplementary_notes": [{"merged_ids": [mid], "note": "Synthetic analyst caution; not a speaker statement"}],
            "proposed_confidence": {k: dim() for k in ("evidence_fidelity", "attribution_certainty", "source_clarity")}}


def evidence_proposal(source, empty=False):
    rows = [] if empty else [item(source), item(source, 2, "reported_action", 1), item(source, 3, "inferred_tendency")]
    return {"episode_id": source["episode_id"], "summary": "Synthetic behavioral observations",
            "items": rows, "unselected_regions": [{"merged_ids": [0, 1, 2] if empty else [2], "note": "Synthetic logistics or no supported observation"}]}


def hypothesis(support):
    return {"hypothesis_id": "PROFILE:H1", "label": "Synthetic conditional preference", "conditional_description": "Limited to fictional source context",
            "hypothesis_type": "low_frequency_signal", "categories": ["values_and_beliefs"], "domains": ["personal"],
            "supporting_evidence": support, "counterevidence": [], "conditions": [], "exceptions": [], "switch_conditions": [],
            "alternative_explanations": ["Unspecified context may explain the statement"], "falsification_conditions": [],
            "limitations": ["No stable recurrence or event independence established"], "proposed_salience": dim("high"),
            "statement_action_relationships": [], "proposed_confidence": {k: dim() for k in (
                "evidence_fidelity", "attribution_certainty", "context_consistency", "generalization_support")}}


def synthesis_proposal(evidence, support=None):
    ids = [e["evidence_id"] for e in evidence]
    support = ids[:1] if support is None else support
    return {"summary": "Synthetic provisional profile", "hypotheses": [hypothesis(support)] if support else [],
            "evidence_accounting": [{"evidence_id": rid, "disposition": "in_hypotheses" if rid in support else "retained_without_hypothesis",
                                     "hypothesis_ids": ["PROFILE:H1"] if rid in support else [], "reason": "Synthetic retention accounting"} for rid in ids]}


class SyntheticModel:
    def __init__(self, empty=False):
        self.calls, self.contexts, self.empty = [], [], empty

    def complete(self, prompt, context, output_schema):
        self.contexts.append(copy.deepcopy(context))
        stage = "evidence" if "items" in output_schema["properties"] else "synthesis"
        self.calls.append(stage)
        if stage == "evidence":
            source = {**context["source"], "segments": context["merged_segments"],
                      "raw_segments": {s["segment_id"]: s for s in context["raw_segments"]}}
            content = evidence_proposal(source, self.empty)
        else:
            content = synthesis_proposal(context["evidence"])
        return content, {"provider": "synthetic", "model_call_performed": False, "latency_seconds": 0, "usage": None}


def root(cfg):
    return Path(cfg["output_root"]) / cfg["run_id"]


@pytest.fixture
def mapped(cfg):
    source = pipeline._sources(cfg)["SYN1"]
    content = evidence_proposal(source)
    artifact = {"source": pipeline._meta(source), "content": content,
                "grounding": pipeline._derive_evidence(content, source)}
    return source, content, artifact


def test_end_to_end_private_sources_preserved_and_resume_never_infers(cfg, monkeypatch):
    original = {Path(e[k]): Path(e[k]).read_bytes() for e in cfg["episodes"] for k in ("raw", "draft")}
    forbidden = Mock(side_effect=AssertionError("No provider access"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    assert pipeline.run(cfg, dry_run=True)["estimated_new_calls"] == 5
    assert not root(cfg).exists()
    model = SyntheticModel()
    result = pipeline.run(cfg, backend=model)
    assert result["model_calls"] == 5 and model.calls == ["evidence"] * 4 + ["synthesis"]
    assert len(list((root(cfg) / "calls").glob("*.request.json"))) == 5
    assert all(Path(p).read_bytes() == value for p, value in original.items())
    for entry in cfg["episodes"]:
        a = read_json(root(cfg) / entry["episode_id"] / "evidence.json")
        assert a["schema_version"] == "profile.v1" and a["policy"] == pipeline.POLICY
        assert a["source"]["raw_metadata"]["audio_sha256"] == "a" * 64
        assert a["source"]["source_acceptance"] == "not_granted"
    assert all(not (root(cfg) / eid).exists() for eid in cfg["reserved_episode_ids"])
    before = {p: p.read_bytes() for p in root(cfg).rglob("*") if p.is_file()}
    assert pipeline.run(cfg, validate_only=True)["model_calls"] == 0
    assert pipeline.run(cfg)["model_calls"] == 0
    assert {p: p.read_bytes() for p in before} == before
    forbidden.assert_not_called()


def test_synthesis_keeps_every_low_frequency_and_noninvestment_item(cfg):
    model = SyntheticModel()
    pipeline.run(cfg, backend=model)
    a = read_json(root(cfg) / "synthesis.json")
    assert len(a["grounding"]["retained_evidence_ids"]) == 12
    assert len(a["grounding"]["ungrouped_evidence_ids"]) == 11
    stat = a["grounding"]["hypotheses"]["PROFILE:H1"]
    assert stat["low_frequency_status"] == "single_source_group" and stat["independent_event_count"] is None
    assert stat["unknown_event_time_count"] == 1
    assert a["policy"]["usage_assessment"] == {"storage": "retain", "global_profile": "undecided", "dynamic_retrieval": "undecided"}
    assert not a["policy"]["eligible_for_serving"] and not a["policy"]["publication_authorized"]
    assert a["content"]["hypotheses"][0]["proposed_salience"]["value"] == "high"
    assert "knowledge" not in model.contexts[-1] and all(e["domains"] == ["personal"] for e in model.contexts[-1]["evidence"])


def test_zero_items_and_zero_hypotheses_are_valid(cfg):
    result = pipeline.run(cfg, backend=SyntheticModel(empty=True))
    assert result["model_calls"] == 5
    a = read_json(root(cfg) / "synthesis.json")
    assert a["content"]["hypotheses"] == [] and a["grounding"]["retained_evidence_ids"] == []


@pytest.mark.parametrize("fault", ["quote_text", "timestamp", "raw_id", "merged_id", "duplicate_id", "wrong_episode",
                                   "missing_interpretation", "recursive_basis", "foreign_basis", "basis_anchor",
                                   "note_anchor", "commentary_observation", "unaccounted_source", "fake_eligibility"])
def test_invalid_evidence_rejected(mapped, fault):
    source, content, _ = mapped
    row = content["items"][0]
    if fault == "quote_text": row["quotes"][0]["text"] = "Invented quote"
    elif fault == "timestamp": row["quotes"][0]["start"] += 1
    elif fault == "raw_id": row["quotes"][0]["segment_id"] = 999
    elif fault == "merged_id": row["merged_ids"] = [999]
    elif fault == "duplicate_id": content["items"].append(copy.deepcopy(row))
    elif fault == "wrong_episode": row["evidence_id"] = "OTHER:E1"
    elif fault == "missing_interpretation": content["items"][2]["interpretation"] = None
    elif fault == "recursive_basis": content["items"][2]["basis_evidence_ids"] = ["SYN1:E3"]
    elif fault == "foreign_basis": content["items"][2]["basis_evidence_ids"] = ["OTHER:E1"]
    elif fault == "basis_anchor": content["items"][2]["basis_evidence_ids"] = ["SYN1:E2"]
    elif fault == "note_anchor": row["supplementary_notes"][0]["merged_ids"] = [1]
    elif fault == "commentary_observation": row["source_observation"] = "[Agent clarification] Invented motive"
    elif fault == "unaccounted_source": content["unselected_regions"] = []
    else: row["eligible_for_serving"] = True
    with pytest.raises(ValueError): pipeline._derive_evidence(content, source)


def test_exact_separate_regions_and_unknown_event_not_fabricated(mapped):
    source, content, _ = mapped
    row = content["items"][0]
    row["merged_ids"] = [0, 2]
    row["quotes"].append({k: source["raw_segments"][2][k] for k in ("segment_id", "start", "end", "text")})
    resolved = pipeline._derive_evidence(content, source)["items"][row["evidence_id"]]
    assert [r["merged_ids"] for r in resolved["timestamp_regions"]] == [[0], [2]]
    assert row["decision_context"]["event_time"] is row["decision_context"]["event_reference"] is None
    assert resolved["interpretation_authority"] == "model_analyst_not_speaker"
    assert resolved["supplementary_notes_authority"] == "model_analyst_not_speaker"


def test_nullable_tradeoff_action_outcome_and_hypothetical_action_boundary(mapped):
    source, content, _ = mapped
    for row in content["items"]:
        row.update(tradeoff=None, action=None, outcome=None)
    pipeline._derive_evidence(content, source)
    content["items"][1]["decision_context"]["mode"] = "hypothetical"
    with pytest.raises(ValueError, match="hypothetical choice"):
        pipeline._derive_evidence(content, source)


def test_synthesis_shared_review_notes_are_preserved_once_and_references_resolve(mapped):
    source, content, _ = mapped
    raw = source["raw_segments"][0]
    source["annotations_sha256"] = "b" * 64
    source["reviewed_annotations"] = {"sha256": "b" * 64, "review": {"method": "Synthetic shared review method"},
        "annotations": [{"annotation_id": "synthetic:I1", "kind": "uncertainty", "severity": "critical",
                         "note": "Synthetic ambiguity must remain explicit", "anchors": [
                             {"segment_id": 0, "raw_text": raw["text"], "start": raw["start"], "end": raw["end"]}]}]}
    a = {"source": pipeline._meta(source), "content": content, "grounding": pipeline._derive_evidence(content, source)}
    original = copy.deepcopy(a)
    context = pipeline._context("synthesis", None, [a])
    assert a == original
    assert context["sources"][0]["source"]["reviewed_annotations"] == source["reviewed_annotations"]
    g = context["evidence"][0]["grounding"]
    assert g["source_issue_ids"] == g["reviewed_annotation_ids"] == ["synthetic:I1"]
    assert "source_issues" not in g and "reviewed_annotations" not in g
    assert g["annotations_sha256"] == "b" * 64


def test_duplicate_observations_and_inferences_share_source_group(mapped):
    source, content, _ = mapped
    duplicate = copy.deepcopy(content["items"][0]); duplicate["evidence_id"] = "SYN1:E4"
    inferred = copy.deepcopy(content["items"][2]); inferred.update(evidence_id="SYN1:E5", basis_evidence_ids=["SYN1:E4"])
    content["items"].extend([duplicate, inferred])
    artifact = {"source": pipeline._meta(source), "content": content, "grounding": pipeline._derive_evidence(content, source)}
    support = ["SYN1:E1", "SYN1:E3", "SYN1:E4", "SYN1:E5"]
    p = synthesis_proposal(list(pipeline._catalog([artifact]).values()), support)
    stat = pipeline._derive_synthesis(p, [artifact])["hypotheses"]["PROFILE:H1"]
    assert stat["supporting_item_count"] == 4 and stat["underlying_observation_count"] == 2
    assert stat["source_group_count"] == stat["source_region_count"] == 1
    assert stat["low_frequency_status"] == "single_source_group" and stat["independent_event_count"] is None


def test_multiple_regions_do_not_prove_independent_events(mapped):
    _, _, artifact = mapped
    p = synthesis_proposal(list(pipeline._catalog([artifact]).values()), ["SYN1:E1", "SYN1:E2"])
    stat = pipeline._derive_synthesis(p, [artifact])["hypotheses"]["PROFILE:H1"]
    assert stat["source_group_count"] == 2 and stat["low_frequency_status"] == "unknown"
    assert stat["independent_event_count"] is None


@pytest.mark.parametrize("fault", ["missing_item", "duplicate_account", "false_hypothesis", "foreign_support",
                                   "duplicate_support", "false_relationship", "non_host"])
def test_synthesis_references_and_retention_fail_closed(mapped, fault):
    _, _, a = mapped
    p = synthesis_proposal(list(pipeline._catalog([a]).values()))
    if fault == "missing_item": p["evidence_accounting"].pop()
    elif fault == "duplicate_account": p["evidence_accounting"].append(copy.deepcopy(p["evidence_accounting"][0]))
    elif fault == "false_hypothesis": p["evidence_accounting"][0]["hypothesis_ids"] = ["PROFILE:H99"]
    elif fault == "foreign_support": p["hypotheses"][0]["supporting_evidence"] = ["OTHER:E1"]
    elif fault == "duplicate_support": p["hypotheses"][0]["supporting_evidence"] *= 2
    elif fault == "false_relationship": p["hypotheses"][0]["statement_action_relationships"] = [
        {"statement_id": "SYN1:E1", "action_id": "SYN1:E1", "relationship": "agreement", "reason": "Synthetic"}]
    else: a["content"]["items"][0]["speaker_attribution"] = "listener"
    with pytest.raises(ValueError): pipeline._derive_synthesis(p, [a])


def test_bad_relationship_diagnostics_identify_hypotheses_links_and_kinds(mapped):
    _, _, artifact = mapped
    proposal = synthesis_proposal(list(pipeline._catalog([artifact]).values()), ["SYN1:E1", "SYN1:E2", "SYN1:E3"])
    proposal["hypotheses"][0]["statement_action_relationships"] = [
        {"statement_id": "SYN1:E2", "action_id": "SYN1:E1", "relationship": "agreement", "reason": "Synthetic"},
        {"statement_id": "SYN1:E3", "action_id": "SYN1:E2", "relationship": "agreement", "reason": "Synthetic"}]
    with pytest.raises(ValueError) as exc:
        pipeline._derive_synthesis(proposal, [artifact])
    message = str(exc.value)
    assert "expected statement=direct_statement, action=reported_action" in message
    assert "PROFILE:H1[0] statement=SYN1:E2:reported_action action=SYN1:E1:direct_statement" in message
    assert "PROFILE:H1[1] statement=SYN1:E3:inferred_tendency action=SYN1:E2:reported_action" in message


@pytest.mark.parametrize("fault", ["reserved", "raw_pin", "draft_pin", "public_input", "public_output", "extra_input"])
def test_invalid_config_or_source_never_reaches_backend(cfg, fault):
    if fault == "reserved": cfg["episodes"][0]["episode_id"] = "RESERVED1"
    elif fault == "raw_pin": cfg["episodes"][0]["raw_sha256"] = "0" * 64
    elif fault == "draft_pin": cfg["episodes"][0]["draft_sha256"] = "0" * 64
    elif fault == "public_input": cfg["episodes"][0]["raw"] = str(Path(pipeline.__file__).resolve())
    elif fault == "public_output": cfg["output_root"] = str(Path(pipeline.__file__).resolve().parent)
    else: cfg["knowledge_inputs"] = ["Not permitted"]
    model = SyntheticModel()
    with pytest.raises(ValueError): pipeline.run(cfg, backend=model)
    assert not model.calls


def test_reserved_membership_checked_before_source_loading(cfg, monkeypatch):
    cfg["episodes"][0]["episode_id"] = "RESERVED1"
    forbidden = Mock(side_effect=AssertionError("No reserved transcript access"))
    monkeypatch.setattr(pipeline.source_pipeline, "sources", forbidden)
    with pytest.raises(ValueError, match="disjoint"): pipeline.run(cfg, dry_run=True)
    forbidden.assert_not_called()


def test_held_review_and_original_metadata_are_retained(cfg, tmp_path):
    e = cfg["episodes"][0]
    review = tmp_path / "held.json"
    atomic_write_json(review, {"schema_version": "transcript_review.v1", "episode_id": e["episode_id"],
        "transcript_sha256": e["raw_sha256"], "decision": "uncertain", "reviewer": "Synthetic reviewer",
        "reviewed_at": "2026-01-01T00:00:00Z", "method": "Synthetic text-only review", "sampled_segment_ids": [0],
        "unresolved_critical_issues": ["Synthetic unresolved meaning"]})
    e.update(source_review=str(review), source_review_sha256=sha256_file(review))
    model = SyntheticModel()
    pipeline.run(cfg, through="evidence", backend=model)
    assert model.contexts[0]["source"]["prior_review_limitations"] == ["Synthetic unresolved meaning"]
    a = read_json(root(cfg) / e["episode_id"] / "evidence.json")
    assert a["source"]["review_sha256"] == e["source_review_sha256"]
    assert a["policy"]["source_acceptance"] == "not_granted"


@pytest.mark.parametrize("fault", ["model", "retry", "call_cap", "source", "policy", "content", "call_record"])
def test_resume_rejects_changed_configuration_sources_or_artifacts(cfg, fault):
    pipeline.run(cfg, through="evidence", backend=SyntheticModel())
    if fault == "model": cfg["model"] = "different"
    elif fault == "retry": cfg["retry_count"] = 1
    elif fault == "call_cap": cfg["max_calls"] = 10
    elif fault == "source":
        path = Path(cfg["episodes"][0]["raw"])
        value = read_json(path); value["changed"] = True; atomic_write_json(path, value)
    elif fault == "call_record":
        path = next((root(cfg) / "calls").glob("*.result.json"))
        value = read_json(path); value["usage"] = {}; atomic_write_json(path, value)
    else:
        path = root(cfg) / "SYN1/evidence.json"
        value = read_json(path)
        if fault == "policy": value["policy"]["eligible_for_serving"] = True
        else: value["content"]["items"][0]["source_observation"] = "Tampered observation"
        atomic_write_json(path, value)
    model = SyntheticModel()
    with pytest.raises(ValueError): pipeline.run(cfg, backend=model)
    assert not model.calls


def test_repair_is_bounded_and_retains_invalid_response_and_request(cfg):
    cfg.update(retry_count=1, max_calls=6)
    class Repair(SyntheticModel):
        def complete(self, *args):
            value, usage = super().complete(*args)
            if len(self.calls) == 1: value["items"][0]["quotes"][0]["text"] = "Invalid synthetic quote"
            return value, usage
    model = Repair()
    assert pipeline.run(cfg, backend=model)["model_calls"] == 6
    records = [read_json(p) for p in (root(cfg) / "calls").glob("*.result.json")]
    failed = [r for r in records if r["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["error"]["phase"] == "validation"
    assert failed[0]["response"]["items"][0]["quotes"][0]["text"] == "Invalid synthetic quote"
    assert model.contexts[1]["validation_repair"]["previous_response"] == failed[0]["response"]


def test_failed_provider_metadata_retained_without_exception_body_or_retry_loop(cfg):
    cfg.update(retry_count=1, max_calls=10)
    metadata = {"provider": "synthetic", "usage": {"input_tokens": 12}, "latency_seconds": 1}
    backend = Mock()
    backend.complete.side_effect = ModelCallError("secret provider exception body", metadata)
    with pytest.raises(ValueError, match="attempts exhausted"): pipeline.run(cfg, backend=backend)
    assert backend.complete.call_count == 2
    for path in (root(cfg) / "calls").glob("*.result.json"):
        value = read_json(path)
        assert value["usage"] == metadata and value["response"] is None
        assert "secret provider exception body" not in path.read_text()
    with pytest.raises(ValueError, match="attempts exhausted"): pipeline.run(cfg, backend=backend)
    assert backend.complete.call_count == 2


def test_source_change_during_call_retains_error_without_publishing(cfg):
    class Mutate(SyntheticModel):
        def complete(self, *args):
            result = super().complete(*args)
            path = Path(cfg["episodes"][0]["raw"])
            value = read_json(path); value["synthetic_change"] = True; atomic_write_json(path, value)
            return result
    with pytest.raises(ValueError): pipeline.run(cfg, backend=Mutate())
    assert not (root(cfg) / "SYN1/evidence.json").exists()
    record = read_json(next((root(cfg) / "calls").glob("*.result.json")))
    assert record["status"] == "failed" and record["error"]["phase"] == "provenance"


def test_run_budget_is_not_reset_on_resume(cfg):
    cfg["max_calls"] = 1
    model = SyntheticModel()
    for _ in range(2):
        with pytest.raises(ValueError, match="budget exhausted"): pipeline.run(cfg, backend=model)
    assert model.calls == ["evidence"]


def test_missing_artifact_recovers_completed_response_without_new_backend(cfg, monkeypatch):
    pipeline.run(cfg, through="evidence", backend=SyntheticModel())
    (root(cfg) / "SYN1/evidence.json").unlink()
    forbidden = Mock(side_effect=AssertionError("No regeneration of completed response"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    assert pipeline.run(cfg, through="evidence")["model_calls"] == 0
    forbidden.assert_not_called()


def test_input_overflow_never_calls_model_and_is_not_truncated(cfg):
    cfg["max_input_chars"] = 1
    model = SyntheticModel()
    with pytest.raises(ValueError): pipeline.run(cfg, backend=model)
    assert not model.calls
    record = read_json(next((root(cfg) / "calls").glob("*.result.json")))
    assert record["error"]["phase"] == "input"


def test_catalog_binding_and_cli_plan_are_read_only(cfg, tmp_path, monkeypatch, capsys):
    catalog = tmp_path / "catalog.json"
    atomic_write_json(catalog, {"metadata_only": True})
    cfg["catalog"] = {"path": str(catalog), "sha256": sha256_file(catalog)}
    config = tmp_path / "bound_config.json"
    atomic_write_json(config, cfg)
    forbidden = Mock(side_effect=AssertionError("CLI planning must not infer"))
    monkeypatch.setattr(pipeline, "CodexBackend", forbidden)
    assert main(["plan", "--config", str(config), "--through", "evidence"]) == 0
    assert '"model_calls": 0' in capsys.readouterr().out
    assert not root(cfg).exists()
    atomic_write_json(catalog, {"changed": True})
    assert main(["plan", "--config", str(config)]) == 2
    assert "Pinned" not in capsys.readouterr().out  # Console omits even detailed validation text.
    forbidden.assert_not_called()
