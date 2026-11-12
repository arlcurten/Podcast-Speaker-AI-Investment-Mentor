"""Domain-neutral transport batching; callbacks retain full semantic closure.

Try each whole episode once; merge adjacent fitting episodes; recursively bisect
an oversized episode in source order. Split chunks are emitted separately. No
monotone-size assumption or maximal-packing claim: compact size is nonmonotone.
Contexts are transient; only caller-produced descriptors survive each probe.
"""
from collections import Counter

STRATEGY = "whole_episode_adjacent_midpoint_split.v1"


def plan(episodes, *, build, describe, too_large, owned_key, progress=None):
    """build(ids) must enforce the exact full-closure cap or raise too_large.

    At most 2*N + E - 1 build calls for N items in E nonempty episodes;
    without splits, at most 2*E - 1. A singleton overflow fails finitely.
    This bounds probes, not CPU/RAM per complete closure construction.
    """
    groups = [list(ids) for ids in episodes if ids]
    expected = [rid for group in groups for rid in group]
    if len(expected) != len(set(expected)):
        raise ValueError("Duplicate owned items in episode plan inputs.")
    packets, pending = [], None
    probes = 0

    def probe(ids):
        nonlocal probes
        probes += 1
        return describe(build(ids))

    def split(ids):
        try:
            descriptor = probe(ids)
        except too_large as exc:
            if len(ids) == 1:
                raise ValueError(f"Single owned item cannot fit complete closure: {ids[0]}; {exc}") from None
            midpoint = len(ids) // 2
            split(ids[:midpoint])
            split(ids[midpoint:])
        else:
            packets.append(descriptor)

    for index, ids in enumerate(groups):
        try:
            current = probe(ids)
        except too_large as exc:
            if pending is not None:
                packets.append(pending)
                pending = None
            if len(ids) == 1:
                raise ValueError(f"Single owned item cannot fit complete closure: {ids[0]}; {exc}") from None
            midpoint = len(ids) // 2
            split(ids[:midpoint])
            split(ids[midpoint:])
        else:
            if pending is None:
                pending = current
            else:
                try:
                    combined = probe([*pending[owned_key], *ids])
                except too_large:
                    packets.append(pending)
                    pending = current
                else:
                    pending = combined
        if progress:
            progress(index + 1, len(groups), probes)
    if pending is not None:
        packets.append(pending)
    actual = [rid for packet in packets for rid in packet[owned_key]]
    if (actual != expected or Counter(actual) != Counter(expected)
            or any(not packet[owned_key] for packet in packets)):
        raise ValueError("Episode planner failed exact ordered ownership coverage.")
    return [{**packet, "packet_id": f"packet_{index:06d}"} for index, packet in enumerate(packets, 1)]
