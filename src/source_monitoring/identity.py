from typing import Any

from src.database.planning_identity import canonical_json, hash_canonical_value
from src.models import TargetCareerPath
from src.source_monitoring.models import (
    ENTITY_TYPE_CANDIDATE_SCHEMA_VERSION,
    ENTITY_TYPE_EXPANSION_SCHEMA_VERSION,
    EntityTypeCandidate,
    ProposedEntityType,
    RejectedEntityTypeSuggestion,
    INFORMATION_NEED_SCHEMA_VERSION,
    InformationNeed,
    LLMExecutionMetadata,
    MonitoringObjectiveDefinition,
)


def build_information_need_id(
    *,
    objective_code: str,
    need_key: str,
    schema_version: str = INFORMATION_NEED_SCHEMA_VERSION,
) -> str:
    """
    Build a stable InformationNeed ID from code-owned semantic identity.
    """

    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "objective_code": objective_code,
            "need_key": need_key,
        }
    )

    return f"need_{digest[:16]}"


def build_information_need_input_fingerprint(
    *,
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    monitoring_objectives: tuple[MonitoringObjectiveDefinition, ...],
    llm_metadata: LLMExecutionMetadata,
    generation_limits: dict[str, Any],
    temperature: float,
) -> str:
    """
    Hash all meaningful Phase 0 generation inputs.
    """

    return hash_canonical_value(
        {
            "target_career_paths": target_career_paths,
            "user_preferences": user_preferences,
            "monitoring_objectives": monitoring_objectives,
            "provider": llm_metadata.provider,
            "model": llm_metadata.model,
            "prompt_version": llm_metadata.prompt_version,
            "schema_version": llm_metadata.schema_version,
            "generation_limits": generation_limits,
            "temperature": temperature,
        }
    )


def build_information_need_output_hash(
    information_needs: tuple[InformationNeed, ...],
) -> str:
    """
    Hash normalized, deterministically ordered InformationNeeds.
    """

    return hash_canonical_value(
        {
            "information_needs": information_needs,
        }
    )


def canonical_information_need_json(value: Any) -> str:
    return canonical_json(value)


def build_entity_type_candidate_id(
    *,
    entity_type_code: str,
    ontology_version: str,
    schema_version: str = ENTITY_TYPE_CANDIDATE_SCHEMA_VERSION,
) -> str:
    """
    Build a stable EntityTypeCandidate ID from ontology-controlled identity.
    """

    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "ontology_version": ontology_version,
            "entity_type_code": entity_type_code,
        }
    )

    return f"entity_type_{digest[:16]}"


def build_entity_type_expansion_input_fingerprint(
    *,
    information_needs: tuple[InformationNeed, ...],
    phase0_output_hash: str,
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    monitoring_objectives: tuple[MonitoringObjectiveDefinition, ...],
    ontology: tuple[Any, ...],
    llm_metadata: LLMExecutionMetadata,
    generation_limits: dict[str, Any],
    temperature: float,
) -> str:
    """
    Hash all meaningful Phase 1 generation inputs.
    """

    return hash_canonical_value(
        {
            "information_needs": sorted(
                information_needs,
                key=lambda need: need.information_need_id,
            ),
            "phase0_output_hash": phase0_output_hash,
            "target_career_paths": sorted(
                target_career_paths,
                key=lambda path: path.path_id,
            ),
            "user_preferences": user_preferences,
            "monitoring_objectives": monitoring_objectives,
            "entity_type_ontology": ontology,
            "provider": llm_metadata.provider,
            "model": llm_metadata.model,
            "prompt_version": llm_metadata.prompt_version,
            "schema_version": llm_metadata.schema_version,
            "generation_limits": generation_limits,
            "temperature": temperature,
        }
    )


def build_entity_type_expansion_output_hash(
    *,
    canonical_candidates: tuple[EntityTypeCandidate, ...],
    proposed_new_types: tuple[ProposedEntityType, ...],
    rejected_suggestions: tuple[RejectedEntityTypeSuggestion, ...],
    uncovered_information_need_ids: tuple[str, ...],
) -> str:
    """
    Hash normalized Phase 1 output, including audit-visible rejections.
    """

    return hash_canonical_value(
        {
            "schema_version": ENTITY_TYPE_EXPANSION_SCHEMA_VERSION,
            "canonical_candidates": canonical_candidates,
            "proposed_new_types": proposed_new_types,
            "rejected_suggestions": rejected_suggestions,
            "uncovered_information_need_ids": uncovered_information_need_ids,
        }
    )
