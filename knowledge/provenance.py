"""Source integrity, evidence resolution and conservative principle qualification."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from itertools import combinations
from importlib.metadata import version

from transcription.common import TERMINOLOGY, private_path, read_json, sha256_file
from transcription.prepare import annotate_terms, load_annotation_terms
from transcription.quality import quality_gate


def digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def load_source(raw_path, normalized_path, review_path) -> dict:
    raw_path, normalized_path, review_path = map(private_path, (raw_path, normalized_path, review_path))
    quality = quality_gate(raw_path, review_path=review_path)
    if not quality["ready_for_extraction"]:
        raise ValueError("Source quality is not accepted; review the exact transcript before extraction.")
    raw, normalized = read_json(raw_path), read_json(normalized_path)
    if normalized.get("schema_version") != "transcription_normalized.v1":
        raise ValueError("Unsupported normalized transcript schema.")
    metadata = normalized["metadata"]
    # Accepted raw evidence cannot legitimize an edited normalized view. Verify
    # this deterministic transformation before sending it to an extraction model.
    import opencc
    if (metadata.get("opencc_version") != version("opencc-python-reimplemented")
            or metadata.get("terminology_sha256") != sha256_file(TERMINOLOGY)
            or metadata.get("annotation_matching_version") != "normalized_suggestions.v2"):
        raise ValueError("Normalizer/dictionary version changed; prepare a new normalized version for review.")
    converter = opencc.OpenCC("s2twp")
    terms = load_annotation_terms()
    if (metadata.get("source_raw_sha256") != sha256_file(raw_path)
            or metadata.get("review_sha256") != sha256_file(review_path)
            or metadata.get("episode_id") != quality["episode_id"]):
        raise ValueError("Normalized transcript/source/review version mismatch; prepare a new version.")
    raw_segments = {s["segment_id"]: s for s in raw["segments"]}
    mapped = []
    mids = set()
    for row in normalized["segments"]:
        mid = row["merged_id"]
        if type(mid) is not int or mid < 0 or mid in mids:
            raise ValueError("Invalid or duplicate merged ID.")
        mids.add(mid)
        ids = row["source_segment_ids"]
        if not ids or any(type(i) is not int or i not in raw_segments for i in ids):
            raise ValueError("Unresolved normalized source IDs.")
        originals = [raw_segments[i] for i in ids]
        if (row["raw_text"] != " ".join(s["text"].strip() for s in originals)
                or row["start"] != originals[0]["start"] or row["end"] != max(s["end"] for s in originals)
                or not isinstance(row.get("normalized_text_zh_tw"), str) or not row["normalized_text_zh_tw"].strip()):
            raise ValueError("Normalized evidence text/timestamps disagree with raw mapping.")
        converted = converter.convert(row["raw_text"])
        if (row["normalized_text_zh_tw"] != converted
                or row.get("terminology_annotations") != annotate_terms(row["raw_text"], converted, terms, converter)):
            raise ValueError("Normalized text/annotations differ from the pinned deterministic transformation.")
        mapped.extend(ids)
    if mapped != list(raw_segments):
        raise ValueError("Normalized mapping must cover raw segments exactly once, in source order.")
    return {"episode_id": quality["episode_id"], "raw_sha256": sha256_file(raw_path),
            "normalized_sha256": sha256_file(normalized_path), "review_sha256": sha256_file(review_path),
            "publication_date": raw["metadata"].get("source_episode_metadata", {}).get("publication_date"),
            "segments": normalized["segments"], "raw_segments": raw_segments}


def compact_ids(ids: list[int]) -> dict:
    ids = sorted(set(ids))
    runs, singles = [], []
    for value in ids:
        if runs and value == runs[-1][-1] + 1:
            runs[-1].append(value)
        else:
            runs.append([value])
    ranges = []
    for run in runs:
        if len(run) == 1:
            singles.append(run[0])
        else:
            ranges.append({"start_id": run[0], "end_id": run[-1]})
    return {"source_segment_id_ranges": ranges, "source_segment_ids": singles}


def evidence(merged_ids: list[int], source: dict) -> dict:
    by_id = {s["merged_id"]: s for s in source["segments"]}
    if not merged_ids or len(set(merged_ids)) != len(merged_ids) or any(i not in by_id for i in merged_ids):
        raise ValueError("Unresolved/duplicate evidence merged IDs.")
    segments = [by_id[i] for i in sorted(merged_ids)]
    raw_ids = [i for s in segments for i in s["source_segment_ids"]]
    # Preserve separated regions; a min/max bounding box would imply unseen evidence.
    regions = []
    for row in segments:
        if regions and row["merged_id"] == regions[-1]["merged_ids"][-1] + 1:
            regions[-1]["merged_ids"].append(row["merged_id"])
            regions[-1]["end"] = row["end"]
        else:
            regions.append({"start": row["start"], "end": row["end"], "merged_ids": [row["merged_id"]]})
    return {"episode_id": source["episode_id"], "source_raw_sha256": source["raw_sha256"],
            "source_normalized_sha256": source["normalized_sha256"], "merged_ids": sorted(merged_ids),
            **compact_ids(raw_ids), "timestamp_regions": regions}


def case_catalog(level2_artifacts: list[dict]) -> dict:
    result = {}
    for artifact in level2_artifacts:
        for row in artifact["content"]["cases"]:
            rid = row["record_id"]
            if rid in result:
                raise ValueError("Duplicate global case ID.")
            result[rid] = {**row, "episode_id": artifact["content"]["episode_id"],
                           "publication_date": artifact["source"]["publication_date"],
                           "evidence": artifact["evidence"][rid]}
    return result


def provenance_statistics(ids: list[str], cases: dict, relationships: list[dict] | None = None) -> dict:
    unique = sorted(set(ids))
    if len(unique) != len(ids) or any(i not in cases for i in ids):
        raise ValueError("Supporting cases must be unique resolvable Level 2 IDs.")
    episodes = sorted({cases[i]["episode_id"] for i in ids})
    threads = sorted({t for i in ids for t in cases[i]["thread_ids"]})
    # Episode/date counts do not establish independence. Unassessed/unknown
    # pairs are conservatively connected, as are known repeat source contexts.
    pairs = {tuple(sorted(pair)) for pair in combinations(ids, 2)}
    assessments = {}
    for item in relationships or []:
        pair = tuple(sorted(item["record_ids"]))
        if pair not in pairs or pair in assessments or not item["explanation"].strip():
            raise ValueError("Context relationships need unique supporting-case pairs and an explanation.")
        relation, basis = item["relationship"], item["basis"]
        allowed = {"same_context": {"same_event", "retelling"},
                   "independent": {"distinct_events", "distinct_decision_scenarios"},
                   "unknown": {"insufficient_information"}}
        if basis not in allowed.get(relation, set()):
            raise ValueError("Context relationship and independence basis disagree.")
        if relation == "independent":
            kinds = {cases[i].get("decision_context", {}).get("kind", "unknown") for i in pair}
            expected = {"historical_event"} if basis == "distinct_events" else {"historical_event", "hypothetical_scenario"}
            if not kinds <= expected:
                raise ValueError("Generic/unknown source contexts cannot establish independent decisions.")
        assessments[pair] = item
    if relationships is not None and set(assessments) != pairs:
        raise ValueError("Every supporting-case pair needs an explicit context relationship; unknown is allowed.")
    parent = {i: i for i in ids}
    def find(i):
        while parent[i] != i:
            i = parent[i]
        return i
    overlap_pairs, repeated_pairs, unknown_pairs, overridden_pairs = [], [], [], []
    for n, left in enumerate(ids):
        for right in ids[n + 1:]:
            a, b = cases[left], cases[right]
            same_episode = a["episode_id"] == b["episode_id"]
            overlapping = same_episode and bool(set(a["merged_ids"]) & set(b["merged_ids"]))
            same_thread = same_episode and bool(set(a["thread_ids"]) & set(b["thread_ids"]))
            event_a = a.get("decision_context", {}).get("event_reference")
            event_b = b.get("decision_context", {}).get("event_reference")
            same_event = bool(event_a and event_b and event_a.strip().casefold() == event_b.strip().casefold())
            pair = tuple(sorted((left, right)))
            relation = assessments.get(pair, {}).get("relationship", "unknown")
            if overlapping:
                overlap_pairs.append([left, right])
            if same_event or relation == "same_context":
                repeated_pairs.append([left, right])
            if relation == "unknown":
                unknown_pairs.append([left, right])
            if relation == "independent" and (overlapping or same_thread or same_event):
                overridden_pairs.append([left, right])
            if overlapping or same_thread or same_event or relation != "independent":
                parent[find(left)] = find(right)
    years = sorted({str(cases[i]["publication_date"])[:4] for i in ids
                    if re.match(r"^\d{4}-\d{2}-\d{2}", str(cases[i]["publication_date"] or ""))})
    return {"supporting_record_count": len(unique), "supporting_records": unique,
            "supporting_episode_ids": episodes, "supporting_episode_count": len(episodes),
            "level1_thread_ids": threads, "independent_source_context_count": len({find(i) for i in ids}),
            "source_context_groups": [sorted(i for i in ids if find(i) == root) for root in sorted({find(i) for i in ids})],
            "repeated_context_pairs": repeated_pairs, "unknown_context_pairs": unknown_pairs,
            "overridden_independence_pairs": overridden_pairs,
            "independence_scope": "Conservative components of source constraints and proposed pair assessments; semantic independence requires review.",
            "overlapping_source_pairs": overlap_pairs, "publication_years": years,
            "time_period_scope": "calendar-year coverage only, not verified market-regime diversity"}


def _counterevidence_accounting(p: dict, cases: dict, upstream_l3: dict) -> dict:
    expected = set()
    support = set(p["supporting_records"])
    # Include all overlapping upstream abstractions, even if derived_from names
    # cases directly or an upstream abstraction is ineligible due to conflict.
    for pid, parent in upstream_l3.items():
        if support & set(parent["supporting_records"]):
            for kind, field in (("counterexample", "counterexamples"), ("contradiction", "contradictions")):
                expected.update((pid, row["record_id"], kind) for row in parent[field])
    seen, counts = set(), {"retained": 0, "context_excluded": 0, "unresolved": 0}
    for row in p.get("counterevidence_dispositions", []):
        key = (row["source_principle_id"], row["record_id"], row["kind"])
        if key not in expected or key in seen or row["record_id"] not in cases or not row["reason"].strip():
            raise ValueError("Invalid/duplicate upstream counterevidence disposition or missing reason.")
        seen.add(key)
        disposition = row["disposition"]
        field = "counterexamples" if row["kind"] == "counterexample" else "contradictions"
        retained = row["record_id"] in {r["record_id"] for r in p[field]}
        if disposition not in counts or retained != (disposition != "context_excluded"):
            raise ValueError("Counterevidence disposition disagrees with retained Level 4 evidence.")
        counts[disposition] += 1
    if seen != expected:
        raise ValueError("Known upstream counterevidence needs an explicit retained/excluded/unresolved disposition.")
    return {"upstream_link_count": len(expected), **counts,
            "unique_record_count": len({key[1] for key in expected}),
            "scope": "Link counts are not independent evidence counts; exclusions require semantic review."}


def qualify(principle: dict, cases: dict, stage: str, eligible_l3: dict | None = None,
            upstream_l3: dict | None = None) -> dict:
    p = copy.deepcopy(principle)
    stats = provenance_statistics(p["supporting_records"], cases, p["context_relationships"])
    all_refs = p["supporting_records"] + [x["record_id"] for x in p["counterexamples"] + p["contradictions"]]
    if any(r not in cases for r in all_refs):
        raise ValueError("Unresolved principle evidence/counterexample reference.")
    if any(cases[r]["speaker_attribution"] != "host" for r in p["supporting_records"]):
        raise ValueError("Listener/guest/unknown statements cannot establish host principles.")
    if stage == "level3" and any(cases[r]["episode_id"] != p["episode_id"] for r in all_refs):
        raise ValueError("Level 3 evidence must be from the same episode.")
    counterevidence = {}
    if stage == "level4":
        if stats["supporting_episode_count"] < 2:
            raise ValueError("Level 4 requires at least two independent episodes and contexts.")
        if not p["derived_from"]:
            raise ValueError("Level 4 derived_from provenance is required.")
        for ref in p["derived_from"]:
            if ref in cases:
                if ref not in p["supporting_records"]:
                    raise ValueError("Derived case is not a supporting record.")
            elif ref in (eligible_l3 or {}):
                if not (set(eligible_l3[ref]["supporting_records"]) & set(p["supporting_records"])):
                    raise ValueError("Derived principle has no linked supporting case.")
            else:
                raise ValueError("Level 4 derives from an ineligible or missing abstraction.")
        counterevidence = _counterevidence_accounting(p, cases, upstream_l3 if upstream_l3 is not None else (eligible_l3 or {}))
    reasons = []
    single = stats["supporting_record_count"] == 1
    if single:
        p["principle_type"] = "case_derived_hypothesis"
        reasons.append("Single-case abstraction is a hypothesis, not recurring evidence.")
    hypothesis = p["principle_type"] == "case_derived_hypothesis"
    if hypothesis:
        reasons.append("Hypotheses cannot enter Level 4 derivation or online reasoning, regardless of record count.")
    q = p["proposed_assessment"]
    specificity = q["speaker_specificity"]
    tradeoff = p["decision_tradeoff"]
    incomplete = not (p["conditions"] and p["preferred_actions"] and
                      (tradeoff["switch_conditions"] or p["exceptions"]) and p["falsification_conditions"])
    generic = specificity == "low" or incomplete
    genericness = "too_generic" if specificity == "low" else ("partially_specific" if incomplete else "specific")
    if generic:
        reasons.append("Missing distinctive trade-off, conditions, switch/exception or falsification information.")
    contradiction = q["contradiction_severity"]
    if p["contradictions"] and contradiction == "none":
        contradiction = "minor"
        reasons.append("Contradiction metadata prevents a no-contradiction rating.")
    if counterevidence.get("unresolved"):
        contradiction = "major"
        reasons.append("Unresolved upstream counterevidence blocks online eligibility and lowers confidence.")
    independent = stats["independent_source_context_count"]
    strength = q["evidence_strength"]
    if single or independent < 2:
        strength = "low"
        reasons.append("Independent source-context coverage is below two.")
    elif incomplete or contradiction == "major":
        strength = "low" if contradiction == "major" else min(strength, "medium", key=["low", "medium", "high"].index)
    clarity = q["condition_clarity"] if p["conditions"] else "low"
    overall = "medium" if strength != "low" and specificity != "low" and clarity != "low" and not incomplete and contradiction != "major" else "low"
    # High is deliberately unavailable: raw counts cannot prove semantic
    # consistency or independently verified market-period diversity.
    eligible = not hypothesis and not single and independent >= 2 and not generic and overall == "medium"
    runtime = "principle_assisted" if eligible and stage == "level4" else "not_eligible"
    if not eligible:
        reasons.append("Cases remain available; this abstraction cannot guide runtime answers.")
    confidence = {"evidence_strength": strength, "evidence_independence": "medium" if independent >= 2 else "low",
                  "speaker_specificity": specificity, "condition_clarity": clarity,
                  "contradiction_severity": contradiction,
                  "cross_episode_consistency": "not_applicable" if stage == "level3" else "provisional",
                  "time_period_diversity": "not_applicable" if stage == "level3" else ("multiple_years" if len(stats["publication_years"]) >= 2 else "limited_or_unknown"),
                  "overall": overall}
    return {**p, "provenance": stats, "counterevidence_accounting": counterevidence,
            "confidence": confidence, "provisional": True,
            "generalization_scope": "episode_local" if stage == "level3" else "cross_episode_candidate",
            "eligible_for_level4": eligible if stage == "level3" else False,
            "eligible_for_online_reasoning": eligible and stage == "level4",
            "runtime_mode_recommendation": runtime, "eligibility_reasons": reasons,
            "genericness_status": genericness,
            "qualification_scope": "Provenance constraints are deterministic; specificity/semantic assessments remain model proposals requiring review."}
