"""Bounded deterministic transport probes without semantic shortcuts."""
import pytest

from knowledge import episode_packet_planner as planner


class TooLarge(ValueError):
    pass


def planned(groups, cap):
    calls = []

    def build(ids):
        calls.append(list(ids))
        if len(ids) > cap:
            raise TooLarge("full synthetic closure exceeds cap")
        return {"owned": list(ids), "closure": [*ids, "reference-only"]}

    return planner.plan(groups, build=build, describe=lambda ctx: ctx,
                        too_large=TooLarge, owned_key="owned"), calls


@pytest.mark.parametrize("cap", [1, 2, 3, 6, 100])
def test_exact_coverage_determinism_and_cap(cap):
    groups = [["a", "b", "c", "d", "e"], [], ["f"], ["g", "h"]]
    packets, calls = planned(groups, cap)
    assert packets == planned(groups, cap)[0]
    assert [rid for packet in packets for rid in packet["owned"]] == list("abcdefgh")
    assert all(0 < len(p["owned"]) <= cap and p["closure"][-1] == "reference-only" for p in packets)
    assert len(calls) <= 2 * 8 + 3 - 1


def test_full_scale_probe_bound():
    groups = [list(range(index * 36, min((index + 1) * 36, 25325))) for index in range(704)]
    groups = [group for group in groups if group]
    packets, calls = planned(groups, 40)
    assert sum(len(packet["owned"]) for packet in packets) == 25325
    assert len(calls) == 2 * len(groups) - 1
    assert len(calls) < 1500


def test_singleton_overflow_finite_no_truncation():
    with pytest.raises(ValueError, match="Single owned item"):
        planned([["a", "b", "c"]], 0)


def test_duplicates_and_empty_inputs():
    with pytest.raises(ValueError, match="Duplicate"):
        planned([["a"], ["a"]], 10)
    assert planned([[], []], 1) == ([], [])


def test_other_errors_not_treated_as_overflow():
    def build(ids):
        raise OSError("source hash failure")
    with pytest.raises(OSError, match="source hash"):
        planner.plan([["a", "b"]], build=build, describe=lambda x: x,
                     too_large=TooLarge, owned_key="owned")


def test_real_knowledge_context_callback(ready):
    from knowledge import corpus_discovery as base
    from .test_corpus_discovery import pin
    cfg, _, catalog, _, _, pins, prompt = base._prepare(pin(ready["path"]))
    materials = base._materials(catalog, pins)
    owners = {key: min(row["supporting_records"]) for key, row in catalog["principles"].items()}
    groups = [catalog["episodes"][eid]["level2_record_ids"] for eid in catalog["expected_episode_ids"]]

    def build(ids):
        return base._context(ids, catalog, materials, owners, prompt=prompt, max_chars=cfg["max_input_chars"])

    packets = planner.plan(groups, build=build, describe=lambda context: {
        "owned_case_ids": context["owned_case_ids"], "context": context},
        too_large=base._PacketTooLarge, owned_key="owned_case_ids")
    assert {rid for packet in packets for rid in packet["owned_case_ids"]} == set(catalog["cases"])
    for packet in packets:
        assert packet["context"] == build(packet["owned_case_ids"])
        assert packet["context"]["raw_regions"]
        assert base._size(prompt, packet["context"]) <= cfg["max_input_chars"]


from .test_corpus_discovery import cfg, ready, no_provider
