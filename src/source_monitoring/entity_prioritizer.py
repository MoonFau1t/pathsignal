import json
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.config import (
    ENTITY_PRIORITIES_FILE,
    ENTITY_PRIORITIZATION_BATCH_SIZE,
    ENTITY_PRIORITIZATION_CACHE_ENABLED,
    ENTITY_PRIORITIZATION_MAX_EVIDENCE_PER_ENTITY,
    ENTITY_PRIORITIZATION_MODEL,
    ENTITY_PRIORITIZATION_TEMPERATURE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
)
from src.database.planning_identity import hash_canonical_value
from src.storage import save_json
from src.models import TargetCareerPath
from src.source_monitoring.cache import (
    load_cached_entity_prioritization_result,
    save_entity_prioritization_result,
)
from src.source_monitoring.entity_discovery_models import (
    EntityCandidate,
    EntityUniverseResult,
)
from src.source_monitoring.entity_identity import normalize_organization_name
from src.source_monitoring.entity_prioritization_models import (
    COMPACT_CONTEXT_POLICY_VERSION,
    ENTITY_PRIORITIZATION_PROMPT_VERSION,
    ENTITY_PRIORITIZATION_RESULT_SCHEMA_VERSION,
    ENTITY_PRIORITY_SCORING_POLICY_VERSION,
    EVIDENCE_READINESS_POLICY_VERSION,
    PRIORITY_TIER_POLICY_VERSION,
    SEMANTIC_SCORE_SCALE_VERSION,
    EntityPrioritizationExecutionMetadata,
    EntityPrioritizationResult,
    EntityPriorityAssessment,
    EntitySemanticAssessment,
    PriorityTier,
    RejectedEntityPriorityAssessment,
    SemanticAssessmentStatus,
    SemanticDimensionAssessment,
)
from src.source_monitoring.entity_priority_context import (
    build_compact_entity_contexts,
    consolidate_entity_universe_for_prioritization,
)
from src.source_monitoring.entity_priority_policy import (
    assign_ranks_and_tiers,
    calculate_entity_priority_score,
    calculate_evidence_readiness_assessment,
    calculate_geography_assessment,
    evidence_readiness_policy_snapshot,
    scoring_policy_snapshot,
    tier_policy_snapshot,
)
from src.source_monitoring.models import InformationNeed
from src.source_monitoring.prompts import build_entity_prioritization_prompt


class EntityPrioritizationError(Exception):
    """
    Raised when Phase 3 cannot use structured prioritization output.
    """


class EntityPrioritizationClient:
    """
    Dedicated OpenAI-compatible client for Phase 3 semantic prioritization.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = ENTITY_PRIORITIZATION_TEMPERATURE,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature

        if not self.api_key or self.api_key.startswith("your_"):
            raise EntityPrioritizationError(
                "LLM_API_KEY is missing. Add a real DeepSeek-compatible key "
                "before running EntityPrioritization."
            )

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate(
        self,
        *,
        compact_entity_contexts: tuple[dict[str, Any], ...],
        information_needs: tuple[InformationNeed, ...],
        target_career_paths: list[TargetCareerPath],
        user_preferences: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = build_entity_prioritization_prompt(
            compact_entity_contexts=compact_entity_contexts,
            information_needs=information_needs,
            target_career_paths=target_career_paths,
            user_preferences=user_preferences,
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Assess Source Monitoring Phase 3 entity semantic "
                        "priority. Return only valid JSON. Do not approve "
                        "sources, discover RSS feeds, or assign final tiers."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=self.temperature,
            stream=False,
        )
        response_text = _extract_llm_response_text(response)
        if response_text is None or not response_text.strip():
            raise EntityPrioritizationError(
                "LLM returned an empty EntityPrioritization response."
            )
        try:
            parsed = json.loads(_normalize_json_response_text(response_text))
        except json.JSONDecodeError as error:
            raise EntityPrioritizationError(
                "EntityPrioritization returned invalid JSON after cleanup."
            ) from error
        if not isinstance(parsed, dict):
            raise EntityPrioritizationError(
                "EntityPrioritization response must be a JSON object."
            )
        return parsed


def prioritize_entities(
    *,
    entity_universe_result: EntityUniverseResult,
    information_needs: tuple[InformationNeed, ...],
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    force_refresh: bool = False,
    client: EntityPrioritizationClient | None = None,
    cache_enabled: bool = ENTITY_PRIORITIZATION_CACHE_ENABLED,
    cache_file: Path = ENTITY_PRIORITIES_FILE,
    batch_size: int = ENTITY_PRIORITIZATION_BATCH_SIZE,
    max_evidence_per_entity: int = ENTITY_PRIORITIZATION_MAX_EVIDENCE_PER_ENTITY,
    model: str | None = None,
    provider: str | None = None,
    temperature: float = ENTITY_PRIORITIZATION_TEMPERATURE,
    batch_checkpoint_dir: Path | None = None,
) -> EntityPrioritizationResult:
    entity_universe_result, consolidation_diagnostics = (
        consolidate_entity_universe_for_prioritization(
            entity_universe_result=entity_universe_result,
        )
    )
    selected_provider = provider or (client.provider if client is not None else LLM_PROVIDER)
    selected_model = model or (
        client.model if client is not None else ENTITY_PRIORITIZATION_MODEL
    )
    compact_contexts = build_compact_entity_contexts(
        entity_universe_result=entity_universe_result,
        max_evidence_per_entity=max_evidence_per_entity,
    )
    provider_configuration = {
        "provider": selected_provider,
        "model": selected_model,
        "prompt_version": ENTITY_PRIORITIZATION_PROMPT_VERSION,
        "temperature": temperature,
    }
    generation_limits = {
        "batch_size": batch_size,
        "max_evidence_per_entity": max_evidence_per_entity,
    }
    input_fingerprint = build_entity_prioritization_input_fingerprint(
        entity_universe_result=entity_universe_result,
        compact_entity_contexts=compact_contexts,
        information_needs=information_needs,
        target_career_paths=target_career_paths,
        user_preferences=user_preferences,
        provider_configuration=provider_configuration,
        generation_limits=generation_limits,
    )
    metadata = EntityPrioritizationExecutionMetadata(
        provider=selected_provider,
        model=selected_model,
        prompt_version=ENTITY_PRIORITIZATION_PROMPT_VERSION,
        input_fingerprint=input_fingerprint,
    )

    if cache_enabled and not force_refresh:
        cached_result, cache_diagnostics = load_cached_entity_prioritization_result(
            cache_file=cache_file,
            input_fingerprint=input_fingerprint,
        )
        if cached_result is not None:
            return cached_result
    else:
        cache_diagnostics = ()

    diagnostics: list[str] = list(consolidation_diagnostics) + list(cache_diagnostics)
    try:
        prioritization_client = client or EntityPrioritizationClient(
            provider=selected_provider,
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=selected_model,
            temperature=temperature,
        )
        semantic_assessments, rejected, parse_diagnostics = (
            generate_semantic_assessments(
                compact_entity_contexts=compact_contexts,
                information_needs=information_needs,
                target_career_paths=target_career_paths,
                user_preferences=user_preferences,
                client=prioritization_client,
                batch_size=batch_size,
                batch_checkpoint_dir=batch_checkpoint_dir,
            )
        )
        diagnostics.extend(parse_diagnostics)
    except Exception as error:
        diagnostics.append(
            f"EntityPrioritization unavailable: {type(error).__name__}: {error}"
        )
        return _build_prioritization_result(
            priority_assessments=(),
            rejected_assessments=(),
            unassessed_entity_ids=tuple(
                sorted(entity.entity_id for entity in entity_universe_result.entity_candidates)
            ),
            diagnostics=tuple(diagnostics),
            execution_metadata=metadata,
            input_fingerprint=input_fingerprint,
            generation_mode="unavailable",
            cache_enabled=cache_enabled,
            cache_file=cache_file,
        )

    assessments = build_priority_assessments(
        semantic_assessments=semantic_assessments,
        entity_universe_result=entity_universe_result,
        user_preferences=user_preferences,
    )
    assessed_entity_ids = {item.entity_id for item in assessments}
    rejected_entity_ids = {item.entity_id for item in rejected if item.entity_id}
    all_entity_ids = {
        item.entity_id for item in entity_universe_result.entity_candidates
    }
    unassessed_entity_ids = tuple(
        sorted(all_entity_ids - assessed_entity_ids - rejected_entity_ids)
    )
    if unassessed_entity_ids:
        diagnostics.append(
            "EntityPrioritization incomplete: at least one Phase 2 entity was "
            "not assessed or rejected."
        )
    generation_mode = (
        "generated"
        if assessments and not unassessed_entity_ids
        else "partial"
    )
    return _build_prioritization_result(
        priority_assessments=assessments,
        rejected_assessments=rejected,
        unassessed_entity_ids=unassessed_entity_ids,
        diagnostics=tuple(diagnostics),
        execution_metadata=metadata,
        input_fingerprint=input_fingerprint,
        generation_mode=generation_mode,
        cache_enabled=cache_enabled,
        cache_file=cache_file,
    )


def generate_semantic_assessments(
    *,
    compact_entity_contexts: tuple[dict[str, Any], ...],
    information_needs: tuple[InformationNeed, ...],
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    client: EntityPrioritizationClient,
    batch_size: int,
    batch_checkpoint_dir: Path | None = None,
) -> tuple[
    tuple[EntitySemanticAssessment, ...],
    tuple[RejectedEntityPriorityAssessment, ...],
    tuple[str, ...],
]:
    suggestions: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    batches = tuple(
        compact_entity_contexts[index:index + max(1, batch_size)]
        for index in range(0, len(compact_entity_contexts), max(1, batch_size))
    )
    diagnostics.append(
        "EntityPrioritization batches: "
        + ", ".join(str(len(batch)) for batch in batches)
    )
    for batch_index, batch in enumerate(batches):
        parsed = client.generate(
            compact_entity_contexts=batch,
            information_needs=information_needs,
            target_career_paths=target_career_paths,
            user_preferences=user_preferences,
        )
        if batch_checkpoint_dir is not None:
            batch_checkpoint_dir.mkdir(parents=True, exist_ok=True)
            save_json(
                parsed,
                batch_checkpoint_dir / f"entity_priority_batch_{batch_index + 1}.json",
            )
        raw, parse_diagnostics = parse_entity_prioritization_response(parsed)
        suggestions.extend(raw)
        diagnostics.extend(
            f"batch {batch_index}: {diagnostic}"
            for diagnostic in parse_diagnostics
        )

    valid_entity_ids = tuple(str(item["entity_id"]) for item in compact_entity_contexts)
    semantic, rejected, validation_diagnostics = (
        validate_entity_semantic_assessment_suggestions(
            suggestions=suggestions,
            entity_contexts=compact_entity_contexts,
            information_needs=information_needs,
            valid_entity_ids=valid_entity_ids,
        )
    )
    return semantic, rejected, tuple(diagnostics) + validation_diagnostics


def parse_entity_prioritization_response(
    parsed: Any,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    if not isinstance(parsed, dict):
        return [], ("EntityPrioritization response must be a JSON object.",)
    diagnostics = tuple(
        f"Unexpected prioritization top-level field rejected: {key}"
        for key in sorted(set(parsed) - {"entity_semantic_assessments"})
    )
    raw = parsed.get("entity_semantic_assessments")
    if not isinstance(raw, list):
        return [], diagnostics + (
            "EntityPrioritization response must contain entity_semantic_assessments list.",
        )
    suggestions: list[dict[str, Any]] = []
    item_diagnostics = list(diagnostics)
    for index, item in enumerate(raw):
        if isinstance(item, dict):
            suggestions.append(item)
        else:
            item_diagnostics.append(
                f"Semantic assessment at index {index} rejected: item must be an object."
            )
    return suggestions, tuple(item_diagnostics)


def validate_entity_semantic_assessment_suggestions(
    *,
    suggestions: list[dict[str, Any]],
    entity_contexts: tuple[dict[str, Any], ...],
    information_needs: tuple[InformationNeed, ...],
    valid_entity_ids: tuple[str, ...],
) -> tuple[
    tuple[EntitySemanticAssessment, ...],
    tuple[RejectedEntityPriorityAssessment, ...],
    tuple[str, ...],
]:
    valid_entity_id_set = set(valid_entity_ids)
    context_by_entity_id = {
        str(item["entity_id"]): item
        for item in entity_contexts
    }
    valid_need_ids = {item.information_need_id for item in information_needs}
    seen_entity_ids: set[str] = set()
    accepted: list[EntitySemanticAssessment] = []
    rejected: list[RejectedEntityPriorityAssessment] = []
    diagnostics: list[str] = []
    allowed_fields = {
        "entity_id",
        "path_relevance",
        "stage_relevance",
        "expected_signal_potential",
        "strategic_importance",
        "short_overall_rationale",
    }

    for index, suggestion in enumerate(suggestions):
        errors: list[str] = []
        extra_fields = sorted(set(suggestion) - allowed_fields)
        if extra_fields:
            errors.append(f"unsupported fields: {extra_fields}")
        entity_id = str(suggestion.get("entity_id", "")).strip()
        if entity_id not in valid_entity_id_set:
            errors.append("unknown entity_id")
        if entity_id in seen_entity_ids:
            errors.append("duplicate entity assessment")
        seen_entity_ids.add(entity_id)
        if _contains_forbidden_source_claim(suggestion):
            errors.append(
                "assessment makes source-level or observed-signal claims reserved for later phases"
            )

        context = context_by_entity_id.get(entity_id, {})
        related_need_ids = set(context.get("related_information_need_ids", []))
        dimensions: dict[str, SemanticDimensionAssessment] = {}
        for field_name in (
            "path_relevance",
            "stage_relevance",
            "expected_signal_potential",
            "strategic_importance",
        ):
            dimension, dimension_errors = _dimension_from_payload(
                suggestion.get(field_name),
                field_name=field_name,
                valid_need_ids=valid_need_ids,
                related_need_ids=related_need_ids,
            )
            dimensions[field_name] = dimension
            errors.extend(dimension_errors)

        overall = str(suggestion.get("short_overall_rationale", "")).strip()
        if not overall:
            errors.append("short_overall_rationale is required")
        if len(overall) > 600:
            errors.append("short_overall_rationale is too long")

        if errors:
            rejected.append(
                RejectedEntityPriorityAssessment(
                    entity_id=entity_id,
                    original_assessment=dict(suggestion),
                    rejection_reason="; ".join(sorted(set(errors))),
                    diagnostics=tuple(sorted(set(errors))),
                    source_assessment_index=index,
                )
            )
            continue

        accepted.append(
            EntitySemanticAssessment(
                entity_id=entity_id,
                path_relevance=dimensions["path_relevance"],
                stage_relevance=dimensions["stage_relevance"],
                expected_signal_potential=dimensions["expected_signal_potential"],
                strategic_importance=dimensions["strategic_importance"],
                short_overall_rationale=overall,
                source_assessment_index=index,
            )
        )

    return tuple(sorted(accepted, key=lambda item: item.entity_id)), tuple(rejected), tuple(diagnostics)


def build_priority_assessments(
    *,
    semantic_assessments: tuple[EntitySemanticAssessment, ...],
    entity_universe_result: EntityUniverseResult,
    user_preferences: dict[str, Any],
) -> tuple[EntityPriorityAssessment, ...]:
    entity_by_id = {
        item.entity_id: item
        for item in entity_universe_result.entity_candidates
    }
    preliminary: list[EntityPriorityAssessment] = []
    for semantic in semantic_assessments:
        entity = entity_by_id[semantic.entity_id]
        geography = calculate_geography_assessment(
            entity=entity,
            user_preferences=user_preferences,
        )
        readiness = calculate_evidence_readiness_assessment(
            entity=entity,
            entity_universe_result=entity_universe_result,
        )
        priority_score, weights_used = calculate_entity_priority_score(
            path_relevance=semantic.path_relevance,
            geography_relevance=geography,
            stage_relevance=semantic.stage_relevance,
            expected_signal_potential=semantic.expected_signal_potential,
            strategic_importance=semantic.strategic_importance,
        )
        review_flags = tuple(
            sorted(
                set(
                    semantic.path_relevance.review_flags
                    + semantic.stage_relevance.review_flags
                    + semantic.expected_signal_potential.review_flags
                    + semantic.strategic_importance.review_flags
                    + _readiness_review_flags(readiness)
                )
            )
        )
        preliminary.append(
            EntityPriorityAssessment(
                priority_assessment_id=build_priority_assessment_id(
                    entity_id=entity.entity_id,
                    scoring_policy_version=ENTITY_PRIORITY_SCORING_POLICY_VERSION,
                ),
                entity_id=entity.entity_id,
                canonical_name=entity.canonical_name,
                primary_entity_kind=entity.primary_entity_kind,
                semantic_assessment=semantic,
                geography_assessment=geography,
                evidence_readiness_assessment=readiness,
                dimension_weights_used=weights_used,
                entity_priority_score=priority_score,
                evidence_readiness_score=readiness.score,
                rank=0,
                priority_tier=PriorityTier.TIER_D_DEFERRED,
                rationale=semantic.short_overall_rationale,
                review_flags=review_flags,
                provenance={
                    "source": "phase3_semantic_assessment",
                    "scoring_policy_version": ENTITY_PRIORITY_SCORING_POLICY_VERSION,
                    "evidence_readiness_policy_version": EVIDENCE_READINESS_POLICY_VERSION,
                    "priority_tier_policy_version": PRIORITY_TIER_POLICY_VERSION,
                },
            )
        )
    return assign_ranks_and_tiers(tuple(preliminary))


def build_entity_prioritization_input_fingerprint(
    *,
    entity_universe_result: EntityUniverseResult,
    compact_entity_contexts: tuple[dict[str, Any], ...],
    information_needs: tuple[InformationNeed, ...],
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    provider_configuration: dict[str, Any],
    generation_limits: dict[str, Any],
) -> str:
    return hash_canonical_value(
        {
            "schema_version": ENTITY_PRIORITIZATION_RESULT_SCHEMA_VERSION,
            "entity_candidates": sorted(
                entity_universe_result.entity_candidates,
                key=lambda item: item.entity_id,
            ),
            "phase2_output_hash": entity_universe_result.output_hash,
            "information_needs": sorted(
                information_needs,
                key=lambda item: item.information_need_id,
            ),
            "target_career_paths": sorted(
                target_career_paths,
                key=lambda item: item.path_id,
            ),
            "user_preferences": user_preferences,
            "compact_entity_contexts": compact_entity_contexts,
            "scoring_policy": scoring_policy_snapshot(),
            "semantic_scale_version": SEMANTIC_SCORE_SCALE_VERSION,
            "evidence_readiness_policy": evidence_readiness_policy_snapshot(),
            "compact_context_policy_version": COMPACT_CONTEXT_POLICY_VERSION,
            "priority_tier_policy": tier_policy_snapshot(),
            "prompt_version": ENTITY_PRIORITIZATION_PROMPT_VERSION,
            "provider_configuration": provider_configuration,
            "generation_limits": generation_limits,
        }
    )


def build_entity_prioritization_output_hash(
    *,
    priority_assessments: tuple[EntityPriorityAssessment, ...],
    rejected_assessments: tuple[RejectedEntityPriorityAssessment, ...],
    unassessed_entity_ids: tuple[str, ...],
) -> str:
    return hash_canonical_value(
        {
            "schema_version": ENTITY_PRIORITIZATION_RESULT_SCHEMA_VERSION,
            "priority_assessments": sorted(
                priority_assessments,
                key=lambda item: item.priority_assessment_id,
            ),
            "rejected_assessments": rejected_assessments,
            "unassessed_entity_ids": sorted(unassessed_entity_ids),
        }
    )


def build_priority_assessment_id(
    *,
    entity_id: str,
    scoring_policy_version: str,
) -> str:
    digest = hash_canonical_value(
        {
            "entity_id": entity_id,
            "scoring_policy_version": scoring_policy_version,
        }
    )
    return f"entity_priority_{digest[:16]}"


def _build_prioritization_result(
    *,
    priority_assessments: tuple[EntityPriorityAssessment, ...],
    rejected_assessments: tuple[RejectedEntityPriorityAssessment, ...],
    unassessed_entity_ids: tuple[str, ...],
    diagnostics: tuple[str, ...],
    execution_metadata: EntityPrioritizationExecutionMetadata,
    input_fingerprint: str,
    generation_mode: str,
    cache_enabled: bool,
    cache_file: Path,
) -> EntityPrioritizationResult:
    result = EntityPrioritizationResult(
        priority_assessments=tuple(
            sorted(priority_assessments, key=lambda item: item.rank)
        ),
        rejected_assessments=tuple(rejected_assessments),
        unassessed_entity_ids=tuple(sorted(unassessed_entity_ids)),
        diagnostics=tuple(diagnostics),
        execution_metadata=execution_metadata,
        scoring_policy_version=ENTITY_PRIORITY_SCORING_POLICY_VERSION,
        compact_context_policy_version=COMPACT_CONTEXT_POLICY_VERSION,
        prompt_version=ENTITY_PRIORITIZATION_PROMPT_VERSION,
        input_fingerprint=input_fingerprint,
        output_hash=build_entity_prioritization_output_hash(
            priority_assessments=priority_assessments,
            rejected_assessments=rejected_assessments,
            unassessed_entity_ids=unassessed_entity_ids,
        ),
        generation_mode=generation_mode,
    )
    if cache_enabled and generation_mode == "generated":
        save_entity_prioritization_result(cache_file, result)
    return result


def _dimension_from_payload(
    payload: Any,
    *,
    field_name: str,
    valid_need_ids: set[str],
    related_need_ids: set[str],
) -> tuple[SemanticDimensionAssessment, list[str]]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        payload = {}
        errors.append(f"{field_name} must be an object")

    status_text = str(payload.get("status", "assessed")).strip()
    try:
        status = SemanticAssessmentStatus(status_text)
    except ValueError:
        errors.append(f"{field_name} status is invalid")
        status = SemanticAssessmentStatus.ASSESSED

    score_value = payload.get("score")
    score: int | None
    if score_value is None:
        score = None
    else:
        try:
            score = int(score_value)
        except (TypeError, ValueError):
            errors.append(f"{field_name} score must be an integer")
            score = None

    if status in {
        SemanticAssessmentStatus.ASSESSED,
        SemanticAssessmentStatus.APPLICABLE,
    }:
        if score is None or not 0 <= score <= 5:
            errors.append(f"{field_name} score must be between 0 and 5")
    elif score is not None and not 0 <= score <= 5:
        errors.append(f"{field_name} score must be null or between 0 and 5")

    if field_name == "stage_relevance":
        if status == SemanticAssessmentStatus.ASSESSED:
            status = SemanticAssessmentStatus.APPLICABLE
        if status in {
            SemanticAssessmentStatus.NOT_APPLICABLE,
            SemanticAssessmentStatus.INSUFFICIENT_EVIDENCE,
        } and score is not None:
            errors.append(f"{field_name} score must be null when status is {status.value}")
    elif status == SemanticAssessmentStatus.APPLICABLE:
        errors.append(f"{field_name} status cannot be applicable")

    rationale = str(payload.get("rationale", "")).strip()
    if not rationale:
        errors.append(f"{field_name} rationale is required")
    if len(rationale) > 800:
        errors.append(f"{field_name} rationale is too long")

    supporting_need_ids = tuple(
        str(item)
        for item in payload.get("supporting_information_need_ids", [])
        if item is not None
    ) if isinstance(payload.get("supporting_information_need_ids", []), list) else ()
    unknown_need_ids = sorted(set(supporting_need_ids) - valid_need_ids)
    if unknown_need_ids:
        errors.append(f"{field_name} references unknown InformationNeed IDs")
    unrelated_need_ids = sorted(set(supporting_need_ids) - related_need_ids)
    if unrelated_need_ids:
        errors.append(f"{field_name} references unrelated InformationNeed IDs")

    return (
        SemanticDimensionAssessment(
            score=score,
            status=status,
            rationale=rationale,
            supporting_information_need_ids=tuple(sorted(set(supporting_need_ids))),
            limiting_factors=_string_tuple(payload.get("limiting_factors")),
            review_flags=_string_tuple(payload.get("review_flags")),
        ),
        errors,
    )


def _readiness_review_flags(readiness) -> tuple[str, ...]:
    flags: list[str] = []
    if readiness.score < 50:
        flags.append("low_evidence_readiness")
    if readiness.official_domain_status in {"none", "unresolved", "third_party"}:
        flags.append("needs_domain_verification")
    if readiness.identity_conflict_status != "none":
        flags.append("identity_conflict_review")
    return tuple(flags)


def _contains_forbidden_source_claim(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False).casefold()
    forbidden_patterns = (
        "observed_signal_potential",
        "rss available",
        "has rss",
        "rss feed exists",
        "publishes daily",
        "weekly publication",
        "publication frequency",
        "observed source performance",
        "historical signal yield",
        "content freshness",
        "source reliability",
        "newsroom quality",
        "careers page quality",
        "approved source",
    )
    return any(pattern in text for pattern in forbidden_patterns)


def _extract_llm_response_text(response: Any) -> str | None:
    choices = getattr(response, "choices", None)
    if not choices:
        return None
    first = choices[0]
    message = getattr(first, "message", None)
    if message is None:
        return None
    return getattr(message, "content", None)


def _normalize_json_response_text(response_text: str) -> str:
    text = response_text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    return text


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if item is not None)
