import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from knowledge.codex_backend import CodexBackend
from knowledge.tests.test_case_release import prepared, publish
from knowledge.tests.test_pipeline import cfg
from mentor_agent import __main__ as cli
from mentor_agent.retrieval import KnowledgeIndex
from mentor_agent.tests.test_runtime import mentor_setup
from transcription.common import PROJECT_ROOT, atomic_write_json, read_json


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


@pytest.mark.parametrize("arguments, message", [
    (["read-case"], "--record-id"),
    (["read-case", "--record-id", ""], "must not be blank"),
    (["read-case", "--record-id", "  "], "must not be blank"),
    (["read-case", "--record-id", "SYN:R1", "--record-id", "SYN:R1"], "duplicate"),
])
def test_read_case_requires_unique_nonblank_ids_before_loading(monkeypatch, capsys, arguments, message):
    loader = Mock()
    monkeypatch.setattr(cli, "load_config", loader)
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    loader.assert_not_called()


@pytest.fixture(params=["reviewed", "scoped"])
def read_case_setup(request, monkeypatch, tmp_path):
    forbidden = Mock(side_effect=AssertionError("No model calls or retrieval during case reads"))
    monkeypatch.setattr(CodexBackend, "complete", forbidden)
    monkeypatch.setattr("mentor_agent.runtime.OpenRouter", forbidden)
    monkeypatch.setattr(cli.Mentor, "ask", forbidden)
    monkeypatch.setattr(KnowledgeIndex, "select", forbidden)
    monkeypatch.setattr(KnowledgeIndex, "search", forbidden)
    monkeypatch.setattr(cli, "load_project_dotenv", Mock())
    for name in ("MENTOR_RELEASE", "MENTOR_MODEL_ID", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    if request.param == "reviewed":
        settings = request.getfixturevalue("mentor_setup")
        release_path = Path(settings["release"])
        review_path = Path(read_json(release_path)["review_path"])
    else:
        synthetic = request.getfixturevalue("prepared")
        publish(synthetic)
        settings = yaml.safe_load((Path(cli.__file__).parent / "config/default.yaml").read_text())
        release_path, review_path = synthetic["output"], synthetic["review"]
        settings["release"] = str(release_path)
    config = tmp_path / "read-case.yaml"
    config.write_text(yaml.safe_dump(settings))
    mentor = cli.Mentor(settings)
    assert mentor.backend is None
    yield {"args": ["--config", str(config), "read-case"], "mentor": mentor,
           "review": review_path, "release": release_path, "kind": request.param}
    forbidden.assert_not_called()


def test_read_case_returns_only_exact_selected_cases_in_order(read_case_setup, capsys):
    setup = read_case_setup
    mentor = setup["mentor"]
    ids = list(reversed(mentor.index.cases))[:2]
    arguments = [arg for rid in ids for arg in ("--record-id", rid)]
    assert cli.main(setup["args"] + arguments) == 0
    captured = capsys.readouterr()
    assert not captured.err
    result = json.loads(captured.out)
    assert set(result) == {"command", "cases", "citations", "evidence_review_limits", "release_sha256", "model_calls"}
    assert result["command"] == "read-case" and result["model_calls"] == 0
    assert result["release_sha256"] == mentor.release_hash
    assert result["cases"] == [mentor.index.cases[rid] for rid in ids]
    assert result["citations"] == [cli.citation(mentor.index.cases[rid]) for rid in ids]
    assert result["evidence_review_limits"] == cli.evidence_review_limits(result["cases"])
    if setup["kind"] == "scoped":
        case = result["cases"][0]
        assert case["source_assessment"]["eligible_for_online_reasoning"] is False
        assert case["scoped_review"]["source_acceptance"] == "not_granted"
        assert case["scoped_review"]["issue_dispositions"]
        assert case["scoped_review"]["omission_dispositions"]
        assert case["scoped_review"]["global_dispositions"]
        assert {note["authority"] for note in case["supplementary_notes"]} == {
            "model_assessment_not_source", "reviewer_commentary_not_source"}
        assert result["evidence_review_limits"][0]["limitations"]


def test_read_case_unknown_or_unapproved_id_never_emits_partial_results(read_case_setup, tmp_path, capsys):
    setup = read_case_setup
    known = next(iter(setup["mentor"].index.cases))
    unknown = "SYN2:R2" if setup["kind"] == "scoped" else "SYN9:UNKNOWN"
    output = tmp_path / "must-not-exist.json"
    with pytest.raises(SystemExit) as exc:
        cli.main(setup["args"] + ["--record-id", known, "--record-id", unknown, "--output", str(output)])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert not captured.out and "IDs not in the reviewed release" in captured.err
    assert unknown in captured.err
    assert not output.exists()


def test_read_case_private_output_is_optional_and_immutable(read_case_setup, tmp_path, capsys):
    setup = read_case_setup
    output = tmp_path / "case-read.json"
    args = setup["args"] + ["--record-id", next(iter(setup["mentor"].index.cases)), "--output", str(output)]
    assert cli.main(args) == 0
    assert read_json(output) == json.loads(capsys.readouterr().out)
    original = output.read_bytes()
    assert cli.main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out and "FileExistsError" in captured.err
    assert output.read_bytes() == original


@pytest.mark.parametrize("kind", ["relative", "public", "symlink_to_public"])
def test_read_case_output_cannot_enter_public_tree(read_case_setup, tmp_path, monkeypatch, capsys, kind):
    if kind == "relative":
        output = Path("case-read.json")
    elif kind == "public":
        output = PROJECT_ROOT / "case-read.json"
    else:
        link = tmp_path / "public-link"
        link.symlink_to(PROJECT_ROOT, target_is_directory=True)
        output = link / "case-read.json"
    writer = Mock(side_effect=AssertionError("Must validate the private boundary before writing"))
    monkeypatch.setattr(cli, "atomic_write_json", writer)
    rid = next(iter(read_case_setup["mentor"].index.cases))
    assert cli.main(read_case_setup["args"] + ["--record-id", rid, "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert not captured.out and "read-case failed" in captured.err
    writer.assert_not_called()


@pytest.mark.parametrize("when", ["before_load", "during_preparation"])
def test_read_case_review_revocation_suppresses_all_output(read_case_setup, tmp_path, monkeypatch, capsys, when):
    setup = read_case_setup
    def revoke():
        review = read_json(setup["review"])
        review["decision"] = "revoked"
        atomic_write_json(setup["review"], review)
    if when == "before_load":
        revoke()
    else:
        original = cli.citation
        def revoke_after_citation(case):
            result = original(case)
            revoke()
            return result
        monkeypatch.setattr(cli, "citation", revoke_after_citation)
    output = tmp_path / "revoked-output.json"
    rid = next(iter(setup["mentor"].index.cases))
    assert cli.main(setup["args"] + ["--record-id", rid, "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert not captured.out and "source/review pins" in captured.err
    assert str(tmp_path) not in captured.err and "OPENROUTER_API_KEY" not in captured.err
    assert not output.exists()
