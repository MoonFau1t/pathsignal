from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.config import PROJECT_ROOT
from src.database.planning_identity import hash_canonical_value
from src.source_monitoring.acquisition_identity import (
    build_selected_website_acquisition_config_fingerprint,
    build_selected_website_acquisition_config_id,
    build_selected_website_resolution_output_hash,
    build_selected_website_resolution_result_fingerprint,
    build_selected_website_resolution_result_id,
)
from src.source_monitoring.acquisition_models import (
    AcquisitionMethod,
    AcquisitionPlanningResult,
    AcquisitionResolutionPlan,
    SelectedWebsiteAcquisitionConfig,
    SelectedWebsiteResolutionPlan,
    SelectedWebsiteResolutionResult,
    SelectedWebsiteResolutionStatus,
)
from src.source_monitoring.acquisition_planner import (
    ACQUISITION_RESOLUTION_PLANS_FILE,
    load_phase6a_corpus,
)
from src.source_monitoring.feed_verifier import FEED_VERIFICATION_RESULTS_FILE
from src.source_monitoring.source_discovery_identity import (
    normalize_source_url,
    root_domain_from_url,
)
from src.source_monitoring.source_discovery_models import SourceRole
from src.source_monitoring.source_evaluation_models import FetchStatus, SourceInspection
from src.source_monitoring.source_fetcher import HTML_CONTENT_TYPES, SourceFetcher, SourceFetchPolicy
from src.source_monitoring.source_inspector import (
    SourceInspectionOutcome,
    SourceInspector,
    persist_inspection_checkpoint,
)
from src.source_monitoring.source_observer import (
    ObservationItemCandidate,
    extract_observation_item_candidates,
    select_observation_items,
)


PHASE6C_SELECTED_WEBSITE_RESULT_SET_SCHEMA_VERSION = "phase6c_selected_website_resolution_result_set_v1"
SELECTED_WEBSITE_ITEM_DISCOVERY_POLICY_VERSION = "selected_website_item_discovery_policy_v1"
SELECTED_WEBSITE_FEASIBILITY_POLICY_VERSION = "selected_website_feasibility_policy_v1"
SELECTED_WEBSITE_CONFIG_POLICY_VERSION = "selected_website_acquisition_config_policy_v1"
SELECTED_WEBSITE_REFRESH_FETCH_POLICY_VERSION = "phase6c_selected_website_refresh_fetch_policy_v1"
SELECTED_WEBSITE_LINK_NORMALIZATION_POLICY = "normalize_source_url_v1_fragmentless"
SELECTED_WEBSITE_DEDUP_IDENTITY_POLICY_VERSION = "selected_website_normalized_url_identity_v1"
SELECTED_WEBSITE_TITLE_EXTRACTION_STRATEGY_REF = "phase5c_representative_link_text_v1"
SELECTED_WEBSITE_DATE_EXTRACTION_STRATEGY_REF = "phase5e_url_path_date_hint_v1"
DEFAULT_MAX_RETAINED_ITEM_CANDIDATES = 20
DEFAULT_MAX_ITEMS_PER_RUN = 20

SELECTED_WEBSITE_RESOLUTION_RESULTS_FILE = (
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "selected_website_resolution_results.json"
)
SELECTED_WEBSITE_RESOLUTION_DIAGNOSTIC_ROOT = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase6_acquisition"
    / "selected_website_resolution"
)
SELECTED_WEBSITE_RAW_ARTIFACT_ROOT = SELECTED_WEBSITE_RESOLUTION_DIAGNOSTIC_ROOT / "source_refreshes"
SELECTED_WEBSITE_FAILURE_ROOT = SELECTED_WEBSITE_RESOLUTION_DIAGNOSTIC_ROOT / "fetch_failures"
SELECTED_WEBSITE_INSPECTION_ROOT = SELECTED_WEBSITE_RESOLUTION_DIAGNOSTIC_ROOT / "inspections"

FEASIBLE_MIN_SELECTED_LINKS = 1
HTML_ACCEPTED_CONTENT_TYPES = HTML_CONTENT_TYPES


@dataclass(frozen=True)
class SelectedWebsiteDiscoveryEvidence:
    inspection_id: str | None
    inspection_hash: str | None
    source_url: str
    final_url: str | None
    observed_source_role: SourceRole
    fetch_status: FetchStatus | None
    inspectable: bool
    skipped_reason: str | None
    total_candidate_link_count: int
    selected_candidate_link_count: int
    unique_normalized_item_url_count: int
    duplicate_normalized_item_url_count: int
    in_scope_candidate_link_count: int
    out_of_scope_candidate_link_count: int
    candidate_links_with_title_count: int
    candidate_links_with_date_hint_count: int
    role_compatible_candidate_link_count: int
    normalized_item_url_support: bool
    item_title_support: bool
    date_hint_support: bool
    item_type_role_support: bool
    stable_item_identity_support: bool
    client_rendering_required_hint: bool
    has_pagination_hints: bool
    has_detail_hints: bool
    allowed_domain_scope: tuple[str, ...]
    selected_candidates: tuple[ObservationItemCandidate, ...]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "inspection_id": self.inspection_id,
            "inspection_hash": self.inspection_hash,
            "source_url": self.source_url,
            "final_url": self.final_url,
            "observed_source_role": self.observed_source_role.value,
            "fetch_status": self.fetch_status.value if self.fetch_status else None,
            "inspectable": self.inspectable,
            "skipped_reason": self.skipped_reason,
            "total_candidate_link_count": self.total_candidate_link_count,
            "selected_candidate_link_count": self.selected_candidate_link_count,
            "unique_normalized_item_url_count": self.unique_normalized_item_url_count,
            "duplicate_normalized_item_url_count": self.duplicate_normalized_item_url_count,
            "in_scope_candidate_link_count": self.in_scope_candidate_link_count,
            "out_of_scope_candidate_link_count": self.out_of_scope_candidate_link_count,
            "candidate_links_with_title_count": self.candidate_links_with_title_count,
            "candidate_links_with_date_hint_count": self.candidate_links_with_date_hint_count,
            "role_compatible_candidate_link_count": self.role_compatible_candidate_link_count,
            "normalized_item_url_support": self.normalized_item_url_support,
            "item_title_support": self.item_title_support,
            "date_hint_support": self.date_hint_support,
            "item_type_role_support": self.item_type_role_support,
            "stable_item_identity_support": self.stable_item_identity_support,
            "client_rendering_required_hint": self.client_rendering_required_hint,
            "has_pagination_hints": self.has_pagination_hints,
            "has_detail_hints": self.has_detail_hints,
            "allowed_domain_scope": list(self.allowed_domain_scope),
            "selected_candidates": [item.to_dict() for item in self.selected_candidates],
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class SelectedWebsiteResolutionExecution:
    result: SelectedWebsiteResolutionResult
    plan: SelectedWebsiteResolutionPlan
    acquisition_plan: AcquisitionResolutionPlan
    fetch_cache_hit: bool
    current_evidence: SelectedWebsiteDiscoveryEvidence
    historical_evidence: SelectedWebsiteDiscoveryEvidence | None
    current_inspection_checkpoint: str | None
    raw_artifact_ref: dict[str, Any] | None
    routing_source: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.to_dict(),
            "plan": self.plan.to_dict(),
            "acquisition_plan_ref": {
                "acquisition_resolution_plan_id": self.acquisition_plan.acquisition_resolution_plan_id,
                "candidate_source_id": self.acquisition_plan.candidate_source_id,
                "entity_id": self.acquisition_plan.entity_id,
                "source_url": self.acquisition_plan.source_url,
                "observed_source_role": self.acquisition_plan.observed_source_role.value,
            },
            "fetch_cache_hit": self.fetch_cache_hit,
            "current_evidence": self.current_evidence.to_dict(),
            "historical_evidence": self.historical_evidence.to_dict() if self.historical_evidence else None,
            "current_inspection_checkpoint": self.current_inspection_checkpoint,
            "raw_artifact_ref": self.raw_artifact_ref,
            "routing_source": dict(self.routing_source),
        }


@dataclass(frozen=True)
class SelectedWebsiteResolutionResultSet:
    selected_website_resolution_results: tuple[SelectedWebsiteResolutionExecution, ...]
    phase6a_input_hash: str
    phase6b_input_hash: str
    input_fingerprint: str
    result_distribution: dict[str, int]
    per_source_summary: tuple[dict[str, Any], ...]
    phase6d_routing: tuple[dict[str, Any], ...]
    diagnostics: tuple[str, ...]
    generation: dict[str, Any]
    output_hash: str
    item_discovery_policy_version: str = SELECTED_WEBSITE_ITEM_DISCOVERY_POLICY_VERSION
    feasibility_policy_version: str = SELECTED_WEBSITE_FEASIBILITY_POLICY_VERSION
    config_policy_version: str = SELECTED_WEBSITE_CONFIG_POLICY_VERSION
    schema_version: str = PHASE6C_SELECTED_WEBSITE_RESULT_SET_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "item_discovery_policy_version": self.item_discovery_policy_version,
            "feasibility_policy_version": self.feasibility_policy_version,
            "config_policy_version": self.config_policy_version,
            "phase6a_input_hash": self.phase6a_input_hash,
            "phase6b_input_hash": self.phase6b_input_hash,
            "input_fingerprint": self.input_fingerprint,
            "selected_website_resolution_results": [
                item.to_dict() for item in self.selected_website_resolution_results
            ],
            "result_distribution": dict(self.result_distribution),
            "per_source_summary": [dict(item) for item in self.per_source_summary],
            "phase6d_routing": [dict(item) for item in self.phase6d_routing],
            "diagnostics": list(self.diagnostics),
            "generation": dict(self.generation),
            "output_hash": self.output_hash,
        }


class SelectedWebsiteResolutionError(ValueError):
    pass


class SelectedWebsiteResolver:
    def __init__(
        self,
        *,
        fetcher: SourceFetcher | None = None,
        inspector: SourceInspector | None = None,
        max_retained_item_candidates: int = DEFAULT_MAX_RETAINED_ITEM_CANDIDATES,
        max_items_per_run: int = DEFAULT_MAX_ITEMS_PER_RUN,
        item_discovery_policy_version: str = SELECTED_WEBSITE_ITEM_DISCOVERY_POLICY_VERSION,
        feasibility_policy_version: str = SELECTED_WEBSITE_FEASIBILITY_POLICY_VERSION,
        config_policy_version: str = SELECTED_WEBSITE_CONFIG_POLICY_VERSION,
    ) -> None:
        if max_retained_item_candidates <= 0:
            raise SelectedWebsiteResolutionError("max_retained_item_candidates must be positive.")
        if max_items_per_run <= 0:
            raise SelectedWebsiteResolutionError("max_items_per_run must be positive.")
        self.fetcher = fetcher or build_phase6c_source_fetcher()
        self.inspector = inspector or SourceInspector()
        self.max_retained_item_candidates = max_retained_item_candidates
        self.max_items_per_run = max_items_per_run
        self.item_discovery_policy_version = item_discovery_policy_version
        self.feasibility_policy_version = feasibility_policy_version
        self.config_policy_version = config_policy_version

    def resolve(
        self,
        *,
        plan: SelectedWebsiteResolutionPlan,
        acquisition_plan: AcquisitionResolutionPlan,
        historical_inspection: SourceInspection | None,
        routing_source: dict[str, Any],
    ) -> SelectedWebsiteResolutionExecution:
        if plan.acquisition_resolution_plan_id != acquisition_plan.acquisition_resolution_plan_id:
            raise SelectedWebsiteResolutionError("SelectedWebsiteResolutionPlan parent acquisition plan mismatch.")
        request = self.fetcher.build_request(plan.source_url)
        fetch_outcome = self.fetcher.fetch(
            request=request,
            source_evaluation_plan_id=plan.selected_website_resolution_plan_id,
            candidate_source_id=plan.candidate_source_id,
        )
        inspection_outcome = self.inspector.inspect_fetch_execution(fetch_outcome.execution)
        checkpoint = persist_inspection_checkpoint(
            outcome=inspection_outcome,
            output_root=SELECTED_WEBSITE_INSPECTION_ROOT,
        )
        current = build_discovery_evidence(
            inspection=inspection_outcome.inspection,
            source_url=plan.source_url,
            observed_source_role=plan.observed_source_role,
            fetch_status=fetch_outcome.execution.fetch_status,
            inspectable=inspection_outcome.inspectable,
            skipped_reason=inspection_outcome.skipped_reason,
            max_retained_item_candidates=self.max_retained_item_candidates,
        )
        historical = (
            build_discovery_evidence(
                inspection=historical_inspection,
                source_url=plan.source_url,
                observed_source_role=plan.observed_source_role,
                fetch_status=None,
                inspectable=True,
                skipped_reason=None,
                max_retained_item_candidates=self.max_retained_item_candidates,
            )
            if historical_inspection is not None
            else None
        )
        result = assess_selected_website_resolution(
            plan=plan,
            acquisition_plan=acquisition_plan,
            current_evidence=current,
            historical_evidence=historical,
            fetch_execution_id=fetch_outcome.execution.source_fetch_execution_id,
            current_final_url=fetch_outcome.execution.final_url,
            current_raw_body_sha256=fetch_outcome.execution.raw_body_sha256,
            max_items_per_run=self.max_items_per_run,
            item_discovery_policy_version=self.item_discovery_policy_version,
            feasibility_policy_version=self.feasibility_policy_version,
            config_policy_version=self.config_policy_version,
            routing_source=routing_source,
        )
        return SelectedWebsiteResolutionExecution(
            result=result,
            plan=plan,
            acquisition_plan=acquisition_plan,
            fetch_cache_hit=fetch_outcome.cache_hit,
            current_evidence=current,
            historical_evidence=historical,
            current_inspection_checkpoint=relative_to_project(checkpoint) if checkpoint else None,
            raw_artifact_ref=(
                fetch_outcome.execution.raw_artifact_ref.to_dict()
                if fetch_outcome.execution.raw_artifact_ref
                else None
            ),
            routing_source=dict(routing_source),
        )


def build_phase6c_source_fetcher(
    *,
    session: Any | None = None,
    cache_enabled: bool = True,
    max_response_bytes: int = 1_000_000,
) -> SourceFetcher:
    return SourceFetcher(
        policy=SourceFetchPolicy(
            timeout_seconds=20,
            max_response_bytes=max_response_bytes,
            max_redirects=5,
            accepted_content_types=HTML_ACCEPTED_CONTENT_TYPES,
            fetch_policy_version=SELECTED_WEBSITE_REFRESH_FETCH_POLICY_VERSION,
            artifact_root=SELECTED_WEBSITE_RAW_ARTIFACT_ROOT,
            failure_root=SELECTED_WEBSITE_FAILURE_ROOT,
            cache_enabled=cache_enabled,
            batch_size=2,
        ),
        session=session,
    )


def execute_selected_website_resolution_plans(
    *,
    planning_result: AcquisitionPlanningResult,
    feed_verification_result_payload: dict[str, Any],
    historical_inspections: tuple[SourceInspection, ...] = (),
    fetcher: SourceFetcher | None = None,
    inspector: SourceInspector | None = None,
    max_retained_item_candidates: int = DEFAULT_MAX_RETAINED_ITEM_CANDIDATES,
    max_items_per_run: int = DEFAULT_MAX_ITEMS_PER_RUN,
    generation_mode: str = "phase6c_selected_website_resolution",
) -> SelectedWebsiteResolutionResultSet:
    acquisition_by_id = {
        item.acquisition_resolution_plan_id: item
        for item in planning_result.acquisition_resolution_plans
    }
    historical_by_candidate = {
        item.candidate_source_id: item
        for item in historical_inspections
    }
    routing_by_candidate = {
        str(item["candidate_source_id"]): dict(item)
        for item in feed_verification_result_payload.get("phase6c_routing", [])
    }
    selected_plans = select_phase6c_execution_plans(
        planning_result=planning_result,
        feed_verification_result_payload=feed_verification_result_payload,
    )
    resolver = SelectedWebsiteResolver(
        fetcher=fetcher,
        inspector=inspector,
        max_retained_item_candidates=max_retained_item_candidates,
        max_items_per_run=max_items_per_run,
    )
    executions: list[SelectedWebsiteResolutionExecution] = []
    diagnostics: list[str] = []
    for plan in selected_plans:
        acquisition = acquisition_by_id.get(plan.acquisition_resolution_plan_id)
        if acquisition is None:
            diagnostics.append(f"missing_parent_acquisition_plan:{plan.selected_website_resolution_plan_id}")
            continue
        executions.append(
            resolver.resolve(
                plan=plan,
                acquisition_plan=acquisition,
                historical_inspection=historical_by_candidate.get(plan.candidate_source_id),
                routing_source=routing_by_candidate.get(plan.candidate_source_id, {}),
            )
        )
    if len(executions) != len(selected_plans):
        diagnostics.append("not_every_selected_website_plan_produced_result")

    result_distribution = _result_distribution(tuple(executions))
    per_source = _per_source_summary(
        planning_result=planning_result,
        feed_verification_result_payload=feed_verification_result_payload,
        executions=tuple(executions),
    )
    routing = _phase6d_routing(
        feed_verification_result_payload=feed_verification_result_payload,
        executions=tuple(executions),
    )
    phase6b_hash = str(feed_verification_result_payload.get("output_hash", ""))
    input_fingerprint = hash_canonical_value(
        {
            "phase6a_output_hash": planning_result.output_hash,
            "phase6b_output_hash": phase6b_hash,
            "selected_website_plan_ids": tuple(
                item.selected_website_resolution_plan_id for item in selected_plans
            ),
            "item_discovery_policy_version": SELECTED_WEBSITE_ITEM_DISCOVERY_POLICY_VERSION,
            "feasibility_policy_version": SELECTED_WEBSITE_FEASIBILITY_POLICY_VERSION,
            "max_retained_item_candidates": max_retained_item_candidates,
            "max_items_per_run": max_items_per_run,
        }
    )
    semantic_payload = {
        "phase6a_input_hash": planning_result.output_hash,
        "phase6b_input_hash": phase6b_hash,
        "input_fingerprint": input_fingerprint,
        "selected_website_resolution_results": [
            _semantic_execution_payload(item) for item in executions
        ],
        "result_distribution": result_distribution,
        "per_source_summary": [dict(item) for item in per_source],
        "phase6d_routing": [dict(item) for item in routing],
        "diagnostics": tuple(sorted(diagnostics)),
    }
    generation = {
        "generation_mode": generation_mode,
        "http_calls_possible_max": len(selected_plans),
        "new_fetch_count": sum(1 for item in executions if not item.fetch_cache_hit),
        "cache_hit_count": sum(1 for item in executions if item.fetch_cache_hit),
        "executed_selected_website_plan_count": len(executions),
    }
    return SelectedWebsiteResolutionResultSet(
        selected_website_resolution_results=tuple(executions),
        phase6a_input_hash=planning_result.output_hash,
        phase6b_input_hash=phase6b_hash,
        input_fingerprint=input_fingerprint,
        result_distribution=result_distribution,
        per_source_summary=per_source,
        phase6d_routing=routing,
        diagnostics=tuple(sorted(diagnostics)),
        generation=generation,
        output_hash=build_selected_website_resolution_output_hash(**semantic_payload),
    )


def select_phase6c_execution_plans(
    *,
    planning_result: AcquisitionPlanningResult,
    feed_verification_result_payload: dict[str, Any],
) -> tuple[SelectedWebsiteResolutionPlan, ...]:
    routing_by_plan = {
        str(item["selected_website_resolution_plan_id"]): dict(item)
        for item in feed_verification_result_payload.get("phase6c_routing", [])
    }
    selected = []
    for plan in sorted(
        planning_result.selected_website_resolution_plans,
        key=lambda item: item.candidate_source_id,
    ):
        route = routing_by_plan.get(plan.selected_website_resolution_plan_id)
        if route is None:
            continue
        if route.get("routing") == "NO_USABLE_VERIFIED_FEED":
            selected.append(plan)
    return tuple(selected)


def build_discovery_evidence(
    *,
    inspection: SourceInspection | None,
    source_url: str,
    observed_source_role: SourceRole,
    fetch_status: FetchStatus | None,
    inspectable: bool,
    skipped_reason: str | None,
    max_retained_item_candidates: int = DEFAULT_MAX_RETAINED_ITEM_CANDIDATES,
) -> SelectedWebsiteDiscoveryEvidence:
    if inspection is None:
        reason_codes = tuple(
            sorted(
                {
                    "no_inspection_available",
                    *(("source_surface_not_inspectable",) if not inspectable else ()),
                    *(("source_surface_fetch_failed",) if _fetch_failed(fetch_status) else ()),
                }
            )
        )
        return SelectedWebsiteDiscoveryEvidence(
            inspection_id=None,
            inspection_hash=None,
            source_url=source_url,
            final_url=None,
            observed_source_role=observed_source_role,
            fetch_status=fetch_status,
            inspectable=inspectable,
            skipped_reason=skipped_reason,
            total_candidate_link_count=0,
            selected_candidate_link_count=0,
            unique_normalized_item_url_count=0,
            duplicate_normalized_item_url_count=0,
            in_scope_candidate_link_count=0,
            out_of_scope_candidate_link_count=0,
            candidate_links_with_title_count=0,
            candidate_links_with_date_hint_count=0,
            role_compatible_candidate_link_count=0,
            normalized_item_url_support=False,
            item_title_support=False,
            date_hint_support=False,
            item_type_role_support=False,
            stable_item_identity_support=False,
            client_rendering_required_hint=False,
            has_pagination_hints=False,
            has_detail_hints=False,
            allowed_domain_scope=tuple(sorted({_root_domain_or_host(source_url)} - {""})),
            selected_candidates=(),
            reason_codes=reason_codes,
        )
    all_candidates = extract_observation_item_candidates(
        source_inspection=inspection,
        observed_source_role=observed_source_role,
    )
    selected = select_observation_items(
        candidates=all_candidates,
        source_url=source_url,
        observed_source_role=observed_source_role,
        max_item_count=max_retained_item_candidates,
    )
    allowed_scope = _allowed_domain_scope(
        source_url=source_url,
        final_url=inspection.final_url,
        selected_candidates=selected,
    )
    in_scope = sum(
        1 for item in selected
        if _candidate_in_scope(item.normalized_item_url, allowed_scope)
    )
    unique_urls = {item.normalized_item_url for item in selected}
    role_categories = _role_categories(observed_source_role)
    role_compatible = sum(
        1 for item in selected
        if not role_categories or role_categories & set(item.hint_categories)
    )
    reason_codes = set()
    if selected:
        reason_codes.add("candidate_item_links_observed")
    else:
        reason_codes.add("no_candidate_item_links_observed")
    if all_candidates and not selected:
        reason_codes.add("candidate_links_filtered_as_navigation_or_pagination")
    if inspection.client_rendering_required_hint:
        reason_codes.add("client_rendering_required_hint")
    if inspection.has_pagination_hints:
        reason_codes.add("pagination_hints_present_not_followed")
    if role_compatible:
        reason_codes.add("role_compatible_item_links_observed")
    if in_scope < len(selected):
        reason_codes.add("out_of_scope_item_links_observed")
    return SelectedWebsiteDiscoveryEvidence(
        inspection_id=inspection.inspection_id,
        inspection_hash=inspection.inspection_output_hash,
        source_url=source_url,
        final_url=inspection.final_url,
        observed_source_role=observed_source_role,
        fetch_status=fetch_status,
        inspectable=inspectable,
        skipped_reason=skipped_reason,
        total_candidate_link_count=len(all_candidates),
        selected_candidate_link_count=len(selected),
        unique_normalized_item_url_count=len(unique_urls),
        duplicate_normalized_item_url_count=max(0, len(selected) - len(unique_urls)),
        in_scope_candidate_link_count=in_scope,
        out_of_scope_candidate_link_count=max(0, len(selected) - in_scope),
        candidate_links_with_title_count=sum(1 for item in selected if item.item_title.strip()),
        candidate_links_with_date_hint_count=sum(1 for item in selected if item.date_hint),
        role_compatible_candidate_link_count=role_compatible,
        normalized_item_url_support=bool(unique_urls),
        item_title_support=any(item.item_title.strip() for item in selected),
        date_hint_support=any(item.date_hint for item in selected),
        item_type_role_support=bool(role_compatible),
        stable_item_identity_support=bool(unique_urls),
        client_rendering_required_hint=inspection.client_rendering_required_hint,
        has_pagination_hints=inspection.has_pagination_hints,
        has_detail_hints=inspection.has_detail_page_hints,
        allowed_domain_scope=allowed_scope,
        selected_candidates=selected,
        reason_codes=tuple(sorted(reason_codes)),
    )


def assess_selected_website_resolution(
    *,
    plan: SelectedWebsiteResolutionPlan,
    acquisition_plan: AcquisitionResolutionPlan,
    current_evidence: SelectedWebsiteDiscoveryEvidence,
    historical_evidence: SelectedWebsiteDiscoveryEvidence | None,
    fetch_execution_id: str,
    current_final_url: str,
    current_raw_body_sha256: str | None,
    max_items_per_run: int,
    item_discovery_policy_version: str,
    feasibility_policy_version: str,
    config_policy_version: str,
    routing_source: dict[str, Any],
) -> SelectedWebsiteResolutionResult:
    status, discoverability, consistency, limitations, reason_codes = _feasibility_decision(
        current=current_evidence,
        historical=historical_evidence,
    )
    normalized_support = current_evidence.normalized_item_url_support
    title_support = current_evidence.item_title_support
    date_support = current_evidence.date_hint_support
    role_support = current_evidence.item_type_role_support
    scope = current_evidence.allowed_domain_scope
    if not normalized_support and historical_evidence:
        normalized_support = historical_evidence.normalized_item_url_support
        title_support = historical_evidence.item_title_support
        date_support = historical_evidence.date_hint_support
        role_support = historical_evidence.item_type_role_support
        scope = historical_evidence.allowed_domain_scope

    base_fingerprint_payload = {
        "selected_website_resolution_plan_id": plan.selected_website_resolution_plan_id,
        "selected_website_resolution_plan_fingerprint": plan.input_fingerprint,
        "acquisition_resolution_plan_id": acquisition_plan.acquisition_resolution_plan_id,
        "candidate_source_id": plan.candidate_source_id,
        "final_source_evaluation_id": plan.final_source_evaluation_id,
        "source_url": plan.source_url,
        "current_final_url": current_final_url,
        "fetch_execution_id": fetch_execution_id,
        "current_raw_body_sha256": current_raw_body_sha256,
        "current_evidence": _semantic_evidence_payload(current_evidence),
        "historical_evidence": (
            _semantic_evidence_payload(historical_evidence)
            if historical_evidence
            else None
        ),
        "routing_source": routing_source,
        "feasibility_status": status.value,
        "candidate_item_link_discoverability": discoverability,
        "bounded_extraction_consistency": consistency,
        "technical_limitations": limitations,
        "reason_codes": reason_codes,
        "item_discovery_policy_version": item_discovery_policy_version,
        "feasibility_policy_version": feasibility_policy_version,
        "config_policy_version": config_policy_version,
        "max_items_per_run": max_items_per_run,
    }
    input_fingerprint = build_selected_website_resolution_result_fingerprint(
        **base_fingerprint_payload
    )
    config = None
    if status == SelectedWebsiteResolutionStatus.FEASIBLE:
        config_fingerprint = build_selected_website_acquisition_config_fingerprint(
            selected_website_resolution_plan_id=plan.selected_website_resolution_plan_id,
            candidate_source_id=plan.candidate_source_id,
            source_url=plan.source_url,
            allowed_domain_scope=scope,
            item_discovery_strategy_version=item_discovery_policy_version,
            item_link_normalization_policy=SELECTED_WEBSITE_LINK_NORMALIZATION_POLICY,
            max_discovered_items_per_run=max_items_per_run,
            title_extraction_strategy_ref=SELECTED_WEBSITE_TITLE_EXTRACTION_STRATEGY_REF if title_support else None,
            date_extraction_strategy_ref=SELECTED_WEBSITE_DATE_EXTRACTION_STRATEGY_REF if date_support else None,
            dedup_identity_strategy_version=SELECTED_WEBSITE_DEDUP_IDENTITY_POLICY_VERSION,
            source_role=plan.observed_source_role.value,
            current_evidence=_semantic_evidence_payload(current_evidence),
            historical_evidence=(
                _semantic_evidence_payload(historical_evidence)
                if historical_evidence
                else None
            ),
        )
        config_id = build_selected_website_acquisition_config_id(
            selected_website_resolution_plan_id=plan.selected_website_resolution_plan_id,
            candidate_source_id=plan.candidate_source_id,
            source_url=plan.source_url,
            input_fingerprint=config_fingerprint,
        )
        config = SelectedWebsiteAcquisitionConfig(
            selected_website_acquisition_config_id=config_id,
            source_url=plan.source_url,
            acquisition_method=AcquisitionMethod.SELECTED_WEBSITE,
            item_discovery_strategy_version=item_discovery_policy_version,
            allowed_domain_scope=scope,
            item_link_normalization_policy=SELECTED_WEBSITE_LINK_NORMALIZATION_POLICY,
            max_discovered_items_per_run=max_items_per_run,
            title_extraction_strategy_ref=(
                SELECTED_WEBSITE_TITLE_EXTRACTION_STRATEGY_REF if title_support else None
            ),
            date_extraction_strategy_ref=(
                SELECTED_WEBSITE_DATE_EXTRACTION_STRATEGY_REF if date_support else None
            ),
            dedup_identity_strategy_version=SELECTED_WEBSITE_DEDUP_IDENTITY_POLICY_VERSION,
            source_role=plan.observed_source_role,
            provenance={
                "phase": "phase6c_selected_website_resolution",
                "selected_website_resolution_plan_id": plan.selected_website_resolution_plan_id,
                "acquisition_resolution_plan_id": acquisition_plan.acquisition_resolution_plan_id,
                "current_inspection_id": current_evidence.inspection_id,
                "current_inspection_hash": current_evidence.inspection_hash,
                "historical_inspection_id": historical_evidence.inspection_id if historical_evidence else None,
                "historical_inspection_hash": historical_evidence.inspection_hash if historical_evidence else None,
                "fetch_execution_id": fetch_execution_id,
                "phase6b_routing": dict(routing_source),
                "selected_candidate_link_count": current_evidence.selected_candidate_link_count,
                "technical_limitations": list(limitations),
            },
            input_fingerprint=config_fingerprint,
        )
    result_id = build_selected_website_resolution_result_id(
        selected_website_resolution_plan_id=plan.selected_website_resolution_plan_id,
        candidate_source_id=plan.candidate_source_id,
        source_url=plan.source_url,
        input_fingerprint=input_fingerprint,
    )
    return SelectedWebsiteResolutionResult(
        selected_website_resolution_result_id=result_id,
        selected_website_resolution_plan_id=plan.selected_website_resolution_plan_id,
        candidate_source_id=plan.candidate_source_id,
        final_source_evaluation_id=plan.final_source_evaluation_id,
        source_url=plan.source_url,
        feasibility_status=status,
        candidate_item_link_discoverability=discoverability,
        normalized_item_url_support=normalized_support,
        item_title_support=title_support,
        date_hint_support=date_support,
        item_type_role_support=role_support,
        bounded_extraction_consistency=consistency,
        technical_limitations=limitations,
        selected_website_acquisition_config=config,
        reason_codes=reason_codes,
        resolution_policy_version=feasibility_policy_version,
        input_fingerprint=input_fingerprint,
    )


def persist_selected_website_resolution_results(
    *,
    result_set: SelectedWebsiteResolutionResultSet,
    output_file: Path = SELECTED_WEBSITE_RESOLUTION_RESULTS_FILE,
) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result_set.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    if output_file.exists() and output_file.read_text(encoding="utf-8") == text:
        return output_file
    output_file.write_text(text, encoding="utf-8")
    return output_file


def load_phase6c_inputs(
    *,
    planning_path: Path = ACQUISITION_RESOLUTION_PLANS_FILE,
    feed_verification_path: Path = FEED_VERIFICATION_RESULTS_FILE,
) -> tuple[AcquisitionPlanningResult, dict[str, Any], tuple[SourceInspection, ...]]:
    planning = AcquisitionPlanningResult.from_dict(json.loads(planning_path.read_text(encoding="utf-8")))
    feed_payload = json.loads(feed_verification_path.read_text(encoding="utf-8"))
    corpus = load_phase6a_corpus()
    inspections = tuple(corpus.get("inspections", ()))
    return planning, feed_payload, inspections


def artifact_signature(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    payload = path.read_bytes()
    stat = path.stat()
    return {
        "exists": True,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "mtime_ns": stat.st_mtime_ns,
    }


def relative_to_project(path: Path | None) -> str | None:
    if path is None:
        return None
    return path.relative_to(PROJECT_ROOT).as_posix()


def _feasibility_decision(
    *,
    current: SelectedWebsiteDiscoveryEvidence,
    historical: SelectedWebsiteDiscoveryEvidence | None,
) -> tuple[SelectedWebsiteResolutionStatus, str, str, tuple[str, ...], tuple[str, ...]]:
    reasons = set(current.reason_codes)
    limitations: set[str] = set()
    if current.fetch_status in {
        FetchStatus.TIMEOUT,
        FetchStatus.NETWORK_FAILURE,
        FetchStatus.HTTP_FAILURE,
        FetchStatus.REDIRECT_FAILURE,
    }:
        limitations.add(f"current_fetch_{current.fetch_status.value}")
    if current.fetch_status == FetchStatus.UNSUPPORTED_CONTENT:
        limitations.add("current_source_surface_non_html")
    if current.client_rendering_required_hint:
        limitations.add("client_rendering_required_hint")
    if current.has_pagination_hints:
        limitations.add("pagination_hints_present_not_followed")
    if not current.date_hint_support:
        limitations.add("date_hint_support_limited")
    if current.out_of_scope_candidate_link_count:
        limitations.add("out_of_scope_candidate_links_ignored")

    if current.in_scope_candidate_link_count >= FEASIBLE_MIN_SELECTED_LINKS and current.normalized_item_url_support:
        reasons.add("current_source_surface_supports_selected_website_acquisition")
        consistency = (
            "current_and_historical_compatible"
            if historical and _candidate_url_overlap(current, historical)
            else "current_only_evidence"
        )
        return (
            SelectedWebsiteResolutionStatus.FEASIBLE,
            "discoverable",
            consistency,
            tuple(sorted(limitations)),
            tuple(sorted(reasons)),
        )

    if historical and historical.selected_candidate_link_count >= FEASIBLE_MIN_SELECTED_LINKS:
        reasons.add("historical_item_link_evidence_available")
        limitations.add("current_source_surface_lacks_item_link_evidence")
        status = SelectedWebsiteResolutionStatus.NEEDS_REVIEW
        if current.client_rendering_required_hint:
            reasons.add("current_surface_may_require_browser_rendering")
        return (
            status,
            "limited",
            "historical_only_current_uncertain",
            tuple(sorted(limitations)),
            tuple(sorted(reasons)),
        )

    if current.client_rendering_required_hint:
        reasons.add("selected_website_requires_unsupported_rendering")
        return (
            SelectedWebsiteResolutionStatus.UNSUPPORTED,
            "not_observed",
            "insufficient_evidence",
            tuple(sorted(limitations | {"client_rendering_required_hint"})),
            tuple(sorted(reasons)),
        )

    if current.fetch_status in {
        FetchStatus.TIMEOUT,
        FetchStatus.NETWORK_FAILURE,
        FetchStatus.HTTP_FAILURE,
        FetchStatus.REDIRECT_FAILURE,
        FetchStatus.UNSUPPORTED_CONTENT,
    }:
        reasons.add("current_refresh_failed_no_prior_item_evidence")
        return (
            SelectedWebsiteResolutionStatus.NEEDS_REVIEW,
            "fetch_failed",
            "insufficient_evidence",
            tuple(sorted(limitations)),
            tuple(sorted(reasons)),
        )

    reasons.add("no_selected_website_item_discovery_support")
    return (
        SelectedWebsiteResolutionStatus.UNSUPPORTED,
        "not_observed",
        "insufficient_evidence",
        tuple(sorted(limitations)),
        tuple(sorted(reasons)),
    )


def _candidate_url_overlap(
    left: SelectedWebsiteDiscoveryEvidence,
    right: SelectedWebsiteDiscoveryEvidence,
) -> bool:
    left_urls = {item.normalized_item_url for item in left.selected_candidates}
    right_urls = {item.normalized_item_url for item in right.selected_candidates}
    return bool(left_urls & right_urls)


def _allowed_domain_scope(
    *,
    source_url: str,
    final_url: str | None,
    selected_candidates: tuple[ObservationItemCandidate, ...],
) -> tuple[str, ...]:
    domains = {_root_domain_or_host(source_url)}
    if final_url:
        domains.add(_root_domain_or_host(final_url))
    return tuple(sorted(domain for domain in domains if domain))


def _candidate_in_scope(url: str, scope: tuple[str, ...]) -> bool:
    domain = _root_domain_or_host(url)
    return bool(domain) and domain in set(scope)


def _root_domain_or_host(url: str | None) -> str:
    root = root_domain_from_url(url)
    if root:
        return root
    parsed = urlparse(url or "")
    return (parsed.hostname or "").casefold().removeprefix("www.")


def _role_categories(role: SourceRole | None) -> set[str]:
    if role == SourceRole.CAREERS:
        return {"job", "detail"}
    if role in {SourceRole.RESEARCH_PUBLICATIONS, SourceRole.REPORTS_OR_DATA, SourceRole.INSIGHTS}:
        return {"report", "article", "detail"}
    if role in {SourceRole.NEWSROOM, SourceRole.PRESS_RELEASES, SourceRole.BLOG}:
        return {"article", "event", "detail"}
    if role == SourceRole.EVENTS_OR_PROGRAMS:
        return {"event", "article", "detail"}
    if role == SourceRole.PORTFOLIO:
        return {"portfolio", "detail"}
    return set()


def _fetch_failed(status: FetchStatus | None) -> bool:
    return status in {
        FetchStatus.TIMEOUT,
        FetchStatus.NETWORK_FAILURE,
        FetchStatus.HTTP_FAILURE,
        FetchStatus.REDIRECT_FAILURE,
        FetchStatus.UNSUPPORTED_CONTENT,
        FetchStatus.RESPONSE_TOO_LARGE,
    }


def _result_distribution(executions: tuple[SelectedWebsiteResolutionExecution, ...]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for execution in executions:
        key = execution.result.feasibility_status.value
        distribution[key] = distribution.get(key, 0) + 1
    return dict(sorted(distribution.items()))


def _per_source_summary(
    *,
    planning_result: AcquisitionPlanningResult,
    feed_verification_result_payload: dict[str, Any],
    executions: tuple[SelectedWebsiteResolutionExecution, ...],
) -> tuple[dict[str, Any], ...]:
    execution_by_candidate = {
        item.result.candidate_source_id: item
        for item in executions
    }
    phase6b_routing = {
        str(item["candidate_source_id"]): dict(item)
        for item in feed_verification_result_payload.get("phase6c_routing", [])
    }
    rows = []
    for plan in sorted(
        planning_result.selected_website_resolution_plans,
        key=lambda item: item.candidate_source_id,
    ):
        execution = execution_by_candidate.get(plan.candidate_source_id)
        phase6b = phase6b_routing.get(plan.candidate_source_id, {})
        rows.append(
            {
                "candidate_source_id": plan.candidate_source_id,
                "selected_website_resolution_plan_id": plan.selected_website_resolution_plan_id,
                "source_url": plan.source_url,
                "phase6b_routing": phase6b.get("routing"),
                "phase6b_reason": phase6b.get("reason"),
                "phase6c_executed": execution is not None,
                "phase6c_exclusion_reason": (
                    None
                    if execution is not None
                    else "usable_verified_feed_exists"
                    if phase6b.get("routing") == "HAS_USABLE_VERIFIED_FEED"
                    else "not_routed_to_selected_website"
                ),
                "feasibility_status": (
                    execution.result.feasibility_status.value if execution else None
                ),
                "candidate_item_link_discoverability": (
                    execution.result.candidate_item_link_discoverability if execution else None
                ),
                "selected_candidate_link_count": (
                    execution.current_evidence.selected_candidate_link_count if execution else 0
                ),
                "selected_website_acquisition_config_id": (
                    execution.result.selected_website_acquisition_config.selected_website_acquisition_config_id
                    if execution and execution.result.selected_website_acquisition_config
                    else None
                ),
            }
        )
    return tuple(rows)


def _phase6d_routing(
    *,
    feed_verification_result_payload: dict[str, Any],
    executions: tuple[SelectedWebsiteResolutionExecution, ...],
) -> tuple[dict[str, Any], ...]:
    by_candidate = {item.result.candidate_source_id: item for item in executions}
    routes = []
    for phase6b in sorted(
        feed_verification_result_payload.get("phase6c_routing", []),
        key=lambda item: str(item.get("candidate_source_id", "")),
    ):
        candidate_source_id = str(phase6b["candidate_source_id"])
        execution = by_candidate.get(candidate_source_id)
        if str(phase6b.get("routing")) == "HAS_USABLE_VERIFIED_FEED":
            routes.append(
                {
                    "candidate_source_id": candidate_source_id,
                    "phase6d_route": "USE_VERIFIED_FEED_RESOLUTION",
                    "reason": "usable_feed_verified_in_phase6b",
                    "selected_website_resolution_result_id": None,
                    "selected_website_acquisition_config_id": None,
                }
            )
            continue
        if execution and execution.result.feasibility_status == SelectedWebsiteResolutionStatus.FEASIBLE:
            config = execution.result.selected_website_acquisition_config
            routes.append(
                {
                    "candidate_source_id": candidate_source_id,
                    "phase6d_route": "USE_SELECTED_WEBSITE_RESOLUTION",
                    "reason": "selected_website_feasible_in_phase6c",
                    "selected_website_resolution_result_id": execution.result.selected_website_resolution_result_id,
                    "selected_website_acquisition_config_id": (
                        config.selected_website_acquisition_config_id if config else None
                    ),
                }
            )
            continue
        routes.append(
            {
                "candidate_source_id": candidate_source_id,
                "phase6d_route": "NEEDS_REVIEW_OR_UNSUPPORTED",
                "reason": (
                    "selected_website_resolution_not_feasible"
                    if execution else "selected_website_resolution_not_executed"
                ),
                "selected_website_resolution_result_id": (
                    execution.result.selected_website_resolution_result_id if execution else None
                ),
                "selected_website_acquisition_config_id": None,
            }
        )
    return tuple(routes)


def _semantic_execution_payload(execution: SelectedWebsiteResolutionExecution) -> dict[str, Any]:
    payload = execution.to_dict()
    payload.pop("fetch_cache_hit", None)
    return payload


def _semantic_evidence_payload(evidence: SelectedWebsiteDiscoveryEvidence) -> dict[str, Any]:
    payload = evidence.to_dict()
    payload["selected_candidates"] = [
        {
            "normalized_item_url": item.normalized_item_url,
            "item_title": item.item_title,
            "hint_categories": list(item.hint_categories),
            "date_hint": item.date_hint,
            "source_order": item.source_order,
        }
        for item in evidence.selected_candidates
    ]
    return payload
