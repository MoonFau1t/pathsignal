from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.source_monitoring.entity_prioritization_models import PriorityTier
from src.source_monitoring.models import _json_ready


SOURCE_ROLE_ONTOLOGY_VERSION = "source_role_ontology_v1"
ENTITY_KIND_SOURCE_ROLE_POLICY_VERSION = "entity_kind_source_role_policy_v1"
SOURCE_DISCOVERY_BUDGET_POLICY_VERSION = "source_discovery_budget_policy_v1"
SOURCE_DISCOVERY_PLAN_RANKING_POLICY_VERSION = (
    "source_discovery_plan_ranking_policy_v1_1"
)
SOURCE_DISCOVERY_QUERY_TEMPLATE_VERSION = "source_discovery_query_template_v1"
SOURCE_DISCOVERY_CLASSIFIER_PROMPT_VERSION = (
    "source_discovery_classifier_prompt_v2"
)
SOURCE_DISCOVERY_URL_NORMALIZATION_POLICY_VERSION = (
    "source_discovery_url_normalization_policy_v1"
)
SOURCE_DISCOVERY_PRECLASSIFICATION_POLICY_VERSION = (
    "source_discovery_preclassification_policy_v1"
)

SOURCE_ROLE_DEFINITION_SCHEMA_VERSION = "source_role_definition_v1"
SOURCE_DISCOVERY_BUDGET_SCHEMA_VERSION = "source_discovery_budget_v1"
SOURCE_DISCOVERY_PLAN_SCHEMA_VERSION = "source_discovery_plan_v1"
SOURCE_DISCOVERY_PLANNING_RESULT_SCHEMA_VERSION = (
    "source_discovery_planning_result_v1"
)
SOURCE_DISCOVERY_EXECUTION_SCHEMA_VERSION = "source_discovery_execution_v1"
SOURCE_DISCOVERY_EVIDENCE_SCHEMA_VERSION = "source_discovery_evidence_v1"
CANDIDATE_SOURCE_SCHEMA_VERSION = "candidate_source_v1"
REJECTED_CANDIDATE_SOURCE_SCHEMA_VERSION = "rejected_candidate_source_v1"
SOURCE_DISCOVERY_RESULT_SCHEMA_VERSION = "source_discovery_result_v1"


class SourceRole(str, Enum):
    OFFICIAL_HOMEPAGE = "official_homepage"
    NEWSROOM = "newsroom"
    PRESS_RELEASES = "press_releases"
    INSIGHTS = "insights"
    RESEARCH_PUBLICATIONS = "research_publications"
    CAREERS = "careers"
    PORTFOLIO = "portfolio"
    TRANSACTIONS = "transactions"
    POLICY_UPDATES = "policy_updates"
    REPORTS_OR_DATA = "reports_or_data"
    EVENTS_OR_PROGRAMS = "events_or_programs"
    BLOG = "blog"
    OTHER_OFFICIAL_SECTION = "other_official_section"


class SourceFormatHint(str, Enum):
    HTML_PAGE = "html_page"
    RSS_CANDIDATE = "rss_candidate"
    ATOM_CANDIDATE = "atom_candidate"
    UNKNOWN = "unknown"


class DiscoveryStrategy(str, Enum):
    DOMAIN_FIRST = "domain_first"
    NAME_FIRST = "name_first"
    IDENTITY_RESOLUTION = "identity_resolution"


class DiscoveryPlanStatus(str, Enum):
    EXECUTABLE = "executable"
    DEFERRED_BUDGET_LIMIT = "deferred_budget_limit"
    DEFERRED_LOW_RELEVANCE = "deferred_low_relevance"
    DEFERRED_MISSING_LANGUAGE_IDENTITY = "deferred_missing_language_identity"
    DEFERRED_UNRESOLVED_DOMAIN = "deferred_unresolved_domain_requires_identity_first"
    DEFERRED_DUPLICATE_EQUIVALENT_QUERY = "deferred_duplicate_equivalent_query"
    DEFERRED_LOW_PRIORITY_LOW_READINESS = "deferred_low_priority_and_low_readiness"
    DEFERRED_UNSUPPORTED_ROLE = "deferred_unsupported_role_for_entity_kind"


class DiscoveryExecutionStatus(str, Enum):
    PENDING = "pending"
    EXECUTED = "executed"
    EXECUTED_NO_RESULTS = "executed_no_results"
    FAILED = "failed"
    DEFERRED_BY_LIMIT = "deferred_by_limit"
    SKIPPED_CACHE_HIT = "skipped_cache_hit"


class CandidateSourceStatus(str, Enum):
    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class CandidateOfficialityStatus(str, Enum):
    PROBABLY_OFFICIAL = "probably_official"
    OFFICIAL_DOMAIN_MATCH = "official_domain_match"
    UNRESOLVED = "unresolved"
    THIRD_PARTY = "third_party"
    REJECTED = "rejected"


class CandidateDecision(str, Enum):
    ACCEPT = "accept"
    NEEDS_REVIEW = "needs_review"
    REJECT = "reject"


@dataclass(frozen=True)
class SourceRoleDefinition:
    source_role: SourceRole
    english_aliases: tuple[str, ...]
    chinese_aliases: tuple[str, ...]
    description: str
    applicable_primary_entity_kinds: tuple[str, ...]
    query_terms_by_language: dict[str, tuple[str, ...]]
    url_path_hints: tuple[str, ...]
    ontology_version: str = SOURCE_ROLE_ONTOLOGY_VERSION
    schema_version: str = SOURCE_ROLE_DEFINITION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class SourceDiscoveryBudget:
    entity_id: str
    priority_tier: PriorityTier
    maximum_plan_count: int
    allocated_plan_count: int
    readiness_score: int
    needs_domain_verification: bool
    low_evidence_readiness: bool
    probable_official_domain: str | None
    rationale: str
    policy_version: str = SOURCE_DISCOVERY_BUDGET_POLICY_VERSION
    schema_version: str = SOURCE_DISCOVERY_BUDGET_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceDiscoveryBudget":
        return cls(
            entity_id=str(payload["entity_id"]),
            priority_tier=PriorityTier(str(payload["priority_tier"])),
            maximum_plan_count=int(payload.get("maximum_plan_count", 0)),
            allocated_plan_count=int(payload.get("allocated_plan_count", 0)),
            readiness_score=int(payload.get("readiness_score", 0)),
            needs_domain_verification=bool(
                payload.get("needs_domain_verification", False)
            ),
            low_evidence_readiness=bool(payload.get("low_evidence_readiness", False)),
            probable_official_domain=payload.get("probable_official_domain"),
            rationale=str(payload.get("rationale", "")),
            policy_version=str(
                payload.get("policy_version", SOURCE_DISCOVERY_BUDGET_POLICY_VERSION)
            ),
            schema_version=str(
                payload.get("schema_version", SOURCE_DISCOVERY_BUDGET_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class SourceDiscoveryPlan:
    plan_id: str
    entity_id: str
    canonical_name: str
    source_role: SourceRole
    strategy: DiscoveryStrategy
    query_language: str
    query: str
    domain_constraint: str | None
    max_result_count: int
    candidate_plan_rank: int
    status: DiscoveryPlanStatus
    phase3_tier: PriorityTier
    budget_provenance: dict[str, Any]
    supporting_information_need_ids: tuple[str, ...]
    supporting_target_career_path_ids: tuple[str, ...]
    query_template_version: str = SOURCE_DISCOVERY_QUERY_TEMPLATE_VERSION
    deferral_reason: str | None = None
    ranking_score: float = 0.0
    schema_version: str = SOURCE_DISCOVERY_PLAN_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceDiscoveryPlan":
        return cls(
            plan_id=str(payload["plan_id"]),
            entity_id=str(payload["entity_id"]),
            canonical_name=str(payload.get("canonical_name", "")),
            source_role=SourceRole(str(payload["source_role"])),
            strategy=DiscoveryStrategy(str(payload["strategy"])),
            query_language=str(payload["query_language"]),
            query=str(payload["query"]),
            domain_constraint=payload.get("domain_constraint"),
            max_result_count=int(payload.get("max_result_count", 0)),
            candidate_plan_rank=int(payload.get("candidate_plan_rank", 0)),
            status=DiscoveryPlanStatus(str(payload["status"])),
            phase3_tier=PriorityTier(str(payload["phase3_tier"])),
            budget_provenance=_dict(payload.get("budget_provenance")),
            supporting_information_need_ids=_string_tuple(
                payload.get("supporting_information_need_ids")
            ),
            supporting_target_career_path_ids=_string_tuple(
                payload.get("supporting_target_career_path_ids")
            ),
            query_template_version=str(
                payload.get(
                    "query_template_version",
                    SOURCE_DISCOVERY_QUERY_TEMPLATE_VERSION,
                )
            ),
            deferral_reason=payload.get("deferral_reason"),
            ranking_score=float(payload.get("ranking_score", 0.0)),
            schema_version=str(
                payload.get("schema_version", SOURCE_DISCOVERY_PLAN_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class SourceDiscoveryPlanningResult:
    planning_result_hash: str
    budgets: tuple[SourceDiscoveryBudget, ...]
    plans: tuple[SourceDiscoveryPlan, ...]
    executable_plan_ids: tuple[str, ...]
    deferred_plan_ids: tuple[str, ...]
    deferred_entity_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]
    role_ontology_version: str
    entity_kind_role_policy_version: str
    budget_policy_version: str
    plan_ranking_policy_version: str
    query_template_version: str
    input_fingerprint: str
    output_hash: str
    schema_version: str = SOURCE_DISCOVERY_PLANNING_RESULT_SCHEMA_VERSION
    generation_mode: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceDiscoveryPlanningResult":
        return cls(
            planning_result_hash=str(payload["planning_result_hash"]),
            budgets=tuple(
                SourceDiscoveryBudget.from_dict(item)
                for item in _dict_items(payload.get("budgets"))
            ),
            plans=tuple(
                SourceDiscoveryPlan.from_dict(item)
                for item in _dict_items(payload.get("plans"))
            ),
            executable_plan_ids=_string_tuple(payload.get("executable_plan_ids")),
            deferred_plan_ids=_string_tuple(payload.get("deferred_plan_ids")),
            deferred_entity_ids=_string_tuple(payload.get("deferred_entity_ids")),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            role_ontology_version=str(
                payload.get("role_ontology_version", SOURCE_ROLE_ONTOLOGY_VERSION)
            ),
            entity_kind_role_policy_version=str(
                payload.get(
                    "entity_kind_role_policy_version",
                    ENTITY_KIND_SOURCE_ROLE_POLICY_VERSION,
                )
            ),
            budget_policy_version=str(
                payload.get(
                    "budget_policy_version",
                    SOURCE_DISCOVERY_BUDGET_POLICY_VERSION,
                )
            ),
            plan_ranking_policy_version=str(
                payload.get(
                    "plan_ranking_policy_version",
                    SOURCE_DISCOVERY_PLAN_RANKING_POLICY_VERSION,
                )
            ),
            query_template_version=str(
                payload.get(
                    "query_template_version",
                    SOURCE_DISCOVERY_QUERY_TEMPLATE_VERSION,
                )
            ),
            input_fingerprint=str(payload["input_fingerprint"]),
            output_hash=str(payload["output_hash"]),
            schema_version=str(
                payload.get(
                    "schema_version",
                    SOURCE_DISCOVERY_PLANNING_RESULT_SCHEMA_VERSION,
                )
            ),
            generation_mode=str(payload.get("generation_mode", "loaded_from_cache")),
        )


@dataclass(frozen=True)
class SourceDiscoveryExecution:
    execution_id: str
    plan_id: str
    entity_id: str
    status: DiscoveryExecutionStatus
    provider: str
    query: str
    result_count: int
    checkpoint_path: str | None = None
    diagnostics: tuple[str, ...] = ()
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SOURCE_DISCOVERY_EXECUTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceDiscoveryExecution":
        return cls(
            execution_id=str(payload["execution_id"]),
            plan_id=str(payload["plan_id"]),
            entity_id=str(payload["entity_id"]),
            status=DiscoveryExecutionStatus(str(payload["status"])),
            provider=str(payload.get("provider", "")),
            query=str(payload.get("query", "")),
            result_count=int(payload.get("result_count", 0)),
            checkpoint_path=payload.get("checkpoint_path"),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            raw_metadata=_dict(payload.get("raw_metadata")),
            schema_version=str(
                payload.get(
                    "schema_version",
                    SOURCE_DISCOVERY_EXECUTION_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class SourceDiscoveryEvidence:
    evidence_id: str
    execution_id: str
    plan_id: str
    entity_id: str
    result_rank: int
    title: str
    url: str
    normalized_url: str
    root_domain: str
    snippet: str
    language: str
    provider: str
    raw_metadata: dict[str, Any]
    retrieved_at: str
    schema_version: str = SOURCE_DISCOVERY_EVIDENCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceDiscoveryEvidence":
        return cls(
            evidence_id=str(payload["evidence_id"]),
            execution_id=str(payload["execution_id"]),
            plan_id=str(payload["plan_id"]),
            entity_id=str(payload["entity_id"]),
            result_rank=int(payload.get("result_rank", 0)),
            title=str(payload.get("title", "")),
            url=str(payload.get("url", "")),
            normalized_url=str(payload.get("normalized_url", "")),
            root_domain=str(payload.get("root_domain", "")),
            snippet=str(payload.get("snippet", "")),
            language=str(payload.get("language", "")),
            provider=str(payload.get("provider", "")),
            raw_metadata=_dict(payload.get("raw_metadata")),
            retrieved_at=str(payload.get("retrieved_at", "")),
            schema_version=str(
                payload.get(
                    "schema_version",
                    SOURCE_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class CandidateSource:
    candidate_source_id: str
    entity_id: str
    canonical_url: str
    normalized_url: str
    root_domain: str
    source_role: SourceRole
    source_format_hint: SourceFormatHint
    language: str
    candidate_officiality_status: CandidateOfficialityStatus
    discovery_methods: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    confidence: float
    rationale: str
    review_flags: tuple[str, ...]
    provenance: dict[str, Any]
    schema_version: str = CANDIDATE_SOURCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CandidateSource":
        return cls(
            candidate_source_id=str(payload["candidate_source_id"]),
            entity_id=str(payload["entity_id"]),
            canonical_url=str(payload.get("canonical_url", "")),
            normalized_url=str(payload.get("normalized_url", "")),
            root_domain=str(payload.get("root_domain", "")),
            source_role=SourceRole(str(payload["source_role"])),
            source_format_hint=SourceFormatHint(str(payload["source_format_hint"])),
            language=str(payload.get("language", "")),
            candidate_officiality_status=CandidateOfficialityStatus(
                str(payload["candidate_officiality_status"])
            ),
            discovery_methods=_string_tuple(payload.get("discovery_methods")),
            supporting_evidence_ids=_string_tuple(
                payload.get("supporting_evidence_ids")
            ),
            confidence=float(payload.get("confidence", 0.0)),
            rationale=str(payload.get("rationale", "")),
            review_flags=_string_tuple(payload.get("review_flags")),
            provenance=_dict(payload.get("provenance")),
            schema_version=str(
                payload.get("schema_version", CANDIDATE_SOURCE_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class RejectedCandidateSource:
    rejected_candidate_id: str
    evidence_id: str | None
    provisional_candidate_id: str | None
    entity_id: str
    url: str
    rejection_reason: str
    diagnostics: tuple[str, ...]
    classifier_index: int | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: str = REJECTED_CANDIDATE_SOURCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RejectedCandidateSource":
        return cls(
            rejected_candidate_id=str(payload["rejected_candidate_id"]),
            evidence_id=payload.get("evidence_id"),
            provisional_candidate_id=payload.get("provisional_candidate_id"),
            entity_id=str(payload.get("entity_id", "")),
            url=str(payload.get("url", "")),
            rejection_reason=str(payload.get("rejection_reason", "")),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            classifier_index=payload.get("classifier_index"),
            provenance=_dict(payload.get("provenance")),
            schema_version=str(
                payload.get(
                    "schema_version",
                    REJECTED_CANDIDATE_SOURCE_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class SourceDiscoveryResult:
    planning_result_hash: str
    budgets: tuple[SourceDiscoveryBudget, ...]
    plans: tuple[SourceDiscoveryPlan, ...]
    executions: tuple[SourceDiscoveryExecution, ...]
    evidence: tuple[SourceDiscoveryEvidence, ...]
    candidate_sources: tuple[CandidateSource, ...]
    rejected_candidates: tuple[RejectedCandidateSource, ...]
    needs_review_candidates: tuple[CandidateSource, ...]
    deferred_entity_ids: tuple[str, ...]
    deferred_plan_ids: tuple[str, ...]
    failed_execution_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]
    execution_metadata: dict[str, Any]
    role_ontology_version: str
    budget_policy_version: str
    plan_ranking_policy_version: str
    query_template_version: str
    classifier_prompt_version: str
    url_normalization_policy_version: str
    preclassification_policy_version: str
    input_fingerprint: str
    output_hash: str
    generation_mode: str
    schema_version: str = SOURCE_DISCOVERY_RESULT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceDiscoveryResult":
        return cls(
            planning_result_hash=str(payload["planning_result_hash"]),
            budgets=tuple(
                SourceDiscoveryBudget.from_dict(item)
                for item in _dict_items(payload.get("budgets"))
            ),
            plans=tuple(
                SourceDiscoveryPlan.from_dict(item)
                for item in _dict_items(payload.get("plans"))
            ),
            executions=tuple(
                SourceDiscoveryExecution.from_dict(item)
                for item in _dict_items(payload.get("executions"))
            ),
            evidence=tuple(
                SourceDiscoveryEvidence.from_dict(item)
                for item in _dict_items(payload.get("evidence"))
            ),
            candidate_sources=tuple(
                CandidateSource.from_dict(item)
                for item in _dict_items(payload.get("candidate_sources"))
            ),
            rejected_candidates=tuple(
                RejectedCandidateSource.from_dict(item)
                for item in _dict_items(payload.get("rejected_candidates"))
            ),
            needs_review_candidates=tuple(
                CandidateSource.from_dict(item)
                for item in _dict_items(payload.get("needs_review_candidates"))
            ),
            deferred_entity_ids=_string_tuple(payload.get("deferred_entity_ids")),
            deferred_plan_ids=_string_tuple(payload.get("deferred_plan_ids")),
            failed_execution_ids=_string_tuple(payload.get("failed_execution_ids")),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            execution_metadata=_dict(payload.get("execution_metadata")),
            role_ontology_version=str(
                payload.get("role_ontology_version", SOURCE_ROLE_ONTOLOGY_VERSION)
            ),
            budget_policy_version=str(
                payload.get(
                    "budget_policy_version",
                    SOURCE_DISCOVERY_BUDGET_POLICY_VERSION,
                )
            ),
            plan_ranking_policy_version=str(
                payload.get(
                    "plan_ranking_policy_version",
                    SOURCE_DISCOVERY_PLAN_RANKING_POLICY_VERSION,
                )
            ),
            query_template_version=str(
                payload.get(
                    "query_template_version",
                    SOURCE_DISCOVERY_QUERY_TEMPLATE_VERSION,
                )
            ),
            classifier_prompt_version=str(
                payload.get(
                    "classifier_prompt_version",
                    SOURCE_DISCOVERY_CLASSIFIER_PROMPT_VERSION,
                )
            ),
            url_normalization_policy_version=str(
                payload.get(
                    "url_normalization_policy_version",
                    SOURCE_DISCOVERY_URL_NORMALIZATION_POLICY_VERSION,
                )
            ),
            preclassification_policy_version=str(
                payload.get(
                    "preclassification_policy_version",
                    SOURCE_DISCOVERY_PRECLASSIFICATION_POLICY_VERSION,
                )
            ),
            input_fingerprint=str(payload["input_fingerprint"]),
            output_hash=str(payload["output_hash"]),
            generation_mode=str(payload.get("generation_mode", "loaded_from_cache")),
            schema_version=str(
                payload.get("schema_version", SOURCE_DISCOVERY_RESULT_SCHEMA_VERSION)
            ),
        )


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _dict_items(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if item is not None)
