from unittest.mock import Mock

import pytest

from mentor_agent.model import OpenRouter, ModelError
from knowledge.schemas import obj,TEXT


def settings():
    return {"model": "synthetic", "max_context_chars": 1000, "timeout_seconds": 5, "temperature": 0, "max_output_tokens": 100}


def test_openrouter_structured_format_usage_and_no_key_logs(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-credential")
    response = Mock(status_code=200)
    response.json.return_value = {"id": "synthetic", "usage": {"prompt_tokens": 20, "completion_tokens": 10, "cost": 0.001},
                                 "choices": [{"finish_reason": "stop", "message": {"content": '{"value":"ok"}'}}]}
    post = Mock(return_value=response);monkeypatch.setattr("mentor_agent.model.requests.post", post)
    data, meta = OpenRouter(settings()).complete("Test", {}, obj(value=TEXT))
    assert data == {"value": "ok"} and meta["cost_usd"] == 0.001
    sent = post.call_args.kwargs["json"]
    assert sent["response_format"]["json_schema"]["strict"]
    assert sent["provider"]["require_parameters"]
    assert post.call_args.kwargs["timeout"] == (10, 5)
    assert sent["max_tokens"] == 100
    assert "synthetic-credential" not in str(meta)


@pytest.mark.parametrize("failure", ["truncated", "invalid_json", "invalid_schema", "http_error"])
def test_provider_failure_is_sanitized(monkeypatch, failure):
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-credential")
    response = Mock(status_code=200)
    choice = {"finish_reason": "stop", "message": {"content": '{"value":"ok"}'}}
    if failure == "truncated":choice["finish_reason"] = "length"
    elif failure == "invalid_json":choice["message"]["content"] = "not-json"
    elif failure == "invalid_schema":choice["message"]["content"] = "{}"
    else:response.raise_for_status.side_effect = RuntimeError("synthetic-credential private provider body")
    response.json.return_value = {"usage": {}, "choices": [choice]}
    monkeypatch.setattr("mentor_agent.model.requests.post", Mock(return_value=response))
    with pytest.raises(ModelError) as exc:OpenRouter(settings()).complete("Test", {}, obj(value=TEXT))
    assert "synthetic-credential" not in str(exc.value)


def test_missing_key_no_request(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):OpenRouter(settings())


def test_blank_key_and_oversized_context_make_no_network_request(monkeypatch):
    post = Mock()
    monkeypatch.setattr("mentor_agent.model.requests.post", post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "   ")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenRouter(settings())
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-credential")
    with pytest.raises(ModelError, match="context exceeds limit"):
        OpenRouter(settings()).complete("Test", {"large": "x" * 1000}, obj(value=TEXT))
    post.assert_not_called()
