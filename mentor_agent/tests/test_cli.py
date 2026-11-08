from unittest.mock import Mock

import pytest

from mentor_agent import __main__ as cli


@pytest.mark.parametrize("arguments", [["serve", "--port", "0"], ["serve", "--port", "65536"],
                                       ["--no-llm", "serve"]])
def test_invalid_serve_options_fail_before_loading_data(monkeypatch, arguments):
    loader = Mock()
    monkeypatch.setattr(cli, "load_config", loader)
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments)
    assert exc.value.code == 2
    loader.assert_not_called()


def test_serve_binds_loopback_without_access_logs_or_model_calls(monkeypatch):
    settings = {"max_query_chars": 2000}
    mentor = Mock(settings=settings)
    constructor, run = Mock(), Mock()
    monkeypatch.setattr(cli, "load_project_dotenv", Mock())
    monkeypatch.setattr(cli, "load_config", Mock(return_value=settings))
    monkeypatch.setattr(cli, "Mentor", Mock(return_value=mentor))
    monkeypatch.setattr("mentor_agent.model.OpenRouter", constructor)
    monkeypatch.setattr("uvicorn.run", run)
    assert cli.main(["serve", "--port", "9876"]) == 0
    constructor.assert_called_once_with(settings)
    constructor.return_value.complete.assert_not_called()
    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 9876, "access_log": False}


def test_missing_credential_never_binds(monkeypatch, capsys):
    run = Mock()
    monkeypatch.setattr(cli, "load_project_dotenv", Mock())
    monkeypatch.setattr(cli, "load_config", Mock(return_value={}))
    monkeypatch.setattr(cli, "Mentor", Mock())
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("uvicorn.run", run)
    assert cli.main(["serve"]) == 2
    run.assert_not_called()
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
