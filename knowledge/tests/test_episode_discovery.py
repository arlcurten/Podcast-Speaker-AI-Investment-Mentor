"""Knowledge episode adapter integration over synthetic pinned sources only."""
import copy
from pathlib import Path
import threading

import pytest

from knowledge import episode_discovery as episode, parallel_discovery, provenance
from transcription.common import atomic_write_json, read_json
from .test_corpus_discovery import cfg, ready, no_provider, discovery, pin, FakeBackend, response_for


def config(ready, **changes):
    value = copy.deepcopy(ready["config"])
    value.update(schema_version=episode.CONFIG_VERSION, packet_strategy=episode.planner.STRATEGY,
                 run_id="synthetic_knowledge_episode")
    value.update(changes)
    atomic_write_json(ready["path"], value)
    return pin(ready["path"])


def test_exact_closure_coverage_determinism_and_single_inventory_load(ready, monkeypatch):
    ref = config(ready)
    original = discovery.inventory.load_checked
    calls = 0

    def checked(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(discovery.inventory, "load_checked", checked)
    cfg, _, catalog, packets, manifest, pins, prompt = episode._prepare(ref)
    assert calls == 1
    assert manifest["schema_version"] == episode.RUN_VERSION
    assert manifest["implementation"]["packet_strategy"] == episode.planner.STRATEGY
    assert packets == episode._prepare(ref)[3]
    cases = [rid for packet in packets for rid in packet["owned_case_ids"]]
    principles = [pid for packet in packets for pid in packet["owned_principle_ids"]]
    assert len(cases) == len(set(cases)) == len(catalog["cases"])
    assert len(principles) == len(set(principles)) == len(catalog["principles"])
    owners = {pid: min(row["supporting_records"]) for pid, row in catalog["principles"].items()}
    for packet in packets:
        context = episode._materialize(packet, catalog, pins, prompt, cfg["max_input_chars"])
        original_context = discovery._context(packet["owned_case_ids"], catalog,
            discovery._materials(catalog, pins), owners, prompt=prompt, max_chars=cfg["max_input_chars"])
        assert context == original_context
        assert provenance.digest(context) == packet["context_sha256"]
        assert episode._size(prompt, context) <= cfg["max_input_chars"]
        assert context["known_counter_obligations"]
        assert any(not row["parent_eligible_for_level4"] for row in context["known_counter_obligations"])
        assert context["raw_regions"] and context["sources"] and context["bindings"]
        for eid, regions in context["raw_regions"].items():
            path = next(row["raw"] for row in ready["source_cfg"]["episodes"] if row["episode_id"] == eid)
            raw = {row["segment_id"]: row for row in read_json(Path(path))["segments"]}
            assert all(row == {key: raw[row["segment_id"]][key] for key in row} for row in regions)
        assert episode._derive(response_for(context), context) == discovery._derive(response_for(context), context)


def test_parallel_overlap_resume_and_own_plan_validation(ready):
    ref = config(ready)
    barrier = threading.Barrier(2, timeout=10)

    class Model(FakeBackend):
        def complete(self, *args):
            barrier.wait()
            return super().complete(*args)

    result = parallel_discovery.execute(episode, ref, command="run", workers=2, backend_factory=Model)
    assert result["status"] == "completed" and result["model_calls_this_execution"] == 2
    assert episode.execute(ref, command="validate")["status"] == "validated"
    assert parallel_discovery.execute(episode, ref, command="run", workers=2,
                                      backend_factory=Model)["model_calls_this_execution"] == 0
    with pytest.raises(ValueError):
        discovery.execute(ref)


def test_adjacent_episode_merge_and_no_empty_packets(ready):
    cfg, _, catalog, packets, _, pins, prompt = episode._prepare(config(ready, max_input_chars=1000000))
    assert len(packets) == 1
    assert len(packets[0]["owned_case_ids"]) == len(catalog["cases"])
    assert episode._materialize(packets[0], catalog, pins, prompt, cfg["max_input_chars"])


@pytest.mark.parametrize("change", ["missing_case", "duplicate_case", "missing_principle"])
def test_ownership_mismatch_rejected(ready, change):
    cfg, _, catalog, _, _, pins, prompt = episode._prepare(config(ready))
    catalog = copy.deepcopy(catalog)
    first = catalog["episodes"][catalog["expected_episode_ids"][0]]["level2_record_ids"]
    if change == "missing_case":
        first.pop()
    elif change == "duplicate_case":
        first.append(first[0])
    else:
        principle = copy.deepcopy(next(iter(catalog["principles"].values())))
        principle["supporting_records"] = ["unowned"]
        catalog["principles"]["unowned_principle"] = principle
    with pytest.raises(ValueError):
        episode._packets(cfg, catalog, pins, prompt)


def test_old_config_rejected_and_changed_strategy_not_migrated(ready):
    with pytest.raises(ValueError):
        episode.execute(pin(ready["path"]))
    ref = config(ready)
    episode.execute(ref, command="run", backend=FakeBackend(), max_packets=1)
    value = read_json(ready["path"])
    value["packet_strategy"] = "greedy"
    atomic_write_json(ready["path"], value)
    with pytest.raises(ValueError):
        episode.execute(pin(ready["path"]))


def test_changed_implementation_rejected_on_resume(ready, monkeypatch):
    ref = config(ready)
    episode.execute(ref, command="run", backend=FakeBackend(), max_packets=1)
    original = episode._implementation
    monkeypatch.setattr(episode, "_implementation", lambda: {**original(), "episode_planner_sha256": "0" * 64})
    with pytest.raises(ValueError, match="changed"):
        episode.execute(ref)


def test_singleton_overflow_no_admission(ready):
    ref = config(ready, max_input_chars=1)
    with pytest.raises(ValueError, match="Single owned item"):
        episode.execute(ref, command="run", backend=FakeBackend())
    assert not list(Path(ready["config"]["output_root"]).glob("**/*.request.json"))


def test_raw_pin_drift_rejected(ready):
    ref = config(ready)
    atomic_write_json(Path(ready["source_cfg"]["episodes"][0]["raw"]), {"changed": True})
    with pytest.raises(ValueError):
        episode.execute(ref)


@pytest.mark.parametrize("nested", [False, True])
def test_original_output_root_protected(ready, nested):
    source = ready["source_cfg"]
    output = str(Path(source["output_root"]) / source["run_id"]) if nested else source["output_root"]
    ref = config(ready, output_root=output, run_id="nested" if nested else source["run_id"])
    with pytest.raises(ValueError, match="outside extraction|contain an input dependency"):
        episode.execute(ref)


def test_incomplete_inventory_rejected(ready, monkeypatch):
    ref = config(ready)
    load = discovery.inventory.load_checked
    monkeypatch.setattr(discovery.inventory, "load_checked", lambda value: {**load(value), "inventory_complete": False})
    with pytest.raises(ValueError, match="complete explicit inventory"):
        episode.execute(ref)


def test_budget_and_explicit_validation_retry(ready):
    ref = config(ready, max_calls=3)
    result = parallel_discovery.execute(episode, ref, command="run", workers=1, max_packets=1,
                                        backend_factory=lambda: FakeBackend(lambda *_: {}))
    assert result["admitted_calls"] == 1 and result["completed_packets"] == 0
    result = parallel_discovery.execute(episode, ref, command="run", workers=1, backend_factory=FakeBackend)
    assert result["admitted_calls"] == 2 and result["completed_packets"] == 1
    repaired = FakeBackend()
    result = parallel_discovery.execute(episode, ref, command="run", workers=1, retry_failed=True,
                                        backend_factory=lambda: repaired)
    assert result["status"] == "completed" and result["admitted_calls"] == 3
    assert "validation_repair" in repaired.calls[0]
    assert episode.execute(ref, command="validate")["status"] == "validated"


def test_cli_progress_and_nonzero_failed_return(ready, monkeypatch, capsys):
    ref = config(ready)
    execute = parallel_discovery.execute

    def mocked(adapter, config_ref, **kwargs):
        assert adapter.__name__ == "knowledge.episode_discovery"
        return execute(adapter, config_ref, backend_factory=lambda: FakeBackend(lambda *_: {}), **kwargs)

    monkeypatch.setattr(parallel_discovery, "execute", mocked)
    code = episode.main(["run", "--config", ref["path"], "--sha256", ref["sha256"],
                         "--workers", "2", "--progress"])
    assert code == 1
    output = capsys.readouterr()
    assert "inventory_replay_started" in output.err and "episode_packing" in output.err
    assert ref["path"] not in output.err
    assert "failed_needs_explicit_retry" in output.out
