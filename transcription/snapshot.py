"""Versioned, checksum-verified private JSON snapshots; no canonical restore writes."""
from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile

from .common import atomic_write_json, private_path, read_json, sha256_file, utc_now
from .storage import StorageGuard

MAX_BYTES = 2_000_000_000
MAX_FILE_BYTES = 32_000_000
ROLES = {"raw", "rejected", "metadata"}


def source_path(root: Path, relative: str) -> Path:
    rel = PurePosixPath(relative)
    if (not isinstance(relative, str) or rel.is_absolute() or not rel.parts
            or str(rel) != relative or any(p in {"..", ".git"} for p in rel.parts)
            or "\\" in relative or rel.suffix != ".json"
            or any(s in rel.name.lower() for s in ("credential", "secret", "token"))):
        raise ValueError("Snapshot entries must be safe relative JSON data paths.")
    path = root
    for part in rel.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Snapshot paths must not contain symlinks.")
    return private_path(path)


def checked_entries(document: dict) -> list[dict]:
    if not isinstance(document, dict):
        raise ValueError("Snapshot manifest must be an object.")
    files = document.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 20_000:
        raise ValueError("Snapshot requires 1-20000 explicit files.")
    paths, active = set(), set()
    total = 0
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("Invalid snapshot entry.")
        if row["path"] in paths or row.get("role") not in ROLES:
            raise ValueError("Duplicate snapshot path or unknown role.")
        paths.add(row["path"])
        digest = row.get("sha256", "")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Every snapshot entry requires an expected SHA-256.")
        if type(row.get("size_bytes")) is not int or not 0 < row["size_bytes"] <= MAX_FILE_BYTES:
            raise ValueError("Invalid or oversized snapshot JSON file.")
        if type(row.get("active", False)) is not bool:
            raise ValueError("Active source marker must be boolean.")
        if row.get("active"):
            eid = row.get("episode_id")
            if row["role"] != "raw" or not isinstance(eid, str) or not eid or eid in active:
                raise ValueError("Use exactly one explicitly selected raw version per active episode.")
            active.add(eid)
        total += row["size_bytes"]
    if total > MAX_BYTES:
        raise ValueError("Snapshot exceeds 2 GB safety cap; split the selection.")
    return files


def check_sources(root: Path, files: list[dict]) -> None:
    for row in files:
        path = source_path(root, row["path"])
        if not path.is_file() or path.stat().st_size != row["size_bytes"] or sha256_file(path) != row["sha256"]:
            raise ValueError(f"Source missing or changed: {row['path']}")


def _reserve(output: Path, size: int, storage: dict | None) -> None:
    guard = StorageGuard(storage or {}, audio_root=output.parent / "unused_audio",
                         write_roots=[output.parent], budget_file=output.parent / "snapshot_storage_budget.json")
    guard.check(size + 64_000_000)


def verify_snapshot(snapshot: Path) -> dict:
    snapshot = private_path(snapshot)
    if (snapshot / "checksums.json").is_symlink():
        raise ValueError("Snapshot receipt must not be a symlink.")
    receipts = read_json(snapshot / "checksums.json")
    if not isinstance(receipts, dict) or set(receipts) != {"manifest.json", "artifacts.zip"}:
        raise ValueError("Invalid snapshot checksum receipt.")
    for name, digest in receipts.items():
        path = snapshot / name
        if path.is_symlink() or sha256_file(path) != digest:
            raise ValueError("Snapshot checksum mismatch.")
    manifest = read_json(snapshot / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "transcript_snapshot.v1":
        raise ValueError("Unsupported snapshot version.")
    files = checked_entries(manifest)
    for row in files:
        source_path(snapshot, row["path"])
    expected = {"data/" + row["path"]: row for row in files}
    with zipfile.ZipFile(snapshot / "artifacts.zip") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("Snapshot archive contains missing, duplicate or unexpected members.")
        for info in archive.infolist():
            row = expected[info.filename]
            if info.file_size != row["size_bytes"] or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Snapshot member size/type mismatch.")
            digest = hashlib.sha256()
            with archive.open(info) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != row["sha256"]:
                raise ValueError("Snapshot member checksum mismatch.")
    return manifest


def create_snapshot(selection: Path, root: Path, output: Path, *, execute: bool = False,
                    storage: dict | None = None) -> dict:
    selection, root, output = map(private_path, (selection, root, output))
    document = read_json(selection)
    if not isinstance(document, dict) or document.get("schema_version") != "transcript_snapshot_selection.v1":
        raise ValueError("Expected transcript_snapshot_selection.v1.")
    files = checked_entries(document)
    check_sources(root, files)
    size = sum(row["size_bytes"] for row in files)
    if output.exists():
        raise FileExistsError("Snapshot destination exists; never overwrite an earlier snapshot.")
    if not execute:
        return {"dry_run": True, "file_count": len(files), "source_bytes": size}
    _reserve(output, size, storage)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if output.exists():
            raise FileExistsError("Snapshot destination already exists.")
        staging = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=output.parent))
        try:
            with zipfile.ZipFile(staging / "artifacts.zip", "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for row in files:
                    archive.write(source_path(root, row["path"]), "data/" + row["path"])
            with (staging / "artifacts.zip").open("rb") as stream:
                os.fsync(stream.fileno())
            manifest = {"schema_version": "transcript_snapshot.v1", "created_at": utc_now(),
                        "selection_sha256": sha256_file(selection), "files": files,
                        "content_review_accepted": False,
                        "scope": "Byte preservation, not transcript quality approval or off-device backup."}
            atomic_write_json(staging / "manifest.json", manifest, overwrite=False)
            receipts = {name: sha256_file(staging / name) for name in ("manifest.json", "artifacts.zip")}
            atomic_write_json(staging / "checksums.json", receipts, overwrite=False)
            verify_snapshot(staging)
            check_sources(root, files)
            if output.exists():
                raise FileExistsError("Snapshot destination appeared during publication.")
            os.rename(staging, output)
            for path in output.iterdir():
                path.chmod(0o444)
            output.chmod(0o555)
            fd = os.open(output.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {"dry_run": False, "file_count": len(files), "source_bytes": size,
            "archive_bytes": (output / "artifacts.zip").stat().st_size, **receipts}


def restore_snapshot(snapshot: Path, output: Path, *, execute: bool = False,
                     storage: dict | None = None) -> dict:
    snapshot, output = map(private_path, (snapshot, output))
    manifest = verify_snapshot(snapshot)
    files = manifest["files"]
    if output.exists():
        raise FileExistsError("Restore requires a new directory; canonical data is never overwritten.")
    result = {"dry_run": not execute, "file_count": len(files), "source_bytes": sum(r["size_bytes"] for r in files)}
    if not execute:
        return result
    _reserve(output, result["source_bytes"], storage)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if output.exists():
            raise FileExistsError("Restore destination already exists.")
        staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=output.parent))
        try:
            with zipfile.ZipFile(snapshot / "artifacts.zip") as archive:
                for row in files:
                    if archive.getinfo("data/" + row["path"]).file_size != row["size_bytes"]:
                        raise ValueError("Archive member size changed before restore.")
                    path = source_path(staging, row["path"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open("data/" + row["path"]) as src, path.open("xb") as dst:
                        remaining = row["size_bytes"]
                        while remaining:
                            chunk = src.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("Truncated archive member.")
                            dst.write(chunk)
                            remaining -= len(chunk)
                        if src.read(1):
                            raise ValueError("Oversized archive member.")
                        dst.flush()
                        os.fsync(dst.fileno())
                    if path.stat().st_size != row["size_bytes"] or sha256_file(path) != row["sha256"]:
                        raise ValueError("Archive changed during restore.")
                    path.chmod(0o444)
            if output.exists():
                raise FileExistsError("Restore destination appeared during publication.")
            os.rename(staging, output)
            fd = os.open(output.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return result


def protect_raw_sources(snapshot: Path, root: Path) -> dict:
    manifest = verify_snapshot(snapshot)
    root = private_path(root)
    check_sources(root, manifest["files"])
    count = 0
    for row in manifest["files"]:
        if row["role"] in {"raw", "rejected"}:
            source_path(root, row["path"]).chmod(0o444)
            count += 1
    return {"read_only_source_files": count,
            "scope": "Accidental-write protection only; the owner can chmod or replace files."}
