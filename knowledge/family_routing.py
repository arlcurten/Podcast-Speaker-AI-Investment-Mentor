"""Bounded descriptor-page routing, not source adjudication or global challenge.

CLI: python -m knowledge.family_routing plan|run|validate --config PATH --sha256 HASH
Config pins a completed discovery parent, explicit max_response_entries and
audit_pairs, plus discovery runtime limits. There is no default call budget.
Completed discovery.json is the immutable selection consumed by verifier v2.
Routing still has quadratic descriptor work; only selected pairs need source calls.
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
from . import corpus_discovery as discovery, corpus_inventory as inventory
from . import discovery_runner as runner, family_reconciliation as verifier, provenance
from .schemas import TEXT, STRINGS, array, enum, obj

REQUEST_VERSION = "knowledge_family_routing_request.v1"
RESULT_VERSION = "knowledge_family_routing_result.v1"
PROMPT_PATH = Path(__file__).parent / "prompts" / "family_routing.md"
PAIR = {**STRINGS, "minItems": 2, "maxItems": 2, "uniqueItems": True}
CONFIG = copy.deepcopy(verifier.CONFIG)
CONFIG["properties"].pop("max_pairs")
CONFIG["required"].remove("max_pairs")
CONFIG["properties"].update(schema_version=enum("knowledge_family_routing_config.v1"),
    max_response_entries=discovery.POSITIVE, audit_pairs=array(PAIR))
CONFIG["required"].extend(["max_response_entries", "audit_pairs"])
PROPOSAL = obj(cell_sha256=TEXT, pair_dispositions=array(obj(family_keys=PAIR,
    disposition=enum("proposed_link", "uncertain", "routing_negative"), reason=TEXT)))
POLICY = {**verifier.POLICY, "scope": "descriptor_pair_routing_only",
    "execution_scope": "bounded_descriptor_pages_then_explicit_selected_source_verification",
    "cross_packet_reconciliation": "not_source_verified",
    "negative_routing": "not_semantic_unrelatedness_or_counterevidence_absence"}
_check_pins = verifier._check_pins


def _implementation():
    return {"routing_sha256": sha256_file(Path(__file__)), "prompt_sha256": sha256_file(PROMPT_PATH),
            "verifier": verifier._implementation(), "proposal_sha256": provenance.digest(PROPOSAL)}


def _size(prompt, context):
    return runner.input_size(prompt, context, PROPOSAL)


def _valid_pair(pair, families):
    if (len(pair) != 2 or pair != sorted(set(pair)) or any(key not in families for key in pair)
            or families[pair[0]]["packet_id"] == families[pair[1]]["packet_id"]):
        raise ValueError("Expected an ordered, versioned cross-packet family pair.")


def _context(keys, pairs, catalog):
    descriptors = []
    for key in keys:
        family = catalog["families"][key]
        descriptors.append({**{k: copy.deepcopy(v) for k, v in family.items() if k != "memberships"},
                            "memberships_sha256": provenance.digest(family["memberships"])})
    context = {"parent": catalog["parent_ref"], "inventory": catalog["parent"]["inventory"],
               "families": descriptors, "family_pairs": pairs, "policy": copy.deepcopy(POLICY)}
    return {**context, "cell_sha256": provenance.digest(context)}


def _packet_context(packet, catalog):
    families = catalog["families"]
    pairs = [[a, b] for a in packet["left"] for b in packet["right"]
             if a < b and families[a]["packet_id"] != families[b]["packet_id"]]
    return _context(sorted(set(packet["left"]) | set(packet["right"])), pairs, catalog)


class CellSchedule:
    """Reiterable page cells; at most one bounded response-pair list in memory."""

    def __init__(self, catalog, prompt, cap, entries):
        self.catalog, self.prompt, self.cap, self.entries = catalog, prompt, cap, entries
        self.pages, page = [], []
        for key in sorted(catalog["families"]):
            if page and (len(page) >= entries + 1 or
                         _size(prompt, _context([*page, key], [], catalog)) > cap):
                self.pages.append(page)
                page = []
            page.append(key)
        if page:
            self.pages.append(page)
        self.count, self.pair_count, self.largest = 0, 0, 0
        digest = hashlib.sha256()
        for packet in self:
            context = self.context(packet)
            self.count += 1
            self.pair_count += len(context["family_pairs"])
            self.largest = max(self.largest, _size(prompt, context))
            digest.update((provenance.digest(context) + "\n").encode("ascii"))
        self.context_digest = digest.hexdigest()

    def __len__(self):
        return self.count

    def _pair_count(self, left, right, diagonal):
        families = self.catalog["families"]
        a, b = (Counter(families[k]["packet_id"] for k in keys) for keys in (left, right))
        count = len(left) * len(right) - sum(n * b[k] for k, n in a.items())
        return count // 2 if diagonal else count

    def _split(self, left, right, diagonal):
        count = self._pair_count(left, right, diagonal)
        if not count:
            return
        packet = {"left": left, "right": right, "diagonal": diagonal}
        if count <= self.entries and _size(self.prompt, self.context(packet)) <= self.cap:
            yield packet
            return
        if diagonal:
            mid = len(left) // 2
            yield from self._split(left[:mid], left[:mid], True)
            yield from self._split(left[:mid], left[mid:], False)
            yield from self._split(left[mid:], left[mid:], True)
        elif len(left) > 1 and len(left) >= len(right):
            mid = len(left) // 2
            yield from self._split(left[:mid], right, False)
            yield from self._split(left[mid:], right, False)
        elif len(right) > 1:
            mid = len(right) // 2
            yield from self._split(left, right[:mid], False)
            yield from self._split(left, right[mid:], False)
        else:
            raise verifier.IncompletePlan({"status": "incomplete_descriptor_overflow",
                "family_keys": left + right, "parent": self.catalog["parent_ref"],
                "model_calls_this_execution": 0, "policy": copy.deepcopy(POLICY)})

    def __iter__(self):
        number = 0
        for i, left in enumerate(self.pages):
            for j in range(i, len(self.pages)):
                for packet in self._split(left, self.pages[j], i == j):
                    number += 1
                    yield {"packet_id": f"packet_{number:09d}", **packet}

    def context(self, packet):
        return _packet_context(packet, self.catalog)


def _prepare(config_ref):
    pins = {}
    config_ref = inventory._ref(config_ref)
    cfg = inventory._read(config_ref, pins)
    discovery._validate(cfg, CONFIG)
    implementation, prompt = _implementation(), PROMPT_PATH.read_text(encoding="utf-8")
    safe_id(cfg["run_id"])
    cfg = {**cfg, "output_root": str(private_path(cfg["output_root"])),
           "parent": {k: inventory._ref(v) for k, v in cfg["parent"].items()}}
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    catalog = verifier._load_parent(cfg["parent"], pins)
    verifier._protect_output(root, catalog, pins)
    seen = set()
    for pair in cfg["audit_pairs"]:
        _valid_pair(pair, catalog["families"])
        if tuple(pair) in seen:
            raise ValueError("Duplicate explicit audit pair.")
        seen.add(tuple(pair))
    try:
        packets = CellSchedule(catalog, prompt, cfg["max_input_chars"], cfg["max_response_entries"])
    finally:
        _check_pins(pins)
        if _implementation() != implementation:
            raise ValueError("Routing implementation changed during planning.")
    catalog.update(pair_count=packets.pair_count, packet_count=len(packets), max_pairs=packets.pair_count)
    schedule = {"kind": "bounded_descriptor_page_cells.v1", "pages": packets.pages,
        "page_sha256": provenance.digest(packets.pages), "pair_count": packets.pair_count,
        "packet_count": len(packets), "ordered_context_digest": packets.context_digest,
        "max_input_chars_observed": packets.largest, "max_response_entries": cfg["max_response_entries"],
        "planned_first_attempt_calls": len(packets), "potential_calls_with_retries": len(packets) * (cfg["retry_count"] + 1),
        "first_pass_fits_call_budget": len(packets) <= cfg["max_calls"], "lifetime_max_calls": cfg["max_calls"]}
    manifest = {"schema_version": "knowledge_family_routing_run.v1", "config": cfg, "config_ref": config_ref,
                "implementation": implementation, "packets": schedule, "policy": copy.deepcopy(POLICY)}
    return cfg, root, catalog, packets, manifest, pins, prompt


def _materialize(packet, catalog, pins, prompt, max_chars):
    # The packet is already bounded by the deterministic page schedule.
    return _packet_context(packet, catalog)


def _derive(response, context):
    discovery._validate(response, PROPOSAL)
    if response["cell_sha256"] != context["cell_sha256"]:
        raise ValueError("Routing response names a different descriptor cell.")
    expected, seen = {tuple(p) for p in context["family_pairs"]}, set()
    for row in response["pair_dispositions"]:
        pair = tuple(row["family_keys"])
        if pair not in expected or pair in seen:
            raise ValueError("Routing pairs must be exact, ordered and unique.")
        seen.add(pair)
    if seen != expected:
        raise ValueError("Every declared routing pair needs an explicit disposition.")
    return copy.deepcopy(response)


def _known_links(catalog):
    by_record = {}
    for key, family in catalog["families"].items():
        for rid in family["memberships"]:
            by_record.setdefault(rid, set()).add(key)
    for pid, principle in sorted(catalog["inventory"]["principles"].items()):
        targets = {key for rid in principle["supporting_records"] for key in by_record.get(rid, ())}
        for kind in ("counterexamples", "contradictions"):
            for link in principle[kind]:
                for target in sorted(targets):
                    origin = {"kind": "known_counter", "source_principle_id": pid, "counter_kind": kind,
                              "record_id": link["record_id"], "target_family": target,
                              "link": copy.deepcopy(link)}
                    peers = [key for key in sorted(by_record.get(link["record_id"], ()))
                             if catalog["families"][key]["packet_id"] != catalog["families"][target]["packet_id"]]
                    if not peers:
                        yield None, origin
                    for peer in peers:
                        yield tuple(sorted((target, peer))), origin


def _aggregate(catalog, packets, histories, manifest):
    selected, counts = {}, dict.fromkeys(("proposed_link", "uncertain", "routing_negative"), 0)
    digest = hashlib.sha256()
    for packet in packets:
        last = histories[packet["packet_id"]][-1]
        receipt = {"path": str(last["result"]), "sha256": last["result_sha256"]}
        digest.update((provenance.digest({"packet_id": packet["packet_id"], "receipt": receipt}) + "\n").encode("ascii"))
        for row in runner._result(last)["response"]["pair_dispositions"]:
            counts[row["disposition"]] += 1
            if row["disposition"] != "routing_negative":
                selected.setdefault(tuple(row["family_keys"]), []).append({"kind": row["disposition"],
                    "receipt": receipt, "packet_id": packet["packet_id"], "reason": row["reason"]})
    item_links = []
    for pair, origin in _known_links(catalog):
        if pair is None:
            item_links.append(origin)
        else:
            selected.setdefault(pair, []).append(origin)
    for pair in manifest["config"]["audit_pairs"]:
        selected.setdefault(tuple(pair), []).append({"kind": "explicit_audit", "config": manifest["config_ref"]})
    rows = [{"family_keys": list(pair), "origins": origins} for pair, origins in sorted(selected.items())]
    return {"schema_version": "knowledge_family_selection.v1", "status": "descriptor_routing_complete",
        "run_sha256": provenance.digest(manifest), "config_ref": manifest["config_ref"], "parent": manifest["config"]["parent"],
        "selection": rows, "selected_count": len(rows), "ordered_selection_sha256": provenance.digest(rows),
        "routing_counts": counts, "routing_schedule": manifest["packets"], "ordered_result_digest": digest.hexdigest(),
        "family_keys": sorted(catalog["families"]), "case_accounting": copy.deepcopy(catalog["parent"]["case_accounting"]),
        "principle_accounting": copy.deepcopy(catalog["parent"]["principle_accounting"]),
        "future_challenge": {"status": "mandatory_not_performed", "inventory": catalog["parent"]["inventory"],
            "scope": "Every inventory L2 item against all versioned targets; no source or global negative proof.",
            "ungrouped_case_ids": sorted(rid for rid, row in catalog["parent"]["case_accounting"].items() if not row["memberships"]),
            "known_item_family_obligations": item_links}, "policy": copy.deepcopy(POLICY)}


def load_completed(selection_ref, pins):
    value = inventory._read(selection_ref, pins)
    cfg, root, catalog, packets, manifest, dependencies, prompt = _prepare(value["config_ref"])
    if inventory._ref(selection_ref)["path"] != str(root / "discovery.json"):
        raise ValueError("Selection must be the pinned routing run's canonical artifact.")
    histories, _ = runner.read_history(sys.modules[__name__], root, manifest, packets, prompt, catalog, dependencies)
    if any(not histories.get(p["packet_id"]) or histories[p["packet_id"]][-1]["status"] != "completed" for p in packets):
        raise ValueError("Selection requires completed routing receipts.")
    if provenance.digest(value) != provenance.digest(_aggregate(catalog, packets, histories, manifest)):
        raise ValueError("Selection differs from reconstructed routing and mandatory union.")
    for path, checksum in dependencies.items():
        inventory._pin_file({"path": str(path), "sha256": checksum}, pins)
    _check_pins(pins)
    if _implementation() != manifest["implementation"]:
        raise ValueError("Routing implementation changed during selection replay.")
    catalog["protected_roots"].append(root)
    return value, catalog


def _counts(catalog):
    return {"family_count": len(catalog["families"]), "routed_pair_count": catalog["pair_count"],
            "planned_first_attempt_calls": catalog["packet_count"], "selected_pair_count": None,
            "retained_cases": len(catalog["parent"]["case_accounting"])}


def execute(config_ref, *, command="plan", backend=None, max_packets=None, retry_failed=False):
    try:
        return runner.execute(sys.modules[__name__], config_ref, command=command, backend=backend,
                              max_packets=max_packets, retry_failed=retry_failed)
    except verifier.IncompletePlan as exc:
        return exc.result


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
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 2
    fields = ("status", "packet_count", "planned_first_attempt_calls", "max_calls", "completed_packets",
              "admitted_calls", "model_calls_this_execution", "family_count", "routed_pair_count", "policy")
    print(json.dumps({k: result[k] for k in fields if k in result}))
    return 0 if result["status"] in {"planned", "completed", "validated", "paused_at_packet_limit"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
