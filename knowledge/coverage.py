"""Structural source/topic accounting, not a semantic completeness score."""
from __future__ import annotations


def coverage(stage: str, content: dict, source: dict, episode_map: dict | None = None) -> dict:
    source_ids = {s["merged_id"] for s in source["segments"]}
    if stage == "level1":
        mapped = {i for t in content["threads"] for i in t["merged_ids"]}
        excluded = []
        for region in content["excluded_regions"]:
            excluded.extend(region["merged_ids"])
        if len(excluded) != len(set(excluded)) or mapped & set(excluded):
            raise ValueError("Level 1 exclusions must be unique and separate from mapped threads.")
        if mapped | set(excluded) != source_ids:
            raise ValueError("Level 1 must account for every source segment in threads or explained exclusions.")
        return {"source_segment_count": len(source_ids), "mapped_segment_count": len(mapped),
                "excluded_segment_count": len(excluded), "unaccounted_segment_count": 0}

    threads = {t["thread_id"]: set(t["merged_ids"]) for t in episode_map["threads"]}
    cases = {r["record_id"]: r for r in content["cases"]}
    by_thread = {tid: set() for tid in threads}
    for row in cases.values():
        tids, mids = row["thread_ids"], set(row["merged_ids"])
        if len(tids) != len(set(tids)) or any(t not in threads for t in tids):
            raise ValueError("Level 2 requires unique, resolvable thread references.")
        if (any(not mids & threads[t] for t in tids)
                or not mids <= set().union(*(threads[t] for t in tids))):
            raise ValueError("Level 2 evidence disagrees with its Level 1 threads; revise the map in a new run.")
        for tid in tids:
            by_thread[tid].add(row["record_id"])
    dispositions = content["thread_dispositions"]
    tids = [d["thread_id"] for d in dispositions]
    if len(tids) != len(set(tids)) or set(tids) != set(threads):
        raise ValueError("Level 2 must give exactly one disposition for every Level 1 thread.")
    unextracted, omitted = set(), []
    for item in dispositions:
        tid, refs = item["thread_id"], item["record_ids"]
        if len(refs) != len(set(refs)) or set(refs) != by_thread[tid]:
            raise ValueError("Thread disposition must list exactly the cases linked to that thread.")
        if (item["status"] == "covered") != bool(refs):
            raise ValueError("Covered thread needs cases; a thread without cases needs an explicit omission reason.")
        used = {i for rid in refs for i in cases[rid]["merged_ids"]} & threads[tid]
        skipped = [i for region in item["unextracted_regions"] for i in region["merged_ids"]]
        if (len(skipped) != len(set(skipped)) or used & set(skipped)
                or used | set(skipped) != threads[tid]):
            raise ValueError("Thread regions must be covered by cases or explained unextracted regions, without overlap.")
        unextracted.update(skipped)
        if not refs:
            omitted.append(tid)
    used = {i for r in cases.values() for i in r["merged_ids"]}
    return {"source_segment_count": len(source_ids), "thread_count": len(threads),
            "case_segment_count": len(used), "threads_without_cases": sorted(omitted),
            "unextracted_thread_segment_count": len(unextracted - used),
            "unaccounted_thread_segment_count": 0,
            "scope": "Accounting only; exclusion reasons and topic completeness need source-grounded review."}
