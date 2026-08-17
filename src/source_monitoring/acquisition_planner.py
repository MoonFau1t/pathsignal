from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from src.config import PROJECT_ROOT
from src.database.planning_identity import hash_canonical_value
from src.source_monitoring.acquisition_identity import (
    build_acquisition_planning_output_hash,
    build_acquisition_resolution_plan_fingerprint,
    build_acquisition_resolution_plan_id,
    build_deferred_feed_candidate_id,
    build_feed_hint_evidence_ref_id,
    build_feed_verification_plan_fingerprint,
    build_feed_verification_plan_id,
    build_phase5_handoff_fingerprint,
    build_selected_website_resolution_plan_fingerprint,
    build_selected_website_resolution_plan_id,
)
from src.source_monitoring.acquisition_models import (
    AcquisitionPlanningResult,
    AcquisitionResolutionPlan,
    DeferredFeedCandidate,
    FeedHintEvidenceRef,
    FeedVerificationPlan,
    PlanStatus,
    SelectedWebsiteResolutionPlan,
)
from src.source_monitoring.source_discovery_identity import infer_source_format_hint, normalize_source_url
from src.source_monitoring.source_discovery_models import CandidateSource, SourceFormatHint, SourceRole
from src.source_monitoring.source_evaluation_models import FinalEvaluationDecision, FinalSourceEvaluation, SourceInspection


ACQUISITION_PLANNING_POLICY_VERSION = "acquisition_planning_policy_v1"
FEED_VERIFICATION_POLICY_VERSION = "feed_verification_policy_v1"
FEED_PARSER_POLICY_VERSION = "feed_parser_policy_v1"
SELECTED_WEBSITE_RESOLUTION_POLICY_VERSION = "selected_website_resolution_policy_v1"
SELECTED_WEBSITE_FALLBACK_POLICY_VERSION = "selected_website_fallback_policy_v1"
DEFAULT_MAX_FEED_CANDIDATES_PER_SOURCE = 3
ACQUISITION_RESOLUTION_PLANS_FILE = (
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "acquisition_resolution_plans.json"
)


class AcquisitionPlanningError(ValueError):
    pass


def plan_acquisition_resolution(
    *,
    phase5_canonical: dict[str, Any],
    phase6_handoff: dict[str, Any],
    candidates: tuple[CandidateSource, ...],
    inspections: tuple[SourceInspection, ...],
    source_observation_results: tuple[dict[str, Any], ...] = (),
    max_feed_candidates_per_source: int = DEFAULT_MAX_FEED_CANDIDATES_PER_SOURCE,
    planning_policy_version: str = ACQUISITION_PLANNING_POLICY_VERSION,
) -> AcquisitionPlanningResult:
    if max_feed_candidates_per_source <= 0:
        raise AcquisitionPlanningError("max_feed_candidates_per_source must be positive.")

    final_by_candidate = {
        item.candidate_source_id: item
        for item in (
            FinalSourceEvaluation.from_dict(payload)
            for payload in phase5_canonical.get("final_evaluations", [])
        )
    }
    candidates_by_id = {item.candidate_source_id: item for item in candidates}
    inspections_by_id = {item.candidate_source_id: item for item in inspections}
    observation_by_id = {
        str(item.get("source_observation_result_id")): dict(item)
        for item in source_observation_results
    }
    approved_sources = tuple(dict(item) for item in phase6_handoff.get("approved_sources", ()))
    phase5_handoff_input_hash = read_payload_hash(phase6_handoff)
    global_input_fingerprint = hash_canonical_value(
        {
            "planning_policy_version": planning_policy_version,
            "phase5_handoff_input_hash": phase5_handoff_input_hash,
            "approved_source_ids": [item["candidate_source_id"] for item in approved_sources],
            "max_feed_candidates_per_source": max_feed_candidates_per_source,
        }
    )

    acquisition_plans: list[AcquisitionResolutionPlan] = []
    feed_plans: list[FeedVerificationPlan] = []
    website_plans: list[SelectedWebsiteResolutionPlan] = []
    deferred: list[DeferredFeedCandidate] = []
    diagnostics: list[str] = []

    for handoff in sorted(approved_sources, key=lambda item: str(item["candidate_source_id"])):
        candidate_source_id = str(handoff["candidate_source_id"])
        final = final_by_candidate.get(candidate_source_id)
        if final is None:
            raise AcquisitionPlanningError(f"Phase 6 handoff references missing final evaluation: {candidate_source_id}")
        if final.final_decision != FinalEvaluationDecision.APPROVED_FOR_ACQUISITION:
            raise AcquisitionPlanningError(
                f"Phase 6 planning only accepts approved sources: {candidate_source_id}"
            )
        candidate = candidates_by_id.get(candidate_source_id)
        if candidate is None:
            raise AcquisitionPlanningError(f"missing CandidateSource for Phase 6 handoff: {candidate_source_id}")
        inspection = inspections_by_id.get(candidate_source_id)
        source_url = _source_url(candidate=candidate, inspection=inspection)
        source_observation_result = (
            observation_by_id.get(final.observation_result_id)
            if final.observation_result_id
            else None
        )
        final_fingerprint = hash_canonical_value(final.to_dict())
        handoff_fingerprint = build_phase5_handoff_fingerprint(
            {
                "candidate_source_id": handoff["candidate_source_id"],
                "entity_id": handoff["entity_id"],
                "final_source_evaluation_id": handoff["final_source_evaluation_id"],
                "observed_source_role": handoff["observed_source_role"],
                "supported_information_need_ids": tuple(handoff.get("supported_information_need_ids") or ()),
                "source_value": handoff.get("source_value"),
                "evaluation_confidence": handoff.get("evaluation_confidence"),
                "reason_codes": tuple(handoff.get("reason_codes") or ()),
                "final_fingerprint": final_fingerprint,
                "source_url": source_url,
            }
        )
        technical_flags = _technical_flags(inspection)
        feed_refs, feed_ref_diagnostics = _feed_hint_refs(
            inspection=inspection,
            source_url=source_url,
        )
        diagnostics.extend(feed_ref_diagnostics)
        grouped_refs = _dedup_feed_candidates(feed_refs)
        executable_groups = grouped_refs[:max_feed_candidates_per_source]
        deferred_groups = grouped_refs[max_feed_candidates_per_source:]

        plan_input = build_acquisition_resolution_plan_fingerprint(
            candidate_source_id=candidate_source_id,
            entity_id=final.entity_id,
            final_source_evaluation_id=final.final_source_evaluation_id,
            source_url=source_url,
            observed_source_role=handoff["observed_source_role"],
            supported_information_need_ids=tuple(handoff.get("supported_information_need_ids") or ()),
            final_source_evaluation_fingerprint=final_fingerprint,
            phase5_handoff_fingerprint=handoff_fingerprint,
            source_inspection_hash=inspection.inspection_output_hash if inspection else None,
            source_observation_result_hash=hash_canonical_value(source_observation_result) if source_observation_result else None,
            planning_policy_version=planning_policy_version,
            max_feed_candidates_per_source=max_feed_candidates_per_source,
        )
        acquisition_plan_id = build_acquisition_resolution_plan_id(
            candidate_source_id=candidate_source_id,
            final_source_evaluation_id=final.final_source_evaluation_id,
            source_url=source_url,
            observed_source_role=str(handoff["observed_source_role"]),
            phase5_handoff_fingerprint=handoff_fingerprint,
            planning_policy_version=planning_policy_version,
            input_fingerprint=plan_input,
        )
        for normalized_url, refs in executable_groups:
            feed_plans.append(
                _build_feed_plan(
                    acquisition_resolution_plan_id=acquisition_plan_id,
                    candidate_source_id=candidate_source_id,
                    final_source_evaluation_id=final.final_source_evaluation_id,
                    normalized_url=normalized_url,
                    refs=refs,
                )
            )
        for normalized_url, refs in deferred_groups:
            deferred.append(
                _build_deferred_feed_candidate(
                    acquisition_resolution_plan_id=acquisition_plan_id,
                    candidate_source_id=candidate_source_id,
                    normalized_url=normalized_url,
                    refs=refs,
                    deferral_reason="feed_candidate_budget_exceeded",
                )
            )
        website_plan = _build_website_plan(
            acquisition_resolution_plan_id=acquisition_plan_id,
            candidate_source_id=candidate_source_id,
            final_source_evaluation_id=final.final_source_evaluation_id,
            source_url=source_url,
            inspection=inspection,
            source_observation_result=source_observation_result,
            observed_source_role=SourceRole(str(handoff["observed_source_role"])),
        )
        website_plans.append(website_plan)
        acquisition_plans.append(
            AcquisitionResolutionPlan(
                acquisition_resolution_plan_id=acquisition_plan_id,
                candidate_source_id=candidate_source_id,
                entity_id=final.entity_id,
                final_source_evaluation_id=final.final_source_evaluation_id,
                source_url=source_url,
                observed_source_role=SourceRole(str(handoff["observed_source_role"])),
                supported_information_need_ids=tuple(str(item) for item in handoff.get("supported_information_need_ids") or ()),
                phase5_handoff_fingerprint=handoff_fingerprint,
                final_source_evaluation_fingerprint=final_fingerprint,
                source_inspection_id=inspection.inspection_id if inspection else None,
                source_inspection_hash=inspection.inspection_output_hash if inspection else None,
                source_observation_result_id=final.observation_result_id,
                source_observation_result_hash=hash_canonical_value(source_observation_result) if source_observation_result else None,
                known_technical_limitation_flags=technical_flags,
                strategy_order=("verify_known_feed_candidates", "selected_website_fallback", "phase6d_needs_review_or_unsupported"),
                feed_candidate_count=len(grouped_refs),
                executable_feed_verification_plan_count=len(executable_groups),
                deferred_feed_candidate_count=len(deferred_groups),
                selected_website_fallback_planned=True,
                dependency_model={
                    "feed_verification": {
                        "plan_ids": [
                            item.feed_verification_plan_id
                            for item in feed_plans
                            if item.acquisition_resolution_plan_id == acquisition_plan_id
                        ],
                        "status": PlanStatus.PLANNED.value,
                    },
                    "selected_website_fallback": {
                        "plan_id": website_plan.selected_website_resolution_plan_id,
                        "condition": "execute_if_no_verified_usable_feed",
                    },
                    "final_resolution": "phase6d_only",
                },
                planning_policy_version=planning_policy_version,
                input_fingerprint=plan_input,
            )
        )

    result_payload = {
        "acquisition_resolution_plans": [item.to_dict() for item in acquisition_plans],
        "feed_verification_plans": [item.to_dict() for item in feed_plans],
        "selected_website_resolution_plans": [item.to_dict() for item in website_plans],
        "deferred_feed_candidates": [item.to_dict() for item in deferred],
        "diagnostics": diagnostics,
        "phase5_handoff_input_hash": phase5_handoff_input_hash,
        "approved_input_count": len(approved_sources),
        "planning_policy_version": planning_policy_version,
        "input_fingerprint": global_input_fingerprint,
    }
    output_hash = build_acquisition_planning_output_hash(**result_payload)
    return AcquisitionPlanningResult(
        acquisition_resolution_plans=tuple(acquisition_plans),
        feed_verification_plans=tuple(feed_plans),
        selected_website_resolution_plans=tuple(website_plans),
        deferred_feed_candidates=tuple(deferred),
        diagnostics=tuple(sorted(diagnostics)),
        phase5_handoff_input_hash=phase5_handoff_input_hash,
        approved_input_count=len(approved_sources),
        planning_policy_version=planning_policy_version,
        input_fingerprint=global_input_fingerprint,
        output_hash=output_hash,
        generation={
            "generation_mode": "offline_deterministic_planning",
            "http_calls": 0,
            "brave_calls": 0,
            "deepseek_calls": 0,
            "browser_calls": 0,
            "new_external_request_count": 0,
        },
    )


def persist_acquisition_planning_result(
    *,
    result: AcquisitionPlanningResult,
    output_file: Path = ACQUISITION_RESOLUTION_PLANS_FILE,
) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    if output_file.exists() and output_file.read_text(encoding="utf-8") == text:
        return output_file
    output_file.write_text(text, encoding="utf-8")
    return output_file


def load_phase6a_corpus(base: Path | None = None) -> dict[str, Any]:
    root = base or PROJECT_ROOT / "outputs" / "planning" / "source_monitoring"
    phase5_canonical = read_json(root / "source_evaluations.json")
    phase6_handoff = read_json(root / "diagnostics" / "phase5_source_evaluation" / "phase6_source_handoff.json")
    discovery = read_json(root / "candidate_sources.json")
    candidates = tuple(
        CandidateSource.from_dict(item)
        for group in ("candidate_sources", "needs_review_candidates")
        for item in discovery.get(group, [])
    )
    inspections = []
    for path in sorted((root / "diagnostics" / "phase5_source_evaluation" / "inspections").glob("*/inspection.json")):
        payload = read_json(path)
        inspections.append(SourceInspection.from_dict(payload.get("inspection", payload)))
    observations = read_json(root / "diagnostics" / "phase5_source_evaluation" / "source_observations.json")
    return {
        "phase5_canonical": phase5_canonical,
        "phase6_handoff": phase6_handoff,
        "candidates": candidates,
        "inspections": tuple(inspections),
        "source_observation_results": tuple(dict(item) for item in observations.get("observation_results", [])),
    }


def run_phase6a_planning(
    *,
    write: bool = True,
    output_file: Path = ACQUISITION_RESOLUTION_PLANS_FILE,
) -> AcquisitionPlanningResult:
    corpus = load_phase6a_corpus()
    result = plan_acquisition_resolution(**corpus)
    if write:
        persist_acquisition_planning_result(result=result, output_file=output_file)
    return result


def read_payload_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_url(*, candidate: CandidateSource, inspection: SourceInspection | None) -> str:
    if candidate.normalized_url or candidate.canonical_url:
        return normalize_source_url(candidate.normalized_url or candidate.canonical_url)
    if inspection is not None:
        return normalize_source_url(inspection.final_url or inspection.requested_url or inspection.canonical_url)
    return ""


def _technical_flags(inspection: SourceInspection | None) -> tuple[str, ...]:
    if inspection is None:
        return ("source_inspection_missing",)
    flags = []
    if inspection.semantic_content_truncated:
        flags.append("semantic_content_truncated")
    if inspection.client_rendering_required_hint:
        flags.append("client_rendering_required_hint")
    if inspection.has_pagination_hints:
        flags.append("pagination_hints_present")
    if not inspection.feed_link_hints:
        flags.append("no_explicit_feed_hint_observed")
    if not (inspection.has_article_link_hints or inspection.has_report_link_hints or inspection.has_job_link_hints or inspection.has_event_link_hints):
        flags.append("no_detail_item_link_hints_observed")
    return tuple(sorted(flags))


def _feed_hint_refs(
    *,
    inspection: SourceInspection | None,
    source_url: str,
) -> tuple[tuple[FeedHintEvidenceRef, ...], tuple[str, ...]]:
    if inspection is None:
        return (), ("source_inspection_missing_for_feed_hints",)
    refs: list[FeedHintEvidenceRef] = []
    diagnostics: list[str] = []
    for index, hint in enumerate(inspection.feed_link_hints):
        resolved = _resolve_hint_url(source_url=source_url or inspection.final_url, href=hint.href)
        if not _is_http_url(resolved):
            diagnostics.append(f"invalid_feed_candidate_url:{inspection.candidate_source_id}:{index}")
            continue
        candidate_format = _feed_format_hint(href=resolved, mime_type=hint.mime_type)
        ref_id = build_feed_hint_evidence_ref_id(
            source_inspection_id=inspection.inspection_id,
            source_inspection_hash=inspection.inspection_output_hash,
            hint_index=index,
            normalized_url=resolved,
            rel=hint.rel,
            mime_type=hint.mime_type,
        )
        refs.append(
            FeedHintEvidenceRef(
                feed_hint_reference_id=ref_id,
                source_inspection_id=inspection.inspection_id,
                source_inspection_hash=inspection.inspection_output_hash,
                hint_index=index,
                href=hint.href,
                normalized_url=resolved,
                rel=hint.rel,
                mime_type=hint.mime_type,
                title=hint.title,
                candidate_format_hint=candidate_format,
                verification_status=hint.verification_status,
            )
        )
    return tuple(sorted(refs, key=lambda item: (item.normalized_url, item.hint_index))), tuple(sorted(diagnostics))


def _resolve_hint_url(*, source_url: str, href: str) -> str:
    text = str(href or "").strip()
    if not text:
        return ""
    parsed_hint = urlparse(text)
    if parsed_hint.scheme and parsed_hint.scheme not in {"http", "https"}:
        return ""
    if source_url:
        text = urljoin(source_url, text)
    try:
        return normalize_source_url(text)
    except ValueError:
        return ""


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _feed_format_hint(*, href: str, mime_type: str) -> SourceFormatHint:
    text = f"{href} {mime_type}".casefold()
    if "atom" in text:
        return SourceFormatHint.ATOM_CANDIDATE
    if "rss" in text or "xml" in text or "/feed" in text:
        return SourceFormatHint.RSS_CANDIDATE
    inferred = infer_source_format_hint(href)
    if inferred in {SourceFormatHint.RSS_CANDIDATE, SourceFormatHint.ATOM_CANDIDATE}:
        return inferred
    return SourceFormatHint.UNKNOWN


def _dedup_feed_candidates(refs: tuple[FeedHintEvidenceRef, ...]) -> tuple[tuple[str, tuple[FeedHintEvidenceRef, ...]], ...]:
    grouped: dict[str, list[FeedHintEvidenceRef]] = defaultdict(list)
    for ref in refs:
        grouped[ref.normalized_url].append(ref)
    return tuple(
        (url, tuple(sorted(items, key=lambda item: item.hint_index)))
        for url, items in sorted(grouped.items(), key=lambda item: item[0])
    )


def _build_feed_plan(
    *,
    acquisition_resolution_plan_id: str,
    candidate_source_id: str,
    final_source_evaluation_id: str,
    normalized_url: str,
    refs: tuple[FeedHintEvidenceRef, ...],
) -> FeedVerificationPlan:
    candidate_format = _combined_format_hint(refs)
    input_fingerprint = build_feed_verification_plan_fingerprint(
        acquisition_resolution_plan_id=acquisition_resolution_plan_id,
        candidate_source_id=candidate_source_id,
        final_source_evaluation_id=final_source_evaluation_id,
        feed_candidate_url=normalized_url,
        feed_hint_reference_ids=tuple(item.feed_hint_reference_id for item in refs),
        candidate_format_hint=candidate_format.value,
        verification_policy_version=FEED_VERIFICATION_POLICY_VERSION,
    )
    plan_id = build_feed_verification_plan_id(
        acquisition_resolution_plan_id=acquisition_resolution_plan_id,
        candidate_source_id=candidate_source_id,
        final_source_evaluation_id=final_source_evaluation_id,
        feed_candidate_url=normalized_url,
        feed_hint_reference_ids=tuple(item.feed_hint_reference_id for item in refs),
        verification_policy_version=FEED_VERIFICATION_POLICY_VERSION,
        input_fingerprint=input_fingerprint,
    )
    return FeedVerificationPlan(
        feed_verification_plan_id=plan_id,
        acquisition_resolution_plan_id=acquisition_resolution_plan_id,
        candidate_source_id=candidate_source_id,
        final_source_evaluation_id=final_source_evaluation_id,
        feed_candidate_url=normalized_url,
        feed_hint_evidence_refs=refs,
        candidate_format_hint=candidate_format,
        verification_policy_version=FEED_VERIFICATION_POLICY_VERSION,
        fetch_policy_ref={
            "method": "GET",
            "allowed_content_types": ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml"),
            "max_bytes": 1_000_000,
            "max_redirects": 5,
            "timeout_seconds": 20,
            "phase6a_executes_fetch": False,
        },
        parser_policy_version=FEED_PARSER_POLICY_VERSION,
        input_fingerprint=input_fingerprint,
    )


def _combined_format_hint(refs: tuple[FeedHintEvidenceRef, ...]) -> SourceFormatHint:
    hints = {item.candidate_format_hint for item in refs}
    if SourceFormatHint.ATOM_CANDIDATE in hints and SourceFormatHint.RSS_CANDIDATE not in hints:
        return SourceFormatHint.ATOM_CANDIDATE
    if SourceFormatHint.RSS_CANDIDATE in hints:
        return SourceFormatHint.RSS_CANDIDATE
    return SourceFormatHint.UNKNOWN


def _build_deferred_feed_candidate(
    *,
    acquisition_resolution_plan_id: str,
    candidate_source_id: str,
    normalized_url: str,
    refs: tuple[FeedHintEvidenceRef, ...],
    deferral_reason: str,
) -> DeferredFeedCandidate:
    deferred_id = build_deferred_feed_candidate_id(
        acquisition_resolution_plan_id=acquisition_resolution_plan_id,
        candidate_source_id=candidate_source_id,
        normalized_url=normalized_url,
        feed_hint_reference_ids=tuple(item.feed_hint_reference_id for item in refs),
        deferral_reason=deferral_reason,
    )
    return DeferredFeedCandidate(
        deferred_feed_candidate_id=deferred_id,
        acquisition_resolution_plan_id=acquisition_resolution_plan_id,
        candidate_source_id=candidate_source_id,
        normalized_url=normalized_url,
        feed_hint_evidence_refs=refs,
        deferral_reason=deferral_reason,
    )


def _build_website_plan(
    *,
    acquisition_resolution_plan_id: str,
    candidate_source_id: str,
    final_source_evaluation_id: str,
    source_url: str,
    inspection: SourceInspection | None,
    source_observation_result: dict[str, Any] | None,
    observed_source_role: SourceRole,
) -> SelectedWebsiteResolutionPlan:
    observation_hash = hash_canonical_value(source_observation_result) if source_observation_result else None
    evidence_refs = []
    if inspection is not None:
        evidence_refs.extend(
            [
                f"source_inspection:{inspection.inspection_id}",
                f"source_inspection_hash:{inspection.inspection_output_hash}",
            ]
        )
    if source_observation_result:
        evidence_refs.append(f"source_observation_result:{source_observation_result['source_observation_result_id']}")
    input_fingerprint = build_selected_website_resolution_plan_fingerprint(
        acquisition_resolution_plan_id=acquisition_resolution_plan_id,
        candidate_source_id=candidate_source_id,
        final_source_evaluation_id=final_source_evaluation_id,
        source_url=source_url,
        source_inspection_hash=inspection.inspection_output_hash if inspection else None,
        source_observation_result_hash=observation_hash,
        observed_source_role=observed_source_role.value,
        resolution_policy_version=SELECTED_WEBSITE_RESOLUTION_POLICY_VERSION,
    )
    plan_id = build_selected_website_resolution_plan_id(
        acquisition_resolution_plan_id=acquisition_resolution_plan_id,
        candidate_source_id=candidate_source_id,
        final_source_evaluation_id=final_source_evaluation_id,
        source_url=source_url,
        source_inspection_hash=inspection.inspection_output_hash if inspection else None,
        source_observation_result_hash=observation_hash,
        resolution_policy_version=SELECTED_WEBSITE_RESOLUTION_POLICY_VERSION,
        input_fingerprint=input_fingerprint,
    )
    return SelectedWebsiteResolutionPlan(
        selected_website_resolution_plan_id=plan_id,
        acquisition_resolution_plan_id=acquisition_resolution_plan_id,
        candidate_source_id=candidate_source_id,
        final_source_evaluation_id=final_source_evaluation_id,
        source_url=source_url,
        source_inspection_id=inspection.inspection_id if inspection else None,
        source_inspection_hash=inspection.inspection_output_hash if inspection else None,
        source_observation_result_id=source_observation_result.get("source_observation_result_id") if source_observation_result else None,
        source_observation_result_hash=observation_hash,
        observed_source_role=observed_source_role,
        evidence_input_refs=tuple(evidence_refs),
        execution_dependency={
            "after": "feed_verification_plans",
            "condition": "execute_if_no_verified_usable_feed",
            "fallback_policy_version": SELECTED_WEBSITE_FALLBACK_POLICY_VERSION,
        },
        resolution_policy_version=SELECTED_WEBSITE_RESOLUTION_POLICY_VERSION,
        input_fingerprint=input_fingerprint,
    )
