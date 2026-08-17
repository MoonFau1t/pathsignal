from __future__ import annotations

from typing import Any

from src.database.planning_identity import hash_canonical_value
from src.source_monitoring.acquisition_models import (
    ACQUISITION_PLANNING_RESULT_SCHEMA_VERSION,
    ACQUISITION_RESOLUTION_PLAN_SCHEMA_VERSION,
    ACQUISITION_RESOLUTION_SCHEMA_VERSION,
    DEFERRED_FEED_CANDIDATE_SCHEMA_VERSION,
    FEED_HINT_EVIDENCE_REF_SCHEMA_VERSION,
    FEED_VERIFICATION_PLAN_SCHEMA_VERSION,
    FEED_VERIFICATION_RESULT_SCHEMA_VERSION,
    PHASE7_MONITORING_HANDOFF_SCHEMA_VERSION,
    SELECTED_WEBSITE_RESOLUTION_PLAN_SCHEMA_VERSION,
    SELECTED_WEBSITE_RESOLUTION_RESULT_SCHEMA_VERSION,
    SELECTED_WEBSITE_ACQUISITION_CONFIG_SCHEMA_VERSION,
)
from src.source_monitoring.source_discovery_identity import normalize_source_url


def build_phase5_handoff_fingerprint(payload: Any) -> str:
    return hash_canonical_value({"schema": "phase5_handoff_for_phase6a_v1", "payload": payload})


def build_acquisition_resolution_plan_fingerprint(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": ACQUISITION_RESOLUTION_PLAN_SCHEMA_VERSION,
            **_normalize_url_fields(payload, ("source_url",)),
        }
    )


def build_acquisition_resolution_plan_id(
    *,
    candidate_source_id: str,
    final_source_evaluation_id: str,
    source_url: str,
    observed_source_role: str,
    phase5_handoff_fingerprint: str,
    planning_policy_version: str,
    input_fingerprint: str,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": ACQUISITION_RESOLUTION_PLAN_SCHEMA_VERSION,
            "candidate_source_id": candidate_source_id,
            "final_source_evaluation_id": final_source_evaluation_id,
            "source_url": normalize_source_url(source_url),
            "observed_source_role": observed_source_role,
            "phase5_handoff_fingerprint": phase5_handoff_fingerprint,
            "planning_policy_version": planning_policy_version,
            "input_fingerprint": input_fingerprint,
        }
    )
    return f"acquisition_resolution_plan_{digest[:16]}"


def build_feed_hint_evidence_ref_id(
    *,
    source_inspection_id: str,
    source_inspection_hash: str,
    hint_index: int,
    normalized_url: str,
    rel: str,
    mime_type: str,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": FEED_HINT_EVIDENCE_REF_SCHEMA_VERSION,
            "source_inspection_id": source_inspection_id,
            "source_inspection_hash": source_inspection_hash,
            "hint_index": hint_index,
            "normalized_url": normalize_source_url(normalized_url),
            "rel": rel,
            "mime_type": mime_type,
        }
    )
    return f"feed_hint_ref_{digest[:16]}"


def build_feed_verification_plan_fingerprint(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": FEED_VERIFICATION_PLAN_SCHEMA_VERSION,
            **_normalize_url_fields(payload, ("feed_candidate_url",)),
        }
    )


def build_feed_verification_plan_id(
    *,
    acquisition_resolution_plan_id: str,
    candidate_source_id: str,
    final_source_evaluation_id: str,
    feed_candidate_url: str,
    feed_hint_reference_ids: tuple[str, ...],
    verification_policy_version: str,
    input_fingerprint: str,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": FEED_VERIFICATION_PLAN_SCHEMA_VERSION,
            "acquisition_resolution_plan_id": acquisition_resolution_plan_id,
            "candidate_source_id": candidate_source_id,
            "final_source_evaluation_id": final_source_evaluation_id,
            "feed_candidate_url": normalize_source_url(feed_candidate_url),
            "feed_hint_reference_ids": tuple(sorted(feed_hint_reference_ids)),
            "verification_policy_version": verification_policy_version,
            "input_fingerprint": input_fingerprint,
        }
    )
    return f"feed_verification_plan_{digest[:16]}"


def build_deferred_feed_candidate_id(
    *,
    acquisition_resolution_plan_id: str,
    candidate_source_id: str,
    normalized_url: str,
    feed_hint_reference_ids: tuple[str, ...],
    deferral_reason: str,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": DEFERRED_FEED_CANDIDATE_SCHEMA_VERSION,
            "acquisition_resolution_plan_id": acquisition_resolution_plan_id,
            "candidate_source_id": candidate_source_id,
            "normalized_url": normalize_source_url(normalized_url),
            "feed_hint_reference_ids": tuple(sorted(feed_hint_reference_ids)),
            "deferral_reason": deferral_reason,
        }
    )
    return f"deferred_feed_candidate_{digest[:16]}"


def build_selected_website_resolution_plan_fingerprint(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": SELECTED_WEBSITE_RESOLUTION_PLAN_SCHEMA_VERSION,
            **_normalize_url_fields(payload, ("source_url",)),
        }
    )


def build_selected_website_resolution_plan_id(
    *,
    acquisition_resolution_plan_id: str,
    candidate_source_id: str,
    final_source_evaluation_id: str,
    source_url: str,
    source_inspection_hash: str | None,
    source_observation_result_hash: str | None,
    resolution_policy_version: str,
    input_fingerprint: str,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": SELECTED_WEBSITE_RESOLUTION_PLAN_SCHEMA_VERSION,
            "acquisition_resolution_plan_id": acquisition_resolution_plan_id,
            "candidate_source_id": candidate_source_id,
            "final_source_evaluation_id": final_source_evaluation_id,
            "source_url": normalize_source_url(source_url),
            "source_inspection_hash": source_inspection_hash,
            "source_observation_result_hash": source_observation_result_hash,
            "resolution_policy_version": resolution_policy_version,
            "input_fingerprint": input_fingerprint,
        }
    )
    return f"selected_website_plan_{digest[:16]}"


def build_feed_verification_result_id(
    *,
    feed_verification_plan_id: str,
    candidate_source_id: str,
    feed_candidate_url: str,
    input_fingerprint: str,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": FEED_VERIFICATION_RESULT_SCHEMA_VERSION,
            "feed_verification_plan_id": feed_verification_plan_id,
            "candidate_source_id": candidate_source_id,
            "feed_candidate_url": normalize_source_url(feed_candidate_url),
            "input_fingerprint": input_fingerprint,
        }
    )
    return f"feed_verification_result_{digest[:16]}"


def build_selected_website_resolution_result_id(
    *,
    selected_website_resolution_plan_id: str,
    candidate_source_id: str,
    source_url: str,
    input_fingerprint: str,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": SELECTED_WEBSITE_RESOLUTION_RESULT_SCHEMA_VERSION,
            "selected_website_resolution_plan_id": selected_website_resolution_plan_id,
            "candidate_source_id": candidate_source_id,
            "source_url": normalize_source_url(source_url),
            "input_fingerprint": input_fingerprint,
        }
    )
    return f"selected_website_result_{digest[:16]}"


def build_selected_website_resolution_result_fingerprint(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": SELECTED_WEBSITE_RESOLUTION_RESULT_SCHEMA_VERSION,
            **_normalize_url_fields(payload, ("source_url", "current_final_url")),
        }
    )


def build_selected_website_acquisition_config_fingerprint(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": SELECTED_WEBSITE_ACQUISITION_CONFIG_SCHEMA_VERSION,
            **_normalize_url_fields(payload, ("source_url",)),
        }
    )


def build_selected_website_acquisition_config_id(
    *,
    selected_website_resolution_plan_id: str,
    candidate_source_id: str,
    source_url: str,
    input_fingerprint: str,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": SELECTED_WEBSITE_ACQUISITION_CONFIG_SCHEMA_VERSION,
            "selected_website_resolution_plan_id": selected_website_resolution_plan_id,
            "candidate_source_id": candidate_source_id,
            "source_url": normalize_source_url(source_url),
            "input_fingerprint": input_fingerprint,
        }
    )
    return f"selected_website_config_{digest[:16]}"


def build_selected_website_resolution_output_hash(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": "phase6c_selected_website_resolution_result_set_v1",
            **payload,
        }
    )


def build_acquisition_resolution_id(
    *,
    acquisition_resolution_plan_id: str,
    candidate_source_id: str,
    final_source_evaluation_id: str,
    resolution_status: str,
    acquisition_method: str | None,
    input_fingerprint: str,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": ACQUISITION_RESOLUTION_SCHEMA_VERSION,
            "acquisition_resolution_plan_id": acquisition_resolution_plan_id,
            "candidate_source_id": candidate_source_id,
            "final_source_evaluation_id": final_source_evaluation_id,
            "resolution_status": resolution_status,
            "acquisition_method": acquisition_method,
            "input_fingerprint": input_fingerprint,
        }
    )
    return f"acquisition_resolution_{digest[:16]}"


def build_acquisition_resolution_fingerprint(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": ACQUISITION_RESOLUTION_SCHEMA_VERSION,
            **_normalize_url_fields(
                payload,
                ("source_url", "primary_feed_url", "primary_feed_final_url"),
            ),
        }
    )


def build_final_acquisition_resolution_output_hash(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": "phase6d_final_acquisition_resolution_result_set_v1",
            **payload,
        }
    )


def build_phase7_monitoring_handoff_id(
    *,
    acquisition_method: str,
    acquisition_target_url: str,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": PHASE7_MONITORING_HANDOFF_SCHEMA_VERSION,
            "acquisition_method": acquisition_method,
            "acquisition_target_url": normalize_source_url(
                acquisition_target_url
            ),
        }
    )
    return f"phase7_monitoring_handoff_{digest[:16]}"


def build_acquisition_planning_output_hash(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": ACQUISITION_PLANNING_RESULT_SCHEMA_VERSION,
            **payload,
        }
    )


def build_feed_verification_result_fingerprint(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": FEED_VERIFICATION_RESULT_SCHEMA_VERSION,
            **_normalize_url_fields(payload, ("feed_candidate_url", "final_url")),
        }
    )


def build_feed_verification_output_hash(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": "phase6b_feed_verification_result_set_v1",
            **payload,
        }
    )


def _normalize_url_fields(payload: dict[str, Any], field_names: tuple[str, ...]) -> dict[str, Any]:
    normalized = dict(payload)
    for field_name in field_names:
        if field_name in normalized:
            normalized[field_name] = normalize_source_url(normalized[field_name])
    return normalized
