"""Bounded, resumable semantic family discovery over an explicit frozen inventory.

CLI: python -m knowledge.corpus_discovery plan|run|validate --config PATH --sha256 HASH
run optionally accepts --max-packets N and --retry-failed. No automatic retries.
The private knowledge_discovery_config.v1 object pins inventory, run_id,
output_root, model, reasoning_effort, timeout_seconds, max_input_chars,
max_response_chars, max_calls (lifetime admissions), and retry_count (0 or 1).
plan/validate never call a model. This module does not change existing workers.
Discovery retains one episode payload and one bounded packet at a time, plus
inventory indices, packet descriptors and final accounting. Inventory replay has
its own memory requirements; this is not a full-corpus memory-fit guarantee.
For an admitted request without a result, retain it and inspect the exact request
and provider records. Never delete it to regain budget. If no verifiable result
can be recovered, an explicitly authorized new config/run may retry the work,
accepting possible duplicate usage; the old admission remains recorded.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from transcription.common import atomic_write_json, child_path, private_path, safe_id, sha256_file
from . import case_release, codex_backend, corpus_inventory as inventory, discovery_runner, provenance
from .schemas import TEXT, STRINGS, array, enum, obj

REQUEST_VERSION = "knowledge_discovery_request.v1"
RESULT_VERSION = "knowledge_discovery_result.v1"
PROMPT_PATH = Path(__file__).parent / "prompts" / "corpus_discovery.md"
POSITIVE = {"type": "integer", "minimum": 1}
CONFIG = obj(schema_version=enum("knowledge_discovery_config.v1"), run_id=TEXT, output_root=TEXT,
    inventory=inventory.REF, model=TEXT, reasoning_effort=enum("low", "medium", "high", "xhigh"),
    timeout_seconds=POSITIVE, max_input_chars=POSITIVE, max_response_chars=POSITIVE, max_calls=POSITIVE,
    retry_count={"type": "integer", "minimum": 0, "maximum": 1})
MEMBERSHIP = obj(family_id=TEXT, role=enum("potential_support", "potential_counter", "context_only", "uncertain"), reason=TEXT)
PROPOSAL = obj(families=array(obj(family_id=TEXT, label=TEXT, decision_process=TEXT,
                                 conditions=STRINGS, accepted_costs=STRINGS, limitations=STRINGS)),
    case_dispositions=array(obj(record_id=TEXT, disposition=enum("assigned", "retained_without_family"),
                                memberships=array(MEMBERSHIP), reason=TEXT)),
    principle_dispositions=array(obj(principle_id=TEXT, disposition=enum("navigation_only", "retained_without_family"),
                                     family_ids=STRINGS, reason=TEXT)))
POLICY = {**inventory.POLICY, "scope": "bounded_semantic_family_candidates",
          "authority": "model_analyst_grouping_not_source_evidence",
          "cross_packet_reconciliation": "not_performed", "global_counter_review": "not_performed"}
POLICY.pop("model_calls")
ENVELOPE = discovery_runner.ENVELOPE


def _validate(value, schema):
    if next(Draft202012Validator(schema).iter_errors(value), None) is not None:
        raise ValueError("Invalid discovery configuration or response schema.")
    provenance.digest(value)


def _size(prompt, context):
    # Include schema characters conservatively, in addition to the backend message.
    return discovery_runner.input_size(prompt, context, PROPOSAL)


def _implementation():
    return {"discovery_sha256": sha256_file(Path(__file__)), "prompt_sha256": sha256_file(PROMPT_PATH),
            "runner_sha256": sha256_file(Path(discovery_runner.__file__)),
            "backend_sha256": sha256_file(Path(codex_backend.__file__)), "proposal_sha256": provenance.digest(PROPOSAL)}


class _Materials:
    """One-episode cache; no raw/stage payload survives eviction or packet replay."""

    def __init__(self, catalog, pins):
        self.catalog, self.pins = catalog, pins
        self.episode_id, self.cached = None, None

    def episode(self, eid):
        if eid == self.episode_id:
            return self.cached
        self.episode_id, self.cached = None, None
        entry, pins = self.catalog["episodes"][eid], self.pins
        stages = {stage: inventory._read(state["artifact"], pins) for stage, state in entry["stages"].items()}
        cases = provenance.case_catalog([stages["level2"]])
        principles = {p["principle_id"]: {**p, "qualification": stages["level3"]["qualification"][p["principle_id"]]}
                      for p in stages["level3"]["content"]["principles"]}
        cfg = case_release._snapshot(entry["config"]["path"], pins, entry["config"]["sha256"], yaml_config=True)
        original = next(row for row in cfg["episodes"] if row["episode_id"] == eid)
        doc = inventory._read({"path": original["raw"], "sha256": entry["source"]["raw_sha256"]}, pins)
        raw = {row["segment_id"]: row for row in doc["segments"]}
        source = {"source": entry["source"], "episode_map": stages["level1"]["content"],
                  "thread_dispositions": stages["level2"]["content"]["thread_dispositions"]}
        self.episode_id, self.cached = eid, (cases, principles, source, raw)
        return self.cached


def _materials(catalog, pins):
    return _Materials(catalog, pins)


class _PacketTooLarge(ValueError):
    pass


def _context(owned, catalog, materials, owners, *, prompt=None, max_chars=None):
    cases, principles = catalog["cases"], catalog["principles"]
    owned_principles = sorted(pid for pid, rid in owners.items() if rid in owned)
    seeds = set(owned) | {rid for pid in owned_principles for rid in principles[pid]["supporting_records"]}
    relevant = sorted(pid for pid, p in principles.items() if seeds.intersection(p["supporting_records"]))
    refs, obligations = set(seeds), []
    for pid in relevant:
        p = principles[pid]
        refs.update(p["supporting_records"])
        for kind, field in (("counterexample", "counterexamples"), ("contradiction", "contradictions")):
            for link in p[field]:
                refs.add(link["record_id"])
                obligations.append({"source_principle_id": pid, "kind": kind, **copy.deepcopy(link),
                    "parent_eligible_for_level4": p["qualification"]["eligible_for_level4"]})
    episode_ids = sorted({cases[rid]["episode_id"] for rid in refs})
    context = {"owned_case_ids": list(owned), "owned_principle_ids": owned_principles,
        "cases": [], "principles": [], "known_counter_obligations": obligations,
        "sources": {}, "raw_regions": {},
        "bindings": {rid: {"artifact_sha256": catalog["cases"][rid]["artifact"]["sha256"],
                           "content_pointer": catalog["cases"][rid]["content_pointer"]} for rid in sorted(refs)},
        "policy": copy.deepcopy(POLICY)}

    def check_size():
        if max_chars is not None and (size := _size(prompt, context)) > max_chars:
            raise _PacketTooLarge(f"needs at least {size} input characters including its complete "
                                  f"navigation/counter bundle; cap={max_chars}. Use a reviewed new limit/run; no truncation.")

    check_size()
    for eid in episode_ids:
        episode_cases, episode_principles, source, raw = materials.episode(eid)
        raw_ids = set()
        for rid in sorted(refs):
            if cases[rid]["episode_id"] != eid:
                continue
            case = episode_cases[rid]
            context["cases"].append(copy.deepcopy(case))
            raw_ids.update(case["evidence"]["source_segment_ids"])
            for region in case["evidence"]["source_segment_id_ranges"]:
                raw_ids.update(range(region["start_id"], region["end_id"] + 1))
        context["principles"].extend(copy.deepcopy(episode_principles[pid]) for pid in relevant
                                     if principles[pid]["episode_id"] == eid)
        context["sources"][eid] = copy.deepcopy(source)
        context["raw_regions"][eid] = [{key: raw[sid][key] for key in ("segment_id", "start", "end", "text")}
                                      for sid in sorted(raw_ids)]
        check_size()
        # Do not retain the evicted episode through this frame's loop variables.
        del episode_cases, episode_principles, source, raw
        case = None
    context["cases"].sort(key=lambda row: row["record_id"])
    context["principles"].sort(key=lambda row: row["principle_id"])
    return context


def _materialize(packet, catalog, pins, prompt, max_chars):
    owners = {pid: min(p["supporting_records"]) for pid, p in catalog["principles"].items()}
    context = _context(packet["owned_case_ids"], catalog, _materials(catalog, pins), owners,
                       prompt=prompt, max_chars=max_chars)
    if (provenance.digest(context) != packet["context_sha256"]
            or _size(prompt, context) != packet["input_chars"]
            or context["owned_principle_ids"] != packet["owned_principle_ids"]):
        raise ValueError("Rematerialized discovery packet differs from its pinned plan.")
    return context


def _prepare(config_ref):
    pins = {}
    config_ref = inventory._ref(config_ref)
    cfg = inventory._read(config_ref, pins)
    _validate(cfg, CONFIG)
    safe_id(cfg["run_id"])
    cfg = {**cfg, "output_root": str(private_path(cfg["output_root"])), "inventory": inventory._ref(cfg["inventory"])}
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    catalog = inventory.load_checked(cfg["inventory"])
    if not catalog["inventory_complete"]:
        raise ValueError("Discovery requires a complete explicit inventory; retain partial inventory gaps separately.")
    inventory._pin_file(cfg["inventory"], pins)
    for ref in catalog["dependencies"]:
        inventory._pin_file(ref, pins)
    if any(path.is_relative_to(root) for path in pins):
        raise ValueError("Discovery run directory cannot contain an input dependency.")
    for eid, entry in catalog["episodes"].items():
        original = case_release._snapshot(entry["config"]["path"], pins, entry["config"]["sha256"], yaml_config=True)
        protected = [child_path(private_path(original["output_root"]), original["run_id"])]
        for state in entry["stages"].values():
            folder = Path(state["artifact"]["path"]).parent
            protected.append(folder.parent if folder.name == eid else folder)
        if any(root.is_relative_to(folder) for folder in protected):
            raise ValueError("Discovery output must be outside extraction runs and referenced artifact directories.")
    implementation, prompt = _implementation(), PROMPT_PATH.read_text(encoding="utf-8")
    materials = _materials(catalog, pins)
    owners = {pid: min(p["supporting_records"]) for pid, p in catalog["principles"].items()}
    order = [rid for eid in catalog["expected_episode_ids"] for rid in catalog["episodes"][eid]["level2_record_ids"]]
    packets, owned, last = [], [], None
    for rid in order:
        try:
            context = _context([*owned, rid], catalog, materials, owners, prompt=prompt, max_chars=cfg["max_input_chars"])
        except _PacketTooLarge:
            if last is not None:
                packets.append(last)
            owned = [rid]
            try:
                context = _context(owned, catalog, materials, owners, prompt=prompt, max_chars=cfg["max_input_chars"])
            except _PacketTooLarge as exc:
                raise ValueError(f"Owned case {rid} {exc}") from None
        else:
            owned.append(rid)
        last = {"packet_id": f"packet_{len(packets) + 1:06d}", "context_sha256": provenance.digest(context),
                "input_chars": _size(prompt, context), "owned_case_ids": context["owned_case_ids"],
                "owned_principle_ids": context["owned_principle_ids"]}
        del context
    if last is not None:
        packets.append(last)
    manifest = {"schema_version": "knowledge_discovery_run.v1", "config": cfg, "config_ref": config_ref,
        "implementation": implementation, "policy": copy.deepcopy(POLICY),
        "packets": packets}
    case_release._check_pins(pins)
    if _implementation() != implementation:
        raise ValueError("Discovery implementation changed while planning.")
    return cfg, root, catalog, packets, manifest, pins, prompt


def _derive(response, context):
    _validate(response, PROPOSAL)
    families = [row["family_id"] for row in response["families"]]
    if len(families) != len(set(families)) or any(not re.fullmatch(r"F[1-9][0-9]*", fid) for fid in families):
        raise ValueError("Family IDs must be unique local F1, F2, ... identifiers.")
    available = {row["record_id"]: row for row in context["cases"]}
    seen, used = set(), set()
    for row in response["case_dispositions"]:
        rid = row["record_id"]
        if rid not in available or rid in seen:
            raise ValueError("Every supplied case needs exactly one disposition.")
        seen.add(rid)
        refs = [member["family_id"] for member in row["memberships"]]
        if len(refs) != len(set(refs)) or not set(refs) <= set(families):
            raise ValueError("Case memberships require unique supplied family IDs.")
        if (row["disposition"] == "assigned") != bool(refs):
            raise ValueError("Case disposition disagrees with its memberships.")
        if available[rid]["speaker_attribution"] != "host" and any(
                member["role"] in {"potential_support", "potential_counter"} for member in row["memberships"]):
            raise ValueError("Non-host cases cannot support or contradict a host pattern.")
        used.update(refs)
    if seen != available.keys() or used != set(families):
        raise ValueError("All cases must remain accounted for; families require actual case memberships.")
    expected = {row["principle_id"] for row in context["principles"]}
    seen = set()
    for row in response["principle_dispositions"]:
        pid, refs = row["principle_id"], row["family_ids"]
        if pid not in expected or pid in seen or len(refs) != len(set(refs)) or not set(refs) <= set(families):
            raise ValueError("Every supplied L3 needs unique navigation-only accounting.")
        if (row["disposition"] == "navigation_only") != bool(refs):
            raise ValueError("L3 disposition disagrees with its navigation references.")
        seen.add(pid)
    if seen != expected:
        raise ValueError("Missing L3 navigation disposition, including ineligible patterns.")
    return copy.deepcopy(response)


def _request(manifest, packet, context, attempt, prompt, previous=None):
    return discovery_runner.build_request(sys.modules[__name__], manifest, packet, context, attempt, prompt, previous)


_read_json = discovery_runner._read_json
_result = discovery_runner._result
_check_pins = case_release._check_pins


def _history(root, manifest, packets, prompt, catalog, pins):
    return discovery_runner.read_history(sys.modules[__name__], root, manifest, packets, prompt, catalog, pins)


def _check_response(response, context, cfg):
    return discovery_runner.check_response(sys.modules[__name__], response, context, cfg)


def _aggregate(catalog, packets, histories, manifest):
    families, receipts = [], []
    cases = {rid: {"memberships": [], "packet_reasons": []} for rid in catalog["cases"]}
    principles = {pid: {"family_ids": [], "packet_reasons": []} for pid in catalog["principles"]}
    for packet in packets:
        last = histories[packet["packet_id"]][-1]
        response = _result(last)["response"]
        prefix = packet["packet_id"] + ":"
        receipts.append({"path": str(last["result"]), "sha256": last["result_sha256"]})
        families.extend({**copy.deepcopy(family), "family_id": prefix + family["family_id"],
                         "packet_id": packet["packet_id"]} for family in response["families"])
        for row in response["case_dispositions"]:
            case = cases[row["record_id"]]
            case["memberships"].extend({**copy.deepcopy(m), "family_id": prefix + m["family_id"]} for m in row["memberships"])
            case["packet_reasons"].append({"packet_id": packet["packet_id"], "disposition": row["disposition"], "reason": row["reason"]})
        for row in response["principle_dispositions"]:
            principle = principles[row["principle_id"]]
            principle["family_ids"].extend(prefix + fid for fid in row["family_ids"])
            principle["packet_reasons"].append({"packet_id": packet["packet_id"], "disposition": row["disposition"], "reason": row["reason"]})
    for value in cases.values():
        if not value["packet_reasons"]:
            raise ValueError("Case missing from final discovery accounting.")
        value["disposition"] = "assigned" if value["memberships"] else "retained_without_family"
    for value in principles.values():
        if not value["packet_reasons"]:
            raise ValueError("L3 missing from final discovery accounting.")
        value["disposition"] = "navigation_only" if value["family_ids"] else "retained_without_family"
    return {"schema_version": "knowledge_family_discovery.v1", "status": "bounded_discovery_complete",
        "run_sha256": provenance.digest(manifest), "inventory": manifest["config"]["inventory"],
        "families": families, "case_accounting": cases, "principle_accounting": principles,
        "receipts": receipts, "policy": copy.deepcopy(POLICY),
        "scope": "All supplied inventory items accounted for; packet-local family proposals are not global semantic coverage."}


def _counts(catalog):
    return {"owned_cases": len(catalog["cases"]), "owned_principles": len(catalog["principles"])}


def execute(config_ref: dict, *, command="plan", backend=None, max_packets=None, retry_failed=False):
    return discovery_runner.execute(sys.modules[__name__], config_ref, command=command, backend=backend,
                                    max_packets=max_packets, retry_failed=retry_failed)


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
                          "note": "Inspect private discovery inputs and immutable receipts."}))
        return 2
    print(json.dumps({key: value for key, value in result.items() if key != "packet_plan"}))
    return 0 if result["status"] in {"planned", "completed", "validated", "paused_at_packet_limit"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
