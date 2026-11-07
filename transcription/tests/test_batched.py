from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from transcription import batch
from transcription.common import atomic_write_json, read_json, sha256_file
from transcription.main import main


@pytest.fixture
def decoder(monkeypatch):
    calls = []
    segments = [SimpleNamespace(start=0.25, end=1.5, text="synthetic speech", avg_logprob=-0.2,
                                no_speech_prob=0.1, compression_ratio=1.1,
                                words=[SimpleNamespace(word="synthetic", start=0.25, end=1.5, probability=0.9)])]

    class Model:
        def __init__(self, *args, **kwargs):
            calls.append(("load", kwargs))

        def transcribe(self, *args, **kwargs):
            calls.append(("sequential", kwargs))
            return iter(segments), SimpleNamespace(language="zh", language_probability=1.0)

    class Batched:
        def __init__(self, model):
            assert isinstance(model, Model)
            calls.append(("batched_init", {}))

        def transcribe(self, *args, **kwargs):
            calls.append(("batched", kwargs))
            return iter(segments), SimpleNamespace(language="zh", language_probability=1.0)

    class Monitor:
        peak_mb = 123
        measurement_scope = "synthetic"

        def __init__(self, enabled):
            pass

        def start(self):
            calls.append(("monitor_start", {}))

        def stop(self):
            calls.append(("monitor_stop", {}))

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model, BatchedInferencePipeline=Batched))
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", SimpleNamespace(download_model=lambda *a, **kw: "/synthetic/snapshots/revision"))
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda _: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=1024))))
    monkeypatch.setattr(batch, "VramMonitor", Monitor)
    monkeypatch.setattr(batch, "ensure_cuda_if_requested", lambda _: None)
    monkeypatch.setattr(batch, "inspect_audio", lambda _: {"duration_seconds": 2})
    monkeypatch.setattr(batch, "gpu_model", lambda: "synthetic GPU")
    monkeypatch.setattr(batch, "pkg", lambda _: "synthetic-version")
    return calls


@pytest.mark.parametrize("mode,size", [("sequential", 1), ("batched", 1), ("batched", 4)])
def test_decoder_selection_preserves_timestamps_prompt_and_diagnostics(tmp_path, decoder, mode, size):
    cfg = {"model_name": "synthetic", "device": "cuda", "compute_type": "float16", "language": "zh",
           "beam_size": 5, "inference_mode": mode, "inference_batch_size": size,
           "word_timestamps": True, "condition_on_previous_text": False,
           "vad": {"enabled": True, "min_silence_duration_ms": 500}}
    segments, metadata = batch.transcribe_audio(tmp_path / "audio.mp3", cfg, {"episode_id": "SYN1"})
    options = next(opts for name, opts in decoder if name == mode)
    assert options["without_timestamps"] is False
    assert options["condition_on_previous_text"] is False
    assert options["initial_prompt"] == batch.terminology_prompt()
    assert options["vad_parameters"]["min_silence_duration_ms"] == 500
    assert options["word_timestamps"] is True
    assert options["beam_size"] == 5
    assert options.get("batch_size") == (size if mode == "batched" else None)
    assert sum(name == "load" for name, _ in decoder) == 1
    assert sum(name == "batched_init" for name, _ in decoder) == (mode == "batched")
    assert decoder[-1][0] == "monitor_stop"
    assert segments[0]["start"] == 0.25 and segments[0]["end"] == 1.5
    assert segments[0]["segment_id"] == 0 and segments[0]["avg_logprob"] == -0.2
    assert segments[0]["words"][0]["probability"] == 0.9
    assert metadata["inference_mode"] == mode and metadata["inference_batch_size"] == size
    assert bool(metadata["decoding_caveats"]) == (mode == "batched")


def test_batched_error_does_not_fall_back_to_sequential(tmp_path, decoder, monkeypatch):
    def fail(*args, **kwargs):
        def generate():
            raise RuntimeError("synthetic out of memory")
            yield
        return generate(), SimpleNamespace(language="zh")

    monkeypatch.setattr(sys.modules["faster_whisper"].BatchedInferencePipeline, "transcribe", fail)
    with pytest.raises(RuntimeError, match="out of memory"):
        batch.transcribe_audio(tmp_path / "audio.mp3", {"model_name": "synthetic", "device": "cuda",
                               "compute_type": "float16", "inference_mode": "batched", "inference_batch_size": 4}, {})
    assert not any(name == "sequential" for name, _ in decoder)
    assert decoder[-1][0] == "monitor_stop"


@pytest.mark.parametrize("settings", [
    {"inference_mode": "unknown"}, {"inference_batch_size": 0}, {"inference_batch_size": True},
    {"inference_batch_size": 4.0}, {"inference_batch_size": 33}, {"inference_batch_size": 4},
    {"inference_mode": "batched", "condition_on_previous_text": True},
    {"inference_mode": "batched", "vad": {"enabled": False}},
])
def test_unsupported_config_rejected_before_model_load(tmp_path, decoder, settings):
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps(settings))
    with pytest.raises(ValueError):
        batch.load_batch_config(config)
    with pytest.raises(ValueError):
        batch.transcribe_audio(tmp_path / "audio.mp3", settings, {})
    assert decoder == []


def test_legacy_fingerprint_unchanged_and_batch_size_is_versioned():
    cfg = {"model_name": "synthetic", "beam_size": 5}
    legacy = batch.config_fingerprint(cfg)
    assert "inference_mode" not in legacy
    assert batch.config_fingerprint({**cfg, "inference_mode": "sequential", "inference_batch_size": 1}) == legacy
    four = batch.config_fingerprint({**cfg, "inference_mode": "batched", "inference_batch_size": 4})
    eight = batch.config_fingerprint({**cfg, "inference_mode": "batched", "inference_batch_size": 8})
    assert legacy != four != eight
    assert four["batched_profile"] == "timestamped_v1"


def test_batched_plan_read_only_and_cli_override(tmp_path, monkeypatch, capsys):
    config = {"batch_id": "synthetic_v1", "episode_ids": ["SYN1"], "manifest_path": str(tmp_path / "catalog.json"),
              "output_root": str(tmp_path / "output"), "audio_root": str(tmp_path / "audio"),
              "transcript_root": str(tmp_path / "raw")}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(json.dumps(config))
    atomic_write_json(tmp_path / "catalog.json", [{"episode_id": "SYN1"}])
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setattr(batch, "download_audio", lambda *a: pytest.fail("dry-run downloaded audio"))
    before = set(tmp_path.rglob("*"))
    assert main(["batch-plan", "--config", str(config_path), "--dry-run", "--inference-mode", "batched",
                 "--inference-batch-size", "4"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["inference_mode"] == "batched" and result["inference_batch_size"] == 4
    assert set(tmp_path.rglob("*")) == before
    assert main(["batch-plan", "--config", str(config_path), "--inference-batch-size", "0"]) == 2


def test_batched_resume_and_config_collision(tmp_path, monkeypatch, decoder):
    from transcription.transfer import export_bundle, import_bundle, verify_bundle

    cfg = {"batch_id": "synthetic_v1", "episode_ids": ["SYN1"], "manifest_path": str(tmp_path / "catalog.json"),
           "output_root": str(tmp_path / "output"), "audio_root": str(tmp_path / "audio"),
           "transcript_root": str(tmp_path / "raw"), "model_name": "synthetic", "device": "cuda",
           "compute_type": "float16", "language": "zh", "inference_mode": "batched", "inference_batch_size": 4,
           "audio_policy": "delete_on_success"}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(json.dumps(cfg))
    cfg = batch.load_batch_config(config_path)
    atomic_write_json(tmp_path / "catalog.json", [{"episode_id": "SYN1"}])

    def download(episode, path, *args):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic placeholder")
        return {"duration_seconds": 2, "sha256": sha256_file(path)}

    monkeypatch.setattr(batch, "download_audio", download)
    result = batch.run_batch(cfg, dry_run=False, resume=False, retry_failed=False, max_episodes=1)
    assert result["status_counts"] == {"completed": 1}
    paths = batch.resolve_paths(cfg)
    assert not batch.audio_path(paths, "SYN1").exists()
    original_hash = sha256_file(batch.transcript_path(paths, "SYN1"))
    calls_before = list(decoder)
    monkeypatch.setattr(batch, "download_audio", lambda *a: pytest.fail("resume downloaded audio"))
    result = batch.run_batch(cfg, dry_run=False, resume=True, retry_failed=True, max_episodes=1)
    assert result["skipped_completed_count"] == 1 and decoder == calls_before
    assert sha256_file(batch.transcript_path(paths, "SYN1")) == original_hash
    for update in ({"inference_batch_size": 8}, {"inference_mode": "sequential", "inference_batch_size": 1}):
        with pytest.raises(ValueError, match="Config mismatch"):
            batch.plan({**cfg, **update})
    assert batch.validate_batch_outputs(cfg)["valid"]
    export_bundle(cfg, tmp_path / "bundle", execute=True)
    imported = import_bundle(tmp_path / "bundle", tmp_path / "destination", execute=True)
    from pathlib import Path
    assert verify_bundle(Path(imported["destination"]))["audio_included"] is False
    metadata = read_json(Path(imported["destination"]) / "transcripts/SYN1/raw_run_metadata.json")
    assert metadata["inference_batch_size"] == 4
