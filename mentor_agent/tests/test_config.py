from pathlib import Path

import pytest
import yaml

from mentor_agent.runtime import load_config


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MENTOR_RELEASE", raising=False)
    monkeypatch.delenv("MENTOR_MODEL_ID", raising=False)
    settings = yaml.safe_load((Path(__file__).parents[1] / "config/default.yaml").read_text())
    settings["release"] = str(tmp_path / "release.json")
    return settings, tmp_path / "config.yaml"


@pytest.mark.parametrize("key,value", [
    ("model", ["invalid"]), ("model", "   "), ("release", None),
    ("retry_count", True), ("retry_count", 1.0), ("retry_count", 2),
    ("temperature", None), ("temperature", float("nan")), ("temperature", True),
    ("temperature", -1), ("temperature", 3), ("timeout_seconds", 0),
    ("max_query_chars", False), ("minimum_host_cases", 7), ("retrieval", []),
])
def test_invalid_config_fails_at_load(config_file, key, value):
    settings, path = config_file
    settings[key] = value
    path.write_text(yaml.safe_dump(settings))
    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize("key,value", [
    ("lexical_weight", float("nan")), ("cosine_weight", float("inf")),
    ("metadata_weight", True), ("lexical_weight", 10**1000), ("lexical_weight", -0.1),
    ("bm25_scale", 0), ("bm25_scale", float("nan")), ("minimum_score", "0.08"),
    ("domain_mismatch_multiplier", None), ("domain_mismatch_multiplier", -1),
    ("domain_mismatch_multiplier", 2), ("case_limit", True),
])
def test_invalid_retrieval_config_fails_at_load(config_file, key, value):
    settings, path = config_file
    settings["retrieval"][key] = value
    path.write_text(yaml.safe_dump(settings))
    with pytest.raises(ValueError):
        load_config(path)


def test_environment_overrides_and_query_limit(config_file, monkeypatch, tmp_path):
    settings, path = config_file
    settings["max_query_chars"] = 3000
    path.write_text(yaml.safe_dump(settings))
    release = tmp_path / "reviewed.json"
    monkeypatch.setenv("MENTOR_RELEASE", str(release))
    monkeypatch.setenv("MENTOR_MODEL_ID", "synthetic-model")
    loaded = load_config(path)
    assert loaded["release"] == str(release)
    assert loaded["model"] == "synthetic-model"
    assert loaded["max_query_chars"] == 3000


def test_zero_retrieval_weights_are_rejected(config_file):
    settings, path = config_file
    for key in ("lexical_weight", "cosine_weight", "metadata_weight"):
        settings["retrieval"][key] = 0
    path.write_text(yaml.safe_dump(settings))
    with pytest.raises(ValueError, match="positive sum"):
        load_config(path)


def test_malformed_yaml_is_sanitized(config_file):
    _, path = config_file
    path.write_text("private_setting: [unfinished")
    with pytest.raises(ValueError, match="Invalid Mentor YAML") as exc:
        load_config(path)
    assert "private_setting" not in str(exc.value)
