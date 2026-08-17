from dataclasses import replace
from pathlib import Path
from typing import Any

from src.config import (
    BRAVE_API_KEY,
    ENTITY_DISCOVERY_CACHE_ENABLED,
    ENTITY_DISCOVERY_EXTRACTION_MODEL,
    ENTITY_DISCOVERY_EXTRACTION_MAX_EVIDENCE_PER_BATCH,
    ENTITY_DISCOVERY_EXTRACTION_TEMPERATURE,
    ENTITY_DISCOVERY_LANGUAGES,
    ENTITY_DISCOVERY_MAX_ENTITIES_PER_TYPE,
    ENTITY_DISCOVERY_MAX_PLANS,
    ENTITY_DISCOVERY_MAX_QUERIES_PER_TYPE,
    ENTITY_DISCOVERY_PLANNING_MODEL,
    ENTITY_DISCOVERY_PLANNING_TEMPERATURE,
    ENTITY_DISCOVERY_REGIONS,
    ENTITY_DISCOVERY_RESULTS_PER_PLAN,
    ENTITY_UNIVERSE_FILE,
    LLM_BASE_URL,
    LLM_PROVIDER,
)
from src.models import TargetCareerPath
from src.source_monitoring.cache import (
    load_cached_entity_universe_result,
    save_entity_universe_result,
)
from src.source_monitoring.entity_candidate_extractor import (
    EntityCandidateExtractionClient,
    extract_entity_candidates,
)
from src.source_monitoring.entity_classifier import classify_entity_candidate
from src.source_monitoring.entity_discovery_executor import (
    execute_entity_discovery_plans,
)
from src.source_monitoring.entity_discovery_models import (
    ENTITY_DISCOVERY_PLANNING_PROMPT_VERSION,
    ENTITY_EXTRACTION_PROMPT_VERSION,
    EntityCandidate,
    EntityCandidateVerificationStatus,
    EntityDiscoveryEvidence,
    EntityDiscoveryPlan,
    EntityUniverseExecutionMetadata,
    EntityUniverseResult,
    RejectedEntityCandidate,
    UnresolvedIdentityConflict,
)
from src.source_monitoring.entity_discovery_planner import (
    EntityDiscoveryPlanningClient,
    plan_entity_discovery,
)
from src.source_monitoring.entity_identity import (
    build_entity_universe_input_fingerprint,
    build_entity_universe_output_hash,
    resolve_entity_identities,
)
from src.source_monitoring.models import EntityTypeExpansionResult, InformationNeed


def build_entity_universe(
    *,
    entity_type_expansion_result: EntityTypeExpansionResult,
    information_needs: tuple[InformationNeed, ...],
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    force_refresh: bool = False,
    planning_client: EntityDiscoveryPlanningClient | None = None,
    search_client: Any | None = None,
    extraction_client: EntityCandidateExtractionClient | None = None,
    cache_enabled: bool = ENTITY_DISCOVERY_CACHE_ENABLED,
    cache_file: Path = ENTITY_UNIVERSE_FILE,
    languages: tuple[str, ...] = ENTITY_DISCOVERY_LANGUAGES,
    regions: tuple[str, ...] = ENTITY_DISCOVERY_REGIONS,
    max_plans: int = ENTITY_DISCOVERY_MAX_PLANS,
    max_results_per_plan: int = ENTITY_DISCOVERY_RESULTS_PER_PLAN,
    max_queries_per_type: int = ENTITY_DISCOVERY_MAX_QUERIES_PER_TYPE,
    max_entities_per_type: int = ENTITY_DISCOVERY_MAX_ENTITIES_PER_TYPE,
    planning_model: str | None = None,
    planning_provider: str | None = None,
    planning_temperature: float = ENTITY_DISCOVERY_PLANNING_TEMPERATURE,
    extraction_model: str | None = None,
    extraction_provider: str | None = None,
    extraction_temperature: float = ENTITY_DISCOVERY_EXTRACTION_TEMPERATURE,
    phase1_output_hash: str | None = None,
) -> EntityUniverseResult:
    """
    Build the Phase 2 concrete Source Monitoring Entity Universe.
    """

    selected_planning_provider = planning_provider or (
        planning_client.provider if planning_client is not None else LLM_PROVIDER
    )
    selected_planning_model = planning_model or (
        planning_client.model
        if planning_client is not None
        else ENTITY_DISCOVERY_PLANNING_MODEL
    )
    selected_extraction_provider = extraction_provider or (
        extraction_client.provider if extraction_client is not None else LLM_PROVIDER
    )
    selected_extraction_model = extraction_model or (
        extraction_client.model
        if extraction_client is not None
        else ENTITY_DISCOVERY_EXTRACTION_MODEL
    )
    provider_configuration = {
        "planning_provider": selected_planning_provider,
        "planning_model": selected_planning_model,
        "planning_prompt_version": ENTITY_DISCOVERY_PLANNING_PROMPT_VERSION,
        "planning_temperature": planning_temperature,
        "extraction_provider": selected_extraction_provider,
        "extraction_model": selected_extraction_model,
        "extraction_prompt_version": ENTITY_EXTRACTION_PROMPT_VERSION,
        "extraction_temperature": extraction_temperature,
        "search_provider": "brave",
        "brave_configured": bool(BRAVE_API_KEY),
    }
    generation_limits = {
        "languages": languages,
        "regions": regions,
        "max_plans": max_plans,
        "max_results_per_plan": max_results_per_plan,
        "max_queries_per_type": max_queries_per_type,
        "max_entities_per_type": max_entities_per_type,
        "max_evidence_per_extraction_batch": (
            ENTITY_DISCOVERY_EXTRACTION_MAX_EVIDENCE_PER_BATCH
        ),
    }
    phase1_result = (
        replace(entity_type_expansion_result, output_hash=phase1_output_hash)
        if phase1_output_hash is not None
        else entity_type_expansion_result
    )
    input_fingerprint = build_entity_universe_input_fingerprint(
        entity_type_expansion_result=phase1_result,
        information_needs=information_needs,
        target_career_paths=target_career_paths,
        user_preferences=user_preferences,
        provider_configuration=provider_configuration,
        generation_limits=generation_limits,
    )
    metadata = EntityUniverseExecutionMetadata(
        planning_provider=selected_planning_provider,
        planning_model=selected_planning_model,
        planning_prompt_version=ENTITY_DISCOVERY_PLANNING_PROMPT_VERSION,
        extraction_provider=selected_extraction_provider,
        extraction_model=selected_extraction_model,
        extraction_prompt_version=ENTITY_EXTRACTION_PROMPT_VERSION,
        search_provider="brave",
        input_fingerprint=input_fingerprint,
    )
    cache_diagnostics: tuple[str, ...] = ()

    if cache_enabled and not force_refresh:
        cached_result, cache_diagnostics = load_cached_entity_universe_result(
            cache_file=cache_file,
            input_fingerprint=input_fingerprint,
        )
        if cached_result is not None:
            return cached_result

    diagnostics: list[str] = list(cache_diagnostics)
    try:
        plans, planning_diagnostics = plan_entity_discovery(
            entity_type_candidates=entity_type_expansion_result.canonical_candidates,
            information_needs=information_needs,
            target_career_paths=target_career_paths,
            user_preferences=user_preferences,
            client=planning_client,
            languages=languages,
            regions=regions,
            max_plans=max_plans,
            max_results_per_plan=max_results_per_plan,
            max_queries_per_type=max_queries_per_type,
            model=selected_planning_model,
            provider=selected_planning_provider,
            temperature=planning_temperature,
        )
        diagnostics.extend(planning_diagnostics)
    except Exception as error:
        diagnostics.append(
            f"EntityDiscovery planning unavailable: {type(error).__name__}: {error}"
        )
        return _build_entity_universe_result(
            entity_discovery_plans=(),
            entity_discovery_evidence=(),
            entity_candidates=(),
            rejected_candidates=(),
            unresolved_identity_conflicts=(),
            uncovered_entity_type_candidate_ids=tuple(
                sorted(
                    candidate.candidate_id
                    for candidate in entity_type_expansion_result.canonical_candidates
                )
            ),
            diagnostics=tuple(diagnostics),
            execution_metadata=metadata,
            input_fingerprint=input_fingerprint,
            generation_mode="unavailable",
            cache_enabled=cache_enabled,
            cache_file=cache_file,
        )

    evidence, execution_diagnostics = execute_entity_discovery_plans(
        plans=plans,
        search_client=search_client,
        search_provider="brave",
    )
    diagnostics.extend(execution_diagnostics)
    planning_complete = _planning_is_complete(
        entity_type_candidates=entity_type_expansion_result.canonical_candidates,
        plans=plans,
        languages=languages,
    )
    if not planning_complete:
        diagnostics.append(
            "EntityDiscovery planning incomplete: not all selected "
            "EntityTypeCandidates have validated plans for every configured "
            "language."
        )

    try:
        extracted, rejected, extraction_diagnostics = extract_entity_candidates(
            entity_discovery_evidence=evidence,
            entity_discovery_plans=plans,
            entity_type_candidates=entity_type_expansion_result.canonical_candidates,
            client=extraction_client,
            max_entities_per_type=max_entities_per_type,
            max_evidence_per_batch=ENTITY_DISCOVERY_EXTRACTION_MAX_EVIDENCE_PER_BATCH,
            model=selected_extraction_model,
            provider=selected_extraction_provider,
            temperature=extraction_temperature,
        )
        diagnostics.extend(extraction_diagnostics)
        extraction_failed = False
    except Exception as error:
        extracted = ()
        rejected = ()
        extraction_failed = True
        diagnostics.append(
            f"EntityCandidate extraction unavailable: {type(error).__name__}: {error}"
        )

    classified = tuple(classify_entity_candidate(candidate) for candidate in extracted)
    deduplicated, conflicts = resolve_entity_identities(classified)
    if conflicts:
        deduplicated = tuple(
            _mark_conflicted(candidate, conflicts)
            for candidate in deduplicated
        )

    uncovered_ids = _uncovered_entity_type_candidate_ids(
        entity_type_candidates=entity_type_expansion_result.canonical_candidates,
        entity_candidates=deduplicated,
        plans=plans,
    )
    generation_mode = _generation_mode_for_run(
        plans=plans,
        evidence=evidence,
        extracted=deduplicated,
        execution_diagnostics=execution_diagnostics,
        planning_complete=planning_complete,
        extraction_failed=extraction_failed,
    )
    result = _build_entity_universe_result(
        entity_discovery_plans=plans,
        entity_discovery_evidence=evidence,
        entity_candidates=deduplicated,
        rejected_candidates=rejected,
        unresolved_identity_conflicts=conflicts,
        uncovered_entity_type_candidate_ids=uncovered_ids,
        diagnostics=tuple(diagnostics),
        execution_metadata=metadata,
        input_fingerprint=input_fingerprint,
        generation_mode=generation_mode,
        cache_enabled=cache_enabled,
        cache_file=cache_file,
    )
    return result


def _build_entity_universe_result(
    *,
    entity_discovery_plans: tuple[EntityDiscoveryPlan, ...],
    entity_discovery_evidence: tuple[EntityDiscoveryEvidence, ...],
    entity_candidates: tuple[EntityCandidate, ...],
    rejected_candidates: tuple[RejectedEntityCandidate, ...],
    unresolved_identity_conflicts: tuple[UnresolvedIdentityConflict, ...],
    uncovered_entity_type_candidate_ids: tuple[str, ...],
    diagnostics: tuple[str, ...],
    execution_metadata: EntityUniverseExecutionMetadata,
    input_fingerprint: str,
    generation_mode: str,
    cache_enabled: bool,
    cache_file: Path,
) -> EntityUniverseResult:
    result = EntityUniverseResult(
        entity_discovery_plans=tuple(
            sorted(entity_discovery_plans, key=lambda item: item.plan_id)
        ),
        entity_discovery_evidence=tuple(
            sorted(entity_discovery_evidence, key=lambda item: item.evidence_id)
        ),
        entity_candidates=tuple(sorted(entity_candidates, key=lambda item: item.entity_id)),
        rejected_candidates=tuple(rejected_candidates),
        unresolved_identity_conflicts=tuple(
            sorted(unresolved_identity_conflicts, key=lambda item: item.conflict_id)
        ),
        uncovered_entity_type_candidate_ids=tuple(
            sorted(uncovered_entity_type_candidate_ids)
        ),
        diagnostics=tuple(diagnostics),
        execution_metadata=execution_metadata,
        input_fingerprint=input_fingerprint,
        output_hash=build_entity_universe_output_hash(
            entity_discovery_plans=tuple(entity_discovery_plans),
            entity_discovery_evidence=tuple(entity_discovery_evidence),
            entity_candidates=tuple(entity_candidates),
            rejected_candidates=tuple(rejected_candidates),
            unresolved_identity_conflicts=tuple(unresolved_identity_conflicts),
            uncovered_entity_type_candidate_ids=tuple(
                uncovered_entity_type_candidate_ids
            ),
        ),
        generation_mode=generation_mode,
    )

    if cache_enabled and generation_mode == "generated":
        save_entity_universe_result(cache_file, result)

    return result


def _uncovered_entity_type_candidate_ids(
    *,
    entity_type_candidates,
    entity_candidates: tuple[EntityCandidate, ...],
    plans: tuple[EntityDiscoveryPlan, ...],
) -> tuple[str, ...]:
    produced_ids = {
        candidate_id
        for entity in entity_candidates
        for candidate_id in entity.related_entity_type_candidate_ids
    }
    return tuple(
        sorted(
            candidate.candidate_id
            for candidate in entity_type_candidates
            if candidate.candidate_id not in produced_ids
        )
    )


def _generation_mode_for_run(
    *,
    plans: tuple[EntityDiscoveryPlan, ...],
    evidence: tuple[EntityDiscoveryEvidence, ...],
    extracted: tuple[EntityCandidate, ...],
    execution_diagnostics: tuple[str, ...],
    planning_complete: bool,
    extraction_failed: bool,
) -> str:
    if not planning_complete:
        return "partial"
    if not plans or not evidence:
        return "unavailable"
    if extraction_failed:
        return "unavailable"
    if execution_diagnostics:
        return "partial"
    if not extracted:
        return "partial"
    return "generated"


def _planning_is_complete(
    *,
    entity_type_candidates,
    plans: tuple[EntityDiscoveryPlan, ...],
    languages: tuple[str, ...],
) -> bool:
    language_set = set(languages)
    planned_languages_by_candidate: dict[str, set[str]] = {}
    for plan in plans:
        planned_languages_by_candidate.setdefault(
            plan.entity_type_candidate_id,
            set(),
        ).add(plan.language)

    return all(
        planned_languages_by_candidate.get(candidate.candidate_id, set())
        >= language_set
        for candidate in entity_type_candidates
    )


def _mark_conflicted(
    candidate: EntityCandidate,
    conflicts: tuple[UnresolvedIdentityConflict, ...],
) -> EntityCandidate:
    conflicted_ids = {
        candidate_id
        for conflict in conflicts
        for candidate_id in conflict.candidate_entity_ids
    }
    if candidate.entity_id not in conflicted_ids:
        return candidate

    return replace(
        candidate,
        verification_status=EntityCandidateVerificationStatus.CONFLICTED,
    )
