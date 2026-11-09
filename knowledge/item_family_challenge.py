"""Exhaustive bounded L2-item x versioned-family inspection, not acceptance.

CLI: python -m knowledge.item_family_challenge plan|run|validate --config PATH --sha256 HASH
Completed discovery.json is replayed by item_family_verification. Each stage has
its own immutable config, lifetime budget and receipts. No source-worker changes.
Planning/resume streams the Cartesian universe; no global pair matrix is stored.
Complete source closure is constructed before the packed input check: the cap
does NOT bound unpacked RAM. Parent replay, inventory, selected receipt history
and connected source closure have additional memory/time costs.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

from transcription.common import child_path, private_path, safe_id, sha256_file
from . import corpus_discovery as discovery, corpus_inventory as inventory
from . import discovery_runner as runner, family_reconciliation as verifier, family_verification, provenance
from .schemas import TEXT, array, enum, obj

REQUEST_VERSION = "knowledge_item_family_challenge_request.v2"
RESULT_VERSION = "knowledge_item_family_challenge_result.v2"
PROMPT_PATH = Path(__file__).parent / "prompts" / "item_family_challenge.md"
DISPOSITIONS = ("potential_support", "potential_counter", "context_difference", "retelling",
                "evolution", "uncertain", "no_link_found")
PAIR = obj(record_id=TEXT, family_key=TEXT)
FOLLOWUP = obj(config=inventory.REF, artifact=inventory.REF, result=inventory.REF,
               record_id=TEXT, family_key=TEXT)
OBLIGATION = obj(obligation_id=TEXT, disposition=enum("retained_for_verification", "contested_for_verification"), reason=TEXT)
PROPOSAL = obj(cell_sha256=inventory.REF["properties"]["sha256"], pair_dispositions=array(obj(
    record_id=TEXT, family_key=TEXT, disposition=enum(*DISPOSITIONS), reason=TEXT,
    source_anchors=array(verifier.ANCHOR, 1), obligation_dispositions=array(OBLIGATION))))
CONFIG = copy.deepcopy(discovery.CONFIG)
CONFIG["properties"].pop("inventory")
CONFIG["required"].remove("inventory")
CONFIG["properties"].update(schema_version=enum("knowledge_item_family_challenge_config.v2"),
    parent=obj(config=inventory.REF, artifact=inventory.REF),
    max_items_per_page=discovery.POSITIVE, max_families_per_page=discovery.POSITIVE,
    max_response_entries=discovery.POSITIVE, audit_pairs=array(PAIR), pair_review_followups=array(FOLLOWUP))
CONFIG["required"].extend(["parent", "max_items_per_page", "max_families_per_page", "max_response_entries",
                          "audit_pairs", "pair_review_followups"])
POLICY = {**discovery.POLICY, "scope": "every_inventory_L2_item_against_every_frozen_family",
    "authority": "model_challenge_navigation_not_source_evidence",
    "negative_scope": "no_link_found_in_this_item_source_and_descriptor_inspection_only",
    "global_counter_review": "inspection_then_separate_selected_source_verification_required",
    "independent_event_count": None, "automatic_membership_changes": False,
    "principle_construction": "not_performed", "semantic_recall": "not_certified"}
_check_pins = verifier._check_pins


def _implementation():
    return {"challenge_sha256": sha256_file(Path(__file__)), "prompt_sha256": sha256_file(PROMPT_PATH),
            "runner_sha256": sha256_file(Path(runner.__file__)), "loaders": verifier._implementation(),
            "selected_pair_loader": family_verification._implementation(),
            "proposal_sha256": provenance.digest(PROPOSAL), "config_sha256": provenance.digest(CONFIG)}


def _size(prompt, context):
    return runner.input_size(prompt, context, PROPOSAL)


def _index(catalog):
    source = catalog["inventory"]
    if (not source["inventory_complete"] or set(source["cases"]) != set(catalog["parent"]["case_accounting"])
            or set(source["principles"]) != set(catalog["parent"]["principle_accounting"])):
        raise ValueError("Challenge requires exact complete inventory item and L3 accounting.")
    by_case = {rid: set() for rid in source["cases"]}
    for pid, principle in source["principles"].items():
        ids = set(principle["supporting_records"])
        ids.update(link["record_id"] for field in ("counterexamples", "contradictions") for link in principle[field])
        for rid in ids:
            by_case[rid].add(pid)
    catalog["principles_by_case"] = by_case
    catalog["descriptors"] = {}
    for key, family in catalog["families"].items():
        if not family["memberships"] or not set(family["memberships"]) <= source["cases"].keys():
            raise ValueError("A target needs real, inventory-bound family members; no singleton fabrication.")
        value = {k: copy.deepcopy(v) for k, v in family.items() if k != "memberships"}
        value.update(membership_sha256=provenance.digest(family["memberships"]),
                     family_sha256=provenance.digest(family), inventory=catalog["parent"]["inventory"])
        catalog["descriptors"][key] = {**value, "descriptor_sha256": provenance.digest(value)}


def _load_followups(cfg, catalog, pins):
    """Replay completed pair runs; a config requests follow-up, not the old model."""
    groups, seen = {}, set()
    catalog["pair_followups"] = {}
    for row in cfg["pair_review_followups"]:
        identity = provenance.digest(row)
        if identity in seen:
            raise ValueError("Duplicate pair-review follow-up.")
        seen.add(identity)
        if row["record_id"] not in catalog["inventory"]["cases"] or row["family_key"] not in catalog["families"]:
            raise ValueError("Follow-up item/target is outside the exact parent catalog.")
        key = provenance.digest({k: row[k] for k in ("config", "artifact")})
        groups.setdefault(key, []).append(row)
    for rows in groups.values():
        refs = rows[0]
        config = inventory._read(refs["config"], pins)
        adapters = {"knowledge_reconciliation_config.v1": verifier,
                    "knowledge_reconciliation_config.v2": family_verification}
        adapter = adapters.get(config.get("schema_version"))
        if adapter is None:
            raise ValueError("Follow-ups require a known source-backed pair-review contract, not routing.")
        discovery._validate(config, adapter.CONFIG)
        if {k: inventory._ref(v) for k, v in config["parent"].items()} != cfg["parent"]:
            raise ValueError("Pair-review follow-up must pin the exact challenge parent/version.")
        _, root, parent_catalog, packets, manifest, dependencies, prompt = adapter._prepare(refs["config"])
        if refs["artifact"]["path"] != str(root / "discovery.json"):
            raise ValueError("Follow-up must reference the canonical completed pair-review artifact.")
        if provenance.digest(parent_catalog["families"]) != provenance.digest(catalog["families"]):
            raise ValueError("Pair-review follow-up family catalog differs from the challenge.")
        artifact = inventory._read(refs["artifact"], dependencies)
        histories, _ = runner.read_history(adapter, root, manifest, packets, prompt, parent_catalog, dependencies)
        if any(not histories.get(p["packet_id"]) or histories[p["packet_id"]][-1]["status"] != "completed"
               for p in packets):
            raise ValueError("Pair-review follow-ups require a fully completed run.")
        if provenance.digest(artifact) != provenance.digest(adapter._aggregate(parent_catalog, packets, histories, manifest)):
            raise ValueError("Pair-review follow-up artifact differs from completed receipts.")
        remaining = list(rows)
        for packet in packets:
            last = histories[packet["packet_id"]][-1]
            result_ref = {"path": str(last["result"]), "sha256": last["result_sha256"]}
            matching = [r for r in remaining if r["result"] == result_ref]
            if not matching:
                continue
            result = inventory._read(result_ref, dependencies)
            response = result["response"]
            cases = {r["record_id"]: r for r in response["case_dispositions"]}
            for row in matching:
                rid, target = row["record_id"], row["family_key"]
                if target not in packet["family_keys"] or rid not in cases:
                    raise ValueError("Follow-up must name an actual inspected case and target from the completed pair.")
                origin = {"kind": "pair_review_followup", "review": copy.deepcopy(row),
                    "request": {"path": str(last["request"]), "sha256": dependencies[last["request"]]},
                    "packet_id": packet["packet_id"], "family_keys": packet["family_keys"],
                    "relationship": response["relationship"], "reason": response["reason"],
                    "conditions": response["conditions"], "limitations": response["limitations"],
                    "case_disposition": copy.deepcopy(cases[rid]),
                    "authority": "configured_followup_of_completed_pair_review_not_model_requested_or_source_evidence"}
                catalog["pair_followups"].setdefault((rid, target), []).append(origin)
                remaining.remove(row)
        if remaining:
            raise ValueError("Follow-up result must be the exact final completed pair receipt.")
        for path, checksum in dependencies.items():
            inventory._pin_file({"path": str(path), "sha256": checksum}, pins)
        _check_pins(pins)
        if adapter._implementation() != manifest["implementation"]:
            raise ValueError("Pair-review implementation changed during follow-up replay.")
        catalog["protected_roots"].extend([root, *parent_catalog["protected_roots"]])


def _closure(owned, catalog):
    """Connected known support/counter closure, including counter-only/ineligible L3s."""
    refs, pending, visited = set(owned), list(owned), set()
    principles = catalog["inventory"]["principles"]
    while pending:
        rid = pending.pop()
        for pid in sorted(catalog["principles_by_case"][rid] - visited):
            visited.add(pid)
            p = principles[pid]
            linked = set(p["supporting_records"])
            linked.update(row["record_id"] for field in ("counterexamples", "contradictions") for row in p[field])
            pending.extend(sorted(linked - refs))
            refs.update(linked)
    return sorted(refs)


def _bundle(owned, catalog, pins, materials=None):
    refs = _closure(owned, catalog)
    bundle = discovery._context(refs, catalog["inventory"],
        materials or discovery._materials(catalog["inventory"], pins), {})
    bundle["owned_case_ids"] = list(owned)
    bundle["policy"] = copy.deepcopy(POLICY)
    bundle["known_counter_obligations"] = [
        {**row, "obligation_id": f"counter_{i:06d}"}
        for i, row in enumerate(bundle["known_counter_obligations"], 1)]
    return bundle


def _known(rid, key, catalog):
    family = catalog["families"][key]
    rows = copy.deepcopy(catalog.get("pair_followups", {}).get((rid, key), []))
    if rid in family["memberships"]:
        rows.append({"kind": "existing_membership", "membership": family["memberships"][rid]})
    for pid in sorted(catalog["principles_by_case"][rid]):
        p = catalog["inventory"]["principles"][pid]
        linked = set(p["supporting_records"])
        linked.update(link["record_id"] for field in ("counterexamples", "contradictions") for link in p[field])
        targets = sorted(linked.intersection(family["memberships"]))
        if not targets:
            continue
        # A target containing the counter also obliges inspection of the support;
        # shared navigation is a forced comparison, not a settled semantic link.
        base = {"source_principle_id": pid, "target_linked_case_ids": targets,
                "parent_eligible_for_level4": p["qualification"]["eligible_for_level4"]}
        if rid in p["supporting_records"]:
            rows.append({**base, "kind": "known_support", "record_id": rid})
        for field in ("counterexamples", "contradictions"):
            for index, link in enumerate(p[field]):
                if link["record_id"] == rid:
                    rows.append({**base, "kind": field, "link_index": index, "link": copy.deepcopy(link)})
    return [{**row, "obligation_id": provenance.digest({"record_id": rid, "family_key": key, "origin": row})}
            for row in rows]


def _context(packet, catalog, pins, materials=None):
    pairs = [{"record_id": rid, "family_key": key, "known_obligations": _known(rid, key, catalog)}
             for rid in packet["item_ids"] for key in packet["family_keys"]]
    value = {"parent": catalog["parent_ref"], "inventory": catalog["parent"]["inventory"],
        "item_ids": packet["item_ids"], "families": [catalog["descriptors"][k] for k in packet["family_keys"]],
        "pairs": pairs, "evidence_bundle": _bundle(packet["item_ids"], catalog, pins, materials),
        "policy": copy.deepcopy(POLICY)}
    return {**value, "cell_sha256": provenance.digest(value)}


class CellSchedule:
    """Lazy Cartesian pages and deterministic transport splits, never semantic pruning."""

    def __init__(self, catalog, pins, prompt, cfg):
        self.catalog, self.pins, self.prompt, self.cfg = catalog, pins, prompt, cfg
        ids, keys = sorted(catalog["inventory"]["cases"]), sorted(catalog["families"])
        self.item_pages = [ids[i:i + cfg["max_items_per_page"]] for i in range(0, len(ids), cfg["max_items_per_page"])]
        self.family_pages = [keys[i:i + cfg["max_families_per_page"]] for i in range(0, len(keys), cfg["max_families_per_page"])]
        self.count, self.largest = 0, 0
        checksum = hashlib.sha256()
        for packet in self:
            self.count += 1
            self.largest = max(self.largest, packet["input_chars"])
            checksum.update((provenance.digest(packet) + "\n").encode())
        self.context_digest = checksum.hexdigest()

    def __len__(self):
        return self.count

    def _split(self, ids, keys, materials):
        packet = {"item_ids": ids, "family_keys": keys}
        if len(ids) * len(keys) <= self.cfg["max_response_entries"]:
            context = _context(packet, self.catalog, self.pins, materials)
            size, checksum = _size(self.prompt, context), provenance.digest(context)
            del context
            if size <= self.cfg["max_input_chars"]:
                yield {**packet, "context_sha256": checksum, "input_chars": size}
                return
        if len(ids) > 1 and len(ids) >= len(keys):
            mid = len(ids) // 2
            yield from self._split(ids[:mid], keys, materials)
            yield from self._split(ids[mid:], keys, materials)
        elif len(keys) > 1:
            mid = len(keys) // 2
            yield from self._split(ids, keys[:mid], materials)
            yield from self._split(ids, keys[mid:], materials)
        else:
            raise verifier.IncompletePlan({"status": "incomplete_input_overflow", **packet,
                "required_case_ids": _closure(ids, self.catalog), "parent": self.catalog["parent_ref"],
                "required_input_chars": size, "max_input_chars": self.cfg["max_input_chars"],
                "pair_count": len(self.catalog["inventory"]["cases"]) * len(self.catalog["families"]),
                "model_calls_this_execution": 0, "policy": copy.deepcopy(POLICY)})

    def __iter__(self):
        materials = discovery._materials(self.catalog["inventory"], self.pins)
        number = 0
        for ids in self.item_pages:
            for keys in self.family_pages:
                for packet in self._split(ids, keys, materials):
                    number += 1
                    yield {"packet_id": f"packet_{number:09d}", **packet}


def _materialize(packet, catalog, pins, prompt, max_chars):
    context = _context(packet, catalog, pins)
    if (provenance.digest(context) != packet["context_sha256"]
            or _size(prompt, context) != packet["input_chars"] or packet["input_chars"] > max_chars):
        raise ValueError("Challenge context differs from its exact planned source/descriptor cell.")
    return context


def _prepare(config_ref):
    pins = {}
    config_ref = inventory._ref(config_ref)
    cfg = inventory._read(config_ref, pins)
    if cfg.get("schema_version") == "knowledge_item_family_challenge_config.v1":
        raise ValueError("Item challenge v1 requires historical code; create a new v2 config/run with explicit pair_review_followups.")
    discovery._validate(cfg, CONFIG)
    implementation, prompt = _implementation(), PROMPT_PATH.read_text(encoding="utf-8")
    safe_id(cfg["run_id"])
    cfg = {**cfg, "output_root": str(private_path(cfg["output_root"])),
           "parent": {k: inventory._ref(v) for k, v in cfg["parent"].items()}}
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    catalog = verifier._load_parent(cfg["parent"], pins)
    _index(catalog)
    cfg["pair_review_followups"] = [{**r, **{k: inventory._ref(r[k]) for k in ("config", "artifact", "result")}}
                                    for r in cfg["pair_review_followups"]]
    _load_followups(cfg, catalog, pins)
    verifier._protect_output(root, catalog, pins)
    audits = [(p["record_id"], p["family_key"]) for p in cfg["audit_pairs"]]
    if (len(audits) != len(set(audits)) or any(r not in catalog["inventory"]["cases"]
            or f not in catalog["families"] for r, f in audits)):
        raise ValueError("Audit pairs must be unique exact inventory item/versioned target pairs.")
    try:
        packets = CellSchedule(catalog, pins, prompt, cfg)
    finally:
        _check_pins(pins)
        if _implementation() != implementation:
            raise ValueError("Challenge implementation changed during planning.")
    catalog["packet_count"] = len(packets)
    schedule = {"kind": "every_L2_item_x_every_family.v1", "item_pages": packets.item_pages,
        "family_pages": packets.family_pages, "pair_count": len(catalog["inventory"]["cases"]) * len(catalog["families"]),
        "descriptor_versions": {k: v["descriptor_sha256"] for k, v in catalog["descriptors"].items()},
        "packet_count": len(packets), "ordered_context_digest": packets.context_digest,
        "max_input_chars_observed": packets.largest, "planned_first_attempt_calls": len(packets),
        "potential_calls_with_retries": len(packets) * (cfg["retry_count"] + 1),
        "first_pass_fits_call_budget": len(packets) <= cfg["max_calls"], "lifetime_max_calls": cfg["max_calls"]}
    manifest = {"schema_version": "knowledge_item_family_challenge_run.v2", "config": cfg,
        "config_ref": config_ref, "implementation": implementation, "packets": schedule, "policy": copy.deepcopy(POLICY)}
    return cfg, root, catalog, packets, manifest, pins, prompt


def _derive(response, context):
    discovery._validate(response, PROPOSAL)
    if response["cell_sha256"] != context["cell_sha256"]:
        raise ValueError("Challenge response names a stale source/descriptor cell.")
    expected = {(r["record_id"], r["family_key"]): r for r in context["pairs"]}
    seen = set()
    for row in response["pair_dispositions"]:
        pair = row["record_id"], row["family_key"]
        if pair not in expected or pair in seen:
            raise ValueError("Every item-target pair needs one exact disposition.")
        seen.add(pair)
        verifier._anchors(row["source_anchors"], row["record_id"], context["evidence_bundle"])
        obligations = [r["obligation_id"] for r in row["obligation_dispositions"]]
        if (len(set(obligations)) != len(obligations)
                or set(obligations) != {o["obligation_id"] for o in expected[pair]["known_obligations"]}):
            raise ValueError("Every known obligation needs an explicit retained/contested disposition.")
    if seen != expected.keys():
        raise ValueError("Missing item-target challenge dispositions.")
    return copy.deepcopy(response)


def selected_rows(catalog, packets, histories, manifest):
    """Exact union streamed from closed receipts; no global selected-pair matrix."""
    audits = {(p["record_id"], p["family_key"]) for p in manifest["config"]["audit_pairs"]}
    for packet in packets:
        last = histories[packet["packet_id"]][-1]
        receipt = {"path": str(last["result"]), "sha256": last["result_sha256"]}
        rows = {(r["record_id"], r["family_key"]): r for r in runner._result(last)["response"]["pair_dispositions"]}
        for rid in packet["item_ids"]:
            for key in packet["family_keys"]:
                row, obligations = rows[(rid, key)], _known(rid, key, catalog)
                origins = []
                if row["disposition"] != "no_link_found":
                    origins.append({"kind": row["disposition"], "reason": row["reason"]})
                origins.extend({"kind": "forced_pair_review_followup" if o["kind"] == "pair_review_followup"
                                else "forced_known_obligation", "obligation": o} for o in obligations)
                if (rid, key) in audits:
                    origins.append({"kind": "explicit_audit", "config": manifest["config_ref"]})
                if origins:
                    yield {"record_id": rid, "family_key": key, "origins": origins,
                           "challenge_receipt": receipt, "challenge_packet_id": packet["packet_id"],
                           "challenge_disposition": row["disposition"],
                           "descriptor_sha256": catalog["descriptors"][key]["descriptor_sha256"]}


def _aggregate(catalog, packets, histories, manifest):
    selected, checksum, receipts = 0, hashlib.sha256(), hashlib.sha256()
    counts = dict.fromkeys(DISPOSITIONS, 0)
    for packet in packets:
        last = histories[packet["packet_id"]][-1]
        receipts.update((provenance.digest({"packet_id": packet["packet_id"], "sha256": last["result_sha256"]}) + "\n").encode())
        for row in runner._result(last)["response"]["pair_dispositions"]:
            counts[row["disposition"]] += 1
    for row in selected_rows(catalog, packets, histories, manifest):
        selected += 1
        checksum.update((provenance.digest(row) + "\n").encode())
    if sum(counts.values()) != manifest["packets"]["pair_count"]:
        raise ValueError("Challenge pair coverage differs from the full Cartesian universe.")
    return {"schema_version": "knowledge_item_family_selection.v2",
        "status": "inspection_complete_verification_pending" if catalog["families"] else "no_targets",
        "config_ref": manifest["config_ref"], "parent": manifest["config"]["parent"],
        "run_sha256": provenance.digest(manifest), "schedule": manifest["packets"],
        "case_accounting": copy.deepcopy(catalog["parent"]["case_accounting"]),
        "principle_accounting": copy.deepcopy(catalog["parent"]["principle_accounting"]),
        "disposition_counts": counts, "selected_count": selected, "pending_source_verifications": selected,
        "ordered_selection_sha256": checksum.hexdigest(), "ordered_result_sha256": receipts.hexdigest(),
        "selection_location": "Exact positive/uncertain/known/pair-review-follow-up/audit union reconstructed from pinned receipts",
        "source_verified_pair_count": 0, "policy": copy.deepcopy(POLICY)}


def load_completed(selection_ref, pins):
    value = inventory._read(selection_ref, pins)
    cfg, root, catalog, packets, manifest, dependencies, prompt = _prepare(value["config_ref"])
    if inventory._ref(selection_ref)["path"] != str(root / "discovery.json"):
        raise ValueError("Challenge selection must be its canonical completed artifact.")
    histories, _ = runner.read_history(sys.modules[__name__], root, manifest, packets, prompt, catalog, dependencies)
    if any(not histories.get(p["packet_id"]) or histories[p["packet_id"]][-1]["status"] != "completed" for p in packets):
        raise ValueError("Challenge selection requires all cells completed; missing cells remain pending.")
    if provenance.digest(value) != provenance.digest(_aggregate(catalog, packets, histories, manifest)):
        raise ValueError("Challenge selection differs from the exact reconstructed mandatory union.")
    for path, checksum in dependencies.items():
        inventory._pin_file({"path": str(path), "sha256": checksum}, pins)
    _check_pins(pins)
    if _implementation() != manifest["implementation"]:
        raise ValueError("Challenge implementation changed during replay.")
    catalog["protected_roots"].append(root)
    return value, catalog, packets, histories, manifest


def _counts(catalog):
    return {"retained_cases": len(catalog["inventory"]["cases"]), "family_count": len(catalog["families"]),
        "pair_count": len(catalog["inventory"]["cases"]) * len(catalog["families"]),
        "planned_first_attempt_calls": catalog["packet_count"]}


def execute(config_ref, *, command="plan", backend=None, max_packets=None, retry_failed=False):
    try:
        result = runner.execute(sys.modules[__name__], config_ref, command=command, backend=backend,
                                max_packets=max_packets, retry_failed=retry_failed)
    except verifier.IncompletePlan as exc:
        return exc.result
    return {**result, "pending_cells": result["packet_count"] - result["completed_packets"],
        "coverage": "no_targets" if not result["family_count"] else
            "inspection_complete_verification_separate" if result["completed_packets"] == result["packet_count"] else "incomplete"}


def cli(adapter, argv=None):
    parser = argparse.ArgumentParser(description=adapter.__doc__)
    parser.add_argument("command", choices=("plan", "run", "validate"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--max-packets", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = adapter.execute({"path": str(args.config), "sha256": args.sha256}, command=args.command,
                                 max_packets=args.max_packets, retry_failed=args.retry_failed)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"planned", "completed", "validated", "paused_at_packet_limit"} else 2


def main(argv=None):
    return cli(sys.modules[__name__], argv)


if __name__ == "__main__":
    raise SystemExit(main())
