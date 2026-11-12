"""Profile successor integration; same compact contexts, mixed-role receipts."""
import copy

import pytest

from knowledge import parallel_discovery, role_aware_discovery as shared
from speaker_profile import episode_discovery as episode, role_aware_episode_discovery as successor
from transcription.common import atomic_write_json, read_json
from .test_corpus_discovery import cfg, root, ready, no_provider, pin, SyntheticDiscovery


def configure(ready):
    value = read_json(ready["path"])
    value.update(schema_version=successor.CONFIG_VERSION, membership_policy=shared.MEMBERSHIP_POLICY,
                 packet_strategy=episode.planner.STRATEGY, run_id="synthetic_profile_role_successor")
    atomic_write_json(ready["path"], value)
    return pin(ready["path"])


def mixed(response, context):
    row = next(row for row in response["evidence_dispositions"] if row["memberships"])
    row["memberships"].append({**row["memberships"][0], "role": "context_only", "reason": "Separate synthetic context role"})
    return response


def test_exact_plan_context_and_untouched_old_globals(ready, monkeypatch):
    value = read_json(ready["path"])
    value.update(schema_version=episode.CONFIG_VERSION, packet_strategy=episode.planner.STRATEGY)
    atomic_write_json(ready["path"], value)
    old = episode._prepare(pin(ready["path"]))
    original_schema, implementation = copy.deepcopy(episode.CONFIG), episode._implementation
    load = episode.base.inventory.load_checked
    calls = []

    def checked(ref):
        calls.append(ref)
        return load(ref)

    monkeypatch.setattr(episode.base.inventory, "load_checked", checked)
    new = successor._prepare(configure(ready))
    assert len(calls) == 1 and new[3] == old[3]
    assert episode.CONFIG == original_schema and episode._implementation is implementation
    assert new[4]["schema_version"] == successor.RUN_VERSION
    for packet in new[3]:
        assert successor._materialize(packet, new[2], new[5], new[6], new[0]["max_input_chars"]) == (
            episode._materialize(packet, old[2], old[5], old[6], old[0]["max_input_chars"]))


def test_profile_mixed_roles_serial_replay_and_aggregation(ready):
    ref = configure(ready)
    result = parallel_discovery.execute(successor, ref, command="run", workers=2,
                                        backend_factory=lambda: SyntheticDiscovery(mixed))
    assert result["status"] == "completed"
    assert successor.execute(ref, command="validate")["status"] == "validated"
    _, output, *_ = successor._prepare(ref)
    artifact = read_json(output / "discovery.json")
    assert any({"uncertain", "context_only"} <= {m["role"] for m in row["memberships"]}
               for row in artifact["evidence_accounting"].values())
    assert parallel_discovery.execute(successor, ref, command="run", workers=2,
        backend_factory=lambda: SyntheticDiscovery(mixed))["model_calls_this_execution"] == 0


def test_cli_plan_success_without_calls(ready, capsys):
    ref = configure(ready)
    assert successor.main(["plan", "--config", ref["path"], "--sha256", ref["sha256"],
                           "--workers", "2", "--progress"]) == 0
    output = capsys.readouterr()
    assert "planned" in output.out and "inventory_replay_started" in output.err
    with pytest.raises(ValueError):
        episode._prepare(ref)
