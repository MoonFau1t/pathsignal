from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.source_monitoring.entity_discovery_models import PrimaryEntityKind
from src.source_monitoring.models import _json_ready
from src.source_monitoring.source_discovery_models import (
    CandidateOfficialityStatus,
    CandidateSource,
    CandidateSourceStatus,
    SourceFormatHint,
    SourceRole,
)


SOURCE_EVALUATION_SCHEMA_VERSION = "source_evaluation_schema_v1"
SOURCE_INSPECTION_SCHEMA_VERSION = "source_inspection_schema_v1"
SOURCE_SEMANTIC_BUNDLE_SCHEMA_VERSION = "source_semantic_bundle_schema_v1"
SOURCE_OBSERVATION_SCHEMA_VERSION = "source_observation_schema_v1"

SOURCE_EVALUATION_PLAN_SCHEMA_VERSION = "source_evaluation_plan_v1"
SOURCE_FETCH_REQUEST_SCHEMA_VERSION = "source_fetch_request_v1"
SOURCE_FETCH_EXECUTION_SCHEMA_VERSION = "source_fetch_execution_v1"
REDIRECT_HOP_SCHEMA_VERSION = "redirect_hop_v1"
RAW_PAGE_ARTIFACT_REF_SCHEMA_VERSION = "raw_page_artifact_ref_v1"
FETCHED_PAGE_RUNTIME_SCHEMA_VERSION = "fetched_page_runtime_v1"
FEED_LINK_HINT_SCHEMA_VERSION = "feed_link_hint_v1"
SEMANTIC_TEXT_WINDOW_SCHEMA_VERSION = "semantic_text_window_v1"
INITIAL_SOURCE_EVALUATION_SCHEMA_VERSION = "initial_source_evaluation_v1"
SOURCE_OBSERVATION_PLAN_SCHEMA_VERSION = "source_observation_plan_v1"
OBSERVED_SOURCE_EVIDENCE_SCHEMA_VERSION = "observed_source_evidence_v1"
SOURCE_OBSERVATION_RESULT_SCHEMA_VERSION = "source_observation_result_v1"
OBSERVED_SIGNAL_POTENTIAL_SCHEMA_VERSION = "observed_signal_potential_v1"
FINAL_SOURCE_EVALUATION_SCHEMA_VERSION = "final_source_evaluation_v1"
REJECTED_SOURCE_EVALUATION_RECORD_SCHEMA_VERSION = (
    "rejected_source_evaluation_record_v1"
)
SOURCE_EVALUATION_RESULT_SCHEMA_VERSION = "source_evaluation_result_v1"

SOURCE_FETCH_POLICY_VERSION = "source_fetch_policy_v1"
SOURCE_USER_AGENT_POLICY_VERSION = "source_user_agent_policy_v1"
SOURCE_INSPECTOR_VERSION = "source_inspector_contract_v1"
SOURCE_EVALUATOR_POLICY_VERSION = "source_evaluator_policy_v1"
SOURCE_OBSERVATION_POLICY_VERSION = "source_observation_policy_v1"
SOURCE_SEMANTIC_WINDOW_POLICY_VERSION = "semantic_text_window_policy_v1"

DEFAULT_SEMANTIC_TEXT_WINDOW_MAX_CHARS = 2000
DEFAULT_SEMANTIC_BUNDLE_MAX_BYTES = 60000
UNTRUSTED_WEBPAGE_EVIDENCE_MARKER = "untrusted_external_webpage_evidence"


class EvaluationScope(str, Enum):
    SOURCE_SURFACE = "source_surface"
    ONE_OFF_CONTENT_DIAGNOSTIC = "one_off_content_diagnostic"


class FetchMethod(str, Enum):
    GET = "GET"


class FetchStatus(str, Enum):
    COMPLETED_HTML = "completed_html"
    COMPLETED_NON_HTML = "completed_non_html"
    COMPLETED_EMPTY_RESPONSE = "completed_empty_response"
    TIMEOUT = "timeout"
    NETWORK_FAILURE = "network_failure"
    HTTP_FAILURE = "http_failure"
    REDIRECT_FAILURE = "redirect_failure"
    RESPONSE_TOO_LARGE = "response_too_large"
    UNSUPPORTED_CONTENT = "unsupported_content"
    OTHER_FAILURE = "other_failure"


class EntityMatchStatus(str, Enum):
    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    UNCERTAIN = "uncertain"
    MISMATCH = "mismatch"


class OfficialityStatus(str, Enum):
    OFFICIAL = "official"
    PROBABLE_OFFICIAL = "probable_official"
    AFFILIATED = "affiliated"
    THIRD_PARTY = "third_party"
    UNCERTAIN = "uncertain"


class PageType(str, Enum):
    HOMEPAGE = "homepage"
    SECTION_HUB = "section_hub"
    LISTING_PAGE = "listing_page"
    ARTICLE_DETAIL = "article_detail"
    JOB_DETAIL = "job_detail"
    REPORT_DETAIL = "report_detail"
    EVENT_DETAIL = "event_detail"
    PORTFOLIO_INDEX = "portfolio_index"
    PROFILE_PAGE = "profile_page"
    SEARCH_RESULTS = "search_results"
    OTHER = "other"
    UNKNOWN = "unknown"


class SurfaceDurabilityStatus(str, Enum):
    DURABLE_SURFACE = "durable_surface"
    LIKELY_DURABLE_SURFACE = "likely_durable_surface"
    ONE_OFF_CONTENT = "one_off_content"
    UNCERTAIN = "uncertain"


class SourceRoleMatchStatus(str, Enum):
    MATCH = "match"
    COMPATIBLE = "compatible"
    MISMATCH = "mismatch"
    UNCERTAIN = "uncertain"


class RelevanceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


class SourceValueLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


class EvaluationConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class InitialEvaluationDecision(str, Enum):
    PROCEED_TO_OBSERVATION = "proceed_to_observation"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class FinalEvaluationDecision(str, Enum):
    APPROVED_FOR_ACQUISITION = "approved_for_acquisition"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class ObservedSignalPotentialLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AssessmentMethod(str, Enum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    HYBRID = "hybrid"


class SemanticTextWindowType(str, Enum):
    PAGE_TITLE = "page_title"
    META_DESCRIPTION = "meta_description"
    HEADING_CONTEXT = "heading_context"
    NAVIGATION = "navigation"
    MAIN_CONTENT_EXCERPT = "main_content_excerpt"
    REPRESENTATIVE_LINK_CLUSTER = "representative_link_cluster"
    STRUCTURED_DATA_EXCERPT = "structured_data_excerpt"


class ObservationSamplingStrategy(str, Enum):
    BOUNDED_SOURCE_SAMPLE = "bounded_source_sample"
    SECTION_LISTING_SAMPLE = "section_listing_sample"
    FEED_HINT_LINK_SAMPLE = "feed_hint_link_sample"
    MANUAL_REVIEW_SAMPLE = "manual_review_sample"


class ObservationStatus(str, Enum):
    COMPLETED = "completed"
    COMPLETED_NO_ITEMS = "completed_no_items"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class SourceEvaluationPlan:
    source_evaluation_plan_id: str
    candidate_source_id: str
    entity_id: str
    candidate_url: str
    planned_source_role: SourceRole
    phase4_candidate_status: CandidateSourceStatus
    supporting_source_discovery_evidence_ids: tuple[str, ...]
    allowed_information_need_ids: tuple[str, ...]
    evaluation_scope: EvaluationScope
    candidate_priority_rank: int
    planned_source_format_hint: SourceFormatHint
    source_role_ontology_version: str
    phase4_candidate_schema_version: str
    phase4_input_fingerprint: str
    phase4_output_hash: str
    input_fingerprint: str
    policy_version: str = SOURCE_EVALUATOR_POLICY_VERSION
    schema_version: str = SOURCE_EVALUATION_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(
            self.phase4_candidate_status
            in {CandidateSourceStatus.ACCEPTED, CandidateSourceStatus.NEEDS_REVIEW},
            "Phase 5 plans can only target accepted or needs-review CandidateSources.",
        )
        _require(isinstance(self.planned_source_role, SourceRole), "invalid SourceRole")
        _require(
            isinstance(self.planned_source_format_hint, SourceFormatHint),
            "invalid SourceFormatHint",
        )
        _require(
            self.planned_source_format_hint.value not in {role.value for role in SourceRole},
            "SourceFormatHint must remain separate from SourceRole.",
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceEvaluationPlan":
        return cls(
            source_evaluation_plan_id=str(payload["source_evaluation_plan_id"]),
            candidate_source_id=str(payload["candidate_source_id"]),
            entity_id=str(payload["entity_id"]),
            candidate_url=str(payload["candidate_url"]),
            planned_source_role=_source_role(payload["planned_source_role"]),
            phase4_candidate_status=CandidateSourceStatus(
                str(payload["phase4_candidate_status"])
            ),
            supporting_source_discovery_evidence_ids=_string_tuple(
                payload.get("supporting_source_discovery_evidence_ids")
            ),
            allowed_information_need_ids=_string_tuple(
                payload.get("allowed_information_need_ids")
            ),
            evaluation_scope=EvaluationScope(str(payload["evaluation_scope"])),
            candidate_priority_rank=int(payload.get("candidate_priority_rank", 0)),
            planned_source_format_hint=SourceFormatHint(
                str(payload["planned_source_format_hint"])
            ),
            source_role_ontology_version=str(
                payload.get("source_role_ontology_version", "")
            ),
            phase4_candidate_schema_version=str(
                payload.get("phase4_candidate_schema_version", "")
            ),
            phase4_input_fingerprint=str(payload.get("phase4_input_fingerprint", "")),
            phase4_output_hash=str(payload.get("phase4_output_hash", "")),
            input_fingerprint=str(payload["input_fingerprint"]),
            policy_version=str(
                payload.get("policy_version", SOURCE_EVALUATOR_POLICY_VERSION)
            ),
            schema_version=str(
                payload.get("schema_version", SOURCE_EVALUATION_PLAN_SCHEMA_VERSION)
            ),
        )

    @classmethod
    def from_candidate_source(
        cls,
        *,
        candidate: CandidateSource,
        phase4_candidate_status: CandidateSourceStatus,
        allowed_information_need_ids: tuple[str, ...],
        source_evaluation_plan_id: str,
        input_fingerprint: str,
        phase4_input_fingerprint: str,
        phase4_output_hash: str,
        source_role_ontology_version: str,
        candidate_priority_rank: int = 0,
        evaluation_scope: EvaluationScope = EvaluationScope.SOURCE_SURFACE,
    ) -> "SourceEvaluationPlan":
        return cls(
            source_evaluation_plan_id=source_evaluation_plan_id,
            candidate_source_id=candidate.candidate_source_id,
            entity_id=candidate.entity_id,
            candidate_url=candidate.normalized_url or candidate.canonical_url,
            planned_source_role=candidate.source_role,
            phase4_candidate_status=phase4_candidate_status,
            supporting_source_discovery_evidence_ids=candidate.supporting_evidence_ids,
            allowed_information_need_ids=allowed_information_need_ids,
            evaluation_scope=evaluation_scope,
            candidate_priority_rank=candidate_priority_rank,
            planned_source_format_hint=candidate.source_format_hint,
            source_role_ontology_version=source_role_ontology_version,
            phase4_candidate_schema_version=candidate.schema_version,
            phase4_input_fingerprint=phase4_input_fingerprint,
            phase4_output_hash=phase4_output_hash,
            input_fingerprint=input_fingerprint,
        )


@dataclass(frozen=True)
class SourceFetchRequest:
    requested_url: str
    method: FetchMethod
    timeout_seconds: int
    max_response_bytes: int
    max_redirects: int
    accepted_content_types: tuple[str, ...]
    user_agent_policy_version: str
    fetch_policy_version: str
    request_fingerprint: str
    schema_version: str = SOURCE_FETCH_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.method == FetchMethod.GET, "Phase 5A V1 fetch requests are GET-only.")
        _require(self.timeout_seconds > 0, "timeout_seconds must be positive.")
        _require(self.max_response_bytes > 0, "max_response_bytes must be positive.")
        _require(self.max_redirects >= 0, "max_redirects must be non-negative.")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceFetchRequest":
        return cls(
            requested_url=str(payload["requested_url"]),
            method=FetchMethod(str(payload.get("method", FetchMethod.GET.value))),
            timeout_seconds=int(payload.get("timeout_seconds", 0)),
            max_response_bytes=int(payload.get("max_response_bytes", 0)),
            max_redirects=int(payload.get("max_redirects", 0)),
            accepted_content_types=_string_tuple(payload.get("accepted_content_types")),
            user_agent_policy_version=str(payload.get("user_agent_policy_version", "")),
            fetch_policy_version=str(payload.get("fetch_policy_version", "")),
            request_fingerprint=str(payload["request_fingerprint"]),
            schema_version=str(
                payload.get("schema_version", SOURCE_FETCH_REQUEST_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class RedirectHop:
    source_url: str
    destination_url: str
    status_code: int
    hop_order: int
    schema_version: str = REDIRECT_HOP_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(300 <= self.status_code <= 399, "redirect status must be 3xx.")
        _require(self.hop_order >= 0, "hop_order must be non-negative.")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RedirectHop":
        return cls(
            source_url=str(payload["source_url"]),
            destination_url=str(payload["destination_url"]),
            status_code=int(payload["status_code"]),
            hop_order=int(payload.get("hop_order", 0)),
            schema_version=str(payload.get("schema_version", REDIRECT_HOP_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class RawPageArtifactRef:
    artifact_path: str
    sha256: str
    byte_size: int
    content_type: str
    encoding: str | None
    retrieved_at: str
    schema_version: str = RAW_PAGE_ARTIFACT_REF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(not _looks_absolute_path(self.artifact_path), "artifact_path must be repository-relative.")
        _require(len(self.sha256) == 64, "sha256 must be a hex SHA-256 digest.")
        _require(self.byte_size >= 0, "byte_size must be non-negative.")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RawPageArtifactRef":
        return cls(
            artifact_path=str(payload["artifact_path"]),
            sha256=str(payload["sha256"]),
            byte_size=int(payload.get("byte_size", 0)),
            content_type=str(payload.get("content_type", "")),
            encoding=payload.get("encoding"),
            retrieved_at=str(payload.get("retrieved_at", "")),
            schema_version=str(
                payload.get("schema_version", RAW_PAGE_ARTIFACT_REF_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class FetchedPage:
    """
    Transient runtime payload for future fetch code.

    It may hold raw bytes/text while the process is running, but persistent
    domain models should reference RawPageArtifactRef and content hashes.
    """

    fetch_execution_id: str
    response_metadata: dict[str, Any]
    raw_bytes: bytes
    decoded_text: str | None
    raw_artifact_ref: RawPageArtifactRef | None
    schema_version: str = FETCHED_PAGE_RUNTIME_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetch_execution_id": self.fetch_execution_id,
            "response_metadata": _json_ready(self.response_metadata),
            "raw_artifact_ref": (
                self.raw_artifact_ref.to_dict() if self.raw_artifact_ref else None
            ),
            "schema_version": self.schema_version,
            "runtime_payload_omitted": True,
        }


@dataclass(frozen=True)
class SourceFetchExecution:
    source_fetch_execution_id: str
    source_evaluation_plan_id: str
    candidate_source_id: str
    request_fingerprint: str
    requested_url: str
    final_url: str
    fetch_status: FetchStatus
    http_status: int | None
    redirect_chain: tuple[RedirectHop, ...]
    content_type: str | None
    content_length_reported: int | None
    declared_encoding: str | None
    detected_encoding: str | None
    content_language: str | None
    response_size_bytes: int | None
    etag: str | None
    last_modified: str | None
    retrieved_at: str | None
    elapsed_ms: int | None
    raw_body_sha256: str | None
    raw_artifact_ref: RawPageArtifactRef | None
    error_type: str | None
    error_message: str | None
    fetch_policy_version: str
    schema_version: str = SOURCE_FETCH_EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.raw_artifact_ref is not None and self.raw_body_sha256 is not None:
            _require(
                self.raw_artifact_ref.sha256 == self.raw_body_sha256,
                "raw artifact hash must match raw_body_sha256.",
            )
        if self.response_size_bytes is not None:
            _require(self.response_size_bytes >= 0, "response_size_bytes must be non-negative.")
        _require(
            _hop_orders_are_stable(self.redirect_chain),
            "redirect_chain hop_order values must be deterministic and sorted.",
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceFetchExecution":
        return cls(
            source_fetch_execution_id=str(payload["source_fetch_execution_id"]),
            source_evaluation_plan_id=str(payload["source_evaluation_plan_id"]),
            candidate_source_id=str(payload["candidate_source_id"]),
            request_fingerprint=str(payload["request_fingerprint"]),
            requested_url=str(payload["requested_url"]),
            final_url=str(payload.get("final_url", "")),
            fetch_status=FetchStatus(str(payload["fetch_status"])),
            http_status=_optional_int(payload.get("http_status")),
            redirect_chain=tuple(
                RedirectHop.from_dict(item) for item in _dict_items(payload.get("redirect_chain"))
            ),
            content_type=payload.get("content_type"),
            content_length_reported=_optional_int(payload.get("content_length_reported")),
            declared_encoding=payload.get("declared_encoding"),
            detected_encoding=payload.get("detected_encoding"),
            content_language=payload.get("content_language"),
            response_size_bytes=_optional_int(payload.get("response_size_bytes")),
            etag=payload.get("etag"),
            last_modified=payload.get("last_modified"),
            retrieved_at=payload.get("retrieved_at"),
            elapsed_ms=_optional_int(payload.get("elapsed_ms")),
            raw_body_sha256=payload.get("raw_body_sha256"),
            raw_artifact_ref=(
                RawPageArtifactRef.from_dict(payload["raw_artifact_ref"])
                if isinstance(payload.get("raw_artifact_ref"), dict)
                else None
            ),
            error_type=payload.get("error_type"),
            error_message=payload.get("error_message"),
            fetch_policy_version=str(payload.get("fetch_policy_version", "")),
            schema_version=str(
                payload.get("schema_version", SOURCE_FETCH_EXECUTION_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class FeedLinkHint:
    href: str
    rel: str
    mime_type: str
    title: str | None = None
    verification_status: str = "unverified"
    schema_version: str = FEED_LINK_HINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(
            self.verification_status == "unverified",
            "feed hints must remain unverified in Phase 5A.",
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeedLinkHint":
        return cls(
            href=str(payload["href"]),
            rel=str(payload.get("rel", "")),
            mime_type=str(payload.get("mime_type", payload.get("type", ""))),
            title=payload.get("title"),
            verification_status=str(payload.get("verification_status", "unverified")),
            schema_version=str(payload.get("schema_version", FEED_LINK_HINT_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class SemanticTextWindow:
    window_id: str
    window_type: SemanticTextWindowType
    source_location: str
    text: str
    character_count: int
    structural_context: str | None
    evidence_provenance: dict[str, Any]
    max_character_count: int = DEFAULT_SEMANTIC_TEXT_WINDOW_MAX_CHARS
    policy_version: str = SOURCE_SEMANTIC_WINDOW_POLICY_VERSION
    schema_version: str = SEMANTIC_TEXT_WINDOW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.character_count == len(self.text), "character_count must match text length.")
        _require(self.max_character_count > 0, "max_character_count must be positive.")
        _require(
            self.character_count <= self.max_character_count,
            "semantic text window exceeds configured maximum size.",
        )
        _reject_raw_webpage_content(self.text, "semantic text window")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SemanticTextWindow":
        _reject_forbidden_payload_keys(payload, "SemanticTextWindow")
        return cls(
            window_id=str(payload["window_id"]),
            window_type=SemanticTextWindowType(str(payload["window_type"])),
            source_location=str(payload.get("source_location", "")),
            text=str(payload.get("text", "")),
            character_count=int(payload.get("character_count", len(str(payload.get("text", ""))))),
            structural_context=payload.get("structural_context"),
            evidence_provenance=_dict(payload.get("evidence_provenance")),
            max_character_count=int(
                payload.get("max_character_count", DEFAULT_SEMANTIC_TEXT_WINDOW_MAX_CHARS)
            ),
            policy_version=str(
                payload.get("policy_version", SOURCE_SEMANTIC_WINDOW_POLICY_VERSION)
            ),
            schema_version=str(
                payload.get("schema_version", SEMANTIC_TEXT_WINDOW_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class SourceInspection:
    inspection_id: str
    fetch_execution_id: str
    candidate_source_id: str
    requested_url: str
    final_url: str
    canonical_url: str | None
    root_domain: str
    canonical_root_domain: str | None
    page_title: str | None
    meta_description: str | None
    html_language: str | None
    content_language: str | None
    open_graph_title: str | None
    open_graph_description: str | None
    structured_data_types: tuple[str, ...]
    structured_data_organization_names: tuple[str, ...]
    heading_summary: tuple[str, ...]
    navigation_labels: tuple[str, ...]
    internal_link_count: int
    external_link_count: int
    same_domain_link_count: int
    has_pagination_hints: bool
    has_article_link_hints: bool
    has_job_link_hints: bool
    has_report_link_hints: bool
    has_event_link_hints: bool
    has_section_hub_hints: bool
    has_detail_page_hints: bool
    feed_link_hints: tuple[FeedLinkHint, ...]
    source_format_hints: tuple[SourceFormatHint, ...]
    visible_text_length: int
    semantic_text_windows: tuple[SemanticTextWindow, ...]
    semantic_content_truncated: bool
    client_rendering_required_hint: bool
    inspector_version: str
    raw_body_sha256: str
    raw_artifact_ref: RawPageArtifactRef | None
    inspection_input_fingerprint: str
    inspection_output_hash: str
    schema_version: str = SOURCE_INSPECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.visible_text_length >= 0, "visible_text_length must be non-negative.")
        _require(
            all(isinstance(item, SourceFormatHint) for item in self.source_format_hints),
            "source_format_hints must use SourceFormatHint, not SourceRole.",
        )
        _require(
            not any(item.value in {role.value for role in SourceRole} for item in self.source_format_hints),
            "SourceFormatHint remains separate from SourceRole.",
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceInspection":
        _reject_forbidden_payload_keys(payload, "SourceInspection")
        return cls(
            inspection_id=str(payload["inspection_id"]),
            fetch_execution_id=str(payload["fetch_execution_id"]),
            candidate_source_id=str(payload["candidate_source_id"]),
            requested_url=str(payload["requested_url"]),
            final_url=str(payload.get("final_url", "")),
            canonical_url=payload.get("canonical_url"),
            root_domain=str(payload.get("root_domain", "")),
            canonical_root_domain=payload.get("canonical_root_domain"),
            page_title=payload.get("page_title"),
            meta_description=payload.get("meta_description"),
            html_language=payload.get("html_language"),
            content_language=payload.get("content_language"),
            open_graph_title=payload.get("open_graph_title"),
            open_graph_description=payload.get("open_graph_description"),
            structured_data_types=_string_tuple(payload.get("structured_data_types")),
            structured_data_organization_names=_string_tuple(
                payload.get("structured_data_organization_names")
            ),
            heading_summary=_string_tuple(payload.get("heading_summary")),
            navigation_labels=_string_tuple(payload.get("navigation_labels")),
            internal_link_count=int(payload.get("internal_link_count", 0)),
            external_link_count=int(payload.get("external_link_count", 0)),
            same_domain_link_count=int(payload.get("same_domain_link_count", 0)),
            has_pagination_hints=bool(payload.get("has_pagination_hints", False)),
            has_article_link_hints=bool(payload.get("has_article_link_hints", False)),
            has_job_link_hints=bool(payload.get("has_job_link_hints", False)),
            has_report_link_hints=bool(payload.get("has_report_link_hints", False)),
            has_event_link_hints=bool(payload.get("has_event_link_hints", False)),
            has_section_hub_hints=bool(payload.get("has_section_hub_hints", False)),
            has_detail_page_hints=bool(payload.get("has_detail_page_hints", False)),
            feed_link_hints=tuple(
                FeedLinkHint.from_dict(item)
                for item in _dict_items(payload.get("feed_link_hints"))
            ),
            source_format_hints=tuple(
                SourceFormatHint(str(item))
                for item in _list(payload.get("source_format_hints"))
            ),
            visible_text_length=int(payload.get("visible_text_length", 0)),
            semantic_text_windows=tuple(
                SemanticTextWindow.from_dict(item)
                for item in _dict_items(payload.get("semantic_text_windows"))
            ),
            semantic_content_truncated=bool(payload.get("semantic_content_truncated", False)),
            client_rendering_required_hint=bool(
                payload.get("client_rendering_required_hint", False)
            ),
            inspector_version=str(payload.get("inspector_version", SOURCE_INSPECTOR_VERSION)),
            raw_body_sha256=str(payload.get("raw_body_sha256", "")),
            raw_artifact_ref=(
                RawPageArtifactRef.from_dict(payload["raw_artifact_ref"])
                if isinstance(payload.get("raw_artifact_ref"), dict)
                else None
            ),
            inspection_input_fingerprint=str(payload["inspection_input_fingerprint"]),
            inspection_output_hash=str(payload["inspection_output_hash"]),
            schema_version=str(payload.get("schema_version", SOURCE_INSPECTION_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class SourceSemanticEvidenceBundle:
    semantic_evidence_bundle_id: str
    entity_id: str
    canonical_name: str
    aliases: tuple[str, ...]
    known_domain_evidence: tuple[str, ...]
    primary_entity_kind: PrimaryEntityKind
    candidate_source_id: str
    candidate_url: str
    planned_source_role: SourceRole
    phase4_officiality_status: CandidateOfficialityStatus
    supporting_source_discovery_evidence_ids: tuple[str, ...]
    source_inspection_id: str
    requested_url: str
    final_url: str
    root_domain: str
    canonical_url: str | None
    page_title: str | None
    meta_description: str | None
    structural_hints: tuple[str, ...]
    feed_link_hints: tuple[FeedLinkHint, ...]
    semantic_text_windows: tuple[SemanticTextWindow, ...]
    allowed_source_roles: tuple[SourceRole, ...]
    allowed_information_need_ids: tuple[str, ...]
    untrusted_content_marker: str
    bundle_size_bytes: int
    semantic_content_truncated: bool
    bundle_fingerprint: str
    max_bundle_bytes: int = DEFAULT_SEMANTIC_BUNDLE_MAX_BYTES
    schema_version: str = SOURCE_SEMANTIC_BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(
            self.untrusted_content_marker == UNTRUSTED_WEBPAGE_EVIDENCE_MARKER,
            "semantic bundles must mark webpage content as untrusted external evidence.",
        )
        _require(self.bundle_size_bytes <= self.max_bundle_bytes, "semantic bundle exceeds maximum size.")
        _require(
            all(isinstance(role, SourceRole) for role in self.allowed_source_roles),
            "allowed_source_roles must reuse SourceRole.",
        )
        _require(
            self.planned_source_role in self.allowed_source_roles,
            "planned SourceRole must be one of the allowed SourceRoles.",
        )
        for window in self.semantic_text_windows:
            _reject_raw_webpage_content(window.text, "SourceSemanticEvidenceBundle")

    def to_dict(self) -> dict[str, Any]:
        payload = _json_ready(self)
        _reject_forbidden_payload_keys(payload, "SourceSemanticEvidenceBundle")
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceSemanticEvidenceBundle":
        _reject_forbidden_payload_keys(payload, "SourceSemanticEvidenceBundle")
        return cls(
            semantic_evidence_bundle_id=str(payload["semantic_evidence_bundle_id"]),
            entity_id=str(payload["entity_id"]),
            canonical_name=str(payload.get("canonical_name", "")),
            aliases=_string_tuple(payload.get("aliases")),
            known_domain_evidence=_string_tuple(payload.get("known_domain_evidence")),
            primary_entity_kind=PrimaryEntityKind(str(payload["primary_entity_kind"])),
            candidate_source_id=str(payload["candidate_source_id"]),
            candidate_url=str(payload.get("candidate_url", "")),
            planned_source_role=_source_role(payload["planned_source_role"]),
            phase4_officiality_status=CandidateOfficialityStatus(
                str(payload["phase4_officiality_status"])
            ),
            supporting_source_discovery_evidence_ids=_string_tuple(
                payload.get("supporting_source_discovery_evidence_ids")
            ),
            source_inspection_id=str(payload["source_inspection_id"]),
            requested_url=str(payload.get("requested_url", "")),
            final_url=str(payload.get("final_url", "")),
            root_domain=str(payload.get("root_domain", "")),
            canonical_url=payload.get("canonical_url"),
            page_title=payload.get("page_title"),
            meta_description=payload.get("meta_description"),
            structural_hints=_string_tuple(payload.get("structural_hints")),
            feed_link_hints=tuple(
                FeedLinkHint.from_dict(item)
                for item in _dict_items(payload.get("feed_link_hints"))
            ),
            semantic_text_windows=tuple(
                SemanticTextWindow.from_dict(item)
                for item in _dict_items(payload.get("semantic_text_windows"))
            ),
            allowed_source_roles=tuple(
                _source_role(item) for item in _list(payload.get("allowed_source_roles"))
            ),
            allowed_information_need_ids=_string_tuple(
                payload.get("allowed_information_need_ids")
            ),
            untrusted_content_marker=str(payload.get("untrusted_content_marker", "")),
            bundle_size_bytes=int(payload.get("bundle_size_bytes", 0)),
            semantic_content_truncated=bool(payload.get("semantic_content_truncated", False)),
            bundle_fingerprint=str(payload["bundle_fingerprint"]),
            max_bundle_bytes=int(
                payload.get("max_bundle_bytes", DEFAULT_SEMANTIC_BUNDLE_MAX_BYTES)
            ),
            schema_version=str(
                payload.get("schema_version", SOURCE_SEMANTIC_BUNDLE_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class EntityMatchAssessment:
    status: EntityMatchStatus
    confidence: EvaluationConfidence
    rationale: str
    evidence_refs: tuple[str, ...]
    assessment_method: AssessmentMethod

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntityMatchAssessment":
        return cls(
            status=EntityMatchStatus(str(payload["status"])),
            confidence=EvaluationConfidence(str(payload["confidence"])),
            rationale=str(payload.get("rationale", "")),
            evidence_refs=_string_tuple(payload.get("evidence_refs")),
            assessment_method=AssessmentMethod(str(payload["assessment_method"])),
        )


@dataclass(frozen=True)
class OfficialityAssessment:
    status: OfficialityStatus
    confidence: EvaluationConfidence
    rationale: str
    evidence_refs: tuple[str, ...]
    assessment_method: AssessmentMethod

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OfficialityAssessment":
        return cls(
            status=OfficialityStatus(str(payload["status"])),
            confidence=EvaluationConfidence(str(payload["confidence"])),
            rationale=str(payload.get("rationale", "")),
            evidence_refs=_string_tuple(payload.get("evidence_refs")),
            assessment_method=AssessmentMethod(str(payload["assessment_method"])),
        )


@dataclass(frozen=True)
class PageTypeAssessment:
    page_type: PageType
    confidence: EvaluationConfidence
    rationale: str
    evidence_refs: tuple[str, ...]
    assessment_method: AssessmentMethod

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PageTypeAssessment":
        return cls(
            page_type=PageType(str(payload["page_type"])),
            confidence=EvaluationConfidence(str(payload["confidence"])),
            rationale=str(payload.get("rationale", "")),
            evidence_refs=_string_tuple(payload.get("evidence_refs")),
            assessment_method=AssessmentMethod(str(payload["assessment_method"])),
        )


@dataclass(frozen=True)
class SurfaceDurabilityAssessment:
    status: SurfaceDurabilityStatus
    confidence: EvaluationConfidence
    rationale: str
    evidence_refs: tuple[str, ...]
    assessment_method: AssessmentMethod

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SurfaceDurabilityAssessment":
        return cls(
            status=SurfaceDurabilityStatus(str(payload["status"])),
            confidence=EvaluationConfidence(str(payload["confidence"])),
            rationale=str(payload.get("rationale", "")),
            evidence_refs=_string_tuple(payload.get("evidence_refs")),
            assessment_method=AssessmentMethod(str(payload["assessment_method"])),
        )


@dataclass(frozen=True)
class SourceRoleAssessment:
    planned_source_role: SourceRole
    observed_source_role: SourceRole | None
    source_role_match_status: SourceRoleMatchStatus
    confidence: EvaluationConfidence
    rationale: str
    evidence_refs: tuple[str, ...]
    assessment_method: AssessmentMethod

    def __post_init__(self) -> None:
        _require(isinstance(self.planned_source_role, SourceRole), "planned_source_role must be SourceRole.")
        if self.observed_source_role is not None:
            _require(isinstance(self.observed_source_role, SourceRole), "observed_source_role must be SourceRole.")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceRoleAssessment":
        return cls(
            planned_source_role=_source_role(payload["planned_source_role"]),
            observed_source_role=(
                _source_role(payload["observed_source_role"])
                if payload.get("observed_source_role") is not None
                else None
            ),
            source_role_match_status=SourceRoleMatchStatus(
                str(payload["source_role_match_status"])
            ),
            confidence=EvaluationConfidence(str(payload["confidence"])),
            rationale=str(payload.get("rationale", "")),
            evidence_refs=_string_tuple(payload.get("evidence_refs")),
            assessment_method=AssessmentMethod(str(payload["assessment_method"])),
        )


@dataclass(frozen=True)
class InformationNeedRelevanceAssessment:
    allowed_information_need_ids: tuple[str, ...]
    supported_information_need_ids: tuple[str, ...]
    relevance_level: RelevanceLevel
    confidence: EvaluationConfidence
    rationale: str
    evidence_refs: tuple[str, ...]
    assessment_method: AssessmentMethod

    def __post_init__(self) -> None:
        _require(
            set(self.supported_information_need_ids).issubset(
                set(self.allowed_information_need_ids)
            ),
            "supported_information_need_ids must be a subset of allowed_information_need_ids.",
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "InformationNeedRelevanceAssessment":
        return cls(
            allowed_information_need_ids=_string_tuple(payload.get("allowed_information_need_ids")),
            supported_information_need_ids=_string_tuple(payload.get("supported_information_need_ids")),
            relevance_level=RelevanceLevel(str(payload["relevance_level"])),
            confidence=EvaluationConfidence(str(payload["confidence"])),
            rationale=str(payload.get("rationale", "")),
            evidence_refs=_string_tuple(payload.get("evidence_refs")),
            assessment_method=AssessmentMethod(str(payload["assessment_method"])),
        )


@dataclass(frozen=True)
class InitialSourceEvaluation:
    initial_source_evaluation_id: str
    source_evaluation_plan_id: str
    source_inspection_id: str
    semantic_evidence_bundle_id: str
    candidate_source_id: str
    entity_id: str
    entity_match_assessment: EntityMatchAssessment
    officiality_assessment: OfficialityAssessment
    page_type_assessment: PageTypeAssessment
    surface_durability_assessment: SurfaceDurabilityAssessment
    source_role_assessment: SourceRoleAssessment
    information_need_relevance_assessment: InformationNeedRelevanceAssessment
    initial_monitoring_suitability: RelevanceLevel
    source_value: SourceValueLevel
    evaluation_confidence: EvaluationConfidence
    rationale: str
    review_flags: tuple[str, ...]
    decision: InitialEvaluationDecision
    evaluator_policy_version: str
    schema_version: str = INITIAL_SOURCE_EVALUATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "InitialSourceEvaluation":
        return cls(
            initial_source_evaluation_id=str(payload["initial_source_evaluation_id"]),
            source_evaluation_plan_id=str(payload["source_evaluation_plan_id"]),
            source_inspection_id=str(payload["source_inspection_id"]),
            semantic_evidence_bundle_id=str(payload["semantic_evidence_bundle_id"]),
            candidate_source_id=str(payload["candidate_source_id"]),
            entity_id=str(payload["entity_id"]),
            entity_match_assessment=EntityMatchAssessment.from_dict(
                _dict(payload.get("entity_match_assessment"))
            ),
            officiality_assessment=OfficialityAssessment.from_dict(
                _dict(payload.get("officiality_assessment"))
            ),
            page_type_assessment=PageTypeAssessment.from_dict(
                _dict(payload.get("page_type_assessment"))
            ),
            surface_durability_assessment=SurfaceDurabilityAssessment.from_dict(
                _dict(payload.get("surface_durability_assessment"))
            ),
            source_role_assessment=SourceRoleAssessment.from_dict(
                _dict(payload.get("source_role_assessment"))
            ),
            information_need_relevance_assessment=(
                InformationNeedRelevanceAssessment.from_dict(
                    _dict(payload.get("information_need_relevance_assessment"))
                )
            ),
            initial_monitoring_suitability=RelevanceLevel(
                str(payload["initial_monitoring_suitability"])
            ),
            source_value=SourceValueLevel(str(payload["source_value"])),
            evaluation_confidence=EvaluationConfidence(
                str(payload["evaluation_confidence"])
            ),
            rationale=str(payload.get("rationale", "")),
            review_flags=_string_tuple(payload.get("review_flags")),
            decision=InitialEvaluationDecision(str(payload["decision"])),
            evaluator_policy_version=str(payload.get("evaluator_policy_version", "")),
            schema_version=str(
                payload.get("schema_version", INITIAL_SOURCE_EVALUATION_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class SourceObservationPlan:
    source_observation_plan_id: str
    candidate_source_id: str
    initial_source_evaluation_id: str
    sampling_strategy: ObservationSamplingStrategy
    max_item_count: int
    lookback_window_days: int | None
    observation_policy_version: str
    input_fingerprint: str
    schema_version: str = SOURCE_OBSERVATION_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.max_item_count > 0, "max_item_count must be positive.")
        _require(
            self.max_item_count <= 100,
            "bounded observation plans must not encode unrestricted crawling.",
        )
        if self.lookback_window_days is not None:
            _require(self.lookback_window_days > 0, "lookback_window_days must be positive when set.")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceObservationPlan":
        return cls(
            source_observation_plan_id=str(payload["source_observation_plan_id"]),
            candidate_source_id=str(payload["candidate_source_id"]),
            initial_source_evaluation_id=str(payload["initial_source_evaluation_id"]),
            sampling_strategy=ObservationSamplingStrategy(str(payload["sampling_strategy"])),
            max_item_count=int(payload.get("max_item_count", 0)),
            lookback_window_days=_optional_int(payload.get("lookback_window_days")),
            observation_policy_version=str(payload.get("observation_policy_version", "")),
            input_fingerprint=str(payload["input_fingerprint"]),
            schema_version=str(
                payload.get("schema_version", SOURCE_OBSERVATION_PLAN_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class ObservedSourceEvidence:
    observed_evidence_id: str
    observation_plan_id: str
    candidate_source_id: str
    item_url: str
    item_title: str
    publication_date_hint: str | None
    content_type_hint: str | None
    relevant_information_need_ids: tuple[str, ...]
    signal_relevance: RelevanceLevel
    observation_provenance: dict[str, Any]
    fetch_execution_id: str | None = None
    inspection_id: str | None = None
    schema_version: str = OBSERVED_SOURCE_EVIDENCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ObservedSourceEvidence":
        return cls(
            observed_evidence_id=str(payload["observed_evidence_id"]),
            observation_plan_id=str(payload["observation_plan_id"]),
            candidate_source_id=str(payload["candidate_source_id"]),
            item_url=str(payload.get("item_url", "")),
            item_title=str(payload.get("item_title", "")),
            publication_date_hint=payload.get("publication_date_hint"),
            content_type_hint=payload.get("content_type_hint"),
            relevant_information_need_ids=_string_tuple(
                payload.get("relevant_information_need_ids")
            ),
            signal_relevance=RelevanceLevel(str(payload["signal_relevance"])),
            observation_provenance=_dict(payload.get("observation_provenance")),
            fetch_execution_id=payload.get("fetch_execution_id"),
            inspection_id=payload.get("inspection_id"),
            schema_version=str(
                payload.get("schema_version", OBSERVED_SOURCE_EVIDENCE_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class SourceObservationResult:
    source_observation_result_id: str
    source_observation_plan_id: str
    observation_status: ObservationStatus
    sampled_item_count: int
    recent_item_count: int | None
    relevant_item_count: int
    information_need_hit_count: dict[str, int]
    observed_date_span_start: str | None
    observed_date_span_end: str | None
    observed_evidence_ids: tuple[str, ...]
    failures: tuple[str, ...]
    diagnostics: tuple[str, ...]
    observation_policy_version: str
    schema_version: str = SOURCE_OBSERVATION_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.sampled_item_count >= 0, "sampled_item_count must be non-negative.")
        _require(self.relevant_item_count >= 0, "relevant_item_count must be non-negative.")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceObservationResult":
        return cls(
            source_observation_result_id=str(payload["source_observation_result_id"]),
            source_observation_plan_id=str(payload["source_observation_plan_id"]),
            observation_status=ObservationStatus(str(payload["observation_status"])),
            sampled_item_count=int(payload.get("sampled_item_count", 0)),
            recent_item_count=_optional_int(payload.get("recent_item_count")),
            relevant_item_count=int(payload.get("relevant_item_count", 0)),
            information_need_hit_count={
                str(key): int(value)
                for key, value in _dict(payload.get("information_need_hit_count")).items()
            },
            observed_date_span_start=payload.get("observed_date_span_start"),
            observed_date_span_end=payload.get("observed_date_span_end"),
            observed_evidence_ids=_string_tuple(payload.get("observed_evidence_ids")),
            failures=_string_tuple(payload.get("failures")),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            observation_policy_version=str(payload.get("observation_policy_version", "")),
            schema_version=str(
                payload.get("schema_version", SOURCE_OBSERVATION_RESULT_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class ObservedSignalPotential:
    observed_signal_potential_id: str
    source_observation_result_id: str
    level: ObservedSignalPotentialLevel
    sampled_item_count: int
    relevant_item_count: int
    information_need_hit_count: dict[str, int]
    supporting_observed_evidence_ids: tuple[str, ...]
    rationale: str
    limitations: tuple[str, ...]
    supporting_metrics: dict[str, Any] = field(default_factory=dict)
    schema_version: str = OBSERVED_SIGNAL_POTENTIAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        forbidden = ("cadence", "frequency", "weekly", "monthly", "long_term")
        _require(
            not any(any(part in key for part in forbidden) for key in self.supporting_metrics),
            "ObservedSignalPotential must not imply long-term cadence.",
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ObservedSignalPotential":
        return cls(
            observed_signal_potential_id=str(payload["observed_signal_potential_id"]),
            source_observation_result_id=str(payload["source_observation_result_id"]),
            level=ObservedSignalPotentialLevel(str(payload["level"])),
            sampled_item_count=int(payload.get("sampled_item_count", 0)),
            relevant_item_count=int(payload.get("relevant_item_count", 0)),
            information_need_hit_count={
                str(key): int(value)
                for key, value in _dict(payload.get("information_need_hit_count")).items()
            },
            supporting_observed_evidence_ids=_string_tuple(
                payload.get("supporting_observed_evidence_ids")
            ),
            rationale=str(payload.get("rationale", "")),
            limitations=_string_tuple(payload.get("limitations")),
            supporting_metrics=_dict(payload.get("supporting_metrics")),
            schema_version=str(
                payload.get("schema_version", OBSERVED_SIGNAL_POTENTIAL_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class FinalSourceEvaluation:
    final_source_evaluation_id: str
    initial_source_evaluation_id: str
    observation_result_id: str | None
    candidate_source_id: str
    entity_id: str
    source_value: SourceValueLevel
    evaluation_confidence: EvaluationConfidence
    observed_signal_potential: ObservedSignalPotential
    final_rationale: str
    review_flags: tuple[str, ...]
    final_decision: FinalEvaluationDecision
    policy_version: str
    input_fingerprint: str
    schema_version: str = FINAL_SOURCE_EVALUATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FinalSourceEvaluation":
        return cls(
            final_source_evaluation_id=str(payload["final_source_evaluation_id"]),
            initial_source_evaluation_id=str(payload["initial_source_evaluation_id"]),
            observation_result_id=payload.get("observation_result_id"),
            candidate_source_id=str(payload["candidate_source_id"]),
            entity_id=str(payload["entity_id"]),
            source_value=SourceValueLevel(str(payload["source_value"])),
            evaluation_confidence=EvaluationConfidence(str(payload["evaluation_confidence"])),
            observed_signal_potential=ObservedSignalPotential.from_dict(
                _dict(payload.get("observed_signal_potential"))
            ),
            final_rationale=str(payload.get("final_rationale", "")),
            review_flags=_string_tuple(payload.get("review_flags")),
            final_decision=FinalEvaluationDecision(str(payload["final_decision"])),
            policy_version=str(payload.get("policy_version", "")),
            input_fingerprint=str(payload["input_fingerprint"]),
            schema_version=str(
                payload.get("schema_version", FINAL_SOURCE_EVALUATION_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True)
class RejectedSourceEvaluationRecord:
    rejected_source_evaluation_id: str
    candidate_source_id: str | None
    source_evaluation_plan_id: str | None
    entity_id: str | None
    rejection_reason: str
    diagnostics: tuple[str, ...]
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: str = REJECTED_SOURCE_EVALUATION_RECORD_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RejectedSourceEvaluationRecord":
        return cls(
            rejected_source_evaluation_id=str(payload["rejected_source_evaluation_id"]),
            candidate_source_id=payload.get("candidate_source_id"),
            source_evaluation_plan_id=payload.get("source_evaluation_plan_id"),
            entity_id=payload.get("entity_id"),
            rejection_reason=str(payload.get("rejection_reason", "")),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            provenance=_dict(payload.get("provenance")),
            schema_version=str(
                payload.get(
                    "schema_version",
                    REJECTED_SOURCE_EVALUATION_RECORD_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class SourceEvaluationResult:
    upstream_phase4_input_fingerprint: str
    upstream_phase4_output_hash: str
    evaluation_plans: tuple[SourceEvaluationPlan, ...]
    fetch_executions: tuple[SourceFetchExecution, ...]
    inspections: tuple[SourceInspection, ...]
    initial_evaluations: tuple[InitialSourceEvaluation, ...]
    observation_plans: tuple[SourceObservationPlan, ...]
    observation_results: tuple[SourceObservationResult, ...]
    final_evaluations: tuple[FinalSourceEvaluation, ...]
    rejected_evaluation_records: tuple[RejectedSourceEvaluationRecord, ...]
    diagnostics: tuple[str, ...]
    input_fingerprint: str
    output_hash: str
    generation_mode: str
    schema_version: str = SOURCE_EVALUATION_RESULT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceEvaluationResult":
        return cls(
            upstream_phase4_input_fingerprint=str(
                payload.get("upstream_phase4_input_fingerprint", "")
            ),
            upstream_phase4_output_hash=str(payload.get("upstream_phase4_output_hash", "")),
            evaluation_plans=tuple(
                SourceEvaluationPlan.from_dict(item)
                for item in _dict_items(payload.get("evaluation_plans"))
            ),
            fetch_executions=tuple(
                SourceFetchExecution.from_dict(item)
                for item in _dict_items(payload.get("fetch_executions"))
            ),
            inspections=tuple(
                SourceInspection.from_dict(item)
                for item in _dict_items(payload.get("inspections"))
            ),
            initial_evaluations=tuple(
                InitialSourceEvaluation.from_dict(item)
                for item in _dict_items(payload.get("initial_evaluations"))
            ),
            observation_plans=tuple(
                SourceObservationPlan.from_dict(item)
                for item in _dict_items(payload.get("observation_plans"))
            ),
            observation_results=tuple(
                SourceObservationResult.from_dict(item)
                for item in _dict_items(payload.get("observation_results"))
            ),
            final_evaluations=tuple(
                FinalSourceEvaluation.from_dict(item)
                for item in _dict_items(payload.get("final_evaluations"))
            ),
            rejected_evaluation_records=tuple(
                RejectedSourceEvaluationRecord.from_dict(item)
                for item in _dict_items(payload.get("rejected_evaluation_records"))
            ),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            input_fingerprint=str(payload["input_fingerprint"]),
            output_hash=str(payload["output_hash"]),
            generation_mode=str(payload.get("generation_mode", "loaded_from_cache")),
            schema_version=str(
                payload.get("schema_version", SOURCE_EVALUATION_RESULT_SCHEMA_VERSION)
            ),
        )


def eligible_phase5_candidate_sources(
    *,
    accepted_candidates: tuple[CandidateSource, ...],
    needs_review_candidates: tuple[CandidateSource, ...],
) -> tuple[tuple[CandidateSource, CandidateSourceStatus], ...]:
    ordered = tuple((item, CandidateSourceStatus.ACCEPTED) for item in accepted_candidates)
    ordered += tuple(
        (item, CandidateSourceStatus.NEEDS_REVIEW) for item in needs_review_candidates
    )
    return ordered


def validate_source_evaluation_plans(
    *,
    plans: tuple[SourceEvaluationPlan, ...],
    candidate_sources: tuple[CandidateSource, ...],
    allowed_information_need_ids: tuple[str, ...],
    source_discovery_evidence_ids: tuple[str, ...] = (),
) -> tuple[str, ...]:
    errors: list[str] = []
    candidates_by_id = {item.candidate_source_id: item for item in candidate_sources}
    allowed_needs = set(allowed_information_need_ids)
    known_evidence = set(source_discovery_evidence_ids)

    for plan in plans:
        candidate = candidates_by_id.get(plan.candidate_source_id)
        if candidate is None:
            errors.append(f"unknown CandidateSource: {plan.candidate_source_id}")
            continue
        if plan.entity_id != candidate.entity_id:
            errors.append(f"entity mismatch for {plan.source_evaluation_plan_id}")
        if plan.planned_source_role != candidate.source_role:
            errors.append(f"SourceRole mismatch for {plan.source_evaluation_plan_id}")
        if not set(plan.allowed_information_need_ids).issubset(allowed_needs):
            errors.append(f"unknown InformationNeed IDs for {plan.source_evaluation_plan_id}")
        if known_evidence and not set(plan.supporting_source_discovery_evidence_ids).issubset(known_evidence):
            errors.append(f"unknown evidence refs for {plan.source_evaluation_plan_id}")

    return tuple(errors)


def validate_source_evaluation_result_references(result: SourceEvaluationResult) -> tuple[str, ...]:
    errors: list[str] = []
    plan_ids = {item.source_evaluation_plan_id for item in result.evaluation_plans}
    candidate_ids = {item.candidate_source_id for item in result.evaluation_plans}
    fetch_ids = {item.source_fetch_execution_id for item in result.fetch_executions}
    inspection_ids = {item.inspection_id for item in result.inspections}
    initial_ids = {item.initial_source_evaluation_id for item in result.initial_evaluations}
    observation_plan_ids = {item.source_observation_plan_id for item in result.observation_plans}
    observation_result_ids = {item.source_observation_result_id for item in result.observation_results}

    for fetch in result.fetch_executions:
        if fetch.source_evaluation_plan_id not in plan_ids:
            errors.append(f"fetch references unknown plan: {fetch.source_evaluation_plan_id}")
    for inspection in result.inspections:
        if inspection.fetch_execution_id not in fetch_ids:
            errors.append(f"inspection references unknown fetch: {inspection.fetch_execution_id}")
    for evaluation in result.initial_evaluations:
        if evaluation.source_evaluation_plan_id not in plan_ids:
            errors.append(f"initial evaluation references unknown plan: {evaluation.source_evaluation_plan_id}")
        if evaluation.source_inspection_id not in inspection_ids:
            errors.append(f"initial evaluation references unknown inspection: {evaluation.source_inspection_id}")
    for observation_plan in result.observation_plans:
        if observation_plan.initial_source_evaluation_id not in initial_ids:
            errors.append("observation plan references unknown initial evaluation")
    for observation_result in result.observation_results:
        if observation_result.source_observation_plan_id not in observation_plan_ids:
            errors.append("observation result references unknown observation plan")
    for final in result.final_evaluations:
        if final.initial_source_evaluation_id not in initial_ids:
            errors.append("final evaluation references unknown initial evaluation")
        if final.candidate_source_id not in candidate_ids:
            errors.append("final evaluation references unknown candidate source")
        if final.observation_result_id and final.observation_result_id not in observation_result_ids:
            errors.append("final evaluation references unknown observation result")

    return tuple(errors)


def _source_role(value: Any) -> SourceRole:
    if isinstance(value, SourceRole):
        return value
    text = str(value)
    try:
        return SourceRole(text)
    except ValueError as error:
        normalized = " ".join(text.casefold().replace("_", " ").split())
        aliases = {
            "homepage": SourceRole.OFFICIAL_HOMEPAGE,
            "official website": SourceRole.OFFICIAL_HOMEPAGE,
            "news": SourceRole.NEWSROOM,
            "media": SourceRole.NEWSROOM,
            "press": SourceRole.PRESS_RELEASES,
            "announcements": SourceRole.PRESS_RELEASES,
            "research": SourceRole.RESEARCH_PUBLICATIONS,
            "publications": SourceRole.RESEARCH_PUBLICATIONS,
            "jobs": SourceRole.CAREERS,
            "recruiting": SourceRole.CAREERS,
            "companies": SourceRole.PORTFOLIO,
            "investments": SourceRole.PORTFOLIO,
            "events": SourceRole.EVENTS_OR_PROGRAMS,
            "programs": SourceRole.EVENTS_OR_PROGRAMS,
        }
        if normalized in aliases:
            return aliases[normalized]
        raise error


def _reject_forbidden_payload_keys(payload: Any, model_name: str) -> None:
    if isinstance(payload, dict):
        forbidden = {
            "raw_html",
            "html",
            "raw_body",
            "body",
            "raw_bytes",
            "decoded_text",
            "script",
            "scripts",
            "style",
            "styles",
            "fetched_page",
        }
        for key, value in payload.items():
            if str(key).casefold() in forbidden:
                raise ValueError(f"{model_name} must not embed raw webpage content.")
            _reject_forbidden_payload_keys(value, model_name)
    elif isinstance(payload, list):
        for item in payload:
            _reject_forbidden_payload_keys(item, model_name)


def _reject_raw_webpage_content(text: str, context: str) -> None:
    lowered = text.casefold()
    forbidden_fragments = ("<html", "<body", "<script", "</script", "<style", "</style")
    if any(fragment in lowered for fragment in forbidden_fragments):
        raise ValueError(f"{context} must not embed raw HTML/script/style content.")


def _looks_absolute_path(value: str) -> bool:
    return value.startswith(("/", "\\")) or (len(value) > 2 and value[1:3] == ":\\")


def _hop_orders_are_stable(hops: tuple[RedirectHop, ...]) -> bool:
    return tuple(item.hop_order for item in hops) == tuple(range(len(hops)))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict_items(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if item is not None)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)
