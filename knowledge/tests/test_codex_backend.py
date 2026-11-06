import json
import subprocess
from pathlib import Path

import pytest

from knowledge.codex_backend import CodexBackend, DISABLED_HOST_NOTICE, ModelCallError
from knowledge.schemas import obj,TEXT


def fake_process(monkeypatch, items, result='{"episode_id":"SYN"}', returncode=0, events=()):
    commands = []
    class FakeProcess:
        pid = 999999
        def __init__(self, args, **kwargs):
            commands.append((args, kwargs))
            self.returncode = returncode
            self.output = Path(args[args.index("--output-last-message") + 1])
        def communicate(self, message=None, timeout=None):
            self.output.write_text(result)
            lines = [{"type": "item.completed", "item": item} for item in items]
            lines.extend(events)
            lines.append({"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 5}})
            return "\n".join(json.dumps(x) for x in lines), "private provider stderr not logged"
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    return commands


def test_structured_call_disables_tools_and_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-do-not-pass")
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-do-not-pass")
    commands = fake_process(monkeypatch, [{"type": "agent_message", "text": "synthetic"}])
    backend = CodexBackend("synthetic", tmp_path)
    data, metadata = backend.complete("Test", {}, obj(episode_id=TEXT))
    assert data == {"episode_id": "SYN"}
    args, kwargs = commands[0]
    assert "--ignore-user-config" in args and "--ephemeral" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "multi_agent" in args and "shell_tool" in args
    assert "OPENAI_API_KEY" not in kwargs["env"] and "OPENROUTER_API_KEY" not in kwargs["env"]
    assert metadata["usage"]["input_tokens"] == 12
    assert metadata["cost_usd"] is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("item", [{"type": "command_execution"}, {"type": "mcp_tool_call"}, {"type": "error", "message": "Real failure"}])
def test_unexpected_event_fails_closed_with_usage(monkeypatch, tmp_path, item):
    fake_process(monkeypatch, [item])
    with pytest.raises(ModelCallError) as exc:
        CodexBackend("synthetic", tmp_path).complete("Test", {}, obj(episode_id=TEXT))
    assert exc.value.metadata["usage"]["output_tokens"] == 5


def test_only_known_disabled_host_notice_is_nonfatal(monkeypatch, tmp_path):
    fake_process(monkeypatch, [{"type": "error", "message": DISABLED_HOST_NOTICE}])
    result, meta = CodexBackend("synthetic", tmp_path).complete("Test", {}, obj(episode_id=TEXT))
    assert result["episode_id"] == "SYN"
    assert meta["unexpected_item_types"] == []
    assert meta["cli_notices"] == [DISABLED_HOST_NOTICE]


def test_invalid_json_and_failed_process_retain_usage(monkeypatch, tmp_path):
    fake_process(monkeypatch, [], result="truncated")
    with pytest.raises(ModelCallError, match="valid structured"):
        CodexBackend("synthetic", tmp_path).complete("Test", {}, obj(episode_id=TEXT))
    fake_process(monkeypatch, [], returncode=1)
    with pytest.raises(ModelCallError, match="failed"):
        CodexBackend("synthetic", tmp_path).complete("Test", {}, obj(episode_id=TEXT))


def test_top_level_failure_never_accepts_result_file(monkeypatch, tmp_path):
    fake_process(monkeypatch, [], events=[{"type": "turn.failed", "error": {"message": "Synthetic failure"}}])
    with pytest.raises(ModelCallError, match="failed"):
        CodexBackend("synthetic", tmp_path).complete("Test", {}, obj(episode_id=TEXT))


def test_oversize_context_never_starts_process(monkeypatch, tmp_path):
    commands = fake_process(monkeypatch, [])
    with pytest.raises(ValueError, match="exceeds"):
        CodexBackend("synthetic", tmp_path, max_input_chars=10).complete("Test", {}, obj(episode_id=TEXT))
    assert commands == []


def test_transport_removes_only_unsupported_uniqueness_keyword():
    from knowledge.codex_backend import transport_schema
    from knowledge.schemas import schema, validate_proposal
    original = schema("level1")
    projected = transport_schema(original)
    assert original["properties"]["threads"]["items"]["properties"]["merged_ids"]["uniqueItems"]
    assert "uniqueItems" not in projected["properties"]["threads"]["items"]["properties"]["merged_ids"]
    assert projected["additionalProperties"] is False
    duplicate = {"episode_id": "SYN", "episode_summary": "Synthetic", "threads": [
        {"thread_id": "SYN:T1", "title": "Synthetic", "summary": "Synthetic", "domains": ["investment"],
         "merged_ids": [0, 0], "related_threads": []}]}
    with pytest.raises(ValueError, match="uniqueItems"):
        validate_proposal("level1", duplicate)
