import json
import stat
import zipfile

import pytest

from transcription.common import PROJECT_ROOT, atomic_write_json, read_json, sha256_file
from transcription.main import main
from transcription.review import create_review_packet, scan_corpus
from transcription.snapshot import (check_sources, create_snapshot, protect_raw_sources,
                                    restore_snapshot, verify_snapshot)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from transcription import storage
    monkeypatch.setattr(storage, "is_wsl", lambda: False)
    root = tmp_path / "private"
    source = root / "raw/EP1.json"
    atomic_write_json(source, {"metadata": {"episode_id": "EP1", "audio_duration_seconds": 3,
                                            "language": "zh", "model": "synthetic"},
                              "segments": [{"segment_id": 0, "start": 0, "end": 2,
                                            "text": "Synthetic source"}]})
    selection = root / "selection.json"
    atomic_write_json(selection, {"schema_version": "transcript_snapshot_selection.v1", "files": [{
        "path": "raw/EP1.json", "role": "raw", "episode_id": "EP1", "active": True,
        "sha256": sha256_file(source), "size_bytes": source.stat().st_size}]})
    return root, source, selection, tmp_path / "snapshot"


def create(setup):
    root, source, selection, output = setup
    return create_snapshot(selection, root, output, execute=True)


def update_selection(setup):
    root, source, selection, _ = setup
    data = read_json(selection)
    data["files"][0].update(sha256=sha256_file(source), size_bytes=source.stat().st_size)
    atomic_write_json(selection, data)


def test_dry_run_writes_nothing(setup, tmp_path):
    root, source, selection, output = setup
    before = set(tmp_path.rglob("*"))
    result = create_snapshot(selection, root, output)
    assert result["dry_run"] and result["file_count"] == 1
    assert before == set(tmp_path.rglob("*"))


def test_round_trip_preserves_bytes_and_never_uses_hardlinks(setup, tmp_path):
    root, source, selection, output = setup
    digest = sha256_file(source)
    result = create(setup)
    assert result["manifest.json"] == sha256_file(output / "manifest.json")
    assert verify_snapshot(output)["files"][0]["sha256"] == digest
    assert not (output.stat().st_mode & stat.S_IWUSR)
    destination = tmp_path / "restore"
    assert restore_snapshot(output, destination)["dry_run"]
    assert not destination.exists()
    restore_snapshot(output, destination, execute=True)
    restored = destination / "raw/EP1.json"
    assert sha256_file(restored) == digest == sha256_file(source)
    assert restored.stat().st_ino != source.stat().st_ino
    assert not (restored.stat().st_mode & stat.S_IWUSR)
    with pytest.raises(FileExistsError):
        restore_snapshot(output, root, execute=True)
    with pytest.raises(FileExistsError):
        create(setup)


def test_accidental_source_change_does_not_change_snapshot(setup, tmp_path):
    root, source, _, output = setup
    original = source.read_bytes()
    create(setup)
    source.write_text('{"accidental": true}')
    manifest = verify_snapshot(output)
    with pytest.raises(ValueError, match="changed"):
        check_sources(root, manifest["files"])
    restore_snapshot(output, tmp_path / "restore", execute=True)
    assert (tmp_path / "restore/raw/EP1.json").read_bytes() == original


def test_changed_source_prevents_creation(setup):
    root, source, selection, output = setup
    source.write_text('{"changed": true}')
    with pytest.raises(ValueError, match="changed"):
        create(setup)
    assert not output.exists()


def test_raw_permission_protection_is_explicit_and_hash_checked(setup, capsys):
    root, source, selection, output = setup
    create(setup)
    assert main(["snapshot-protect", "--snapshot", str(output), "--root", str(root)]) == 2
    assert source.stat().st_mode & stat.S_IWUSR
    result = protect_raw_sources(output, root)
    assert result["read_only_source_files"] == 1
    assert not (source.stat().st_mode & stat.S_IWUSR)
    assert "owner can" in result["scope"]


@pytest.mark.parametrize("path", ["../raw.json", "/outside.json", "data/../../raw.json",
                                  ".git/config.json", "credentials.json", "raw/audio.mp3",
                                  "raw\\file.json", "raw/./EP1.json"])
def test_path_escape_and_secret_audio_rejection(setup, path):
    root, source, selection, output = setup
    data = read_json(selection)
    data["files"][0]["path"] = path
    atomic_write_json(selection, data)
    with pytest.raises(ValueError):
        create(setup)
    assert not output.exists()


def test_symlink_and_public_tree_rejected(setup, tmp_path):
    root, source, selection, output = setup
    moved = tmp_path / "real.json"
    source.rename(moved)
    source.symlink_to(moved)
    with pytest.raises(ValueError, match="symlink"):
        create(setup)
    with pytest.raises(ValueError, match="public"):
        create_snapshot(selection, root, PROJECT_ROOT / "sample_outputs/backup")


def test_selection_size_hash_duplicates_and_active_rejection(setup):
    root, source, selection, output = setup
    original = read_json(selection)
    for patch in ({"sha256": "bad"}, {"size_bytes": -1}, {"role": "rejected", "active": True},
                  {"size_bytes": 32_000_001}):
        data = json.loads(json.dumps(original))
        data["files"][0].update(patch)
        atomic_write_json(selection, data)
        with pytest.raises(ValueError):
            create(setup)
    original["files"].append(original["files"][0])
    atomic_write_json(selection, original)
    with pytest.raises(ValueError, match="Duplicate"):
        create(setup)


def test_archive_corruption_fails_verification_and_restore(setup, tmp_path):
    create(setup)
    output = setup[-1]
    archive = output / "artifacts.zip"
    archive.chmod(0o644)
    archive.write_bytes(b"broken")
    with pytest.raises(ValueError, match="checksum"):
        verify_snapshot(output)
    with pytest.raises(ValueError):
        restore_snapshot(output, tmp_path / "restore", execute=True)
    assert not (tmp_path / "restore").exists()


def test_unlisted_archive_member_even_with_updated_receipt_rejected(setup):
    create(setup)
    output = setup[-1]
    output.chmod(0o755)
    archive = output / "artifacts.zip"
    archive.chmod(0o644)
    with zipfile.ZipFile(archive, "a") as zf:
        zf.writestr("data/../outside.json", "{}")
    receipts = read_json(output / "checksums.json")
    receipts["artifacts.zip"] = sha256_file(archive)
    atomic_write_json(output / "checksums.json", receipts)
    with pytest.raises(ValueError, match="unexpected"):
        verify_snapshot(output)


def test_review_never_approves_and_preserves_recovery_annotation(setup, tmp_path):
    root, source, selection, output = setup
    payload = read_json(source)
    payload["metadata"]["source_recovery"] = {"method": "synthetic", "excluded_segments": [
        {"segment_id": 99, "start": 3, "end": 30, "text": "Synthetic rejected tail"}],
        "content_review_accepted": False}
    payload["segments"][0]["text"] = "a" * 21
    atomic_write_json(source, payload)
    update_selection(setup)
    create(setup)
    digest = sha256_file(source)
    report = tmp_path / "review.json"
    result = scan_corpus(output, root, report)
    assert result["priority_counts"] == {"high": 1}
    assert not result["ready_for_extraction"]
    row = read_json(report)["episodes"][0]
    assert row["has_source_recovery"] and row["signal_counts"]["character_repetition"] == 1
    packet = tmp_path / "packet.json"
    create_review_packet(source, packet, sample_count=1)
    assert read_json(packet)["source_recovery"] == payload["metadata"]["source_recovery"]
    assert sha256_file(source) == digest
    with pytest.raises(ValueError, match="new output"):
        scan_corpus(output, root, report)


def test_invalid_selected_raw_is_blocked_not_accepted(setup, tmp_path):
    root, source, selection, output = setup
    payload = read_json(source)
    payload["segments"] = []
    atomic_write_json(source, payload)
    update_selection(setup)
    create(setup)
    result = scan_corpus(output, root, tmp_path / "review.json")
    assert result["priority_counts"] == {"blocked": 1}
    assert not result["ready_for_extraction"]


def test_triage_bounds_listening_queue_not_full_signal_counts(setup, tmp_path):
    root, source, selection, output = setup
    payload = read_json(source)
    payload["metadata"]["audio_duration_seconds"] = 3000
    payload["segments"] = [{"segment_id": i, "start": i * 30, "end": i * 30 + 2,
                            "text": f"Synthetic {i}", "no_speech_prob": 0.9} for i in range(100)]
    payload["segments"][-1]["text"] = "a" * 21
    atomic_write_json(source, payload)
    update_selection(setup)
    create(setup)
    report = tmp_path / "triage.json"
    scan_corpus(output, root, report)
    row = read_json(report)["episodes"][0]
    assert len(row["focus_segments"]) == 16
    assert row["focus_segments"][0]["segment_id"] == 99
    assert row["focus_candidate_count"] == 100
    assert row["signal_counts"]["review_no_speech_prob"] == 100
    assert row["signal_segment_fraction"] == 1
    assert not row["ready_for_extraction"]


def test_cli_verify_does_not_print_source_content(setup, capsys):
    create(setup)
    assert main(["snapshot-verify", "--snapshot", str(setup[-1])]) == 0
    assert "Synthetic source" not in capsys.readouterr().out


def test_interrupted_snapshot_never_publishes_partial_output(setup, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("synthetic interruption")
    monkeypatch.setattr(zipfile.ZipFile, "write", fail)
    with pytest.raises(OSError):
        create(setup)
    output = setup[-1]
    assert not output.exists()
    assert not list(output.parent.glob(".snapshot-*"))


def test_source_changed_during_archive_creation_is_rejected(setup, monkeypatch):
    root, source, selection, output = setup
    write = zipfile.ZipFile.write
    def mutate(*args, **kwargs):
        result = write(*args, **kwargs)
        source.write_text('{"changed": true}')
        return result
    monkeypatch.setattr(zipfile.ZipFile, "write", mutate)
    with pytest.raises(ValueError, match="changed"):
        create(setup)
    assert not output.exists()
