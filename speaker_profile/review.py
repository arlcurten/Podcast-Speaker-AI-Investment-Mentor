"""No-inference editorial revisions, never source evidence or profile approval.

apply(proposal, output) creates a new private JSON file; validate(proposal, output)
independently replays it. All paths are absolute private paths. Edit fields are
JSON pointers relative to one hypothesis, e.g. /label or /limitations/0.
Validation establishes provenance/structure, not semantic correctness of prose.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from knowledge import schemas as knowledge_schemas
from knowledge.schemas import TEXT, array, obj
from transcription.common import atomic_write_json, child_path, private_path, sha256_file
from . import pipeline
from .schemas import QUOTE

AUTHORITY = "agent_editorial_revision_not_source_evidence"
ARRAY_FIELDS = {"conditions", "exceptions", "switch_conditions", "alternative_explanations",
                "falsification_conditions", "limitations"}
PIN = obj(path=TEXT, sha256={"type": "string", "pattern": "^[a-f0-9]{64}$"})
PROPOSAL_SCHEMA = obj(
    schema_version={"const": "profile_synthesis_revision.v1"}, parent_config=PIN, parent_artifact=PIN,
    reviewer=TEXT, method=TEXT, rationale=TEXT,
    edits=array(obj(hypothesis_id=TEXT, field=TEXT, expected={}, value={}, reason=TEXT,
                    evidence_ids={**array(TEXT, 1), "uniqueItems": True},
                    source_anchors={**array(obj(episode_id=TEXT, **QUOTE["properties"]), 1),
                                    "uniqueItems": True}), 1))


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key.")
        result[key] = value
    return result


def _read(path, pins, checksum=None):
    resolved = private_path(path)
    payload = resolved.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if checksum is not None and checksum != actual:
        raise ValueError("Pinned revision input hash differs.")
    pin = {"path": str(resolved), "sha256": actual}
    if str(path) in pins and pins[str(path)] != pin:
        raise ValueError("Revision input changed during replay.")
    pins[str(path)] = pin
    value = json.loads(payload, object_pairs_hook=_object)
    _json(value)  # Reject non-finite JSON numbers, including in expected/value.
    return value, pin


def _implementation():
    result = pipeline._implementation()
    result["code"].update(profile_review=sha256_file(Path(__file__)),
                          knowledge_schemas=sha256_file(Path(knowledge_schemas.__file__)))
    return result


def _target(hypothesis, field):
    parts = field.split("/")[1:]
    if not field.startswith("/") or not parts:
        raise ValueError("Use a hypothesis-relative JSON pointer beginning with /.")
    name = parts[0]
    if len(parts) == 1 and name in {"label", "conditional_description"} | ARRAY_FIELDS:
        return hypothesis, name, parts
    if (len(parts) == 2 and name in ARRAY_FIELDS
            and re.fullmatch(r"0|[1-9][0-9]*", parts[1])):
        index = int(parts[1])
        if index < len(hypothesis[name]):
            return hypothesis[name], index, parts
    if (len(parts) == 3 and name == "statement_action_relationships"
            and re.fullmatch(r"0|[1-9][0-9]*", parts[1]) and parts[2] in {"reason", "relationship"}):
        index = int(parts[1])
        if index < len(hypothesis[name]):
            return hypothesis[name][index], parts[2], parts
    raise ValueError("Field is outside the explicit editorial allowlist or index is absent.")


def _anchors(edit, hypothesis, catalog, sources):
    refs = set(edit["evidence_ids"])
    if not refs <= set(hypothesis["supporting_evidence"] + hypothesis["counterevidence"]):
        raise ValueError("Editorial evidence must already belong to this hypothesis.")
    regions = {}
    for rid in refs:
        row = catalog[rid]
        source = sources[row["episode_id"]]
        regions[rid] = {(row["episode_id"], sid) for segment in source["segments"]
                        if segment["merged_id"] in row["merged_ids"] for sid in segment["source_segment_ids"]}
    covered = set()
    for anchor in edit["source_anchors"]:
        if type(anchor["segment_id"]) is not int:
            raise ValueError("Raw segment IDs must be integers.")
        key = (anchor["episode_id"], anchor["segment_id"])
        matches = {rid for rid, region in regions.items() if key in region}
        raw = sources.get(key[0], {}).get("raw_segments", {}).get(key[1])
        if not matches or raw is None or any(anchor[k] != raw[k] for k in ("start", "end", "text")):
            raise ValueError("Anchor must exactly match raw text/time in the cited evidence's source region.")
        covered.update(matches)
    if covered != refs:
        raise ValueError("Every cited evidence ID needs an exact raw source anchor.")


def _edited_content(proposal, parent, upstream, sources):
    """Pure editorial replay shared by strict and historical validation."""
    if _json(parent["grounding"]) != _json(pipeline._derive("synthesis", parent["content"], None, upstream)):
        raise ValueError("Parent grounding changed during replay.")
    content = copy.deepcopy(parent["content"])
    hypotheses = {h["hypothesis_id"]: h for h in content["hypotheses"]}
    catalog, seen = pipeline._catalog(upstream), {}
    for edit in proposal["edits"]:
        hid = edit["hypothesis_id"]
        if hid not in hypotheses:
            raise ValueError("Unknown hypothesis ID.")
        hypothesis = hypotheses[hid]
        container, key, parts = _target(hypothesis, edit["field"])
        for previous in seen.setdefault(hid, []):
            if parts[:len(previous)] == previous or previous[:len(parts)] == parts:
                raise ValueError("Duplicate or conflicting editorial paths.")
        seen[hid].append(parts)
        if _json(container[key]) != _json(edit["expected"]):
            raise ValueError("Expected old value does not exactly match the parent.")
        if parts[0] == "statement_action_relationships" and key == "relationship" and edit["value"] != "unresolved":
            raise ValueError("Relationship revisions may only downgrade to unresolved.")
        _anchors(edit, hypothesis, catalog, sources)
        container[key] = copy.deepcopy(edit["value"])
    grounding = pipeline._derive("synthesis", content, None, upstream)
    if _json(content["evidence_accounting"]) != _json(parent["content"]["evidence_accounting"]):
        raise ValueError("Editorial revisions cannot change evidence accounting.")
    return content, grounding


def _replay(proposal_path, output_path):
    pins = {}
    implementation = _implementation()
    proposal, proposal_pin = _read(proposal_path, pins)
    if next(Draft202012Validator(PROPOSAL_SCHEMA).iter_errors(proposal), None) is not None:
        raise ValueError("Invalid profile_synthesis_revision.v1 proposal.")
    config, config_pin = _read(proposal["parent_config"]["path"], pins, proposal["parent_config"]["sha256"])
    cfg = pipeline._config(config)
    parent, parent_pin = _read(proposal["parent_artifact"]["path"], pins, proposal["parent_artifact"]["sha256"])
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    if Path(parent_pin["path"]) != child_path(root, "synthesis.json"):
        raise ValueError("Parent synthesis does not belong to the pinned configuration.")
    if output_path.is_relative_to(root):
        raise ValueError("Editorial output must be separate from the parent run.")
    pipeline.run(cfg, through="synthesis", validate_only=True)
    _, manifest_pin = _read(root / "run_manifest.json", pins)
    sources = pipeline._sources(cfg)
    upstream, evidence_pins = [], {}
    for entry in cfg["episodes"]:
        eid = entry["episode_id"]
        ref = (cfg.get("reuse_recovered_evidence", {}).get(eid)
               or cfg.get("reuse_evidence", {}).get("artifacts", {}).get(eid))
        path = ref["path"] if ref else child_path(root, eid, "evidence.json")
        artifact, pin = _read(path, pins, ref["sha256"] if ref else None)
        upstream.append(artifact)
        evidence_pins[eid] = pin
    content, grounding = _edited_content(proposal, parent, upstream, sources)
    result = {"schema_version": "profile_reviewed_revision.v1", "authority": AUTHORITY,
              "additional_model_calls": 0, "model_usage": None, "output_path": str(output_path),
              "parent_config": config_pin, "parent_artifact": parent_pin, "parent_manifest": manifest_pin,
              "proposal": proposal_pin, "implementation": implementation, "evidence": evidence_pins,
              "editorial_review": {k: copy.deepcopy(proposal[k]) for k in ("reviewer", "method", "rationale", "edits")},
              "source": copy.deepcopy(parent["source"]), "content": content, "grounding": grounding,
              "policy": copy.deepcopy(parent["policy"])}
    # Recheck original provenance after reconstruction, without creating calls or artifacts.
    pipeline.run(cfg, through="synthesis", validate_only=True)
    if pipeline._config(config) != cfg or pipeline._sources(cfg) != sources or _implementation() != implementation:
        raise ValueError("Revision sources, paths or implementation changed during replay.")
    for original, pin in pins.items():
        if str(private_path(original)) != pin["path"] or sha256_file(Path(pin["path"])) != pin["sha256"]:
            raise ValueError("Revision input hash or path changed during replay.")
    return result


def _historical_completed(ref, evidence_pins, pins):
    """Inventory-only replay after its completed-parent call-chain validation.

    The caller supplies the verified parent's evidence refs and dependency pins.
    Historical implementation identifies the original revision, not this validator;
    the inventory separately pins today's implementation. This never resumes a run.
    """
    from . import evidence_revision, recover_evidence

    current = _implementation()
    ref = recover_evidence._ref(ref)
    stored = evidence_revision._read(ref, pins)
    if not isinstance(stored, dict) or stored.get("schema_version") != "profile_reviewed_revision.v1":
        raise ValueError("Expected a completed-parent editorial revision.")
    recorded = stored.get("implementation")
    recover_evidence._profile_lineage(recorded, current)
    proposal = evidence_revision._read(stored["proposal"], pins)
    if next(Draft202012Validator(PROPOSAL_SCHEMA).iter_errors(proposal), None) is not None:
        raise ValueError("Invalid profile_synthesis_revision.v1 proposal.")
    config = evidence_revision._read(proposal["parent_config"], pins)
    cfg = pipeline._config(config)
    parent = evidence_revision._read(proposal["parent_artifact"], pins)
    manifest = evidence_revision._read(stored["parent_manifest"], pins)
    root = child_path(Path(cfg["output_root"]), cfg["run_id"])
    path = private_path(ref["path"])
    if (private_path(proposal["parent_artifact"]["path"]) != root / "synthesis.json"
            or private_path(stored["parent_manifest"]["path"]) != root / "run_manifest.json"
            or manifest.get("schema_version") != "profile_run.v1" or manifest.get("config") != cfg
            or manifest.get("config_digest") != pipeline.digest(cfg) or manifest.get("policy") != pipeline.POLICY
            or path.is_relative_to(root)):
        raise ValueError("Historical editorial parent manifest/configuration differs.")
    if evidence_pins != stored.get("evidence") or set(evidence_pins) != {e["episode_id"] for e in cfg["episodes"]}:
        raise ValueError("Historical editorial evidence differs from its checked parent.")
    sources = evidence_revision._sources(cfg, pins)
    upstream = [evidence_revision._read(evidence_pins[e["episode_id"]], pins) for e in cfg["episodes"]]
    content, grounding = _edited_content(proposal, parent, upstream, sources)
    expected = {"schema_version": "profile_reviewed_revision.v1", "authority": AUTHORITY,
                "additional_model_calls": 0, "model_usage": None, "output_path": str(path),
                "parent_config": proposal["parent_config"], "parent_artifact": proposal["parent_artifact"],
                "parent_manifest": stored["parent_manifest"], "proposal": stored["proposal"],
                "implementation": recorded, "evidence": evidence_pins,
                "editorial_review": {k: copy.deepcopy(proposal[k]) for k in ("reviewer", "method", "rationale", "edits")},
                "source": copy.deepcopy(parent["source"]), "content": content, "grounding": grounding,
                "policy": copy.deepcopy(parent["policy"])}
    if _json(stored) != _json(expected):
        raise ValueError("Historical editorial output differs from independently replayed revision.")
    if pipeline._sources(cfg) != sources or _implementation() != current or private_path(ref["path"]) != path:
        raise ValueError("Historical editorial inputs or validator changed during replay.")
    recover_evidence._recheck(pins)
    return stored


def apply(proposal: str | Path, output: str | Path) -> dict:
    """Write a separate immutable private editorial revision; never run inference."""
    path = private_path(output)
    if path.exists():
        raise FileExistsError("Editorial output already exists; choose a new version.")
    result = _replay(proposal, path)
    if private_path(output) != path:
        raise ValueError("Editorial output path changed during replay.")
    atomic_write_json(path, result, overwrite=False)
    return result


def validate(proposal: str | Path, output: str | Path) -> dict:
    """Read-only independent replay, including proposal, parent and code pins."""
    path = private_path(output)
    stored, pin = _read(output, {})
    replayed = _replay(proposal, path)
    if (_json(stored) != _json(replayed) or private_path(output) != path
            or sha256_file(path) != pin["sha256"]):
        raise ValueError("Editorial output differs from independently replayed revision.")
    return replayed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apply", "validate"))
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        {"apply": apply, "validate": validate}[args.command](args.proposal, args.output)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "note": "Inspect the private proposal and pinned parent artifacts."}))
        return 2
    print(json.dumps({"status": "created" if args.command == "apply" else "validated",
                      "schema_version": "profile_reviewed_revision.v1", "authority": AUTHORITY,
                      "additional_model_calls": 0, "model_usage": None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
