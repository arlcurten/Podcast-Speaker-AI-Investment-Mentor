from __future__ import annotations

from typing import Any


def compact_id_refs(ids: list[int]) -> dict[str, Any]:
    ordered = sorted({int(x) for x in ids})
    ranges: list[dict[str, int]] = []
    isolated: list[int] = []
    if not ordered:
        return {"source_segment_id_ranges": [], "source_segment_ids": []}

    start = prev = ordered[0]
    for value in ordered[1:]:
        if value == prev + 1:
            prev = value
            continue
        _append_range_or_ids(start, prev, ranges, isolated)
        start = prev = value
    _append_range_or_ids(start, prev, ranges, isolated)
    return {"source_segment_id_ranges": ranges, "source_segment_ids": isolated}


def expand_id_refs(evidence: dict[str, Any]) -> list[int]:
    ids: set[int] = set()
    for item in evidence.get("source_segment_ids") or []:
        if isinstance(item, int):
            ids.add(item)
    for item in evidence.get("source_segment_id_ranges") or []:
        if not isinstance(item, dict):
            continue
        start = item.get("start_id")
        end = item.get("end_id")
        if isinstance(start, int) and isinstance(end, int) and start <= end:
            ids.update(range(start, end + 1))
    return sorted(ids)


def normalize_evidence_refs(obj: Any) -> Any:
    if isinstance(obj, list):
        return [normalize_evidence_refs(item) for item in obj]
    if not isinstance(obj, dict):
        return obj

    normalized = {key: normalize_evidence_refs(value) for key, value in obj.items()}
    if _looks_like_evidence_ref(normalized):
        source_ids = normalized.get("source_segment_ids")
        if not isinstance(source_ids, list):
            source_ids = []
        source_ranges = normalized.get("source_segment_id_ranges")
        if not isinstance(source_ranges, list):
            source_ranges = []
        if len(source_ids) > 20:
            compact = compact_id_refs([int(x) for x in source_ids if isinstance(x, int)])
            source_ranges = [*source_ranges, *compact["source_segment_id_ranges"]]
            source_ids = compact["source_segment_ids"]
        normalized["source_segment_id_ranges"] = source_ranges
        normalized["source_segment_ids"] = source_ids
    return normalized


def _looks_like_evidence_ref(obj: dict[str, Any]) -> bool:
    return any(
        key in obj
        for key in (
            "merged_ids",
            "source_segment_id_ranges",
            "source_segment_ids",
        )
    )


def _append_range_or_ids(
    start: int,
    end: int,
    ranges: list[dict[str, int]],
    isolated: list[int],
) -> None:
    if start == end:
        isolated.append(start)
    else:
        ranges.append({"start_id": start, "end_id": end})
