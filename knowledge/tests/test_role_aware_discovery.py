"""Mixed memberships retain all original roles/reasons; all other gates remain."""
import copy
import json

import pytest

from knowledge import corpus_discovery as old, episode_discovery as episode
from knowledge import role_aware_discovery as shared, role_aware_episode_discovery as successor, parallel_discovery
from speaker_profile import corpus_discovery as profile
from transcription.common import atomic_write_json, read_json
from .test_corpus_discovery import cfg, ready, no_provider, pin, FakeBackend


def sample(domain):
    fields = shared.FIELDS[domain]
    records, rid, dispositions, navigation, nid, nav_dispositions = fields
    context = {records: [{rid: "R1", "speaker_attribution": "host"}], navigation: [{nid: "N1"}]}
    family = {"family_id": "F1", "label": "Synthetic conditional family", "conditions": [],
              "accepted_costs": [], "limitations": ["Synthetic only"]}
    family["decision_process" if domain == "knowledge" else "conditional_pattern"] = "Synthetic condition"
    response = {"families": [family], dispositions: [{rid: "R1", "disposition": "assigned", "reason": "Mixed synthetic evidence",
                "memberships": [{"family_id": "F1", "role": "potential_support", "reason": "Synthetic supporting condition"},
                                {"family_id": "F1", "role": "potential_counter", "reason": "Synthetic opposing condition"}]}],
                nav_dispositions: [{nid: "N1", "disposition": "navigation_only", "family_ids": ["F1"], "reason": "Navigation only"}]}
    proposal = old.PROPOSAL if domain == "knowledge" else profile.PROPOSAL
    return context, response, proposal, fields


@pytest.mark.parametrize("domain", ["knowledge", "profile"])
def test_mixed_roles_and_reasons_preserved_exactly(domain):
    context, response, proposal, fields = sample(domain)
    before, source = json.dumps(response), copy.deepcopy(context)
    result = shared.derive(response, context, proposal=proposal, domain=domain)
    assert json.dumps(result) == before and json.dumps(response) == before and context == source
    assert result is not response
    with pytest.raises(ValueError, match="unique supplied family"):
        (old._derive if domain == "knowledge" else profile._derive_plain)(response, context)
    response[fields[2]][0]["memberships"].pop()
    assert shared.derive(response, context, proposal=proposal, domain=domain) == (
        old._derive if domain == "knowledge" else profile._derive_plain)(response, context)


@pytest.mark.parametrize("domain", ["knowledge", "profile"])
@pytest.mark.parametrize("problem", ["same_role", "undefined_family", "invalid_role", "duplicate_family", "bad_family_id",
    "unused_family", "missing_record", "duplicate_record", "unknown_record", "wrong_disposition", "nonhost",
    "missing_navigation", "duplicate_navigation", "unknown_navigation", "duplicate_nav_family", "undefined_nav_family",
    "wrong_nav_disposition"])
def test_only_role_uniqueness_is_relaxed(domain, problem):
    context, value, proposal, fields = sample(domain)
    records, rid, dispositions, navigation, nid, nav_dispositions = fields
    row, nav = value[dispositions][0], value[nav_dispositions][0]
    if problem == "same_role":
        row["memberships"][1]["role"] = "potential_support"
    elif problem == "undefined_family":
        row["memberships"][1]["family_id"] = "F99"
    elif problem == "invalid_role":
        row["memberships"][1]["role"] = "invented_role"
    elif problem == "duplicate_family":
        value["families"].append(copy.deepcopy(value["families"][0]))
    elif problem == "bad_family_id":
        value["families"][0]["family_id"] = "invalid"
    elif problem == "unused_family":
        value["families"].append({**value["families"][0], "family_id": "F2"})
    elif problem == "missing_record":
        value[dispositions] = []
    elif problem == "duplicate_record":
        value[dispositions].append(copy.deepcopy(row))
    elif problem == "unknown_record":
        row[rid] = "unknown"
    elif problem == "wrong_disposition":
        row["disposition"] = "retained_without_family"
    elif problem == "nonhost":
        context[records][0]["speaker_attribution"] = "guest"
        row["memberships"][0]["role"] = "context_only"
    elif problem == "missing_navigation":
        value[nav_dispositions] = []
    elif problem == "duplicate_navigation":
        value[nav_dispositions].append(copy.deepcopy(nav))
    elif problem == "unknown_navigation":
        nav[nid] = "unknown"
    elif problem == "duplicate_nav_family":
        nav["family_ids"].append("F1")
    elif problem == "undefined_nav_family":
        nav["family_ids"] = ["F99"]
    else:
        nav["disposition"] = "retained_without_family"
    with pytest.raises(ValueError):
        shared.derive(value, context, proposal=proposal, domain=domain)


@pytest.mark.parametrize("domain", ["knowledge", "profile"])
def test_nonhost_context_and_uncertain_roles_preserved(domain):
    context, response, proposal, fields = sample(domain)
    context[fields[0]][0]["speaker_attribution"] = "guest"
    for member, role in zip(response[fields[2]][0]["memberships"], ["context_only", "uncertain"]):
        member["role"] = role
    assert shared.derive(response, context, proposal=proposal, domain=domain) == response


def new_config(ready):
    value = read_json(ready["path"])
    value.update(schema_version=successor.CONFIG_VERSION, membership_policy=shared.MEMBERSHIP_POLICY,
                 packet_strategy=episode.planner.STRATEGY, run_id="synthetic_role_successor")
    atomic_write_json(ready["path"], value)
    return pin(ready["path"])


def test_prepare_exact_context_plan_without_old_global_mutation(ready, monkeypatch):
    value = read_json(ready["path"])
    value.update(schema_version=episode.CONFIG_VERSION, packet_strategy=episode.planner.STRATEGY)
    atomic_write_json(ready["path"], value)
    previous = episode._prepare(pin(ready["path"]))
    original_schema, original_impl = copy.deepcopy(episode.CONFIG), episode._implementation
    load = episode.base.inventory.load_checked
    calls = []

    def checked(ref):
        calls.append(ref)
        return load(ref)

    monkeypatch.setattr(episode.base.inventory, "load_checked", checked)
    current = successor._prepare(new_config(ready))
    assert len(calls) == 1 and current[3] == previous[3]
    assert episode.CONFIG == original_schema and episode._implementation is original_impl
    assert current[4]["schema_version"] == successor.RUN_VERSION
    assert current[4]["implementation"]["membership_policy"] == shared.MEMBERSHIP_POLICY
    for packet in current[3]:
        assert successor._materialize(packet, current[2], current[5], current[6], current[0]["max_input_chars"]) == (
            episode._materialize(packet, previous[2], previous[5], previous[6], previous[0]["max_input_chars"]))


def mixed(value, context):
    row = next(row for row in value["case_dispositions"] if row["memberships"])
    row["memberships"].append({**row["memberships"][0], "role": "potential_counter", "reason": "Synthetic opposing condition"})
    return value


def test_knowledge_replay_and_aggregation_keep_mixed_roles(ready):
    ref = new_config(ready)
    result = parallel_discovery.execute(successor, ref, command="run", workers=2,
                                        backend_factory=lambda: FakeBackend(mixed))
    assert result["status"] == "completed"
    assert successor.execute(ref, command="validate")["status"] == "validated"
    cfg, root, *_ = successor._prepare(ref)
    artifact = read_json(root / "discovery.json")
    assert any({"potential_support", "potential_counter"} <= {m["role"] for m in row["memberships"]}
               for row in artifact["case_accounting"].values())
    assert parallel_discovery.execute(successor, ref, command="run", workers=2,
        backend_factory=lambda: FakeBackend(mixed))["model_calls_this_execution"] == 0


def test_versions_are_explicit(ready):
    with pytest.raises(ValueError):
        successor._prepare(pin(ready["path"]))
    ref = new_config(ready)
    with pytest.raises(ValueError):
        episode._prepare(ref)
    value = read_json(ready["path"])
    value["membership_policy"] = "unique_family_id"
    atomic_write_json(ready["path"], value)
    with pytest.raises(ValueError):
        successor._prepare(pin(ready["path"]))
