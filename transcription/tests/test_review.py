from types import SimpleNamespace

import pytest

from transcription import prepare as preparation
from transcription.batch import serialize_segment
from transcription.common import PROJECT_ROOT, atomic_write_json, read_json, sha256_file
from transcription.main import main
from transcription.quality import quality_gate
from transcription.review import create_review_packet, review_signals


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "raw.json"
    atomic_write_json(path, {
        "metadata": {"episode_id": "SYNTHETIC", "model": "synthetic", "language": "zh", "audio_duration_seconds": 200},
        "segments": [{"segment_id": i * 2, "start": i * 5, "end": i * 5 + 4,
                      "text": f"Synthetic review sentence {i}", "avg_logprob": -0.1,
                      "compression_ratio": 1.2, "no_speech_prob": 0.0} for i in range(40)],
    })
    return path


def test_normalized_dictionary_match_is_annotation_not_replacement(source, tmp_path, monkeypatch):
    # Synthetic spelling pair: Simplified input versus Traditional dictionary.
    payload = read_json(source)
    payload["segments"][0]["text"] = "\u6d4b\u8bd5\u9519\u8bcd"
    atomic_write_json(source, payload)
    monkeypatch.setattr(preparation, "load_annotation_terms", lambda: [{
        "source_text": "\u6e2c\u8a66\u932f\u8a5e", "target_text": "\u5408\u6210\u66ff\u4ee3",
        "type": "correction", "status": "human_reviewed", "notes": "Synthetic context only; not a universal correction.",
    }])
    digest = sha256_file(source)
    output = tmp_path / "normalized.json"
    preparation.prepare(source, output)
    row = read_json(output)["segments"][0]
    note = row["terminology_annotations"][0]
    assert note["matched_views"] == ["normalized_text_zh_tw"]
    assert note["status"] == "human_reviewed" and note["notes"].startswith("Synthetic")
    assert note["context_verification_required"]
    assert note["suggested_text"] not in row["normalized_text_zh_tw"]
    assert payload["segments"][0]["text"] in row["raw_text"]
    assert sha256_file(source) == digest


def test_segment_serializer_preserves_optional_metrics_and_word_ids():
    segment = SimpleNamespace(start=1, end=2, text="synthetic", avg_logprob=-0.3,
                              no_speech_prob=0.1, compression_ratio=1.4,
                              words=[SimpleNamespace(word="synthetic", start=1, end=2, probability=0.9)])
    row = serialize_segment(segment, 17, True)
    assert row["segment_id"] == 17
    assert row["avg_logprob"] == -0.3 and row["no_speech_prob"] == 0.1 and row["compression_ratio"] == 1.4
    assert row["words"][0]["start"] == 1
    segment.avg_logprob = float("nan")
    segment.no_speech_prob = True
    del segment.compression_ratio
    assert all(serialize_segment(segment, 0)[key] is None for key in ("avg_logprob", "no_speech_prob", "compression_ratio"))


def test_packet_spread_risk_selection_and_source_immutability(source, tmp_path):
    payload = read_json(source)
    payload["segments"][12]["avg_logprob"] = -2.0
    atomic_write_json(source, payload)
    digest = sha256_file(source)
    output = tmp_path / "packet.json"
    summary = create_review_packet(source, output, sample_count=6, context_seconds=3)
    packet = read_json(output)
    assert summary["sample_count"] == 6 and summary["ready_for_extraction"] is False
    samples = packet["samples"]
    assert 24 in [s["focus_segment_id"] for s in samples]
    assert any("coverage_sample" in s["selection_reasons"] for s in samples)
    assert all(s["review_status"] == "pending_audio_review" for s in samples)
    assert samples[-1]["end"] > 150
    assert packet["diagnostics"]["candidate_segment_counts"]["review_avg_logprob"] == 1
    assert all(v == 40 for v in packet["diagnostics"]["metric_coverage"].values())
    assert sha256_file(source) == digest
    assert not quality_gate(source)["ready_for_extraction"]
    with pytest.raises(ValueError, match="transcript_review.v1"):
        quality_gate(source, review_path=output)


def test_packet_reproducible_selection_and_explicit_ids(source, tmp_path):
    outputs = [tmp_path / "one.json", tmp_path / "two.json"]
    for output in outputs:
        create_review_packet(source, output, sample_count=3, context_seconds=1, segment_ids=[2, 2, 4])
    first, second = (read_json(path) for path in outputs)
    assert first["samples"] == second["samples"]
    assert first["selection"]["requested_segment_ids"] == [2, 4]
    assert {2, 4} <= {s["focus_segment_id"] for s in first["samples"]}
    for sample in first["samples"]:
        assert sample["start"] <= sample["end"]
        assert sample["focus_segment_id"] in [s["segment_id"] for s in sample["segments"]]


@pytest.mark.parametrize("kwargs", [{"sample_count": 0}, {"sample_count": 33}, {"context_seconds": float("nan")},
                                   {"context_seconds": -1}, {"segment_ids": [999]}, {"sample_count": 1, "segment_ids": [0, 2]}])
def test_invalid_review_request_does_not_write(source, tmp_path, kwargs):
    output = tmp_path / "packet.json"
    with pytest.raises(ValueError):
        create_review_packet(source, output, **kwargs)
    assert not output.exists()


def test_packet_audio_binding_and_no_decoding(source, tmp_path):
    audio = tmp_path / "synthetic.mp3"
    audio.write_bytes(b"not real audio, hash only")
    output = tmp_path / "packet.json"
    create_review_packet(source, output, audio=audio)
    assert read_json(output)["audio"]["source_binding"] == "unverified_retrospective_association"
    assert not read_json(output)["audio"]["listened"]
    payload = read_json(source)
    payload["metadata"]["audio_sha256"] = "wrong"
    atomic_write_json(source, payload)
    with pytest.raises(ValueError, match="Audio does not match"):
        create_review_packet(source, tmp_path / "bad.json", audio=audio)
    payload["metadata"]["audio_sha256"] = sha256_file(audio)
    atomic_write_json(source, payload)
    create_review_packet(source, tmp_path / "matched.json", audio=audio)
    assert read_json(tmp_path / "matched.json")["audio"]["source_binding"] == "recorded_hash_match"


def test_private_packet_paths_and_no_overwrite(source, tmp_path):
    with pytest.raises(ValueError, match="private|public|repository"):
        create_review_packet(source, PROJECT_ROOT / "sample_outputs" / "review.json")
    with pytest.raises(ValueError, match="new output"):
        create_review_packet(source, source)
    output = tmp_path / "packet.json"
    create_review_packet(source, output)
    digest = sha256_file(output)
    with pytest.raises(ValueError, match="new output"):
        create_review_packet(source, output)
    assert sha256_file(output) == digest


def test_cli_only_prints_metadata_and_does_not_invoke_worker(source, tmp_path, monkeypatch, capsys):
    from transcription import batch

    def forbidden(*args, **kwargs):
        pytest.fail("Review preparation must not download audio or load a model")

    monkeypatch.setattr(batch, "download_audio", forbidden)
    monkeypatch.setattr(batch, "transcribe_audio", forbidden)
    assert main(["review-packet", "--input", str(source), "--output", str(tmp_path / "packet.json")]) == 0
    stdout = capsys.readouterr().out
    assert "Synthetic review sentence" not in stdout
    assert '"ready_for_extraction": false' in stdout


def test_missing_metrics_do_not_claim_accuracy(source, tmp_path):
    payload = read_json(source)
    for row in payload["segments"]:
        for key in ("avg_logprob", "no_speech_prob", "compression_ratio"):
            row.pop(key)
    atomic_write_json(source, payload)
    output = tmp_path / "packet.json"
    create_review_packet(source, output)
    assert read_json(output)["diagnostics"]["metric_coverage"] == dict.fromkeys(("avg_logprob", "no_speech_prob", "compression_ratio"), 0)
    assert review_signals({}) == []
    assert review_signals({"avg_logprob": float("nan"), "no_speech_prob": True}) == []
