"""Synthetic receipt/role/navigation tests; no provider calls or real source data."""
import copy
import json
from pathlib import Path

import pytest

from knowledge import postdiscovery_bridge_v1 as bridge, episode_discovery as episode
from knowledge import role_aware_episode_discovery as successor, role_aware_discovery
from knowledge import discovery_runner as runner, provenance
from transcription.common import atomic_write_json, read_json, sha256_file
from .test_corpus_discovery import cfg, ready, no_provider, FakeBackend, pin


def config(ready):
    value = read_json(ready["path"])
    value.update(schema_version=successor.CONFIG_VERSION, membership_policy=role_aware_discovery.MEMBERSHIP_POLICY,
                 packet_strategy=episode.planner.STRATEGY, run_id="synthetic_bridge_successor")
    atomic_write_json(ready["path"], value)
    return pin(ready["path"])


def dual_role(response, _context):
    for row in response["case_dispositions"]:
        if row["memberships"]:
            row["memberships"].append({**row["memberships"][0], "role": "potential_counter",
                                       "reason": "Same fictional source is adverse under a different condition."})
            break
    return response


@pytest.fixture
def completed(ready):
    adapter = successor
    ref = config(ready)
    result = runner.execute(adapter, ref, command="run", backend=FakeBackend(dual_role))
    assert result["status"] == "completed"
    _, root, *_ = adapter._prepare(ref)
    spec = {"adapter": adapter.__name__, "adapter_sha256": sha256_file(Path(adapter.__file__)),
            "run_version": adapter.RUN_VERSION, "request_version": adapter.REQUEST_VERSION,
            "config": ref, "artifact": pin(root / "discovery.json")}
    return bridge.replay_completed(spec, adapter=adapter)


def authored(plan, *, relation="merge_candidate"):
    responses = {}
    for page_id, page in plan["pages"].items():
        keys = [row["family_key"] for row in page["context"]["families"]]
        responses[page_id] = {"page_sha256": page_id, "author": "Synthetic reviewer", "method": "Semantic comparison",
            "response": {"family_dispositions": [{"family_key": key, "status": "assigned", "group_ids": ["G1"],
                                                   "reason": "Compare fictional conditions, not matching words."} for key in keys],
                         "groups": [{"group_id": "G1", "family_keys": keys, "relationship": relation,
                                     "reason": "Review competing actions under different conditions.",
                                     "conditions": ["Fictional bounded context"], "limitations": ["Unreviewed source entailment"]}]}}
    return responses


def navigation(parent):
    value = bridge.project(parent)
    plan = bridge.plan_pages(value, max_families=8, max_input_chars=100000)
    responses = authored(plan)
    selected = bridge.shortlist(value, plan, responses, max_group_families=8, max_groups_per_page=2)
    return value, plan, responses, selected


def test_closed_receipts_and_dual_roles_survive_full_roundtrip(completed):
    value, plan, responses, selected = navigation(completed)
    dual = [(key, rid, rows) for key, family in value["families"].items()
            for rid, rows in family["memberships"].items() if len(rows) > 1]
    assert dual
    for key, rid, rows in dual:
        assert {m["membership"]["role"] for m in rows} == {"potential_support", "potential_counter"}
        assert len({m["accounting_pointer"] for m in rows}) == 2
    assert value["case_accounting"] == completed.artifact["case_accounting"]
    assert value["principle_accounting"] == completed.artifact["principle_accounting"]
    work = bridge.expand_group(completed, value, selected, next(iter(selected["groups"])),
                               plan=plan, responses=responses, max_bytes=10000000)
    assert work["status"] == "prepared_pending_semantic_review"
    assert work["known_counter_obligations"] and work["counter_ledger"]
    assert work["evidence_bundle"]["raw_regions"]
    assert len(work["selected_record_ids"]) == len(set(work["selected_record_ids"]))
    assert len(work["evidence_bundle"]["cases"]) == len(work["required_record_ids"])
    for key, rid, rows in dual:
        assert len(work["role_ledger"][key][rid]) == len(rows)
    assert work["policy"]["publication_authorized"] is False


@pytest.mark.parametrize("field", ["adapter_sha256", "run_version", "request_version", "adapter"])
def test_identity_mismatch_never_casts(completed, field):
    spec = copy.deepcopy(completed.spec)
    spec[field] = "0" * 64 if field == "adapter_sha256" else "unknown_version"
    with pytest.raises(ValueError, match="identity/version"):
        bridge.replay_completed(spec, adapter=completed.adapter)


def test_wrong_canonical_path_rejected(completed, tmp_path):
    other = tmp_path / "copied.json"
    atomic_write_json(other, completed.artifact)
    spec = {**completed.spec, "artifact": pin(other)}
    with pytest.raises(ValueError, match="canonical"):
        bridge.replay_completed(spec, adapter=completed.adapter)


def test_aggregate_tampering_rejected_even_with_new_hash(completed):
    path = Path(completed.spec["artifact"]["path"])
    value = read_json(path)
    value["families"][0]["label"] = "Altered summary"
    atomic_write_json(path, value)
    with pytest.raises(ValueError, match="aggregate"):
        bridge.replay_completed({**completed.spec, "artifact": pin(path)}, adapter=completed.adapter)


def test_receipt_seal_tampering_rejected(completed):
    path = next(completed.root.glob("packet_*/*.result.json"))
    value = read_json(path)
    value["usage"] = {"altered": True}
    atomic_write_json(path, value)
    with pytest.raises(ValueError, match="seal mismatch"):
        bridge.replay_completed(completed.spec, adapter=completed.adapter)


def test_incomplete_run_is_not_a_completed_bridge(ready):
    adapter, ref = successor, config(ready)
    runner.execute(adapter, ref, command="run", backend=FakeBackend(), max_packets=1)
    _, root, *_ = adapter._prepare(ref)
    artifact = root / "discovery.json"
    atomic_write_json(artifact, {"status": "bounded_discovery_complete"})
    spec = {"adapter": adapter.__name__, "adapter_sha256": sha256_file(Path(adapter.__file__)),
            "run_version": adapter.RUN_VERSION, "request_version": adapter.REQUEST_VERSION,
            "config": ref, "artifact": pin(artifact)}
    with pytest.raises(ValueError, match="incomplete"):
        bridge.replay_completed(spec, adapter=adapter)


def test_pending_ledger_keeps_every_family_case_and_parent(completed):
    value = bridge.project(completed)
    plan = bridge.plan_pages(value, max_families=1, max_input_chars=100000)
    responses = authored(plan)
    responses.pop(next(iter(responses)))
    selected = bridge.shortlist(value, plan, responses, max_group_families=1, max_groups_per_page=1)
    assert set(selected["family_ledger"]) == set(value["families"])
    assert set(selected["case_ledger"]) == set(completed.catalog["cases"])
    assert set(selected["principle_ledger"]) == set(completed.catalog["principles"])
    assert set(selected["counter_ledger"]) == set(value["known_counter_obligations"])
    assert sum(len(rows) for fam in selected["role_ledger"].values() for rows in fam.values()) == sum(
        len(rows) for fam in value["families"].values() for rows in fam["memberships"].values())
    assert any(row["state"] == "pending_unread" for row in selected["family_ledger"].values())
    assert any(row["state"] == "pending_ungrouped_review" for row in selected["case_ledger"].values())


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "wrong_page", "too_many", "wrong_group", "outside_page"])
def test_invalid_navigation_rejected(completed, mutation):
    value = bridge.project(completed)
    plan = bridge.plan_pages(value, max_families=1, max_input_chars=100000)
    responses = authored(plan)
    first = next(iter(responses.values()))
    response = first["response"]
    if mutation == "missing":
        response["family_dispositions"].clear()
    elif mutation == "duplicate":
        response["family_dispositions"] *= 2
    elif mutation == "unknown":
        response["groups"][0]["family_keys"] = ["unknown"]
    elif mutation == "wrong_page":
        first["page_sha256"] = "wrong"
    elif mutation == "too_many":
        response["groups"] *= 2
    elif mutation == "wrong_group":
        response["family_dispositions"][0]["group_ids"] = ["missing"]
    else:
        response["groups"][0]["family_keys"] = list(value["families"])
    with pytest.raises(ValueError):
        bridge.shortlist(value, plan, responses, max_group_families=8, max_groups_per_page=1)


def test_semantic_reordering_allows_cross_page_comparison_without_pairs(completed):
    value = bridge.project(completed)
    keys = list(value["families"])
    order = keys[::2] + keys[1::2]
    plan = bridge.plan_pages(value, max_families=2, max_input_chars=100000, family_order=order)
    assert [f["family_key"] for p in plan["pages"].values() for f in p["context"]["families"]] == order
    responses = authored(plan, relation="conditional_difference")
    selected = bridge.shortlist(value, plan, responses, max_group_families=2, max_groups_per_page=1)
    assert len(selected["groups"]) == len(plan["pages"])
    with pytest.raises(ValueError, match="every family"):
        bridge.plan_pages(value, max_families=2, max_input_chars=100000, family_order=order[:-1])


def test_descriptor_and_source_overflow_remain_pending(completed):
    value, plan, responses, selected = navigation(completed)
    tiny = bridge.plan_pages(value, max_families=8, max_input_chars=1)
    assert not tiny["pages"] and len(tiny["pending_oversized"]) == len(value["families"])
    pending = bridge.shortlist(value, tiny, {}, max_group_families=8, max_groups_per_page=1)
    assert all(row["state"] == "pending_oversized" for row in pending["family_ledger"].values())
    work = bridge.expand_group(completed, value, selected, next(iter(selected["groups"])),
                               plan=plan, responses=responses, max_bytes=1)
    assert work["status"] == "pending_oversized" and "evidence_bundle" not in work
    assert work["required_record_ids"] and work["known_counter_obligations"] and work["role_ledger"]


def test_tampered_selection_cannot_expand(completed):
    value, plan, responses, selected = navigation(completed)
    selected["groups"][next(iter(selected["groups"]))]["reason"] = "Invented reviewer verdict"
    with pytest.raises(ValueError, match="exact semantic responses"):
        bridge.expand_group(completed, value, selected, next(iter(selected["groups"])),
                            plan=plan, responses=responses, max_bytes=10000000)


def test_outputs_create_only_private_and_outside_runs(completed, tmp_path):
    output = tmp_path / "new" / "bridge.json"
    bridge._write(completed, str(output), {"synthetic": True})
    with pytest.raises(FileExistsError):
        bridge._write(completed, str(output), {"synthetic": False})
    with pytest.raises(ValueError, match="outside"):
        bridge._write(completed, str(completed.root / "new.json"), {})
    with pytest.raises(ValueError, match="public"):
        bridge._write(completed, str(Path(bridge.__file__).parent / "new.json"), {})


def test_descriptor_planning_does_not_rescan_bridge_per_family(monkeypatch):
    value = {"families": {f"F{i:05}": {"descriptor": {"family_id": f"F{i}"}, "memberships": {},
                                               "known_obligation_ids": []} for i in range(1000)}}
    original, scans = provenance.digest, []

    def counted(obj):
        if obj is value:
            scans.append(1)
        return original(obj)

    monkeypatch.setattr(provenance, "digest", counted)
    plan = bridge.plan_pages(value, max_families=16, max_input_chars=100000)
    assert len(scans) == 1
    assert len(plan["pages"]) == 63
    assert sum(len(p["context"]["families"]) for p in plan["pages"].values()) == 1000


def test_cli_plan_has_no_backend_launch(completed, tmp_path, monkeypatch, capsys):
    specfile = tmp_path / "parent.json"
    atomic_write_json(specfile, completed.spec)
    original = bridge.replay_completed
    monkeypatch.setattr(bridge, "replay_completed", lambda spec: original(spec, adapter=completed.adapter))
    output = tmp_path / "newplan.json"
    assert bridge.main(["plan", "--parent", str(specfile), "--sha256", sha256_file(specfile),
                        "--output", str(output), "--max-families", "8", "--max-input-chars", "100000"]) == 0
    result = read_json(output)
    assert result["shortlist"]["groups"] == {}
    assert json.loads(capsys.readouterr().out)["sha256"] == sha256_file(output)


def test_completion_hook_captures_exact_final_parent(completed):
    spec = completed.spec
    current = bridge.capture_parent(spec["config"], **{key: spec[key] for key in
        ("adapter", "adapter_sha256", "run_version", "request_version")})
    assert current.spec == spec
    assert bridge.project(current) == bridge.project(completed)


def test_completion_hook_missing_aggregate_stays_pending(ready):
    ref = config(ready)
    with pytest.raises(FileNotFoundError):
        bridge.capture_parent(ref, adapter=successor.__name__, adapter_sha256=sha256_file(Path(successor.__file__)),
                              run_version=successor.RUN_VERSION, request_version=successor.REQUEST_VERSION)


def test_new_source_drift_rejected_on_expansion(completed):
    value, plan, responses, selected = navigation(completed)
    artifact = Path(next(iter(completed.catalog["cases"].values()))["artifact"]["path"])
    atomic_write_json(artifact, {"changed": True})
    with pytest.raises(ValueError):
        bridge.expand_group(completed, value, selected, next(iter(selected["groups"])),
                            plan=plan, responses=responses, max_bytes=10000000)
