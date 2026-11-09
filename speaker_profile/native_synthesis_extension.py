"""Bounded native synthesis over an explicitly checked v3 inventory.

python -m speaker_profile.native_synthesis_extension schema --kind config|submission
prepare --input CONFIG --sha256 SHA prints a read-only authoring packet. Bind its
packet_sha256 in an explicit submission; preview/apply take that submission and
--output NEW_PRIVATE_PATH. validate/read take a candidate reference; read returns
the replayed bounded source packet as well as the candidate. No preauthoring
freeze, backend execution, global completion or publication approval is asserted.
Outside-workset knowledge remains in the pinned inventory, not discarded or read
by implication. Existing synthesis/release readers do not accept this new format.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from knowledge.schemas import array, enum, obj
from transcription.common import atomic_write_json, load_project_dotenv, private_path, sha256_file, utc_now
from . import inventory_extension as extension, native_synthesis as parent

native, pipeline, recovery = extension.native, extension.pipeline, extension.recovery
REF, TEXT, IDS = native.REF, native.TEXT, parent.IDS
CONFIG_VERSION = "profile_native_synthesis_extension_config.v1"
SUBMISSION_VERSION = "profile_native_synthesis_extension_submission.v1"
PACKET_VERSION = "profile_native_synthesis_extension_packet.v1"
VERSION = "profile_native_synthesis_extension_candidate.v1"
CONFIG_SCHEMA = obj(schema_version=enum(CONFIG_VERSION), inventory=REF, selection=REF,
    workset_evidence_ids={**IDS, "minItems": 1}, actor=native.ACTOR,
    reports=parent.CONFIG_SCHEMA["properties"]["reports"],
    limits=obj(max_packet_bytes=native.LIMIT, max_submission_bytes=native.LIMIT, max_candidate_bytes=native.LIMIT))
POLICY = {**pipeline.POLICY, "scope": "bounded_native_synthesis_workset",
    "source_holds": "unchanged", "semantic_entailment": "not_certified",
    "global_completion": False, "preauthoring_freeze": "not_asserted",
    "closure_scope": "one_incident_pass_over_workset_and_bases_not_transitive",
    "inspection_scope": "declared_packet_subset_not_exhaustive_recall"}
INSTRUCTIONS = parent.INSTRUCTIONS + """
Author only from this packet's observations and inspected primary source anchors.
Inherited hypotheses are context, never additional observations. Preserve inherited
support/counters, typed comparisons, recovered caveats and pending work. An explicit
disposition adds attributed analysis; it never edits or resolves the original record.
Account for every required packet item, including context-only and ungrouped items.
Outside-scope items remain in the exact inventory and have not been inspected by
implication. Do not claim global synthesis, independent events or perfect recall.
"""


def submission_schema():
    fields = copy.deepcopy(parent.submission_schema()["properties"])
    fields.pop("context_dispositions")
    fields.update(schema_version=enum(SUBMISSION_VERSION), packet_sha256=REF["properties"]["sha256"],
        reading=parent.CONFIG_SCHEMA["properties"]["reading"],
        comparison_coverage=parent.CONFIG_SCHEMA["properties"]["comparison_coverage"],
        inherited_dispositions=array(obj(obligation_key=TEXT,
            disposition=enum("retained", "contextualized", "unresolved"), reason=TEXT,
            source_anchors=parent.CONTEXT_ANCHORS, report_refs=array(REF, 1))))
    return obj(**fields)


def _implementation():
    return {"consumer_sha256": sha256_file(Path(__file__)), "extension": extension._implementation(),
        "config_schema_sha256": pipeline.digest(CONFIG_SCHEMA),
        "submission_schema_sha256": pipeline.digest(submission_schema()),
        "instructions_sha256": pipeline.digest(INSTRUCTIONS)}


def _finish(pins, implementation):
    recovery._recheck(pins)
    if _implementation() != implementation:
        raise ValueError("Synthesis extension implementation changed during replay.")


def _bounded(value, limit, label):
    if native._size(value) > limit:
        raise ValueError(label + " exceeds configured byte limit; no truncation.")


def _raw_ids(grounding):
    ids = set(grounding["source_segment_ids"])
    for row in grounding["source_segment_id_ranges"]:
        ids.update(range(row["start_id"], row["end_id"] + 1))
    for note in grounding.get("supplementary_context", []):
        ids.update(_raw_ids(note["grounding"]))
    return ids


def _obligations(index, closure):
    rows = []
    for key, hypothesis in sorted(closure["hypotheses"].items()):
        rows.append(("prior_hypothesis", key, extension._hypothesis_ids(hypothesis), hypothesis))
    for row in closure["obligations"]:
        kind = row["kind"]
        if kind == "native_comparison":
            ids = [e["evidence_id"] for e in row["comparison"]["endpoints"]]
        elif kind == "pending_comparison":
            ids = row["task"]["evidence_ids"]
        elif kind == "prior_context":
            ids = set(row["context"]["required_evidence_ids"]) | extension._hypothesis_ids(row["prior_hypothesis"])
        else:
            raise ValueError("Unknown inherited obligation kind.")
        rows.append((kind, row["key"], ids, row))
    return [{"obligation_key": kind + ":" + key, "kind": kind,
             "required_evidence_ids": sorted(extension.base._basis_closure(index["evidence"], ids)),
             "original": copy.deepcopy(original)} for kind, key, ids, original in rows]


def _materialize(index, required, pins):
    observations, sources, raw_regions = {}, {}, {}
    for eid in sorted({index["evidence"][rid]["episode_id"] for rid in required}):
        entry = index["episodes"][eid]
        descriptor = entry["evidence"]["input"]
        artifact = native._read(descriptor["artifact"], pins)
        wanted = {rid for rid in required if index["evidence"][rid]["episode_id"] == eid}
        raw_ids = set()
        for item in artifact["content"]["items"]:
            rid = item["evidence_id"]
            if rid in wanted:
                grounding = artifact["grounding"]["items"][rid]
                observations[rid] = {"item": copy.deepcopy(item), "grounding": copy.deepcopy(grounding),
                    "binding": copy.deepcopy(index["evidence"][rid])}
                raw_ids.update(_raw_ids(grounding))
        sources[eid] = {"source": copy.deepcopy(artifact["source"]), "policy": copy.deepcopy(artifact["policy"]),
            "authority": entry["authority"], "evidence": copy.deepcopy(descriptor),
            "original_manifest": entry["original_manifest"],
            "unselected_regions": copy.deepcopy(artifact["content"]["unselected_regions"]),
            "unselected_region_grounding": copy.deepcopy(artifact["grounding"]["unselected_regions"])}
        if descriptor["kind"] == "native":
            if entry["original_manifest"] is not None:
                raise ValueError("Native evidence cannot acquire a backend manifest.")
            raw_ref = entry["native_lineage"]["source_files"]["raw"]
            sources[eid]["native_provenance"] = {key: copy.deepcopy(artifact[key]) for key in
                                                 ("authorship", "model_metadata", "adoption")}
        else:
            manifest = native._read(entry["original_manifest"], pins)
            original = next(e for e in manifest["config"]["episodes"] if e["episode_id"] == eid)
            raw_ref = {"path": original["raw"], "sha256": entry["source_hashes"]["raw_sha256"]}
        if raw_ref["sha256"] != entry["source_hashes"]["raw_sha256"]:
            raise ValueError("Raw source differs from checked inventory.")
        del artifact
        raw = native._read(raw_ref, pins)
        raw_regions[eid] = [{k: r[k] for k in ("segment_id", "start", "end", "text")}
                           for r in raw["segments"] if r["segment_id"] in raw_ids]
        if {r["segment_id"] for r in raw_regions[eid]} != raw_ids:
            raise ValueError("Missing mapped raw source region.")
        sources[eid]["raw"] = raw_ref
        del raw
    if set(observations) != set(required):
        raise ValueError("Checked evidence workset could not be materialized.")
    return observations, sources, raw_regions


def _complete_hypotheses(obligations, pins):
    # Historical index rows are navigation summaries, not complete claim content.
    cached_ref, artifact = None, None
    for row in obligations:
        if row["kind"] == "prior_hypothesis":
            hypothesis = row["original"]
        elif row["kind"] == "prior_context":
            hypothesis = row["original"]["prior_hypothesis"]
        else:
            continue
        if cached_ref != hypothesis["artifact"]:
            artifact = None
            artifact = native._read(hypothesis["artifact"], pins)
            cached_ref = hypothesis["artifact"]
        value = artifact
        for part in hypothesis["content_pointer"].lstrip("/").split("/"):
            value = value[int(part)] if isinstance(value, list) else value[part.replace("~1", "/").replace("~0", "~")]
        if value["hypothesis_id"] != hypothesis["hypothesis_id"]:
            raise ValueError("Inherited hypothesis pointer identity differs.")
        row["source_hypothesis"] = copy.deepcopy(value)
        del value


def _prepare(config_ref, pins):
    implementation = _implementation()
    config_ref = extension._ref(config_ref)
    cfg = native._read(config_ref, pins)
    parent._validate(cfg, CONFIG_SCHEMA)
    reports = set()
    for row in cfg["reports"]:
        ref = extension._ref(row["ref"])
        key = (ref["path"], ref["sha256"])
        if key in reports:
            raise ValueError("Duplicate author report.")
        reports.add(key)
        native._time(row["authored_at"])
        recovery._pin(pins, Path(ref["path"]), ref["sha256"])
    inventory_ref, selection_ref = extension._ref(cfg["inventory"]), extension._ref(cfg["selection"])
    # The v3 loader performs authoritative typed/reserved/cycle preflight before source loaders.
    index = extension.load_checked(inventory_ref, selection_ref)
    for ref in [inventory_ref, selection_ref, *index["dependencies"]]:
        recovery._pin(pins, Path(ref["path"]), ref["sha256"])
    if not set(cfg["workset_evidence_ids"]) <= index["evidence"].keys():
        raise ValueError("Workset references unknown evidence.")
    closure = extension.checked_index_closure(index, inventory_ref, cfg["workset_evidence_ids"])
    required = closure["required_evidence_ids"]
    obligations = _obligations(index, closure)
    _complete_hypotheses(obligations, pins)
    observations, sources, raw_regions = _materialize(index, required, pins)
    packet = {"schema_version": PACKET_VERSION, "config": config_ref, "implementation": implementation,
        "instructions": INSTRUCTIONS, "submission_schema": submission_schema(), "actor": copy.deepcopy(cfg["actor"]),
        "reports": copy.deepcopy(cfg["reports"]),
        "scope": {"workset_evidence_ids": sorted(cfg["workset_evidence_ids"]), "required_evidence_ids": required,
            "context_evidence_ids": sorted(set(required) - set(cfg["workset_evidence_ids"])),
            "outside_evidence_ids": sorted(set(index["evidence"]) - set(required)),
            "inventory_complete": index["inventory_complete"], "inventory_gaps": copy.deepcopy(index["gaps"])},
        "inherited_inventory": {"inventory": inventory_ref, "selection": selection_ref,
            "retention": "Complete pinned parent retained; only incident context materialized here, not all inspected.",
            **{key + "_keys": sorted(index[key]) for key in
               ("hypotheses", "native_comparisons", "native_pending_comparisons", "native_context")}},
        "observations": observations, "source_context": sources, "raw_regions": raw_regions,
        "inherited_obligations": obligations, "policy": copy.deepcopy(POLICY)}
    _bounded(packet, cfg["limits"]["max_packet_bytes"], "Authoring packet")
    _finish(pins, implementation)
    return cfg, index, packet


def prepare(config_ref: dict) -> dict:
    """Return an actual bounded source packet without writing or claiming authorship."""
    _, _, packet = _prepare(config_ref, {})
    return {"packet_sha256": pipeline.digest(packet), "packet_bytes": native._size(packet), "packet": packet}


def _views(packet):
    """In-memory projections of checked originals, never persisted extraction artifacts."""
    return [{"source": source["source"],
             "content": {"items": [row["item"] for row in packet["observations"].values()
                                   if row["binding"]["episode_id"] == eid]},
             "grounding": {"items": {rid: row["grounding"] for rid, row in packet["observations"].items()
                                     if row["binding"]["episode_id"] == eid}}}
            for eid, source in packet["source_context"].items()]


def _authored(submission, cfg, index, packet, pins):
    if submission["actor"] != cfg["actor"] or submission["packet_sha256"] != pipeline.digest(packet):
        raise ValueError("Authorship or source packet binding differs.")
    authored_at = native._time(submission["authored_at"])
    if any(native._time(r["authored_at"]) > authored_at for r in cfg["reports"]):
        raise ValueError("Reports must precede submission authorship.")
    reports = {(r["ref"]["path"], r["ref"]["sha256"]) for r in cfg["reports"]}
    views = _views(packet)
    grounding = pipeline._derive_synthesis(submission["content"], views)
    catalog = pipeline._catalog(views)
    reading = submission["reading"]
    inspected, pending = set(reading["inspected_evidence_ids"]), set(reading["pending_evidence_ids"])
    if inspected & pending or inspected | pending != catalog.keys():
        raise ValueError("Inspection must partition required packet items, not outside-scope items.")
    hypotheses = parent._unique(submission["content"]["hypotheses"], "hypothesis_id")
    anchors = parent._unique(submission["hypothesis_anchors"], "hypothesis_id")
    if hypotheses.keys() != anchors.keys():
        raise ValueError("Every authored hypothesis requires an anchored report binding.")
    groups, claims = [], {}
    basis = lambda ids: extension.base._basis_closure(index["evidence"], ids)
    for hid, h in hypotheses.items():
        ids = basis(h["supporting_evidence"] + h["counterevidence"])
        claims[hid] = ids
        groups.append((anchors[hid]["source_anchors"], ids))
        parent._report_refs(anchors[hid]["report_refs"], reports)
    coverage = parent._unique(submission["comparison_coverage"], "comparison_id")
    comparisons = parent._unique(submission["comparisons"], "comparison_id")
    if comparisons.keys() != {k for k, v in coverage.items() if v["status"] == "inspected"}:
        raise ValueError("Authored comparisons must match inspected tasks exactly.")
    for row in coverage.values():
        if not set(row["evidence_ids"]) <= catalog.keys():
            raise ValueError("Comparison task references outside the packet.")
    for cid, row in comparisons.items():
        endpoints = parent._unique(row["endpoints"], "evidence_id")
        if endpoints.keys() != set(coverage[cid]["evidence_ids"]) or any(
                e["evidence_kind"] != catalog[rid]["evidence_kind"] for rid, e in endpoints.items()):
            raise ValueError("Comparison endpoints/kinds differ from checked evidence.")
        if not set(row["hypothesis_ids"]) <= hypotheses.keys() or any(
                not endpoints.keys() <= claims[hid] for hid in row["hypothesis_ids"]):
            raise ValueError("Comparison references an unrelated authored hypothesis.")
        groups.append((row["source_anchors"], basis(endpoints)))
        parent._report_refs(row["report_refs"], reports)
    obligations = {r["obligation_key"]: r for r in packet["inherited_obligations"]}
    authored_ids = set().union(*(ids for _, ids in groups))
    if authored_ids:
        incident = extension.checked_index_closure(index, cfg["inventory"], sorted(authored_ids))
        incident_keys = {r["obligation_key"] for r in _obligations(index, incident)}
        if not incident_keys <= obligations.keys() or not set(incident["required_evidence_ids"]) <= catalog.keys():
            raise ValueError("Authored claims expand incident context; prepare an explicit larger workset.")
    dispositions = parent._unique(submission["inherited_dispositions"], "obligation_key")
    if not dispositions.keys() <= obligations.keys():
        raise ValueError("Unknown inherited obligation disposition.")
    for key, row in dispositions.items():
        groups.append((row["source_anchors"], set(obligations[key]["required_evidence_ids"])))
        parent._report_refs(row["report_refs"], reports)
    if any(not ids <= inspected for _, ids in groups):
        raise ValueError("Authored claims/dispositions require inspected references and inference bases.")
    parent._check_anchors(groups, catalog, index, pins)
    return grounding, [{"obligation_key": key, "disposition": "pending", "reason": "Not adjudicated by this author."}
                       if key not in dispositions else copy.deepcopy(dispositions[key]) for key in sorted(obligations)]


def _build(submission_ref, output, created_at, pins):
    submission_ref = extension._ref(submission_ref)
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
        "scope": copy.deepcopy(packet["scope"]), "inherited_inventory": copy.deepcopy(packet["inherited_inventory"]),
        "inherited_obligations": copy.deepcopy(packet["inherited_obligations"]),
        "inherited_dispositions": dispositions,
        "authorship": {k: copy.deepcopy(submission[k]) for k in ("actor", "authored_at", "method", "limitations")},
        "reports": copy.deepcopy(cfg["reports"]), "grounding": grounding,
        "authored": {k: copy.deepcopy(submission[k]) for k in
                     ("content", "hypothesis_anchors", "comparisons", "comparison_coverage", "reading")},
        "model_metadata": copy.deepcopy(native.MODEL_METADATA), "adoption": copy.deepcopy(native.ADOPTION),
        "policy": copy.deepcopy(POLICY)}
    result["fingerprint"] = pipeline.digest(result)
    _bounded(result, cfg["limits"]["max_candidate_bytes"], "Candidate")
    _finish(pins, packet["implementation"])
    return result, packet


def preview(submission_ref: dict, output: str | Path) -> dict:
    if private_path(output).exists():
        raise FileExistsError("Use a new private candidate path.")
    return _build(submission_ref, output, utc_now(), {})[0]


def apply(submission_ref: dict, output: str | Path) -> dict:
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


def read(ref: dict) -> dict:
    """Replay once and expose bounded source material plus exact full-inventory refs."""
    ref = extension._ref(ref)
    value = native._read(ref, {})
    if value.get("schema_version") != VERSION:
        raise ValueError("Expected an explicit synthesis extension candidate.")
    pins = {}
    expected, packet = _build(value["submission"], ref["path"], value["created_at"], pins)
    if parent.review._json(value) != parent.review._json(expected):
        raise ValueError("Synthesis extension differs from deterministic replay.")
    recovery._pin(pins, Path(ref["path"]), ref["sha256"])
    _finish(pins, expected["implementation"])
    return {"candidate": value, "packet": packet}


def load_checked(ref: dict) -> dict:
    return read(ref)["candidate"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="prepare",
                        choices=("prepare", "preview", "apply", "validate", "read", "schema"))
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
            value = preview(ref, args.output) if args.command == "preview" else load_checked(ref)
            result = {"status": "checked", "packet_sha256": value["packet_sha256"], "scope": value["scope"],
                "hypotheses": len(value["authored"]["content"]["hypotheses"]), "adoption": native.ADOPTION}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "adoption": native.ADOPTION}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
