from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from transcription import batch
from transcription.common import PROJECT_ROOT, atomic_write_json, find_episode, private_path, read_json, sha256_file
from transcription.main import main
from transcription.prepare import prepare
from transcription.quality import quality_gate, validate_payload


def raw():
    return {"metadata": {"episode_id": "EP57", "model": "synthetic", "language": "zh", "audio_duration_seconds": 2},
            "segments": [{"segment_id": 0, "start": 0, "end": 1, "text": "synthetic first"},
                         {"segment_id": 1, "start": 1, "end": 2, "text": "synthetic second"}]}


@pytest.fixture
def config(tmp_path):
    rows = [{"episode_id": eid, "episode_number": eid[2:], "episode_title": eid + " synthetic",
             "audio_url": "https://example.invalid/audio.mp3"} for eid in ["EP579", "EP57", "EP670"]]
    manifest = tmp_path / "episodes.json"
    atomic_write_json(manifest, rows)
    path = tmp_path / "batch.yaml"
    path.write_text(json.dumps({"batch_id": "test_v1", "episode_ids": ["EP57", "EP670"],
                               "manifest_path": str(manifest), "output_root": str(tmp_path / "batches"),
                               "audio_root": str(tmp_path / "audio"), "transcript_root": str(tmp_path / "raw"),
                               "retry_count": 0, "audio_policy": "keep"}))
    return batch.load_batch_config(path)


@pytest.fixture
def worker(monkeypatch):
    calls = {"download": 0, "transcribe": 0}

    def download(episode, path, *args):
        calls["download"] += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic audio placeholder")
        return {"duration_seconds": 2, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}

    def transcribe(*args):
        calls["transcribe"] += 1
        return raw()["segments"], {"model_load_seconds": 0.1, "transcription_seconds": 0.2}

    monkeypatch.setattr(batch, "download_audio", download)
    monkeypatch.setattr(batch, "transcribe_audio", transcribe)
    return calls


def run(config, **kwargs):
    return batch.run_batch(config, dry_run=False, resume=False, retry_failed=False, max_episodes=None, **kwargs)


def test_exact_matching_and_numeric_prefix_no_fallback(config):
    rows = batch.resolve_manifest_rows(config)
    assert find_episode(rows, "EP57")["episode_id"] == "EP57"
    assert find_episode(rows, "57")["episode_id"] == "EP57"
    assert find_episode(rows, "EP670")["episode_id"] == "EP670"
    with pytest.raises(ValueError, match="not found"):
        find_episode(rows[:1], "EP57")
    with pytest.raises(ValueError, match="Ambiguous"):
        find_episode(rows, "synthetic")
    # Exact ID outranks a different record's matching episode_number.
    assert find_episode([rows[1], {**rows[0], "episode_number": "57"}], "EP57")["episode_id"] == "EP57"


def test_plan_and_dry_run_are_read_only(config, worker, tmp_path):
    before = set(tmp_path.rglob("*"))
    result = batch.run_batch(config, dry_run=True, resume=False, retry_failed=False, max_episodes=1)
    assert result["jobs"][0]["episode_id"] == "EP57"
    assert result["jobs"][0]["action"] == "run"
    assert set(tmp_path.rglob("*")) == before
    assert worker == {"download": 0, "transcribe": 0}


def test_run_resume_retry_flag_never_reruns_completed(config, worker):
    assert run(config)["status_counts"] == {"completed": 2}
    paths = batch.resolve_paths(config)
    digest = sha256_file(batch.transcript_path(paths, "EP57"))
    result = batch.run_batch(config, dry_run=False, resume=True, retry_failed=True, max_episodes=None)
    assert result["status_counts"] == {"completed": 2}
    assert result["skipped_completed_count"] == 2
    assert worker == {"download": 2, "transcribe": 2}
    assert sha256_file(batch.transcript_path(paths, "EP57")) == digest
    assert batch.validate_batch_outputs(config)["valid"]


def test_config_mismatch_precedes_skip_and_retry(config, worker):
    run(config)
    config["beam_size"] += 1
    for fn in (lambda: batch.plan(config), lambda: batch.run_batch(config, dry_run=False, resume=True, retry_failed=True, max_episodes=None)):
        with pytest.raises(ValueError, match="Config mismatch"):
            fn()
    assert worker["transcribe"] == 2


def test_failed_job_isolated_and_explicit_retry(config, worker, monkeypatch):
    original = batch.transcribe_audio

    def fail_one(audio, cfg, episode, storage=None):
        if episode["episode_id"] == "EP57":
            raise RuntimeError("https://secret.invalid/?token=do-not-record")
        return original(audio, cfg, episode)

    monkeypatch.setattr(batch, "transcribe_audio", fail_one)
    assert run(config)["status_counts"] == {"failed": 1, "completed": 1}
    paths = batch.resolve_paths(config)
    assert "do-not-record" not in paths.checkpoint_path.read_text()
    assert batch.audio_path(paths, "EP57").exists()
    monkeypatch.setattr(batch, "transcribe_audio", original)
    assert run(config)["status_counts"]["failed"] == 1
    result = batch.run_batch(config, dry_run=False, resume=True, retry_failed=True, max_episodes=None)
    assert result["status_counts"] == {"completed": 2}
    assert read_json(paths.output_root / config["batch_id"] / "failures.json") == []


def test_crash_after_transcript_publication_recovers_without_model(config, worker):
    run(config)
    paths = batch.resolve_paths(config)
    checkpoint = batch.load_checkpoint(paths.checkpoint_path)
    checkpoint["jobs"]["EP57"].update(status="transcribing", stage_history=[])
    atomic_write_json(paths.checkpoint_path, checkpoint)
    batch.run_metadata_path(paths, "EP57").unlink()
    run(config)
    assert worker["transcribe"] == 2
    assert batch.validate_batch_outputs(config)["valid"]


def test_invalid_partial_is_not_overwritten(config, worker):
    paths = batch.resolve_paths(config)
    target = batch.transcript_path(paths, "EP57")
    atomic_write_json(target, {"segments": []})
    digest = sha256_file(target)
    assert batch.plan(config)["jobs"][0]["action"] == "blocked_existing_output"
    assert run(config)["status_counts"] == {"failed": 1, "completed": 1}
    assert sha256_file(target) == digest
    assert worker["transcribe"] == 1


def test_skipped_outputs_revalidated(config, worker):
    run(config)
    run(config)
    target = batch.transcript_path(batch.resolve_paths(config), "EP57")
    target.write_text("{broken")
    assert not batch.validate_batch_outputs(config)["valid"]


def test_empty_checkpoint_not_success(config):
    assert not batch.validate_batch_outputs(config)["valid"]


def test_delete_only_after_completed_checkpoint(config, worker, monkeypatch):
    config["audio_policy"] = "delete_on_success"
    paths = batch.resolve_paths(config)
    unlink = Path.unlink

    def checked_unlink(path, *args, **kwargs):
        if path.suffix == ".mp3":
            job = read_json(paths.checkpoint_path)["jobs"][path.parent.name]
            assert job["status"] == "completed"
            assert job["stage"] == "job_completed"
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", checked_unlink)
    run(config)
    assert not batch.audio_path(paths, "EP57").exists()


def test_empty_transcription_keeps_audio(config, worker, monkeypatch):
    config["audio_policy"] = "delete_on_success"
    monkeypatch.setattr(batch, "transcribe_audio", lambda *a: ([], {}))
    assert run(config)["status_counts"] == {"failed": 2}
    paths = batch.resolve_paths(config)
    assert batch.audio_path(paths, "EP57").exists()
    assert not batch.transcript_path(paths, "EP57").exists()


def test_rejected_decoder_output_preserved_with_safe_diagnostic(config, worker, monkeypatch):
    segments = raw()["segments"]
    segments[1]["text"] = " "
    monkeypatch.setattr(batch, "transcribe_audio", lambda *a: (segments, {}))
    assert run(config)["status_counts"] == {"failed": 2}
    paths = batch.resolve_paths(config)
    rejected = paths.checkpoint_path.parent / "rejected" / "EP57.attempt-1.rejected.json"
    assert read_json(rejected)["segments"] == segments
    error = batch.load_checkpoint(paths.checkpoint_path)["jobs"]["EP57"]["last_error"]
    assert error["validation_detail"] == "Empty text in segment 1."
    assert error["rejected_output_saved"]
    assert error["rejected_output_sha256"] == sha256_file(rejected)
    assert not batch.transcript_path(paths, "EP57").exists()
    digest = sha256_file(rejected)
    batch.run_batch(config, dry_run=False, resume=True, retry_failed=True, max_episodes=None)
    assert sha256_file(rejected) == digest
    assert rejected.with_name("EP57.attempt-2.rejected.json").is_file()


def test_non_json_rejection_retains_original_failure(config, worker, monkeypatch):
    segments = raw()["segments"]
    segments[0]["end"] = float("nan")
    monkeypatch.setattr(batch, "transcribe_audio", lambda *a: (segments, {}))
    assert run(config)["status_counts"] == {"failed": 2}
    paths = batch.resolve_paths(config)
    error = batch.load_checkpoint(paths.checkpoint_path)["jobs"]["EP57"]["last_error"]
    assert "Non-finite" in error["validation_detail"]
    assert error["rejected_output_saved"] is False


def test_atomic_failed_write_preserves_previous(tmp_path):
    path = tmp_path / "checkpoint.json"
    atomic_write_json(path, {"valid": True})
    with pytest.raises(ValueError):
        atomic_write_json(path, {"invalid": float("nan")})
    assert read_json(path) == {"valid": True}
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(FileExistsError):
        atomic_write_json(path, {}, overwrite=False)
    assert read_json(path) == {"valid": True}


@pytest.mark.parametrize("key,value", [("batch_id", "../escape"), ("output_root", "relative"),
                                     ("transcript_root", str(PROJECT_ROOT / "sample_outputs")),
                                     ("audio_root", "${UNCONFIGURED_TRANSCRIPTION_ROOT}/audio")])
def test_invalid_paths_rejected(config, key, value):
    config[key] = value
    with pytest.raises(ValueError):
        batch.resolve_paths(config)


def test_symlink_cannot_escape_data_root(config, tmp_path):
    paths = batch.resolve_paths(config)
    paths.transcript_root.mkdir()
    (paths.transcript_root / "EP57").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        batch.transcript_path(paths, "EP57")
    (tmp_path / "public-link").symlink_to(PROJECT_ROOT)
    with pytest.raises(ValueError, match="public"):
        private_path(tmp_path / "public-link" / "sample_outputs")


@pytest.mark.parametrize("change", ["empty", "duplicate", "nan", "negative", "order", "beyond_duration", "missing_id", "wrong_episode"])
def test_quality_rejects_bad_source(change):
    payload = raw()
    if change == "empty": payload["segments"][0]["text"] = " "
    if change == "duplicate": payload["segments"][1]["segment_id"] = 0
    if change == "nan": payload["segments"][0]["start"] = float("nan")
    if change == "negative": payload["segments"][0]["end"] = -1
    if change == "order": payload["segments"].reverse()
    if change == "beyond_duration": payload["segments"][-1]["end"] = 50
    if change == "missing_id": del payload["segments"][0]["segment_id"]
    if change == "wrong_episode": payload["metadata"]["episode_id"] = "EP579"
    with pytest.raises(ValueError):
        validate_payload(payload, "EP57")


def review(source):
    return {"schema_version": "transcript_review.v1", "episode_id": "EP57",
            "transcript_sha256": sha256_file(source), "decision": "accept", "reviewer": "synthetic-test",
            "reviewed_at": "2026-11-05T00:00:00Z", "method": "synthetic test, not a real quality review",
            "sampled_segment_ids": [0, 1], "unresolved_critical_issues": []}


def test_quality_requires_explicit_hash_bound_review(tmp_path):
    source, assessment = tmp_path / "raw.json", tmp_path / "review.json"
    atomic_write_json(source, raw())
    assert not quality_gate(source)["ready_for_extraction"]
    atomic_write_json(assessment, review(source))
    assert quality_gate(source, review_path=assessment)["ready_for_extraction"]
    atomic_write_json(source, {**raw(), "changed": True})
    with pytest.raises(ValueError, match="version"):
        quality_gate(source, review_path=assessment)


def test_prepare_preserves_raw_mapping_and_blocks_overwrite(tmp_path):
    source, output = tmp_path / "raw.json", tmp_path / "normalized.json"
    atomic_write_json(source, raw())
    before = sha256_file(source)
    result = prepare(source, output)
    assert not result["ready_for_extraction"]
    assert sha256_file(source) == before
    data = read_json(output)
    assert [sid for seg in data["segments"] for sid in seg["source_segment_ids"]] == [0, 1]
    assert data["metadata"]["source_raw_sha256"] == before
    assert data["segments"][0]["raw_text"] == "synthetic first synthetic second"
    with pytest.raises(ValueError, match="new path"):
        prepare(source, output)


def test_cli_help_without_data_or_gpu():
    proc = subprocess.run([sys.executable, "-m", "transcription", "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "validate-transcript" in proc.stdout


def test_cli_quality_exit_code(tmp_path, capsys):
    source = tmp_path / "raw.json"
    atomic_write_json(source, raw())
    assert main(["validate-transcript", "--input", str(source), "--require-ready"]) == 1
    assert '"ready_for_extraction": false' in capsys.readouterr().out


def test_no_archive_imports_or_model_calls_in_quality():
    import ast

    for path in (PROJECT_ROOT / "transcription").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("legacy")
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("legacy") for alias in node.names)


def test_invalid_config_and_checkpoint_are_clear_errors(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("- not-a-mapping\n")
    with pytest.raises(ValueError, match="mapping"):
        batch.load_batch_config(path)
    path.write_text("retry_count: -1\n")
    with pytest.raises(ValueError, match="retry_count"):
        batch.load_batch_config(path)
    atomic_write_json(tmp_path / "checkpoint.json", [])
    with pytest.raises(ValueError, match="Unsupported checkpoint"):
        batch.load_checkpoint(tmp_path / "checkpoint.json")


def test_unresolved_quality_issue_cannot_pass_gate(tmp_path):
    source, assessment = tmp_path / "raw.json", tmp_path / "review.json"
    atomic_write_json(source, raw())
    atomic_write_json(assessment, {**review(source), "unresolved_critical_issues": ["synthetic missing negation"]})
    assert not quality_gate(source, review_path=assessment)["ready_for_extraction"]


def test_missing_cuda_is_explicit(monkeypatch):
    monkeypatch.setattr(batch.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="CUDA requested"):
        batch.ensure_cuda_if_requested("cuda")
    batch.ensure_cuda_if_requested("cpu")


def test_preexisting_audio_is_not_deleted(config, worker):
    paths = batch.resolve_paths(config)
    target = batch.audio_path(paths, "EP57")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"synthetic audio placeholder")
    config["audio_policy"] = "delete_on_success"
    run(config)
    assert target.exists()
    assert not batch.audio_path(paths, "EP670").exists()


def test_completed_resume_recovers_cleanup_without_retranscription(config, worker):
    run(config)
    paths = batch.resolve_paths(config)
    config["audio_policy"] = "delete_on_success"
    run(config)
    assert worker == {"download": 2, "transcribe": 2}
    assert not batch.audio_path(paths, "EP57").exists()


def test_replaced_audio_not_deleted_on_resume(config, worker):
    run(config)
    target = batch.audio_path(batch.resolve_paths(config), "EP57")
    target.write_bytes(b"different user audio")
    config["audio_policy"] = "delete_on_success"
    run(config)
    assert target.read_bytes() == b"different user audio"


def test_storage_failure_stops_admitting_more_jobs(config, worker, monkeypatch):
    def full(*args):
        raise batch.StorageLimitError("synthetic storage floor")
    monkeypatch.setattr(batch, "download_audio", full)
    result = run(config)
    assert result["status_counts"] == {"failed": 1}
    assert worker["transcribe"] == 0
    assert "EP670" not in batch.status(config)["jobs"]
