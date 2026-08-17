from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.source_monitoring.models import _json_ready


ENTITY_DISCOVERY_QUERY_SCHEMA_VERSION = "entity_discovery_query_v1"
ENTITY_DISCOVERY_PLAN_SCHEMA_VERSION = "entity_discovery_plan_v1"
ENTITY_DISCOVERY_EVIDENCE_SCHEMA_VERSION = "entity_discovery_evidence_v1"
OFFICIAL_DOMAIN_CANDIDATE_SCHEMA_VERSION = "official_domain_candidate_v1"
ENTITY_CANDIDATE_SCHEMA_VERSION = "entity_candidate_v1"
REJECTED_ENTITY_CANDIDATE_SCHEMA_VERSION = "rejected_entity_candidate_v1"
UNRESOLVED_IDENTITY_CONFLICT_SCHEMA_VERSION = "unresolved_identity_conflict_v1"
ENTITY_UNIVERSE_EXECUTION_METADATA_SCHEMA_VERSION = (
    "entity_universe_execution_metadata_v1"
)
ENTITY_UNIVERSE_RESULT_SCHEMA_VERSION = "entity_universe_result_v1"

PRIMARY_ENTITY_KIND_TAXONOMY_VERSION = "primary_entity_kind_taxonomy_v1"
CLASSIFICATION_FACET_TAXONOMY_VERSION = "classification_facet_taxonomy_v1"
ENTITY_DISCOVERY_PLANNING_PROMPT_VERSION = "entity_discovery_planning_prompt_v1"
ENTITY_EXTRACTION_PROMPT_VERSION = "entity_candidate_extraction_prompt_v1"


class PrimaryEntityKind(str, Enum):
    OPERATING_COMPANY = "operating_company"
    INVESTMENT_FIRM = "investment_firm"
    PROFESSIONAL_SERVICES_FIRM = "professional_services_firm"
    KNOWLEDGE_INSTITUTION = "knowledge_institution"
    PUBLIC_SECTOR_BODY = "public_sector_body"
    INFORMATION_PLATFORM = "information_platform"
    TALENT_MARKET_PLATFORM = "talent_market_platform"
    ECOSYSTEM_SUPPORT_ORGANIZATION = "ecosystem_support_organization"


class OfficialDomainVerificationStatus(str, Enum):
    VERIFIED_OFFICIAL = "verified_official"
    PROBABLE_OFFICIAL = "probable_official"
    THIRD_PARTY = "third_party"
    UNRESOLVED = "unresolved"


class EntityCandidateVerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    EVIDENCE_SUPPORTED = "evidence_supported"
    CONFLICTED = "conflicted"


@dataclass(frozen=True)
class EntityDiscoveryQuery:
    query_id: str
    query_text: str
    language: str
    region: str
    entity_type_code: str
    related_entity_type_candidate_id: str
    related_information_need_ids: tuple[str, ...]
    discovery_intent: str
    rationale: str = ""
    schema_version: str = ENTITY_DISCOVERY_QUERY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntityDiscoveryQuery":
        return cls(
            query_id=str(payload["query_id"]),
            query_text=str(payload["query_text"]),
            language=str(payload["language"]),
            region=str(payload["region"]),
            entity_type_code=str(payload["entity_type_code"]),
            related_entity_type_candidate_id=str(
                payload["related_entity_type_candidate_id"]
            ),
            related_information_need_ids=_string_tuple(
                payload.get("related_information_need_ids")
            ),
            discovery_intent=str(payload["discovery_intent"]),
            rationale=str(payload.get("rationale", "")),
            schema_version=str(
                payload.get("schema_version", ENTITY_DISCOVERY_QUERY_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class EntityDiscoveryPlan:
    plan_id: str
    entity_type_candidate_id: str
    entity_type_code: str
    queries: tuple[EntityDiscoveryQuery, ...]
    language: str
    region: str
    max_results: int
    priority: float
    confidence: float
    planning_notes: tuple[str, ...] = ()
    schema_version: str = ENTITY_DISCOVERY_PLAN_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntityDiscoveryPlan":
        return cls(
            plan_id=str(payload["plan_id"]),
            entity_type_candidate_id=str(payload["entity_type_candidate_id"]),
            entity_type_code=str(payload["entity_type_code"]),
            queries=tuple(
                EntityDiscoveryQuery.from_dict(item)
                for item in _dict_items(payload.get("queries"))
            ),
            language=str(payload["language"]),
            region=str(payload["region"]),
            max_results=int(payload["max_results"]),
            priority=float(payload["priority"]),
            confidence=float(payload["confidence"]),
            planning_notes=_string_tuple(payload.get("planning_notes")),
            schema_version=str(
                payload.get("schema_version", ENTITY_DISCOVERY_PLAN_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class EntityDiscoveryEvidence:
    evidence_id: str
    plan_id: str
    query_id: str
    result_rank: int
    title: str
    snippet: str
    url: str
    displayed_domain: str
    search_provider: str
    retrieved_at: str
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = ENTITY_DISCOVERY_EVIDENCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntityDiscoveryEvidence":
        return cls(
            evidence_id=str(payload["evidence_id"]),
            plan_id=str(payload["plan_id"]),
            query_id=str(payload["query_id"]),
            result_rank=int(payload["result_rank"]),
            title=str(payload.get("title", "")),
            snippet=str(payload.get("snippet", "")),
            url=str(payload.get("url", "")),
            displayed_domain=str(payload.get("displayed_domain", "")),
            search_provider=str(payload.get("search_provider", "")),
            retrieved_at=str(payload.get("retrieved_at", "")),
            raw_metadata=dict(payload.get("raw_metadata", {}))
            if isinstance(payload.get("raw_metadata"), dict)
            else {},
            schema_version=str(
                payload.get(
                    "schema_version",
                    ENTITY_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class OfficialDomainCandidate:
    domain: str
    evidence_url: str
    confidence: float
    verification_status: OfficialDomainVerificationStatus
    reason: str
    schema_version: str = OFFICIAL_DOMAIN_CANDIDATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OfficialDomainCandidate":
        return cls(
            domain=str(payload["domain"]),
            evidence_url=str(payload.get("evidence_url", "")),
            confidence=float(payload.get("confidence", 0.0)),
            verification_status=OfficialDomainVerificationStatus(
                str(payload.get("verification_status", "unresolved"))
            ),
            reason=str(payload.get("reason", "")),
            schema_version=str(
                payload.get(
                    "schema_version",
                    OFFICIAL_DOMAIN_CANDIDATE_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class EntityCandidate:
    entity_id: str
    canonical_name: str
    names_by_language: dict[str, tuple[str, ...]]
    primary_entity_kind: PrimaryEntityKind
    entity_type_codes: tuple[str, ...]
    classification_facets: dict[str, tuple[str, ...]]
    related_entity_type_candidate_ids: tuple[str, ...]
    related_information_need_ids: tuple[str, ...]
    related_target_career_path_ids: tuple[str, ...]
    official_domain_candidates: tuple[OfficialDomainCandidate, ...]
    evidence_ids: tuple[str, ...]
    evidence_urls: tuple[str, ...]
    geographic_scope: str
    rationale: str
    confidence: float
    verification_status: EntityCandidateVerificationStatus
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: str = ENTITY_CANDIDATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntityCandidate":
        return cls(
            entity_id=str(payload["entity_id"]),
            canonical_name=str(payload["canonical_name"]),
            names_by_language=_language_tuple_map(payload.get("names_by_language")),
            primary_entity_kind=PrimaryEntityKind(str(payload["primary_entity_kind"])),
            entity_type_codes=_string_tuple(payload.get("entity_type_codes")),
            classification_facets=_language_tuple_map(
                payload.get("classification_facets")
            ),
            related_entity_type_candidate_ids=_string_tuple(
                payload.get("related_entity_type_candidate_ids")
            ),
            related_information_need_ids=_string_tuple(
                payload.get("related_information_need_ids")
            ),
            related_target_career_path_ids=_string_tuple(
                payload.get("related_target_career_path_ids")
            ),
            official_domain_candidates=tuple(
                OfficialDomainCandidate.from_dict(item)
                for item in _dict_items(payload.get("official_domain_candidates"))
            ),
            evidence_ids=_string_tuple(payload.get("evidence_ids")),
            evidence_urls=_string_tuple(payload.get("evidence_urls")),
            geographic_scope=str(payload.get("geographic_scope", "")),
            rationale=str(payload.get("rationale", "")),
            confidence=float(payload.get("confidence", 0.0)),
            verification_status=EntityCandidateVerificationStatus(
                str(payload.get("verification_status", "unverified"))
            ),
            provenance=dict(payload.get("provenance", {}))
            if isinstance(payload.get("provenance"), dict)
            else {},
            schema_version=str(
                payload.get("schema_version", ENTITY_CANDIDATE_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class RejectedEntityCandidate:
    original_candidate: dict[str, Any]
    rejection_reason: str
    supporting_evidence_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]
    source_extraction_index: int | None = None
    schema_version: str = REJECTED_ENTITY_CANDIDATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RejectedEntityCandidate":
        return cls(
            original_candidate=dict(payload.get("original_candidate", {}))
            if isinstance(payload.get("original_candidate"), dict)
            else {},
            rejection_reason=str(payload.get("rejection_reason", "")),
            supporting_evidence_ids=_string_tuple(
                payload.get("supporting_evidence_ids")
            ),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            source_extraction_index=payload.get("source_extraction_index"),
            schema_version=str(
                payload.get(
                    "schema_version",
                    REJECTED_ENTITY_CANDIDATE_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class UnresolvedIdentityConflict:
    conflict_id: str
    candidate_entity_ids: tuple[str, ...]
    reason: str
    evidence_ids: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()
    schema_version: str = UNRESOLVED_IDENTITY_CONFLICT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UnresolvedIdentityConflict":
        return cls(
            conflict_id=str(payload["conflict_id"]),
            candidate_entity_ids=_string_tuple(payload.get("candidate_entity_ids")),
            reason=str(payload.get("reason", "")),
            evidence_ids=_string_tuple(payload.get("evidence_ids")),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            schema_version=str(
                payload.get(
                    "schema_version",
                    UNRESOLVED_IDENTITY_CONFLICT_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class EntityUniverseExecutionMetadata:
    planning_provider: str | None = None
    planning_model: str | None = None
    planning_prompt_version: str | None = None
    extraction_provider: str | None = None
    extraction_model: str | None = None
    extraction_prompt_version: str | None = None
    search_provider: str | None = None
    primary_kind_taxonomy_version: str = PRIMARY_ENTITY_KIND_TAXONOMY_VERSION
    facet_taxonomy_version: str = CLASSIFICATION_FACET_TAXONOMY_VERSION
    input_fingerprint: str | None = None
    schema_version: str = ENTITY_UNIVERSE_EXECUTION_METADATA_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntityUniverseExecutionMetadata":
        return cls(
            planning_provider=payload.get("planning_provider"),
            planning_model=payload.get("planning_model"),
            planning_prompt_version=payload.get("planning_prompt_version"),
            extraction_provider=payload.get("extraction_provider"),
            extraction_model=payload.get("extraction_model"),
            extraction_prompt_version=payload.get("extraction_prompt_version"),
            search_provider=payload.get("search_provider"),
            primary_kind_taxonomy_version=str(
                payload.get(
                    "primary_kind_taxonomy_version",
                    PRIMARY_ENTITY_KIND_TAXONOMY_VERSION,
                )
            ),
            facet_taxonomy_version=str(
                payload.get(
                    "facet_taxonomy_version",
                    CLASSIFICATION_FACET_TAXONOMY_VERSION,
                )
            ),
            input_fingerprint=payload.get("input_fingerprint"),
            schema_version=str(
                payload.get(
                    "schema_version",
                    ENTITY_UNIVERSE_EXECUTION_METADATA_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class EntityUniverseResult:
    entity_discovery_plans: tuple[EntityDiscoveryPlan, ...]
    entity_discovery_evidence: tuple[EntityDiscoveryEvidence, ...]
    entity_candidates: tuple[EntityCandidate, ...]
    rejected_candidates: tuple[RejectedEntityCandidate, ...]
    unresolved_identity_conflicts: tuple[UnresolvedIdentityConflict, ...]
    uncovered_entity_type_candidate_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]
    execution_metadata: EntityUniverseExecutionMetadata
    input_fingerprint: str
    output_hash: str
    schema_version: str = ENTITY_UNIVERSE_RESULT_SCHEMA_VERSION
    generation_mode: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntityUniverseResult":
        return cls(
            entity_discovery_plans=tuple(
                EntityDiscoveryPlan.from_dict(item)
                for item in _dict_items(payload.get("entity_discovery_plans"))
            ),
            entity_discovery_evidence=tuple(
                EntityDiscoveryEvidence.from_dict(item)
                for item in _dict_items(payload.get("entity_discovery_evidence"))
            ),
            entity_candidates=tuple(
                EntityCandidate.from_dict(item)
                for item in _dict_items(payload.get("entity_candidates"))
            ),
            rejected_candidates=tuple(
                RejectedEntityCandidate.from_dict(item)
                for item in _dict_items(payload.get("rejected_candidates"))
            ),
            unresolved_identity_conflicts=tuple(
                UnresolvedIdentityConflict.from_dict(item)
                for item in _dict_items(payload.get("unresolved_identity_conflicts"))
            ),
            uncovered_entity_type_candidate_ids=_string_tuple(
                payload.get("uncovered_entity_type_candidate_ids")
            ),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            execution_metadata=EntityUniverseExecutionMetadata.from_dict(
                payload.get("execution_metadata", {})
                if isinstance(payload.get("execution_metadata"), dict)
                else {}
            ),
            input_fingerprint=str(payload["input_fingerprint"]),
            output_hash=str(payload["output_hash"]),
            schema_version=str(
                payload.get("schema_version", ENTITY_UNIVERSE_RESULT_SCHEMA_VERSION)
            ),
            generation_mode=str(payload.get("generation_mode", "loaded_from_cache")),
        )


def _dict_items(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()

    return tuple(item for item in value if isinstance(item, dict))


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()

    return tuple(str(item) for item in value if item is not None)


def _language_tuple_map(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, tuple[str, ...]] = {}
    for key, item in value.items():
        normalized[str(key)] = _string_tuple(item)

    return normalized
