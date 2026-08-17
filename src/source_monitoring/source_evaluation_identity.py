from typing import Any

from src.database.planning_identity import hash_canonical_value
from src.source_monitoring.source_discovery_identity import normalize_source_url
from src.source_monitoring.source_evaluation_models import (
    FINAL_SOURCE_EVALUATION_SCHEMA_VERSION,
    INITIAL_SOURCE_EVALUATION_SCHEMA_VERSION,
    OBSERVED_SOURCE_EVIDENCE_SCHEMA_VERSION,
    SEMANTIC_TEXT_WINDOW_SCHEMA_VERSION,
    SOURCE_EVALUATION_PLAN_SCHEMA_VERSION,
    SOURCE_EVALUATION_RESULT_SCHEMA_VERSION,
    SOURCE_FETCH_EXECUTION_SCHEMA_VERSION,
    SOURCE_FETCH_REQUEST_SCHEMA_VERSION,
    SOURCE_INSPECTION_SCHEMA_VERSION,
    SOURCE_OBSERVATION_PLAN_SCHEMA_VERSION,
    SOURCE_SEMANTIC_BUNDLE_SCHEMA_VERSION,
    SourceRole,
)


def build_source_evaluation_plan_id(
    *,
    candidate_source_id: str,
    entity_id: str,
    candidate_url: str,
    planned_source_role: SourceRole,
    phase4_candidate_status: str,
    input_fingerprint: str,
    schema_version: str = SOURCE_EVALUATION_PLAN_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "candidate_source_id": candidate_source_id,
            "entity_id": entity_id,
            "candidate_url": normalize_source_url(candidate_url),
            "planned_source_role": planned_source_role.value,
            "phase4_candidate_status": phase4_candidate_status,
            "input_fingerprint": input_fingerprint,
        }
    )
    return f"source_eval_plan_{digest[:16]}"


def build_source_fetch_request_fingerprint(
    **payload: Any,
) -> str:
    return hash_canonical_value(
        {
            "schema_version": SOURCE_FETCH_REQUEST_SCHEMA_VERSION,
            **_normalized_url_payload(payload, "requested_url"),
        }
    )


def build_source_fetch_execution_id(
    *,
    source_evaluation_plan_id: str,
    candidate_source_id: str,
    request_fingerprint: str,
    final_url: str,
    fetch_status: str,
    raw_body_sha256: str | None = None,
    schema_version: str = SOURCE_FETCH_EXECUTION_SCHEMA_VERSION,
) -> str:
    """
    Build an execution-snapshot identity.

    Operational occurrence fields such as retrieved_at and elapsed time are
    intentionally excluded. A different body hash or terminal network status
    represents a different fetch snapshot.
    """

    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "source_evaluation_plan_id": source_evaluation_plan_id,
            "candidate_source_id": candidate_source_id,
            "request_fingerprint": request_fingerprint,
            "final_url": normalize_source_url(final_url),
            "fetch_status": fetch_status,
            "raw_body_sha256": raw_body_sha256,
        }
    )
    return f"source_fetch_exec_{digest[:16]}"


def build_source_inspection_id(
    *,
    fetch_execution_id: str,
    candidate_source_id: str,
    raw_body_sha256: str,
    inspection_input_fingerprint: str,
    schema_version: str = SOURCE_INSPECTION_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "fetch_execution_id": fetch_execution_id,
            "candidate_source_id": candidate_source_id,
            "raw_body_sha256": raw_body_sha256,
            "inspection_input_fingerprint": inspection_input_fingerprint,
        }
    )
    return f"source_inspection_{digest[:16]}"


def build_semantic_text_window_id(
    *,
    source_inspection_id: str,
    window_type: str,
    source_location: str,
    text: str,
    schema_version: str = SEMANTIC_TEXT_WINDOW_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "source_inspection_id": source_inspection_id,
            "window_type": window_type,
            "source_location": source_location,
            "text": text,
        }
    )
    return f"semantic_window_{digest[:16]}"


def build_source_semantic_evidence_bundle_id(
    *,
    source_inspection_id: str,
    candidate_source_id: str,
    entity_id: str,
    bundle_fingerprint: str,
    schema_version: str = SOURCE_SEMANTIC_BUNDLE_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "source_inspection_id": source_inspection_id,
            "candidate_source_id": candidate_source_id,
            "entity_id": entity_id,
            "bundle_fingerprint": bundle_fingerprint,
        }
    )
    return f"semantic_bundle_{digest[:16]}"


def build_source_semantic_bundle_fingerprint(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": SOURCE_SEMANTIC_BUNDLE_SCHEMA_VERSION,
            **payload,
        }
    )


def build_initial_source_evaluation_id(
    *,
    source_evaluation_plan_id: str,
    source_inspection_id: str,
    semantic_evidence_bundle_id: str,
    evaluator_policy_version: str,
    schema_version: str = INITIAL_SOURCE_EVALUATION_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "source_evaluation_plan_id": source_evaluation_plan_id,
            "source_inspection_id": source_inspection_id,
            "semantic_evidence_bundle_id": semantic_evidence_bundle_id,
            "evaluator_policy_version": evaluator_policy_version,
        }
    )
    return f"initial_source_eval_{digest[:16]}"


def build_source_observation_plan_id(
    *,
    candidate_source_id: str,
    initial_source_evaluation_id: str,
    sampling_strategy: str,
    max_item_count: int,
    input_fingerprint: str,
    schema_version: str = SOURCE_OBSERVATION_PLAN_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "candidate_source_id": candidate_source_id,
            "initial_source_evaluation_id": initial_source_evaluation_id,
            "sampling_strategy": sampling_strategy,
            "max_item_count": max_item_count,
            "input_fingerprint": input_fingerprint,
        }
    )
    return f"source_observation_plan_{digest[:16]}"


def build_observed_source_evidence_id(
    *,
    candidate_source_id: str,
    item_url: str,
    item_title: str,
    observation_plan_id: str,
    schema_version: str = OBSERVED_SOURCE_EVIDENCE_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "candidate_source_id": candidate_source_id,
            "item_url": normalize_source_url(item_url),
            "item_title": item_title.strip(),
            "observation_plan_id": observation_plan_id,
        }
    )
    return f"observed_source_evidence_{digest[:16]}"


def build_final_source_evaluation_id(
    *,
    initial_source_evaluation_id: str,
    candidate_source_id: str,
    observation_result_id: str | None,
    input_fingerprint: str,
    schema_version: str = FINAL_SOURCE_EVALUATION_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "initial_source_evaluation_id": initial_source_evaluation_id,
            "candidate_source_id": candidate_source_id,
            "observation_result_id": observation_result_id,
            "input_fingerprint": input_fingerprint,
        }
    )
    return f"final_source_eval_{digest[:16]}"


def build_source_evaluation_result_hash(**payload: Any) -> str:
    return hash_canonical_value(
        {
            "schema_version": SOURCE_EVALUATION_RESULT_SCHEMA_VERSION,
            **payload,
        }
    )


def _normalized_url_payload(payload: dict[str, Any], field_name: str) -> dict[str, Any]:
    normalized = dict(payload)
    if field_name in normalized:
        normalized[field_name] = normalize_source_url(normalized[field_name])
    return normalized
