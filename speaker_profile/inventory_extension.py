"""Additive v3 inventory over a checked v2 base and explicit native syntheses.

Existing loaders/contracts are unchanged. New consumers must opt in to this
contract, including its native comparisons and pending/context obligations.
Metadata preflight precedes source loaders, not all source bytes: native packets
contain embedded raw text. Limits fail explicitly; no truncation or model calls.
Only current v1 native syntheses with v2 inventory parents are supported.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from knowledge.schemas import TEXT, array, enum, obj
from transcription.common import atomic_write_json, private_path, safe_id, sha256_file
from . import corpus_inventory as base, native_synthesis as synthesis

native, pipeline, recovery, shared = base.native_evidence, base.pipeline, base.recover_evidence, base.shared
VERSION = "profile_corpus_inventory.v3"
SELECTION_VERSION = "profile_inventory_extension_selection.v1"
LIMITS = obj(**{key: {"type": "integer", "minimum": 1} for key in
               ("max_nodes", "max_depth", "max_metadata_bytes")})
SELECTION = obj(schema_version=enum(SELECTION_VERSION), corpus_id=TEXT, base_inventory=base.REF,
    limits=LIMITS,
    reserved_episode_ids={**array(TEXT), "uniqueItems": True},
    expected_synthesis_ids={**array(TEXT, 1), "uniqueItems": True},
    native_syntheses=array(obj(synthesis_id=TEXT, kind=enum("native"), artifact=base.REF), 1))
POLICY = {**base.POLICY, "eligible_for_serving": False, "publication_authorized": False,
          "extension_scope": "Known candidate lineage only; not global synthesis or source/semantic approval",
          "closure_scope": "One incident pass over original seeds/bases, both endpoints and required prior context"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _ref(ref):
    result = shared._ref(ref)
    _require(str(Path(ref["path"])) == result["path"], "Use canonical absolute private paths, not aliases/symlinks.")
    return result


def _implementation():
    return {"extension_sha256": sha256_file(Path(__file__)), "base": base._implementation(),
            "native_synthesis": synthesis._implementation(), "selection_schema_sha256": pipeline.digest(SELECTION)}


class _Graph:
    """One bounded metadata walk; a single active stack spans typed boundaries."""
    def __init__(self, limits):
        shared._validate(limits, LIMITS)
        self.limits = limits
        self.pins, self.nodes, self.roles = {}, {}, {}
        self.active, self.reserved, self.episodes, self.protected = set(), set(), set(), set()
        self.bytes = 0

    def leaf(self, ref):
        ref = _ref(ref)
        path = Path(ref["path"])
        _require(path not in self.active, "Cyclic extension lineage.")
        recovery._pin(self.pins, path, ref["sha256"])
        _require(len(self.pins) <= self.limits["max_nodes"], "Extension dependency node limit exceeded.")
        return ref

    def walk(self, ref, role, descriptor=None):
        ref = _ref(ref)
        path = Path(ref["path"])
        _require(path not in self.active, "Cyclic extension lineage.")
        self.leaf(ref)
        binding = (role, descriptor)
        _require(path not in self.roles or self.roles[path] == binding, "Conflicting typed dependency.")
        if path in self.nodes:
            return self.nodes[path]
        _require(len(self.active) < self.limits["max_depth"], "Extension dependency depth limit exceeded.")
        self.roles[path] = copy.deepcopy(binding)
        self.active.add(path)
        remaining = self.limits["max_metadata_bytes"] - self.bytes
        if role in {"native_synthesis", "synthesis_submission", "synthesis_config", "evidence:native",
                    "evidence_submission", "packet", "evidence_config", "extension"}:
            remaining = min(native.MAX_JSON_BYTES, remaining)
        _require(remaining > 0, "Extension metadata byte limit exceeded.")
        value = native._read(ref, self.pins, limit=remaining)
        self.bytes += path.stat().st_size
        _require(self.bytes <= self.limits["max_metadata_bytes"], "Extension metadata byte limit exceeded.")
        self._visit(value, role, descriptor)
        self.active.remove(path)
        self.nodes[path] = value
        return value

    def _ids(self, selection):
        self.reserved.update(selection["reserved_episode_ids"])
        for key, rows, field in (("expected_episode_ids", "episodes", "episode_id"),
                                 ("expected_synthesis_ids", "syntheses", "synthesis_id")):
            actual = [r[field] for r in selection[rows]]
            _require(len(actual) == len(set(actual)) and set(actual) == set(selection[key]),
                     "Selection must account for every expected identity exactly once.")
        self.episodes.update(selection["expected_episode_ids"])

    def _descriptor(self, descriptor, evidence):
        shared._validate(descriptor, base.EVIDENCE if evidence else base.SYNTHESIS)
        role = ("evidence:" if evidence else "synthesis:") + descriptor["kind"]
        return self.walk(descriptor["artifact"], role, descriptor)

    def _config(self, cfg):
        cfg = pipeline._config(cfg)
        self.reserved.update(cfg["reserved_episode_ids"])
        self.episodes.update(e["episode_id"] for e in cfg["episodes"])
        self.protected.add(str(Path(cfg["output_root"]) / cfg["run_id"]))
        for e in cfg["episodes"]:
            for key in ("raw", "draft", "source_review"):
                if e[key] is not None:
                    self.leaf({"path": e[key], "sha256": e[key + "_sha256"]})
        if "reuse_evidence" in cfg:
            reuse = cfg["reuse_evidence"]
            self.walk(reuse["manifest"], "manifest")
            for ref in reuse["artifacts"].values():
                self._descriptor({"kind": "completed", "artifact": ref, "manifest": reuse["manifest"]}, True)
        for ref in cfg.get("reuse_recovered_evidence", {}).values():
            self._descriptor({"kind": "recovered", "artifact": ref}, True)
        if "reuse_failed_synthesis" in cfg:
            reuse = cfg["reuse_failed_synthesis"]
            self.walk(reuse["manifest"], "manifest")
            for key in ("request", "result"):
                self.leaf(reuse[key])
        return cfg

    def _visit(self, v, role, descriptor):
        if role == "extension":
            shared._validate(v, SELECTION)
            safe_id(v["corpus_id"])
            ids = [r["synthesis_id"] for r in v["native_syntheses"]]
            _require(len(ids) == len(set(ids)) and set(ids) == set(v["expected_synthesis_ids"]), "Invalid synthesis selection.")
            _require(len({r["artifact"]["sha256"] for r in v["native_syntheses"]}) == len(ids), "Duplicate synthesis artifact.")
            for sid in ids:
                safe_id(sid)
            self.reserved.update(v["reserved_episode_ids"])
            self.walk(v["base_inventory"], "inventory")
            for row in v["native_syntheses"]:
                self.walk(row["artifact"], "native_synthesis")
        elif role == "inventory":
            _require(v.get("schema_version") == base.VERSION, "Only checked v2 inventory parents are supported; no implicit migration.")
            self.walk(v["selection"], "selection")
        elif role == "selection":
            shared._validate(v, base.SELECTION)
            self._ids(v)
            for collection, key in (("episodes", "evidence"), ("syntheses", "synthesis")):
                for row in v[collection]:
                    state = row[key]
                    if state["status"] == "available":
                        artifact = self._descriptor(state["input"], key == "evidence")
                        if key == "evidence":
                            _require(artifact["source"]["episode_id"] == row["episode_id"], "Evidence selection identity differs.")
                    elif state["record"] is not None:
                        self.leaf(state["record"])
        elif role == "native_synthesis":
            _require(v.get("schema_version") == synthesis.VERSION and v.get("stage") == "synthesis", "Expected native synthesis candidate.")
            submission = self.walk(v["submission"], "synthesis_submission")
            cfg = self.walk(submission["config"], "synthesis_config")
            _require(_ref(v["config"]) == _ref(submission["config"]) and _ref(v["inventory"]) == _ref(cfg["inventory"]),
                     "Native synthesis duplicated lineage headers differ.")
        elif role == "synthesis_submission":
            synthesis._validate(v, synthesis.submission_schema())
            self.walk(v["config"], "synthesis_config")
        elif role == "synthesis_config":
            synthesis._validate(v, synthesis.CONFIG_SCHEMA)
            self.walk(v["inventory"], "inventory")
            for report in v["reports"]:
                self.leaf(report["ref"])
        elif role == "evidence:native":
            _require(v.get("schema_version") == native.VERSION and v.get("stage") == "evidence", "Expected native evidence candidate.")
            submission = self.walk(v["submission"], "evidence_submission")
            packet = self.walk(submission["packet"], "packet")
            cfg = self.walk(packet["config"], "evidence_config")
            _require(_ref(v["packet"]) == _ref(submission["packet"])
                     and v["source"]["episode_id"] == submission["content"]["episode_id"] == cfg["episode"]["episode_id"],
                     "Native evidence authoritative lineage identity differs.")
        elif role == "evidence_submission":
            native._validate(v, native.submission_schema())
            self.walk(v["packet"], "packet")
        elif role == "packet":
            _require(v.get("schema_version") == native.PACKET_VERSION, "Expected native packet.")
            self.walk(v["config"], "evidence_config")
        elif role == "evidence_config":
            native._validate(v, native.CONFIG_SCHEMA)
            self.reserved.update(v["reserved_episode_ids"])
            self.episodes.add(v["episode"]["episode_id"])
            for key in ("raw", "normalized", "source_review"):
                if v["episode"][key] is not None:
                    self.leaf(v["episode"][key])
        elif role == "manifest":
            _require(v.get("schema_version") == "profile_run.v1", "Expected original Profile manifest.")
            self._config(v["config"])
        elif role == "legacy_config":
            self._config(v)
        elif role == "evidence_revision_config":
            shared._validate(v, base.evidence_revision.CONFIG_SCHEMA)
            self._descriptor(v["parent"], True)
        elif role in ("recovery_proposal", "review_proposal", "review_recovered_proposal"):
            module = {"recovery_proposal": base.recover_synthesis, "review_proposal": base.review,
                      "review_recovered_proposal": base.review_recovered}[role]
            shared._validate(v, module.PROPOSAL_SCHEMA)
            if role == "recovery_proposal":
                self.walk(v["manifest"], "manifest")
                self.leaf(v["request"])
                self.leaf(v["result"])
            elif role == "review_recovered_proposal":
                self._descriptor({"kind": "recovered", "artifact": v["parent_artifact"]}, False)
            else:
                cfg = self.walk(v["parent_config"], "legacy_config")
                path = private_path(Path(cfg["output_root"]) / cfg["run_id"] / "run_manifest.json")
                manifest = {"path": str(path), "sha256": sha256_file(path)}
                self._descriptor({"kind": "completed", "artifact": v["parent_artifact"], "manifest": manifest}, False)
        else:
            evidence, kind = role.startswith("evidence:"), descriptor["kind"]
            versions = ({"completed": base.schemas.VERSION, "recovered": recovery.VERSION,
                         "editorial": base.evidence_revision.VERSION} if evidence else
                        {"completed": base.schemas.VERSION, "recovered": base.recover_synthesis.VERSION,
                         "editorial_completed": "profile_reviewed_revision.v1",
                         "editorial_recovered": base.review_recovered.VERSION})
            _require(v.get("schema_version") == versions.get(kind)
                     and v.get("stage", None if evidence else "synthesis") == ("evidence" if evidence else "synthesis"),
                     "Artifact kind/schema/stage differs.")
            if kind == "completed":
                self.walk(descriptor["manifest"], "manifest")
            elif evidence and kind == "recovered":
                cfg = recovery._config(v["recovery_config"])
                self.walk(cfg["manifest"], "manifest")
                self.leaf(cfg["request"])
                self.leaf(cfg["result"])
            elif evidence:
                self.walk(v["config"], "evidence_revision_config")
            else:
                self.walk(v["proposal"], {"recovered": "recovery_proposal", "editorial_completed": "review_proposal",
                                           "editorial_recovered": "review_recovered_proposal"}[kind])

    def finish(self):
        for eid in self.reserved | self.episodes:
            safe_id(eid)
        _require(not self.reserved & self.episodes, "Reserved episode in authoritative dependency configuration.")
        recovery._recheck(self.pins)


def _join(index, candidate, parent):
    """Exact descriptors AND validated source lineage, never an ID-only rebind."""
    for eid, descriptor in candidate["evidence"].items():
        current = index["episodes"].get(eid, {}).get("evidence", {})
        original = parent["episodes"].get(eid)
        _require(current == {"status": "available", "input": descriptor} and original is not None
                 and index["episodes"][eid] == original, "Native synthesis evidence version/source lineage differs from base.")
    ids = {rid for eid in candidate["evidence"] for rid in index["episodes"][eid]["evidence_ids"]}
    _require(set(candidate["grounding"]["retained_evidence_ids"]) == ids
             and {r["evidence_id"] for r in candidate["content"]["evidence_accounting"]} == ids,
             "Native synthesis accounting differs from its exact evidence subset.")


def _selection(ref):
    pins = {}
    value = native._read(_ref(ref), pins)
    shared._validate(value, SELECTION)
    return value


def inspect(selection_ref: dict) -> dict:
    implementation = _implementation()
    selection_ref = _ref(selection_ref)
    graph = _Graph(_selection(selection_ref)["limits"])
    selection = graph.walk(selection_ref, "extension")
    graph.finish()  # No source loader is called until the entire graph is checked.
    index = base.load_checked(selection["base_inventory"])
    _require(index == graph.nodes[Path(selection["base_inventory"]["path"])], "Base changed during replay.")
    result = copy.deepcopy(index)
    result.update(schema_version=VERSION, corpus_id=selection["corpus_id"], selection=selection_ref,
                  base_inventory=_ref(selection["base_inventory"]), base_implementation=copy.deepcopy(index["implementation"]),
                  reserved_episode_ids=sorted(graph.reserved), native_comparisons={}, native_pending_comparisons={},
                  native_context={}, implementation=implementation, policy=copy.deepcopy(POLICY))
    for ref in index["dependencies"]:
        recovery._pin(graph.pins, Path(ref["path"]), ref["sha256"])
    seen = {row["synthesis"]["input"]["artifact"]["sha256"] for row in index["syntheses"].values()
            if row["synthesis"]["status"] == "available"}
    for row in selection["native_syntheses"]:
        sid, ref = row["synthesis_id"], _ref(row["artifact"])
        _require(sid not in result["syntheses"] and ref["sha256"] not in seen, "Duplicate synthesis ID/artifact.")
        seen.add(ref["sha256"])
        candidate = synthesis.load_checked(ref, graph.pins)
        _require(candidate == graph.nodes[Path(ref["path"])], "Native candidate changed during replay.")
        # load_checked already replays this parent; metadata was pinned before it ran.
        parent = graph.nodes[Path(candidate["inventory"]["path"])]
        _join(index, candidate, parent)
        prefix, keys = ref["sha256"] + ":", []
        anchors = {h["hypothesis_id"]: h for h in candidate["hypothesis_anchors"]}
        for number, h in enumerate(candidate["content"]["hypotheses"]):
            key = prefix + h["hypothesis_id"]
            _require(key not in result["hypotheses"], "Duplicate hypothesis key.")
            keys.append(key)
            result["hypotheses"][key] = {**copy.deepcopy(h), "artifact": ref, "artifact_kind": "native",
                "content_pointer": f"/content/hypotheses/{number}",
                "grounding": copy.deepcopy(candidate["grounding"]["hypotheses"][h["hypothesis_id"]]),
                "anchor_binding": copy.deepcopy(anchors[h["hypothesis_id"]]),
                "recovered_comparisons": [], "authority": candidate["authority"], "policy": copy.deepcopy(candidate["policy"])}
        for number, comparison in enumerate(candidate["comparison_ledger"]):
            result["native_comparisons"][prefix + comparison["comparison_id"]] = {
                "artifact": ref, "pointer": f"/comparison_ledger/{number}", "comparison": copy.deepcopy(comparison)}
        for number, task in enumerate(candidate["comparison_coverage"]):
            if task["status"] == "pending":
                result["native_pending_comparisons"][prefix + task["comparison_id"]] = {
                    "artifact": ref, "pointer": f"/comparison_coverage/{number}", "task": copy.deepcopy(task)}
        for number, context in enumerate(candidate["known_context"]):
            result["native_context"][prefix + str(number)] = {
                "artifact": ref, "pointer": f"/known_context/{number}", "context": copy.deepcopy(context),
                "prior_hypothesis": copy.deepcopy(candidate["prior_hypotheses"][context["prior_hypothesis_key"]])}
        retained = ("authorship", "reports", "reading", "comparison_coverage", "comparison_ledger", "known_context",
                    "prior_hypotheses", "hypothesis_anchors", "source_context", "inventory_gaps", "model_metadata",
                    "adoption", "policy", "authority", "config", "submission", "inventory", "implementation")
        result["syntheses"][sid] = {"synthesis": {"status": "available", "input": {"kind": "native", "artifact": ref}},
            "original_manifest": None, "hypothesis_keys": keys, "evidence": copy.deepcopy(candidate["evidence"]),
            "schema_version": candidate["schema_version"], "evidence_accounting": copy.deepcopy(candidate["content"]["evidence_accounting"]),
            **{k: copy.deepcopy(candidate[k]) for k in retained}}
        result["expected_synthesis_ids"].append(sid)
        graph.protected.add(str(Path(ref["path"]).parent))
        graph.protected.update(parent["protected_output_roots"])
    result["protected_output_roots"] = sorted(set(index["protected_output_roots"]) | graph.protected)
    result["dependencies"] = native._refs(graph.pins)
    _require(len(graph.pins) <= selection["limits"]["max_nodes"], "Extension dependency node limit exceeded during replay.")
    recovery._recheck(graph.pins)
    _require(_implementation() == implementation, "Extension implementation changed during replay.")
    _require(native._size(result) <= selection["limits"]["max_metadata_bytes"], "Extension output exceeds byte limit; no truncation.")
    return result


def freeze(selection_ref: dict, output: str | Path) -> dict:
    path = private_path(output)
    if path.exists():
        raise FileExistsError("Choose a new private extension output.")
    result = inspect(selection_ref)
    pins = {Path(r["path"]): r["sha256"] for r in result["dependencies"]}
    _require(path not in pins and not any(path.is_relative_to(Path(p)) for p in result["protected_output_roots"]),
             "Output overlaps protected inputs/artifact directories.")
    _require(private_path(output) == path and _implementation() == result["implementation"], "Output/implementation changed.")
    recovery._recheck(pins)
    atomic_write_json(path, result, overwrite=False)
    ref = {"path": str(path), "sha256": sha256_file(path)}
    # Read-back checks bytes; checked reconstruction was completed immediately above.
    budget = _selection(selection_ref)["limits"]["max_metadata_bytes"]
    _require(native._read(ref, {}, limit=budget) == result, "Extension read-back differs; preserve for inspection.")
    recovery._recheck(pins)
    return ref


def load_checked(ref: dict, selection_ref: dict) -> dict:
    """Explicit selection ref provides the byte budget before reading the output."""
    ref, pins = _ref(ref), {}
    selection_ref = _ref(selection_ref)
    budget = _selection(selection_ref)["limits"]["max_metadata_bytes"]
    stored = native._read(ref, pins, limit=budget)
    _require(stored.get("schema_version") == VERSION, "Expected explicit v3 inventory extension.")
    _require(stored["selection"] == selection_ref, "Extension selection differs from requested pinned configuration.")
    current = inspect(selection_ref)
    _require(current == stored, "Extension differs from independent reconstruction.")
    for dependency in current["dependencies"]:
        recovery._pin(pins, Path(dependency["path"]), dependency["sha256"])
    recovery._recheck(pins)
    _require(_implementation() == current["implementation"], "Extension implementation changed after reconstruction.")
    return current


def _hypothesis_ids(h):
    ids = set(h["supporting_evidence"] + h["counterevidence"])
    for link in h["statement_action_relationships"]:
        ids.update((link["statement_id"], link["action_id"]))
    for row in h.get("recovered_comparisons", []):
        ids.update(row["evidence_ids"])
    return ids


def checked_index_closure(index: dict, ref: dict, support_ids: list[str]) -> dict:
    """Pure one-incident-pass closure over an already checked extension."""
    _require(index.get("schema_version") == VERSION and isinstance(support_ids, list) and support_ids
             and all(isinstance(r, str) for r in support_ids) and len(support_ids) == len(set(support_ids)),
             "Use unique evidence IDs from a checked extension.")
    evidence = index["evidence"]
    seeds = base._basis_closure(evidence, support_ids)
    required, hypotheses, obligations, claims = set(seeds), {}, [], set()
    for key, h in sorted(index["hypotheses"].items()):
        ids = base._basis_closure(evidence, _hypothesis_ids(h))
        if ids & seeds:
            hypotheses[key] = copy.deepcopy(h)
            required.update(ids)
            claims.add(h["artifact"]["sha256"] + ":hypothesis:" + h["hypothesis_id"])
    for collection, kind in (("native_comparisons", "native_comparison"),
                              ("native_pending_comparisons", "pending_comparison"), ("native_context", "prior_context")):
        for key, row in sorted(index[collection].items()):
            if collection == "native_comparisons":
                ids = [e["evidence_id"] for e in row["comparison"]["endpoints"]]
            elif collection == "native_pending_comparisons":
                ids = row["task"]["evidence_ids"]
            else:
                ids = list(set(row["context"]["required_evidence_ids"]) | _hypothesis_ids(row["prior_hypothesis"]))
            ids = base._basis_closure(evidence, ids)
            linked = (collection == "native_comparisons" and any(
                row["artifact"]["sha256"] + ":" + h in hypotheses for h in row["comparison"]["hypothesis_ids"]))
            # Context for a matched claim stays accessible even if its required IDs do not overlap seeds.
            context_claim = row.get("context", {}).get("claim_id", "")
            linked = linked or row["artifact"]["sha256"] + ":" + context_claim in claims
            if ids & seeds or linked:
                required.update(ids)
                obligations.append({"key": key, "kind": kind, **copy.deepcopy(row)})
                if collection == "native_comparisons":
                    claims.add(row["artifact"]["sha256"] + ":comparison:" + row["comparison"]["comparison_id"])
    required = base._basis_closure(evidence, required)
    return {"schema_version": "profile_extension_closure.v1", "inventory": _ref(ref),
            "supporting_evidence": list(support_ids), "hypotheses": hypotheses, "obligations": obligations,
            "required_evidence_ids": sorted(required),
            "required_evidence_keys": [evidence[r]["evidence_key"] for r in sorted(required)],
            "episodes": {eid: copy.deepcopy(index["episodes"][eid]) for eid in sorted({evidence[r]["episode_id"] for r in required})},
            "inventory_complete": index["inventory_complete"], "policy": copy.deepcopy(POLICY)}


def known_closure(ref: dict, support_ids: list[str], selection_ref: dict) -> dict:
    return checked_index_closure(load_checked(ref, selection_ref), ref, support_ids)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "freeze", "validate", "closure"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--selection-sha256")
    parser.add_argument("--support", action="append", default=[])
    args = parser.parse_args(argv)
    if bool(args.output) != (args.command == "freeze") or bool(args.support) != (args.command == "closure"):
        parser.error("Only freeze requires --output; only closure requires --support IDs")
    if (bool(args.selection) != (args.command in {"validate", "closure"})
            or bool(args.selection_sha256) != bool(args.selection)):
        parser.error("validate/closure require --selection and --selection-sha256 for the explicit read budget")
    try:
        ref = {"path": str(args.input), "sha256": args.sha256}
        selection_ref = {"path": str(args.selection), "sha256": args.selection_sha256}
        if args.command == "freeze":
            result = {"artifact": freeze(ref, args.output)}
        elif args.command == "closure":
            result = known_closure(ref, args.support, selection_ref)
        else:
            result = inspect(ref) if args.command == "inspect" else load_checked(ref, selection_ref)
        print(json.dumps({"status": "created" if args.command == "freeze" else "checked",
            "model_calls": 0, "evidence_count": len(result.get("evidence", {})),
            "hypothesis_count": len(result.get("hypotheses", {})),
            "obligation_count": len(result.get("obligations", [])), "artifact": result.get("artifact"),
            "policy": POLICY}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "model_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
