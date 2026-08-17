import json
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.config import (
    INFORMATION_NEED_CACHE_ENABLED,
    INFORMATION_NEED_MAX_PER_PATH_OBJECTIVE,
    INFORMATION_NEED_MAX_SIGNAL_EXAMPLES,
    INFORMATION_NEED_MAX_TOTAL,
    INFORMATION_NEED_MODEL,
    INFORMATION_NEED_TEMPERATURE,
    INFORMATION_NEEDS_FILE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
)
from src.models import TargetCareerPath
from src.source_monitoring.cache import (
    load_cached_information_need_result,
    save_information_need_result,
)
from src.source_monitoring.identity import (
    build_information_need_input_fingerprint,
    build_information_need_output_hash,
)
from src.source_monitoring.models import (
    INFORMATION_NEED_SCHEMA_VERSION,
    InformationNeedGenerationResult,
    LLMExecutionMetadata,
)
from src.source_monitoring.monitoring_objectives import (
    get_monitoring_objectives,
    validate_monitoring_objectives,
)
from src.source_monitoring.prompts import (
    INFORMATION_NEED_PROMPT_VERSION,
    build_information_need_prompt,
)
from src.source_monitoring.validators import (
    parse_information_need_suggestions,
    validate_normalize_and_deduplicate_information_needs,
)


class InformationNeedGenerationError(Exception):
    """
    Raised when Phase 0 InformationNeed generation cannot use LLM output.
    """


class InformationNeedClient:
    """
    Narrow OpenAI-compatible LLM client for Source Monitoring Phase 0.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = INFORMATION_NEED_TEMPERATURE,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature

        if not self.api_key or self.api_key.startswith("your_"):
            raise InformationNeedGenerationError(
                "LLM_API_KEY is missing. Add your real LLM API key to .env "
                "before running InformationNeed generation."
            )

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate(
        self,
        *,
        target_career_paths: list[TargetCareerPath],
        user_preferences: dict[str, Any],
        monitoring_objectives,
        max_per_path_objective: int,
        max_total: int,
        max_signal_examples: int,
    ) -> dict[str, Any]:
        prompt = build_information_need_prompt(
            target_career_paths=target_career_paths,
            user_preferences=user_preferences,
            monitoring_objectives=monitoring_objectives,
            max_per_path_objective=max_per_path_objective,
            max_total=max_total,
            max_signal_examples=max_signal_examples,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You generate Source Monitoring InformationNeeds for "
                        "a career intelligence workflow. Return only valid JSON. "
                        "Do not search the web. Do not generate concrete "
                        "entities, sources, URLs, feeds, jobs, or SearchPlans."
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
            raise InformationNeedGenerationError(
                "LLM returned an empty InformationNeed response."
            )

        json_text = _normalize_json_response_text(response_text)

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as error:
            raise InformationNeedGenerationError(
                "InformationNeed generation returned invalid JSON after cleanup. "
                f"Response preview: {json_text[:500]!r}"
            ) from error

        if not isinstance(parsed, dict):
            raise InformationNeedGenerationError(
                "InformationNeed response must be a JSON object."
            )

        return parsed


def generate_information_needs(
    *,
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    monitoring_objectives=None,
    client: InformationNeedClient | None = None,
    force_refresh: bool = False,
    cache_enabled: bool = INFORMATION_NEED_CACHE_ENABLED,
    cache_file: Path = INFORMATION_NEEDS_FILE,
    model: str | None = None,
    provider: str | None = None,
    temperature: float = INFORMATION_NEED_TEMPERATURE,
    max_per_path_objective: int = INFORMATION_NEED_MAX_PER_PATH_OBJECTIVE,
    max_total: int = INFORMATION_NEED_MAX_TOTAL,
    max_signal_examples: int = INFORMATION_NEED_MAX_SIGNAL_EXAMPLES,
) -> InformationNeedGenerationResult:
    """
    Generate, validate, normalize, deduplicate, and optionally cache Phase 0.
    """

    objectives = validate_monitoring_objectives(
        monitoring_objectives or get_monitoring_objectives()
    )
    selected_provider = provider or (client.provider if client is not None else LLM_PROVIDER)
    selected_model = model or (client.model if client is not None else INFORMATION_NEED_MODEL)
    llm_metadata = LLMExecutionMetadata(
        provider=selected_provider,
        model=selected_model,
        prompt_version=INFORMATION_NEED_PROMPT_VERSION,
        schema_version=INFORMATION_NEED_SCHEMA_VERSION,
    )
    generation_limits = {
        "max_per_path_objective": max_per_path_objective,
        "max_total": max_total,
        "max_signal_examples": max_signal_examples,
    }
    input_fingerprint = build_information_need_input_fingerprint(
        target_career_paths=target_career_paths,
        user_preferences=user_preferences,
        monitoring_objectives=objectives,
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
        cached_result, cache_diagnostics = load_cached_information_need_result(
            cache_file=cache_file,
            input_fingerprint=input_fingerprint,
        )

        if cached_result is not None:
            return cached_result

    try:
        generation_client = client or InformationNeedClient(
            provider=selected_provider or LLM_PROVIDER,
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=selected_model or INFORMATION_NEED_MODEL,
            temperature=temperature,
        )
        parsed = generation_client.generate(
            target_career_paths=target_career_paths,
            user_preferences=user_preferences,
            monitoring_objectives=objectives,
            max_per_path_objective=max_per_path_objective,
            max_total=max_total,
            max_signal_examples=max_signal_examples,
        )
    except Exception as error:
        diagnostics = cache_diagnostics + (
            f"InformationNeed generation unavailable: {type(error).__name__}: {error}",
        )
        return _build_result(
            objectives=objectives,
            information_needs=(),
            rejected_suggestions=(),
            diagnostics=diagnostics,
            llm_metadata=llm_metadata,
            input_fingerprint=input_fingerprint,
            generation_mode="unavailable",
        )

    suggestions, parse_diagnostics = parse_information_need_suggestions(parsed)
    information_needs, rejected, validation_diagnostics = (
        validate_normalize_and_deduplicate_information_needs(
            suggestions=suggestions,
            target_career_paths=target_career_paths,
            llm_metadata=llm_metadata,
            input_fingerprint=input_fingerprint,
            max_total=max_total,
            max_signal_examples=max_signal_examples,
            max_per_path_objective=max_per_path_objective,
        )
    )
    result = _build_result(
        objectives=objectives,
        information_needs=information_needs,
        rejected_suggestions=rejected,
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
        save_information_need_result(cache_file, result)

    return result


def _build_result(
    *,
    objectives,
    information_needs,
    rejected_suggestions,
    diagnostics,
    llm_metadata: LLMExecutionMetadata,
    input_fingerprint: str,
    generation_mode: str,
) -> InformationNeedGenerationResult:
    return InformationNeedGenerationResult(
        monitoring_objectives=tuple(objectives),
        information_needs=tuple(information_needs),
        rejected_suggestions=tuple(rejected_suggestions),
        diagnostics=tuple(diagnostics),
        llm_execution_metadata=llm_metadata,
        input_fingerprint=input_fingerprint,
        output_hash=build_information_need_output_hash(tuple(information_needs)),
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
