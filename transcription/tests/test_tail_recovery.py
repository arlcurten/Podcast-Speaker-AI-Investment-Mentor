import copy

import pytest

from transcription.common import atomic_write_json, read_json, sha256_file
from transcription.quality import recover_padding_tail, validate_payload


def payload():
    return {"schema_version": "transcription_raw.v1",
            "metadata": {"episode_id": "EP1", "audio_duration_seconds": 100},
            "segments": [
                {"segment_id": 7, "start": 0, "end": 99, "text": "synthetic speech"},
                {"segment_id": 8, "start": 99.6, "end": 129.5,
                 "text": "synthetic rejected padding", "no_speech_prob": 0.88}]}


def recover(tmp_path, value):
    path = tmp_path / "rejected.json"
    atomic_write_json(path, value)
    return recover_padding_tail(path, "EP1", sha256_file(path))


def test_tail_is_quarantined_not_clamped_or_lost(tmp_path):
    original = payload()
    result = recover(tmp_path, original)
    assert result["segments"] == original["segments"][:-1]
    assert result["metadata"]["source_recovery"]["excluded_segments"] == original["segments"][-1:]
    assert result["metadata"]["source_recovery"]["content_review_accepted"] is False
    assert read_json(tmp_path / "rejected.json") == original
    assert validate_payload(result, "EP1")["structurally_valid"]


@pytest.mark.parametrize("field,value", [
    ("start", 98), ("start", 100.01), ("end", 101), ("end", 131),
    ("no_speech_prob", 0.79), ("no_speech_prob", None), ("no_speech_prob", 1.1),
    ("segment_id", 7), ("segment_id", -1),
])
def test_outside_narrow_recovery_bounds_fails(tmp_path, field, value):
    source = payload()
    source["segments"][-1][field] = value
    with pytest.raises(ValueError):
        recover(tmp_path, source)


def test_retained_segment_errors_are_not_hidden(tmp_path):
    source = payload()
    source["segments"][0]["end"] = 102
    with pytest.raises(ValueError, match="exceeds audio duration"):
        recover(tmp_path, source)


def test_empty_transcript_is_not_recovered(tmp_path):
    source = payload()
    source["segments"] = source["segments"][-1:]
    with pytest.raises(ValueError):
        recover(tmp_path, source)


def test_episode_and_hash_must_match(tmp_path):
    source = payload()
    path = tmp_path / "rejected.json"
    atomic_write_json(path, source)
    with pytest.raises(ValueError, match="checksum"):
        recover_padding_tail(path, "EP1", "wrong")
    with pytest.raises(ValueError, match="episode ID"):
        recover_padding_tail(path, "EP2", sha256_file(path))


def test_valid_output_unchanged_and_recursive_recovery_rejected(tmp_path):
    source = payload()
    source["segments"][-1]["end"] = 100
    with pytest.raises(ValueError, match="does not need"):
        recover(tmp_path, source)
    source = payload()
    source["metadata"]["source_recovery"] = {"method": "earlier_recovery"}
    before = copy.deepcopy(source)
    with pytest.raises(ValueError, match="Repeated"):
        recover(tmp_path, source)
    assert read_json(tmp_path / "rejected.json") == before
