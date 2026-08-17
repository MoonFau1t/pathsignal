from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any

from src.config import PROJECT_ROOT
from src.database.planning_identity import hash_canonical_value
from src.source_monitoring.acquisition_identity import (
    build_acquisition_resolution_fingerprint,
    build_acquisition_resolution_id,
    build_final_acquisition_resolution_output_hash,
    build_phase7_monitoring_handoff_id,
)
from src.source_monitoring.acquisition_models import (
    ACQUISITION_RESOLUTION_SCHEMA_VERSION,
    FEED_VERIFICATION_RESULT_SCHEMA_VERSION,
    AcquisitionMethod,
    AcquisitionPlanningResult,
    AcquisitionResolution,
    AcquisitionResolutionPlan,
    AcquisitionResolutionStatus,
    FeedFormat,
    FeedParseStatus,
    FeedVerificationResult,
    FeedVerificationStatus,
    Phase7MonitoringHandoff,
    SelectedWebsiteAcquisitionConfig,
    SelectedWebsiteResolutionResult,
    SelectedWebsiteResolutionStatus,
)
from src.source_monitoring.acquisition_planner import ACQUISITION_RESOLUTION_PLANS_FILE
from src.source_monitoring.feed_verifier import FEED_VERIFICATION_RESULTS_FILE
from src.source_monitoring.selected_website_resolver import SELECTED_WEBSITE_RESOLUTION_RESULTS_FILE
from src.source_monitoring.source_discovery_identity import normalize_source_url
from src.source_monitoring.source_discovery_models import SourceRole
from src.source_monitoring.source_evaluation_models import FetchStatus


PHASE6D_FINAL_ACQUISITION_RESULT_SET_SCHEMA_VERSION = "phase6d_final_acquisition_resolution_result_set_v1"
FINAL_ACQUISITION_RESOLUTION_POLICY_VERSION = "final_acquisition_resolution_policy_v1"
FEED_PRIMARY_SELECTION_POLICY_VERSION = "feed_primary_selection_policy_v1"
PHASE7_HANDOFF_POLICY_VERSION = "phase7_monitoring_handoff_policy_v1"
FEED_ITEM_IDENTITY_STRATEGY_REF = "feed_entry_guid_or_normalized_link_identity_v1"
FEED_RUNTIME_PARSER_STRATEGY_REF = "phase7_feed_runtime_parse_rss_atom_v1"
ACQUISITION_RESOLUTIONS_FILE = (
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "acquisition_resolutions.json"
)
ACQUISITION_RESOLUTION_DIAGNOSTIC_ROOT = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase6_acquisition"
    / "final_acquisition_resolution"
)


@dataclass(frozen=True)
class AcquisitionResolutionPolicy:
    resolution_policy_version: str = FINAL_ACQUISITION_RESOLUTION_POLICY_VERSION
    feed_primary_selection_policy_version: str = FEED_PRIMARY_SELECTION_POLICY_VERSION
    phase7_handoff_policy_version: str = PHASE7_HANDOFF_POLICY_VERSION
    feed_first: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution_policy_version": self.resolution_policy_version,
            "feed_primary_selection_policy_version": self.feed_primary_selection_policy_version,
            "phase7_handoff_policy_version": self.phase7_handoff_policy_version,
            "feed_first": self.feed_first,
        }


@dataclass(frozen=True)
class FeedResolutionEvidence:
    result: FeedVerificationResult
    execution_payload: dict[str, Any]
    plan_payload: dict[str, Any]
    source_relationship_status: str
    source_relationship_diagnostic: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "feed_verification_result_id": self.result.feed_verification_result_id,
            "feed_verification_plan_id": self.result.feed_verification_plan_id,
            "feed_candidate_url": self.result.feed_candidate_url,
            "final_url": self.result.final_url,
            "feed_title": self.result.feed_title,
            "feed_home_link": self.result.feed_home_link,
            "verified_feed_format": self.result.verified_feed_format.value,
            "verification_status": self.result.verification_status.value,
            "parse_status": self.result.parse_status.value,
            "sampled_entry_count": self.result.sampled_entry_count,
            "valid_entry_url_count": self.result.valid_entry_url_count,
            "stable_item_identity_support": self.result.stable_item_identity_support,
            "title_support": self.result.title_support,
            "publication_date_support": self.result.publication_date_support,
            "syntax_valid": self.result.syntax_valid,
            "usable_for_monitoring": self.result.usable_for_monitoring,
            "source_relationship_status": self.source_relationship_status,
            "source_relationship_diagnostic": self.source_relationship_diagnostic,
            "diagnostics": list(self.result.diagnostics),
            "reason_codes": list(self.reason_codes),
            "input_fingerprint": self.result.input_fingerprint,
        }


@dataclass(frozen=True)
class AcquisitionResolutionExecution:
    resolution: AcquisitionResolution
    acquisition_plan: AcquisitionResolutionPlan
    phase7_handoff: Phase7MonitoringHandoff | None
    primary_feed_evidence: FeedResolutionEvidence | None
    alternate_feed_evidence: tuple[FeedResolutionEvidence, ...]
    selected_website_result: SelectedWebsiteResolutionResult | None
    feed_evidence: tuple[FeedResolutionEvidence, ...]
    resolution_trace: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution": self.resolution.to_dict(),
            "acquisition_plan_ref": {
                "acquisition_resolution_plan_id": self.acquisition_plan.acquisition_resolution_plan_id,
                "candidate_source_id": self.acquisition_plan.candidate_source_id,
                "entity_id": self.acquisition_plan.entity_id,
                "final_source_evaluation_id": self.acquisition_plan.final_source_evaluation_id,
                "source_url": self.acquisition_plan.source_url,
                "observed_source_role": self.acquisition_plan.observed_source_role.value,
                "supported_information_need_ids": list(self.acquisition_plan.supported_information_need_ids),
            },
            "phase7_handoff": self.phase7_handoff.to_dict() if self.phase7_handoff else None,
            "primary_feed_evidence": (
                self.primary_feed_evidence.to_dict() if self.primary_feed_evidence else None
            ),
            "alternate_feed_evidence": [
                item.to_dict() for item in self.alternate_feed_evidence
            ],
            "selected_website_result": (
                self.selected_website_result.to_dict() if self.selected_website_result else None
            ),
            "feed_evidence": [item.to_dict() for item in self.feed_evidence],
            "resolution_trace": dict(self.resolution_trace),
        }


@dataclass(frozen=True)
class FinalAcquisitionResolutionResultSet:
    acquisition_resolution_results: tuple[AcquisitionResolutionExecution, ...]
    phase6a_input_hash: str
    phase6b_input_hash: str
    phase6c_input_hash: str
    input_fingerprint: str
    population_accounting: dict[str, Any]
    resolution_distribution: dict[str, int]
    method_distribution: dict[str, int]
    reason_code_summary: dict[str, int]
    phase7_handoff_accounting: dict[str, Any]
    needs_review_backlog: tuple[dict[str, Any], ...]
    unsupported_provenance: tuple[dict[str, Any], ...]
    multi_feed_audits: tuple[dict[str, Any], ...]
    diagnostics: tuple[str, ...]
    generation: dict[str, Any]
    output_hash: str
    policy: AcquisitionResolutionPolicy = AcquisitionResolutionPolicy()
    schema_version: str = PHASE6D_FINAL_ACQUISITION_RESULT_SET_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        phase7_handoffs = _unique_phase7_handoffs(
            self.acquisition_resolution_results
        )
        return {
            "schema_version": self.schema_version,
            "policy": self.policy.to_dict(),
            "phase6a_input_hash": self.phase6a_input_hash,
            "phase6b_input_hash": self.phase6b_input_hash,
            "phase6c_input_hash": self.phase6c_input_hash,
            "input_fingerprint": self.input_fingerprint,
            "acquisition_resolution_results": [
                item.to_dict() for item in self.acquisition_resolution_results
            ],
            "phase7_monitoring_handoffs": [
                handoff.to_dict() for handoff in phase7_handoffs
            ],
            "population_accounting": dict(self.population_accounting),
            "resolution_distribution": dict(self.resolution_distribution),
            "method_distribution": dict(self.method_distribution),
            "reason_code_summary": dict(self.reason_code_summary),
            "phase7_handoff_accounting": dict(self.phase7_handoff_accounting),
            "needs_review_backlog": [dict(item) for item in self.needs_review_backlog],
            "unsupported_provenance": [dict(item) for item in self.unsupported_provenance],
            "multi_feed_audits": [dict(item) for item in self.multi_feed_audits],
            "diagnostics": list(self.diagnostics),
            "generation": dict(self.generation),
            "output_hash": self.output_hash,
        }


class AcquisitionResolutionError(ValueError):
    pass


class FinalAcquisitionResolver:
    def __init__(self, policy: AcquisitionResolutionPolicy | None = None) -> None:
        self.policy = policy or AcquisitionResolutionPolicy()

    def resolve(
        self,
        *,
        planning_result: AcquisitionPlanningResult,
        feed_verification_result_payload: dict[str, Any],
        selected_website_result_payload: dict[str, Any],
        generation_mode: str = "phase6d_final_acquisition_resolution",
    ) -> FinalAcquisitionResolutionResultSet:
        feed_by_candidate = _feed_evidence_by_candidate(feed_verification_result_payload)
        website_by_candidate = _selected_website_by_candidate(selected_website_result_payload)
        _reject_duplicate_plans(planning_result.acquisition_resolution_plans)

        executions = []
        diagnostics: list[str] = []
        for plan in sorted(
            planning_result.acquisition_resolution_plans,
            key=lambda item: item.candidate_source_id,
        ):
            executions.append(
                self._resolve_one(
                    plan=plan,
                    feed_evidence=feed_by_candidate.get(plan.candidate_source_id, ()),
                    website_result=website_by_candidate.get(plan.candidate_source_id),
                )
            )

        executions = list(
            _reconcile_phase7_handoffs(tuple(executions))
        )

        population = _population_accounting(
            planning_result=planning_result,
            executions=tuple(executions),
        )
        if population["missing_candidate_source_ids"] or population["duplicate_candidate_source_ids"]:
            diagnostics.append("population_reconciliation_has_gaps")
        resolution_distribution = _resolution_distribution(tuple(executions))
        method_distribution = _method_distribution(tuple(executions))
        reason_summary = _reason_code_summary(tuple(executions))
        handoff_accounting = _phase7_handoff_accounting(tuple(executions))
        needs_review = tuple(
            _backlog_item(item)
            for item in executions
            if item.resolution.resolution_status == AcquisitionResolutionStatus.NEEDS_REVIEW
        )
        unsupported = tuple(
            _backlog_item(item)
            for item in executions
            if item.resolution.resolution_status == AcquisitionResolutionStatus.UNSUPPORTED
        )
        multi_feed_audits = tuple(
            _multi_feed_audit(item)
            for item in executions
            if len(item.alternate_feed_evidence) > 0
        )
        input_fingerprint = hash_canonical_value(
            {
                "phase6a_output_hash": planning_result.output_hash,
                "phase6b_output_hash": feed_verification_result_payload.get("output_hash"),
                "phase6c_output_hash": selected_website_result_payload.get("output_hash"),
                "acquisition_resolution_plan_ids": tuple(
                    item.acquisition_resolution_plan_id
                    for item in sorted(
                        planning_result.acquisition_resolution_plans,
                        key=lambda item: item.candidate_source_id,
                    )
                ),
                "policy": self.policy.to_dict(),
            }
        )
        semantic_payload = {
            "phase6a_input_hash": planning_result.output_hash,
            "phase6b_input_hash": str(feed_verification_result_payload.get("output_hash", "")),
            "phase6c_input_hash": str(selected_website_result_payload.get("output_hash", "")),
            "input_fingerprint": input_fingerprint,
            "acquisition_resolution_results": [
                _semantic_execution_payload(item) for item in executions
            ],
            "population_accounting": population,
            "resolution_distribution": resolution_distribution,
            "method_distribution": method_distribution,
            "reason_code_summary": reason_summary,
            "phase7_handoff_accounting": handoff_accounting,
            "needs_review_backlog": [dict(item) for item in needs_review],
            "unsupported_provenance": [dict(item) for item in unsupported],
            "multi_feed_audits": [dict(item) for item in multi_feed_audits],
            "diagnostics": tuple(sorted(diagnostics)),
            "policy": self.policy.to_dict(),
        }
        return FinalAcquisitionResolutionResultSet(
            acquisition_resolution_results=tuple(executions),
            phase6a_input_hash=planning_result.output_hash,
            phase6b_input_hash=str(feed_verification_result_payload.get("output_hash", "")),
            phase6c_input_hash=str(selected_website_result_payload.get("output_hash", "")),
            input_fingerprint=input_fingerprint,
            population_accounting=population,
            resolution_distribution=resolution_distribution,
            method_distribution=method_distribution,
            reason_code_summary=reason_summary,
            phase7_handoff_accounting=handoff_accounting,
            needs_review_backlog=needs_review,
            unsupported_provenance=unsupported,
            multi_feed_audits=multi_feed_audits,
            diagnostics=tuple(sorted(diagnostics)),
            generation={
                "generation_mode": generation_mode,
                "http_calls": 0,
                "brave_calls": 0,
                "deepseek_calls": 0,
                "llm_calls": 0,
                "browser_calls": 0,
                "feed_parser_calls": 0,
                "html_inspection_calls": 0,
                "monitoring_execution_started": False,
            },
            output_hash=build_final_acquisition_resolution_output_hash(**semantic_payload),
            policy=self.policy,
        )

    def _resolve_one(
        self,
        *,
        plan: AcquisitionResolutionPlan,
        feed_evidence: tuple[FeedResolutionEvidence, ...],
        website_result: SelectedWebsiteResolutionResult | None,
    ) -> AcquisitionResolutionExecution:
        usable_feeds = tuple(item for item in feed_evidence if _is_usable_verified_feed(item.result))
        primary_feed = select_primary_feed(usable_feeds) if usable_feeds else None
        alternate_feeds = tuple(item for item in usable_feeds if item is not primary_feed)
        reason_codes = set(_feed_reason_codes(feed_evidence))
        limitations = set(plan.known_technical_limitation_flags)
        evidence_quality = "phase6_technical_evidence_complete"

        if primary_feed is not None:
            method = (
                AcquisitionMethod.RSS
                if primary_feed.result.verified_feed_format == FeedFormat.RSS
                else AcquisitionMethod.ATOM
            )
            status = AcquisitionResolutionStatus.RESOLVED
            reason_codes.add(
                "verified_rss_available"
                if method == AcquisitionMethod.RSS
                else "verified_atom_available"
            )
            reason_codes.add("usable_verified_feed_selected")
            if alternate_feeds:
                reason_codes.add("alternate_usable_feed_preserved")
                limitations.add("multiple_usable_feeds_primary_selected")
            selected_config_ref = None
            feed_ids = (primary_feed.result.feed_verification_result_id,)
            verified_format = primary_feed.result.verified_feed_format
        elif _is_feasible_selected_website(website_result):
            status = AcquisitionResolutionStatus.RESOLVED
            method = AcquisitionMethod.SELECTED_WEBSITE
            reason_codes.add("selected_website_feasible")
            reason_codes.add("no_usable_verified_feed")
            selected_config_ref = (
                website_result.selected_website_acquisition_config.selected_website_acquisition_config_id
                if website_result and website_result.selected_website_acquisition_config
                else None
            )
            feed_ids = ()
            verified_format = None
        else:
            status, reason_codes, limitations = _unresolved_status(
                feed_evidence=feed_evidence,
                website_result=website_result,
                reason_codes=reason_codes,
                limitations=limitations,
            )
            method = None
            selected_config_ref = None
            feed_ids = ()
            verified_format = None

        fingerprint = _resolution_fingerprint(
            plan=plan,
            feed_evidence=feed_evidence,
            website_result=website_result,
            primary_feed=primary_feed,
            alternate_feeds=alternate_feeds,
            resolution_status=status,
            acquisition_method=method,
            selected_config_ref=selected_config_ref,
            reason_codes=tuple(sorted(reason_codes)),
            technical_limitation_flags=tuple(sorted(limitations)),
            policy=self.policy,
        )
        resolution_id = build_acquisition_resolution_id(
            acquisition_resolution_plan_id=plan.acquisition_resolution_plan_id,
            candidate_source_id=plan.candidate_source_id,
            final_source_evaluation_id=plan.final_source_evaluation_id,
            resolution_status=status.value,
            acquisition_method=method.value if method else None,
            input_fingerprint=fingerprint,
        )
        resolution = AcquisitionResolution(
            acquisition_resolution_id=resolution_id,
            acquisition_resolution_plan_id=plan.acquisition_resolution_plan_id,
            candidate_source_id=plan.candidate_source_id,
            entity_id=plan.entity_id,
            final_source_evaluation_id=plan.final_source_evaluation_id,
            source_url=plan.source_url,
            resolution_status=status,
            acquisition_method=method,
            feed_verification_result_ids=feed_ids,
            selected_website_resolution_result_id=(
                website_result.selected_website_resolution_result_id
                if method == AcquisitionMethod.SELECTED_WEBSITE and website_result
                else None
            ),
            selected_acquisition_config_ref=selected_config_ref,
            verified_feed_format=verified_format,
            technical_limitation_flags=tuple(sorted(limitations)),
            resolution_reason_codes=tuple(sorted(reason_codes)),
            evidence_quality=evidence_quality,
            resolution_policy_version=self.policy.resolution_policy_version,
            input_fingerprint=fingerprint,
        )
        handoff = (
            _build_phase7_handoff(
                plan=plan,
                resolution=resolution,
                primary_feed=primary_feed,
                alternate_feeds=alternate_feeds,
                website_result=website_result,
                policy=self.policy,
            )
            if resolution.resolution_status == AcquisitionResolutionStatus.RESOLVED
            else None
        )
        return AcquisitionResolutionExecution(
            resolution=resolution,
            acquisition_plan=plan,
            phase7_handoff=handoff,
            primary_feed_evidence=primary_feed,
            alternate_feed_evidence=alternate_feeds,
            selected_website_result=website_result,
            feed_evidence=feed_evidence,
            resolution_trace={
                "policy_version": self.policy.resolution_policy_version,
                "feed_first": self.policy.feed_first,
                "usable_feed_count": len(usable_feeds),
                "selected_primary_feed_result_id": (
                    primary_feed.result.feed_verification_result_id if primary_feed else None
                ),
                "alternate_usable_feed_result_ids": [
                    item.result.feed_verification_result_id for item in alternate_feeds
                ],
                "selected_website_resolution_result_id": (
                    website_result.selected_website_resolution_result_id
                    if website_result
                    else None
                ),
                "selected_website_feasibility_status": (
                    website_result.feasibility_status.value if website_result else None
                ),
                "selected_website_config_ref": selected_config_ref,
                "final_resolution_status": status.value,
                "final_acquisition_method": method.value if method else None,
            },
        )


def resolve_acquisition_plans(
    *,
    planning_result: AcquisitionPlanningResult,
    feed_verification_result_payload: dict[str, Any],
    selected_website_result_payload: dict[str, Any],
    policy: AcquisitionResolutionPolicy | None = None,
    generation_mode: str = "phase6d_final_acquisition_resolution",
) -> FinalAcquisitionResolutionResultSet:
    return FinalAcquisitionResolver(policy=policy).resolve(
        planning_result=planning_result,
        feed_verification_result_payload=feed_verification_result_payload,
        selected_website_result_payload=selected_website_result_payload,
        generation_mode=generation_mode,
    )


def load_phase6d_inputs(
    *,
    planning_path: Path = ACQUISITION_RESOLUTION_PLANS_FILE,
    feed_verification_path: Path = FEED_VERIFICATION_RESULTS_FILE,
    selected_website_path: Path = SELECTED_WEBSITE_RESOLUTION_RESULTS_FILE,
) -> tuple[AcquisitionPlanningResult, dict[str, Any], dict[str, Any]]:
    planning = AcquisitionPlanningResult.from_dict(json.loads(planning_path.read_text(encoding="utf-8")))
    feed_payload = json.loads(feed_verification_path.read_text(encoding="utf-8"))
    website_payload = json.loads(selected_website_path.read_text(encoding="utf-8"))
    return planning, feed_payload, website_payload


def persist_final_acquisition_resolution_results(
    *,
    result_set: FinalAcquisitionResolutionResultSet,
    output_file: Path = ACQUISITION_RESOLUTIONS_FILE,
) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result_set.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    if output_file.exists() and output_file.read_text(encoding="utf-8") == text:
        return output_file
    output_file.write_text(text, encoding="utf-8")
    return output_file


def select_primary_feed(feeds: tuple[FeedResolutionEvidence, ...]) -> FeedResolutionEvidence:
    if not feeds:
        raise AcquisitionResolutionError("select_primary_feed requires at least one feed.")
    return sorted(feeds, key=_feed_selection_key)[0]


def parse_feed_verification_result(payload: dict[str, Any]) -> FeedVerificationResult:
    return FeedVerificationResult(
        feed_verification_result_id=str(payload["feed_verification_result_id"]),
        feed_verification_plan_id=str(payload["feed_verification_plan_id"]),
        candidate_source_id=str(payload["candidate_source_id"]),
        feed_candidate_url=str(payload.get("feed_candidate_url", "")),
        final_url=payload.get("final_url"),
        fetch_execution_id=payload.get("fetch_execution_id"),
        fetch_status=FetchStatus(str(payload["fetch_status"])) if payload.get("fetch_status") else None,
        http_status=int(payload["http_status"]) if payload.get("http_status") is not None else None,
        content_type=payload.get("content_type"),
        redirect_chain=tuple(dict(item) for item in payload.get("redirect_chain", ())),
        parse_status=FeedParseStatus(str(payload["parse_status"])),
        verified_feed_format=FeedFormat(str(payload["verified_feed_format"])),
        feed_title=payload.get("feed_title"),
        feed_home_link=payload.get("feed_home_link"),
        sampled_entry_count=int(payload.get("sampled_entry_count", 0)),
        valid_entry_url_count=int(payload.get("valid_entry_url_count", 0)),
        title_support=bool(payload.get("title_support", False)),
        publication_date_support=bool(payload.get("publication_date_support", False)),
        stable_item_identity_support=bool(payload.get("stable_item_identity_support", False)),
        syntax_valid=bool(payload.get("syntax_valid", False)),
        usable_for_monitoring=bool(payload.get("usable_for_monitoring", False)),
        verification_status=FeedVerificationStatus(str(payload["verification_status"])),
        failure_reason=payload.get("failure_reason"),
        diagnostics=tuple(str(item) for item in payload.get("diagnostics", ())),
        verification_policy_version=str(payload.get("verification_policy_version", "")),
        input_fingerprint=str(payload["input_fingerprint"]),
        schema_version=str(payload.get("schema_version", FEED_VERIFICATION_RESULT_SCHEMA_VERSION)),
    )


def parse_selected_website_resolution_result(payload: dict[str, Any]) -> SelectedWebsiteResolutionResult:
    config_payload = payload.get("selected_website_acquisition_config")
    config = (
        parse_selected_website_acquisition_config(config_payload)
        if isinstance(config_payload, dict)
        else None
    )
    return SelectedWebsiteResolutionResult(
        selected_website_resolution_result_id=str(payload["selected_website_resolution_result_id"]),
        selected_website_resolution_plan_id=str(payload["selected_website_resolution_plan_id"]),
        candidate_source_id=str(payload["candidate_source_id"]),
        final_source_evaluation_id=str(payload["final_source_evaluation_id"]),
        source_url=str(payload.get("source_url", "")),
        feasibility_status=SelectedWebsiteResolutionStatus(str(payload["feasibility_status"])),
        candidate_item_link_discoverability=str(payload.get("candidate_item_link_discoverability", "")),
        normalized_item_url_support=bool(payload.get("normalized_item_url_support", False)),
        item_title_support=bool(payload.get("item_title_support", False)),
        date_hint_support=bool(payload.get("date_hint_support", False)),
        item_type_role_support=bool(payload.get("item_type_role_support", False)),
        bounded_extraction_consistency=str(payload.get("bounded_extraction_consistency", "")),
        technical_limitations=tuple(str(item) for item in payload.get("technical_limitations", ())),
        selected_website_acquisition_config=config,
        reason_codes=tuple(str(item) for item in payload.get("reason_codes", ())),
        resolution_policy_version=str(payload.get("resolution_policy_version", "")),
        input_fingerprint=str(payload["input_fingerprint"]),
    )


def parse_selected_website_acquisition_config(payload: dict[str, Any]) -> SelectedWebsiteAcquisitionConfig:
    return SelectedWebsiteAcquisitionConfig(
        selected_website_acquisition_config_id=str(payload["selected_website_acquisition_config_id"]),
        source_url=str(payload.get("source_url", "")),
        acquisition_method=AcquisitionMethod(str(payload["acquisition_method"])),
        item_discovery_strategy_version=str(payload.get("item_discovery_strategy_version", "")),
        allowed_domain_scope=tuple(str(item) for item in payload.get("allowed_domain_scope", ())),
        item_link_normalization_policy=str(payload.get("item_link_normalization_policy", "")),
        max_discovered_items_per_run=int(payload.get("max_discovered_items_per_run", 0)),
        title_extraction_strategy_ref=payload.get("title_extraction_strategy_ref"),
        date_extraction_strategy_ref=payload.get("date_extraction_strategy_ref"),
        dedup_identity_strategy_version=str(payload.get("dedup_identity_strategy_version", "")),
        source_role=SourceRole(str(payload["source_role"])),
        provenance=dict(payload.get("provenance") or {}),
        input_fingerprint=str(payload["input_fingerprint"]),
    )


def file_signature(path: Path) -> dict[str, Any]:
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


def _feed_evidence_by_candidate(payload: dict[str, Any]) -> dict[str, tuple[FeedResolutionEvidence, ...]]:
    rows: dict[str, list[FeedResolutionEvidence]] = {}
    for execution in payload.get("feed_verification_results", []):
        result = parse_feed_verification_result(dict(execution["result"]))
        evidence = FeedResolutionEvidence(
            result=result,
            execution_payload=dict(execution),
            plan_payload=dict(execution.get("plan") or {}),
            source_relationship_status=str(execution.get("source_relationship_status", "")),
            source_relationship_diagnostic=str(execution.get("source_relationship_diagnostic", "")),
            reason_codes=tuple(str(item) for item in execution.get("reason_codes", ())),
        )
        rows.setdefault(result.candidate_source_id, []).append(evidence)
    return {
        key: tuple(sorted(value, key=lambda item: item.result.feed_verification_result_id))
        for key, value in rows.items()
    }


def _selected_website_by_candidate(payload: dict[str, Any]) -> dict[str, SelectedWebsiteResolutionResult]:
    rows = {}
    for execution in payload.get("selected_website_resolution_results", []):
        result = parse_selected_website_resolution_result(dict(execution["result"]))
        if result.candidate_source_id in rows:
            raise AcquisitionResolutionError(
                f"duplicate selected website result for candidate: {result.candidate_source_id}"
            )
        rows[result.candidate_source_id] = result
    return rows


def _is_usable_verified_feed(result: FeedVerificationResult) -> bool:
    return (
        result.verification_status == FeedVerificationStatus.VERIFIED_USABLE
        and result.usable_for_monitoring
        and result.syntax_valid
        and result.verified_feed_format in {FeedFormat.RSS, FeedFormat.ATOM}
        and result.valid_entry_url_count > 0
    )


def _is_feasible_selected_website(result: SelectedWebsiteResolutionResult | None) -> bool:
    return (
        result is not None
        and result.feasibility_status == SelectedWebsiteResolutionStatus.FEASIBLE
        and result.selected_website_acquisition_config is not None
    )


def _feed_selection_key(feed: FeedResolutionEvidence) -> tuple[Any, ...]:
    result = feed.result
    relationship_rank = {
        "same_domain": 0,
        "related_home_link": 1,
        "same_root_domain": 2,
        "unresolved_cross_domain": 3,
        "invalid": 4,
    }.get(feed.source_relationship_status, 5)
    limitation_count = len(
        (set(result.diagnostics) | set(feed.reason_codes))
        - {"rss_recognized", "atom_recognized", "source_relationship:same_domain", "verified_usable"}
    )
    return (
        relationship_rank,
        0 if result.stable_item_identity_support else 1,
        -result.valid_entry_url_count,
        0 if result.title_support else 1,
        0 if result.publication_date_support else 1,
        limitation_count,
        normalize_source_url(result.final_url or result.feed_candidate_url),
    )


def _resolution_fingerprint(
    *,
    plan: AcquisitionResolutionPlan,
    feed_evidence: tuple[FeedResolutionEvidence, ...],
    website_result: SelectedWebsiteResolutionResult | None,
    primary_feed: FeedResolutionEvidence | None,
    alternate_feeds: tuple[FeedResolutionEvidence, ...],
    resolution_status: AcquisitionResolutionStatus,
    acquisition_method: AcquisitionMethod | None,
    selected_config_ref: str | None,
    reason_codes: tuple[str, ...],
    technical_limitation_flags: tuple[str, ...],
    policy: AcquisitionResolutionPolicy,
) -> str:
    return build_acquisition_resolution_fingerprint(
        acquisition_resolution_plan_id=plan.acquisition_resolution_plan_id,
        acquisition_resolution_plan_fingerprint=plan.input_fingerprint,
        candidate_source_id=plan.candidate_source_id,
        entity_id=plan.entity_id,
        final_source_evaluation_id=plan.final_source_evaluation_id,
        source_url=plan.source_url,
        source_role=plan.observed_source_role.value,
        supported_information_need_ids=plan.supported_information_need_ids,
        feed_result_fingerprints=tuple(
            (item.result.feed_verification_result_id, item.result.input_fingerprint)
            for item in sorted(feed_evidence, key=lambda item: item.result.feed_verification_result_id)
        ),
        selected_website_result_fingerprint=(
            (
                website_result.selected_website_resolution_result_id,
                website_result.input_fingerprint,
                website_result.selected_website_acquisition_config.input_fingerprint
                if website_result.selected_website_acquisition_config
                else None,
            )
            if website_result
            else None
        ),
        primary_feed_result_id=primary_feed.result.feed_verification_result_id if primary_feed else None,
        primary_feed_url=primary_feed.result.feed_candidate_url if primary_feed else None,
        primary_feed_final_url=primary_feed.result.final_url if primary_feed else None,
        alternate_feed_result_ids=tuple(
            item.result.feed_verification_result_id for item in alternate_feeds
        ),
        selected_config_ref=selected_config_ref,
        resolution_status=resolution_status.value,
        acquisition_method=acquisition_method.value if acquisition_method else None,
        reason_codes=reason_codes,
        technical_limitation_flags=technical_limitation_flags,
        policy=policy.to_dict(),
    )


def _build_phase7_handoff(
    *,
    plan: AcquisitionResolutionPlan,
    resolution: AcquisitionResolution,
    primary_feed: FeedResolutionEvidence | None,
    alternate_feeds: tuple[FeedResolutionEvidence, ...],
    website_result: SelectedWebsiteResolutionResult | None,
    policy: AcquisitionResolutionPolicy,
) -> Phase7MonitoringHandoff:
    if resolution.acquisition_method in {AcquisitionMethod.RSS, AcquisitionMethod.ATOM}:
        if primary_feed is None:
            raise AcquisitionResolutionError("feed handoff requires primary feed evidence.")
        acquisition_config_ref = primary_feed.result.feed_verification_result_id
        provenance = {
            "phase": "phase6d_final_acquisition_resolution",
            "handoff_policy_version": policy.phase7_handoff_policy_version,
            "execution_kind": "verified_feed_runtime_parse",
            "verified_feed_url": primary_feed.result.final_url or primary_feed.result.feed_candidate_url,
            "feed_candidate_url": primary_feed.result.feed_candidate_url,
            "feed_format": primary_feed.result.verified_feed_format.value,
            "selected_feed_verification_result_id": primary_feed.result.feed_verification_result_id,
            "alternate_verified_feed_result_ids": [
                item.result.feed_verification_result_id for item in alternate_feeds
            ],
            "parser_policy_version": primary_feed.plan_payload.get("parser_policy_version"),
            "verification_policy_version": primary_feed.result.verification_policy_version,
            "runtime_parser_strategy_ref": FEED_RUNTIME_PARSER_STRATEGY_REF,
            "item_identity_strategy_ref": FEED_ITEM_IDENTITY_STRATEGY_REF,
            "bounded_item_handling": {
                "sampled_entry_count_in_phase6b": primary_feed.result.sampled_entry_count,
                "valid_entry_url_count_in_phase6b": primary_feed.result.valid_entry_url_count,
            },
            "technical_limitations": list(resolution.technical_limitation_flags),
        }
    else:
        if website_result is None or website_result.selected_website_acquisition_config is None:
            raise AcquisitionResolutionError("website handoff requires selected website config.")
        config = website_result.selected_website_acquisition_config
        acquisition_config_ref = config.selected_website_acquisition_config_id
        provenance = {
            "phase": "phase6d_final_acquisition_resolution",
            "handoff_policy_version": policy.phase7_handoff_policy_version,
            "execution_kind": "selected_website_source_surface_discovery",
            "selected_website_resolution_result_id": website_result.selected_website_resolution_result_id,
            "selected_website_acquisition_config_id": config.selected_website_acquisition_config_id,
            "item_discovery_strategy_version": config.item_discovery_strategy_version,
            "item_link_normalization_policy": config.item_link_normalization_policy,
            "dedup_identity_strategy_version": config.dedup_identity_strategy_version,
            "max_discovered_items_per_run": config.max_discovered_items_per_run,
            "technical_limitations": list(resolution.technical_limitation_flags),
        }
    acquisition_target_url = _handoff_acquisition_target_url(
        acquisition_method=resolution.acquisition_method,
        source_url=resolution.source_url,
        provenance=provenance,
    )
    handoff_id = build_phase7_monitoring_handoff_id(
        acquisition_method=resolution.acquisition_method.value,
        acquisition_target_url=acquisition_target_url,
    )
    return Phase7MonitoringHandoff.from_resolution(
        phase7_monitoring_handoff_id=handoff_id,
        resolution=resolution,
        supported_information_need_ids=plan.supported_information_need_ids,
        source_role=plan.observed_source_role,
        provenance=provenance,
    )


def _handoff_acquisition_target_url(
    *,
    acquisition_method: AcquisitionMethod,
    source_url: str,
    provenance: dict[str, Any],
) -> str:
    if acquisition_method in {AcquisitionMethod.RSS, AcquisitionMethod.ATOM}:
        target_url = str(
            provenance.get("verified_feed_url")
            or provenance.get("feed_candidate_url")
            or source_url
        )
    else:
        target_url = source_url
    return normalize_source_url(target_url)


def _canonical_handoff_identity(
    handoff: Phase7MonitoringHandoff,
) -> tuple[str, str]:
    return (
        handoff.acquisition_method.value,
        _handoff_acquisition_target_url(
            acquisition_method=handoff.acquisition_method,
            source_url=handoff.source_url,
            provenance=handoff.provenance,
        ),
    )


def _reconcile_phase7_handoffs(
    executions: tuple[AcquisitionResolutionExecution, ...],
) -> tuple[AcquisitionResolutionExecution, ...]:
    grouped: dict[tuple[str, str], list[AcquisitionResolutionExecution]] = {}
    for execution in executions:
        if execution.phase7_handoff is None:
            continue
        grouped.setdefault(
            _canonical_handoff_identity(execution.phase7_handoff), []
        ).append(execution)

    canonical_by_identity: dict[tuple[str, str], Phase7MonitoringHandoff] = {}
    for identity, contributors in sorted(grouped.items()):
        ordered = sorted(
            contributors,
            key=lambda item: (
                item.resolution.candidate_source_id,
                item.resolution.acquisition_resolution_id,
            ),
        )
        representative = ordered[0].phase7_handoff
        if representative is None:
            raise AcquisitionResolutionError(
                "canonical handoff contributor is missing a handoff."
            )
        provenance = dict(representative.provenance)
        provenance["canonical_acquisition_identity"] = {
            "acquisition_method": identity[0],
            "normalized_target_url": identity[1],
        }
        provenance["contributing_candidate_sources"] = [
            _handoff_contributor_provenance(item) for item in ordered
        ]
        canonical_by_identity[identity] = replace(
            representative,
            supported_information_need_ids=tuple(
                sorted(
                    {
                        information_need_id
                        for item in ordered
                        for information_need_id in (
                            item.acquisition_plan.supported_information_need_ids
                        )
                    }
                )
            ),
            provenance=provenance,
        )

    return tuple(
        replace(
            execution,
            phase7_handoff=canonical_by_identity[
                _canonical_handoff_identity(execution.phase7_handoff)
            ],
        )
        if execution.phase7_handoff is not None
        else execution
        for execution in executions
    )


def _handoff_contributor_provenance(
    execution: AcquisitionResolutionExecution,
) -> dict[str, Any]:
    handoff = execution.phase7_handoff
    if handoff is None:
        raise AcquisitionResolutionError(
            "canonical handoff provenance requires a resolved handoff."
        )
    resolution = execution.resolution
    return {
        "candidate_source_id": resolution.candidate_source_id,
        "acquisition_resolution_id": resolution.acquisition_resolution_id,
        "acquisition_resolution_plan_id": (
            resolution.acquisition_resolution_plan_id
        ),
        "final_source_evaluation_id": resolution.final_source_evaluation_id,
        "entity_id": resolution.entity_id,
        "source_url": resolution.source_url,
        "source_role": execution.acquisition_plan.observed_source_role.value,
        "supported_information_need_ids": list(
            execution.acquisition_plan.supported_information_need_ids
        ),
        "acquisition_config_ref": handoff.acquisition_config_ref,
        "feed_verification_result_ids": list(
            resolution.feed_verification_result_ids
        ),
        "selected_website_resolution_result_id": (
            resolution.selected_website_resolution_result_id
        ),
    }


def _unique_phase7_handoffs(
    executions: tuple[AcquisitionResolutionExecution, ...],
) -> tuple[Phase7MonitoringHandoff, ...]:
    unique: dict[str, Phase7MonitoringHandoff] = {}
    for execution in executions:
        handoff = execution.phase7_handoff
        if handoff is not None:
            unique.setdefault(handoff.phase7_monitoring_handoff_id, handoff)
    return tuple(unique.values())


def _unresolved_status(
    *,
    feed_evidence: tuple[FeedResolutionEvidence, ...],
    website_result: SelectedWebsiteResolutionResult | None,
    reason_codes: set[str],
    limitations: set[str],
) -> tuple[AcquisitionResolutionStatus, set[str], set[str]]:
    reason_codes.add("no_usable_verified_feed")
    if not feed_evidence:
        reason_codes.add("no_known_feed_candidate_verified")
    if website_result is None:
        reason_codes.add("selected_website_evidence_missing")
        return AcquisitionResolutionStatus.NEEDS_REVIEW, reason_codes, limitations
    if (
        website_result.feasibility_status == SelectedWebsiteResolutionStatus.FEASIBLE
        and website_result.selected_website_acquisition_config is None
    ):
        reason_codes.add("selected_website_config_missing")
        reason_codes.add("technical_evidence_conflicting")
        return AcquisitionResolutionStatus.NEEDS_REVIEW, reason_codes, limitations
    if website_result.feasibility_status == SelectedWebsiteResolutionStatus.NEEDS_REVIEW:
        reason_codes.add("selected_website_needs_review")
        limitations.update(website_result.technical_limitations)
        return AcquisitionResolutionStatus.NEEDS_REVIEW, reason_codes, limitations
    if _has_incomplete_feed_evidence(feed_evidence):
        reason_codes.add("technical_evidence_incomplete")
        limitations.update(website_result.technical_limitations)
        return AcquisitionResolutionStatus.NEEDS_REVIEW, reason_codes, limitations
    reason_codes.add("selected_website_unsupported")
    reason_codes.add("no_supported_acquisition_method")
    limitations.update(website_result.technical_limitations)
    return AcquisitionResolutionStatus.UNSUPPORTED, reason_codes, limitations


def _has_incomplete_feed_evidence(feed_evidence: tuple[FeedResolutionEvidence, ...]) -> bool:
    incomplete_statuses = {
        FeedVerificationStatus.NEEDS_REVIEW,
        FeedVerificationStatus.UNREACHABLE,
    }
    return any(item.result.verification_status in incomplete_statuses for item in feed_evidence)


def _feed_reason_codes(feed_evidence: tuple[FeedResolutionEvidence, ...]) -> tuple[str, ...]:
    reasons: set[str] = set()
    if not feed_evidence:
        return ("no_known_feed_candidate_verified",)
    if not any(_is_usable_verified_feed(item.result) for item in feed_evidence):
        reasons.add("no_usable_verified_feed")
    for item in feed_evidence:
        if item.result.verification_status == FeedVerificationStatus.EMPTY_OR_INSUFFICIENT:
            reasons.add("feed_empty_or_insufficient")
        elif item.result.verification_status == FeedVerificationStatus.VERIFIED_BUT_LIMITED:
            reasons.add("feed_verified_but_limited")
        elif item.result.verification_status == FeedVerificationStatus.NEEDS_REVIEW:
            reasons.add("feed_needs_review")
        elif item.result.verification_status == FeedVerificationStatus.UNREACHABLE:
            reasons.add("feed_unreachable")
        elif item.result.verification_status in {
            FeedVerificationStatus.INVALID_FEED,
            FeedVerificationStatus.PARSE_FAILURE,
            FeedVerificationStatus.UNSUPPORTED_CONTENT,
        }:
            reasons.add("feed_not_usable")
    return tuple(sorted(reasons))


def _population_accounting(
    *,
    planning_result: AcquisitionPlanningResult,
    executions: tuple[AcquisitionResolutionExecution, ...],
) -> dict[str, Any]:
    plan_ids = [item.candidate_source_id for item in planning_result.acquisition_resolution_plans]
    resolution_ids = [item.resolution.candidate_source_id for item in executions]
    duplicates = sorted({item for item in resolution_ids if resolution_ids.count(item) > 1})
    missing = sorted(set(plan_ids) - set(resolution_ids))
    extras = sorted(set(resolution_ids) - set(plan_ids))
    return {
        "approved_source_count": planning_result.approved_input_count,
        "acquisition_resolution_plan_count": len(plan_ids),
        "final_acquisition_resolution_count": len(resolution_ids),
        "missing_candidate_source_ids": missing,
        "duplicate_candidate_source_ids": duplicates,
        "unexpected_extra_candidate_source_ids": extras,
        "all_plans_resolved_once": not missing and not duplicates and not extras and len(plan_ids) == len(resolution_ids),
    }


def _phase7_handoff_accounting(executions: tuple[AcquisitionResolutionExecution, ...]) -> dict[str, Any]:
    resolved = {
        item.resolution.candidate_source_id
        for item in executions
        if item.resolution.resolution_status == AcquisitionResolutionStatus.RESOLVED
    }
    covered_sources = {
        item.resolution.candidate_source_id
        for item in executions
        if item.phase7_handoff is not None
    }
    canonical_handoffs = _unique_phase7_handoffs(executions)
    return {
        "resolved_source_count": len(resolved),
        "phase7_handoff_count": len(canonical_handoffs),
        "canonical_acquisition_identity_count": len(canonical_handoffs),
        "converged_candidate_source_count": max(
            0, len(resolved) - len(canonical_handoffs)
        ),
        "handoff_matches_resolved_sources": resolved == covered_sources,
        "missing_handoff_candidate_source_ids": sorted(
            resolved - covered_sources
        ),
        "unexpected_handoff_candidate_source_ids": sorted(
            covered_sources - resolved
        ),
    }


def _resolution_distribution(executions: tuple[AcquisitionResolutionExecution, ...]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for item in executions:
        key = item.resolution.resolution_status.value
        distribution[key] = distribution.get(key, 0) + 1
    return dict(sorted(distribution.items()))


def _method_distribution(executions: tuple[AcquisitionResolutionExecution, ...]) -> dict[str, int]:
    distribution = {"rss": 0, "atom": 0, "selected_website": 0, "unresolved": 0}
    for item in executions:
        method = item.resolution.acquisition_method
        if method is None:
            distribution["unresolved"] += 1
        else:
            distribution[method.value] = distribution.get(method.value, 0) + 1
    return dict(sorted(distribution.items()))


def _reason_code_summary(executions: tuple[AcquisitionResolutionExecution, ...]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in executions:
        for code in item.resolution.resolution_reason_codes:
            summary[code] = summary.get(code, 0) + 1
    return dict(sorted(summary.items()))


def _backlog_item(execution: AcquisitionResolutionExecution) -> dict[str, Any]:
    return {
        "candidate_source_id": execution.resolution.candidate_source_id,
        "entity_id": execution.resolution.entity_id,
        "acquisition_resolution_id": execution.resolution.acquisition_resolution_id,
        "resolution_status": execution.resolution.resolution_status.value,
        "source_url": execution.resolution.source_url,
        "reason_codes": list(execution.resolution.resolution_reason_codes),
        "technical_limitation_flags": list(execution.resolution.technical_limitation_flags),
    }


def _multi_feed_audit(execution: AcquisitionResolutionExecution) -> dict[str, Any]:
    feeds = tuple(
        sorted(
            (execution.primary_feed_evidence, *execution.alternate_feed_evidence),
            key=lambda item: _feed_selection_key(item),
        )
    )
    return {
        "candidate_source_id": execution.resolution.candidate_source_id,
        "source_url": execution.resolution.source_url,
        "usable_verified_feed_count": len(feeds),
        "current_contract_supports_multi_feed_execution": False,
        "primary_selection_policy": FEED_PRIMARY_SELECTION_POLICY_VERSION,
        "selected_feed_verification_result_id": (
            execution.primary_feed_evidence.result.feed_verification_result_id
            if execution.primary_feed_evidence
            else None
        ),
        "alternate_verified_feed_result_ids": [
            item.result.feed_verification_result_id for item in execution.alternate_feed_evidence
        ],
        "usable_feed_evidence": [item.to_dict() for item in feeds],
        "contract_limitation": (
            "AcquisitionResolution represents one active V1 feed endpoint; alternate usable feeds are preserved as Phase 6D audit and handoff provenance."
            if execution.alternate_feed_evidence
            else None
        ),
    }


def _semantic_execution_payload(execution: AcquisitionResolutionExecution) -> dict[str, Any]:
    return execution.to_dict()


def _reject_duplicate_plans(plans: tuple[AcquisitionResolutionPlan, ...]) -> None:
    candidate_ids = [item.candidate_source_id for item in plans]
    duplicates = sorted({item for item in candidate_ids if candidate_ids.count(item) > 1})
    if duplicates:
        raise AcquisitionResolutionError(
            f"duplicate acquisition resolution plan candidate IDs: {', '.join(duplicates)}"
        )
