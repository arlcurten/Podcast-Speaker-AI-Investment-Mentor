"""Read-only discovery replay and native semantic shortlist interface.

No backend, pair enumeration, automatic grouping or publication. A parent spec
pins adapter module/hash, exact run/request versions, config and final artifact.
CLI plan|shortlist|expand writes a NEW private artifact. Shortlist responses are
authored externally against exact page hashes; absent pages remain pending.
Profile adapters may reuse replay_completed; source projection is domain-specific.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
from dataclasses import dataclass
from pathlib import Path

from transcription.common import atomic_write_json, child_path, private_path, safe_id, sha256_file
from . import corpus_inventory as inventory, discovery_runner as runner, native_synthesis as native, provenance
from .schemas import TEXT, STRINGS, array, enum, obj

VERSION = "knowledge_postdiscovery_bridge.v1"
POLICY = {"authority": "semantic_navigation_not_source_evidence", "semantic_review": "pending",
          "global_counter_review": "not_performed", "publication_authorized": False,
          "global_completion": False, "all_pairs_reviewed": False,
          "independent_event_count": None, "model_calls": 0}
PARENT = obj(adapter=TEXT, adapter_sha256=inventory.REF["properties"]["sha256"],
             run_version=TEXT, request_version=TEXT, config=inventory.REF, artifact=inventory.REF)
IDS = {**STRINGS, "uniqueItems": True}
PROPOSAL = obj(
    family_dispositions=array(obj(family_key=TEXT, status=enum("assigned", "pending"),
                                  group_ids=IDS, reason=TEXT)),
    groups=array(obj(group_id=TEXT, family_keys={**IDS, "minItems": 1},
                     relationship=enum("merge_candidate", "conditional_difference", "counter_check", "unresolved"),
                     reason=TEXT, conditions=STRINGS, limitations=STRINGS)))
AUTHORING = obj(page_sha256=TEXT, author=TEXT, method=TEXT, response=PROPOSAL)
PROMPT = (
    "Compare decision processes, conditions, accepted costs and contrary roles semantically. "
    "Do not group by keywords alone. Account for each supplied family exactly once as assigned "
    "or pending; multiple groups are allowed. Propose bounded source-review groups, not approved "
    "merges. Preserve support and counter roles of the same source. Unknown, ambiguous or "
    "unreviewed comparisons remain pending. Unselected families are not proven unrelated. "
    "Family labels and L3 are navigation, never independent evidence."
)


def _positive(value):
    if type(value) is not int or value < 1:
        raise ValueError("Limits must be positive integers.")


@dataclass
class Replayed:
    """In-process verified parent, reusable by a domain-specific projection."""

    adapter: object
    spec: dict
    root: Path
    catalog: dict
    artifact: dict
    manifest: dict
    pins: dict

    def check(self):
        self.adapter._check_pins(self.pins)
        if (sha256_file(Path(self.adapter.__file__)) != self.spec["adapter_sha256"]
                or self.adapter._implementation() != self.manifest["implementation"]):
            raise ValueError("Discovery implementation changed during bridge replay.")


def replay_completed(spec, *, adapter=None):
    """Replay exact adapter receipts; never cast configs or adopt partial runs.

    An explicitly supplied adapter supports synthetic tests and Profile reuse.
    Production CLI imports only a pinned module in the two discovery namespaces.
    """
    inventory._validate(spec, PARENT)
    if adapter is None:
        if not spec["adapter"].startswith(("knowledge.", "speaker_profile.")):
            raise ValueError("Expected an explicit local discovery adapter.")
        adapter = importlib.import_module(spec["adapter"])
    if (adapter.__name__ != spec["adapter"]
            or sha256_file(Path(adapter.__file__)) != spec["adapter_sha256"]
            or adapter.RUN_VERSION != spec["run_version"]
            or adapter.REQUEST_VERSION != spec["request_version"]):
        raise ValueError("Adapter identity/version differs from the pinned parent spec.")
    cfg, root, catalog, packets, manifest, pins, prompt = adapter._prepare(spec["config"])
    if manifest["schema_version"] != spec["run_version"]:
        raise ValueError("Wrong discovery run version.")
    artifact_ref = inventory._ref(spec["artifact"])
    if Path(artifact_ref["path"]) != root / "discovery.json":
        raise ValueError("Parent must be the canonical completed discovery artifact.")
    artifact = inventory._read(artifact_ref, pins)
    histories, _ = runner.read_history(adapter, root, manifest, packets, prompt, catalog, pins)
    if any(not histories.get(p["packet_id"]) or histories[p["packet_id"]][-1]["status"] != "completed"
           for p in packets):
        raise ValueError("Parent discovery is incomplete; preserve pending work.")
    if (artifact.get("status") != "bounded_discovery_complete"
            or provenance.digest(artifact) != provenance.digest(adapter._aggregate(catalog, packets, histories, manifest))):
        raise ValueError("Parent aggregate differs from replayed receipts.")
    result = Replayed(adapter, copy.deepcopy(spec), root, catalog, artifact, manifest, pins)
    result.check()
    return result


def capture_parent(config_ref, *, adapter, adapter_sha256, run_version, request_version):
    """Completion hook: capture a final artifact ref, then require full replay.

    Missing discovery.json is a pending dependency, not permission to synthesize
    a summary from partial receipts. No process is started or polled here.
    """
    pins = {}
    config_ref = inventory._ref(config_ref)
    cfg = inventory._read(config_ref, pins)
    path = child_path(private_path(cfg["output_root"]), safe_id(cfg["run_id"]), "discovery.json")
    spec = {"adapter": adapter, "adapter_sha256": adapter_sha256, "run_version": run_version,
            "request_version": request_version, "config": config_ref,
            "artifact": {"path": str(path), "sha256": sha256_file(path)}}
    parent = replay_completed(spec)
    parent.pins.update(pins)
    parent.check()
    return parent


def project(parent):
    """Project Knowledge accounting losslessly; role rows are never scalar values."""
    parent.check()
    value, catalog = parent.artifact, parent.catalog
    if value["schema_version"] != "knowledge_family_discovery.v1":
        raise ValueError("Knowledge projection requires Knowledge discovery accounting.")
    if (set(value["case_accounting"]) != set(catalog["cases"])
            or set(value["principle_accounting"]) != set(catalog["principles"])):
        raise ValueError("Parent must account for every inventory case and principle.")
    families, evidence = {}, {}
    prefix = parent.spec["artifact"]["sha256"] + ":"
    for family in value["families"]:
        key = prefix + family["family_id"]
        if key in families:
            raise ValueError("Duplicate parent family identity.")
        families[key] = {"descriptor": copy.deepcopy(family), "memberships": {}, "known_obligation_ids": []}
    for rid, row in value["case_accounting"].items():
        binding = catalog["cases"][rid]
        identity = {"record_id": rid, "artifact_sha256": binding["artifact"]["sha256"],
                    "content_pointer": binding["content_pointer"]}
        evidence[rid] = {"identity": identity, "identity_sha256": provenance.digest(identity),
                         "binding": copy.deepcopy(binding), "family_keys": []}
        seen_keys = set()
        for index, member in enumerate(row["memberships"]):
            key = prefix + member["family_id"]
            if key not in families:
                raise ValueError("Unknown family ID in parent membership.")
            occurrence = {"membership": copy.deepcopy(member),
                          "accounting_pointer": f"/case_accounting/{rid}/memberships/{index}"}
            families[key]["memberships"].setdefault(rid, []).append(occurrence)
            if key not in seen_keys:
                evidence[rid]["family_keys"].append(key)
                seen_keys.add(key)
    obligations = {}
    for pid, principle in sorted(catalog["principles"].items()):
        endpoints = set(principle["supporting_records"]) | {
            link["record_id"] for kind in ("counterexamples", "contradictions") for link in principle[kind]}
        targets = {key for rid in endpoints for key in evidence[rid]["family_keys"]}
        for kind in ("counterexamples", "contradictions"):
            for index, link in enumerate(principle[kind]):
                oid = f"{pid}:{kind}:{index}"
                obligations[oid] = {"principle_id": pid, "kind": kind, "link": copy.deepcopy(link),
                                    "supporting_records": list(principle["supporting_records"]),
                                    "parent_binding": copy.deepcopy(principle)}
                for key in sorted(targets):
                    families[key]["known_obligation_ids"].append(oid)
    parent.check()
    return {"schema_version": VERSION, "parent": copy.deepcopy(parent.spec),
            "inventory": copy.deepcopy(value["inventory"]), "families": families,
            "evidence": evidence, "known_counter_obligations": obligations,
            "case_accounting": copy.deepcopy(value["case_accounting"]),
            "principle_accounting": copy.deepcopy(value["principle_accounting"]),
            "receipts": copy.deepcopy(value["receipts"]),
            "policy": copy.deepcopy(POLICY)}


def _descriptor(bridge, key):
    family = bridge["families"][key]
    return {"family_key": key, **copy.deepcopy(family["descriptor"]),
            "memberships": copy.deepcopy(family["memberships"]),
            "known_obligation_ids": list(family["known_obligation_ids"]),
            "unique_evidence_count": len(family["memberships"]),
            "role_occurrence_count": sum(map(len, family["memberships"].values()))}


def _page(bridge, keys, bridge_sha):
    """Explicit keys allow later cross-page semantic rounds, not just initial paging."""
    if not keys or len(keys) != len(set(keys)) or not set(keys) <= bridge["families"].keys():
        raise ValueError("Page must contain unique supplied family keys.")
    return {"schema_version": "knowledge_family_navigation_page.v1", "bridge_sha256": bridge_sha,
            "prompt": PROMPT, "schema": copy.deepcopy(PROPOSAL),
            "context": {"families": [_descriptor(bridge, key) for key in keys], "policy": copy.deepcopy(POLICY)}}


def make_page(bridge, keys):
    """Build an explicit cross-page comparison request without enumerating pairs."""
    return _page(bridge, keys, provenance.digest(bridge))


def plan_pages(bridge, *, max_families, max_input_chars, family_order=None):
    """Linear bounded descriptor paging; oversized singletons stay in the ledger."""
    _positive(max_families)
    _positive(max_input_chars)
    order = sorted(bridge["families"]) if family_order is None else family_order
    if len(order) != len(set(order)) or set(order) != set(bridge["families"]):
        raise ValueError("A semantic navigation order must retain every family exactly once.")
    pages, oversized, keys = [], [], []
    bridge_sha = provenance.digest(bridge)

    def fits(page):
        return runner.input_size(page["prompt"], page["context"], page["schema"]) <= max_input_chars

    for key in order:
        trial = _page(bridge, keys + [key], bridge_sha)
        if keys and (len(keys) == max_families or not fits(trial)):
            pages.append(_page(bridge, keys, bridge_sha))
            keys = []
            trial = _page(bridge, [key], bridge_sha)
        if fits(trial):
            keys.append(key)
        else:
            oversized.append(key)
    if keys:
        pages.append(_page(bridge, keys, bridge_sha))
    return {"schema_version": "knowledge_family_navigation_plan.v1", "bridge_sha256": bridge_sha,
            "limits": {"max_families": max_families, "max_input_chars": max_input_chars,
                       "family_order": copy.deepcopy(family_order)},
            "pages": {provenance.digest(page): page for page in pages}, "pending_oversized": oversized}


def shortlist(bridge, plan, responses, *, max_group_families, max_groups_per_page):
    """Apply hash-bound semantic proposals; missing responses never imply negatives."""
    _positive(max_group_families)
    _positive(max_groups_per_page)
    if plan != plan_pages(bridge, **plan["limits"]):
        raise ValueError("Navigation plan differs from its bridge and limits.")
    if not isinstance(responses, dict) or not set(responses) <= plan["pages"].keys():
        raise ValueError("Responses must reference supplied navigation pages.")
    ledger = {key: {"state": "pending_oversized" if key in plan["pending_oversized"] else "pending_unread",
                    "groups": [], "reason": "No semantic disposition yet."} for key in bridge["families"]}
    groups = {}
    for page_id, authored in responses.items():
        inventory._validate(authored, AUTHORING)
        if authored["page_sha256"] != page_id:
            raise ValueError("Semantic response belongs to a different page.")
        page = plan["pages"][page_id]
        owned = {row["family_key"] for row in page["context"]["families"]}
        response, local = authored["response"], {}
        if len(response["groups"]) > max_groups_per_page:
            raise ValueError("Too many shortlisted groups; retain pending comparisons.")
        for group in response["groups"]:
            gid, keys = group["group_id"], set(group["family_keys"])
            if (gid in local or not keys <= owned
                    or len(keys) > max_group_families):
                raise ValueError("Group must be unique, bounded and linked to this page.")
            if group["relationship"] == "conditional_difference" and not group["conditions"]:
                raise ValueError("Conditional differences require explicit conditions.")
            if group["relationship"] == "unresolved" and not group["limitations"]:
                raise ValueError("Unresolved proposals require explicit limitations.")
            local[gid] = group
        seen = set()
        for row in response["family_dispositions"]:
            key = row["family_key"]
            expected = {gid for gid, group in local.items() if key in group["family_keys"]}
            if key not in owned or key in seen or set(row["group_ids"]) != expected:
                raise ValueError("Every page family needs exact unique group accounting.")
            if (row["status"] == "assigned") != bool(expected):
                raise ValueError("Assignment must agree with shortlisted groups.")
            seen.add(key)
            ledger[key] = {"state": "assigned_pending_source_review" if expected else "pending_ambiguous",
                           "groups": [page_id + ":" + gid for gid in sorted(expected)], "reason": row["reason"]}
        if seen != owned:
            raise ValueError("Missing family dispositions; no implicit dropping.")
        for gid, group in local.items():
            groups[page_id + ":" + gid] = {**copy.deepcopy(group), "authorship": copy.deepcopy(authored),
                                          "state": "pending_source_expansion"}
    return {"schema_version": "knowledge_family_shortlist.v1", "bridge_sha256": provenance.digest(bridge),
            "plan_sha256": provenance.digest(plan), "groups": groups, "family_ledger": ledger,
            "role_ledger": {key: {rid: [{"accounting_pointer": m["accounting_pointer"],
                                        "role": m["membership"]["role"], "state": "pending_source_review"} for m in rows]
                                   for rid, rows in family["memberships"].items()}
                            for key, family in bridge["families"].items()},
            "counter_ledger": {oid: "pending_explicit_disposition" for oid in bridge["known_counter_obligations"]},
            "case_ledger": {rid: {"state": "pending_source_review" if row["family_keys"] else "pending_ungrouped_review",
                                  "family_keys": row["family_keys"], "identity_sha256": row["identity_sha256"]}
                            for rid, row in bridge["evidence"].items()},
            "principle_ledger": {pid: "navigation_only_pending_review" for pid in bridge["principle_accounting"]},
            "limits": {"max_group_families": max_group_families, "max_groups_per_page": max_groups_per_page},
            "policy": copy.deepcopy(POLICY)}


def expand_group(parent, bridge, selection, group_key, *, plan, responses, max_bytes):
    """Complete selected-family membership plus one-incident native L3 closure.

    Overflow returns reference-only pending metadata, never a truncated bundle.
    The byte cap bounds the serialized workset, not peak materialization memory.
    """
    _positive(max_bytes)
    if bridge != project(parent) or selection["bridge_sha256"] != provenance.digest(bridge):
        raise ValueError("Workset must use the exact replayed parent bridge.")
    if selection != shortlist(bridge, plan, responses, **selection["limits"]):
        raise ValueError("Shortlist differs from its exact semantic responses.")
    group = selection["groups"][group_key]
    keys = group["family_keys"]
    selected = sorted({rid for key in keys for rid in bridge["families"][key]["memberships"]})
    required, parents = native._closure(parent.catalog, selected)
    obligations = {oid: row for oid, row in bridge["known_counter_obligations"].items()
                   if row["principle_id"] in parents}
    workset = {"schema_version": "knowledge_shortlisted_source_workset.v1", "parent": parent.spec,
               "inventory": bridge["inventory"], "bridge_sha256": provenance.digest(bridge),
               "shortlist_sha256": provenance.digest(selection), "group_key": group_key,
               "family_keys": keys, "selected_record_ids": selected, "required_record_ids": required,
               "inherited_principle_ids": parents, "known_counter_obligations": obligations,
               "role_ledger": {key: {rid: [{**copy.deepcopy(m), "state": "pending_source_review"} for m in rows]
                                      for rid, rows in bridge["families"][key]["memberships"].items()} for key in keys},
               "case_ledger": {rid: "pending_source_review" for rid in required},
               "counter_ledger": {oid: "pending_explicit_disposition" for oid in obligations},
               "counts": {"unique_selected_evidence": len(selected), "unique_closure_evidence": len(required)},
               "status": "prepared_pending_semantic_review", "policy": copy.deepcopy(POLICY)}
    bundle, _ = native._materialize(parent.catalog, required, parents, parent.pins)
    workset["evidence_bundle"] = bundle
    needed = len((json.dumps(workset, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    if needed > max_bytes:
        workset.pop("evidence_bundle")
        workset.update(status="pending_oversized", required_bytes=needed, max_bytes=max_bytes)
    parent.check()
    return workset


def _write(parent, output, value):
    path = private_path(output)
    protected = [parent.root]
    for entry in parent.catalog["episodes"].values():
        cfg = native.case_release._snapshot(entry["config"]["path"], parent.pins,
                                           entry["config"]["sha256"], yaml_config=True)
        protected.append(private_path(cfg["output_root"]) / cfg["run_id"])
        protected.extend(Path(s["artifact"]["path"]).parent for s in entry["stages"].values()
                         if s.get("artifact"))
    if any(path.is_relative_to(root) for root in protected) or path in parent.pins:
        raise ValueError("Bridge outputs must be outside discovery and source runs.")
    parent.check()
    atomic_write_json(path, value, overwrite=False)
    return {"path": str(path), "sha256": sha256_file(path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "shortlist", "expand"))
    parser.add_argument("--parent")
    parser.add_argument("--sha256")
    parser.add_argument("--config")
    parser.add_argument("--config-sha256")
    parser.add_argument("--adapter")
    parser.add_argument("--adapter-sha256")
    parser.add_argument("--run-version")
    parser.add_argument("--request-version")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-families", required=True, type=int)
    parser.add_argument("--max-input-chars", required=True, type=int)
    parser.add_argument("--responses")
    parser.add_argument("--responses-sha256")
    parser.add_argument("--family-order")
    parser.add_argument("--family-order-sha256")
    parser.add_argument("--max-group-families", type=int)
    parser.add_argument("--max-groups-per-page", type=int)
    parser.add_argument("--group-key")
    parser.add_argument("--max-workset-bytes", type=int)
    args = parser.parse_args(argv)
    pins = {}
    if args.parent:
        if not args.sha256 or any((args.config, args.config_sha256, args.adapter, args.adapter_sha256,
                                   args.run_version, args.request_version)):
            parser.error("Use --parent/--sha256 OR the explicit completion-hook config and adapter pins.")
        spec = inventory._read({"path": args.parent, "sha256": args.sha256}, pins)
        parent = replay_completed(spec)
        parent.pins.update(pins)
    else:
        if args.sha256 or not all((args.config, args.config_sha256, args.adapter, args.adapter_sha256,
                                  args.run_version, args.request_version)):
            parser.error("Completion hook requires config/hash, adapter/hash and exact run/request versions.")
        parent = capture_parent({"path": args.config, "sha256": args.config_sha256}, adapter=args.adapter,
                                adapter_sha256=args.adapter_sha256, run_version=args.run_version,
                                request_version=args.request_version)
    bridge = project(parent)
    order = None
    if args.family_order:
        if not args.family_order_sha256:
            parser.error("--family-order requires --family-order-sha256")
        order = inventory._read({"path": args.family_order, "sha256": args.family_order_sha256}, parent.pins)
    plan = plan_pages(bridge, max_families=args.max_families, max_input_chars=args.max_input_chars, family_order=order)
    selection = shortlist(bridge, plan, {}, max_group_families=1, max_groups_per_page=1)
    if args.command != "plan":
        if not args.responses or not args.responses_sha256:
            parser.error("shortlist/expand require hash-bound --responses and --responses-sha256")
        responses = inventory._read({"path": args.responses, "sha256": args.responses_sha256}, parent.pins)
        selection = shortlist(bridge, plan, responses, max_group_families=args.max_group_families,
                              max_groups_per_page=args.max_groups_per_page)
    result = {"bridge": bridge, "navigation_plan": plan, "shortlist": selection,
              "implementation_sha256": sha256_file(Path(__file__))}
    if args.command == "expand":
        if not args.group_key:
            parser.error("expand requires --group-key")
        result["workset"] = expand_group(parent, bridge, selection, args.group_key, plan=plan,
                                          responses=responses, max_bytes=args.max_workset_bytes)
    print(json.dumps(_write(parent, args.output, result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
