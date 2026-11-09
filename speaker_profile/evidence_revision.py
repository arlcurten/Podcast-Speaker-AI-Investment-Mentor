"""Replayable, zero-call evidence edits; never original model output or approval.

Use prepare(config_ref, output) for a read-only preview, apply(config_ref, output)
for a new immutable private artifact, and load_checked(artifact_ref) for replay.
References contain absolute private path and SHA-256. Configs use CONFIG_SCHEMA:
one completed (manifest + artifact) or recovered parent, reviewer/rationale, and
item-relative edits such as /merged_ids or /uncertainty/0 with exact old/new
values, evidence_id guards, reasons and exact raw source_anchors.

Original quotes must remain in order; original model notes may only be extended
with reviewer notes. Identity, attribution, bases, classification, confidence,
outcomes, source holds and eligibility are not editable. All other content stays
unchanged. Grounding and primary overlap are recomputed, not copied. Exact anchors
establish mapping, not semantic correctness of reviewer prose. Parent fingerprints
retain their historical meaning. Derivative replay strictly pins this validator's
code; code upgrades require an explicit new version, not silent reinterpretation.
This schema is intentionally not an implicit pipeline/synthesis input.

CLI: python -m speaker_profile.evidence_revision apply --config PATH --sha256 HASH
     --output NEW_PATH; validate uses the same arguments without writing, or
     --artifact PATH --sha256 HASH for independent derivative replay.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from knowledge.schemas import TEXT, array, obj
from transcription.annotations import _reference
from transcription.common import atomic_write_json, child_path, private_path, sha256_file
from . import pipeline, recover_evidence, review, schemas

VERSION = "profile_evidence_revision.v1"
CONFIG_VERSION = "profile_evidence_revision_config.v1"
AUTHORITY = "reviewer_editorial_revision_not_source_evidence"
NOTE_AUTHORITY = "reviewer_commentary_not_speaker"
LIST_FIELDS = {"conditions", "exceptions", "uncertainty", "alternative_explanations"}
CONFIG_SCHEMA = obj(
    schema_version={"const": CONFIG_VERSION}, commentary_scope={"const": schemas.EPISODE_CONTEXT},
    parent={"oneOf": [obj(kind={"const": "recovered"}, artifact=review.PIN),
                          obj(kind={"const": "completed"}, artifact=review.PIN, manifest=review.PIN)]},
    reviewer=TEXT, rationale=TEXT,
    edits=array(obj(evidence_id=TEXT, field=TEXT, expected={}, value={}, reason=TEXT,
                    source_anchors={**array(schemas.QUOTE, 1), "uniqueItems": True}), 1))


def _implementation():
    return {"profile": pipeline._implementation(),
            "revision_code_sha256": sha256_file(Path(__file__)),
            "recovery_code_sha256": sha256_file(Path(recover_evidence.__file__)),
            "review_code_sha256": sha256_file(Path(review.__file__))}


def _read(ref, pins):
    # Use the strict editorial reader (duplicate keys/nonfinite values rejected).
    value, pin = review._read(ref["path"], {}, ref["sha256"])
    recover_evidence._pin(pins, Path(pin["path"]), pin["sha256"])
    return value


def _sources(cfg, pins):
    for entry in cfg["episodes"]:
        for name in ("raw", "draft", "source_review"):
            if entry[name] is not None:
                recover_evidence._pin(pins, Path(entry[name]), entry[name + "_sha256"])
        if entry["source_review"] is not None:
            path = Path(entry["source_review"])
            record = _read({"path": str(path), "sha256": entry["source_review_sha256"]}, pins)
            if record.get("schema_version") == "transcript_review.v2":
                path, checksum = _reference(record["annotations"], path)
                notes = _read({"path": str(path), "sha256": checksum}, pins)
                for ref in notes["supporting_reviews"]:
                    other, checksum = _reference(ref, path)
                    recover_evidence._pin(pins, other, checksum)
    if "catalog" in cfg:
        _read(cfg["catalog"], pins)
    recover_evidence._pin(pins, pipeline.common.TERMINOLOGY, sha256_file(pipeline.common.TERMINOLOGY))
    return pipeline._sources(cfg)


def _parent(config, pins, output):
    ref = config["parent"]
    if ref["kind"] == "recovered":
        parent = recover_evidence.load_checked(ref["artifact"], pins)
        manifest_ref = parent["recovery_config"]["manifest"]
    else:
        parent = _read(ref["artifact"], pins)
        manifest_ref = ref["manifest"]
    manifest = _read(manifest_ref, pins)
    cfg = pipeline._config(manifest["config"])
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    sources = _sources(cfg, pins)
    eid = parent["source"]["episode_id"]
    source = sources[eid]
    if output.is_relative_to(root):
        raise ValueError("Revision output must be outside the original run.")
    if ref["kind"] == "completed":
        # This is a read-only reuse check, not a new run or a relabeled old manifest.
        check_cfg = {**cfg, "run_id": output.name, "output_root": str(output.parent),
                     "reuse_evidence": {"manifest": manifest_ref, "artifacts": {eid: ref["artifact"]}}}
        reuse_pins = {}
        checked = pipeline._reused_evidence(check_cfg, sources, reuse_pins)[eid]
        for path, checksum in reuse_pins.items():
            recover_evidence._pin(pins, path, checksum)
        if checked != parent:
            raise ValueError("Completed parent changed during validation.")
        request_path = None
        for attempt in range(1, cfg["retry_count"] + 2):
            path = child_path(root, "calls", f"{eid}_evidence_{attempt}.request.json")
            if path.exists() and sha256_file(path) == parent["request_sha256"]:
                request_path = path
                break
        previous = None
        for number in range(1, attempt + 1):
            req_path = child_path(root, "calls", f"{eid}_evidence_{number}.request.json")
            res_path = req_path.with_name(req_path.name.replace(".request.json", ".result.json"))
            req = _read({"path": str(req_path), "sha256": sha256_file(req_path)}, pins)
            res = _read({"path": str(res_path), "sha256": sha256_file(res_path)}, pins)
            expected = pipeline._repair_context(pipeline._context(
                "evidence", source, [], commentary_scope=cfg.get("commentary_scope")), previous)
            if (request_path is None or req.get("context") != expected
                    or req.get("fingerprint") != parent["fingerprint"]
                    or res.get("fingerprint") != parent["fingerprint"]
                    or res.get("request_sha256") != sha256_file(req_path)
                    or req.get("attempt") != number or req.get("episode_id") != eid
                    or req.get("stage") != "evidence"
                    or (number < attempt and res.get("status") != "failed")):
                raise ValueError("Completed parent's original repair chain differs.")
            previous = res
    if parent["source"] != pipeline._meta(source) or parent["policy"] != pipeline.POLICY:
        raise ValueError("Parent source or quarantine policy differs.")
    return parent, source, manifest_ref


def _target(item, field):
    parts = field.split("/")[1:]
    if field.startswith("/") and len(parts) == 1 and parts[0] in LIST_FIELDS | {
            "merged_ids", "quotes", "source_observation", "supplementary_notes"}:
        return item, parts[0], parts
    if (field.startswith("/") and len(parts) == 2 and parts[0] in LIST_FIELDS
            and re.fullmatch(r"0|[1-9][0-9]*", parts[1]) and int(parts[1]) < len(item[parts[0]])):
        return item[parts[0]], int(parts[1]), parts
    raise ValueError("Evidence revision field is not allowed or index is absent.")


def _anchors(edit, old, new, source):
    name = edit["field"].split("/")[1]
    mids = set(new["merged_ids"])
    required = []
    if name == "merged_ids":
        mids |= set(old["merged_ids"])
        required = [{mid} for mid in set(old["merged_ids"]) ^ set(new["merged_ids"])]
    elif name == "supplementary_notes":
        original, notes = old[name], new[name]
        if notes[:len(original)] != original or len(notes) <= len(original):
            raise ValueError("Preserve original model notes; only append reviewer notes.")
        required = [set(n["merged_ids"]) for n in notes[len(original):]]
        mids = set().union(*required)
    elif name == "quotes":
        original = iter(old[name])
        wanted = next(original, None)
        for quote in new[name]:
            if quote == wanted:
                wanted = next(original, None)
        if wanted is not None:
            raise ValueError("Preserve every original quote in order when adding quotes.")
        if any(q not in edit["source_anchors"] for q in new[name] if q not in old[name]):
            raise ValueError("Every added quote needs its exact source anchor.")
    anchored_mids = set()
    for anchor in edit["source_anchors"]:
        raw = source["raw_segments"].get(anchor["segment_id"])
        matches = {s["merged_id"] for s in source["segments"]
                   if anchor["segment_id"] in s["source_segment_ids"]}
        if (type(anchor["segment_id"]) is not int or not matches & mids or raw is None
                or any(anchor[k] != raw[k] for k in ("text", "start", "end"))):
            raise ValueError("Editorial anchor must exactly match the relevant item's same-episode source region.")
        anchored_mids.update(matches)
    if any(not region & anchored_mids for region in required):
        raise ValueError("Each changed primary region or appended note requires a relevant exact source anchor.")


def _accounting(content):
    observations = [r for r in content["items"] if r["evidence_kind"] != "inferred_tendency"]
    groups = []
    for row in observations:
        mids, ids = set(row["merged_ids"]), {row["evidence_id"]}
        matched = [g for g in groups if g[0] & mids]
        groups = [g for g in groups if not g[0] & mids]
        groups.append((mids.union(*(g[0] for g in matched)), ids.union(*(g[1] for g in matched))))
    return {"item_count": len(content["items"]), "direct_action_count": len(observations),
            "primary_region_count": len({m for r in content["items"] for m in r["merged_ids"]}),
            "direct_action_overlap_components": sorted(sorted(g[1]) for g in groups),
            "independent_event_count": None,
            "scope": "Primary overlap only; commentary and inferred tendencies are not independent evidence."}


def _replay(config_ref, output, pins):
    current = _implementation()
    config = _read(config_ref, pins)
    if next(Draft202012Validator(CONFIG_SCHEMA).iter_errors(config), None) is not None:
        raise ValueError("Invalid profile_evidence_revision_config.v1 configuration.")
    parent, source, manifest_ref = _parent(config, pins, output)
    content = copy.deepcopy(parent["content"])
    rows = {r["evidence_id"]: (i, r) for i, r in enumerate(content["items"])}
    seen, operations = {}, []
    for number, edit in enumerate(config["edits"]):
        if edit["evidence_id"] not in rows:
            raise ValueError("Unknown evidence ID; revisions cannot add items.")
        index, row = rows[edit["evidence_id"]]
        container, key, parts = _target(row, edit["field"])
        for previous in seen.setdefault(index, []):
            if parts[:len(previous)] == previous or previous[:len(parts)] == parts:
                raise ValueError("Conflicting or duplicate editorial paths.")
        seen[index].append(parts)
        if review._json(container[key]) != review._json(edit["expected"]):
            raise ValueError("Expected old value differs from the pinned parent.")
        if review._json(edit["expected"]) == review._json(edit["value"]):
            raise ValueError("Editorial edits must change the selected field.")
        container[key] = copy.deepcopy(edit["value"])
        operations.append({"path": f"/content/items/{index}" + edit["field"],
                           "edit_index": number, "authority": AUTHORITY})
    grounding = pipeline._derive_evidence(content, source, schemas.EPISODE_CONTEXT)
    for edit in config["edits"]:
        index, row = rows[edit["evidence_id"]]
        _anchors(edit, parent["content"]["items"][index], row, source)
    for index, row in enumerate(content["items"]):
        original = parent["content"]["items"][index]["supplementary_notes"]
        item_grounding = grounding["items"][row["evidence_id"]]
        for n, note in enumerate(item_grounding["supplementary_context"]):
            if n >= len(original):
                note["authority"] = NOTE_AUTHORITY
                note["authorship"] = {"reviewer": config["reviewer"],
                                      "path": f"/content/items/{index}/supplementary_notes/{n}"}
        if len(row["supplementary_notes"]) > len(original):
            item_grounding["supplementary_notes_authority"] = (
                "mixed_model_and_reviewer_commentary_not_speaker" if original else NOTE_AUTHORITY)
    result = {"schema_version": VERSION, "stage": "evidence", "status": "editorial_candidate",
              "authority": AUTHORITY, "commentary_scope": schemas.EPISODE_CONTEXT,
              "config": copy.deepcopy(config_ref), "parent": copy.deepcopy(config["parent"]),
              "parent_manifest": manifest_ref, "output_path": str(output), "implementation": current,
              "source": copy.deepcopy(parent["source"]), "policy": copy.deepcopy(parent["policy"]),
              "content": content, "grounding": grounding,
              "accounting": {"before": _accounting(parent["content"]), "after": _accounting(content)},
              "editorial_review": {"reviewer": config["reviewer"], "rationale": config["rationale"],
                                   "edits": copy.deepcopy(config["edits"]), "operations": operations},
              "additional_model_calls": 0, "model_usage": None}
    result["fingerprint"] = pipeline.digest(result)
    if output in pins or _implementation() != current:
        raise ValueError("Revision output overlaps an input or implementation changed during replay.")
    recover_evidence._recheck(pins)
    return result


def prepare(config_ref: dict, output: str | Path) -> dict:
    """Validate/replay a pinned config in memory, without writes or model calls."""
    return _replay(recover_evidence._ref(config_ref), private_path(output), {})


def apply(config_ref: dict, output: str | Path) -> dict:
    """Create one new immutable private derivative; never overwrite any parent."""
    path = private_path(output)
    if path.exists():
        raise FileExistsError("Revision output already exists; choose a new version.")
    result = prepare(config_ref, path)
    if private_path(output) != path:
        raise ValueError("Revision output path changed during replay.")
    atomic_write_json(path, result, overwrite=False)
    ref = {"path": str(path), "sha256": sha256_file(path)}
    if load_checked(ref) != result:
        raise ValueError("Revision read-back differs; preserve and inspect the derivative.")
    return ref


def load_checked(ref: dict, pins: dict | None = None) -> dict:
    """Independently replay all edits, source checks, authorship and code identity."""
    pins = {} if pins is None else pins
    ref = recover_evidence._ref(ref)
    artifact = _read(ref, pins)
    if not isinstance(artifact, dict) or artifact.get("schema_version") != VERSION:
        raise ValueError("Expected an explicit evidence editorial derivative.")
    # The output itself is pinned, but is not an input of its own replay.
    replay_pins = {p: sha for p, sha in pins.items() if p != Path(ref["path"])}
    replayed = _replay(artifact["config"], Path(ref["path"]), replay_pins)
    if review._json(replayed) != review._json(artifact):
        raise ValueError("Evidence revision differs from independently replayed output.")
    for path, checksum in replay_pins.items():
        recover_evidence._pin(pins, path, checksum)
    recover_evidence._recheck(pins)
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apply", "validate"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", type=Path)
    group.add_argument("--artifact", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if (args.config is not None) != (args.output is not None) or (args.command == "apply" and args.config is None):
        parser.error("Config replay/apply needs --output; artifact validation does not.")
    try:
        ref = {"path": str(args.config or args.artifact), "sha256": args.sha256}
        if args.config is not None:
            if args.command == "apply":
                ref = apply(ref, args.output)
            else:
                prepare(ref, args.output)
        else:
            load_checked(ref)
        print(json.dumps({"status": "created" if args.command == "apply" else "validated",
                          "schema_version": VERSION, "authority": AUTHORITY, "additional_model_calls": 0}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "note": "Inspect the private revision configuration and pinned parent lineage."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
