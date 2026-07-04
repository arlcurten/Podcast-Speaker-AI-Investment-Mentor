from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict


Level3Strategy = Literal["principle_extraction", "light_principle_grouping", "bypass"]
PrincipleType = Literal["recurring_principle", "case_derived_hypothesis", "conditional_pattern", "exception_pattern"]
RuntimeMode = Literal["principle_guided", "principle_assisted", "case_only_fallback", "not_eligible"]
GenericnessStatus = Literal["specific", "partially_specific", "too_generic"]
GeneralizationScope = Literal["episode_local", "cross_episode_candidate"]
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


class PrincipleConfidence(TypedDict):
    evidence_strength: Literal["low", "medium", "high"]
    evidence_independence: Literal["low", "medium", "high"]
    speaker_specificity: Literal["low", "medium", "high"]
    condition_clarity: Literal["low", "medium", "high"]
    contradiction_severity: Literal["none", "minor", "major"]
    cross_episode_consistency: Literal["not_applicable", "low", "medium", "high"]
    time_period_diversity: Literal["not_applicable", "low", "medium", "high"]
    overall: Literal["low", "medium", "high"]


class DecisionTradeoff(TypedDict):
    option_a: str
    option_b: str
    preferred_option: str
    reason_for_preference: str
    cost_of_preference: str
    switch_conditions: list[str]


class ProvenanceStats(TypedDict, total=False):
    supporting_record_count: int
    supporting_episode_ids: list[str]
    supporting_episode_count: int
    unique_level1_thread_count: int
    unique_level1_threads: list[str]
    distinct_decision_context_count: int
    source_region_count: int
    independent_source_context_count: int
    overlapping_or_duplicate_source_regions: int


class Level3ProvisionalPrinciple(TypedDict):
    schema_version: str
    principle_id: str
    episode_id: str
    name: str
    domains: list[str]
    decision_context: str
    core_preference: str
    reasoning_steps: list[str]
    preferred_actions: list[str]
    conditions: list[str]
    exceptions: list[str]
    supporting_records: list[str]
    counterexamples: list[str]
    contradictions: list[str]
    principle_type: PrincipleType
    decision_tradeoff: DecisionTradeoff
    confidence: PrincipleConfidence
    generalization_scope: Literal["episode_local"]
    speaker_specificity: GenericnessStatus
    genericness_status: GenericnessStatus
    genericness_reasons: list[str]
    distinctive_behavior: list[str]
    eligible_for_level4: bool
    eligible_for_online_reasoning: Literal["false", "limited", "principle_assisted_only"]
    eligibility_reasons: list[str]
    runtime_mode_recommendation: RuntimeMode
    provenance_stats: ProvenanceStats
    provisional: bool
    extraction_strategy: Level3Strategy
    evidence_summary: str


class Level4CrossEpisodePrinciple(TypedDict):
    schema_version: str
    principle_id: str
    name: str
    domains: list[str]
    decision_context: str
    core_preference: str
    reasoning_steps: list[str]
    preferred_actions: list[str]
    conditions: list[str]
    exceptions: list[str]
    supporting_records: list[str]
    supporting_episode_ids: list[str]
    supporting_episode_count: int
    counterexamples: list[str]
    contradictions: list[str]
    principle_evolution: list[str]
    principle_type: PrincipleType
    decision_tradeoff: DecisionTradeoff
    confidence: PrincipleConfidence
    generalization_scope: Literal["cross_episode_candidate"]
    speaker_specificity: GenericnessStatus
    genericness_status: GenericnessStatus
    genericness_reasons: list[str]
    distinctive_behavior: list[str]
    eligible_for_level4: bool
    eligible_for_online_reasoning: Literal["false", "limited", "principle_assisted_only", "true"]
    eligibility_reasons: list[str]
    runtime_mode_recommendation: RuntimeMode
    provenance_stats: ProvenanceStats
    derived_from: list[str]
    evidence_summary: str


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
    confidence: PrincipleConfidence
    runtime_mode_recommendation: RuntimeMode


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
    principle_type: PrincipleType | None = None
    generalization_scope: GeneralizationScope | None = None
    confidence: PrincipleConfidence | None = None
    runtime_mode_recommendation: RuntimeMode | None = None
    decision_tradeoff: DecisionTradeoff | None = None
    distinctive_behavior: list[str] = field(default_factory=list)
    provenance_stats: ProvenanceStats | None = None


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
    confidence: PrincipleConfidence | None = None
    runtime_mode_recommendation: RuntimeMode | None = None


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
