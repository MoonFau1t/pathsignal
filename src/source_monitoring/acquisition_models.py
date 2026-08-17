from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.source_monitoring.models import _json_ready
from src.source_monitoring.source_discovery_models import SourceFormatHint, SourceRole
from src.source_monitoring.source_evaluation_models import FetchStatus


PHASE5_ACQUISITION_HANDOFF_SCHEMA_VERSION = "phase5_acquisition_handoff_v1"
ACQUISITION_RESOLUTION_PLAN_SCHEMA_VERSION = "acquisition_resolution_plan_v1"
FEED_HINT_EVIDENCE_REF_SCHEMA_VERSION = "feed_hint_evidence_ref_v1"
DEFERRED_FEED_CANDIDATE_SCHEMA_VERSION = "deferred_feed_candidate_v1"
FEED_VERIFICATION_PLAN_SCHEMA_VERSION = "feed_verification_plan_v1"
FEED_VERIFICATION_RESULT_SCHEMA_VERSION = "feed_verification_result_v1"
SELECTED_WEBSITE_RESOLUTION_PLAN_SCHEMA_VERSION = "selected_website_resolution_plan_v1"
SELECTED_WEBSITE_RESOLUTION_RESULT_SCHEMA_VERSION = "selected_website_resolution_result_v1"
SELECTED_WEBSITE_ACQUISITION_CONFIG_SCHEMA_VERSION = "selected_website_acquisition_config_v1"
ACQUISITION_RESOLUTION_SCHEMA_VERSION = "acquisition_resolution_v1"
PHASE7_MONITORING_HANDOFF_SCHEMA_VERSION = "phase7_monitoring_handoff_v1"
ACQUISITION_PLANNING_RESULT_SCHEMA_VERSION = "phase6a_acquisition_planning_result_v1"


class AcquisitionMethod(str, Enum):
    RSS = "rss"
    ATOM = "atom"
    SELECTED_WEBSITE = "selected_website"


class AcquisitionResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    NEEDS_REVIEW = "needs_review"
    UNSUPPORTED = "unsupported"


class FeedFormat(str, Enum):
    RSS = "rss"
    ATOM = "atom"
    UNKNOWN = "unknown"


class FeedVerificationStatus(str, Enum):
    VERIFIED_USABLE = "verified_usable"
    VERIFIED_BUT_LIMITED = "verified_but_limited"
    INVALID_FEED = "invalid_feed"
    UNREACHABLE = "unreachable"
    UNSUPPORTED_CONTENT = "unsupported_content"
    PARSE_FAILURE = "parse_failure"
    EMPTY_OR_INSUFFICIENT = "empty_or_insufficient"
    NEEDS_REVIEW = "needs_review"


class FeedParseStatus(str, Enum):
    NOT_PARSED = "not_parsed"
    PARSED_VALID = "parsed_valid"
    PARSED_INVALID = "parsed_invalid"
    PARSE_FAILURE = "parse_failure"


class SelectedWebsiteResolutionStatus(str, Enum):
    FEASIBLE = "feasible"
    NEEDS_REVIEW = "needs_review"
    UNSUPPORTED = "unsupported"


class PlanStatus(str, Enum):
    PLANNED = "planned"
    DEFERRED = "deferred"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED_DUE_TO_PRIOR_RESOLUTION = "skipped_due_to_prior_resolution"


@dataclass(frozen=True)
class Phase5AcquisitionHandoff:
    candidate_source_id: str
    entity_id: str
    final_source_evaluation_id: str
    source_url: str
    observed_source_role: SourceRole
    supported_information_need_ids: tuple[str, ...]
    source_value: str
    evaluation_confidence: str
    reason_codes: tuple[str, ...]
    final_source_evaluation_fingerprint: str
    phase5_handoff_fingerprint: str
    schema_version: str = PHASE5_ACQUISITION_HANDOFF_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Phase5AcquisitionHandoff":
        return cls(
            candidate_source_id=str(payload["candidate_source_id"]),
            entity_id=str(payload["entity_id"]),
            final_source_evaluation_id=str(payload["final_source_evaluation_id"]),
            source_url=str(payload.get("source_url", "")),
            observed_source_role=SourceRole(str(payload["observed_source_role"])),
            supported_information_need_ids=_string_tuple(payload.get("supported_information_need_ids")),
            source_value=str(payload.get("source_value", "")),
            evaluation_confidence=str(payload.get("evaluation_confidence", "")),
            reason_codes=_string_tuple(payload.get("reason_codes")),
            final_source_evaluation_fingerprint=str(payload["final_source_evaluation_fingerprint"]),
            phase5_handoff_fingerprint=str(payload["phase5_handoff_fingerprint"]),
            schema_version=str(payload.get("schema_version", PHASE5_ACQUISITION_HANDOFF_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class FeedHintEvidenceRef:
    feed_hint_reference_id: str
    source_inspection_id: str
    source_inspection_hash: str
    hint_index: int
    href: str
    normalized_url: str
    rel: str
    mime_type: str
    title: str | None
    candidate_format_hint: SourceFormatHint
    verification_status: str
    schema_version: str = FEED_HINT_EVIDENCE_REF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.hint_index >= 0, "hint_index must be non-negative.")
        _require(
            self.verification_status == "unverified",
            "Phase 6A feed hint evidence must remain unverified.",
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeedHintEvidenceRef":
        return cls(
            feed_hint_reference_id=str(payload["feed_hint_reference_id"]),
            source_inspection_id=str(payload["source_inspection_id"]),
            source_inspection_hash=str(payload["source_inspection_hash"]),
            hint_index=int(payload.get("hint_index", 0)),
            href=str(payload.get("href", "")),
            normalized_url=str(payload.get("normalized_url", "")),
            rel=str(payload.get("rel", "")),
            mime_type=str(payload.get("mime_type", "")),
            title=payload.get("title"),
            candidate_format_hint=SourceFormatHint(str(payload.get("candidate_format_hint", SourceFormatHint.UNKNOWN.value))),
            verification_status=str(payload.get("verification_status", "unverified")),
            schema_version=str(payload.get("schema_version", FEED_HINT_EVIDENCE_REF_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class DeferredFeedCandidate:
    deferred_feed_candidate_id: str
    acquisition_resolution_plan_id: str
    candidate_source_id: str
    normalized_url: str
    feed_hint_evidence_refs: tuple[FeedHintEvidenceRef, ...]
    deferral_reason: str
    plan_status: PlanStatus = PlanStatus.DEFERRED
    schema_version: str = DEFERRED_FEED_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.plan_status == PlanStatus.DEFERRED, "deferred feed candidates must be deferred.")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DeferredFeedCandidate":
        return cls(
            deferred_feed_candidate_id=str(payload["deferred_feed_candidate_id"]),
            acquisition_resolution_plan_id=str(payload["acquisition_resolution_plan_id"]),
            candidate_source_id=str(payload["candidate_source_id"]),
            normalized_url=str(payload.get("normalized_url", "")),
            feed_hint_evidence_refs=tuple(
                FeedHintEvidenceRef.from_dict(item) for item in _dict_items(payload.get("feed_hint_evidence_refs"))
            ),
            deferral_reason=str(payload.get("deferral_reason", "")),
            plan_status=PlanStatus(str(payload.get("plan_status", PlanStatus.DEFERRED.value))),
            schema_version=str(payload.get("schema_version", DEFERRED_FEED_CANDIDATE_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class AcquisitionResolutionPlan:
    acquisition_resolution_plan_id: str
    candidate_source_id: str
    entity_id: str
    final_source_evaluation_id: str
    source_url: str
    observed_source_role: SourceRole
    supported_information_need_ids: tuple[str, ...]
    phase5_handoff_fingerprint: str
    final_source_evaluation_fingerprint: str
    source_inspection_id: str | None
    source_inspection_hash: str | None
    source_observation_result_id: str | None
    source_observation_result_hash: str | None
    known_technical_limitation_flags: tuple[str, ...]
    strategy_order: tuple[str, ...]
    feed_candidate_count: int
    executable_feed_verification_plan_count: int
    deferred_feed_candidate_count: int
    selected_website_fallback_planned: bool
    dependency_model: dict[str, Any]
    planning_policy_version: str
    input_fingerprint: str
    plan_status: PlanStatus = PlanStatus.PLANNED
    schema_version: str = ACQUISITION_RESOLUTION_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.plan_status == PlanStatus.PLANNED, "Phase 6A produces planned acquisition work only.")
        _require(
            "acquisition_method" not in self.dependency_model,
            "planning must not select a final acquisition method.",
        )
        _require(self.feed_candidate_count >= 0, "feed_candidate_count must be non-negative.")
        _require(
            self.executable_feed_verification_plan_count >= 0,
            "executable feed plan count must be non-negative.",
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AcquisitionResolutionPlan":
        return cls(
            acquisition_resolution_plan_id=str(payload["acquisition_resolution_plan_id"]),
            candidate_source_id=str(payload["candidate_source_id"]),
            entity_id=str(payload["entity_id"]),
            final_source_evaluation_id=str(payload["final_source_evaluation_id"]),
            source_url=str(payload.get("source_url", "")),
            observed_source_role=SourceRole(str(payload["observed_source_role"])),
            supported_information_need_ids=_string_tuple(payload.get("supported_information_need_ids")),
            phase5_handoff_fingerprint=str(payload["phase5_handoff_fingerprint"]),
            final_source_evaluation_fingerprint=str(payload["final_source_evaluation_fingerprint"]),
            source_inspection_id=payload.get("source_inspection_id"),
            source_inspection_hash=payload.get("source_inspection_hash"),
            source_observation_result_id=payload.get("source_observation_result_id"),
            source_observation_result_hash=payload.get("source_observation_result_hash"),
            known_technical_limitation_flags=_string_tuple(payload.get("known_technical_limitation_flags")),
            strategy_order=_string_tuple(payload.get("strategy_order")),
            feed_candidate_count=int(payload.get("feed_candidate_count", 0)),
            executable_feed_verification_plan_count=int(payload.get("executable_feed_verification_plan_count", 0)),
            deferred_feed_candidate_count=int(payload.get("deferred_feed_candidate_count", 0)),
            selected_website_fallback_planned=bool(payload.get("selected_website_fallback_planned", False)),
            dependency_model=dict(payload.get("dependency_model") or {}),
            planning_policy_version=str(payload.get("planning_policy_version", "")),
            input_fingerprint=str(payload["input_fingerprint"]),
            plan_status=PlanStatus(str(payload.get("plan_status", PlanStatus.PLANNED.value))),
            schema_version=str(payload.get("schema_version", ACQUISITION_RESOLUTION_PLAN_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class FeedVerificationPlan:
    feed_verification_plan_id: str
    acquisition_resolution_plan_id: str
    candidate_source_id: str
    final_source_evaluation_id: str
    feed_candidate_url: str
    feed_hint_evidence_refs: tuple[FeedHintEvidenceRef, ...]
    candidate_format_hint: SourceFormatHint
    verification_policy_version: str
    fetch_policy_ref: dict[str, Any]
    parser_policy_version: str
    input_fingerprint: str
    plan_status: PlanStatus = PlanStatus.PLANNED
    schema_version: str = FEED_VERIFICATION_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.plan_status == PlanStatus.PLANNED, "feed verification plans are not executed in Phase 6A.")
        _require(self.feed_hint_evidence_refs, "feed verification plans require hint evidence references.")
        _require(
            all(item.verification_status == "unverified" for item in self.feed_hint_evidence_refs),
            "feed hints must remain unverified until Phase 6B.",
        )
        _require(
            self.candidate_format_hint in {SourceFormatHint.RSS_CANDIDATE, SourceFormatHint.ATOM_CANDIDATE, SourceFormatHint.UNKNOWN},
            "feed verification plans must target feed-like candidates.",
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeedVerificationPlan":
        return cls(
            feed_verification_plan_id=str(payload["feed_verification_plan_id"]),
            acquisition_resolution_plan_id=str(payload["acquisition_resolution_plan_id"]),
            candidate_source_id=str(payload["candidate_source_id"]),
            final_source_evaluation_id=str(payload["final_source_evaluation_id"]),
            feed_candidate_url=str(payload.get("feed_candidate_url", "")),
            feed_hint_evidence_refs=tuple(
                FeedHintEvidenceRef.from_dict(item) for item in _dict_items(payload.get("feed_hint_evidence_refs"))
            ),
            candidate_format_hint=SourceFormatHint(str(payload.get("candidate_format_hint", SourceFormatHint.UNKNOWN.value))),
            verification_policy_version=str(payload.get("verification_policy_version", "")),
            fetch_policy_ref=dict(payload.get("fetch_policy_ref") or {}),
            parser_policy_version=str(payload.get("parser_policy_version", "")),
            input_fingerprint=str(payload["input_fingerprint"]),
            plan_status=PlanStatus(str(payload.get("plan_status", PlanStatus.PLANNED.value))),
            schema_version=str(payload.get("schema_version", FEED_VERIFICATION_PLAN_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class FeedVerificationResult:
    feed_verification_result_id: str
    feed_verification_plan_id: str
    candidate_source_id: str
    feed_candidate_url: str
    final_url: str | None
    fetch_execution_id: str | None
    fetch_status: FetchStatus | None
    http_status: int | None
    content_type: str | None
    redirect_chain: tuple[dict[str, Any], ...]
    parse_status: FeedParseStatus
    verified_feed_format: FeedFormat
    feed_title: str | None
    feed_home_link: str | None
    sampled_entry_count: int
    valid_entry_url_count: int
    title_support: bool
    publication_date_support: bool
    stable_item_identity_support: bool
    syntax_valid: bool
    usable_for_monitoring: bool
    verification_status: FeedVerificationStatus
    failure_reason: str | None
    diagnostics: tuple[str, ...]
    verification_policy_version: str
    input_fingerprint: str
    schema_version: str = FEED_VERIFICATION_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.sampled_entry_count >= 0, "sampled_entry_count must be non-negative.")
        _require(self.valid_entry_url_count >= 0, "valid_entry_url_count must be non-negative.")
        if self.verification_status == FeedVerificationStatus.VERIFIED_USABLE:
            _require(self.syntax_valid, "usable feed verification requires valid feed syntax.")
            _require(self.usable_for_monitoring, "usable feed verification requires monitoring usability.")
            _require(self.valid_entry_url_count > 0, "usable feeds require usable entry URLs.")
        if self.verification_status == FeedVerificationStatus.VERIFIED_BUT_LIMITED:
            _require(self.syntax_valid, "limited verified feeds must still be syntactically valid.")
            _require(not self.usable_for_monitoring, "limited feeds are valid but not resolved as usable.")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class SelectedWebsiteResolutionPlan:
    selected_website_resolution_plan_id: str
    acquisition_resolution_plan_id: str
    candidate_source_id: str
    final_source_evaluation_id: str
    source_url: str
    source_inspection_id: str | None
    source_inspection_hash: str | None
    source_observation_result_id: str | None
    source_observation_result_hash: str | None
    observed_source_role: SourceRole
    evidence_input_refs: tuple[str, ...]
    execution_dependency: dict[str, Any]
    resolution_policy_version: str
    input_fingerprint: str
    plan_status: PlanStatus = PlanStatus.PLANNED
    schema_version: str = SELECTED_WEBSITE_RESOLUTION_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(self.plan_status == PlanStatus.PLANNED, "website resolution plans are not executed in Phase 6A.")
        _require(
            self.execution_dependency.get("condition") == "execute_if_no_verified_usable_feed",
            "website fallback dependency must be explicit.",
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SelectedWebsiteResolutionPlan":
        return cls(
            selected_website_resolution_plan_id=str(payload["selected_website_resolution_plan_id"]),
            acquisition_resolution_plan_id=str(payload["acquisition_resolution_plan_id"]),
            candidate_source_id=str(payload["candidate_source_id"]),
            final_source_evaluation_id=str(payload["final_source_evaluation_id"]),
            source_url=str(payload.get("source_url", "")),
            source_inspection_id=payload.get("source_inspection_id"),
            source_inspection_hash=payload.get("source_inspection_hash"),
            source_observation_result_id=payload.get("source_observation_result_id"),
            source_observation_result_hash=payload.get("source_observation_result_hash"),
            observed_source_role=SourceRole(str(payload["observed_source_role"])),
            evidence_input_refs=_string_tuple(payload.get("evidence_input_refs")),
            execution_dependency=dict(payload.get("execution_dependency") or {}),
            resolution_policy_version=str(payload.get("resolution_policy_version", "")),
            input_fingerprint=str(payload["input_fingerprint"]),
            plan_status=PlanStatus(str(payload.get("plan_status", PlanStatus.PLANNED.value))),
            schema_version=str(payload.get("schema_version", SELECTED_WEBSITE_RESOLUTION_PLAN_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class SelectedWebsiteAcquisitionConfig:
    selected_website_acquisition_config_id: str
    source_url: str
    acquisition_method: AcquisitionMethod
    item_discovery_strategy_version: str
    allowed_domain_scope: tuple[str, ...]
    item_link_normalization_policy: str
    max_discovered_items_per_run: int
    title_extraction_strategy_ref: str | None
    date_extraction_strategy_ref: str | None
    dedup_identity_strategy_version: str
    source_role: SourceRole
    provenance: dict[str, Any]
    input_fingerprint: str
    schema_version: str = SELECTED_WEBSITE_ACQUISITION_CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(
            self.acquisition_method == AcquisitionMethod.SELECTED_WEBSITE,
            "selected website configs must use selected_website acquisition method.",
        )
        _require(self.max_discovered_items_per_run > 0, "max_discovered_items_per_run must be positive.")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class SelectedWebsiteResolutionResult:
    selected_website_resolution_result_id: str
    selected_website_resolution_plan_id: str
    candidate_source_id: str
    final_source_evaluation_id: str
    source_url: str
    feasibility_status: SelectedWebsiteResolutionStatus
    candidate_item_link_discoverability: str
    normalized_item_url_support: bool
    item_title_support: bool
    date_hint_support: bool
    item_type_role_support: bool
    bounded_extraction_consistency: str
    technical_limitations: tuple[str, ...]
    selected_website_acquisition_config: SelectedWebsiteAcquisitionConfig | None
    reason_codes: tuple[str, ...]
    resolution_policy_version: str
    input_fingerprint: str
    schema_version: str = SELECTED_WEBSITE_RESOLUTION_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.feasibility_status == SelectedWebsiteResolutionStatus.FEASIBLE:
            _require(
                self.selected_website_acquisition_config is not None,
                "feasible selected website resolution requires a config reference.",
            )
        if self.feasibility_status != SelectedWebsiteResolutionStatus.FEASIBLE:
            _require(
                self.selected_website_acquisition_config is None,
                "unresolved website results must not carry acquisition config.",
            )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class AcquisitionResolution:
    acquisition_resolution_id: str
    acquisition_resolution_plan_id: str
    candidate_source_id: str
    entity_id: str
    final_source_evaluation_id: str
    source_url: str
    resolution_status: AcquisitionResolutionStatus
    acquisition_method: AcquisitionMethod | None
    feed_verification_result_ids: tuple[str, ...]
    selected_website_resolution_result_id: str | None
    selected_acquisition_config_ref: str | None
    verified_feed_format: FeedFormat | None
    technical_limitation_flags: tuple[str, ...]
    resolution_reason_codes: tuple[str, ...]
    evidence_quality: str
    resolution_policy_version: str
    input_fingerprint: str
    schema_version: str = ACQUISITION_RESOLUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.resolution_status == AcquisitionResolutionStatus.RESOLVED:
            _require(self.acquisition_method is not None, "resolved acquisition requires an acquisition method.")
            if self.acquisition_method == AcquisitionMethod.RSS:
                _require(self.verified_feed_format == FeedFormat.RSS, "rss resolution requires RSS feed evidence.")
                _require(self.feed_verification_result_ids, "rss resolution requires feed verification evidence.")
            if self.acquisition_method == AcquisitionMethod.ATOM:
                _require(self.verified_feed_format == FeedFormat.ATOM, "atom resolution requires Atom feed evidence.")
                _require(self.feed_verification_result_ids, "atom resolution requires feed verification evidence.")
            if self.acquisition_method == AcquisitionMethod.SELECTED_WEBSITE:
                _require(
                    self.selected_website_resolution_result_id and self.selected_acquisition_config_ref,
                    "selected_website resolution requires feasible website evidence and config.",
                )
        else:
            _require(self.acquisition_method is None, "unresolved acquisition must not select a method.")
            _require(self.selected_acquisition_config_ref is None, "unresolved acquisition must not select config.")

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class Phase7MonitoringHandoff:
    phase7_monitoring_handoff_id: str
    acquisition_resolution_id: str
    candidate_source_id: str
    entity_id: str
    source_url: str
    acquisition_method: AcquisitionMethod
    acquisition_config_ref: str
    supported_information_need_ids: tuple[str, ...]
    source_role: SourceRole
    provenance: dict[str, Any]
    schema_version: str = PHASE7_MONITORING_HANDOFF_SCHEMA_VERSION

    @classmethod
    def from_resolution(
        cls,
        *,
        phase7_monitoring_handoff_id: str,
        resolution: AcquisitionResolution,
        supported_information_need_ids: tuple[str, ...],
        source_role: SourceRole,
        provenance: dict[str, Any],
    ) -> "Phase7MonitoringHandoff":
        _require(
            resolution.resolution_status == AcquisitionResolutionStatus.RESOLVED,
            "Phase 7 handoff accepts only resolved acquisition resolutions.",
        )
        _require(resolution.acquisition_method is not None, "resolved acquisition method required.")
        _require(resolution.selected_acquisition_config_ref or resolution.feed_verification_result_ids, "resolved acquisition evidence required.")
        return cls(
            phase7_monitoring_handoff_id=phase7_monitoring_handoff_id,
            acquisition_resolution_id=resolution.acquisition_resolution_id,
            candidate_source_id=resolution.candidate_source_id,
            entity_id=resolution.entity_id,
            source_url=resolution.source_url,
            acquisition_method=resolution.acquisition_method,
            acquisition_config_ref=resolution.selected_acquisition_config_ref
            or ",".join(resolution.feed_verification_result_ids),
            supported_information_need_ids=supported_information_need_ids,
            source_role=source_role,
            provenance=provenance,
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Phase7MonitoringHandoff":
        return cls(
            phase7_monitoring_handoff_id=str(
                payload["phase7_monitoring_handoff_id"]
            ),
            acquisition_resolution_id=str(
                payload["acquisition_resolution_id"]
            ),
            candidate_source_id=str(payload["candidate_source_id"]),
            entity_id=str(payload["entity_id"]),
            source_url=str(payload.get("source_url", "")),
            acquisition_method=AcquisitionMethod(
                str(payload["acquisition_method"])
            ),
            acquisition_config_ref=str(payload["acquisition_config_ref"]),
            supported_information_need_ids=_string_tuple(
                payload.get("supported_information_need_ids")
            ),
            source_role=SourceRole(str(payload["source_role"])),
            provenance=dict(payload.get("provenance") or {}),
            schema_version=str(
                payload.get(
                    "schema_version",
                    PHASE7_MONITORING_HANDOFF_SCHEMA_VERSION,
                )
            ),
        )


@dataclass(frozen=True)
class AcquisitionPlanningResult:
    acquisition_resolution_plans: tuple[AcquisitionResolutionPlan, ...]
    feed_verification_plans: tuple[FeedVerificationPlan, ...]
    selected_website_resolution_plans: tuple[SelectedWebsiteResolutionPlan, ...]
    deferred_feed_candidates: tuple[DeferredFeedCandidate, ...]
    diagnostics: tuple[str, ...]
    phase5_handoff_input_hash: str
    approved_input_count: int
    planning_policy_version: str
    input_fingerprint: str
    output_hash: str
    generation: dict[str, Any] = field(default_factory=dict)
    schema_version: str = ACQUISITION_PLANNING_RESULT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AcquisitionPlanningResult":
        return cls(
            acquisition_resolution_plans=tuple(
                AcquisitionResolutionPlan.from_dict(item)
                for item in _dict_items(payload.get("acquisition_resolution_plans"))
            ),
            feed_verification_plans=tuple(
                FeedVerificationPlan.from_dict(item)
                for item in _dict_items(payload.get("feed_verification_plans"))
            ),
            selected_website_resolution_plans=tuple(
                SelectedWebsiteResolutionPlan.from_dict(item)
                for item in _dict_items(payload.get("selected_website_resolution_plans"))
            ),
            deferred_feed_candidates=tuple(
                DeferredFeedCandidate.from_dict(item)
                for item in _dict_items(payload.get("deferred_feed_candidates"))
            ),
            diagnostics=_string_tuple(payload.get("diagnostics")),
            phase5_handoff_input_hash=str(payload.get("phase5_handoff_input_hash", "")),
            approved_input_count=int(payload.get("approved_input_count", 0)),
            planning_policy_version=str(payload.get("planning_policy_version", "")),
            input_fingerprint=str(payload.get("input_fingerprint", "")),
            output_hash=str(payload.get("output_hash", "")),
            generation=dict(payload.get("generation") or {}),
            schema_version=str(payload.get("schema_version", ACQUISITION_PLANNING_RESULT_SCHEMA_VERSION)),
        )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _dict_items(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    return tuple(dict(item) for item in value)
