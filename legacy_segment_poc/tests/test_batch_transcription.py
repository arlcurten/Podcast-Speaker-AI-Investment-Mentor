from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.batch import transcription_batch as batch
from modules.common import find_episode


def rows() -> list[dict]:
    return [
        {"episode_id": "EP57", "episode_number": "57", "episode_title": "EP57 real", "audio_url": "https://example.test/57.mp3"},
        {"episode_id": "EP579", "episode_number": "579", "episode_title": "EP579 other", "audio_url": "https://example.test/579.mp3"},
        {"episode_id": "EP670", "episode_number": "670", "episode_title": "EP670 test", "audio_url": "https://example.test/670.mp3"},
    ]


def write_manifest(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows()) + "\n", encoding="utf-8")


def cfg(tmp_path: Path, manifest: Path) -> dict:
    return {
        "batch_id": "test_batch",
        "episode_ids": ["EP57", "EP670"],
        "manifest_path": str(manifest),
        "output_root": str(tmp_path / "pipeline" / "legacy_segment" / "batches"),
        "audio_root": str(tmp_path / "shared" / "audio"),
        "transcript_root": str(tmp_path / "shared" / "transcripts" / "raw"),
        "model_name": "large-v3-turbo",
        "device": "cuda",
        "compute_type": "float16",
        "language": "zh",
        "beam_size": 5,
        "vad": {"enabled": True, "min_silence_duration_ms": 500},
        "audio_policy": "delete_on_success",
        "retry_count": 1,
        "download_timeout_seconds": 1,
        "skip_existing": True,
    }


def test_batch_manifest_parsing(tmp_path: Path) -> None:
    path = tmp_path / "batch.yaml"
    path.write_text("batch_id: x\nepisode_ids: [EP57]\n", encoding="utf-8")
    loaded = batch.load_batch_config(path)
    assert loaded["batch_id"] == "x"
    assert loaded["episode_ids"] == ["EP57"]
    assert loaded["model_name"] == "large-v3-turbo"


def test_exact_episode_matching_ep57_not_ep579() -> None:
    assert find_episode(rows(), "EP57")["episode_id"] == "EP57"
    assert find_episode(rows(), "57")["episode_id"] == "EP57"
    assert find_episode(rows(), "EP579")["episode_id"] == "EP579"


def test_batch_plan(tmp_path: Path) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    result = batch.plan(cfg(tmp_path, manifest))
    assert result["batch_id"] == "test_batch"
    assert [j["episode_id"] for j in result["jobs"]] == ["EP57", "EP670"]
    assert result["jobs"][0]["action"] == "run"


def test_dry_run_does_not_download_or_init_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    monkeypatch.setattr(batch, "download_audio", lambda *a, **k: (_ for _ in ()).throw(AssertionError("download called")))
    monkeypatch.setattr(batch, "transcribe_audio", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called")))
    result = batch.run_batch(cfg(tmp_path, manifest), dry_run=True, resume=False, retry_failed=False, max_episodes=1)
    assert len(result["jobs"]) == 1


def test_completed_episode_skip(tmp_path: Path) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    c = cfg(tmp_path, manifest)
    paths = batch.resolve_paths(c)
    out = batch.transcript_path(paths, "EP57")
    batch.atomic_write_json(out, {"segments": [{"segment_id": 0, "start": 0, "end": 1, "text": "ok"}]})
    result = batch.plan(c)
    assert result["jobs"][0]["action"] == "skip_completed"


def test_failed_episode_retry_runs_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    c = cfg(tmp_path, manifest)
    paths = batch.resolve_paths(c)
    checkpoint = batch.load_checkpoint(paths.checkpoint_path)
    batch.update_job(checkpoint, paths.checkpoint_path, "EP57", {"status": "failed", "stage": "transcription_batch"})
    monkeypatch.setattr(batch, "download_audio", lambda *a, **k: {"duration_seconds": 1, "sha256": "x", "size_bytes": 1})
    monkeypatch.setattr(batch, "transcribe_audio", lambda *a, **k: ([{"segment_id": 0, "start": 0, "end": 1, "text": "ok"}], {"runtime": 1}))
    job = batch.run_job(c, paths, checkpoint, {"episode_id": "EP57", "episode": rows()[0]}, retry_failed=True)
    assert job["status"] == "completed"


def test_resume_from_partial_checkpoint(tmp_path: Path) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    c = cfg(tmp_path, manifest)
    paths = batch.resolve_paths(c)
    checkpoint = batch.load_checkpoint(paths.checkpoint_path)
    job = batch.update_job(checkpoint, paths.checkpoint_path, "EP57", {"status": "downloaded", "stage": "audio_downloaded"})
    assert job["status"] == "downloaded"
    assert paths.checkpoint_path.exists()


def test_atomic_checkpoint_write(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    batch.atomic_write_json(path, {"ok": True})
    assert json.loads(path.read_text())["ok"] is True
    assert not path.with_suffix(".json.tmp").exists()


def test_config_mismatch_detection(tmp_path: Path) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    c = cfg(tmp_path, manifest)
    paths = batch.resolve_paths(c)
    checkpoint = batch.load_checkpoint(paths.checkpoint_path)
    batch.update_job(checkpoint, paths.checkpoint_path, "EP57", {"status": "failed", "config_fingerprint": {"model_name": "old"}})
    with pytest.raises(RuntimeError, match="Config mismatch"):
        batch.run_job(c, paths, checkpoint, {"episode_id": "EP57", "episode": rows()[0]})


def test_partial_output_not_completed(tmp_path: Path) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    paths = batch.resolve_paths(cfg(tmp_path, manifest))
    batch.atomic_write_json(batch.transcript_path(paths, "EP57"), {"segments": []})
    assert not batch.completed_output(paths, "EP57")


def test_empty_transcript_failure(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    batch.atomic_write_json(path, {"segments": []})
    with pytest.raises(ValueError, match="no segments"):
        batch.validate_transcript(path)


def test_invalid_json_validation(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid transcript JSON"):
        batch.validate_transcript(path)


def test_audio_delete_only_after_successful_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    c = cfg(tmp_path, manifest)
    paths = batch.resolve_paths(c)
    apath = batch.audio_path(paths, "EP57")
    apath.parent.mkdir(parents=True)
    apath.write_bytes(b"audio")
    monkeypatch.setattr(batch, "download_audio", lambda *a, **k: {"duration_seconds": 1, "sha256": "x", "size_bytes": 5})
    monkeypatch.setattr(batch, "transcribe_audio", lambda *a, **k: ([{"segment_id": 0, "start": 0, "end": 1, "text": "ok"}], {"runtime": 1}))
    job = batch.run_job(c, paths, batch.load_checkpoint(paths.checkpoint_path), {"episode_id": "EP57", "episode": rows()[0]})
    assert job["status"] == "completed"
    assert not apath.exists()


def test_output_path_stays_under_private_root(tmp_path: Path) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    c = cfg(tmp_path, manifest)
    paths = batch.resolve_paths(c)
    assert batch.transcript_path(paths, "EP57").resolve().is_relative_to(paths.transcript_root.resolve())


def test_public_sample_not_production_output() -> None:
    source = (ROOT / "modules" / "batch" / "transcription_batch.py").read_text(encoding="utf-8")
    assert "sample_outputs" not in source


def test_sync_dry_run_script_exists() -> None:
    script = ROOT.parent / "scripts" / "sync_transcription_outputs.sh"
    assert script.exists()
    assert os.access(script, os.X_OK)


def test_sync_checksum_flag_present() -> None:
    script = (ROOT.parent / "scripts" / "sync_transcription_outputs.sh").read_text(encoding="utf-8")
    assert "--checksum" in script


def test_failure_isolation_records_failed_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    c = cfg(tmp_path, manifest)
    paths = batch.resolve_paths(c)
    monkeypatch.setattr(batch, "download_audio", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    job = batch.run_job(c, paths, batch.load_checkpoint(paths.checkpoint_path), {"episode_id": "EP57", "episode": rows()[0]})
    assert job["status"] == "failed"
    assert job["last_error"]["retryable"] is False


def test_summary_report_generation(tmp_path: Path) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    c = cfg(tmp_path, manifest)
    paths = batch.resolve_paths(c)
    checkpoint = batch.load_checkpoint(paths.checkpoint_path)
    batch.update_job(checkpoint, paths.checkpoint_path, "EP57", {"status": "completed"})
    summary = batch.build_summary(c, checkpoint, paths)
    assert summary["status_counts"]["completed"] == 1
    assert (paths.output_root / "test_batch" / "summary.json").exists()


def test_batch_validate_checks_completed_outputs(tmp_path: Path) -> None:
    manifest = tmp_path / "episodes.jsonl"
    write_manifest(manifest)
    c = cfg(tmp_path, manifest)
    paths = batch.resolve_paths(c)
    checkpoint = batch.load_checkpoint(paths.checkpoint_path)
    batch.update_job(checkpoint, paths.checkpoint_path, "EP57", {"status": "completed"})
    batch.atomic_write_json(batch.transcript_path(paths, "EP57"), {"segments": [{"segment_id": 0, "start": 0, "end": 1, "text": "ok"}]})
    result = batch.validate_batch_outputs(c)
    assert result["valid"] is True
    assert result["validated_jobs"][0]["episode_id"] == "EP57"
    assert (paths.output_root / "test_batch" / "validation_summary.json").exists()
