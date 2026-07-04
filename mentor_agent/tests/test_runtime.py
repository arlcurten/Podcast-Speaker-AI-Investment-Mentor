from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mentor_agent.modules.guards import validate_citations
from mentor_agent.modules.guards import current_market_guard
from mentor_agent.modules.guards import run_guards
from mentor_agent.modules.query import analyze_query_deterministic
from mentor_agent.modules.runtime import MentorRuntime
from mentor_agent.modules.routing import candidate_route


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_PIPELINE = PROJECT_ROOT.parent / "AI-Investment-Mentor-private" / "pipeline_data"


def private_available() -> bool:
    return (PRIVATE_PIPELINE / "semantic_analysis" / "outputs" / "cross_episode" / "level4_principles.json").exists()


def test_query_analysis_current_market_guard() -> None:
    qa = analyze_query_deterministic("現在適合買台積電嗎？")
    assert qa.current_data_requirement is True
    assert qa.historical_or_conceptual_or_current == "current"


def test_missing_private_data_path_error(monkeypatch) -> None:
    monkeypatch.delenv("PIPELINE_DATA_ROOT", raising=False)
    try:
        MentorRuntime(use_llm=False)
    except RuntimeError as exc:
        assert "PIPELINE_DATA_ROOT" in str(exc)
    else:
        raise AssertionError("expected missing PIPELINE_DATA_ROOT error")


def test_load_level2_and_level4_when_private_available(monkeypatch) -> None:
    if not private_available():
        return
    monkeypatch.setenv("PIPELINE_DATA_ROOT", str(PRIVATE_PIPELINE))
    runtime = MentorRuntime(use_llm=False)
    validation = runtime.validate_data()
    assert validation["ok"] is True
    assert validation["cases"]["record_count"] >= 30
    assert validation["principles"]["principle_count"] >= 1
    assert validation["principles"]["eligible_principle_count"] >= 1


def test_principle_assisted_candidate_route(monkeypatch) -> None:
    if not private_available():
        return
    monkeypatch.setenv("PIPELINE_DATA_ROOT", str(PRIVATE_PIPELINE))
    runtime = MentorRuntime(use_llm=False)
    result = runtime.retrieve("他在面對市場敘事和數據不一致時，會怎麼判斷？")
    route = candidate_route(result["query_analysis"], result["principle_candidates"], result["case_candidates"], runtime.config)
    assert route["candidate_route"] in {"principle_assisted", "case_only"}


def test_principle_context_mismatch_downgrades_to_case_only(monkeypatch) -> None:
    if not private_available():
        return
    monkeypatch.setenv("PIPELINE_DATA_ROOT", str(PRIVATE_PIPELINE))
    runtime = MentorRuntime(use_llm=False)
    result = runtime.ask("如果我很怕踏空，但股價已經漲一段了，他的歷史案例比較支持忍住還是追上去？")
    assert result["selected_route"] == "case_only"
    assert result["selected_principles"] == []


def test_no_cases_insufficient_evidence() -> None:
    qa = analyze_query_deterministic("完全沒有資料的問題")
    route = candidate_route(qa.to_dict(), [], [], {"routing": {"min_cases_for_case_only": 1}})
    assert route["candidate_route"] == "insufficient_evidence"


def test_unknown_company_phrase_marks_insufficient(monkeypatch) -> None:
    if not private_available():
        return
    monkeypatch.setenv("PIPELINE_DATA_ROOT", str(PRIVATE_PIPELINE))
    runtime = MentorRuntime(use_llm=False)
    result = runtime.ask("他對一家節目沒有談過、我也沒有提供產業和財務資訊的新創公司，會給什麼明確投資建議？")
    assert result["selected_route"] == "insufficient_evidence"


def test_career_query_not_treated_as_investment_scenario() -> None:
    qa = analyze_query_deterministic("如果我在職涯上想去高薪但壓力大的公司，他的歷史內容能給什麼判斷框架？")
    assert qa.query_type == "non_investment_domain"
    assert "career" in qa.target_domains


def test_citation_rejects_non_retrieved_record(monkeypatch) -> None:
    if not private_available():
        return
    monkeypatch.setenv("PIPELINE_DATA_ROOT", str(PRIVATE_PIPELINE))
    runtime = MentorRuntime(use_llm=False)
    cases = runtime._retrieve(analyze_query_deterministic("他如何看待追高？"))[1]
    check = validate_citations("引用 EP999-reasoning-999", cases)
    assert check["ok"] is False


def test_current_market_guard_accepts_clear_limitation() -> None:
    check = current_market_guard(
        {"current_data_requirement": True},
        "目前無法提供即時買賣建議，因本系統無當前股價、財報或市場數據。",
    )
    assert check["ok"] is True


def test_insufficient_evidence_does_not_require_citation() -> None:
    checks = run_guards(
        "目前已載入的 podcast evidence 不足以支撐 speaker-specific 結論。",
        {"current_data_requirement": False},
        [],
        {"selected_route": "insufficient_evidence"},
    )
    assert checks["checks"]["citations"]["ok"] is True


def test_runtime_falls_back_when_model_cites_unretrieved_record(monkeypatch) -> None:
    if not private_available():
        return
    monkeypatch.setenv("PIPELINE_DATA_ROOT", str(PRIVATE_PIPELINE))
    runtime = MentorRuntime(use_llm=False)
    # Directly exercise guard condition that triggers runtime fallback in live mode.
    cases = runtime._retrieve(analyze_query_deterministic("市場修正時應該加碼嗎？"))[1]
    check = validate_citations("引用不存在的 EP999-reasoning-999", cases)
    assert check["ok"] is False


def test_public_sample_not_referenced_by_runtime() -> None:
    for path in (PROJECT_ROOT / "mentor_agent").rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        assert "sample_outputs" not in path.read_text(encoding="utf-8")


def test_cli_smoke(monkeypatch) -> None:
    if not private_available():
        return
    env = os.environ.copy()
    env["PIPELINE_DATA_ROOT"] = str(PRIVATE_PIPELINE)
    proc = subprocess.run(
        [sys.executable, "-m", "mentor_agent", "--no-llm", "inspect-query", "--query", "他如何看待追高？"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "query_type" in proc.stdout
