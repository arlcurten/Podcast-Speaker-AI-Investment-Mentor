"""Structural source/topic accounting, not a semantic completeness score."""
from __future__ import annotations

from collections import Counter
import json


def _region_error(issues: list[dict]) -> str:
    prefix = "Thread regions must be covered by cases or explained unextracted regions, without overlap. Merged-ID issues: "
    details = {"threads": issues, "omitted_threads": 0}
    def message():
        return prefix + json.dumps(details, ensure_ascii=True, separators=(",", ":"))
    if len(message()) <= 2000:
        return message()
    # Keep complete JSON inside the pipeline's 2,000-character repair-feedback cap.
    for issue in issues:
        omitted = {}
        for key in ("missing", "overlap", "extra", "duplicate_unextracted"):
            ids = issue.get(key, [])
            if len(ids) > 20:
                omitted[key] = len(ids) - 20
                issue[key] = ids[:20]
        if omitted:
            issue["omitted_id_counts"] = omitted
    while len(message()) > 2000 and issues:
        issues.pop()
        details["omitted_threads"] += 1
    return message()


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
    candidate = source.get("extraction_mode") == "candidate"
    unextracted, omitted, region_errors, partial = set(), [], [], []
    repeated_omission_mentions = 0
    for item in dispositions:
        tid, refs = item["thread_id"], item["record_ids"]
        if len(refs) != len(set(refs)) or set(refs) != by_thread[tid]:
            raise ValueError("Thread disposition must list exactly the cases linked to that thread.")
        if (item["status"] == "covered") != bool(refs):
            raise ValueError("Covered thread needs cases; a thread without cases needs an explicit omission reason.")
        used = {i for rid in refs for i in cases[rid]["merged_ids"]} & threads[tid]
        skipped = [i for region in item["unextracted_regions"] for i in region["merged_ids"]]
        issues = {"missing": threads[tid] - (used | set(skipped)),
                  "extra": set(skipped) - threads[tid]}
        if candidate:
            # A merged region contains several statements. A candidate may use
            # its clear claim while explicitly leaving an ambiguous claim out.
            # These are omission notes, not a disjoint sentence-level partition.
            for region in item["unextracted_regions"]:
                overlap = used & set(region["merged_ids"])
                if overlap:
                    partial.append({"thread_id": tid, "merged_ids": sorted(overlap),
                                    "reason": region["reason"], "authority": "model_reported_omission"})
            repeated_omission_mentions += len(skipped) - len(set(skipped))
        else:
            issues.update(overlap=used & set(skipped),
                          duplicate_unextracted={i for i, count in Counter(skipped).items() if count > 1})
        if any(issues.values()):
            region_errors.append({"thread_id": tid, **{key: sorted(ids) for key, ids in issues.items() if ids}})
        unextracted.update(skipped)
        if not refs:
            omitted.append(tid)
    if region_errors:
        raise ValueError(_region_error(region_errors))
    used = {i for r in cases.values() for i in r["merged_ids"]}
    result = {"source_segment_count": len(source_ids), "thread_count": len(threads),
            "case_segment_count": len(used), "threads_without_cases": sorted(omitted),
            "unextracted_thread_segment_count": len(unextracted - used),
            "unaccounted_thread_segment_count": 0,
            "scope": "Accounting only; exclusion reasons and topic completeness need source-grounded review."}
    if candidate:
        result.update(partial_extraction_notes=partial,
                      repeated_omission_mention_count=repeated_omission_mentions,
                      scope="Candidate region accounting, not claim completeness: source-anchored omission notes "
                            "may overlap used regions or each other. Retained notes require semantic review.")
    return result
