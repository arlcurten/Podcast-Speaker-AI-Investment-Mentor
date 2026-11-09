"""Review-bound release descriptor, not a duplicate copy of the corpus."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

from transcription.common import atomic_write_json, private_path, read_json, sha256_file, utc_now
from . import case_release
from .pipeline import run, release_locations
from .schemas import array, enum, obj

COLLECTION_VERSION = "knowledge_release_collection.v1"
LEAF_VERSIONS = {"knowledge_release.v1", "knowledge_case_release.v1"}
COLLECTION_SCHEMA = obj(schema_version=enum(COLLECTION_VERSION), created_at=case_release.TEXT,
    scope=enum("reviewed_leaf_union"), leaves={**array(case_release.REF), "minItems": 1})


def _read_snapshot(path: Path) -> tuple[dict, str]:
    payload = path.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Release metadata and artifacts must be JSON objects.")
    return data, checksum


def _check_pins(pins: dict[Path, str]) -> None:
    for path, checksum in pins.items():
        try:
            actual = sha256_file(path)
        except OSError as exc:
            raise ValueError("Release dependency changed or disappeared during validation.") from exc
        if actual != checksum:
            raise ValueError("Release dependency changed during validation.")


def _merge_pins(pins, incoming):
    for path, checksum in incoming.items():
        path = private_path(path)
        if path in pins and pins[path] != checksum:
            raise ValueError("Release dependencies bind conflicting versions of the same path.")
        pins[path] = checksum


def _validate_review(review: dict) -> None:
    if review.get("schema_version") != "knowledge_review.v1" or review.get("decision") != "accept":
        raise ValueError("A knowledge_review.v1 acceptance is required; do not invent approval.")
    for key in ("reviewer", "method", "reviewed_at"):
        if not isinstance(review.get(key), str) or not review[key].strip():
            raise ValueError(f"Review requires {key}.")
    if review.get("unresolved_critical_issues") != []:
        raise ValueError("Unresolved semantic issues block publication.")


def publish(cfg: dict, review_path: Path, output: Path) -> dict:
    if cfg.get("extraction_mode", "reviewed") != "reviewed":
        raise ValueError("Candidate extraction cannot be published; source and semantic review are required.")
    run(cfg, validate_only=True)
    review_path, output = private_path(review_path), private_path(output)
    if output.exists():
        raise FileExistsError("Use a new release descriptor; existing releases are immutable.")
    review, review_sha = _read_snapshot(review_path)
    _validate_review(review)
    artifacts = []
    for location in release_locations(cfg):
        path = Path(location["path"])
        relative = location["logical_path"]
        checksum = sha256_file(path)
        if review.get("artifact_sha256", {}).get(relative) != checksum:
            raise ValueError("Review does not cover the exact artifact version.")
        artifacts.append({**location, "sha256": checksum})
    data = {"schema_version": "knowledge_release.v1", "created_at": utc_now(), "run_id": cfg["run_id"],
            "artifacts": artifacts, "review_path": str(review_path), "review_sha256": review_sha,
            "accepted_case_ids": review.get("accepted_case_ids"), "accepted_principle_ids": review.get("accepted_principle_ids"),
            "source_config": cfg, "evidence_authority": "level2"}
    # Validation is shared with the runtime; publication never bypasses its checks.
    load_release_payload(data)
    atomic_write_json(output, data, overwrite=False)
    return {"release": str(output), "sha256": sha256_file(output), "case_count": len(data["accepted_case_ids"]),
            "principle_count": len(data["accepted_principle_ids"])}


def load_release_payload(data: dict) -> tuple[dict, dict]:
    if data.get("schema_version") == COLLECTION_VERSION:
        return _load_collection_payload(data)
    cases, principles, _, _ = _load_leaf_payload(data)
    return cases, principles


def _load_leaf_payload(data, *, collect=False):
    if data.get("schema_version") == "knowledge_case_release.v1":
        cases, pins = case_release._validated_payload(data)
        versions = {c["episode_id"]: {k: c["scoped_review"][k] for k in
                    ("source_binding", "level1_sha256", "artifact_sha256")} for c in cases.values()} if collect else {}
        return cases, {}, pins, versions
    if data.get("source_config", {}).get("extraction_mode", "reviewed") != "reviewed":
        raise ValueError("Candidate artifacts cannot enter a serving release.")
    if data.get("schema_version") != "knowledge_release.v1" or data.get("evidence_authority") != "level2":
        raise ValueError("Unsupported knowledge release.")
    if data["run_id"] != data["source_config"]["run_id"]:
        raise ValueError("Release/run identity mismatch.")
    review_path = private_path(data["review_path"])
    review, review_sha = _read_snapshot(review_path)
    if review_sha != data["review_sha256"]:
        raise ValueError("Release review changed; publish a new reviewed release.")
    _validate_review(review)
    pins = {review_path: review_sha}
    # Revalidates raw/normalized/review hashes and every upstream stage.
    report = run(data["source_config"], validate_only=True, allow_pinned_prompts=True)
    if collect:
        _merge_pins(pins, {private_path(r["artifact_path"]): r["sha256"] for r in report["artifacts"]})
    expected = {p["path"]: p for p in release_locations(data["source_config"])}
    actual = [str(private_path(a["path"])) for a in data["artifacts"]]
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("Release artifacts do not exactly match the validated run configuration.")
    cases, principles, sources, versions = {}, {}, {}, {}
    for item in data["artifacts"]:
        path = private_path(item["path"])
        location = expected[str(path)]
        artifact, checksum = _read_snapshot(path)
        _merge_pins(pins, {path: checksum})
        if (checksum != item["sha256"] or item["stage"] != location["stage"]
                or item.get("logical_path", location["logical_path"]) != location["logical_path"]):
            raise ValueError("Release artifact changed or differs from its configured identity.")
        if review["artifact_sha256"].get(location["logical_path"]) != item["sha256"]:
            raise ValueError("Release artifact lacks exact review coverage.")
        if artifact.get("schema_version") != "knowledge.v2":
            raise ValueError("Only reviewed-mode knowledge artifacts may enter a release.")
        if item["stage"] != artifact["stage"]:
            raise ValueError("Release stage identity mismatch.")
        if item["stage"] == "level2":
            if collect:
                eid = artifact["content"]["episode_id"]
                sources[eid] = artifact["source"]
                versions[eid] = {"source_binding": artifact["source"], "artifact_sha256": checksum,
                    "level1_sha256": next(r["sha256"] for r in report["artifacts"] if r["path"] == f"{eid}/level1.json")}
            for row in artifact["content"]["cases"]:
                rid = row["record_id"]
                if rid in cases:
                    raise ValueError("Duplicate released record ID.")
                cases[rid] = {**row, "episode_id": artifact["content"]["episode_id"], "evidence": artifact["evidence"][rid],
                              "publication_date": artifact["source"]["publication_date"],
                              "knowledge_artifact_sha256": item["sha256"]}
        elif item["stage"] == "level4":
            for p in artifact["content"]["principles"]:
                pid = p["principle_id"]
                if pid in principles:
                    raise ValueError("Duplicate released principle ID.")
                principles[pid] = {**p, **artifact["qualification"][pid]}
        else:
            raise ValueError("Only Level 2 evidence and Level 4 abstractions may enter a release.")
    for key, available in (("accepted_case_ids", cases), ("accepted_principle_ids", principles)):
        selected = data[key]
        if (not isinstance(selected, list) or selected != review.get(key)
                or not all(isinstance(i, str) for i in selected) or len(selected) != len(set(selected))
                or any(i not in available for i in selected)):
            raise ValueError("Release selection is unreviewed or unresolved.")
    cases = {i: cases[i] for i in data["accepted_case_ids"]}
    principles = {i: principles[i] for i in data["accepted_principle_ids"]}
    if not cases:
        raise ValueError("A release needs at least one accepted case.")
    for p in principles.values():
        if not p["eligible_for_online_reasoning"] or p["runtime_mode_recommendation"] != "principle_assisted":
            raise ValueError("An ineligible principle cannot enter the online release.")
        refs = p["supporting_records"] + [x["record_id"] for x in p["counterexamples"] + p["contradictions"]]
        refs += [x["record_id"] for x in p["counterevidence_dispositions"]]
        if any(ref not in cases for ref in refs):
            raise ValueError("Release must include supporting AND counter/exception evidence, including context-excluded sources for review.")
    if collect:
        case_release._source_dependencies(data["source_config"], sources, pins)
        selected_episodes = {c["episode_id"] for c in cases.values()}
        versions = {eid: version for eid, version in versions.items() if eid in selected_episodes}
    _check_pins(pins)
    return cases, principles, pins, versions


def _load_collection_payload(data):
    if next(Draft202012Validator(COLLECTION_SCHEMA).iter_errors(data), None) is not None:
        raise ValueError("Reviewed leaf collection does not conform to its schema.")
    try:
        created = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("Missing timezone")
    except ValueError as exc:
        raise ValueError("Collection creation time requires an ISO timestamp with timezone.") from exc
    paths, hashes = set(), set()
    leaves = []
    for ref in data["leaves"]:
        path = private_path(ref["path"])
        if path in paths or ref["sha256"] in hashes:
            raise ValueError("Duplicate collection leaf path or hash.")
        paths.add(path)
        hashes.add(ref["sha256"])
        leaves.append((path, ref["sha256"]))
    cases, principles, versions, pins = {}, {}, {}, {}
    for path, expected in leaves:
        leaf, checksum = _read_snapshot(path)
        if checksum != expected:
            raise ValueError("Collection leaf descriptor changed.")
        if leaf.get("schema_version") not in LEAF_VERSIONS:
            raise ValueError("Collections require supported leaf releases; nesting is forbidden.")
        # Validate leaf-local support and counters before exposing any union evidence.
        leaf_cases, leaf_principles, dependencies, source_versions = _load_leaf_payload(leaf, collect=True)
        _merge_pins(pins, {path: checksum})
        _merge_pins(pins, dependencies)
        if cases.keys() & leaf_cases.keys() or principles.keys() & leaf_principles.keys():
            raise ValueError("Duplicate collection case or principle ID; no deduplication or override.")
        for eid, version in source_versions.items():
            if eid in versions and versions[eid] != version:
                raise ValueError("Collection episode source or artifact versions differ.")
            versions[eid] = version
        cases.update({rid: {**case, "leaf_release_sha256": checksum} for rid, case in leaf_cases.items()})
        principles.update(leaf_principles)
    # Earlier leaves can change while a later leaf is being replayed.
    _check_pins(pins)
    return cases, principles


def publish_collection(leaves: list[dict], output: Path) -> dict:
    """Publish only a validated union descriptor, never new evidence or approval."""
    output = private_path(output)
    if output.exists():
        raise FileExistsError("Use a new collection descriptor; existing releases are immutable.")
    data = {"schema_version": COLLECTION_VERSION, "created_at": utc_now(),
            "scope": "reviewed_leaf_union", "leaves": copy.deepcopy(leaves)}
    cases, principles = load_release_payload(data)
    data["leaves"] = [{**r, "path": str(private_path(r["path"]))} for r in data["leaves"]]
    atomic_write_json(output, data, overwrite=False)
    stored, checksum = _read_snapshot(output)
    if stored != data:
        raise ValueError("Published collection read-back differs.")
    load_release_payload(stored)
    _check_pins({output: checksum})
    return {"release": str(output), "sha256": checksum,
            "case_count": len(cases), "principle_count": len(principles)}


def load_release(path: Path) -> tuple[dict, dict]:
    path = private_path(path)
    data, checksum = _read_snapshot(path)
    result = load_release_payload(data)
    _check_pins({path: checksum})
    return result
