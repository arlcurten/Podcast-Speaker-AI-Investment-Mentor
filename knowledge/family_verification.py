"""Selected-schedule v2 adapter for knowledge.family_reconciliation.

Use its plan/run/validate CLI with knowledge_reconciliation_config.v2, the same
explicit runtime/parent/max_pairs fields as v1, and selection={path, sha256}.
The selection must be a completed independently replayable routing artifact.
Only selected pairs are source-preflighted; global challenge remains mandatory.
"""
from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

from transcription.common import child_path, private_path, safe_id, sha256_file
from . import corpus_discovery as discovery, corpus_inventory as inventory
from . import discovery_runner as runner, family_reconciliation as verifier, family_routing as routing, provenance
from .schemas import enum

REQUEST_VERSION = "knowledge_reconciliation_request.v2"
RESULT_VERSION = "knowledge_reconciliation_result.v2"
PROPOSAL = verifier.PROPOSAL
POLICY = {**verifier.POLICY, "execution_scope": "explicit_selected_pairs_only",
          "negative_routing": "not_semantic_unrelatedness_or_counterevidence_absence"}
CONFIG = copy.deepcopy(verifier.CONFIG)
CONFIG["properties"].update(schema_version=enum("knowledge_reconciliation_config.v2"), selection=inventory.REF)
CONFIG["required"].append("selection")
_check_pins, _size, _derive = verifier._check_pins, verifier._size, verifier._derive


def _implementation():
    return {"selected_verifier_sha256": sha256_file(Path(__file__)), "routing": routing._implementation(),
            "verifier": verifier._implementation()}


class SelectedSchedule:
    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def __iter__(self):
        for number, row in enumerate(self.rows, 1):
            yield {"packet_id": f"packet_{number:09d}", "family_keys": row["family_keys"], "origins": row["origins"]}


def _context(packet, catalog, pins, prompt, cap, materials=None):
    context = verifier._context(packet, catalog, pins, prompt, cap, materials)
    context["selected_schedule"] = {"selection": catalog["selection_ref"], "origins": packet["origins"],
                                    "policy": copy.deepcopy(POLICY)}
    if _size(prompt, context) > cap:
        raise discovery._PacketTooLarge("Complete selected-pair source closure and routing provenance exceed input cap; no truncation.")
    return context


def _materialize(packet, catalog, pins, prompt, max_chars):
    return _context(packet, catalog, pins, prompt, max_chars)


def _prepare(config_ref):
    pins = {}
    config_ref = inventory._ref(config_ref)
    cfg = inventory._read(config_ref, pins)
    discovery._validate(cfg, CONFIG)
    implementation, prompt = _implementation(), verifier.PROMPT_PATH.read_text(encoding="utf-8")
    safe_id(cfg["run_id"])
    cfg = {**cfg, "output_root": str(private_path(cfg["output_root"])), "selection": inventory._ref(cfg["selection"]),
           "parent": {k: inventory._ref(v) for k, v in cfg["parent"].items()}}
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    selection, catalog = routing.load_completed(cfg["selection"], pins)
    if cfg["parent"] != selection["parent"]:
        raise ValueError("Selected schedule and verifier must pin the same discovery parent.")
    verifier._protect_output(root, catalog, pins)
    packets = SelectedSchedule(selection["selection"])
    catalog.update(selection=selection, selection_ref=cfg["selection"], pair_count=len(packets), max_pairs=cfg["max_pairs"])
    schedule = {"kind": "explicit_selected_family_pairs.v1", "selection": cfg["selection"],
        "ordered_selection_sha256": selection["ordered_selection_sha256"], "pair_count": len(packets),
        "max_pairs": cfg["max_pairs"], "planned_first_attempt_calls": len(packets),
        "potential_calls_with_retries": len(packets) * (cfg["retry_count"] + 1),
        "lifetime_max_calls": cfg["max_calls"], "first_pass_fits_call_budget": len(packets) <= cfg["max_calls"],
        "cost_policy": "Only selected source pairs are preflighted/replayed; parent routing replay still checks descriptor cells."}

    def incomplete(status, **details):
        _check_pins(pins)
        if _implementation() != implementation:
            raise ValueError("Selected verifier implementation changed during planning.")
        raise verifier.IncompletePlan({"status": status, "packet_count": len(packets), "packet_plan": schedule,
            "parent": cfg["parent"], "selection": cfg["selection"], **_counts(catalog),
            "future_challenge": selection["future_challenge"], "max_calls": cfg["max_calls"],
            "model_calls_this_execution": 0, "policy": copy.deepcopy(POLICY), **details})

    if len(packets) > cfg["max_pairs"]:
        incomplete("incomplete_pair_limit")
    digest, largest, blocked, first_blocked = hashlib.sha256(), 0, 0, None
    materials = discovery._materials(catalog["inventory"], pins)
    for packet in packets:
        try:
            context = _context(packet, catalog, pins, prompt, cfg["max_input_chars"], materials)
        except discovery._PacketTooLarge as exc:
            blocked += 1
            if first_blocked is None:
                first_blocked = {**packet, "detail": str(exc), "inventory": catalog["parent"]["inventory"],
                                 "parent": catalog["parent_ref"], "source_closure": "All memberships and overlapping L3 support/counter links retained in pinned originals."}
        else:
            largest = max(largest, _size(prompt, context))
            digest.update((provenance.digest(context) + "\n").encode("ascii"))
            del context
    schedule.update(max_input_chars_observed=largest, ordered_context_digest=None if blocked else digest.hexdigest())
    if blocked:
        incomplete("incomplete_input_overflow", blocked_pair_count=blocked, first_blocked_pair=first_blocked)
    _check_pins(pins)
    if _implementation() != implementation:
        raise ValueError("Selected verifier implementation changed during planning.")
    manifest = {"schema_version": "knowledge_reconciliation_run.v2", "config": cfg, "config_ref": config_ref,
                "implementation": implementation, "policy": copy.deepcopy(POLICY), "packets": schedule}
    return cfg, root, catalog, packets, manifest, pins, prompt


def _counts(catalog):
    return {**verifier._counts(catalog), "routed_pair_count": sum(catalog["selection"]["routing_counts"].values()),
            "routing_negative_count": catalog["selection"]["routing_counts"]["routing_negative"]}


def _aggregate(catalog, packets, histories, manifest):
    value = verifier._aggregate(catalog, packets, histories, manifest)
    for row in value["family_accounting"].values():
        if not row["reviewed_pairs"]:
            row["disposition"] = "retained_without_selected_comparison"
    return {**value, "schema_version": "knowledge_family_reconciliation.v2",
        "status": "selected_pairs_reviewed" if len(packets) else "no_selected_pairs",
        "selection": catalog["selection_ref"], "routing_counts": catalog["selection"]["routing_counts"],
        "selected_pair_count": len(packets), "source_verified_pair_count": len(packets),
        "pending_selected_pair_count": 0, "future_challenge": copy.deepcopy(catalog["selection"]["future_challenge"]),
        "scope": "Only the exact selected union was source reviewed; routing negatives are not unrelatedness or global coverage.",
        "policy": copy.deepcopy(POLICY)}


def execute(config_ref, *, command="plan", backend=None, max_packets=None, retry_failed=False):
    try:
        result = runner.execute(sys.modules[__name__], config_ref, command=command, backend=backend,
                                max_packets=max_packets, retry_failed=retry_failed)
    except verifier.IncompletePlan as exc:
        return {**exc.result, "pair_coverage": exc.result["status"]}
    count, completed = result["packet_count"], result["completed_packets"]
    return {**result, "source_verified_pair_count": completed, "pending_selected_pair_count": count - completed,
            "pair_coverage": "no_selected_pairs" if not count else
                "selected_pairs_complete" if completed == count else "partial" if completed else "not_started"}
