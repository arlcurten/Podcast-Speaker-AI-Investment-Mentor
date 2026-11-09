"""Zero-call inventory of explicit immutable L1-L3 inputs, never semantic approval.

inspect(selection_ref) is read-only; freeze(selection_ref, output) writes one
immutable private index; load_checked(freeze_ref) independently replays it.
known_closure(freeze_ref, support_ids) returns known L3 counter obligations across
the entire supplied inventory, including ineligible parents. Partial inventories
remain partial. No grouping, synthesis, scheduler or Profile adapter is provided.

CLI (all data paths must be absolute private paths):
  python -m knowledge.corpus_inventory inspect --input "$SELECTION" --sha256 "$SELECTION_SHA"
  python -m knowledge.corpus_inventory freeze --input "$SELECTION" --sha256 "$SELECTION_SHA" --output "$FREEZE"
  python -m knowledge.corpus_inventory validate --input "$FREEZE" --sha256 "$FREEZE_SHA"
  python -m knowledge.corpus_inventory closure --input "$FREEZE" --sha256 "$FREEZE_SHA" --support "$CASE_ID"
CLI stdout contains counts only; the Python API returns the detailed private index/closure.
The knowledge_corpus_selection.v1 object requires corpus_id, expected_episode_ids
and episodes. Every episode has episode_id, config ({path, sha256} or null), and
stages containing level1, level2 and level3. Each stage is either
{status: available, artifact: {path, sha256}} or
{status: missing|failed|held, reason: nonempty text, record: {path, sha256}|null}.
Nonavailable states are selection declarations, not discovered live-job status.
Available stages must form a complete prefix and require a pinned config.
Validation replays current code/prompts; it does not migrate historical freezes.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path

from jsonschema import Draft202012Validator

from transcription import annotations, common, prepare, quality, source_review
from transcription.common import atomic_write_json, private_path, safe_id, sha256_file
from . import candidates, case_release, coverage, pipeline, provenance, schemas
from .schemas import TEXT, array, enum, obj

VERSION = "knowledge_corpus_inventory.v1"
SELECTION_VERSION = "knowledge_corpus_selection.v1"
REF = obj(path=TEXT, sha256={"type": "string", "pattern": "^[a-f0-9]{64}$"})
OPTIONAL_REF = {"anyOf": [REF, {"type": "null"}]}
STAGE = {"oneOf": [obj(status=enum("available"), artifact=REF),
                    obj(status=enum("missing", "failed", "held"), reason=TEXT, record=OPTIONAL_REF)]}
SELECTION = obj(schema_version=enum(SELECTION_VERSION), corpus_id=TEXT,
    expected_episode_ids={**array(TEXT, 1), "uniqueItems": True},
    episodes=array(obj(episode_id=TEXT, config=OPTIONAL_REF,
                       stages=obj(level1=STAGE, level2=STAGE, level3=STAGE)), 1))
POLICY = {"scope": "inventory_and_known_links_only", "model_calls": 0,
          "semantic_coverage": "not_assessed", "source_acceptance": "not_granted",
          "publication_authorized": False, "eligible_for_serving": False}


def _validate(value, schema):
    if next(Draft202012Validator(schema).iter_errors(value), None) is not None:
        raise ValueError("Invalid corpus inventory input contract.")


def _ref(ref):
    _validate(ref, REF)
    return {"path": str(private_path(ref["path"])), "sha256": ref["sha256"]}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate inventory JSON key.")
        result[key] = value
    return result


def _read(ref, pins):
    ref = _ref(ref)
    path = Path(ref["path"])
    payload = path.read_bytes()
    if (hashlib.sha256(payload).hexdigest() != ref["sha256"]
            or path in pins and pins[path] != ref["sha256"]):
        raise ValueError("Pinned inventory input changed.")
    value = json.loads(payload, object_pairs_hook=_object)
    provenance.digest(value)  # Reject non-finite JSON values.
    pins[path] = ref["sha256"]
    return value


def _implementation():
    modules = {"pipeline": pipeline, "provenance": provenance, "schemas": schemas,
               "candidates": candidates, "coverage": coverage, "case_release": case_release,
               "common": common, "annotations": annotations, "prepare": prepare,
               "quality": quality, "source_review": source_review}
    return {"python": platform.python_version(),
            "packages": {name: version(name) for name in ("jsonschema", "PyYAML", "opencc-python-reimplemented")},
            "code": {**{name: sha256_file(Path(module.__file__)) for name, module in modules.items()},
                     "corpus_inventory": sha256_file(Path(__file__))},
            "prompts": {name: sha256_file(pipeline.PROMPTS / (name + ".md"))
                        for name in (*pipeline.STAGES[:3], "candidate")},
            "terminology_sha256": sha256_file(common.TERMINOLOGY)}


def _pin_file(ref, pins):
    ref = _ref(ref)
    path = Path(ref["path"])
    if sha256_file(path) != ref["sha256"] or path in pins and pins[path] != ref["sha256"]:
        raise ValueError("Pinned inventory dependency changed.")
    pins[path] = ref["sha256"]


def _source_dependencies(cfg, sources, pins):
    for entry in cfg["episodes"]:
        source = sources[entry["episode_id"]]
        for key in ("raw", "normalized", "review"):
            if entry.get(key):
                _pin_file({"path": entry[key], "sha256": source[key + "_sha256"]}, pins)
        if not entry.get("review"):
            continue
        path = private_path(entry["review"])
        review = _read({"path": str(path), "sha256": source["review_sha256"]}, pins)
        owners = [(path, review)]
        if "annotations_sha256" in source:
            path, checksum = annotations._reference(review["annotations"], path)
            notes = _read({"path": str(path), "sha256": checksum}, pins)
            owners.append((path, notes))
        for owner, document in owners:
            for ref in document.get("supporting_reviews", []):
                path, checksum = annotations._reference(ref, owner)
                # Review attachments may be prose rather than JSON documents.
                _pin_file({"path": str(path), "sha256": checksum}, pins)


def inspect(selection_ref: dict) -> dict:
    """Validate all declared available stages; never discover or generate missing ones."""
    pins, loaded_configs = {}, {}
    implementation = _implementation()
    selection_ref = _ref(selection_ref)
    selection = _read(selection_ref, pins)
    _validate(selection, SELECTION)
    safe_id(selection["corpus_id"])
    expected = selection["expected_episode_ids"]
    actual = [entry["episode_id"] for entry in selection["episodes"]]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError("Every expected episode needs exactly one explicit inventory entry.")
    for eid in expected:
        safe_id(eid)
    episodes, gaps, l2_artifacts, l3_artifacts = {}, [], [], []
    for entry in selection["episodes"]:
        eid = entry["episode_id"]
        stages = copy.deepcopy(entry["stages"])
        available = [stage for stage in pipeline.STAGES[:3] if stages[stage]["status"] == "available"]
        if available != list(pipeline.STAGES[:len(available)]):
            raise ValueError("Available inventory stages must form a validated L1-L3 prefix.")
        if available and entry["config"] is None:
            raise ValueError("Available stages require a pinned original configuration.")
        config_ref = _ref(entry["config"]) if entry["config"] is not None else None
        cfg, sources = None, None
        if config_ref is not None:
            key = (config_ref["path"], config_ref["sha256"])
            if key not in loaded_configs:
                original = case_release._snapshot(config_ref["path"], pins, config_ref["sha256"], yaml_config=True)
                cfg = pipeline.load_config(Path(config_ref["path"]))
                if cfg["model"] != original.get("model"):
                    raise ValueError("Inventory cannot substitute the original model through the environment.")
                loaded_configs[key] = (cfg, None)
            cfg, sources = loaded_configs[key]
            if eid not in {row["episode_id"] for row in cfg["episodes"]}:
                raise ValueError("Inventory episode does not belong to its pinned configuration.")
            if available and sources is None:
                sources = pipeline.sources(cfg)
                _source_dependencies(cfg, sources, pins)
                loaded_configs[key] = (cfg, sources)
        source = sources[eid] if available else None
        row = {"config": config_ref, "stages": stages,
               "source": pipeline._source_meta(source) if source is not None else None,
               "extraction_mode": cfg.get("extraction_mode", "reviewed") if cfg is not None else None,
               "level2_record_ids": [], "level3_principle_ids": []}
        upstream = []
        for stage in pipeline.STAGES[:3]:
            state = stages[stage]
            if state["status"] != "available":
                if state["record"] is not None:
                    state["record"] = _ref(state["record"])
                    _pin_file(state["record"], pins)
                gaps.append({"episode_id": eid, "stage": stage, **copy.deepcopy(state)})
                continue
            ref = state["artifact"] = _ref(state["artifact"])
            path, configured_hash = pipeline.stage_location(cfg, stage, eid)
            if Path(ref["path"]) != path or configured_hash not in (None, ref["sha256"]):
                raise ValueError("Inventory artifact differs from the pinned configuration's stage reference.")
            raw = _read(ref, pins)
            artifact, checksum = pipeline.checked_artifact(cfg, sources, stage, source, upstream, path, ref["sha256"])
            if artifact != raw or checksum != ref["sha256"]:
                raise ValueError("Inventory artifact changed during validation.")
            if stage == "level1":
                upstream = [artifact]
            elif stage == "level2":
                l2_artifacts.append(artifact)
                row["level2_record_ids"] = [item["record_id"] for item in artifact["content"]["cases"]]
                upstream = [artifact]
            else:
                l3_artifacts.append(artifact)
                row["level3_principle_ids"] = [item["principle_id"] for item in artifact["content"]["principles"]]
        episodes[eid] = row
    # Index every record; references point to the original content, never a second evidence copy.
    catalog = provenance.case_catalog(l2_artifacts)
    cases = {rid: {"episode_id": case["episode_id"],
                   "artifact": copy.deepcopy(episodes[case["episode_id"]]["stages"]["level2"]["artifact"]),
                   "content_pointer": f"/content/cases/{episodes[case['episode_id']]['level2_record_ids'].index(rid)}",
                   "merged_ids": copy.deepcopy(case["merged_ids"])} for rid, case in catalog.items()}
    principles = {}
    for artifact in l3_artifacts:
        eid = artifact["source"]["episode_id"]
        for index, principle in enumerate(artifact["content"]["principles"]):
            pid = principle["principle_id"]
            if pid in principles:
                raise ValueError("Duplicate principle identity across inventory inputs.")
            principles[pid] = {
                "episode_id": eid, "artifact": copy.deepcopy(episodes[eid]["stages"]["level3"]["artifact"]),
                "content_pointer": f"/content/principles/{index}",
                **{key: copy.deepcopy(principle[key]) for key in ("supporting_records", "counterexamples", "contradictions")},
                "qualification": copy.deepcopy(artifact["qualification"][pid])}
    for cfg, source_map in loaded_configs.values():
        if source_map is not None and pipeline.sources(cfg) != source_map:
            raise ValueError("Inventory sources changed during reconstruction.")
    case_release._check_pins(pins)
    if _implementation() != implementation:
        raise ValueError("Inventory implementation changed during reconstruction.")
    return {"schema_version": VERSION, "corpus_id": selection["corpus_id"], "selection": selection_ref,
            "expected_episode_ids": copy.deepcopy(expected), "episodes": episodes,
            "inventory_complete": not gaps, "gaps": gaps, "cases": cases, "principles": principles,
            "dependencies": [{"path": str(path), "sha256": checksum} for path, checksum in sorted(pins.items())],
            "implementation": implementation, "policy": copy.deepcopy(POLICY)}


def freeze(selection_ref: dict, output: str | Path) -> dict:
    """Freeze a complete or explicitly partial index; never overwrite any file."""
    path = private_path(output)
    if path.exists():
        raise FileExistsError("Inventory output exists; choose a new version.")
    result = inspect(selection_ref)
    protected = set()
    for eid, entry in result["episodes"].items():
        if entry["config"] is not None:
            cfg = pipeline.load_config(Path(entry["config"]["path"]))
            protected.add((private_path(cfg["output_root"]) / cfg["run_id"]).resolve())
        for state in entry["stages"].values():
            if state["status"] == "available":
                folder = Path(state["artifact"]["path"]).parent
                protected.add(folder.parent if folder.name == eid else folder)
    if any(path.is_relative_to(root) for root in protected):
        raise ValueError("Inventory output must be outside extraction runs and referenced artifact directories.")
    if private_path(output) != path or str(path) in {ref["path"] for ref in result["dependencies"]}:
        raise ValueError("Inventory output changed or overlaps an input.")
    case_release._check_pins({Path(ref["path"]): ref["sha256"] for ref in result["dependencies"]})
    if _implementation() != result["implementation"]:
        raise ValueError("Inventory implementation changed before freezing.")
    atomic_write_json(path, result, overwrite=False)
    ref = {"path": str(path), "sha256": sha256_file(path)}
    if load_checked(ref) != result:
        raise ValueError("Inventory read-back differs; preserve and inspect the derivative.")
    return ref


def load_checked(freeze_ref: dict) -> dict:
    """Replay the complete selection, source/annotation pins, stages and index."""
    pins = {}
    stored = _read(freeze_ref, pins)
    if not isinstance(stored, dict) or stored.get("schema_version") != VERSION:
        raise ValueError("Expected an explicit Knowledge corpus inventory.")
    replayed = inspect(stored["selection"])
    if provenance.digest(stored) != provenance.digest(replayed):
        raise ValueError("Frozen inventory differs from independent reconstruction.")
    case_release._check_pins(pins)
    return replayed


def known_closure(freeze_ref: dict, support_ids: list[str]) -> dict:
    """Return required known links, not inferred relevance or a counterevidence verdict."""
    catalog = load_checked(freeze_ref)
    if (not isinstance(support_ids, list) or not support_ids
            or any(not isinstance(rid, str) for rid in support_ids)
            or len(support_ids) != len(set(support_ids)) or not set(support_ids) <= catalog["cases"].keys()):
        raise ValueError("Closure requires unique supporting L2 IDs from this inventory.")
    obligations = []
    for pid, parent in sorted(catalog["principles"].items()):
        overlap = sorted(set(support_ids) & set(parent["supporting_records"]))
        if not overlap:
            continue
        for kind, field in (("counterexample", "counterexamples"), ("contradiction", "contradictions")):
            for index, link in enumerate(parent[field]):
                obligations.append({"source_principle_id": pid, "parent_artifact": copy.deepcopy(parent["artifact"]),
                    "link_pointer": parent["content_pointer"] + f"/{field}/{index}",
                    "record_id": link["record_id"], "kind": kind, "explanation": link["explanation"],
                    "support_overlap": overlap,
                    "parent_eligible_for_level4": parent["qualification"]["eligible_for_level4"],
                    "status": "requires_scope_review"})
    return {"schema_version": "knowledge_known_counter_closure.v1", "inventory": _ref(freeze_ref),
            "supporting_records": list(support_ids), "obligations": obligations,
            "counter_record_ids": sorted({row["record_id"] for row in obligations}),
            "inventory_complete": catalog["inventory_complete"], "policy": copy.deepcopy(POLICY),
            "scope": "Known support-overlapping L3 links across all supplied parents, including ineligible ones; "
                     "not semantic discovery, evidence independence or a retention/exclusion decision."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "freeze", "validate", "closure"))
    parser.add_argument("--input", type=Path, required=True, help="Pinned private selection or inventory JSON")
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--support", action="append", default=[])
    args = parser.parse_args(argv)
    if bool(args.output) != (args.command == "freeze") or bool(args.support) != (args.command == "closure"):
        parser.error("Only freeze requires --output; only closure requires one or more --support IDs")
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
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "note": "Inspect the private selection and pinned dependencies."}))
        return 2
    print(json.dumps({"status": "created" if args.command == "freeze" else "checked",
                      "inventory_complete": result["inventory_complete"], "model_calls": 0,
                      "case_count": len(result.get("cases", {})), "principle_count": len(result.get("principles", {})),
                      "gap_count": len(result.get("gaps", [])), "obligation_count": len(result.get("obligations", [])),
                      "artifact_sha256": ref["sha256"] if args.command != "inspect" else None,
                      "policy": result["policy"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
