"""Bounded native Profile discovery over a complete explicitly frozen inventory.

CLI: python -m speaker_profile.corpus_discovery plan|run|validate --config PATH --sha256 HASH
run accepts --max-packets and --retry-failed. No automatic retry or old-pin migration.
profile_discovery_config.v1 pins inventory, run_id, output_root, model,
reasoning_effort, timeout_seconds, max_input_chars, max_response_chars, max_calls
(lifetime admissions), retry_count (0 or 1). Outputs remain private candidates.
New runs use pinned compact_profile_discovery.v2 transport; max_input_chars bounds the
actual packed request (including schema/envelope). Semantic validation expands it.
Materialization caches one episode and one synthesis payload. Plans/history hold
descriptors/receipt refs only. Startup inventory replay has separate memory needs;
this is not a hard one-episode memory guarantee for inventory validation.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

from knowledge import codex_backend, corpus_inventory as shared, discovery_runner, provenance
from knowledge.schemas import TEXT, STRINGS, array, enum, obj
from transcription.common import child_path, private_path, safe_id, sha256_file
from . import corpus_inventory as inventory, discovery_annotations, serialization

REQUEST_VERSION = "profile_discovery_request.v3"
RESULT_VERSION = "profile_discovery_result.v1"
CONTEXT_FORMAT = "compact_profile_discovery.v2"
PROMPT_PATH = Path(__file__).parent / "prompts" / "corpus_discovery.md"
POSITIVE = {"type": "integer", "minimum": 1}
CONFIG = obj(schema_version=enum("profile_discovery_config.v1"), run_id=TEXT, output_root=TEXT,
    inventory=inventory.REF, model=TEXT, reasoning_effort=enum("low", "medium", "high", "xhigh"),
    timeout_seconds=POSITIVE, max_input_chars=POSITIVE, max_response_chars=POSITIVE, max_calls=POSITIVE,
    retry_count={"type": "integer", "minimum": 0, "maximum": 1})
MEMBERSHIP = obj(family_id=TEXT, role=enum("potential_support", "potential_counter", "context_only", "uncertain"), reason=TEXT)
PROPOSAL = obj(families=array(obj(family_id=TEXT, label=TEXT, conditional_pattern=TEXT,
                                 conditions=STRINGS, accepted_costs=STRINGS, limitations=STRINGS)),
    evidence_dispositions=array(obj(evidence_id=TEXT, disposition=enum("assigned", "retained_without_family"),
                                    memberships=array(MEMBERSHIP), reason=TEXT)),
    hypothesis_dispositions=array(obj(hypothesis_key=TEXT, disposition=enum("navigation_only", "retained_without_family"),
                                      family_ids=STRINGS, reason=TEXT)))
POLICY = {**inventory.POLICY, "scope": "bounded_semantic_family_candidates",
          "authority": "model_analyst_grouping_not_source_evidence",
          "cross_packet_reconciliation": "not_performed", "global_counter_review": "not_performed"}
POLICY.pop("model_calls")
_check_pins = inventory.recover_evidence._recheck


def _size(prompt, context):
    return discovery_runner.input_size(prompt, context, PROPOSAL)


def _implementation():
    return {"discovery_sha256": sha256_file(Path(__file__)), "prompt_sha256": sha256_file(PROMPT_PATH),
            "runner_sha256": sha256_file(Path(discovery_runner.__file__)),
            "backend_sha256": sha256_file(Path(codex_backend.__file__)), "proposal_sha256": provenance.digest(PROPOSAL),
            "context_format": CONTEXT_FORMAT, "codec_sha256": sha256_file(Path(serialization.__file__)),
            "base_context_format": serialization.FORMAT, "annotation_reference_format": discovery_annotations.FORMAT,
            "annotation_refs_sha256": sha256_file(Path(discovery_annotations.__file__)),
            "inventory": inventory._implementation()}


def _pack(context):
    if any(eid != row["source"]["episode_id"] for eid, row in context["sources"].items()):
        raise ValueError("Discovery source keys must match their episodes.")
    if {"context_format", "base_context_format", "annotation_reference_format", "serialization_legend"} & context.keys():
        raise ValueError("Plain discovery context contains reserved transport metadata.")
    factored = discovery_annotations.factor(context)
    if not discovery_annotations.same(discovery_annotations.restore(factored), context):
        raise ValueError("Annotation references failed exact round-trip validation.")
    packed = serialization.compact({**factored, "sources": list(factored["sources"].values())})
    packed.update(context_format=CONTEXT_FORMAT, base_context_format=serialization.FORMAT,
                  annotation_reference_format=discovery_annotations.FORMAT)
    packed["serialization_legend"] += "\n" + discovery_annotations.LEGEND
    return packed


def _expand(context):
    """Require the exact lossless wire form before applying plain semantic rules."""
    try:
        base = dict(context)
        if (base.get("context_format") != CONTEXT_FORMAT
                or base.pop("base_context_format") != serialization.FORMAT
                or base.pop("annotation_reference_format") != discovery_annotations.FORMAT):
            raise ValueError("Unknown discovery annotation transport.")
        base["context_format"] = serialization.FORMAT
        plain = serialization.expand(base)
        sources = plain["sources"]
        plain["sources"] = {row["source"]["episode_id"]: row for row in sources}
        if len(plain["sources"]) != len(sources):
            raise ValueError("Duplicate discovery source episodes.")
        plain = discovery_annotations.restore(plain)
        if not discovery_annotations.same(_pack(plain), context):
            raise ValueError("Noncanonical compact discovery context.")
        return plain
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Invalid compact discovery context.") from exc


def _pointer(value, pointer):
    for part in pointer.lstrip("/").split("/"):
        value = value[int(part)] if isinstance(value, list) else value[part.replace("~1", "/").replace("~0", "~")]
    return value


class _Materials:
    """Evict before reading the next episode/synthesis; each reread checks its hash."""

    def __init__(self, catalog, pins):
        self.catalog, self.pins = catalog, pins
        self.episode_id, self.cached = None, None
        self.synthesis_ref, self.synthesis = None, None

    def episode(self, eid):
        if eid == self.episode_id:
            return self.cached
        self.episode_id, self.cached = None, None
        entry = self.catalog["episodes"][eid]
        artifact = shared._read(entry["evidence"]["input"]["artifact"], self.pins)
        manifest = shared._read(entry["original_manifest"], self.pins)
        original = next(e for e in manifest["config"]["episodes"] if e["episode_id"] == eid)
        raw = shared._read({"path": original["raw"], "sha256": entry["source_hashes"]["raw_sha256"]}, self.pins)
        self.episode_id, self.cached = eid, (artifact, {r["segment_id"]: r for r in raw["segments"]})
        return self.cached

    def hypothesis(self, key):
        entry = self.catalog["hypotheses"][key]
        ref = entry["artifact"]
        if self.synthesis_ref != ref:
            self.synthesis_ref, self.synthesis = None, None
            self.synthesis = shared._read(ref, self.pins)
            self.synthesis_ref = ref
        return {**copy.deepcopy(_pointer(self.synthesis, entry["content_pointer"])),
                "hypothesis_key": key, "artifact_kind": entry["artifact_kind"],
                "authority": self.synthesis.get("authority", "model_synthesis_candidate"),
                "policy": copy.deepcopy(self.synthesis["policy"]),
                "grounding": copy.deepcopy(entry["grounding"]),
                "recovered_comparisons": copy.deepcopy(entry["recovered_comparisons"])}


class _PacketTooLarge(ValueError):
    pass


def _raw_ids(grounding):
    result = set(grounding["source_segment_ids"])
    for region in grounding["source_segment_id_ranges"]:
        result.update(range(region["start_id"], region["end_id"] + 1))
    for note in grounding.get("supplementary_context", []):
        result.update(_raw_ids(note["grounding"]))
    return result


def _context(owned, catalog, materials, inventory_ref, *, prompt, max_chars):
    closure = inventory.checked_index_closure(catalog, inventory_ref, owned)
    owners = {key: min(h["supporting_evidence"]) for key, h in catalog["hypotheses"].items()}
    refs = closure["required_evidence_ids"]
    context = {"owned_evidence_ids": list(owned),
        "owned_hypothesis_keys": sorted(key for key, rid in owners.items() if rid in owned),
        "evidence": [], "hypotheses": [], "known_relationship_closure": closure,
        "sources": {}, "raw_regions": {}, "bindings": {
            rid: {k: copy.deepcopy(catalog["evidence"][rid][k]) for k in
                  ("evidence_key", "artifact", "content_pointer", "grounding_pointer")} for rid in refs},
        "policy": copy.deepcopy(POLICY)}

    def check_size():
        packed = _pack(context)
        if (size := _size(prompt, packed)) > max_chars:
            raise _PacketTooLarge(f"needs at least {size} input characters for complete relationship/context closure; "
                                  f"cap={max_chars}. Use a reviewed new limit/run; no truncation.")
        return packed

    check_size()
    for key in closure["matched_hypothesis_keys"]:
        context["hypotheses"].append(materials.hypothesis(key))
        check_size()
    for eid in sorted({catalog["evidence"][rid]["episode_id"] for rid in refs}):
        artifact, raw = materials.episode(eid)
        context["sources"][eid] = {"source": copy.deepcopy(artifact["source"]),
            "policy": copy.deepcopy(artifact["policy"]),
            "unselected_regions": copy.deepcopy(artifact["grounding"]["unselected_regions"])}
        raw_ids = set()
        for rid in refs:
            entry = catalog["evidence"][rid]
            if entry["episode_id"] != eid:
                continue
            grounding = _pointer(artifact, entry["grounding_pointer"])
            context["evidence"].append({**copy.deepcopy(_pointer(artifact, entry["content_pointer"])),
                "episode_id": eid, "evidence_key": entry["evidence_key"], "artifact_kind": entry["artifact_kind"],
                "authority": catalog["episodes"][eid]["authority"], "grounding": copy.deepcopy(grounding)})
            raw_ids.update(_raw_ids(grounding))
            check_size()
        context["raw_regions"][eid] = [{k: raw[sid][k] for k in ("segment_id", "start", "end", "text")}
                                      for sid in sorted(raw_ids)]
        check_size()
        del artifact, raw, grounding
    context["evidence"].sort(key=lambda row: row["evidence_id"])
    return check_size()


def _materialize(packet, catalog, pins, prompt, max_chars):
    context = _context(packet["owned_evidence_ids"], catalog, _Materials(catalog, pins), packet["inventory"],
                       prompt=prompt, max_chars=max_chars)
    if (provenance.digest(context) != packet["context_sha256"] or _size(prompt, context) != packet["input_chars"]
            or context["owned_hypothesis_keys"] != packet["owned_hypothesis_keys"]):
        raise ValueError("Rematerialized Profile packet differs from its pinned plan.")
    return context


def _prepare(config_ref):
    pins = {}
    config_ref = shared._ref(config_ref)
    cfg = shared._read(config_ref, pins)
    shared._validate(cfg, CONFIG)
    safe_id(cfg["run_id"])
    cfg = {**cfg, "output_root": str(private_path(cfg["output_root"])), "inventory": shared._ref(cfg["inventory"])}
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    catalog = inventory.load_checked(cfg["inventory"])
    if not catalog["inventory_complete"]:
        raise ValueError("Profile discovery requires a complete explicit inventory; preserve gaps separately.")
    for ref in [cfg["inventory"], *catalog["dependencies"]]:
        # Inventory replay also pins the one public terminology table, not corpus data.
        if Path(ref["path"]).resolve() == inventory.pipeline.common.TERMINOLOGY.resolve():
            pins[Path(ref["path"])] = ref["sha256"]
        else:
            shared._pin_file(ref, pins)
    protected = [Path(p) for p in catalog["protected_output_roots"]]
    protected.extend(Path(row["artifact"]["path"]).parent for row in
                     [*catalog["evidence"].values(), *catalog["hypotheses"].values()])
    if any(path.is_relative_to(root) for path in pins) or any(root.is_relative_to(p) for p in protected):
        raise ValueError("Discovery output must be outside original runs and input artifact directories.")
    implementation, prompt = _implementation(), PROMPT_PATH.read_text(encoding="utf-8")
    materials = _Materials(catalog, pins)
    order = [rid for eid in catalog["expected_episode_ids"] for rid in catalog["episodes"][eid]["evidence_ids"]]
    packets, owned, last = [], [], None
    for rid in order:
        try:
            context = _context([*owned, rid], catalog, materials, cfg["inventory"],
                               prompt=prompt, max_chars=cfg["max_input_chars"])
        except _PacketTooLarge:
            if last is not None:
                packets.append(last)
            owned = [rid]
            try:
                context = _context(owned, catalog, materials, cfg["inventory"],
                                   prompt=prompt, max_chars=cfg["max_input_chars"])
            except _PacketTooLarge as exc:
                raise ValueError(f"Owned evidence {rid} {exc}") from None
        else:
            owned.append(rid)
        last = {"packet_id": f"packet_{len(packets) + 1:06d}", "inventory": cfg["inventory"],
                "context_sha256": provenance.digest(context), "input_chars": _size(prompt, context),
                "owned_evidence_ids": context["owned_evidence_ids"], "owned_hypothesis_keys": context["owned_hypothesis_keys"]}
        del context
    if last is not None:
        packets.append(last)
    manifest = {"schema_version": "profile_discovery_run.v3", "config": cfg, "config_ref": config_ref,
                "implementation": implementation, "policy": copy.deepcopy(POLICY), "packets": packets}
    _check_pins(pins)
    if _implementation() != implementation:
        raise ValueError("Profile discovery implementation changed while planning.")
    return cfg, root, catalog, packets, manifest, pins, prompt


def _derive(response, context):
    return _derive_plain(response, _expand(context))


def _derive_plain(response, context):
    shared._validate(response, PROPOSAL)
    families = [row["family_id"] for row in response["families"]]
    if len(families) != len(set(families)) or any(not re.fullmatch(r"F[1-9][0-9]*", fid) for fid in families):
        raise ValueError("Family IDs must be unique local F1, F2, ... identifiers.")
    available = {row["evidence_id"]: row for row in context["evidence"]}
    seen, used = set(), set()
    for row in response["evidence_dispositions"]:
        rid = row["evidence_id"]
        if rid not in available or rid in seen:
            raise ValueError("Every supplied evidence item needs exactly one disposition.")
        seen.add(rid)
        refs = [member["family_id"] for member in row["memberships"]]
        if len(refs) != len(set(refs)) or not set(refs) <= set(families):
            raise ValueError("Evidence memberships require unique supplied family IDs.")
        if (row["disposition"] == "assigned") != bool(refs):
            raise ValueError("Evidence disposition disagrees with its memberships.")
        if available[rid]["speaker_attribution"] != "host" and any(
                m["role"] in {"potential_support", "potential_counter"} for m in row["memberships"]):
            raise ValueError("Non-host evidence cannot support or contradict a host pattern.")
        used.update(refs)
    if seen != available.keys() or used != set(families):
        raise ValueError("All evidence must remain accounted for; families require evidence memberships.")
    expected = {row["hypothesis_key"] for row in context["hypotheses"]}
    seen = set()
    for row in response["hypothesis_dispositions"]:
        key, refs = row["hypothesis_key"], row["family_ids"]
        if key not in expected or key in seen or len(refs) != len(set(refs)) or not set(refs) <= set(families):
            raise ValueError("Every supplied hypothesis needs unique SHA-qualified navigation accounting.")
        if (row["disposition"] == "navigation_only") != bool(refs):
            raise ValueError("Hypothesis disposition disagrees with its navigation references.")
        seen.add(key)
    if seen != expected:
        raise ValueError("Missing hypothesis navigation disposition.")
    return copy.deepcopy(response)


def _aggregate(catalog, packets, histories, manifest):
    families, receipts = [], []
    evidence = {rid: {"evidence_key": row["evidence_key"], "owner_packet_id": None,
                     "memberships": [], "packet_reasons": []} for rid, row in catalog["evidence"].items()}
    hypotheses = {key: {"owner_packet_id": None, "family_ids": [], "packet_reasons": []} for key in catalog["hypotheses"]}
    for packet in packets:
        pid = packet["packet_id"]
        for ids, index in ((packet["owned_evidence_ids"], evidence), (packet["owned_hypothesis_keys"], hypotheses)):
            for rid in ids:
                if index[rid]["owner_packet_id"] is not None:
                    raise ValueError("Duplicate Profile discovery ownership.")
                index[rid]["owner_packet_id"] = pid
        last = histories[pid][-1]
        response = discovery_runner._result(last)["response"]
        prefix = pid + ":"
        receipts.append({"path": str(last["result"]), "sha256": last["result_sha256"]})
        families.extend({**copy.deepcopy(f), "family_id": prefix + f["family_id"], "packet_id": pid}
                        for f in response["families"])
        for row in response["evidence_dispositions"]:
            entry = evidence[row["evidence_id"]]
            entry["memberships"].extend({**copy.deepcopy(m), "family_id": prefix + m["family_id"]}
                                        for m in row["memberships"])
            entry["packet_reasons"].append({"packet_id": pid, "reference_only": row["evidence_id"] not in packet["owned_evidence_ids"],
                                           "disposition": row["disposition"], "reason": row["reason"]})
        for row in response["hypothesis_dispositions"]:
            entry = hypotheses[row["hypothesis_key"]]
            entry["family_ids"].extend(prefix + fid for fid in row["family_ids"])
            entry["packet_reasons"].append({"packet_id": pid, "reference_only": row["hypothesis_key"] not in packet["owned_hypothesis_keys"],
                                           "disposition": row["disposition"], "reason": row["reason"]})
        del response
    for index, field, assigned in ((evidence, "memberships", "assigned"), (hypotheses, "family_ids", "navigation_only")):
        for entry in index.values():
            if entry["owner_packet_id"] is None or not entry["packet_reasons"]:
                raise ValueError("Profile item missing from final owned accounting.")
            entry["disposition"] = assigned if entry[field] else "retained_without_family"
    return {"schema_version": "profile_family_discovery.v1", "status": "bounded_discovery_complete",
        "run_sha256": provenance.digest(manifest), "inventory": manifest["config"]["inventory"],
        "families": families, "evidence_accounting": evidence, "hypothesis_accounting": hypotheses,
        "receipts": receipts, "policy": copy.deepcopy(POLICY),
        "scope": "All supplied inventory items accounted for; packet-local proposals are not global semantic coverage or profile approval."}


def _counts(catalog):
    return {"owned_evidence": len(catalog["evidence"]), "owned_hypotheses": len(catalog["hypotheses"])}


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
                          "note": "Inspect private Profile inventory and immutable discovery receipts."}))
        return 2
    print(json.dumps({key: value for key, value in result.items() if key != "packet_plan"}))
    return 0 if result["status"] in {"planned", "completed", "validated", "paused_at_packet_limit"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
