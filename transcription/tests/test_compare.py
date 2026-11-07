from __future__ import annotations

import json

import pytest

from transcription.common import PROJECT_ROOT, atomic_write_json, sha256_file
from transcription.compare import compare_transcripts
from transcription.main import main


@pytest.fixture
def pair(tmp_path):
    controls = {"schema_version": "transcription_raw.v1", "terminology_sha256": "b" * 64,
                "model_name": "synthetic", "device": "cuda", "compute_type": "float16",
                "language": "zh", "beam_size": 5, "vad": {"enabled": True, "min_silence_duration_ms": 500},
                "condition_on_previous_text": False, "word_timestamps": False}
    metadata = {"episode_id": "SYN1", "model": "synthetic", "model_revision": "synthetic-revision",
                "audio_sha256": "a" * 64, "audio_duration_seconds": 10, "language": "zh",
                "gpu_model": "synthetic GPU", "package_versions": {"faster_whisper": "synthetic"},
                "config_fingerprint": controls, "transcription_seconds": 5}
    left = {"schema_version": "transcription_raw.v1", "metadata": metadata,
            "segments": [{"segment_id": 0, "start": 0, "end": 10, "text": "synthetic private evidence"}]}
    right = {**left, "metadata": {**metadata, "transcription_seconds": 2, "inference_mode": "batched",
                                 "inference_batch_size": 4, "config_fingerprint": {
                                     **controls, "inference_mode": "batched", "inference_batch_size": 4,
                                     "batched_profile": "timestamped_v1"}}}
    paths = [tmp_path / "baseline.json", tmp_path / "candidate.json"]
    for path, data in zip(paths, (left, right)):
        atomic_write_json(path, data)
    return paths


def mutate(path, change):
    data = json.loads(path.read_text())
    change(data)
    atomic_write_json(path, data)


def test_compare_metrics_no_source_text_or_quality_approval(pair, tmp_path):
    before = [sha256_file(p) for p in pair]
    report = tmp_path / "report.json"
    result = compare_transcripts(*pair, report)
    assert result["setup_controls_match"] and result["observed_transcription_speedup"] == 2.5
    assert result["text_identical_ignoring_whitespace"]
    assert result["baseline"]["inference_mode"] == "sequential"
    assert result["candidate"]["inference_batch_size"] == 4
    assert result["candidate"]["realtime_factor"] == 0.2
    assert result["content_review_required"] and not result["ready_for_extraction"]
    assert "synthetic private evidence" not in report.read_text()
    assert str(tmp_path) not in report.read_text()
    assert before == [sha256_file(p) for p in pair]
    with pytest.raises(FileExistsError):
        compare_transcripts(*pair, report)


@pytest.mark.parametrize("key,value", [("audio_sha256", "c" * 64), ("audio_sha256", None), ("episode_id", "SYN2")])
def test_reject_different_or_unknown_source_identity(pair, key, value):
    mutate(pair[1], lambda d: d["metadata"].update({key: value}))
    with pytest.raises(ValueError):
        compare_transcripts(*pair)


@pytest.mark.parametrize("change", ["gpu", "precision", "missing_revision", "missing_fingerprint", "missing_packages"])
def test_confounded_or_missing_controls_suppress_speedup(pair, change):
    def update(data):
        m = data["metadata"]
        if change == "gpu": m["gpu_model"] = "other GPU"
        if change == "precision": m["config_fingerprint"]["compute_type"] = "int8_float16"
        if change == "missing_revision": m.pop("model_revision")
        if change == "missing_fingerprint": m.pop("config_fingerprint")
        if change == "missing_packages": m["package_versions"] = {}
    mutate(pair[1], update)
    result = compare_transcripts(*pair)
    assert not result["setup_controls_match"]
    assert result["observed_transcription_speedup"] is None


@pytest.mark.parametrize("seconds", [None, 0, -1, True, "fast"])
def test_unknown_timing_does_not_invent_speedup(pair, seconds):
    mutate(pair[1], lambda d: d["metadata"].update(transcription_seconds=seconds))
    result = compare_transcripts(*pair)
    assert result["observed_transcription_speedup"] is None
    assert result["candidate"]["realtime_factor"] is None
    assert result["candidate"]["estimated_job_cost_usd"] is None


def test_invalid_candidate_does_not_write_report(pair, tmp_path):
    mutate(pair[1], lambda d: d.update(segments=[]))
    output = tmp_path / "report.json"
    with pytest.raises(ValueError, match="segments"):
        compare_transcripts(*pair, output)
    assert not output.exists()


def test_compare_rejects_public_data_output_and_same_input(pair):
    with pytest.raises(ValueError, match="public"):
        compare_transcripts(*pair, PROJECT_ROOT / "sample_outputs/comparison.json")
    with pytest.raises(ValueError, match="separate"):
        compare_transcripts(pair[0], pair[0])
    with pytest.raises(ValueError, match="public"):
        compare_transcripts(PROJECT_ROOT / "sample_outputs/synthetic.json", pair[1])


def test_compare_cli_no_model_or_network(pair, capsys, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    assert main(["compare-transcripts", "--baseline", str(pair[0]), "--candidate", str(pair[1])]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["content_review_required"]
