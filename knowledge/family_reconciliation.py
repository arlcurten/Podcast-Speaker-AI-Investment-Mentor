"""Small bounded source-grounded pair verifier, not full-corpus reconciliation.

CLI: python -m knowledge.family_reconciliation plan|run|validate --config PATH --sha256 HASH
run accepts --max-packets N and --retry-failed. The config uses discovery runtime
settings, replacing inventory with parent={config: REF, artifact: REF}, plus an
explicit max_pairs preflight ceiling (required, no default). The parent
must be an immutable, completed discovery validated by its current implementation.
The shared runner writes discovery.json with a distinct reconciliation schema.

All cross-packet unordered family pairs are streamed, not stored as a matrix.
Each admitted pair costs a full-source call. Planning and every resume reopen all
pairs below max_pairs: quadratic time, not a scalable discovery solution. Memory
includes inventory/family indices and the runner's admitted-receipt history.
One complete pair bundle must fit; oversized closure produces an incomplete plan
with parent/pair references, never truncation or automatic semantic partitioning.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from transcription.common import child_path, private_path, safe_id, sha256_file
from . import case_release, corpus_discovery as discovery, corpus_inventory as inventory
from . import discovery_runner as runner, provenance
from .schemas import TEXT, STRINGS, array, enum, obj

REQUEST_VERSION = "knowledge_reconciliation_request.v1"
RESULT_VERSION = "knowledge_reconciliation_result.v1"
PROMPT_PATH = Path(__file__).parent / "prompts" / "family_reconciliation.md"
RELATIONS = ("same", "conditional_different", "unrelated", "unresolved")
CONFIG = copy.deepcopy(discovery.CONFIG)
CONFIG["properties"].pop("inventory")
CONFIG["required"].remove("inventory")
CONFIG["properties"].update(schema_version=enum("knowledge_reconciliation_config.v1"),
                            parent=obj(config=inventory.REF, artifact=inventory.REF), max_pairs=discovery.POSITIVE)
CONFIG["required"].extend(["parent", "max_pairs"])
ANCHOR = obj(episode_id=TEXT, segment_id={"type": "integer", "minimum": 0},
             start={"type": "number"}, end={"type": "number"}, text=TEXT)
PROPOSAL = obj(family_keys={**STRINGS, "minItems": 2, "maxItems": 2, "uniqueItems": True},
    relationship=enum(*RELATIONS), reason=TEXT, conditions=STRINGS, limitations=STRINGS,
    case_dispositions=array(obj(record_id=TEXT, disposition=enum("relevant", "context_only", "unresolved"),
                                reason=TEXT, source_anchors=array(ANCHOR, 1))),
    counter_dispositions=array(obj(obligation_id=TEXT, disposition=enum("retained", "context_excluded", "unresolved"),
                                   reason=TEXT, source_anchors=array(ANCHOR, 1))))
POLICY = {**discovery.POLICY, "scope": "cross_packet_pairwise_navigation_review_only",
          "execution_scope": "small_explicitly_capped_full_source_pair_verifier_not_full_corpus_scaling",
          "cross_packet_reconciliation": "pairwise_proposals_not_transitive_merges",
          "global_counter_review": "not_performed", "ungrouped_evidence_sweep": "not_performed",
          "known_counter_scope": "Inventory L3 links overlapping original family memberships; reference cases are not new supports.",
          "independent_event_count": None, "principle_construction": "not_performed"}
_check_pins = case_release._check_pins


def _implementation():
    return {"reconciliation_sha256": sha256_file(Path(__file__)), "prompt_sha256": sha256_file(PROMPT_PATH),
            "runner_sha256": sha256_file(Path(runner.__file__)), "discovery": discovery._implementation(),
            "inventory": inventory._implementation(),
            "proposal_sha256": provenance.digest(PROPOSAL)}


def _size(prompt, context):
    return runner.input_size(prompt, context, PROPOSAL)


class PairSchedule:
    """Reiterable constant-pair-memory schedule; no transitive union operation."""

    def __init__(self, families):
        self.families = families
        self.ids = sorted(families)
        counts = Counter(f["packet_id"] for f in families.values())
        self.count = (len(self.ids) ** 2 - sum(n ** 2 for n in counts.values())) // 2

    def __len__(self):
        return self.count

    def __iter__(self):
        number = 0
        for index, left in enumerate(self.ids):
            for right_index in range(index + 1, len(self.ids)):
                right = self.ids[right_index]
                if self.families[left]["packet_id"] == self.families[right]["packet_id"]:
                    continue
                number += 1
                yield {"packet_id": f"packet_{number:09d}", "family_keys": [left, right]}


class IncompletePlan(ValueError):
    def __init__(self, result):
        self.result = result
        super().__init__(result["status"])


def _context(packet, catalog, pins, prompt, cap, materials=None):
    selected = [catalog["families"][key] for key in packet["family_keys"]]
    ids = sorted({rid for family in selected for rid in family["memberships"]})
    source_catalog = catalog["inventory"]
    # Rebase the inner size guard; the outer pair envelope only adds characters.
    compact = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    source_cap = (cap + discovery._size("", {}) - _size(prompt, {})
                  + len(compact(discovery.POLICY)) - len(compact(POLICY)))
    try:
        bundle = discovery._context(ids, source_catalog, materials or discovery._materials(source_catalog, pins), {},
                                    prompt="", max_chars=source_cap)
    except discovery._PacketTooLarge:
        raise discovery._PacketTooLarge(f"Complete source bundle exceeds reconciliation input cap={cap}, "
            "including its own prompt/schema/policy. Retain all closure references; no truncation.") from None
    bundle["policy"] = copy.deepcopy(POLICY)
    bundle["known_counter_obligations"] = [
        {"obligation_id": f"counter_{index:06d}", **row}
        for index, row in enumerate(bundle["known_counter_obligations"], 1)]
    context = {"parent": catalog["parent_ref"], "family_keys": packet["family_keys"],
               "families": copy.deepcopy(selected), "evidence_bundle": bundle,
               "authority": "model_navigation_comparison_not_source_evidence", "policy": copy.deepcopy(POLICY)}
    if (size := _size(prompt, context)) > cap:
        raise discovery._PacketTooLarge(f"Complete pair requires {size} input characters; cap={cap}. "
                                       "Retain references; use a reviewed new limit/run or separately designed dependent comparisons.")
    return context


def _materialize(packet, catalog, pins, prompt, max_chars):
    return _context(packet, catalog, pins, prompt, max_chars)


def _prepare(config_ref):
    pins = {}
    config_ref = inventory._ref(config_ref)
    cfg = inventory._read(config_ref, pins)
    discovery._validate(cfg, CONFIG)
    implementation, prompt = _implementation(), PROMPT_PATH.read_text(encoding="utf-8")
    safe_id(cfg["run_id"])
    cfg = {**cfg, "output_root": str(private_path(cfg["output_root"])),
           "parent": {key: inventory._ref(value) for key, value in cfg["parent"].items()}}
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    parent_ref = cfg["parent"]["artifact"]
    parent = inventory._read(parent_ref, pins)
    # Validate closed requests/results, exact accounting and source pins without inference.
    _, parent_root, source_catalog, parent_packets, parent_manifest, parent_pins, parent_prompt = discovery._prepare(cfg["parent"]["config"])
    if Path(parent_ref["path"]) != parent_root / "discovery.json":
        raise ValueError("Parent artifact is outside its pinned completed discovery run.")
    histories, _ = discovery._history(parent_root, parent_manifest, parent_packets, parent_prompt, source_catalog, parent_pins)
    if any(not histories.get(p["packet_id"]) or histories[p["packet_id"]][-1]["status"] != "completed" for p in parent_packets):
        raise ValueError("Reconciliation requires completed discovery, not partial or interrupted packets.")
    if provenance.digest(parent) != provenance.digest(discovery._aggregate(source_catalog, parent_packets, histories, parent_manifest)):
        raise ValueError("Parent discovery differs from independently reconstructed receipts.")
    for path, checksum in parent_pins.items():
        inventory._pin_file({"path": str(path), "sha256": checksum}, pins)
    protected = [parent_root]
    for eid, entry in source_catalog["episodes"].items():
        original = case_release._snapshot(entry["config"]["path"], pins, entry["config"]["sha256"], yaml_config=True)
        protected.append(child_path(private_path(original["output_root"]), original["run_id"]))
        for state in entry["stages"].values():
            folder = Path(state["artifact"]["path"]).parent
            protected.append(folder.parent if folder.name == eid else folder)
    if any(root.is_relative_to(path) for path in protected) or any(path.is_relative_to(root) for path in pins):
        raise ValueError("Reconciliation output must not overlap parent runs or pinned inputs.")
    families = {}
    for family in parent["families"]:
        fid = family["family_id"]
        key = parent_ref["sha256"] + ":" + fid
        families[key] = {**family, "family_key": key,
                         "authority": "parent_discovery_routing_commentary_not_evidence", "memberships": {}}
    by_id = {f["family_id"]: f for f in families.values()}
    for rid, row in parent["case_accounting"].items():
        for member in row["memberships"]:
            by_id[member["family_id"]]["memberships"][rid] = copy.deepcopy(member)
    packets = PairSchedule(families)
    catalog = {"families": families, "parent": parent, "parent_ref": parent_ref, "inventory": source_catalog,
               "pair_count": len(packets), "max_pairs": cfg["max_pairs"]}
    schedule = {"kind": "all_unordered_cross_packet_pairs.v1", "family_order": sorted(families),
                "parent": parent_ref, "pair_count": len(packets), "max_pairs": cfg["max_pairs"],
                "planned_first_attempt_calls": len(packets), "potential_calls_with_retries": len(packets) * (cfg["retry_count"] + 1),
                "lifetime_max_calls": cfg["max_calls"], "first_pass_fits_call_budget": len(packets) <= cfg["max_calls"],
                "cost_policy": "One full-source call per pair; every plan/resume streams all declared pairs. Small-scope verifier only."}
    if len(packets) > cfg["max_pairs"]:
        _check_pins(pins)
        if _implementation() != implementation:
            raise ValueError("Reconciliation implementation changed during planning.")
        raise IncompletePlan({"status": "incomplete_pair_limit", "packet_count": len(packets),
            "packet_plan": schedule, "parent": cfg["parent"], "inventory": parent["inventory"],
            **_counts(catalog), "max_calls": cfg["max_calls"], "model_calls_this_execution": 0,
            "policy": copy.deepcopy(POLICY)})
    digest, largest, blocked, first_blocked = hashlib.sha256(), 0, 0, None
    materials = discovery._materials(source_catalog, pins)
    for packet in packets:
        try:
            context = _context(packet, catalog, pins, prompt, cfg["max_input_chars"], materials)
        except discovery._PacketTooLarge as exc:
            blocked += 1
            if first_blocked is None:
                members = {rid for key in packet["family_keys"] for rid in families[key]["memberships"]}
                required = set(members)
                for principle in source_catalog["principles"].values():
                    if members.intersection(principle["supporting_records"]):
                        required.update(principle["supporting_records"])
                        required.update(row["record_id"] for field in ("counterexamples", "contradictions")
                                        for row in principle[field])
                first_blocked = {**packet, "member_case_ids": sorted(members), "required_case_ids": sorted(required),
                                 "detail": str(exc)}
        else:
            size = _size(prompt, context)
            largest = max(largest, size)
            digest.update((provenance.digest({**packet, "context_sha256": provenance.digest(context),
                                              "input_chars": size}) + "\n").encode("ascii"))
            del context
    _check_pins(pins)
    if _implementation() != implementation:
        raise ValueError("Reconciliation implementation changed during planning.")
    schedule.update(max_input_chars_observed=largest, ordered_context_digest=digest.hexdigest() if not blocked else None)
    if blocked:
        raise IncompletePlan({"status": "incomplete_input_overflow", "packet_count": len(packets),
            "blocked_pair_count": blocked, "first_blocked_pair": first_blocked, "packet_plan": schedule,
            "parent": cfg["parent"], "inventory": parent["inventory"], **_counts(catalog),
            "max_calls": cfg["max_calls"], "model_calls_this_execution": 0, "policy": copy.deepcopy(POLICY)})
    manifest = {"schema_version": "knowledge_reconciliation_run.v1", "config": cfg, "config_ref": config_ref,
                "implementation": implementation, "policy": copy.deepcopy(POLICY), "packets": schedule}
    return cfg, root, catalog, packets, manifest, pins, prompt


def _anchors(anchors, record_id, bundle):
    case = next(row for row in bundle["cases"] if row["record_id"] == record_id)
    evidence = case["evidence"]
    allowed = set(evidence["source_segment_ids"])
    for region in evidence["source_segment_id_ranges"]:
        allowed.update(range(region["start_id"], region["end_id"] + 1))
    raw = {row["segment_id"]: row for row in bundle["raw_regions"][case["episode_id"]]}
    seen = set()
    for anchor in anchors:
        sid = anchor["segment_id"]
        if (type(sid) is not int or sid in seen or anchor["episode_id"] != case["episode_id"]
                or sid not in allowed or sid not in raw
                or any(anchor[key] != raw[sid][key] for key in ("start", "end", "text"))):
            raise ValueError("Reconciliation anchors must exactly match the cited case's mapped raw region.")
        seen.add(sid)


def _derive(response, context):
    discovery._validate(response, PROPOSAL)
    if response["family_keys"] != context["family_keys"]:
        raise ValueError("Comparison must name the exact ordered versioned family pair.")
    if response["relationship"] == "conditional_different" and not response["conditions"]:
        raise ValueError("Conditional differences require explicit conditions.")
    if response["relationship"] == "unresolved" and not response["limitations"]:
        raise ValueError("Unresolved comparisons require an explicit limitation.")
    bundle, unresolved = context["evidence_bundle"], False
    for field, expected, identity in (
        ("case_dispositions", {r["record_id"]: r["record_id"] for r in bundle["cases"]}, "record_id"),
        ("counter_dispositions", {r["obligation_id"]: r["record_id"] for r in bundle["known_counter_obligations"]}, "obligation_id")):
        seen = set()
        for row in response[field]:
            key = row[identity]
            if key not in expected or key in seen:
                raise ValueError("Every reopened case and known counter needs one exact disposition.")
            seen.add(key)
            _anchors(row["source_anchors"], expected[key], bundle)
            unresolved |= row["disposition"] == "unresolved"
        if seen != expected.keys():
            raise ValueError("Missing reopened case or known-counter disposition.")
    if unresolved and response["relationship"] != "unresolved":
        raise ValueError("Unresolved source/counter dispositions cannot yield a settled pair relation.")
    return copy.deepcopy(response)


def _counts(catalog):
    return {"family_count": len(catalog["families"]), "max_pairs": catalog["max_pairs"],
            "planned_first_attempt_calls": catalog["pair_count"], "retained_cases": len(catalog["parent"]["case_accounting"]),
            "retained_ungrouped_cases": sum(not row["memberships"] for row in catalog["parent"]["case_accounting"].values())}


def _aggregate(catalog, packets, histories, manifest):
    accounting = {key: {"family_id": f["family_id"], "parent": catalog["parent_ref"], "reviewed_pairs": 0,
                        "relationship_counts": dict.fromkeys(RELATIONS, 0)} for key, f in catalog["families"].items()}
    digest, unresolved = hashlib.sha256(), 0
    for packet in packets:
        last = histories[packet["packet_id"]][-1]
        response = runner._result(last)["response"]
        for key in packet["family_keys"]:
            accounting[key]["reviewed_pairs"] += 1
            accounting[key]["relationship_counts"][response["relationship"]] += 1
        unresolved += response["relationship"] == "unresolved"
        ref = {"path": str(last["result"]), "sha256": last["result_sha256"]}
        digest.update((provenance.digest({"packet_id": packet["packet_id"], "result": ref}) + "\n").encode("ascii"))
    for row in accounting.values():
        row["disposition"] = "retained_pairwise_only" if row["reviewed_pairs"] else "retained_without_cross_packet_peer"
    return {"schema_version": "knowledge_family_reconciliation.v1",
            "status": "declared_pairs_reviewed" if len(packets) else "no_cross_packet_pairs",
            "run_sha256": provenance.digest(manifest), "parent": manifest["config"]["parent"],
            "family_accounting": accounting, "case_accounting": copy.deepcopy(catalog["parent"]["case_accounting"]),
            "principle_accounting": copy.deepcopy(catalog["parent"]["principle_accounting"]),
            "edge_ledger": {"schedule": manifest["packets"], "receipt_count": len(packets),
                            "ordered_result_digest": digest.hexdigest(),
                            "location": "Immutable completed packet result responses; no matrix or transitive merge."},
            "unresolved_pair_count": unresolved, "policy": copy.deepcopy(POLICY),
            "scope": "Only declared cross-packet family pairs. Ungrouped items retained, not swept; no global counterreview or profile completion."}


def execute(config_ref, *, command="plan", backend=None, max_packets=None, retry_failed=False):
    try:
        result = runner.execute(sys.modules[__name__], config_ref, command=command, backend=backend,
                                max_packets=max_packets, retry_failed=retry_failed)
    except IncompletePlan as exc:
        return {**exc.result, "pair_coverage": exc.result["status"]}
    completed, count = result["completed_packets"], result["packet_count"]
    result["pair_coverage"] = ("no_cross_packet_pairs" if not count else
                               "declared_pairs_complete" if completed == count else
                               "partial" if completed else "not_started")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "validate"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--max-packets", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = execute({"path": str(args.config), "sha256": args.sha256}, command=args.command,
                         max_packets=args.max_packets, retry_failed=args.retry_failed)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "note": "Inspect private reconciliation config and immutable parents/receipts."}))
        return 2
    fields = ("status", "pair_coverage", "packet_count", "max_pairs", "planned_first_attempt_calls", "max_calls",
              "completed_packets", "admitted_calls", "model_calls_this_execution",
              "family_count", "retained_cases", "retained_ungrouped_cases", "blocked_pair_count", "policy")
    print(json.dumps({key: result[key] for key in fields if key in result}))
    return 0 if result["status"] in {"planned", "completed", "validated", "paused_at_packet_limit"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
