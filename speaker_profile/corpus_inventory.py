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


def _lineage_manifest(descriptor, artifact, evidence, pins, reserved, protected, active=None):
    """Follow authoritative configs/proposals before any replay can load sources."""
    active = set() if active is None else active
    path = private_path(descriptor["artifact"]["path"])
    if path in active:
        raise ValueError("Cyclic Profile artifact lineage.")
    active.add(path)
    kind = descriptor["kind"]
    formats = ({"completed": schemas.VERSION, "recovered": recover_evidence.VERSION,
                "editorial": evidence_revision.VERSION} if evidence else
               {"completed": schemas.VERSION, "recovered": recover_synthesis.VERSION,
                "editorial_completed": "profile_reviewed_revision.v1", "editorial_recovered": review_recovered.VERSION})
    stage = "evidence" if evidence else "synthesis"
    if (not isinstance(artifact, dict) or artifact.get("schema_version") != formats.get(kind)
            or artifact.get("stage", None if evidence else "synthesis") != stage):
        raise ValueError("Artifact kind must name its actual Profile format and stage.")
    claimed = None
    if kind == "completed":
        manifest_ref = descriptor["manifest"]
    elif evidence and kind == "recovered":
        manifest_ref = recover_evidence._config(artifact["recovery_config"])["manifest"]
    elif evidence and kind == "editorial":
        config = shared._read(artifact["config"], pins)
        shared._validate(config, evidence_revision.CONFIG_SCHEMA)
        parent = config["parent"]
        manifest_ref = _lineage_manifest(parent, shared._read(parent["artifact"], pins), True,
                                        pins, reserved, protected, active)
        claimed = artifact["parent_manifest"]
        if artifact["parent"] != parent:
            raise ValueError("Editorial evidence parent differs from its pinned configuration.")
    elif not evidence and kind == "recovered":
        proposal = shared._read(artifact["proposal"], pins)
        shared._validate(proposal, recover_synthesis.PROPOSAL_SCHEMA)
        manifest_ref, claimed = proposal["manifest"], artifact["parent"]["manifest"]
    elif not evidence and kind == "editorial_completed":
        proposal = shared._read(artifact["proposal"], pins)
        shared._validate(proposal, review.PROPOSAL_SCHEMA)
        cfg = pipeline._config(shared._read(proposal["parent_config"], pins))
        root = child_path(Path(cfg["output_root"]), cfg["run_id"])
        manifest_path = root / "run_manifest.json"
        manifest_ref = {"path": str(manifest_path), "sha256": sha256_file(manifest_path)}
        if (_manifest(manifest_ref, pins, reserved, protected) != cfg
                or private_path(proposal["parent_artifact"]["path"]) != root / "synthesis.json"):
            raise ValueError("Editorial synthesis parent configuration differs from its original run.")
        shared._read(proposal["parent_artifact"], pins)
        claimed = artifact["parent_manifest"]
    elif not evidence and kind == "editorial_recovered":
        proposal = shared._read(artifact["proposal"], pins)
        shared._validate(proposal, review_recovered.PROPOSAL_SCHEMA)
        parent = {"kind": "recovered", "artifact": proposal["parent_artifact"]}
        manifest_ref = _lineage_manifest(parent, shared._read(parent["artifact"], pins), False,
                                        pins, reserved, protected, active)
        claimed = artifact["parent"]["manifest"]
    else:
        raise ValueError("Unsupported Profile artifact lineage.")
    manifest_ref = shared._ref(manifest_ref)
    _manifest(manifest_ref, pins, reserved, protected)
    if claimed is not None and shared._ref(claimed) != manifest_ref:
        raise ValueError("Artifact manifest header differs from its authoritative parent lineage.")
    active.remove(path)
    return manifest_ref


def _load_evidence(descriptor, pins, reserved, protected, check_path):
    descriptor = copy.deepcopy(descriptor)
    ref = descriptor["artifact"] = shared._ref(descriptor["artifact"])
    artifact = shared._read(ref, pins)
    manifest_ref = _lineage_manifest(descriptor, artifact, True, pins, reserved, protected)
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
    manifest_path = root / "run_manifest.json"
    manifest_ref = {"path": str(manifest_path), "sha256": sha256_file(manifest_path)}
    manifest = shared._read(manifest_ref, pins)
    historical = manifest.get("implementation")
    if (not isinstance(historical, dict) or not historical.get("python")
            or not isinstance(historical.get("packages"), dict)
            or not isinstance(historical.get("code"), dict) or not historical["code"]):
        raise ValueError("Missing completed synthesis implementation lineage.")
    for checksum in historical["code"].values():
        pipeline._checksum(checksum)
    # Pin the closed call chain before replay so changes during validation cannot be adopted.
    for identity, stage in [(e["episode_id"], "evidence") for e in cfg["episodes"]] + [("cross_episode", "synthesis")]:
        for attempt in range(1, cfg["retry_count"] + 2):
            for suffix in ("request", "result"):
                path = root / "calls" / f"{identity}_{stage}_{attempt}.{suffix}.json"
                if path.exists():
                    shared._read({"path": str(path), "sha256": sha256_file(path)}, pins)
    sources = evidence_revision._sources(cfg, pins)
    reused = pipeline._reused_evidence(cfg, sources, pins)
    reused.update(pipeline._reused_recovered_evidence(cfg, sources, pins))
    previous = pipeline._reused_failed_synthesis(cfg, sources, reused, pins)
    refs, upstream = {}, []
    for entry in cfg["episodes"]:
        eid = entry["episode_id"]
        ref = (cfg.get("reuse_recovered_evidence", {}).get(eid)
               or cfg.get("reuse_evidence", {}).get("artifacts", {}).get(eid))
        if ref is None:
            path = child_path(root, eid, "evidence.json")
            ref = {"path": str(path), "sha256": sha256_file(path)}
            checked, _, _ = evidence_revision._parent({"parent": {
                "kind": "completed", "manifest": manifest_ref, "artifact": ref}}, pins,
                root.parent / ("inventory_check_" + pipeline.digest(cfg)[:24]))
        else:
            checked = reused[eid]
        refs[eid] = shared._ref(ref)
        if shared._read(refs[eid], pins) != checked:
            raise ValueError("Synthesis evidence changed during historical replay.")
        upstream.append(checked)
    artifact = shared._read(descriptor["artifact"], pins)
    source_meta = {"episodes": [pipeline._meta(sources[e["episode_id"]]) for e in cfg["episodes"]]}
    if (artifact.get("schema_version") != schemas.VERSION or artifact.get("stage") != "synthesis"
            or artifact.get("source") != source_meta or artifact.get("policy") != pipeline.POLICY
            or artifact.get("grounding") != pipeline._derive_synthesis(artifact["content"], upstream)
            or any(artifact.get("reused_" + key) != cfg.get("reuse_" + key)
                   for key in ("evidence", "recovered_evidence", "failed_synthesis"))):
        raise ValueError("Completed synthesis source, grounding or evidence lineage differs.")
    # Historical fingerprints bind the original manifest, schema and prompt, not today's code.
    for number in range(1, cfg["retry_count"] + 2):
        request_path = root / "calls" / f"cross_episode_synthesis_{number}.request.json"
        result_path = request_path.with_name(request_path.name.replace(".request.json", ".result.json"))
        request_ref = {"path": str(request_path), "sha256": sha256_file(request_path)}
        result_ref = {"path": str(result_path), "sha256": sha256_file(result_path)}
        request, result = shared._read(request_ref, pins), shared._read(result_ref, pins)
        if request.get("schema") != schemas.schema("synthesis"):
            raise ValueError("Historical synthesis schema requires explicit compatibility review.")
        fingerprint = pipeline.digest({"run": manifest, "stage": "synthesis", "source": source_meta,
            "upstream": [pipeline.digest(a) for a in upstream],
            "schema": request["schema"], "prompt": request.get("prompt")})
        context = pipeline._repair_context(pipeline._context(
            "synthesis", None, upstream, cfg.get("context_format"), cfg.get("commentary_scope")), previous)
        if (request.get("schema_version") != "profile_call.v1" or result.get("schema_version") != "profile_call.v1"
                or request.get("stage") != "synthesis" or request.get("episode_id") != "cross_episode"
                or type(request.get("attempt")) is not int or request["attempt"] != number
                or not isinstance(request.get("prompt"), str) or not request["prompt"]
                or request.get("context") != context or request.get("fingerprint") != fingerprint
                or result.get("fingerprint") != fingerprint or artifact.get("fingerprint") != fingerprint
                or result.get("request_sha256") != request_ref["sha256"]):
            raise ValueError("Completed synthesis original request/repair provenance differs.")
        if request_ref["sha256"] == artifact.get("request_sha256"):
            if (result_ref["sha256"] != artifact.get("result_sha256") or result.get("status") != "completed"
                    or result.get("error") is not None or result.get("response") != artifact["content"]
                    or result.get("usage") != artifact.get("model_metadata")):
                raise ValueError("Completed synthesis original result/usage binding differs.")
            break
        if result.get("status") != "failed" or not isinstance(result.get("error"), dict):
            raise ValueError("Completed synthesis repair chain lacks an original failed result.")
        previous = result
    else:
        raise ValueError("Completed synthesis lacks its original successful request/result.")
    return refs


def _load_synthesis(descriptor, pins, reserved, protected):
    ref = shared._ref(descriptor["artifact"])
    artifact = shared._read(ref, pins)
    kind = descriptor["kind"]
    manifest_ref = _lineage_manifest(descriptor, artifact, False, pins, reserved, protected)
    cfg = _manifest(manifest_ref, pins, reserved, protected)
    if kind == "completed":
        evidence_refs = _completed_synthesis(descriptor, cfg, pins)
        checked = shared._read(ref, pins)
    elif kind == "editorial_completed":
        proposal = shared._read(artifact["proposal"], pins)
        shared._read(proposal["parent_config"], pins)
        shared._read(proposal["parent_artifact"], pins)
        evidence_refs = _completed_synthesis({"artifact": proposal["parent_artifact"]}, cfg, pins)
        checked = review._historical_completed(ref, evidence_refs, pins)
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
    for collection, stage in (("episodes", "evidence"), ("syntheses", "synthesis")):
        for entry in selection[collection]:
            state = entry[stage]
            if state["status"] == "available":
                descriptor = state["input"]
                _lineage_manifest(descriptor, shared._read(descriptor["artifact"], pins),
                                  stage == "evidence", pins, reserved, protected)
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
    return checked_index_closure(load_checked(ref), ref, support_ids)


def checked_index_closure(inventory: dict, ref: dict, support_ids: list[str]) -> dict:
    """Pure closure over an already load_checked index; caller retains dependency pins."""
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
