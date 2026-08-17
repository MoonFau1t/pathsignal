from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.source_monitoring.entity_discovery_models import PrimaryEntityKind
from src.source_monitoring.models import _json_ready


SEMANTIC_SCORE_SCALE_VERSION = "semantic_score_scale_v1"
ENTITY_PRIORITY_SCORING_POLICY_VERSION = "entity_priority_scoring_policy_v1"
EVIDENCE_READINESS_POLICY_VERSION = "evidence_readiness_policy_v1"
COMPACT_CONTEXT_POLICY_VERSION = "compact_entity_context_policy_v1"
PRIORITY_TIER_POLICY_VERSION = "entity_priority_tier_policy_v1"
ENTITY_PRIORITIZATION_PROMPT_VERSION = "entity_prioritization_prompt_v1"

SEMANTIC_DIMENSION_ASSESSMENT_SCHEMA_VERSION = (
    "semantic_dimension_assessment_v1"
)
ENTITY_SEMANTIC_ASSESSMENT_SCHEMA_VERSION = "entity_semantic_assessment_v1"
GEOGRAPHY_ASSESSMENT_SCHEMA_VERSION = "geography_assessment_v1"
EVIDENCE_READINESS_ASSESSMENT_SCHEMA_VERSION = (
    "evidence_readiness_assessment_v1"
)
ENTITY_PRIORITY_ASSESSMENT_SCHEMA_VERSION = "entity_priority_assessment_v1"
REJECTED_ENTITY_PRIORITY_ASSESSMENT_SCHEMA_VERSION = (
    "rejected_entity_priority_assessment_v1"
)
ENTITY_PRIORITIZATION_EXECUTION_METADATA_SCHEMA_VERSION = (
    "entity_prioritization_execution_metadata_v1"
)
ENTITY_PRIORITIZATION_RESULT_SCHEMA_VERSION = "entity_prioritization_result_v1"


class SemanticAssessmentStatus(str, Enum):
    ASSESSED = "assessed"
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class StageRelevanceStatus(str, Enum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class PriorityTier(str, Enum):
    TIER_A_IMMEDIATE = "tier_a_immediate"
    TIER_B_STANDARD = "tier_b_standard"
    TIER_C_SELECTIVE = "tier_c_selective"
    TIER_D_DEFERRED = "tier_d_deferred"


@dataclass(frozen=True)
class SemanticDimensionAssessment:
    score: int | None
    status: SemanticAssessmentStatus
    rationale: str
    supporting_information_need_ids: tuple[str, ...] = ()
    limiting_factors: tuple[str, ...] = ()
    review_flags: tuple[str, ...] = ()
    schema_version: str = SEMANTIC_DIMENSION_ASSESSMENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SemanticDimensionAssessment":
        return cls(
            score=None if payload.get("score") is None else int(payload["score"]),
            status=SemanticAssessmentStatus(
                str(payload.get("status", "assessed"))
            ),
            rationale=str(payload.get("rationale", "")),
            supporting_information_need_ids=_string_tuple(
                payload.get("supporting_information_need_ids")
            ),
            limiting_factors=_string_tuple(payload.get("limiting_factors")),
            review_flags=_string_tuple(payload.get("review_flags")),
            schema_version=str(
                payload.get(
                    "schema_version",
                    SEMANTIC_DIMENSION_ASSESSMENT_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class EntitySemanticAssessment:
    entity_id: str
    path_relevance: SemanticDimensionAssessment
    stage_relevance: SemanticDimensionAssessment
    expected_signal_potential: SemanticDimensionAssessment
    strategic_importance: SemanticDimensionAssessment
    short_overall_rationale: str
    source_assessment_index: int | None = None
    schema_version: str = ENTITY_SEMANTIC_ASSESSMENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntitySemanticAssessment":
        return cls(
            entity_id=str(payload["entity_id"]),
            path_relevance=SemanticDimensionAssessment.from_dict(
                _dict(payload.get("path_relevance"))
            ),
            stage_relevance=SemanticDimensionAssessment.from_dict(
                _dict(payload.get("stage_relevance"))
            ),
            expected_signal_potential=SemanticDimensionAssessment.from_dict(
                _dict(payload.get("expected_signal_potential"))
            ),
            strategic_importance=SemanticDimensionAssessment.from_dict(
                _dict(payload.get("strategic_importance"))
            ),
            short_overall_rationale=str(
                payload.get("short_overall_rationale", "")
            ),
            source_assessment_index=payload.get("source_assessment_index"),
            schema_version=str(
                payload.get(
                    "schema_version",
                    ENTITY_SEMANTIC_ASSESSMENT_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class GeographyAssessment:
    score: int
    matched_preferences: tuple[str, ...]
    conflicts: tuple[str, ...]
    rationale: str
    schema_version: str = GEOGRAPHY_ASSESSMENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GeographyAssessment":
        return cls(
            score=int(payload.get("score", 0)),
            matched_preferences=_string_tuple(payload.get("matched_preferences")),
            conflicts=_string_tuple(payload.get("conflicts")),
            rationale=str(payload.get("rationale", "")),
            schema_version=str(
                payload.get("schema_version", GEOGRAPHY_ASSESSMENT_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class EvidenceReadinessAssessment:
    score: int
    official_domain_status: str
    supporting_evidence_count: int
    distinct_domain_count: int
    multilingual_name_support: bool
    identity_conflict_status: str
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    schema_version: str = EVIDENCE_READINESS_ASSESSMENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceReadinessAssessment":
        return cls(
            score=int(payload.get("score", 0)),
            official_domain_status=str(payload.get("official_domain_status", "")),
            supporting_evidence_count=int(
                payload.get("supporting_evidence_count", 0)
            ),
            distinct_domain_count=int(payload.get("distinct_domain_count", 0)),
            multilingual_name_support=bool(
                payload.get("multilingual_name_support", False)
            ),
            identity_conflict_status=str(
                payload.get("identity_conflict_status", "")
            ),
            strengths=_string_tuple(payload.get("strengths")),
            weaknesses=_string_tuple(payload.get("weaknesses")),
            schema_version=str(
                payload.get(
                    "schema_version",
                    EVIDENCE_READINESS_ASSESSMENT_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class EntityPriorityAssessment:
    priority_assessment_id: str
    entity_id: str
    canonical_name: str
    primary_entity_kind: PrimaryEntityKind
    semantic_assessment: EntitySemanticAssessment
    geography_assessment: GeographyAssessment
    evidence_readiness_assessment: EvidenceReadinessAssessment
    dimension_weights_used: dict[str, float]
    entity_priority_score: int
    evidence_readiness_score: int
    rank: int
    priority_tier: PriorityTier
    rationale: str
    review_flags: tuple[str, ...]
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: str = ENTITY_PRIORITY_ASSESSMENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntityPriorityAssessment":
        return cls(
            priority_assessment_id=str(payload["priority_assessment_id"]),
            entity_id=str(payload["entity_id"]),
            canonical_name=str(payload["canonical_name"]),
            primary_entity_kind=PrimaryEntityKind(
                str(payload["primary_entity_kind"])
            ),
            semantic_assessment=EntitySemanticAssessment.from_dict(
                _dict(payload.get("semantic_assessment"))
            ),
            geography_assessment=GeographyAssessment.from_dict(
                _dict(payload.get("geography_assessment"))
            ),
            evidence_readiness_assessment=EvidenceReadinessAssessment.from_dict(
                _dict(payload.get("evidence_readiness_assessment"))
            ),
            dimension_weights_used={
                str(key): float(value)
                for key, value in _dict(payload.get("dimension_weights_used")).items()
            },
            entity_priority_score=int(payload.get("entity_priority_score", 0)),
            evidence_readiness_score=int(
                payload.get("evidence_readiness_score", 0)
            ),
            rank=int(payload.get("rank", 0)),
            priority_tier=PriorityTier(str(payload.get("priority_tier", ""))),
            rationale=str(payload.get("rationale", "")),
            review_flags=_string_tuple(payload.get("review_flags")),
            provenance=_dict(payload.get("provenance")),
            schema_version=str(
                payload.get(
                    "schema_version",
                    ENTITY_PRIORITY_ASSESSMENT_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class RejectedEntityPriorityAssessment:
    entity_id: str
    original_assessment: dict[str, Any]
    rejection_reason: str
    diagnostics: tuple[str, ...]
    source_assessment_index: int | None = None
    schema_version: str = REJECTED_ENTITY_PRIORITY_ASSESSMENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RejectedEntityPriorityAssessment":
        return cls(
            entity_id=str(payload.get("entity_id", "")),
            original_assessment=_dict(payload.get("original_assessment")),
            rejection_reason=str(payload.get("rejection_reason", "")),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            source_assessment_index=payload.get("source_assessment_index"),
            schema_version=str(
                payload.get(
                    "schema_version",
                    REJECTED_ENTITY_PRIORITY_ASSESSMENT_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class EntityPrioritizationExecutionMetadata:
    provider: str | None = None
    model: str | None = None
    prompt_version: str = ENTITY_PRIORITIZATION_PROMPT_VERSION
    scoring_policy_version: str = ENTITY_PRIORITY_SCORING_POLICY_VERSION
    semantic_scale_version: str = SEMANTIC_SCORE_SCALE_VERSION
    evidence_readiness_policy_version: str = EVIDENCE_READINESS_POLICY_VERSION
    compact_context_policy_version: str = COMPACT_CONTEXT_POLICY_VERSION
    priority_tier_policy_version: str = PRIORITY_TIER_POLICY_VERSION
    input_fingerprint: str | None = None
    schema_version: str = ENTITY_PRIORITIZATION_EXECUTION_METADATA_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(
        cls, payload: dict[str, Any]
    ) -> "EntityPrioritizationExecutionMetadata":
        return cls(
            provider=payload.get("provider"),
            model=payload.get("model"),
            prompt_version=str(
                payload.get("prompt_version", ENTITY_PRIORITIZATION_PROMPT_VERSION)
            ),
            scoring_policy_version=str(
                payload.get(
                    "scoring_policy_version",
                    ENTITY_PRIORITY_SCORING_POLICY_VERSION,
                )
            ),
            semantic_scale_version=str(
                payload.get("semantic_scale_version", SEMANTIC_SCORE_SCALE_VERSION)
            ),
            evidence_readiness_policy_version=str(
                payload.get(
                    "evidence_readiness_policy_version",
                    EVIDENCE_READINESS_POLICY_VERSION,
                )
            ),
            compact_context_policy_version=str(
                payload.get(
                    "compact_context_policy_version",
                    COMPACT_CONTEXT_POLICY_VERSION,
                )
            ),
            priority_tier_policy_version=str(
                payload.get(
                    "priority_tier_policy_version",
                    PRIORITY_TIER_POLICY_VERSION,
                )
            ),
            input_fingerprint=payload.get("input_fingerprint"),
            schema_version=str(
                payload.get(
                    "schema_version",
                    ENTITY_PRIORITIZATION_EXECUTION_METADATA_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class EntityPrioritizationResult:
    priority_assessments: tuple[EntityPriorityAssessment, ...]
    rejected_assessments: tuple[RejectedEntityPriorityAssessment, ...]
    unassessed_entity_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]
    execution_metadata: EntityPrioritizationExecutionMetadata
    scoring_policy_version: str
    compact_context_policy_version: str
    prompt_version: str
    input_fingerprint: str
    output_hash: str
    schema_version: str = ENTITY_PRIORITIZATION_RESULT_SCHEMA_VERSION
    generation_mode: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntityPrioritizationResult":
        return cls(
            priority_assessments=tuple(
                EntityPriorityAssessment.from_dict(item)
                for item in _dict_items(payload.get("priority_assessments"))
            ),
            rejected_assessments=tuple(
                RejectedEntityPriorityAssessment.from_dict(item)
                for item in _dict_items(payload.get("rejected_assessments"))
            ),
            unassessed_entity_ids=_string_tuple(
                payload.get("unassessed_entity_ids")
            ),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            execution_metadata=EntityPrioritizationExecutionMetadata.from_dict(
                _dict(payload.get("execution_metadata"))
            ),
            scoring_policy_version=str(
                payload.get(
                    "scoring_policy_version",
                    ENTITY_PRIORITY_SCORING_POLICY_VERSION,
                )
            ),
            compact_context_policy_version=str(
                payload.get(
                    "compact_context_policy_version",
                    COMPACT_CONTEXT_POLICY_VERSION,
                )
            ),
            prompt_version=str(
                payload.get("prompt_version", ENTITY_PRIORITIZATION_PROMPT_VERSION)
            ),
            input_fingerprint=str(payload["input_fingerprint"]),
            output_hash=str(payload["output_hash"]),
            schema_version=str(
                payload.get(
                    "schema_version",
                    ENTITY_PRIORITIZATION_RESULT_SCHEMA_VERSION,
                )
            ),
            generation_mode=str(payload.get("generation_mode", "loaded_from_cache")),
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
