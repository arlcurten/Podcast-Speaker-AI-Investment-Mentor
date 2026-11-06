from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from transcription import batch
from transcription.common import PROJECT_ROOT, atomic_write_json, read_json, sha256_file
from transcription.transfer import export_bundle, import_bundle, verify_bundle


@pytest.fixture
def completed(tmp_path, monkeypatch):
    manifest = tmp_path / "episodes.json"
    atomic_write_json(manifest, [{"episode_id": "EP57", "episode_title": "synthetic"}])
    config_path = tmp_path / "worker.yaml"
    config_path.write_text(json.dumps({"batch_id": "synthetic_v1", "episode_ids": ["EP57"],
                                     "manifest_path": str(manifest), "output_root": str(tmp_path / "worker/batches"),
                                     "transcript_root": str(tmp_path / "worker/raw"), "audio_root": str(tmp_path / "worker/audio"),
                                     "audio_policy": "keep", "retry_count": 0}))
    cfg = batch.load_batch_config(config_path)

    def download(episode, path, *args):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic audio")
        return {"sha256": sha256_file(path), "duration_seconds": 2}

    monkeypatch.setattr(batch, "download_audio", download)
    monkeypatch.setattr(batch, "transcribe_audio", lambda *args: ([{"segment_id": 0, "start": 0, "end": 2,
                                                                  "text": "synthetic"}], {"transcription_seconds": 1}))
    batch.run_batch(cfg, dry_run=False, resume=False, retry_failed=False, max_episodes=None)
    return cfg


def test_export_import_dry_run_no_audio_and_immutable(completed, tmp_path):
    source, dest = tmp_path / "bundle", tmp_path / "private"
    before = set(tmp_path.rglob("*"))
    plan = export_bundle(completed, source)
    assert set(tmp_path.rglob("*")) == before
    assert not source.exists()
    assert len(plan["files"]) == 6
    export_bundle(completed, source, execute=True)
    assert verify_bundle(source)["completed_episodes"] == ["EP57"]
    assert not list(source.rglob("*.mp3"))
    before = set(tmp_path.rglob("*"))
    assert import_bundle(source, dest)["dry_run"]
    assert set(tmp_path.rglob("*")) == before
    report = import_bundle(source, dest, execute=True)
    assert not report["canonical_overwritten"]
    imported = Path(report["destination"])
    assert verify_bundle(imported) == verify_bundle(source)
    assert import_bundle(source, dest, execute=True)["action"] == "reuse_verified"
    assert not (dest / "shared_data").exists()


@pytest.mark.parametrize("corrupt", ["checksum", "episode", "timestamps", "metadata", "stage", "version", "traversal", "audio", "duplicate", "missing"])
def test_reject_corrupt_bundle(completed, tmp_path, corrupt):
    source = tmp_path / "bundle"
    export_bundle(completed, source, execute=True)
    index = read_json(source / "bundle_manifest.json")
    raw = source / "transcripts/EP57/raw_transcript.json"
    target = raw
    payload = read_json(raw)
    if corrupt == "checksum":
        raw.write_text("{}")
    elif corrupt == "episode":
        payload["metadata"]["episode_id"] = "EP579"
    elif corrupt == "timestamps":
        payload["segments"][0]["end"] = -5
    elif corrupt == "metadata":
        target = source / "transcripts/EP57/raw_run_metadata.json"
        payload = {}
    elif corrupt == "stage":
        target = source / "batch/checkpoint.json"
        payload = read_json(target)
        payload["jobs"]["EP57"]["stage_history"] = []
    elif corrupt == "version":
        payload["schema_version"] = "unknown"
    elif corrupt == "traversal":
        index["files"][0]["path"] = "../escape.json"
    elif corrupt == "audio":
        index["files"][0]["path"] = "audio/episode.mp3"
    elif corrupt == "duplicate":
        index["files"].append(copy.deepcopy(index["files"][0]))
    elif corrupt == "missing":
        index["files"].pop()
    if corrupt in {"episode", "timestamps", "metadata", "stage", "version"}:
        atomic_write_json(target, payload)
        row = next(x for x in index["files"] if x["path"] == target.relative_to(source).as_posix())
        row.update(sha256=sha256_file(target), size_bytes=target.stat().st_size)
    atomic_write_json(source / "bundle_manifest.json", index)
    with pytest.raises(ValueError):
        import_bundle(source, tmp_path / "private", execute=True)
    assert not (tmp_path / "private/pipeline_data").exists()


def test_reject_changed_existing_destination(completed, tmp_path):
    source, dest = tmp_path / "bundle", tmp_path / "private"
    export_bundle(completed, source, execute=True)
    import_bundle(source, dest, execute=True)
    index = read_json(source / "bundle_manifest.json")
    index["created_at"] = "changed"
    atomic_write_json(source / "bundle_manifest.json", index)
    with pytest.raises(FileExistsError):
        import_bundle(source, dest, execute=True)


def test_reject_public_destination_and_symlink(completed, tmp_path):
    source = tmp_path / "bundle"
    export_bundle(completed, source, execute=True)
    with pytest.raises(ValueError, match="public"):
        import_bundle(source, PROJECT_ROOT / "sample_outputs")
    path = source / "batch/summary.json"
    other = tmp_path / "other.json"
    path.rename(other)
    path.symlink_to(other)
    with pytest.raises(ValueError):
        verify_bundle(source)


def test_copy_error_does_not_publish_partial(completed, tmp_path, monkeypatch):
    from transcription import transfer
    def failure(*args):
        raise OSError("synthetic full disk")
    monkeypatch.setattr(transfer, "_copy", failure)
    with pytest.raises(OSError):
        export_bundle(completed, tmp_path / "bundle", execute=True)
    assert not (tmp_path / "bundle").exists()
    assert not list(tmp_path.glob(".transfer-*"))
