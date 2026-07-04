from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict


Level3Strategy = Literal["principle_extraction", "light_principle_grouping", "bypass"]
QueryType = Literal[
    "investment_decision",
    "market_analysis",
    "risk_management",
    "behavioral_investing",
    "business",
    "career",
    "personal",
    "general",
]


class SourceRecordRef(TypedDict):
    episode_id: str
    record_id: str
    start: float
    end: float


class Level3ProvisionalPrinciple(TypedDict):
    principle_id: str
    episode_id: str
    name: str
    decision_context: str
    core_preference: str
    reasoning_steps: list[str]
    preferred_actions: list[str]
    conditions: list[str]
    exceptions: list[str]
    supporting_records: list[SourceRecordRef]
    counterexamples: list[SourceRecordRef]
    confidence: float
    provisional: bool


class Level4CrossEpisodePrinciple(TypedDict):
    principle_id: str
    name: str
    domains: list[str]
    decision_context: str
    core_preference: str
    reasoning_steps: list[str]
    preferred_actions: list[str]
    conditions: list[str]
    exceptions: list[str]
    supporting_records: list[SourceRecordRef]
    supporting_episode_count: int
    counterexamples: list[SourceRecordRef]
    contradictions: list[str]
    principle_evolution: list[str]
    confidence: float
    derived_from: list[str]


class OnlineApplicabilityAssessment(TypedDict):
    query_type: QueryType
    scenario_facts: list[str]
    candidate_principles: list[str]
    applicable_principles: list[str]
    matched_conditions: list[str]
    missing_information: list[str]
    exceptions_triggered: list[str]
    conflicting_evidence: list[SourceRecordRef]
    supporting_cases: list[SourceRecordRef]
    need_external_tools: bool
    provisional_conclusion: str
    confidence: float


@dataclass(frozen=True)
class QueryAnalysis:
    query: str
    query_type: QueryType
    explicit_entities: list[str] = field(default_factory=list)
    requested_decision: str | None = None
    requires_current_information: bool = False


@dataclass(frozen=True)
class PrincipleCandidate:
    principle_id: str
    score: float
    reason: str


@dataclass(frozen=True)
class CaseEvidence:
    episode_id: str
    record_id: str
    start: float
    end: float
    relationship: Literal["supporting", "conflicting", "similar"]
    summary: str


@dataclass(frozen=True)
class ReasoningAnswer:
    answer: str
    citations: list[SourceRecordRef]
    missing_information: list[str]
    confidence: float


class PrincipleStore(Protocol):
    def retrieve_principles(self, query_analysis: QueryAnalysis) -> list[PrincipleCandidate]:
        raise NotImplementedError


class CaseStore(Protocol):
    def retrieve_cases(
        self,
        query_analysis: QueryAnalysis,
        selected_principles: list[PrincipleCandidate],
    ) -> list[CaseEvidence]:
        raise NotImplementedError


def analyze_query(query: str) -> QueryAnalysis:
    raise NotImplementedError("Reasoning Mentor query analysis is not implemented yet.")


def retrieve_principles(query_analysis: QueryAnalysis) -> list[PrincipleCandidate]:
    raise NotImplementedError("Level 4 principle retrieval is not implemented yet.")


def retrieve_cases(
    query_analysis: QueryAnalysis,
    selected_principles: list[PrincipleCandidate],
) -> list[CaseEvidence]:
    raise NotImplementedError("Level 2 case retrieval for reasoning is not implemented yet.")


def assess_applicability(
    query_analysis: QueryAnalysis,
    principles: list[PrincipleCandidate],
    cases: list[CaseEvidence],
) -> OnlineApplicabilityAssessment:
    raise NotImplementedError("Online applicability assessment is not implemented yet.")


def generate_answer(
    applicability_assessment: OnlineApplicabilityAssessment,
    principles: list[PrincipleCandidate],
    cases: list[CaseEvidence],
) -> ReasoningAnswer:
    raise NotImplementedError("Reasoning Mentor answer generation is not implemented yet.")


def validate_citations(answer: ReasoningAnswer, cases: list[CaseEvidence]) -> bool:
    raise NotImplementedError("Reasoning Mentor citation validation is not implemented yet.")
