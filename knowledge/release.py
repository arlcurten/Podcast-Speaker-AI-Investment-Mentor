"""Review-bound release descriptor, not a duplicate copy of the corpus."""
from __future__ import annotations

from pathlib import Path

from transcription.common import atomic_write_json, private_path, read_json, sha256_file, utc_now
from .pipeline import run, release_locations


def publish(cfg: dict, review_path: Path, output: Path) -> dict:
    run(cfg, validate_only=True)
    review_path, output = private_path(review_path), private_path(output)
    if output.exists():
        raise FileExistsError("Use a new release descriptor; existing releases are immutable.")
    review = read_json(review_path)
    if review.get("schema_version") != "knowledge_review.v1" or review.get("decision") != "accept":
        raise ValueError("A knowledge_review.v1 acceptance is required; do not invent approval.")
    for key in ("reviewer", "method", "reviewed_at"):
        if not isinstance(review.get(key), str) or not review[key].strip():
            raise ValueError(f"Review requires {key}.")
    if review.get("unresolved_critical_issues") != []:
        raise ValueError("Unresolved semantic issues block publication.")
    artifacts = []
    for location in release_locations(cfg):
        path = Path(location["path"])
        relative = location["logical_path"]
        if review.get("artifact_sha256", {}).get(relative) != sha256_file(path):
            raise ValueError("Review does not cover the exact artifact version.")
        artifacts.append({**location, "sha256": sha256_file(path)})
    data = {"schema_version": "knowledge_release.v1", "created_at": utc_now(), "run_id": cfg["run_id"],
            "artifacts": artifacts, "review_path": str(review_path), "review_sha256": sha256_file(review_path),
            "accepted_case_ids": review.get("accepted_case_ids"), "accepted_principle_ids": review.get("accepted_principle_ids"),
            "source_config": cfg, "evidence_authority": "level2"}
    # Validation is shared with the runtime; publication never bypasses its checks.
    load_release_payload(data)
    atomic_write_json(output, data, overwrite=False)
    return {"release": str(output), "sha256": sha256_file(output), "case_count": len(data["accepted_case_ids"]),
            "principle_count": len(data["accepted_principle_ids"])}


def load_release_payload(data: dict) -> tuple[dict, dict]:
    if data.get("schema_version") != "knowledge_release.v1" or data.get("evidence_authority") != "level2":
        raise ValueError("Unsupported knowledge release.")
    if data["run_id"] != data["source_config"]["run_id"]:
        raise ValueError("Release/run identity mismatch.")
    review_path = private_path(data["review_path"])
    if sha256_file(review_path) != data["review_sha256"]:
        raise ValueError("Release review changed; publish a new reviewed release.")
    review = read_json(review_path)
    if review.get("decision") != "accept" or review.get("unresolved_critical_issues") != []:
        raise ValueError("Release acceptance is missing or revoked.")
    # Revalidates raw/normalized/review hashes and every upstream stage.
    run(data["source_config"], validate_only=True, allow_pinned_prompts=True)
    expected = {p["path"]: p for p in release_locations(data["source_config"])}
    actual = [str(private_path(a["path"])) for a in data["artifacts"]]
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("Release artifacts do not exactly match the validated run configuration.")
    cases, principles = {}, {}
    for item in data["artifacts"]:
        path = private_path(item["path"])
        location = expected[str(path)]
        if (sha256_file(path) != item["sha256"] or item["stage"] != location["stage"]
                or item.get("logical_path", location["logical_path"]) != location["logical_path"]):
            raise ValueError("Release artifact changed or differs from its configured identity.")
        if review["artifact_sha256"].get(location["logical_path"]) != item["sha256"]:
            raise ValueError("Release artifact lacks exact review coverage.")
        artifact = read_json(path)
        if item["stage"] != artifact["stage"]:
            raise ValueError("Release stage identity mismatch.")
        if item["stage"] == "level2":
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
    return cases, principles


def load_release(path: Path) -> tuple[dict, dict]:
    return load_release_payload(read_json(private_path(path)))
