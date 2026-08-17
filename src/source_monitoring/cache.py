from pathlib import Path
from typing import Any

from src.storage import load_json, save_json
from src.source_monitoring.models import (
    ENTITY_TYPE_EXPANSION_SCHEMA_VERSION,
    EntityTypeCandidate,
    EntityTypeDefinition,
    EntityTypeExpansionResult,
    InformationNeed,
    InformationNeedGenerationResult,
    InformationNeedPriority,
    LLMExecutionMetadata,
    MonitoringObjectiveCode,
    MonitoringObjectiveDefinition,
    ProposedEntityType,
    RejectedEntityTypeSuggestion,
    RejectedInformationNeedSuggestion,
)
from src.source_monitoring.entity_discovery_models import (
    ENTITY_UNIVERSE_RESULT_SCHEMA_VERSION,
    EntityUniverseResult,
)
from src.source_monitoring.entity_prioritization_models import (
    ENTITY_PRIORITIZATION_RESULT_SCHEMA_VERSION,
    EntityPrioritizationResult,
)
from src.source_monitoring.source_discovery_models import (
    SOURCE_DISCOVERY_PLANNING_RESULT_SCHEMA_VERSION,
    SOURCE_DISCOVERY_RESULT_SCHEMA_VERSION,
    SourceDiscoveryPlanningResult,
    SourceDiscoveryResult,
)


def load_cached_information_need_result(
    cache_file: Path,
    input_fingerprint: str,
) -> tuple[InformationNeedGenerationResult | None, tuple[str, ...]]:
    """
    Load a cached Phase 0 result only when its fingerprint matches.
    """

    if not cache_file.exists():
        return None, ()

    diagnostics: list[str] = []

    try:
        payload = load_json(cache_file)
    except Exception as error:
        return None, (f"InformationNeed cache could not be read: {error}",)

    if not isinstance(payload, dict):
        return None, ("InformationNeed cache payload is not an object.",)

    if payload.get("input_fingerprint") != input_fingerprint:
        return None, ()

    try:
        result = _generation_result_from_dict(payload)
    except Exception as error:
        diagnostics.append(
            "InformationNeed cache was ignored because it was malformed: "
            f"{error}"
        )
        return None, tuple(diagnostics)

    return (
        InformationNeedGenerationResult(
            monitoring_objectives=result.monitoring_objectives,
            information_needs=result.information_needs,
            rejected_suggestions=result.rejected_suggestions,
            diagnostics=result.diagnostics,
            llm_execution_metadata=result.llm_execution_metadata,
            input_fingerprint=result.input_fingerprint,
            output_hash=result.output_hash,
            schema_version=result.schema_version,
            generation_mode="loaded_from_cache",
        ),
        tuple(diagnostics),
    )


def save_information_need_result(
    cache_file: Path,
    result: InformationNeedGenerationResult,
) -> Path:
    return save_json(result, cache_file)


def load_cached_entity_type_expansion_result(
    cache_file: Path,
    input_fingerprint: str,
    ontology_version: str,
) -> tuple[EntityTypeExpansionResult | None, tuple[str, ...]]:
    """
    Load a cached Phase 1 result only when fingerprint and ontology match.
    """

    if not cache_file.exists():
        return None, ()

    try:
        payload = load_json(cache_file)
    except Exception as error:
        return None, (f"EntityTypeExpansion cache could not be read: {error}",)

    if not isinstance(payload, dict):
        return None, ("EntityTypeExpansion cache payload is not an object.",)

    if payload.get("input_fingerprint") != input_fingerprint:
        return None, ()

    if payload.get("schema_version") != ENTITY_TYPE_EXPANSION_SCHEMA_VERSION:
        return None, ("EntityTypeExpansion cache schema version is incompatible.",)

    ontology_payload = payload.get("canonical_entity_types", [])
    if not isinstance(ontology_payload, list):
        return None, ("EntityTypeExpansion cache ontology snapshot is invalid.",)

    if any(
        isinstance(item, dict)
        and item.get("ontology_version") != ontology_version
        for item in ontology_payload
    ):
        return None, ("EntityTypeExpansion cache ontology version is incompatible.",)

    try:
        result = _entity_type_expansion_result_from_dict(payload)
    except Exception as error:
        return None, (
            "EntityTypeExpansion cache was ignored because it was malformed: "
            f"{error}",
        )

    return (
        EntityTypeExpansionResult(
            canonical_entity_types=result.canonical_entity_types,
            canonical_candidates=result.canonical_candidates,
            proposed_new_types=result.proposed_new_types,
            rejected_suggestions=result.rejected_suggestions,
            uncovered_information_need_ids=result.uncovered_information_need_ids,
            diagnostics=result.diagnostics,
            llm_execution_metadata=result.llm_execution_metadata,
            input_fingerprint=result.input_fingerprint,
            output_hash=result.output_hash,
            schema_version=result.schema_version,
            generation_mode="loaded_from_cache",
        ),
        (),
    )


def save_entity_type_expansion_result(
    cache_file: Path,
    result: EntityTypeExpansionResult,
) -> Path:
    return save_json(result, cache_file)


def load_cached_entity_universe_result(
    cache_file: Path,
    input_fingerprint: str,
) -> tuple[EntityUniverseResult | None, tuple[str, ...]]:
    """
    Load a cached Phase 2 EntityUniverseResult only when fingerprint matches.
    """

    if not cache_file.exists():
        return None, ()

    try:
        payload = load_json(cache_file)
    except Exception as error:
        return None, (f"EntityUniverse cache could not be read: {error}",)

    if not isinstance(payload, dict):
        return None, ("EntityUniverse cache payload is not an object.",)

    if payload.get("input_fingerprint") != input_fingerprint:
        return None, ()

    if payload.get("schema_version") != ENTITY_UNIVERSE_RESULT_SCHEMA_VERSION:
        return None, ("EntityUniverse cache schema version is incompatible.",)

    try:
        result = EntityUniverseResult.from_dict(payload)
    except Exception as error:
        return None, (
            "EntityUniverse cache was ignored because it was malformed: "
            f"{error}",
        )

    return (
        EntityUniverseResult(
            entity_discovery_plans=result.entity_discovery_plans,
            entity_discovery_evidence=result.entity_discovery_evidence,
            entity_candidates=result.entity_candidates,
            rejected_candidates=result.rejected_candidates,
            unresolved_identity_conflicts=result.unresolved_identity_conflicts,
            uncovered_entity_type_candidate_ids=(
                result.uncovered_entity_type_candidate_ids
            ),
            diagnostics=result.diagnostics,
            execution_metadata=result.execution_metadata,
            input_fingerprint=result.input_fingerprint,
            output_hash=result.output_hash,
            schema_version=result.schema_version,
            generation_mode="loaded_from_cache",
        ),
        (),
    )


def save_entity_universe_result(
    cache_file: Path,
    result: EntityUniverseResult,
) -> Path:
    return save_json(result, cache_file)


def load_cached_entity_prioritization_result(
    cache_file: Path,
    input_fingerprint: str,
) -> tuple[EntityPrioritizationResult | None, tuple[str, ...]]:
    """
    Load a cached Phase 3 EntityPrioritizationResult only when fingerprint matches.
    """

    if not cache_file.exists():
        return None, ()

    try:
        payload = load_json(cache_file)
    except Exception as error:
        return None, (
            "EntityPrioritization cache could not be read or was malformed: "
            f"{error}",
        )

    if not isinstance(payload, dict):
        return None, ("EntityPrioritization cache payload is not an object.",)

    if payload.get("input_fingerprint") != input_fingerprint:
        return None, ()

    if payload.get("schema_version") != ENTITY_PRIORITIZATION_RESULT_SCHEMA_VERSION:
        return None, ("EntityPrioritization cache schema version is incompatible.",)

    try:
        result = EntityPrioritizationResult.from_dict(payload)
    except Exception as error:
        return None, (
            "EntityPrioritization cache was ignored because it was malformed: "
            f"{error}",
        )

    return (
        EntityPrioritizationResult(
            priority_assessments=result.priority_assessments,
            rejected_assessments=result.rejected_assessments,
            unassessed_entity_ids=result.unassessed_entity_ids,
            diagnostics=result.diagnostics,
            execution_metadata=result.execution_metadata,
            scoring_policy_version=result.scoring_policy_version,
            compact_context_policy_version=result.compact_context_policy_version,
            prompt_version=result.prompt_version,
            input_fingerprint=result.input_fingerprint,
            output_hash=result.output_hash,
            schema_version=result.schema_version,
            generation_mode="loaded_from_cache",
        ),
        (),
    )


def save_entity_prioritization_result(
    cache_file: Path,
    result: EntityPrioritizationResult,
) -> Path:
    return save_json(result, cache_file)


def load_cached_source_discovery_planning_result(
    cache_file: Path,
    input_fingerprint: str,
) -> tuple[SourceDiscoveryPlanningResult | None, tuple[str, ...]]:
    """
    Load a complete Phase 4 planning result when its fingerprint still matches.
    """

    if not cache_file.exists():
        return None, ()

    try:
        payload = load_json(cache_file)
    except Exception as error:
        return None, (f"SourceDiscoveryPlanning cache could not be read: {error}",)

    if not isinstance(payload, dict):
        return None, ("SourceDiscoveryPlanning cache payload is not an object.",)

    if payload.get("input_fingerprint") != input_fingerprint:
        return None, ()

    if payload.get("schema_version") != SOURCE_DISCOVERY_PLANNING_RESULT_SCHEMA_VERSION:
        return None, (
            "SourceDiscoveryPlanning cache schema version is incompatible.",
        )

    try:
        result = SourceDiscoveryPlanningResult.from_dict(payload)
    except Exception as error:
        return None, (
            "SourceDiscoveryPlanning cache was ignored because it was malformed: "
            f"{error}",
        )

    if result.generation_mode != "generated":
        return None, ("SourceDiscoveryPlanning cache is not complete.",)

    return (
        SourceDiscoveryPlanningResult(
            planning_result_hash=result.planning_result_hash,
            budgets=result.budgets,
            plans=result.plans,
            executable_plan_ids=result.executable_plan_ids,
            deferred_plan_ids=result.deferred_plan_ids,
            deferred_entity_ids=result.deferred_entity_ids,
            diagnostics=result.diagnostics,
            role_ontology_version=result.role_ontology_version,
            entity_kind_role_policy_version=result.entity_kind_role_policy_version,
            budget_policy_version=result.budget_policy_version,
            plan_ranking_policy_version=result.plan_ranking_policy_version,
            query_template_version=result.query_template_version,
            input_fingerprint=result.input_fingerprint,
            output_hash=result.output_hash,
            schema_version=result.schema_version,
            generation_mode="loaded_from_cache",
        ),
        (),
    )


def save_source_discovery_planning_result(
    cache_file: Path,
    result: SourceDiscoveryPlanningResult,
) -> Path:
    return save_json(result, cache_file)


def load_cached_source_discovery_result(
    cache_file: Path,
    input_fingerprint: str,
) -> tuple[SourceDiscoveryResult | None, tuple[str, ...]]:
    """
    Load a complete Phase 4 candidate-source result when compatible.
    """

    if not cache_file.exists():
        return None, ()

    try:
        payload = load_json(cache_file)
    except Exception as error:
        return None, (f"SourceDiscovery cache could not be read: {error}",)

    if not isinstance(payload, dict):
        return None, ("SourceDiscovery cache payload is not an object.",)

    if payload.get("input_fingerprint") != input_fingerprint:
        return None, ()

    if payload.get("schema_version") != SOURCE_DISCOVERY_RESULT_SCHEMA_VERSION:
        return None, ("SourceDiscovery cache schema version is incompatible.",)

    try:
        result = SourceDiscoveryResult.from_dict(payload)
    except Exception as error:
        return None, (
            "SourceDiscovery cache was ignored because it was malformed: "
            f"{error}",
        )

    if result.generation_mode != "complete":
        return None, ("SourceDiscovery cache is not complete.",)

    return (
        SourceDiscoveryResult(
            planning_result_hash=result.planning_result_hash,
            budgets=result.budgets,
            plans=result.plans,
            executions=result.executions,
            evidence=result.evidence,
            candidate_sources=result.candidate_sources,
            rejected_candidates=result.rejected_candidates,
            needs_review_candidates=result.needs_review_candidates,
            deferred_entity_ids=result.deferred_entity_ids,
            deferred_plan_ids=result.deferred_plan_ids,
            failed_execution_ids=result.failed_execution_ids,
            diagnostics=result.diagnostics,
            execution_metadata=result.execution_metadata,
            role_ontology_version=result.role_ontology_version,
            budget_policy_version=result.budget_policy_version,
            plan_ranking_policy_version=result.plan_ranking_policy_version,
            query_template_version=result.query_template_version,
            classifier_prompt_version=result.classifier_prompt_version,
            url_normalization_policy_version=(
                result.url_normalization_policy_version
            ),
            preclassification_policy_version=(
                result.preclassification_policy_version
            ),
            input_fingerprint=result.input_fingerprint,
            output_hash=result.output_hash,
            generation_mode="loaded_from_cache",
            schema_version=result.schema_version,
        ),
        (),
    )


def save_source_discovery_result(
    cache_file: Path,
    result: SourceDiscoveryResult,
) -> Path:
    return save_json(result, cache_file)


def _generation_result_from_dict(
    payload: dict[str, Any],
) -> InformationNeedGenerationResult:
    metadata_payload = payload.get("llm_execution_metadata", {})

    if not isinstance(metadata_payload, dict):
        metadata_payload = {}

    return InformationNeedGenerationResult(
        monitoring_objectives=tuple(
            _objective_from_dict(item)
            for item in _list(payload.get("monitoring_objectives"))
        ),
        information_needs=tuple(
            _need_from_dict(item)
            for item in _list(payload.get("information_needs"))
        ),
        rejected_suggestions=tuple(
            _rejected_from_dict(item)
            for item in _list(payload.get("rejected_suggestions"))
        ),
        diagnostics=tuple(str(item) for item in _list(payload.get("diagnostics"))),
        llm_execution_metadata=LLMExecutionMetadata(
            provider=metadata_payload.get("provider"),
            model=metadata_payload.get("model"),
            prompt_version=metadata_payload.get("prompt_version"),
            schema_version=str(
                metadata_payload.get("schema_version", "information_need_v1")
            ),
            input_fingerprint=metadata_payload.get("input_fingerprint"),
        ),
        input_fingerprint=str(payload["input_fingerprint"]),
        output_hash=str(payload["output_hash"]),
        schema_version=str(
            payload.get("schema_version", "information_need_generation_v1")
        ),
        generation_mode=str(payload.get("generation_mode", "loaded_from_cache")),
    )


def _objective_from_dict(payload: Any) -> MonitoringObjectiveDefinition:
    if not isinstance(payload, dict):
        raise ValueError("monitoring objective cache item must be an object")

    return MonitoringObjectiveDefinition(
        code=MonitoringObjectiveCode(str(payload["code"])),
        label=str(payload["label"]),
        description=str(payload["description"]),
        supported_signal_examples=tuple(
            str(item)
            for item in _list(payload.get("supported_signal_examples"))
        ),
        schema_version=str(
            payload.get("schema_version", "monitoring_objective_v1")
        ),
    )


def _need_from_dict(payload: Any) -> InformationNeed:
    if not isinstance(payload, dict):
        raise ValueError("information need cache item must be an object")

    return InformationNeed(
        information_need_id=str(payload["information_need_id"]),
        need_key=str(payload["need_key"]),
        objective_code=MonitoringObjectiveCode(str(payload["objective_code"])),
        title=str(payload["title"]),
        description=str(payload["description"]),
        related_target_career_path_ids=tuple(
            str(item)
            for item in _list(payload.get("related_target_career_path_ids"))
        ),
        signal_examples=tuple(
            str(item)
            for item in _list(payload.get("signal_examples"))
        ),
        rationale=str(payload["rationale"]),
        priority=InformationNeedPriority(str(payload["priority"])),
        confidence=float(payload["confidence"]),
        provenance=(
            dict(payload.get("provenance"))
            if isinstance(payload.get("provenance"), dict)
            else {}
        ),
        schema_version=str(payload.get("schema_version", "information_need_v1")),
    )


def _rejected_from_dict(payload: Any) -> RejectedInformationNeedSuggestion:
    if not isinstance(payload, dict):
        raise ValueError("rejected suggestion cache item must be an object")

    suggestion = payload.get("suggestion", {})

    return RejectedInformationNeedSuggestion(
        suggestion=suggestion if isinstance(suggestion, dict) else {},
        reason=str(payload.get("reason", "")),
        diagnostics=tuple(
            str(item)
            for item in _list(payload.get("diagnostics"))
        ),
    )


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value

    return []


def _entity_type_expansion_result_from_dict(
    payload: dict[str, Any],
) -> EntityTypeExpansionResult:
    metadata_payload = payload.get("llm_execution_metadata", {})

    if not isinstance(metadata_payload, dict):
        metadata_payload = {}

    return EntityTypeExpansionResult(
        canonical_entity_types=tuple(
            _entity_type_definition_from_dict(item)
            for item in _list(payload.get("canonical_entity_types"))
        ),
        canonical_candidates=tuple(
            _entity_type_candidate_from_dict(item)
            for item in _list(payload.get("canonical_candidates"))
        ),
        proposed_new_types=tuple(
            _proposed_entity_type_from_dict(item)
            for item in _list(payload.get("proposed_new_types"))
        ),
        rejected_suggestions=tuple(
            _rejected_entity_type_from_dict(item)
            for item in _list(payload.get("rejected_suggestions"))
        ),
        uncovered_information_need_ids=tuple(
            str(item)
            for item in _list(payload.get("uncovered_information_need_ids"))
        ),
        diagnostics=tuple(str(item) for item in _list(payload.get("diagnostics"))),
        llm_execution_metadata=LLMExecutionMetadata(
            provider=metadata_payload.get("provider"),
            model=metadata_payload.get("model"),
            prompt_version=metadata_payload.get("prompt_version"),
            schema_version=str(metadata_payload.get("schema_version", "")),
            input_fingerprint=metadata_payload.get("input_fingerprint"),
        ),
        input_fingerprint=str(payload["input_fingerprint"]),
        output_hash=str(payload["output_hash"]),
        schema_version=str(payload["schema_version"]),
        generation_mode=str(payload.get("generation_mode", "loaded_from_cache")),
    )


def _entity_type_definition_from_dict(payload: Any) -> EntityTypeDefinition:
    if not isinstance(payload, dict):
        raise ValueError("entity type definition cache item must be an object")

    return EntityTypeDefinition(
        code=str(payload["code"]),
        display_name=str(payload["display_name"]),
        definition=str(payload["definition"]),
        broader_group=str(payload["broader_group"]),
        aliases=tuple(str(item) for item in _list(payload.get("aliases"))),
        example_signal_domains=tuple(
            str(item)
            for item in _list(payload.get("example_signal_domains"))
        ),
        ontology_version=str(payload["ontology_version"]),
        schema_version=str(payload.get("schema_version", "entity_type_definition_v1")),
    )


def _entity_type_candidate_from_dict(payload: Any) -> EntityTypeCandidate:
    if not isinstance(payload, dict):
        raise ValueError("entity type candidate cache item must be an object")

    return EntityTypeCandidate(
        candidate_id=str(payload["candidate_id"]),
        entity_type_code=str(payload["entity_type_code"]),
        display_name=str(payload["display_name"]),
        related_information_need_ids=tuple(
            str(item)
            for item in _list(payload.get("related_information_need_ids"))
        ),
        related_target_career_path_ids=tuple(
            str(item)
            for item in _list(payload.get("related_target_career_path_ids"))
        ),
        supported_monitoring_objectives=tuple(
            MonitoringObjectiveCode(str(item))
            for item in _list(payload.get("supported_monitoring_objectives"))
        ),
        rationale=str(payload["rationale"]),
        discovery_terms=tuple(
            str(item)
            for item in _list(payload.get("discovery_terms"))
        ),
        confidence=float(payload["confidence"]),
        provenance=(
            dict(payload.get("provenance"))
            if isinstance(payload.get("provenance"), dict)
            else {}
        ),
        schema_version=str(payload.get("schema_version", "entity_type_candidate_v1")),
    )


def _proposed_entity_type_from_dict(payload: Any) -> ProposedEntityType:
    if not isinstance(payload, dict):
        raise ValueError("proposed entity type cache item must be an object")

    return ProposedEntityType(
        proposed_code=str(payload["proposed_code"]),
        display_name=str(payload["display_name"]),
        definition=str(payload["definition"]),
        broader_group=str(payload["broader_group"]),
        supporting_information_need_ids=tuple(
            str(item)
            for item in _list(payload.get("supporting_information_need_ids"))
        ),
        related_target_career_path_ids=tuple(
            str(item)
            for item in _list(payload.get("related_target_career_path_ids"))
        ),
        closest_canonical_type_codes=tuple(
            str(item)
            for item in _list(payload.get("closest_canonical_type_codes"))
        ),
        why_canonical_types_are_insufficient=str(
            payload["why_canonical_types_are_insufficient"]
        ),
        rationale=str(payload["rationale"]),
        confidence=float(payload["confidence"]),
        provenance=(
            dict(payload.get("provenance"))
            if isinstance(payload.get("provenance"), dict)
            else {}
        ),
        schema_version=str(payload.get("schema_version", "proposed_entity_type_v1")),
    )


def _rejected_entity_type_from_dict(payload: Any) -> RejectedEntityTypeSuggestion:
    if not isinstance(payload, dict):
        raise ValueError("rejected entity type cache item must be an object")

    suggestion = payload.get("suggestion", {})
    source_index = payload.get("source_suggestion_index")

    return RejectedEntityTypeSuggestion(
        suggestion=suggestion if isinstance(suggestion, dict) else {},
        reason=str(payload.get("reason", "")),
        diagnostics=tuple(
            str(item)
            for item in _list(payload.get("diagnostics"))
        ),
        source_suggestion_index=(
            int(source_index)
            if isinstance(source_index, int)
            else None
        ),
    )
