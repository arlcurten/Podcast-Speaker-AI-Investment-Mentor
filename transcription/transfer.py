"""Portable, checksum-bound transcription bundles; never copy audio or overwrite data."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .batch import (completed_output, load_checkpoint, resolve_paths, run_metadata_path,
                    transcript_path, check_compatibility)
from .common import atomic_write_json, child_path, private_path, read_json, safe_id, sha256_file, utc_now
from .quality import validate_transcript
from .storage import StorageGuard

MAX_BUNDLE_BYTES = 512_000_000
BATCH_FILES = {"batch_manifest.json", "checkpoint.json", "summary.json", "failures.json", "validation_summary.json"}


def _file(root: Path, relative: str) -> Path:
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts or str(rel) != relative:
        raise ValueError("Invalid bundle relative path.")
    path = child_path(root, *rel.parts)
    original = root.joinpath(*rel.parts)
    if any(p.is_symlink() for p in (original, *original.parents) if p != root.parent):
        raise ValueError("Symlinks are not allowed in transfer bundles.")
    return path


def _guard(settings: dict, target: Path) -> StorageGuard:
    return StorageGuard(settings, audio_root=target / "unused_audio", write_roots=[target],
                        budget_file=target.parent / "storage_budget.json")


def _copy(source: Path, target: Path, digest: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    if sha256_file(target) != digest:
        raise ValueError("Source changed during transfer; no bundle was published.")


def _publish(target: Path, entries: list[tuple[Path, str, str]], manifest: dict,
             settings: dict) -> None:
    guard = _guard(settings, target)
    guard.check(sum(path.stat().st_size for path, _, _ in entries) + 64_000_000)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.with_suffix(".transfer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if target.exists():
            raise FileExistsError("Destination exists; use a new bundle version.")
        staging = Path(tempfile.mkdtemp(prefix=".transfer-", dir=target.parent))
        try:
            for source, relative, digest in entries:
                guard.check(source.stat().st_size + 64_000_000)
                _copy(source, _file(staging, relative), digest)
            atomic_write_json(staging / "bundle_manifest.json", manifest, overwrite=False)
            verify_bundle(staging)
            os.rename(staging, target)
            fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def export_bundle(cfg: dict, destination: Path, *, execute: bool = False) -> dict:
    paths = resolve_paths(cfg)
    destination = private_path(destination)
    if any(destination.is_relative_to(p) or p.is_relative_to(destination)
           for p in (paths.transcript_root, paths.checkpoint_path.parent, paths.audio_root)):
        raise ValueError("Bundle destination must be separate from worker inputs and outputs.")
    if not paths.checkpoint_path.exists():
        raise ValueError("No checkpoint to export.")
    with paths.checkpoint_path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Stop the batch before exporting a consistent snapshot.") from exc
        checkpoint = load_checkpoint(paths.checkpoint_path)
        check_compatibility(cfg, checkpoint)
        files: list[tuple[Path, str]] = []
        batch_dir = child_path(paths.output_root, cfg["batch_id"])
        for name in sorted(BATCH_FILES):
            path = paths.checkpoint_path if name == "checkpoint.json" else batch_dir / name
            if path.exists():
                files.append((path, "batch/" + name))
            elif name != "validation_summary.json":
                raise ValueError(f"Missing batch metadata: {name}; resume/status validation first.")
        completed = []
        for eid, job in checkpoint["jobs"].items():
            safe_id(eid)
            if job.get("status") != "completed":
                continue
            if not completed_output(paths, eid, cfg):
                raise ValueError("Completed checkpoint has invalid transcript.")
            meta = read_json(transcript_path(paths, eid))["metadata"]
            if meta != read_json(run_metadata_path(paths, eid)):
                raise ValueError("Transcript/metadata sidecar mismatch.")
            stages = {s["stage"] for s in job.get("stage_history", [])}
            if not {"output_validated", "job_completed"} <= stages:
                raise ValueError("Missing durable completion stages.")
            completed.append(eid)
            files += [(transcript_path(paths, eid), f"transcripts/{eid}/raw_transcript.json"),
                      (run_metadata_path(paths, eid), f"transcripts/{eid}/raw_run_metadata.json")]
        entries = []
        descriptions = []
        for path, relative in files:
            if path.is_symlink() or not path.is_file():
                raise ValueError("Only regular worker JSON files may be exported.")
            read_json(path)
            digest = sha256_file(path)
            entries.append((path, relative, digest))
            descriptions.append({"path": relative, "sha256": digest, "size_bytes": path.stat().st_size})
        size = sum(row["size_bytes"] for row in descriptions)
        if size > MAX_BUNDLE_BYTES:
            raise ValueError("Bundle exceeds 512 MB safety limit; split the batch.")
        manifest = {"schema_version": "transcription_bundle.v1", "batch_id": cfg["batch_id"],
                    "created_at": utc_now(), "completed_episodes": sorted(completed), "files": descriptions,
                    "content_review_accepted": False, "audio_included": False}
        if execute:
            _publish(destination, entries, manifest, cfg.get("storage") or {})
    return {"dry_run": not execute, "batch_id": cfg["batch_id"], "completed_episodes": sorted(completed),
            "files": descriptions, "size_bytes": size, "audio_included": False, "output": str(destination)}


def verify_bundle(source: Path) -> dict:
    source = private_path(source)
    manifest = read_json(_file(source, "bundle_manifest.json"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "transcription_bundle.v1" or manifest.get("audio_included") is not False:
        raise ValueError("Unsupported transfer bundle.")
    safe_id(manifest["batch_id"])
    episodes = manifest["completed_episodes"]
    if not isinstance(episodes, list) or not all(isinstance(e, str) for e in episodes) or len(episodes) != len(set(episodes)):
        raise ValueError("Invalid bundle episode list.")
    if not isinstance(manifest.get("files"), list) or not all(isinstance(x, dict) for x in manifest["files"]):
        raise ValueError("Invalid bundle file list.")
    allowed = {"batch/" + name for name in BATCH_FILES}
    for eid in episodes:
        safe_id(eid)
        allowed |= {f"transcripts/{eid}/{name}" for name in ("raw_transcript.json", "raw_run_metadata.json")}
    seen = set()
    total = 0
    for item in manifest["files"]:
        relative = item["path"]
        if relative in seen or relative not in allowed:
            raise ValueError("Unexpected or duplicate bundle file; audio is never transferred.")
        seen.add(relative)
        path = _file(source, relative)
        if not path.is_file() or path.stat().st_size != item["size_bytes"] or sha256_file(path) != item["sha256"]:
            raise ValueError("Bundle checksum/size mismatch.")
        total += path.stat().st_size
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("Bundle exceeds size limit.")
        read_json(path)
    required = allowed - {"batch/validation_summary.json"}
    if not required <= seen:
        raise ValueError("Incomplete bundle.")
    checkpoint = load_checkpoint(_file(source, "batch/checkpoint.json"))
    if checkpoint.get("batch_id") != manifest["batch_id"]:
        raise ValueError("Bundle/checkpoint batch identity mismatch.")
    summary = read_json(_file(source, "batch/summary.json"))
    counts = {}
    for job in checkpoint["jobs"].values():
        state = job.get("status", "unknown")
        counts[state] = counts.get(state, 0) + 1
    if summary.get("batch_id") != manifest["batch_id"] or summary.get("status_counts") != counts:
        raise ValueError("Summary/checkpoint mismatch; resume the worker before export.")
    batch_manifest = read_json(_file(source, "batch/batch_manifest.json"))
    if batch_manifest.get("batch_id") != manifest["batch_id"] or batch_manifest.get("config_fingerprint") != checkpoint.get("config_fingerprint"):
        raise ValueError("Batch manifest/checkpoint mismatch.")
    failures = [job["last_error"] for job in checkpoint["jobs"].values() if job.get("last_error")]
    if read_json(_file(source, "batch/failures.json")) != failures:
        raise ValueError("Failures/checkpoint mismatch.")
    if sorted(episodes) != sorted(eid for eid, job in checkpoint["jobs"].items() if job.get("status") == "completed"):
        raise ValueError("Bundle/checkpoint completed episodes mismatch.")
    for eid in episodes:
        raw = _file(source, f"transcripts/{eid}/raw_transcript.json")
        validate_transcript(raw, eid)
        payload = read_json(raw)
        metadata = payload["metadata"]
        if payload.get("schema_version") != "transcription_raw.v1" or not metadata.get("audio_sha256"):
            raise ValueError("Missing formal transcript identity.")
        if metadata != read_json(_file(source, f"transcripts/{eid}/raw_run_metadata.json")):
            raise ValueError("Transcript/metadata sidecar mismatch.")
        if metadata.get("config_fingerprint") != checkpoint.get("config_fingerprint"):
            raise ValueError("Transcript/checkpoint configuration mismatch.")
        if metadata.get("source_episode_metadata", {}).get("episode_id") != eid:
            raise ValueError("Source episode identity mismatch.")
        stages = {s["stage"] for s in checkpoint["jobs"][eid].get("stage_history", [])}
        if not {"output_validated", "job_completed"} <= stages:
            raise ValueError("Missing durable completion stages.")
    return manifest


def import_bundle(source: Path, destination_root: Path, *, execute: bool = False,
                  storage: dict | None = None) -> dict:
    source, destination_root = private_path(source), private_path(destination_root)
    manifest = verify_bundle(source)
    target = child_path(destination_root, "pipeline_data", "transcription", "imports", manifest["batch_id"])
    if source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError("Transfer source and destination must be separate.")
    action = "copy"
    if target.exists():
        current = verify_bundle(target)
        if current != manifest:
            raise FileExistsError("Different local bundle already exists; it will not be overwritten.")
        action = "reuse_verified"
    elif execute:
        entries = [(_file(source, row["path"]), row["path"], row["sha256"]) for row in manifest["files"]]
        _publish(target, entries, manifest, storage or {})
    return {"dry_run": not execute, "action": action, "batch_id": manifest["batch_id"],
            "completed_episodes": manifest["completed_episodes"], "files": manifest["files"],
            "destination": str(target), "audio_included": False, "canonical_overwritten": False,
            "content_review_accepted": False}
