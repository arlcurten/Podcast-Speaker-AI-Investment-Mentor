"""Source verification of the exact completed item-family challenge selection.

CLI: python -m knowledge.item_family_verification plan|run|validate --config PATH --sha256 HASH
Config replaces challenge page/audit/follow-up settings with selection REF and explicit
max_pairs; it retains parent refs and separate runtime/budget settings. No fake
singleton family, L3 ancestor, membership update, transitive merge or acceptance.
Only selected comparisons reopen BOTH original source closures. Selection replay
still traverses all inspection cells. Unresolved semantics remain quarantined.
"""
from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

from transcription.common import child_path, private_path, safe_id, sha256_file
from . import corpus_discovery as discovery, corpus_inventory as inventory, discovery_runner as runner
from . import family_reconciliation as verifier, item_family_challenge as challenge, provenance
from .schemas import TEXT, STRINGS, array, enum, obj

REQUEST_VERSION = "knowledge_item_family_verification_request.v2"
RESULT_VERSION = "knowledge_item_family_verification_result.v2"
PROMPT_PATH = Path(__file__).parent / "prompts" / "item_family_verification.md"
RELATIONS = ("support", "counter", "context_difference", "retelling", "evolution", "no_link_found", "unresolved")
CONFIG = copy.deepcopy(discovery.CONFIG)
CONFIG["properties"].pop("inventory")
CONFIG["required"].remove("inventory")
CONFIG["properties"].update(schema_version=enum("knowledge_item_family_verification_config.v2"),
    parent=obj(config=inventory.REF, artifact=inventory.REF), selection=inventory.REF, max_pairs=discovery.POSITIVE)
CONFIG["required"].extend(["parent", "selection", "max_pairs"])
DISPOSITION = obj(disposition=enum("retained", "context_excluded", "unresolved"), reason=TEXT,
                  source_anchors=array(verifier.ANCHOR, 1))
PROPOSAL = obj(context_sha256=inventory.REF["properties"]["sha256"], record_id=TEXT, family_key=TEXT,
    relationship=enum(*RELATIONS), reason=TEXT, conditions=STRINGS, limitations=STRINGS,
    case_dispositions=array(obj(record_id=TEXT, **DISPOSITION["properties"])),
    counter_dispositions=array(obj(obligation_id=TEXT, **DISPOSITION["properties"])),
    known_dispositions=array(obj(obligation_id=TEXT, **DISPOSITION["properties"])))
POLICY = {**challenge.POLICY, "scope": "exact_selected_item_family_source_verification",
          "authority": "model_source_comparison_not_new_evidence",
          "global_counter_review": "declared_inspection_and_selected_verification_not_semantic_recall_proof",
          "direct_link_provenance": "original_L2_item_not_fabricated_L3_ancestor"}
_check_pins = challenge._check_pins


def _implementation():
    return {"verification_sha256": sha256_file(Path(__file__)), "prompt_sha256": sha256_file(PROMPT_PATH),
            "challenge": challenge._implementation(), "proposal_sha256": provenance.digest(PROPOSAL),
            "config_sha256": provenance.digest(CONFIG)}


def _size(prompt, context):
    return runner.input_size(prompt, context, PROPOSAL)


class SelectedSchedule:
    def __init__(self, value, catalog, packets, histories, manifest):
        self.value, self.catalog, self.packets = value, catalog, packets
        self.histories, self.manifest = histories, manifest

    def __len__(self):
        return self.value["selected_count"]

    def __iter__(self):
        for number, row in enumerate(challenge.selected_rows(self.catalog, self.packets, self.histories, self.manifest), 1):
            yield {"packet_id": f"packet_{number:09d}", **row}


def _context(packet, catalog, pins, prompt, cap, materials=None):
    rid, key = packet["record_id"], packet["family_key"]
    family = catalog["families"][key]
    seeds = sorted({rid, *family["memberships"]})
    bundle = challenge._bundle(seeds, catalog, pins, materials)
    bundle["policy"] = copy.deepcopy(POLICY)
    value = {"record_id": rid, "family_key": key, "family": copy.deepcopy(family),
        "descriptor": catalog["descriptors"][key], "parent": catalog["parent_ref"],
        "selection": catalog["selection_ref"], "selection_origin": packet,
        "known_obligations": challenge._known(rid, key, catalog), "evidence_bundle": bundle,
        "direct_L2_provenance": {"record_id": rid, **bundle["bindings"][rid]}, "policy": copy.deepcopy(POLICY)}
    context = {**value, "context_sha256": provenance.digest(value)}
    if _size(prompt, context) > cap:
        raise discovery._PacketTooLarge("Complete item AND target-family source closure exceeds input cap; nothing trimmed.")
    return context


def _materialize(packet, catalog, pins, prompt, max_chars):
    return _context(packet, catalog, pins, prompt, max_chars)


def _prepare(config_ref):
    pins = {}
    config_ref = inventory._ref(config_ref)
    cfg = inventory._read(config_ref, pins)
    if cfg.get("schema_version") == "knowledge_item_family_verification_config.v1":
        raise ValueError("Item verification v1 requires historical code; create a new v2 config/run and v2 challenge selection.")
    discovery._validate(cfg, CONFIG)
    implementation, prompt = _implementation(), PROMPT_PATH.read_text(encoding="utf-8")
    safe_id(cfg["run_id"])
    cfg = {**cfg, "output_root": str(private_path(cfg["output_root"])), "selection": inventory._ref(cfg["selection"]),
           "parent": {k: inventory._ref(v) for k, v in cfg["parent"].items()}}
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    value, catalog, cells, histories, parent_manifest = challenge.load_completed(cfg["selection"], pins)
    if cfg["parent"] != value["parent"]:
        raise ValueError("Verification must pin the exact challenge target universe.")
    verifier._protect_output(root, catalog, pins)
    packets = SelectedSchedule(value, catalog, cells, histories, parent_manifest)
    catalog.update(selection=value, selection_ref=cfg["selection"], selected_count=len(packets))
    schedule = {"kind": "exact_selected_item_family_union.v1", "selection": cfg["selection"],
        "ordered_selection_sha256": value["ordered_selection_sha256"], "selected_count": len(packets),
        "planned_first_attempt_calls": len(packets), "max_pairs": cfg["max_pairs"],
        "potential_calls_with_retries": len(packets) * (cfg["retry_count"] + 1),
        "first_pass_fits_call_budget": len(packets) <= cfg["max_calls"], "lifetime_max_calls": cfg["max_calls"]}
    try:
        if len(packets) > cfg["max_pairs"]:
            raise verifier.IncompletePlan({"status": "incomplete_selected_pair_limit", "packet_plan": schedule,
                "selection": cfg["selection"], "model_calls_this_execution": 0, "policy": copy.deepcopy(POLICY)})
        checksum, largest = hashlib.sha256(), 0
        materials = discovery._materials(catalog["inventory"], pins)
        for packet in packets:
            try:
                context = _context(packet, catalog, pins, prompt, cfg["max_input_chars"], materials)
            except discovery._PacketTooLarge as exc:
                seeds = sorted({packet["record_id"], *catalog["families"][packet["family_key"]]["memberships"]})
                raise verifier.IncompletePlan({"status": "incomplete_input_overflow", "packet_plan": schedule,
                    "blocked_pair": packet, "required_case_ids": challenge._closure(seeds, catalog),
                    "detail": str(exc), "model_calls_this_execution": 0, "policy": copy.deepcopy(POLICY)}) from None
            largest = max(largest, _size(prompt, context))
            checksum.update((provenance.digest(context) + "\n").encode())
            del context
    finally:
        _check_pins(pins)
        if _implementation() != implementation:
            raise ValueError("Verification implementation changed during planning.")
    schedule.update(ordered_context_digest=checksum.hexdigest(), max_input_chars_observed=largest)
    manifest = {"schema_version": "knowledge_item_family_verification_run.v2", "config": cfg,
        "config_ref": config_ref, "implementation": implementation, "packets": schedule, "policy": copy.deepcopy(POLICY)}
    return cfg, root, catalog, packets, manifest, pins, prompt


def _derive(response, context):
    discovery._validate(response, PROPOSAL)
    if any(response[k] != context[k] for k in ("record_id", "family_key", "context_sha256")):
        raise ValueError("Verification must bind the exact item, family and reopened source context.")
    if response["relationship"] == "context_difference" and not response["conditions"]:
        raise ValueError("Context differences require explicit conditions.")
    bundle, unresolved = context["evidence_bundle"], False
    for field, identity, expected in (
        ("case_dispositions", "record_id", {c["record_id"]: c["record_id"] for c in bundle["cases"]}),
        ("counter_dispositions", "obligation_id", {o["obligation_id"]: o["record_id"] for o in bundle["known_counter_obligations"]}),
        ("known_dispositions", "obligation_id", {o["obligation_id"]: context["record_id"] for o in context["known_obligations"]})):
        seen = set()
        for row in response[field]:
            key = row[identity]
            if key not in expected or key in seen:
                raise ValueError("Every reopened case/counter/known obligation needs one exact disposition.")
            seen.add(key)
            verifier._anchors(row["source_anchors"], expected[key], bundle)
            unresolved |= row["disposition"] == "unresolved"
        if seen != expected.keys():
            raise ValueError("Missing reopened case/counter/known obligation disposition.")
    if unresolved and response["relationship"] != "unresolved":
        raise ValueError("Unresolved source obligations cannot yield a settled relationship.")
    if response["relationship"] == "unresolved" and not response["limitations"]:
        raise ValueError("Unresolved relationships need limitations.")
    case = next(c for c in bundle["cases"] if c["record_id"] == context["record_id"])
    retained = {r["record_id"] for r in response["case_dispositions"] if r["disposition"] == "retained"}
    members = context["family"]["memberships"]
    if response["relationship"] in {"support", "counter", "retelling", "evolution"}:
        if context["record_id"] not in retained or not retained.intersection(members):
            raise ValueError("Linked relationships require the inspected item and an actual target member retained.")
    if response["relationship"] == "support":
        hosts = {c["record_id"] for c in bundle["cases"] if c["speaker_attribution"] == "host"}
        supports = {rid for rid, membership in members.items() if membership["role"] == "potential_support"}
        if not retained.intersection(hosts, supports):
            raise ValueError("Support requires a retained host potential-support member of the real target family.")
    if response["relationship"] == "support" and case["speaker_attribution"] != "host":
        raise ValueError("Non-host evidence cannot become host support.")
    return copy.deepcopy(response)


def _aggregate(catalog, packets, histories, manifest):
    checksum, counts = hashlib.sha256(), dict.fromkeys(RELATIONS, 0)
    for packet in packets:
        last = histories[packet["packet_id"]][-1]
        response = runner._result(last)["response"]
        counts[response["relationship"]] += 1
        checksum.update((provenance.digest({"packet": packet, "result_sha256": last["result_sha256"]}) + "\n").encode())
    return {"schema_version": "knowledge_item_family_verification.v2",
        "status": "no_targets" if not catalog["families"] else
            "selected_verification_unresolved" if counts["unresolved"] else "declared_inspection_and_verification_complete",
        "run_sha256": provenance.digest(manifest), "selection": catalog["selection_ref"],
        "parent": manifest["config"]["parent"], "inspection_schedule": catalog["selection"]["schedule"],
        "inspection_dispositions": catalog["selection"]["disposition_counts"],
        "selected_count": len(packets), "source_verified_pair_count": sum(counts.values()),
        "unresolved_pair_count": counts["unresolved"], "pending_selected_pair_count": 0,
        "relationship_counts": counts, "ordered_result_sha256": checksum.hexdigest(),
        "case_accounting": copy.deepcopy(catalog["parent"]["case_accounting"]),
        "principle_accounting": copy.deepcopy(catalog["parent"]["principle_accounting"]),
        "family_versions": {k: v["descriptor_sha256"] for k, v in catalog["descriptors"].items()},
        "edge_location": "Immutable source-verification requests/results with direct L2 provenance; no new memberships",
        "policy": copy.deepcopy(POLICY)}


def _counts(catalog):
    return {"selected_pair_count": catalog["selected_count"], "family_count": len(catalog["families"]),
            "retained_cases": len(catalog["inventory"]["cases"]), "planned_first_attempt_calls": catalog["selected_count"]}


def execute(config_ref, *, command="plan", backend=None, max_packets=None, retry_failed=False):
    try:
        result = runner.execute(sys.modules[__name__], config_ref, command=command, backend=backend,
                                max_packets=max_packets, retry_failed=retry_failed)
    except verifier.IncompletePlan as exc:
        return exc.result
    return {**result, "pending_selected_pair_count": result["packet_count"] - result["completed_packets"],
            "source_verified_pair_count": result["completed_packets"],
            "coverage": "no_targets" if not result["family_count"] else
                "selected_source_inspection_complete_not_semantic_acceptance" if result["completed_packets"] == result["packet_count"] else "incomplete"}


def main(argv=None):
    return challenge.cli(sys.modules[__name__], argv)


if __name__ == "__main__":
    raise SystemExit(main())
