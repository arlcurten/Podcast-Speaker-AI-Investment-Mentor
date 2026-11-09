"""Bounded, source-bound comparison of explicitly pinned native synthesis parents.

Existing native and extension readers are unchanged. This consumer constructs a
temporary comparison view, not a third persisted inventory. Extension parents
must inherit the exact configured v3 inventory/selection; recursive reconciliation
parents are not supported. All parents and their pending records remain pinned.

Use schema --kind config|submission, prepare --input CONFIG --sha256 SHA, then
preview|apply --input SUBMISSION --sha256 SHA --output NEW_PRIVATE_PATH.
read|validate --input CANDIDATE --sha256 SHA reconstructs the complete packet.
No command authors claims, calls a model, accepts sources or publishes a profile.

Metadata limits cover explicit entry files before parent replay; existing parent
loaders enforce their own lineage limits. Source materialization is serial. Full
closure is constructed before packet/candidate byte checks: these are serialized
size limits, not peak-RAM guarantees. Overflow never trims evidence or context.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from knowledge.schemas import array, enum, obj
from transcription.common import atomic_write_json, load_project_dotenv, private_path, sha256_file, utc_now
from . import native_synthesis_extension as extension

inventory, parent = extension.extension, extension.parent
native, pipeline, recovery = extension.native, extension.pipeline, extension.recovery
REF, TEXT, IDS = extension.REF, extension.TEXT, extension.IDS
CONFIG_VERSION = "profile_native_reconciliation_config.v1"
SUBMISSION_VERSION = "profile_native_reconciliation_submission.v1"
PACKET_VERSION = "profile_native_reconciliation_packet.v1"
VERSION = "profile_native_reconciliation_candidate.v1"
CONTEXT_CITATION = obj(context_id=TEXT, **parent.schemas.QUOTE["properties"])
CONTEXT_CITATIONS = {**array(CONTEXT_CITATION), "uniqueItems": True}
CONFIG_SCHEMA = obj(
    schema_version=enum(CONFIG_VERSION), inventory=REF, selection=REF,
    parents=array(obj(kind=enum("native", "native_extension"), artifact=REF), 2),
    target_hypothesis_keys={**IDS, "minItems": 2}, workset_evidence_ids={**IDS, "minItems": 1},
    comparison_tasks=array(obj(comparison_id=TEXT, evidence_ids={**IDS, "minItems": 2},
        parent_hypothesis_keys={**IDS, "minItems": 1}, context_ids=IDS, purpose=TEXT), 1),
    additional_source_context=array(obj(context_id=TEXT, episode_id=TEXT, evidence=REF, raw=REF,
        normalized=REF, merged_ids={**array({"type": "integer", "minimum": 0}, 1), "uniqueItems": True},
        purpose=TEXT)),
    actor=native.ACTOR, reports=extension.CONFIG_SCHEMA["properties"]["reports"],
    limits=obj(max_parents=native.LIMIT, max_metadata_bytes=native.LIMIT,
        **extension.CONFIG_SCHEMA["properties"]["limits"]["properties"]))
POLICY = {**extension.POLICY, "scope": "bounded_cross_version_native_reconciliation",
    "additional_context_authority": "source_context_only_not_profile_evidence",
    "parent_dispositions": "immutable_originals_plus_separate_current_analysis"}
INSTRUCTIONS = extension.INSTRUCTIONS + """
Compare the explicitly versioned parents, not just their labels. Parent hypotheses
are abstractions, not additional observations. Preserve strong and rare statements,
conditions, counterexamples and competing preferences without imposing a default
conservative persona. Do not infer agreement, evolution, or a common motive from
similar wording alone. Comparison-only output with no new hypothesis is valid.
Additional mapped raw context can qualify comparisons/dispositions through separate
context_citations; it cannot replace primary anchors, support, or inference bases.
Preserve listener/host and intention/action distinctions. Inherited unresolved or
pending records are never rewritten by this author's separate dispositions.
"""


def submission_schema():
    fields = copy.deepcopy(extension.submission_schema()["properties"])
    fields["schema_version"] = enum(SUBMISSION_VERSION)
    fields["context_reading"] = obj(inspected_context_ids=IDS, pending_context_ids=IDS,
        method=TEXT, limitations=array(TEXT, 1))
    for name in ("comparisons", "inherited_dispositions"):
        row = fields[name]["items"]["properties"]
        row["context_citations"] = CONTEXT_CITATIONS
        if name == "comparisons":
            row["parent_hypothesis_keys"] = {**IDS, "minItems": 1}
        fields[name]["items"] = obj(**row)
    return obj(**fields)


def _implementation():
    return {"reconciliation_sha256": sha256_file(Path(__file__)),
        "parents": extension._implementation(), "config_schema_sha256": pipeline.digest(CONFIG_SCHEMA),
        "submission_schema_sha256": pipeline.digest(submission_schema()),
        "instructions_sha256": pipeline.digest(INSTRUCTIONS)}


def _finish(pins, implementation):
    recovery._recheck(pins)
    if _implementation() != implementation:
        raise ValueError("Reconciliation implementation changed during replay.")


def _pin_refs(pins, refs):
    for ref in refs:
        ref = inventory._ref(ref)
        recovery._pin(pins, Path(ref["path"]), ref["sha256"])


def _pin_dependencies(pins, refs):
    # Checked loaders also bind public implementation/terminology dependencies.
    for ref in refs:
        recovery._pin(pins, Path(ref["path"]), ref["sha256"])


def _put(rows, key, value):
    if key in rows:
        raise ValueError("Duplicate version-qualified parent record: " + key)
    rows[key] = copy.deepcopy(value)


def _join_extension(index, ref, candidate, packet):
    """Join checked originals in memory; never fabricate a persisted v3 artifact."""
    if candidate["inherited_inventory"]["inventory"] != packet["inherited_inventory"]["inventory"]:
        raise ValueError("Parent inventory binding differs.")
    required = set(candidate["scope"]["required_evidence_ids"])
    if (required != set(packet["observations"])
            or required != set(candidate["grounding"]["retained_evidence_ids"])):
        raise ValueError("Parent evidence closure/accounting differs.")
    for rid, row in packet["observations"].items():
        if row["binding"] != index["evidence"].get(rid):
            raise ValueError("Parent evidence ID/version binding differs.")
    for eid, source in packet["source_context"].items():
        entry = index["episodes"].get(eid)
        if (entry is None or source["evidence"] != entry["evidence"]["input"]
                or source["raw"]["sha256"] != entry["source_hashes"]["raw_sha256"]):
            raise ValueError("Parent episode source/version binding differs.")
    prefix = ref["sha256"] + ":"
    authored = candidate["authored"]
    anchors = parent._unique(authored["hypothesis_anchors"], "hypothesis_id")
    for number, h in enumerate(authored["content"]["hypotheses"]):
        _put(index["hypotheses"], prefix + h["hypothesis_id"], {
            **h, "artifact": ref, "artifact_kind": "native_extension",
            "content_pointer": f"/authored/content/hypotheses/{number}",
            "grounding": candidate["grounding"]["hypotheses"][h["hypothesis_id"]],
            "anchor_binding": anchors[h["hypothesis_id"]], "recovered_comparisons": [],
            "authority": candidate["authority"], "policy": candidate["policy"]})
    for number, comparison in enumerate(authored["comparisons"]):
        _put(index["native_comparisons"], prefix + comparison["comparison_id"], {
            "artifact": ref, "pointer": f"/authored/comparisons/{number}", "comparison": comparison})
    for number, task in enumerate(authored["comparison_coverage"]):
        if task["status"] == "pending":
            _put(index["native_pending_comparisons"], prefix + task["comparison_id"], {
                "artifact": ref, "pointer": f"/authored/comparison_coverage/{number}", "task": task})


def _parents(cfg, config_ref, pins):
    refs = [inventory._ref(row["artifact"]) for row in cfg["parents"]]
    if (len(refs) > cfg["limits"]["max_parents"]
            or len({r["path"] for r in refs}) != len(refs)
            or len({r["sha256"] for r in refs}) != len(refs)):
        raise ValueError("Parent count/duplicate path or hash limit violated.")
    entry_refs = [config_ref, cfg["inventory"], cfg["selection"], *refs, *[r["ref"] for r in cfg["reports"]]]
    _pin_refs(pins, entry_refs)
    if sum(Path(p).stat().st_size for p in {r["path"] for r in entry_refs}) > cfg["limits"]["max_metadata_bytes"]:
        raise ValueError("Explicit metadata exceeds configured byte limit; no truncation.")
    index = inventory.load_checked(cfg["inventory"], cfg["selection"])
    _pin_dependencies(pins, index["dependencies"])
    index = copy.deepcopy(index)
    records = []
    for descriptor, ref in zip(cfg["parents"], refs):
        if descriptor["kind"] == "native":
            # The inventory loader has already independently replayed these parents.
            registered = [r for r in index["syntheses"].values()
                if r["synthesis"]["status"] == "available"
                and r["synthesis"]["input"].get("artifact") == ref]
            if len(registered) != 1 or registered[0]["schema_version"] != parent.VERSION:
                raise ValueError("Native parent must be the exact checked inventory version.")
            candidate = native._read(ref, pins)
            records.append({"kind": "native", "artifact": ref,
                "pending_comparisons": copy.deepcopy([r for r in candidate["comparison_coverage"]
                                                       if r["status"] == "pending"]),
                "known_context": copy.deepcopy(candidate["known_context"])})
        else:
            header = native._read(ref, pins)
            if header.get("schema_version") != extension.VERSION:
                raise ValueError("Expected a native extension parent, not nested reconciliation.")
            if any(header["inherited_inventory"][key] != cfg[key] for key in ("inventory", "selection")):
                raise ValueError("Extension parent must inherit the exact inventory and selection.")
            del header
            checked = extension.read(ref)
            candidate, packet = checked["candidate"], checked["packet"]
            _pin_refs(pins, [ref])
            _pin_dependencies(pins, candidate["dependencies"])
            _join_extension(index, ref, candidate, packet)
            records.append({"kind": "native_extension", "artifact": ref,
                "inherited_inventory": copy.deepcopy(candidate["inherited_inventory"]),
                "inherited_obligations": copy.deepcopy(candidate["inherited_obligations"]),
                "inherited_dispositions": copy.deepcopy(candidate["inherited_dispositions"]),
                "pending_comparisons": copy.deepcopy([r for r in candidate["authored"]["comparison_coverage"]
                                                       if r["status"] == "pending"])})
            del checked, packet
        del candidate
    recovery._recheck(pins)
    return index, records


def _tasks(cfg, index):
    targets = set(cfg["target_hypothesis_keys"])
    parent_shas = {r["artifact"]["sha256"] for r in cfg["parents"]}
    if (not targets <= index["hypotheses"].keys()
            or any(index["hypotheses"][k]["artifact"]["sha256"] not in parent_shas for k in targets)
            or len({index["hypotheses"][k]["artifact"]["sha256"] for k in targets}) < 2):
        raise ValueError("Targets must identify exact hypotheses from distinct configured parents.")
    tasks = parent._unique(cfg["comparison_tasks"], "comparison_id")
    contexts = parent._unique(cfg["additional_source_context"], "context_id")
    seeds = set(cfg["workset_evidence_ids"])
    for key in targets:
        seeds.update(inventory._hypothesis_ids(index["hypotheses"][key]))
    for task in tasks.values():
        if (not set(task["parent_hypothesis_keys"]) <= targets
                or not set(task["context_ids"]) <= contexts.keys()):
            raise ValueError("Task references unknown target hypothesis/context.")
        endpoints = inventory.base._basis_closure(index["evidence"], task["evidence_ids"])
        for key in task["parent_hypothesis_keys"]:
            ids = inventory.base._basis_closure(index["evidence"], inventory._hypothesis_ids(index["hypotheses"][key]))
            if not endpoints & ids:
                raise ValueError("Task has no evidence endpoint incident to its linked parent hypothesis.")
        seeds.update(endpoints)
    return sorted(inventory.base._basis_closure(index["evidence"], seeds))


def _additional_context(cfg, index, sources, pins):
    result = {}
    # Only one episode's normalized/raw documents live at a time.
    for eid in sorted({r["episode_id"] for r in cfg["additional_source_context"]}):
        if eid not in sources:
            raise ValueError("Additional context must belong to a required evidence episode.")
        entry = index["episodes"][eid]
        rows = [r for r in cfg["additional_source_context"] if r["episode_id"] == eid]
        if entry["evidence"]["input"]["kind"] == "native":
            normalized_ref = entry["native_lineage"]["source_files"]["normalized"]
        else:
            manifest = native._read(entry["original_manifest"], pins)
            original = next(e for e in manifest["config"]["episodes"] if e["episode_id"] == eid)
            normalized_ref = {"path": original["draft"], "sha256": entry["source_hashes"]["normalized_sha256"]}
        for row in rows:
            if (inventory._ref(row["evidence"]) != entry["evidence"]["input"]["artifact"]
                    or inventory._ref(row["raw"]) != sources[eid]["raw"]
                    or row["normalized"]["sha256"] != entry["source_hashes"]["normalized_sha256"]):
                raise ValueError("Additional context source/evidence version differs.")
            if row["normalized"] != normalized_ref:
                raise ValueError("Additional normalized source path differs.")
            _pin_refs(pins, [row["evidence"], row["raw"], row["normalized"]])
        if any((r["raw"], r["normalized"]) != (rows[0]["raw"], rows[0]["normalized"]) for r in rows):
            raise ValueError("Conflicting same-episode additional source bindings.")
        normalized = native._read(rows[0]["normalized"], pins)
        mapped = parent._unique(normalized["segments"], "merged_id")
        raw = native._read(rows[0]["raw"], pins)
        raw_rows = parent._unique(raw["segments"], "segment_id")
        if any(type(key) is not int or key < 0 for key in (*mapped, *raw_rows)):
            raise ValueError("Source region IDs must be nonnegative integers.")
        for row in rows:
            if not set(row["merged_ids"]) <= mapped.keys():
                raise ValueError("Missing additional merged source ID.")
            regions = [mapped[mid] for mid in row["merged_ids"]]
            ids = set()
            for region in regions:
                sids = region["source_segment_ids"]
                if (not sids or len(sids) != len(set(sids))
                        or any(type(sid) is not int or sid < 0 for sid in sids)
                        or not set(sids) <= raw_rows.keys()):
                    raise ValueError("Missing/duplicate additional raw source IDs.")
                ids.update(sids)
            result[row["context_id"]] = {"binding": copy.deepcopy(row),
                "authority": POLICY["additional_context_authority"],
                "merged_regions": copy.deepcopy(regions),
                "raw_regions": [{k: r[k] for k in ("segment_id", "start", "end", "text")}
                    for r in raw["segments"] if r["segment_id"] in ids]}
        del raw, raw_rows, normalized, mapped
    return result


def _prepare(config_ref, pins):
    implementation = _implementation()
    config_ref = inventory._ref(config_ref)
    cfg = native._read(config_ref, pins)
    parent._validate(cfg, CONFIG_SCHEMA)
    for key in ("inventory", "selection"):
        inventory._ref(cfg[key])
    reports = set()
    for row in cfg["reports"]:
        ref = inventory._ref(row["ref"])
        key = (ref["path"], ref["sha256"])
        if key in reports:
            raise ValueError("Duplicate report reference.")
        reports.add(key)
        native._time(row["authored_at"])
    index, records = _parents(cfg, config_ref, pins)
    seeds = _tasks(cfg, index)
    closure = inventory.checked_index_closure(index, cfg["inventory"], seeds)
    obligations = extension._obligations(index, closure)
    extension._complete_hypotheses(obligations, pins)
    observations, sources, raw_regions = extension._materialize(index, closure["required_evidence_ids"], pins)
    packet = {"schema_version": PACKET_VERSION, "config": config_ref, "implementation": implementation,
        "instructions": INSTRUCTIONS, "submission_schema": submission_schema(), "actor": cfg["actor"],
        "reports": cfg["reports"], "parents": records, "comparison_tasks": cfg["comparison_tasks"],
        "target_hypothesis_keys": cfg["target_hypothesis_keys"],
        "scope": {"workset_evidence_ids": cfg["workset_evidence_ids"], "closure_seed_ids": seeds,
            "required_evidence_ids": closure["required_evidence_ids"],
            "outside_evidence_ids": sorted(set(index["evidence"]) - set(observations)),
            "inventory_complete": index["inventory_complete"], "inventory_gaps": index["gaps"]},
        "inherited_inventory": {"inventory": cfg["inventory"], "selection": cfg["selection"],
            "retention": "Pinned originals retained; incident closure only, not global reconciliation.",
            **{k + "_keys": sorted(index[k]) for k in
                ("hypotheses", "native_comparisons", "native_pending_comparisons", "native_context")}},
        "observations": observations, "source_context": sources, "raw_regions": raw_regions,
        "additional_source_context": _additional_context(cfg, index, sources, pins),
        "inherited_obligations": obligations, "policy": copy.deepcopy(POLICY)}
    extension._bounded(packet, cfg["limits"]["max_packet_bytes"], "Reconciliation packet")
    _finish(pins, implementation)
    return cfg, index, packet


def prepare(config_ref):
    """Read-only packet construction; pin its digest before native authorship."""
    _, _, packet = _prepare(config_ref, {})
    return {"packet_sha256": pipeline.digest(packet), "packet_bytes": native._size(packet), "packet": packet}


def _context_citations(citations, packet, inspected):
    for citation in citations:
        cid = citation["context_id"]
        if cid not in inspected:
            raise ValueError("Context citation requires declared inspection.")
        rows = packet["additional_source_context"][cid]["raw_regions"]
        row = next((r for r in rows if r["segment_id"] == citation["segment_id"]), None)
        if row is None or any(citation[k] != row[k] for k in ("start", "end", "text")):
            raise ValueError("Context citation must exactly match its mapped raw region.")


def _authored(submission, cfg, index, packet, pins):
    reading = submission["context_reading"]
    inspected, pending = set(reading["inspected_context_ids"]), set(reading["pending_context_ids"])
    if inspected & pending or inspected | pending != packet["additional_source_context"].keys():
        raise ValueError("Context reading must partition the exact context packet.")
    tasks = parent._unique(cfg["comparison_tasks"], "comparison_id")
    coverage = parent._unique(submission["comparison_coverage"], "comparison_id")
    if tasks.keys() != coverage.keys() or any(set(coverage[k]["evidence_ids"]) != set(t["evidence_ids"])
                                            for k, t in tasks.items()):
        raise ValueError("Comparison coverage must exactly account for frozen tasks/endpoints.")
    for row in submission["comparisons"]:
        task = tasks.get(row["comparison_id"])
        if task is None or set(row["parent_hypothesis_keys"]) != set(task["parent_hypothesis_keys"]):
            raise ValueError("Comparison parent hypothesis versions differ from frozen task.")
        for key in row["parent_hypothesis_keys"]:
            required = inventory.base._basis_closure(index["evidence"],
                inventory._hypothesis_ids(index["hypotheses"][key]))
            if not required <= set(submission["reading"]["inspected_evidence_ids"]):
                raise ValueError("Parent comparison requires inspection of full support/counter/basis closure.")
        if {a["context_id"] for a in row["context_citations"]} != set(task["context_ids"]):
            raise ValueError("Comparison must cite every configured context, without extras.")
        _context_citations(row["context_citations"], packet, inspected)
    for row in submission["inherited_dispositions"]:
        _context_citations(row["context_citations"], packet, inspected)
    # Reuse the unchanged primary authority/accounting/incident-expansion validator.
    projected = copy.deepcopy(submission)
    for row in projected["comparisons"]:
        row.pop("parent_hypothesis_keys")
        row.pop("context_citations")
    for row in projected["inherited_dispositions"]:
        row.pop("context_citations")
    grounding, dispositions = extension._authored(projected, cfg, index, packet, pins)
    by_key = {r["obligation_key"]: r for r in submission["inherited_dispositions"]}
    return grounding, [copy.deepcopy(by_key.get(r["obligation_key"], r)) for r in dispositions]


def _build(submission_ref, output, created_at, pins):
    submission_ref = inventory._ref(submission_ref)
    submission = native._read(submission_ref, pins)
    parent._validate(submission, submission_schema())
    cfg, index, packet = _prepare(submission["config"], pins)
    if Path(submission_ref["path"]).stat().st_size > cfg["limits"]["max_submission_bytes"]:
        raise ValueError("Submission exceeds configured byte limit; no truncation.")
    path = private_path(output)
    if path in pins or any(path.is_relative_to(Path(p)) for p in index["protected_output_roots"]):
        raise ValueError("Candidate output overlaps protected inputs.")
    if native._time(submission["authored_at"]) > native._time(created_at):
        raise ValueError("Authorship cannot follow adoption.")
    grounding, dispositions = _authored(submission, cfg, index, packet, pins)
    result = {"schema_version": VERSION, "stage": "synthesis", "status": "unreviewed_candidate",
        "authority": native.AUTHORITY, "created_at": created_at, "output_path": str(path),
        "config": packet["config"], "submission": submission_ref, "packet_sha256": pipeline.digest(packet),
        "implementation": packet["implementation"], "dependencies": native._refs(pins),
        **{key: copy.deepcopy(packet[key]) for key in ("scope", "parents", "target_hypothesis_keys",
            "comparison_tasks", "inherited_inventory", "inherited_obligations")},
        "additional_context_bindings": {k: v["binding"] for k, v in packet["additional_source_context"].items()},
        "inherited_dispositions": dispositions,
        "authorship": {k: copy.deepcopy(submission[k]) for k in ("actor", "authored_at", "method", "limitations")},
        "reports": cfg["reports"], "grounding": grounding,
        "authored": {k: copy.deepcopy(submission[k]) for k in ("content", "hypothesis_anchors", "comparisons",
            "comparison_coverage", "reading", "context_reading")},
        "model_metadata": copy.deepcopy(native.MODEL_METADATA), "adoption": copy.deepcopy(native.ADOPTION),
        "policy": copy.deepcopy(POLICY)}
    result["fingerprint"] = pipeline.digest(result)
    extension._bounded(result, cfg["limits"]["max_candidate_bytes"], "Reconciliation candidate")
    _finish(pins, packet["implementation"])
    return result, packet


def preview(submission_ref, output):
    if private_path(output).exists():
        raise FileExistsError("Use a new private candidate path.")
    return _build(submission_ref, output, utc_now(), {})[0]


def apply(submission_ref, output):
    path = private_path(output)
    if path.exists():
        raise FileExistsError("Use a new private candidate path.")
    pins = {}
    result, _ = _build(submission_ref, path, utc_now(), pins)
    if private_path(output) != path:
        raise ValueError("Output path changed during adoption.")
    _finish(pins, result["implementation"])
    atomic_write_json(path, result, overwrite=False)
    return {"path": str(path), "sha256": sha256_file(path)}


def read(ref):
    ref = inventory._ref(ref)
    value = native._read(ref, {})
    if value.get("schema_version") != VERSION:
        raise ValueError("Expected an explicit native reconciliation candidate.")
    pins = {}
    expected, packet = _build(value["submission"], ref["path"], value["created_at"], pins)
    if parent.review._json(value) != parent.review._json(expected):
        raise ValueError("Reconciliation differs from deterministic replay.")
    _pin_refs(pins, [ref])
    _finish(pins, expected["implementation"])
    return {"candidate": value, "packet": packet}


def load_checked(ref):
    return read(ref)["candidate"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="prepare",
        choices=("prepare", "preview", "apply", "read", "validate", "schema"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--kind", choices=("config", "submission"))
    args = parser.parse_args(argv)
    if args.command == "schema":
        if not args.kind or args.input or args.sha256 or args.output:
            parser.error("schema requires only --kind config|submission")
        print(json.dumps(CONFIG_SCHEMA if args.kind == "config" else submission_schema(), indent=2))
        return 0
    if args.kind or not args.input or not args.sha256 or bool(args.output) != (args.command in {"preview", "apply"}):
        parser.error("Use --input --sha256, with --output only for preview/apply")
    load_project_dotenv()
    try:
        ref = {"path": str(args.input), "sha256": args.sha256}
        if args.command == "prepare":
            result = prepare(ref)
        elif args.command == "read":
            result = read(ref)
        elif args.command == "apply":
            result = {"artifact": apply(ref, args.output), "adoption": native.ADOPTION}
        else:
            result = preview(ref, args.output) if args.command == "preview" else load_checked(ref)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "adoption": native.ADOPTION}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
