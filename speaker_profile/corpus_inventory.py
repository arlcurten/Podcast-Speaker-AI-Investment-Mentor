"""Explicit, zero-call Profile inventory and known relationship closure.

inspect(selection_ref) is read-only. freeze(selection_ref, output) creates one
immutable private index; load_checked(ref) independently reconstructs it.
known_closure(ref, support_ids) retains recorded counter/basis/comparison links,
not semantic discovery or acceptance. No source items are copied or filtered.

profile_corpus_selection.v1 requires corpus_id, expected_episode_ids,
reserved_episode_ids, episodes, expected_synthesis_ids and syntheses. An episode
has episode_id and evidence; a synthesis has synthesis_id and synthesis.
Each state is {status: available, input: descriptor}, or
{status: missing|failed|held, reason: text, record: {path, sha256}|null}.
Evidence descriptors are {kind: completed, manifest: REF, artifact: REF}, or
{kind: recovered|editorial, artifact: REF}. Synthesis descriptors additionally
support editorial_completed and editorial_recovered; completed needs manifest.
Every chosen synthesis must reference the exact selected evidence versions.
One evidence artifact is chosen per episode; all its items remain in the index.
inventory_complete covers only the explicit selection, never the entire corpus.
Synthesis identities are artifact SHA plus local hypothesis ID, not local IDs alone.

CLI: inspect|freeze|validate|closure --input PATH --sha256 SHA;
freeze additionally needs --output NEW_PATH, closure repeatable --support ID.
CLI stdout contains counts only. Detailed index/closure is available through the
Python API. All paths are private. Historical artifacts retain their own lineage;
the inventory pins current validators and fails on changed replay results.
"""
from __future__ import annotations

import argparse
import copy
from pathlib import Path

from knowledge import corpus_inventory as shared
from knowledge.schemas import TEXT, array, enum, obj
from transcription.common import atomic_write_json, child_path, private_path, safe_id, sha256_file
from . import evidence_revision, pipeline, recover_evidence, recover_synthesis, review, review_recovered, schemas

VERSION = "profile_corpus_inventory.v1"
SELECTION_VERSION = "profile_corpus_selection.v1"
REF = shared.REF
COMPLETED = obj(kind=enum("completed"), manifest=REF, artifact=REF)
EVIDENCE = {"oneOf": [COMPLETED, obj(kind=enum("recovered", "editorial"), artifact=REF)]}
SYNTHESIS = {"oneOf": [COMPLETED, obj(kind=enum("recovered", "editorial_completed", "editorial_recovered"), artifact=REF)]}


def _state(descriptor):
    return {"oneOf": [obj(status=enum("available"), input=descriptor),
                       obj(status=enum("missing", "failed", "held"), reason=TEXT, record=shared.OPTIONAL_REF)]}


SELECTION = obj(schema_version=enum(SELECTION_VERSION), corpus_id=TEXT,
    expected_episode_ids={**array(TEXT, 1), "uniqueItems": True},
    reserved_episode_ids={**array(TEXT), "uniqueItems": True},
    episodes=array(obj(episode_id=TEXT, evidence=_state(EVIDENCE)), 1),
    expected_synthesis_ids={**array(TEXT), "uniqueItems": True},
    syntheses=array(obj(synthesis_id=TEXT, synthesis=_state(SYNTHESIS))))
POLICY = {**shared.POLICY, "independent_event_count": None,
          "inventory_complete_scope": "explicit selection only, not the entire corpus",
          "known_relationship_scope": "explicitly supplied synthesis parents only; no global semantic discovery"}


def _implementation():
    modules = {"inventory_helpers": shared, "evidence_revision": evidence_revision,
               "recover_evidence": recover_evidence, "recover_synthesis": recover_synthesis,
               "review": review, "review_recovered": review_recovered}
    return {"profile": pipeline._implementation(),
            "code": {**{name: sha256_file(Path(module.__file__)) for name, module in modules.items()},
                     "profile_inventory": sha256_file(Path(__file__))},
            "prompts_sha256": pipeline.digest(schemas.PROMPTS),
            "terminology_sha256": sha256_file(pipeline.common.TERMINOLOGY)}


def _manifest(ref, pins, reserved, protected, active=None):
    active = set() if active is None else active
    path = private_path(ref["path"])
    if path in active:
        raise ValueError("Cyclic Profile manifest lineage.")
    active.add(path)
    value = shared._read(ref, pins)
    cfg = pipeline._config(value["config"])
    if (value.get("schema_version") != "profile_run.v1" or value.get("policy") != pipeline.POLICY
            or value["config"] != cfg or value.get("config_digest") != pipeline.digest(cfg)):
        raise ValueError("Original Profile manifest/config binding differs.")
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    if private_path(ref["path"]) != root / "run_manifest.json":
        raise ValueError("Manifest does not belong to its original run.")
    if set(reserved) & {e["episode_id"] for e in cfg["episodes"]}:
        raise ValueError("Inventory reserved sources must not be loaded, including parent dependencies.")
    protected.add(root)
    parents = [cfg[key]["manifest"] for key in ("reuse_evidence", "reuse_failed_synthesis") if key in cfg]
    for original in cfg.get("reuse_recovered_evidence", {}).values():
        parent = shared._read(original, pins)
        parents.append(parent["recovery_config"]["manifest"])
    for parent in parents:
        _manifest(parent, pins, reserved, protected, active)
    active.remove(path)
    return cfg


def _parent_manifest(descriptor, artifact, evidence):
    if descriptor["kind"] == "completed":
        return descriptor["manifest"]
    if evidence:
        return artifact["recovery_config"]["manifest"] if descriptor["kind"] == "recovered" else artifact["parent_manifest"]
    return artifact["parent_manifest"] if descriptor["kind"] == "editorial_completed" else artifact["parent"]["manifest"]


def _load_evidence(descriptor, pins, reserved, protected, check_path):
    descriptor = copy.deepcopy(descriptor)
    ref = descriptor["artifact"] = shared._ref(descriptor["artifact"])
    artifact = shared._read(ref, pins)
    expected = {"completed": schemas.VERSION, "recovered": recover_evidence.VERSION, "editorial": evidence_revision.VERSION}
    if artifact.get("schema_version") != expected[descriptor["kind"]] or artifact.get("stage") != "evidence":
        raise ValueError("Evidence kind must name its actual artifact format, never relabel a derivative.")
    manifest_ref = shared._ref(_parent_manifest(descriptor, artifact, True))
    _manifest(manifest_ref, pins, reserved, protected)
    if descriptor["kind"] == "editorial":
        checked = evidence_revision.load_checked(ref, pins)
    else:
        descriptor["manifest"] = manifest_ref
        checked, _, checked_manifest = evidence_revision._parent({"parent": descriptor}, pins, check_path)
        if shared._ref(checked_manifest) != manifest_ref:
            raise ValueError("Evidence parent manifest changed.")
    if checked != artifact or checked["policy"] != pipeline.POLICY:
        raise ValueError("Evidence changed during independent replay.")
    protected.add(Path(ref["path"]).parent)
    return checked, manifest_ref


def _completed_synthesis(descriptor, cfg, pins):
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    if private_path(descriptor["artifact"]["path"]) != root / "synthesis.json":
        raise ValueError("Completed synthesis is outside its original run.")
    # Pin the closed call chain before replay so changes during validation cannot be adopted.
    for identity, stage in [(e["episode_id"], "evidence") for e in cfg["episodes"]] + [("cross_episode", "synthesis")]:
        for attempt in range(1, cfg["retry_count"] + 2):
            for suffix in ("request", "result"):
                path = root / "calls" / f"{identity}_{stage}_{attempt}.{suffix}.json"
                if path.exists():
                    shared._read({"path": str(path), "sha256": sha256_file(path)}, pins)
    # Existing validation is read-only and rejects missing stages; no backend is constructed.
    pipeline.run(cfg, through="synthesis", validate_only=True)
    sources = evidence_revision._sources(cfg, pins)
    reused = pipeline._reused_evidence(cfg, sources, pins)
    reused.update(pipeline._reused_recovered_evidence(cfg, sources, pins))
    pipeline._reused_failed_synthesis(cfg, sources, reused, pins)
    refs = {}
    for entry in cfg["episodes"]:
        eid = entry["episode_id"]
        ref = (cfg.get("reuse_recovered_evidence", {}).get(eid)
               or cfg.get("reuse_evidence", {}).get("artifacts", {}).get(eid))
        if ref is None:
            path = child_path(root, eid, "evidence.json")
            ref = {"path": str(path), "sha256": sha256_file(path)}
        refs[eid] = shared._ref(ref)
        shared._read(refs[eid], pins)
    return refs


def _load_synthesis(descriptor, pins, reserved, protected):
    ref = shared._ref(descriptor["artifact"])
    artifact = shared._read(ref, pins)
    kind = descriptor["kind"]
    expected = {"completed": schemas.VERSION, "recovered": recover_synthesis.VERSION,
                "editorial_completed": "profile_reviewed_revision.v1", "editorial_recovered": review_recovered.VERSION}
    if artifact.get("schema_version") != expected[kind] or artifact.get("stage", "synthesis") != "synthesis":
        raise ValueError("Synthesis kind must name its actual artifact format.")
    manifest_ref = shared._ref(_parent_manifest(descriptor, artifact, False))
    cfg = _manifest(manifest_ref, pins, reserved, protected)
    if kind == "completed":
        evidence_refs = _completed_synthesis(descriptor, cfg, pins)
        checked = shared._read(ref, pins)
    elif kind == "editorial_completed":
        proposal = shared._read(artifact["proposal"], pins)
        checked = review.validate(artifact["proposal"]["path"], ref["path"])
        shared._read(proposal["parent_config"], pins)
        shared._read(proposal["parent_artifact"], pins)
        evidence_refs = _completed_synthesis({"artifact": proposal["parent_artifact"]}, cfg, pins)
        if evidence_refs != checked["evidence"]:
            raise ValueError("Editorial synthesis evidence lineage differs.")
    else:
        loader = recover_synthesis.load_checked if kind == "recovered" else review_recovered.load_checked
        checked = loader(ref, pins)
        evidence_refs = checked["evidence"]
    if checked != artifact or checked["policy"] != pipeline.POLICY:
        raise ValueError("Synthesis changed during replay or lost quarantine policy.")
    protected.add(Path(ref["path"]).parent)
    return checked, {eid: shared._ref(r) for eid, r in evidence_refs.items()}, manifest_ref


def inspect(selection_ref: dict) -> dict:
    pins, protected = {}, set()
    implementation = _implementation()
    selection_ref = shared._ref(selection_ref)
    selection = shared._read(selection_ref, pins)
    shared._validate(selection, SELECTION)
    safe_id(selection["corpus_id"])
    expected, reserved = selection["expected_episode_ids"], selection["reserved_episode_ids"]
    if set(expected) & set(reserved):
        raise ValueError("Expected and reserved episodes must be disjoint.")
    for key, rows, field in (("expected_episode_ids", "episodes", "episode_id"),
                             ("expected_synthesis_ids", "syntheses", "synthesis_id")):
        actual = [row[field] for row in selection[rows]]
        if len(actual) != len(set(actual)) or set(actual) != set(selection[key]):
            raise ValueError("Every expected input needs exactly one explicit state.")
        for identifier in selection[key]:
            safe_id(identifier)
    for eid in reserved:
        safe_id(eid)
    episodes, evidence, syntheses, hypotheses, gaps = {}, {}, {}, {}, []
    check_path = Path(selection_ref["path"]).parent / ("inventory_check_" + selection_ref["sha256"][:24])

    def gap(owner, state, stage):
        if not state["reason"].strip():
            raise ValueError("Inventory gaps require a substantive reason.")
        state = copy.deepcopy(state)
        if state["record"] is not None:
            state["record"] = shared._ref(state["record"])
            shared._pin_file(state["record"], pins)
        gaps.append({"input_id": owner, "stage": stage, **state})
        return state

    for entry in selection["episodes"]:
        eid, state = entry["episode_id"], entry["evidence"]
        if state["status"] != "available":
            episodes[eid] = {"evidence": gap(eid, state, "evidence"), "evidence_ids": []}
            continue
        descriptor = state["input"]
        artifact, manifest_ref = _load_evidence(descriptor, pins, reserved, protected, check_path)
        if artifact["source"]["episode_id"] != eid:
            raise ValueError("Evidence episode differs from inventory selection.")
        ref = shared._ref(descriptor["artifact"])
        ids = []
        for index, item in enumerate(artifact["content"]["items"]):
            rid = item["evidence_id"]
            if rid in evidence:
                raise ValueError("Duplicate evidence identity across selected inputs.")
            ids.append(rid)
            evidence[rid] = {"evidence_key": ref["sha256"] + ":" + rid, "episode_id": eid,
                "artifact": ref, "artifact_kind": descriptor["kind"], "schema_version": artifact["schema_version"],
                "content_pointer": f"/content/items/{index}", "grounding_pointer": f"/grounding/items/{rid}",
                **{key: copy.deepcopy(item[key]) for key in ("evidence_kind", "speaker_attribution", "categories",
                                                            "domains", "merged_ids", "basis_evidence_ids")}}
        episodes[eid] = {"evidence": {"status": "available", "input": copy.deepcopy(descriptor)},
            "evidence_ids": ids, "original_manifest": manifest_ref,
            "schema_version": artifact["schema_version"], "authority": artifact.get("authority", "model_extraction_candidate"),
            "source_pointer": "/source", "grounding_pointer": "/grounding",
            "unselected_regions_pointer": "/content/unselected_regions", "policy": copy.deepcopy(artifact["policy"]),
            "source_hashes": {key: artifact["source"][key] for key in
                              ("raw_sha256", "normalized_sha256", "review_sha256", "annotations_sha256") if key in artifact["source"]}}
    # All inference bases must resolve within the exact selected evidence versions.
    _basis_closure(evidence, list(evidence))
    seen_artifacts = set()
    for entry in selection["syntheses"]:
        sid, state = entry["synthesis_id"], entry["synthesis"]
        if state["status"] != "available":
            syntheses[sid] = {"synthesis": gap(sid, state, "synthesis"), "hypothesis_keys": []}
            continue
        descriptor = state["input"]
        ref = shared._ref(descriptor["artifact"])
        if ref["sha256"] in seen_artifacts:
            raise ValueError("Do not inventory the same synthesis artifact twice.")
        seen_artifacts.add(ref["sha256"])
        artifact, refs, manifest_ref = _load_synthesis(descriptor, pins, reserved, protected)
        for eid, original in refs.items():
            chosen = episodes.get(eid, {}).get("evidence", {})
            if chosen.get("status") != "available" or shared._ref(chosen["input"]["artifact"]) != original:
                raise ValueError("Synthesis must reference exact selected evidence versions, not merely matching IDs.")
        accounted = {rid for eid in refs for rid in episodes[eid]["evidence_ids"]}
        if set(artifact["grounding"]["retained_evidence_ids"]) != accounted:
            raise ValueError("Synthesis item accounting differs from the selected evidence universe.")
        keys = []
        for index, h in enumerate(artifact["content"]["hypotheses"]):
            hid = h["hypothesis_id"]
            key = ref["sha256"] + ":" + hid
            keys.append(key)
            hypotheses[key] = {"hypothesis_id": hid, "artifact": ref, "artifact_kind": descriptor["kind"],
                "content_pointer": f"/content/hypotheses/{index}",
                **{name: copy.deepcopy(h[name]) for name in ("hypothesis_type", "categories", "domains",
                                                           "supporting_evidence", "counterevidence",
                                                           "statement_action_relationships", "limitations")},
                "grounding": copy.deepcopy(artifact["grounding"]["hypotheses"][hid]),
                "recovered_comparisons": [{"pointer": f"/comparison_ledger/{i}", **copy.deepcopy(row)}
                                          for i, row in enumerate(artifact.get("comparison_ledger", [])) if row["hypothesis_id"] == hid]}
        syntheses[sid] = {"synthesis": copy.deepcopy(state), "original_manifest": manifest_ref,
            "hypothesis_keys": keys, "evidence": refs, "schema_version": artifact["schema_version"],
            "authority": artifact.get("authority", "model_synthesis_candidate"),
            "evidence_accounting_pointer": "/content/evidence_accounting", "policy": copy.deepcopy(artifact["policy"])}
    recover_evidence._recheck(pins)
    if _implementation() != implementation:
        raise ValueError("Inventory implementation changed during replay.")
    return {"schema_version": VERSION, "corpus_id": selection["corpus_id"], "selection": selection_ref,
        "expected_episode_ids": expected, "reserved_episode_ids": reserved,
        "expected_synthesis_ids": selection["expected_synthesis_ids"], "episodes": episodes,
        "evidence": evidence, "syntheses": syntheses, "hypotheses": hypotheses,
        "inventory_complete": not gaps, "gaps": gaps,
        "protected_output_roots": sorted(str(path) for path in protected),
        "dependencies": [{"path": str(path), "sha256": sha} for path, sha in sorted(pins.items())],
        "implementation": implementation, "policy": copy.deepcopy(POLICY)}


def _basis_closure(evidence, ids):
    seen, active = set(), set()

    def visit(rid):
        if rid not in evidence:
            raise ValueError("Unresolved Profile evidence or inference basis.")
        if rid in active:
            raise ValueError("Cyclic Profile inference basis.")
        if rid in seen:
            return
        active.add(rid)
        for basis in evidence[rid]["basis_evidence_ids"]:
            visit(basis)
        active.remove(rid)
        seen.add(rid)

    for rid in ids:
        visit(rid)
    return seen


def freeze(selection_ref: dict, output: str | Path) -> dict:
    path = private_path(output)
    if path.exists():
        raise FileExistsError("Inventory output exists; choose a new version.")
    result = inspect(selection_ref)
    if any(path.is_relative_to(Path(root)) for root in result["protected_output_roots"]):
        raise ValueError("Inventory must be outside original runs and selected artifact directories.")
    pins = {Path(ref["path"]): ref["sha256"] for ref in result["dependencies"]}
    if private_path(output) != path or path in pins:
        raise ValueError("Inventory output changed or overlaps an input.")
    recover_evidence._recheck(pins)
    if _implementation() != result["implementation"]:
        raise ValueError("Inventory implementation changed before writing.")
    atomic_write_json(path, result, overwrite=False)
    ref = {"path": str(path), "sha256": sha256_file(path)}
    if load_checked(ref) != result:
        raise ValueError("Inventory read-back differs; preserve the derivative for inspection.")
    return ref


def load_checked(ref: dict) -> dict:
    pins = {}
    stored = shared._read(ref, pins)
    if not isinstance(stored, dict) or stored.get("schema_version") != VERSION:
        raise ValueError("Expected an explicit Profile corpus inventory.")
    current = inspect(stored["selection"])
    if pipeline.digest(stored) != pipeline.digest(current):
        raise ValueError("Profile inventory differs from independent reconstruction.")
    recover_evidence._recheck(pins)
    return current


def known_closure(ref: dict, support_ids: list[str]) -> dict:
    inventory = load_checked(ref)
    evidence = inventory["evidence"]
    if (not isinstance(support_ids, list) or not support_ids or any(not isinstance(x, str) for x in support_ids)
            or len(support_ids) != len(set(support_ids)) or not set(support_ids) <= evidence.keys()):
        raise ValueError("Use unique supporting evidence IDs from this inventory.")
    seeds = _basis_closure(evidence, support_ids)
    required, obligations, matched = set(seeds), [], []
    for key, h in sorted(inventory["hypotheses"].items()):
        overlap = sorted(seeds & _basis_closure(evidence, h["supporting_evidence"]))
        if not overlap:
            continue
        matched.append(key)
        required.update(h["supporting_evidence"])
        parent = {"hypothesis_key": key, "parent_artifact": h["artifact"], "support_overlap": overlap,
                  "status": "requires_scope_review"}
        for index, rid in enumerate(h["counterevidence"]):
            required.add(rid)
            obligations.append({**parent, "kind": "counterevidence", "evidence_ids": [rid],
                                "link_pointer": h["content_pointer"] + f"/counterevidence/{index}"})
        for index, link in enumerate(h["statement_action_relationships"]):
            ids = [link["statement_id"], link["action_id"]]
            required.update(ids)
            obligations.append({**parent, "kind": "statement_action_comparison", "evidence_ids": ids,
                "link_pointer": h["content_pointer"] + f"/statement_action_relationships/{index}", "comparison": copy.deepcopy(link)})
        for row in h["recovered_comparisons"]:
            required.update(row["evidence_ids"])
            obligations.append({**parent, "kind": "recovered_comparison_caveat", "evidence_ids": row["evidence_ids"],
                                "link_pointer": row["pointer"], "comparison": {k: copy.deepcopy(v) for k, v in row.items() if k != "pointer"}})
    required = _basis_closure(evidence, sorted(required))
    return {"schema_version": "profile_known_relationship_closure.v1", "inventory": shared._ref(ref),
        "supporting_evidence": list(support_ids), "matched_hypothesis_keys": matched, "obligations": obligations,
        "required_evidence_ids": sorted(required), "required_evidence_keys": [evidence[rid]["evidence_key"] for rid in sorted(required)],
        "inventory_complete": inventory["inventory_complete"], "policy": copy.deepcopy(POLICY),
        "scope": "Explicit support/basis-overlapping parents, all their support/counter and typed/recovered comparison sides; "
                 "not semantic discovery, independence, rejection/retention decisions or serving approval."}


def main(argv=None):
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "freeze", "validate", "closure"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--support", action="append", default=[])
    args = parser.parse_args(argv)
    if bool(args.output) != (args.command == "freeze") or bool(args.support) != (args.command == "closure"):
        parser.error("Only freeze requires --output; only closure requires --support IDs")
    ref = {"path": str(args.input), "sha256": args.sha256}
    try:
        if args.command == "freeze":
            ref = freeze(ref, args.output)
            result = load_checked(ref)
        elif args.command == "inspect":
            result = inspect(ref)
        elif args.command == "closure":
            result = known_closure(ref, args.support)
        else:
            result = load_checked(ref)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "model_calls": 0}))
        return 2
    print(json.dumps({"status": "created" if args.command == "freeze" else "checked", "model_calls": 0,
        "inventory_complete": result["inventory_complete"], "evidence_count": len(result.get("evidence", {})),
        "hypothesis_count": len(result.get("hypotheses", {})), "gap_count": len(result.get("gaps", [])),
        "obligation_count": len(result.get("obligations", [])), "policy": result["policy"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
