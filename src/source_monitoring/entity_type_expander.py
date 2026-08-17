import json
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.config import (
    ENTITY_TYPE_CANDIDATES_FILE,
    ENTITY_TYPE_EXPANSION_CACHE_ENABLED,
    ENTITY_TYPE_EXPANSION_MAX_CANONICAL_CANDIDATES,
    ENTITY_TYPE_EXPANSION_MAX_DISCOVERY_TERMS,
    ENTITY_TYPE_EXPANSION_MAX_PROPOSED_TYPES,
    ENTITY_TYPE_EXPANSION_MAX_TYPES_PER_NEED,
    ENTITY_TYPE_EXPANSION_MODEL,
    ENTITY_TYPE_EXPANSION_TEMPERATURE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
)
from src.models import TargetCareerPath
from src.source_monitoring.cache import (
    load_cached_entity_type_expansion_result,
    save_entity_type_expansion_result,
)
from src.source_monitoring.entity_type_ontology import (
    ENTITY_TYPE_ONTOLOGY_VERSION,
    get_entity_type_ontology,
    validate_entity_type_ontology,
)
from src.source_monitoring.identity import (
    build_entity_type_expansion_input_fingerprint,
    build_entity_type_expansion_output_hash,
    build_information_need_output_hash,
)
from src.source_monitoring.models import (
    ENTITY_TYPE_CANDIDATE_SCHEMA_VERSION,
    EntityTypeExpansionResult,
    InformationNeed,
    LLMExecutionMetadata,
    MonitoringObjectiveDefinition,
)
from src.source_monitoring.monitoring_objectives import (
    get_monitoring_objectives,
    validate_monitoring_objectives,
)
from src.source_monitoring.prompts import (
    ENTITY_TYPE_EXPANSION_PROMPT_VERSION,
    build_entity_type_expansion_prompt,
)
from src.source_monitoring.validators import (
    parse_entity_type_expansion_suggestions,
    validate_normalize_and_deduplicate_entity_type_expansion,
)


class EntityTypeExpansionError(Exception):
    """
    Raised when Phase 1 Entity Type Expansion cannot use LLM output.
    """


class EntityTypeExpansionClient:
    """
    Narrow OpenAI-compatible LLM client for Source Monitoring Phase 1.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = ENTITY_TYPE_EXPANSION_TEMPERATURE,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature

        if not self.api_key or self.api_key.startswith("your_"):
            raise EntityTypeExpansionError(
                "LLM_API_KEY is missing. Add your real LLM API key to .env "
                "before running Entity Type Expansion."
            )

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate(
        self,
        *,
        monitoring_objectives: tuple[MonitoringObjectiveDefinition, ...],
        information_needs: tuple[InformationNeed, ...],
        target_career_paths: list[TargetCareerPath],
        user_preferences: dict[str, Any],
        entity_type_ontology,
        max_canonical_candidates: int,
        max_proposed_types: int,
        max_types_per_need: int,
        max_discovery_terms: int,
    ) -> dict[str, Any]:
        prompt = build_entity_type_expansion_prompt(
            monitoring_objectives=monitoring_objectives,
            information_needs=information_needs,
            target_career_paths=target_career_paths,
            user_preferences=user_preferences,
            entity_type_ontology=entity_type_ontology,
            max_canonical_candidates=max_canonical_candidates,
            max_proposed_types=max_proposed_types,
            max_types_per_need=max_types_per_need,
            max_discovery_terms=max_discovery_terms,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You map Source Monitoring InformationNeeds to a "
                        "controlled Entity Type Ontology. Return only valid "
                        "JSON. Do not search the web. Do not generate concrete "
                        "entities, sources, URLs, feeds, jobs, SearchQueries, "
                        "SearchPlans, or SourceDiscoveryPlans."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            response_format={"type": "json_object"},
            temperature=self.temperature,
            stream=False,
        )

        response_text = _extract_llm_response_text(response)

        if response_text is None or not response_text.strip():
            raise EntityTypeExpansionError(
                "LLM returned an empty EntityTypeExpansion response."
            )

        json_text = _normalize_json_response_text(response_text)

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as error:
            raise EntityTypeExpansionError(
                "EntityTypeExpansion returned invalid JSON after cleanup. "
                f"Response preview: {json_text[:500]!r}"
            ) from error

        if not isinstance(parsed, dict):
            raise EntityTypeExpansionError(
                "EntityTypeExpansion response must be a JSON object."
            )

        return parsed


def expand_entity_types(
    *,
    information_needs: tuple[InformationNeed, ...],
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    monitoring_objectives=None,
    entity_type_ontology=None,
    phase0_output_hash: str | None = None,
    client: EntityTypeExpansionClient | None = None,
    force_refresh: bool = False,
    cache_enabled: bool = ENTITY_TYPE_EXPANSION_CACHE_ENABLED,
    cache_file: Path = ENTITY_TYPE_CANDIDATES_FILE,
    model: str | None = None,
    provider: str | None = None,
    temperature: float = ENTITY_TYPE_EXPANSION_TEMPERATURE,
    max_canonical_candidates: int = ENTITY_TYPE_EXPANSION_MAX_CANONICAL_CANDIDATES,
    max_proposed_types: int = ENTITY_TYPE_EXPANSION_MAX_PROPOSED_TYPES,
    max_types_per_need: int = ENTITY_TYPE_EXPANSION_MAX_TYPES_PER_NEED,
    max_discovery_terms: int = ENTITY_TYPE_EXPANSION_MAX_DISCOVERY_TERMS,
) -> EntityTypeExpansionResult:
    """
    Expand accepted InformationNeeds into canonical EntityTypeCandidates.
    """

    objectives = validate_monitoring_objectives(
        monitoring_objectives or get_monitoring_objectives()
    )
    ontology = validate_entity_type_ontology(
        entity_type_ontology or get_entity_type_ontology()
    )
    selected_provider = provider or (client.provider if client is not None else LLM_PROVIDER)
    selected_model = model or (client.model if client is not None else ENTITY_TYPE_EXPANSION_MODEL)
    llm_metadata = LLMExecutionMetadata(
        provider=selected_provider,
        model=selected_model,
        prompt_version=ENTITY_TYPE_EXPANSION_PROMPT_VERSION,
        schema_version=ENTITY_TYPE_CANDIDATE_SCHEMA_VERSION,
    )
    generation_limits = {
        "max_canonical_candidates": max_canonical_candidates,
        "max_proposed_types": max_proposed_types,
        "max_types_per_need": max_types_per_need,
        "max_discovery_terms": max_discovery_terms,
    }
    selected_phase0_output_hash = phase0_output_hash or build_information_need_output_hash(
        tuple(sorted(information_needs, key=lambda need: need.information_need_id))
    )
    input_fingerprint = build_entity_type_expansion_input_fingerprint(
        information_needs=information_needs,
        phase0_output_hash=selected_phase0_output_hash,
        target_career_paths=target_career_paths,
        user_preferences=user_preferences,
        monitoring_objectives=objectives,
        ontology=ontology,
        llm_metadata=llm_metadata,
        generation_limits=generation_limits,
        temperature=temperature,
    )
    llm_metadata = LLMExecutionMetadata(
        provider=llm_metadata.provider,
        model=llm_metadata.model,
        prompt_version=llm_metadata.prompt_version,
        schema_version=llm_metadata.schema_version,
        input_fingerprint=input_fingerprint,
    )

    cache_diagnostics: tuple[str, ...] = ()

    if cache_enabled and not force_refresh:
        cached_result, cache_diagnostics = load_cached_entity_type_expansion_result(
            cache_file=cache_file,
            input_fingerprint=input_fingerprint,
            ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
        )

        if cached_result is not None:
            return cached_result

    try:
        generation_client = client or EntityTypeExpansionClient(
            provider=selected_provider or LLM_PROVIDER,
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=selected_model or ENTITY_TYPE_EXPANSION_MODEL,
            temperature=temperature,
        )
        parsed = generation_client.generate(
            monitoring_objectives=objectives,
            information_needs=information_needs,
            target_career_paths=target_career_paths,
            user_preferences=user_preferences,
            entity_type_ontology=ontology,
            max_canonical_candidates=max_canonical_candidates,
            max_proposed_types=max_proposed_types,
            max_types_per_need=max_types_per_need,
            max_discovery_terms=max_discovery_terms,
        )
    except Exception as error:
        diagnostics = cache_diagnostics + (
            f"EntityTypeExpansion unavailable: {type(error).__name__}: {error}",
        )
        return _build_entity_type_expansion_result(
            ontology=ontology,
            canonical_candidates=(),
            proposed_new_types=(),
            rejected_suggestions=(),
            uncovered_information_need_ids=tuple(
                need.information_need_id
                for need in sorted(
                    information_needs,
                    key=lambda item: item.information_need_id,
                )
            ),
            diagnostics=diagnostics,
            llm_metadata=llm_metadata,
            input_fingerprint=input_fingerprint,
            generation_mode="unavailable",
        )

    candidate_suggestions, proposed_suggestions, parse_diagnostics = (
        parse_entity_type_expansion_suggestions(parsed)
    )
    canonical_candidates, proposed_new_types, rejected, uncovered, validation_diagnostics = (
        validate_normalize_and_deduplicate_entity_type_expansion(
            candidate_suggestions=candidate_suggestions,
            proposed_type_suggestions=proposed_suggestions,
            information_needs=information_needs,
            target_career_paths=target_career_paths,
            ontology=ontology,
            llm_metadata=llm_metadata,
            input_fingerprint=input_fingerprint,
            max_canonical_candidates=max_canonical_candidates,
            max_proposed_types=max_proposed_types,
            max_types_per_need=max_types_per_need,
            max_discovery_terms=max_discovery_terms,
        )
    )
    result = _build_entity_type_expansion_result(
        ontology=ontology,
        canonical_candidates=canonical_candidates,
        proposed_new_types=proposed_new_types,
        rejected_suggestions=rejected,
        uncovered_information_need_ids=uncovered,
        diagnostics=(
            cache_diagnostics
            + tuple(parse_diagnostics)
            + tuple(validation_diagnostics)
        ),
        llm_metadata=llm_metadata,
        input_fingerprint=input_fingerprint,
        generation_mode="generated",
    )

    if cache_enabled:
        save_entity_type_expansion_result(cache_file, result)

    return result


def _build_entity_type_expansion_result(
    *,
    ontology,
    canonical_candidates,
    proposed_new_types,
    rejected_suggestions,
    uncovered_information_need_ids,
    diagnostics,
    llm_metadata: LLMExecutionMetadata,
    input_fingerprint: str,
    generation_mode: str,
) -> EntityTypeExpansionResult:
    return EntityTypeExpansionResult(
        canonical_entity_types=tuple(ontology),
        canonical_candidates=tuple(canonical_candidates),
        proposed_new_types=tuple(proposed_new_types),
        rejected_suggestions=tuple(rejected_suggestions),
        uncovered_information_need_ids=tuple(uncovered_information_need_ids),
        diagnostics=tuple(diagnostics),
        llm_execution_metadata=llm_metadata,
        input_fingerprint=input_fingerprint,
        output_hash=build_entity_type_expansion_output_hash(
            canonical_candidates=tuple(canonical_candidates),
            proposed_new_types=tuple(proposed_new_types),
            rejected_suggestions=tuple(rejected_suggestions),
            uncovered_information_need_ids=tuple(uncovered_information_need_ids),
        ),
        generation_mode=generation_mode,
    )


def _extract_llm_response_text(response: Any) -> str | None:
    choices = getattr(response, "choices", None)

    if not choices:
        return None

    first_choice = choices[0]
    message = getattr(first_choice, "message", None)

    if message is None:
        return None

    content = getattr(message, "content", None)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        return "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        )

    return None


def _normalize_json_response_text(response_text: str) -> str:
    stripped_text = response_text.strip()
    fenced_match = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if fenced_match is not None:
        stripped_text = fenced_match.group(1).strip()

    if stripped_text.startswith("{") and stripped_text.endswith("}"):
        return stripped_text

    extracted_object = _extract_first_top_level_json_object(stripped_text)

    if extracted_object is not None:
        return extracted_object

    return stripped_text


def _extract_first_top_level_json_object(text: str) -> str | None:
    start_index = text.find("{")

    if start_index == -1:
        return None

    depth = 0
    in_string = False
    is_escaped = False

    for index in range(start_index, len(text)):
        character = text[index]

        if in_string:
            if is_escaped:
                is_escaped = False
            elif character == "\\":
                is_escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1

            if depth == 0:
                return text[start_index:index + 1]

    return None
