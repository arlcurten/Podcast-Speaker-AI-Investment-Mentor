"""Full real adapter contracts over synthetic evidence; no providers."""
import copy
import time

import pytest

from knowledge import parallel_discovery, provenance
from speaker_profile import episode_discovery as episode
from transcription.common import atomic_write_json
from .test_corpus_discovery import cfg, root, ready, no_provider, discovery, pin, SyntheticDiscovery


def config(ready):
    value = copy.deepcopy(ready["config"])
    value.update(schema_version=episode.CONFIG_VERSION, packet_strategy=episode.planner.STRATEGY,
                 run_id="synthetic_episode_transport")
    atomic_write_json(ready["path"], value)
    return pin(ready["path"])


def test_exact_context_coverage_and_deterministic_plan(ready, monkeypatch):
    ref = config(ready)
    load = episode.base.inventory.load_checked
    loads = 0

    def checked(value):
        nonlocal loads
        loads += 1
        return load(value)

    monkeypatch.setattr(episode.base.inventory, "load_checked", checked)
    cfg, _, catalog, packets, manifest, pins, prompt = episode._prepare(ref)
    assert loads == 1
    assert manifest["schema_version"] == episode.RUN_VERSION
    assert manifest["implementation"]["packet_strategy"] == episode.planner.STRATEGY
    assert packets == episode._prepare(ref)[3]
    owned = [rid for packet in packets for rid in packet["owned_evidence_ids"]]
    assert len(owned) == len(set(owned)) == len(catalog["evidence"])
    hypotheses = [key for packet in packets for key in packet["owned_hypothesis_keys"]]
    assert len(hypotheses) == len(set(hypotheses)) == len(catalog["hypotheses"])
    for packet in packets:
        context = episode._materialize(packet, catalog, pins, prompt, cfg["max_input_chars"])
        assert provenance.digest(context) == packet["context_sha256"]
        assert episode._size(prompt, context) <= cfg["max_input_chars"]
        plain = discovery._expand(context)
        closure = discovery.inventory.checked_index_closure(catalog, cfg["inventory"], packet["owned_evidence_ids"])
        assert plain["known_relationship_closure"] == closure
        assert {row["evidence_id"] for row in plain["evidence"]} == set(closure["required_evidence_ids"])
        assert {row["hypothesis_key"] for row in plain["hypotheses"]} == set(closure["matched_hypothesis_keys"])
        assert any(row["grounding"]["supplementary_context"] for row in plain["evidence"])
        assert all(any(row["segment_id"] == 2 for row in rows) for rows in plain["raw_regions"].values())


def test_parallel_run_own_plan_replay_and_old_adapter_rejection(ready):
    ref = config(ready)
    result = parallel_discovery.execute(episode, ref, command="run", workers=2, backend_factory=SyntheticDiscovery)
    assert result["status"] == "completed"
    assert episode.execute(ref, command="validate")["status"] == "validated"
    assert parallel_discovery.execute(episode, ref, command="run", workers=2,
                                      backend_factory=SyntheticDiscovery)["model_calls_this_execution"] == 0
    with pytest.raises(ValueError):
        discovery.execute(ref)


def test_no_old_config_or_changed_strategy_adoption(ready):
    with pytest.raises(ValueError):
        episode.execute(pin(ready["path"]))
    ref = config(ready)
    episode.execute(ref, command="run", backend=SyntheticDiscovery(), max_packets=1)
    value = episode.shared._read(ref, {})
    value["packet_strategy"] = "greedy"
    atomic_write_json(ready["path"], value)
    with pytest.raises(ValueError):
        episode.execute(pin(ready["path"]))


def test_changed_planner_hash_rejected_on_resume(ready, monkeypatch):
    ref = config(ready)
    episode.execute(ref, command="run", backend=SyntheticDiscovery(), max_packets=1)
    original = episode._implementation
    monkeypatch.setattr(episode, "_implementation", lambda: {**original(), "episode_planner_sha256": "0" * 64})
    with pytest.raises(ValueError, match="changed"):
        episode.execute(ref)


def test_source_pins_and_singleton_overflow(ready):
    ref = config(ready)
    value = episode.shared._read(ref, {})
    value["max_input_chars"] = 1
    atomic_write_json(ready["path"], value)
    with pytest.raises(ValueError, match="Single owned item"):
        episode.execute(pin(ready["path"]))
    with pytest.raises(ValueError):
        episode.execute(ref)


def test_real_context_probe_benchmark(ready, monkeypatch):
    context = discovery._context
    calls, cpu = 0, 0.0

    def measured(*args, **kwargs):
        nonlocal calls, cpu
        calls += 1
        started = time.process_time()
        try:
            return context(*args, **kwargs)
        finally:
            cpu += time.process_time() - started

    monkeypatch.setattr(discovery, "_context", measured)
    discovery._prepare(pin(ready["path"]))
    greedy_calls, greedy_cpu = calls, cpu
    calls, cpu = 0, 0.0
    events = []
    episode._prepare(config(ready), progress=lambda *args: events.append(args))
    assert calls < greedy_calls
    assert events[0][0] == "inventory_replay_started" and events[1][0] == "inventory_replay_completed"
    assert events[-1][3] == calls
    print(f"synthetic actual _context: greedy={greedy_calls} calls/{greedy_cpu:.4f}s CPU; "
          f"episode={calls} calls/{cpu:.4f}s CPU (not a full-corpus benchmark)")


def test_cli_parallel_dispatch_and_failure_exit(ready, monkeypatch, capsys):
    ref = config(ready)

    def execute(adapter, config_ref, **kwargs):
        assert adapter.__name__ == "speaker_profile.episode_discovery"
        assert config_ref == ref and kwargs["workers"] == 2
        assert adapter._prepare(ref)[4]["schema_version"] == episode.RUN_VERSION
        return {"status": "failed_needs_explicit_retry"}

    monkeypatch.setattr(parallel_discovery, "execute", execute)
    assert episode.main(["run", "--config", ref["path"], "--sha256", ref["sha256"],
                         "--workers", "2", "--progress"]) == 1
    output = capsys.readouterr()
    assert "inventory_replay_started" in output.err and ref["path"] not in output.err
