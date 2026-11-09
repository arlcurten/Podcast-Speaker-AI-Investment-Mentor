"""Explicit native-authored, bounded Knowledge synthesis; no backend receipts.

CLI: python -m knowledge.native_synthesis schema --kind config|submission
prepare --input CONFIG --sha256 SHA prints a read-only authoring packet.
preview/apply --input SUBMISSION --sha256 SHA validate the author's submission;
only apply requires --output NEW_PRIVATE_PATH. validate/read accept a candidate.

Config pins a checked corpus_inventory.v1, actor, reports, selected L2 record IDs
and positive byte limits. Submit the returned packet_sha256, actual author/time/
method, complete case and inherited-principle accounting, L4-schema content and
exact per-principle case anchors. Prior source work may be transparently recorded;
prepare is not a claim that a packet was frozen before authorship. Unknown native
model/usage/cost stay null; import calls=0 does not measure reasoning usage.

Only selected cases may support new principles. One incident L3 pass retains all
support/counter endpoints, including ineligible parents; context cases are not
new supports. No recursive family expansion or Cartesian discovery is required.
Outside-workset records and missing stages remain explicit, not inspected by
implication. Existing discovery/verification outputs are NOT imported by this
contract; pinned prose reports do not authenticate their machine coverage.
Existing L4 schema/qualification rules apply unchanged, including two-episode
support and complete supporting-pair accounting. Narrower observations remain
cases. These new candidates are not ordinary pipeline L4 artifacts or releases.
Exact anchors and accounting do not certify entailment or semantic completeness.
Inventory replay has its own memory cost; limits reject, never truncate packets.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime
from pathlib import Path

from transcription.common import atomic_write_json, load_project_dotenv, private_path, sha256_file
from . import case_release, corpus_inventory as inventory, family_reconciliation, pipeline, provenance, schemas
from .schemas import TEXT, array, enum, obj

CONFIG_VERSION = "knowledge_native_synthesis_config.v1"
SUBMISSION_VERSION = "knowledge_native_synthesis_submission.v1"
VERSION = "knowledge_native_synthesis_candidate.v1"
REF = inventory.REF
IDS = {**array(TEXT), "uniqueItems": True}
LIMIT = {"type": "integer", "minimum": 1}
ACTOR = obj(identity=TEXT, session_id=TEXT)
CONFIG = obj(schema_version=enum(CONFIG_VERSION), inventory=REF,
    workset_record_ids={**IDS, "minItems": 1}, actor=ACTOR, reports=array(REF, 1),
    limits=obj(max_packet_bytes=LIMIT, max_submission_bytes=LIMIT, max_candidate_bytes=LIMIT))
ANCHORS = array(family_reconciliation.ANCHOR)
SUBMISSION = obj(schema_version=enum(SUBMISSION_VERSION), config=REF,
    packet_sha256=REF["properties"]["sha256"], actor=ACTOR, authored_at=TEXT, method=TEXT,
    content=schemas.schema("level4", candidate=True),
    case_accounting=array(obj(record_id=TEXT, status=enum("inspected", "pending"),
                              reason=TEXT, source_anchors=ANCHORS)),
    inherited_accounting=array(obj(principle_id=TEXT, status=enum("inspected", "pending"), reason=TEXT)),
    grounding=array(obj(principle_id=TEXT, record_id=TEXT, source_anchors={**ANCHORS, "minItems": 1})))
POLICY = {"scope": "bounded_native_knowledge_candidate", "evidence_authority": "original_level2",
    "source_acceptance": "not_granted", "source_holds": "unchanged", "semantic_review": "pending",
    "eligible_for_serving": False, "publication_authorized": False, "global_completion": False,
    "discovery_coverage": "not_imported", "semantic_entailment": "not_certified",
    "preauthoring_freeze": "not_asserted"}


def _implementation():
    return {"native_synthesis_sha256": sha256_file(Path(__file__)),
            "inventory": inventory._implementation(),
            "anchor_checker_sha256": sha256_file(Path(family_reconciliation.__file__)),
            "config_schema_sha256": provenance.digest(CONFIG),
            "submission_schema_sha256": provenance.digest(SUBMISSION)}


def _bounded(value, limit, label):
    size = len((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    if size > limit:
        raise ValueError(f"{label} needs {size} bytes; limit={limit}; no truncation.")


def _finish(pins, implementation):
    case_release._check_pins(pins)
    if _implementation() != implementation:
        raise ValueError("Native synthesis implementation changed during replay.")


def _closure(catalog, selected):
    if not set(selected) <= catalog["cases"].keys():
        raise ValueError("Workset must name inventory L2 cases, not abstractions.")
    required, parents = set(selected), []
    for pid, p in sorted(catalog["principles"].items()):
        refs = set(p["supporting_records"]) | {r["record_id"] for field in
            ("counterexamples", "contradictions") for r in p[field]}
        if refs & set(selected):
            required.update(refs)
            parents.append(pid)
    if not required <= catalog["cases"].keys():
        raise ValueError("Known L3 closure has unavailable case references.")
    return sorted(required), parents


def _materialize(catalog, required, parents, pins):
    bundle = {"cases": [], "principles": [], "sources": {}, "raw_regions": {}, "bindings": {}}
    upstream = []
    for eid in sorted({catalog["cases"][rid]["episode_id"] for rid in required}):
        entry = catalog["episodes"][eid]
        stages = {s: inventory._read(state["artifact"], pins) for s, state in entry["stages"].items()
                  if state["status"] == "available"}
        # Real complete stage objects, never fabricated or projected pipeline parents.
        upstream.extend(stages[s] for s in ("level2", "level3") if s in stages)
        cases = provenance.case_catalog([stages["level2"]])
        selected = [cases[rid] for rid in required if catalog["cases"][rid]["episode_id"] == eid]
        bundle["cases"].extend(selected)
        bundle["principles"].extend({**p, "qualification": stages["level3"]["qualification"][p["principle_id"]]}
            for p in stages.get("level3", {}).get("content", {}).get("principles", [])
            if p["principle_id"] in parents)
        cfg = case_release._snapshot(entry["config"]["path"], pins, entry["config"]["sha256"], yaml_config=True)
        original = next(row for row in cfg["episodes"] if row["episode_id"] == eid)
        raw = inventory._read({"path": original["raw"], "sha256": entry["source"]["raw_sha256"]}, pins)
        by_id = {row["segment_id"]: row for row in raw["segments"]}
        ids = set()
        for case in selected:
            e = case["evidence"]
            ids.update(e["source_segment_ids"])
            for r in e["source_segment_id_ranges"]:
                ids.update(range(r["start_id"], r["end_id"] + 1))
            # Annotation anchors remain context; _anchors still checks primary case IDs.
            for note in e.get("reviewed_annotations", {}).get("annotations", []):
                ids.update(a["segment_id"] for a in note["anchors"])
            bundle["bindings"][case["record_id"]] = copy.deepcopy(catalog["cases"][case["record_id"]])
        bundle["raw_regions"][eid] = [{k: by_id[sid][k] for k in ("segment_id", "start", "end", "text")}
                                      for sid in sorted(ids)]
        bundle["sources"][eid] = {"source": entry["source"], "episode_map": stages["level1"]["content"],
            "thread_dispositions": stages["level2"]["content"]["thread_dispositions"],
            "stage_warnings": {s: a.get("warnings", []) for s, a in stages.items()}}
    return bundle, upstream


def _prepare(config_ref):
    pins, implementation = {}, _implementation()
    cfg = inventory._read(config_ref, pins)
    inventory._validate(cfg, CONFIG)
    for ref in cfg["reports"]:
        inventory._pin_file(ref, pins)
    inventory._pin_file(cfg["inventory"], pins)
    catalog = inventory.load_checked(cfg["inventory"])
    for ref in catalog["dependencies"]:
        inventory._pin_file(ref, pins)
    required, parents = _closure(catalog, cfg["workset_record_ids"])
    bundle, upstream = _materialize(catalog, required, parents, pins)
    packet = {"schema_version": "knowledge_native_synthesis_packet.v1", "config": inventory._ref(config_ref),
        "inventory": cfg["inventory"], "actor": cfg["actor"], "reports": cfg["reports"],
        "workset_record_ids": cfg["workset_record_ids"], "required_record_ids": required,
        "inherited_principle_ids": parents, "bundle": bundle,
        "outside_record_ids": sorted(set(catalog["cases"]) - set(required)),
        "outside_principle_ids": sorted(set(catalog["principles"]) - set(parents)),
        "inventory_complete": catalog["inventory_complete"], "inventory_gaps": catalog["gaps"],
        "implementation": implementation, "policy": copy.deepcopy(POLICY)}
    packet["packet_sha256"] = provenance.digest(packet)
    _bounded(packet, cfg["limits"]["max_packet_bytes"], "Complete authoring packet")
    _finish(pins, implementation)
    return cfg, catalog, packet, upstream, pins, implementation


def prepare(config_ref):
    """Read-only materialization; one authoritative inventory replay."""
    return _prepare(config_ref)[2]


def _account(rows, key, expected):
    result = {row[key]: row for row in rows}
    if len(result) != len(rows) or result.keys() != set(expected):
        raise ValueError("Accounting must cover every exact packet identity once.")
    return result


def _derive(submission, cfg, packet, upstream):
    if submission["actor"] != cfg["actor"] or submission["packet_sha256"] != packet["packet_sha256"]:
        raise ValueError("Actor or packet binding differs.")
    if datetime.fromisoformat(submission["authored_at"].replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("Authorship time needs a timezone, not an inferred freeze time.")
    bundle = packet["bundle"]
    cases = _account(submission["case_accounting"], "record_id", packet["required_record_ids"])
    parents = _account(submission["inherited_accounting"], "principle_id", packet["inherited_principle_ids"])
    for rid, row in cases.items():
        if bool(row["source_anchors"]) != (row["status"] == "inspected"):
            raise ValueError("Inspected cases require anchors; pending cases cannot claim inspection.")
        family_reconciliation._anchors(row["source_anchors"], rid, bundle)
    expected = set()
    for p in submission["content"]["principles"]:
        if not set(p["supporting_records"]) <= set(cfg["workset_record_ids"]):
            raise ValueError("Context/outside cases cannot silently become selected supports.")
        refs = set(p["supporting_records"]) | {r["record_id"] for field in
            ("counterexamples", "contradictions", "counterevidence_dispositions") for r in p[field]}
        if any(r not in cases or cases[r]["status"] != "inspected" for r in refs):
            raise ValueError("Principle references require inspected packet cases.")
        for pid in p["derived_from"]:
            row = cases.get(pid) or parents.get(pid)
            if row is None or row["status"] != "inspected":
                raise ValueError("Derivation parents require inspected packet cases or inherited principles.")
        if any(row["source_principle_id"] not in parents
               or parents[row["source_principle_id"]]["status"] != "inspected"
               for row in p["counterevidence_dispositions"]):
            raise ValueError("Counter dispositions require inspected inherited principles.")
        expected.update((p["principle_id"], rid) for rid in refs)
    seen = set()
    for row in submission["grounding"]:
        pair = row["principle_id"], row["record_id"]
        if pair not in expected or pair in seen:
            raise ValueError("Duplicate or extraneous principle grounding.")
        seen.add(pair)
        family_reconciliation._anchors(row["source_anchors"], row["record_id"], bundle)
    if seen != expected:
        raise ValueError("Missing principle support/counter/disposition grounding.")
    # Keep the existing schema, provenance, pair and known-counter checks unchanged.
    _, qualification, warnings = pipeline.derive("level4", submission["content"], None, upstream)
    for q in qualification.values():
        q.update(eligible_for_online_reasoning=False, runtime_mode_recommendation="not_eligible")
        q["eligibility_reasons"].append("Native synthesis candidate requires separate source and semantic release review.")
    return qualification, warnings


def _build(submission_ref):
    submission_pins = {}
    s = inventory._read(submission_ref, submission_pins)
    inventory._validate(s, SUBMISSION)
    cfg, catalog, packet, upstream, pins, implementation = _prepare(s["config"])
    for path, checksum in submission_pins.items():
        inventory._pin_file({"path": str(path), "sha256": checksum}, pins)
    _bounded(s, cfg["limits"]["max_submission_bytes"], "Submission")
    qualification, warnings = _derive(s, cfg, packet, upstream)
    result = {"schema_version": VERSION, "submission": inventory._ref(submission_ref), "config": s["config"],
        "packet_sha256": packet["packet_sha256"], "content": copy.deepcopy(s["content"]),
        "qualification": qualification, "warnings": warnings, "grounding": copy.deepcopy(s["grounding"]),
        "case_accounting": copy.deepcopy(s["case_accounting"]),
        "inherited_accounting": copy.deepcopy(s["inherited_accounting"]),
        "scope": {k: packet[k] for k in ("inventory", "workset_record_ids", "required_record_ids",
            "inherited_principle_ids", "outside_record_ids", "outside_principle_ids", "inventory_complete", "inventory_gaps")},
        "authorship": {k: s[k] for k in ("actor", "authored_at", "method")}, "reports": cfg["reports"],
        "metadata": {"provider": "native_agent_candidate", "model": None, "usage": None, "cost_usd": None,
            "import_model_calls": 0, "reasoning_usage": "unknown_not_zero", "backend_receipt": None},
        "dependencies": [{"path": str(p), "sha256": h} for p, h in sorted(pins.items())],
        "implementation": implementation, "policy": copy.deepcopy(POLICY)}
    _bounded(result, cfg["limits"]["max_candidate_bytes"], "Candidate")
    _finish(pins, implementation)
    return result, packet, catalog, pins


def preview(submission_ref):
    return _build(submission_ref)[0]


def apply(submission_ref, output):
    path = private_path(output)
    if path.exists():
        raise FileExistsError("Choose a new immutable candidate path.")
    result, _, catalog, pins = _build(submission_ref)
    protected = set()
    for eid, row in catalog["episodes"].items():
        if row["config"]:
            cfg = case_release._snapshot(row["config"]["path"], pins, row["config"]["sha256"], yaml_config=True)
            protected.add(private_path(cfg["output_root"]) / cfg["run_id"])
            for source in cfg["episodes"]:
                protected.update(private_path(source[k]).parent for k in ("raw", "normalized"))
        for state in row["stages"].values():
            if state["status"] == "available":
                protected.add(Path(state["artifact"]["path"]).parent)
    if path in pins or any(path.is_relative_to(p) for p in protected) or private_path(output) != path:
        raise ValueError("Output overlaps protected source/extraction inputs.")
    _finish(pins, result["implementation"])
    atomic_write_json(path, result, overwrite=False)
    return {"path": str(path), "sha256": sha256_file(path)}


def load_checked(candidate_ref, *, with_packet=False):
    pins = {}
    stored = inventory._read(candidate_ref, pins)
    if not isinstance(stored, dict) or stored.get("schema_version") != VERSION:
        raise ValueError("Expected explicit native Knowledge synthesis candidate.")
    result, packet, _, dependencies = _build(stored["submission"])
    if provenance.digest(stored) != provenance.digest(result):
        raise ValueError("Candidate differs from deterministic submission/source replay.")
    case_release._check_pins(pins)
    _finish(dependencies, result["implementation"])
    return {"candidate": result, "packet": packet} if with_packet else result


def main(argv=None):
    load_project_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("schema", "prepare", "preview", "apply", "validate", "read"))
    parser.add_argument("--kind", choices=("config", "submission"))
    parser.add_argument("--input")
    parser.add_argument("--sha256")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.command == "schema":
        if not args.kind or args.input or args.sha256 or args.output:
            parser.error("schema requires only --kind")
        print(json.dumps(CONFIG if args.kind == "config" else SUBMISSION, indent=2))
        return 0
    if not args.input or not args.sha256 or args.kind or bool(args.output) != (args.command == "apply"):
        parser.error("Supply pinned input; only apply requires --output")
    ref = {"path": args.input, "sha256": args.sha256}
    try:
        if args.command == "apply":
            result = {"status": "created", "candidate": apply(ref, args.output), "import_model_calls": 0}
        elif args.command == "prepare":
            result = prepare(ref)
        elif args.command == "preview":
            result = preview(ref)
        else:
            result = load_checked(ref, with_packet=args.command == "read")
            if args.command == "validate":
                result = {"status": "checked", "principle_count": len(result["content"]["principles"]),
                          "import_model_calls": 0, "publication_authorized": False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError):
        print(json.dumps({"status": "failed", "note": "Inspect pinned private inputs; no approval granted."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
