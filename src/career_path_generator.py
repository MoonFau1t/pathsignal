import hashlib
import json
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from src.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
    TARGET_CAREER_PATH_FORCE_REFRESH,
    TARGET_CAREER_PATH_MODEL,
    TARGET_CAREER_PATH_PROMPT_VERSION,
    TARGET_CAREER_PATH_SCHEMA_VERSION,
    TARGET_CAREER_PATHS_FILE,
)
from src.models import CareerPathCategory, TargetCareerPath, UserProfile, utc_now_iso
from src.storage import load_json, save_json


class TargetCareerPathGenerationError(Exception):
    """
    Raised when LLM-based TargetCareerPath generation fails.
    """


class TargetCareerPathClient:
    """
    OpenAI-compatible LLM client for TargetCareerPath generation.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

        if not self.api_key or self.api_key.startswith("your_"):
            raise TargetCareerPathGenerationError(
                "LLM_API_KEY is missing. Add your real LLM API key to .env "
                "before running TargetCareerPath generation."
            )

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    def generate(
        self,
        user_profile: UserProfile,
        user_preferences: dict[str, Any],
    ) -> list[TargetCareerPath]:
        """
        Generate TargetCareerPath objects from factual profile and preferences.
        """

        prompt = _build_target_career_path_prompt(
            user_profile=user_profile,
            user_preferences=user_preferences,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You generate TargetCareerPath hypotheses for a career "
                        "intelligence workflow. Return only valid JSON. "
                        "Do not use markdown. Do not generate search plans."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            response_format={
                "type": "json_object",
            },
            stream=False,
        )

        response_text = _extract_llm_response_text(response)

        if response_text is None or not response_text.strip():
            raise TargetCareerPathGenerationError(
                "LLM returned an empty TargetCareerPath response.\n"
                f"{_build_empty_response_debug_info(response, self)}"
            )

        print(
            "LLM TargetCareerPath response debug: "
            f"length={len(response_text)}, preview={ascii(response_text[:240])}"
        )

        json_text = _normalize_json_response_text(response_text)

        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as error:
            raise TargetCareerPathGenerationError(
                "TargetCareerPath generation returned invalid JSON after cleanup. "
                f"Response preview: {json_text[:500]!r}"
            ) from error

        return _build_target_career_paths_from_ai_json(parsed)


CAREER_PATH_TEMPLATES = [
    {
        "path_id": "corporate_strategy",
        "title": "Corporate Strategy",
        "category": CareerPathCategory.CORPORATE_STRATEGY,
        "description": (
            "Corporate strategy roles focused on market research, "
            "competitive analysis, business planning, and growth strategy."
        ),
        "trigger_keywords": [
            "strategy",
            "corporate strategy",
            "market analysis",
            "business analysis",
            "strategic thinking",
            "research",
        ],
        "suggested_roles": [
            "corporate strategy analyst",
            "strategy analyst",
            "business strategy analyst",
            "growth strategy analyst",
        ],
        "search_seed_terms": [
            "corporate strategy analyst",
            "strategy analyst",
            "business strategy analyst",
            "growth strategy",
        ],
    },
    {
        "path_id": "ai_strategy",
        "title": "AI Strategy",
        "category": CareerPathCategory.AI_STRATEGY,
        "description": (
            "AI strategy roles focused on helping companies understand, "
            "evaluate, and apply AI technologies in business contexts."
        ),
        "trigger_keywords": [
            "ai",
            "artificial intelligence",
            "ai strategy",
            "technology",
            "tech",
            "python",
            "market analysis",
            "consulting",
        ],
        "suggested_roles": [
            "AI strategy associate",
            "AI strategy analyst",
            "AI transformation analyst",
            "AI product strategy analyst",
        ],
        "search_seed_terms": [
            "AI strategy associate",
            "AI strategy analyst",
            "AI transformation consultant",
            "AI product strategy",
        ],
    },
    {
        "path_id": "venture_capital",
        "title": "Venture Capital",
        "category": CareerPathCategory.VENTURE_CAPITAL,
        "description": (
            "Venture capital roles focused on startup research, "
            "market mapping, founder evaluation, and investment analysis."
        ),
        "trigger_keywords": [
            "venture capital",
            "vc",
            "startup",
            "startups",
            "investment",
            "market analysis",
            "research",
            "technology",
            "ai",
        ],
        "suggested_roles": [
            "VC analyst",
            "venture capital analyst",
            "venture associate",
            "investment analyst",
        ],
        "search_seed_terms": [
            "venture capital analyst",
            "VC analyst",
            "venture associate",
            "startup investment analyst",
        ],
    },
    {
        "path_id": "tech_consulting",
        "title": "Technology Consulting",
        "category": CareerPathCategory.TECH_CONSULTING,
        "description": (
            "Technology consulting roles focused on digital transformation, "
            "AI adoption, business process improvement, and client advisory."
        ),
        "trigger_keywords": [
            "consulting",
            "technology consulting",
            "tech consulting",
            "digital transformation",
            "ai transformation",
            "strategy",
            "business analysis",
        ],
        "suggested_roles": [
            "technology consulting analyst",
            "digital transformation consultant",
            "AI consulting analyst",
            "strategy consulting analyst",
        ],
        "search_seed_terms": [
            "technology consulting analyst",
            "digital transformation consultant",
            "AI consulting analyst",
            "strategy consulting analyst",
        ],
    },
    {
        "path_id": "boutique_fa",
        "title": "Boutique FA / Investment Banking",
        "category": CareerPathCategory.BOUTIQUE_FA,
        "description": (
            "Boutique financial advisory or investment banking roles that can "
            "serve as a transition path into venture capital, strategy, or startup finance."
        ),
        "trigger_keywords": [
            "finance",
            "investment",
            "fa",
            "financial advisory",
            "investment banking",
            "market analysis",
            "research",
            "startup",
        ],
        "suggested_roles": [
            "FA analyst",
            "boutique investment banking analyst",
            "financial advisory analyst",
            "M&A analyst",
        ],
        "search_seed_terms": [
            "FA analyst",
            "boutique investment banking analyst",
            "financial advisory analyst",
            "M&A analyst",
        ],
    },
    {
        "path_id": "market_research",
        "title": "Market Research",
        "category": CareerPathCategory.MARKET_RESEARCH,
        "description": (
            "Market research roles focused on industry analysis, company research, "
            "consumer trends, and business intelligence."
        ),
        "trigger_keywords": [
            "research",
            "market research",
            "industry analysis",
            "writing",
            "analysis",
            "economics",
            "history",
        ],
        "suggested_roles": [
            "market research analyst",
            "industry research analyst",
            "business intelligence analyst",
            "research analyst",
        ],
        "search_seed_terms": [
            "market research analyst",
            "industry research analyst",
            "business intelligence analyst",
            "research analyst",
        ],
    },
]


def _normalize_text_items(items: list[str]) -> str:
    """
    Convert a list of text items into one lowercase searchable string.
    """

    return " ".join(items).lower()


def _collect_profile_text(user_profile: UserProfile) -> str:
    """
    Collect important user profile fields into one searchable text block.
    """

    education_text = " ".join(
        " ".join(str(value) for value in item.values())
        for item in user_profile.education
    )

    work_text = " ".join(
        " ".join(str(value) for value in item.values())
        for item in user_profile.work_experience
    )

    profile_text_parts = [
        user_profile.background_summary,
        education_text,
        work_text,
        _normalize_text_items(user_profile.skills),
        _normalize_text_items(user_profile.interests),
        _normalize_text_items(user_profile.preferred_roles),
        _normalize_text_items(user_profile.constraints),
        user_profile.raw_resume_text,
    ]

    return " ".join(profile_text_parts).lower()


def _calculate_fit_score(
    profile_text: str,
    trigger_keywords: list[str],
) -> tuple[float, list[str]]:
    """
    Calculate a simple rule-based fit score for one career path.

    This is intentionally simple in Phase 4.
    LLM-based interpretation will come later.
    """

    matched_keywords = [
        keyword
        for keyword in trigger_keywords
        if keyword.lower() in profile_text
    ]

    if not trigger_keywords:
        return 0.0, []

    raw_score = len(matched_keywords) / len(trigger_keywords)
    fit_score = round(min(raw_score * 100, 100), 2)

    return fit_score, matched_keywords


def generate_rule_based_target_career_paths(
    user_profile: UserProfile,
    minimum_score: float = 10.0,
) -> list[TargetCareerPath]:
    """
    Generate target career paths from a UserProfile.

    Legacy deterministic generator preserved for comparison and fallback tests.
    The V1 main flow should use generate_target_career_paths instead.
    """

    profile_text = _collect_profile_text(user_profile)
    generated_paths: list[TargetCareerPath] = []

    for template in CAREER_PATH_TEMPLATES:
        fit_score, matched_keywords = _calculate_fit_score(
            profile_text=profile_text,
            trigger_keywords=template["trigger_keywords"],
        )

        if fit_score < minimum_score:
            continue

        rationale = [
            f"Matched profile keyword: {keyword}"
            for keyword in matched_keywords
        ]

        generated_paths.append(
            TargetCareerPath(
                path_id=template["path_id"],
                title=template["title"],
                category=template["category"],
                description=template["description"],
                fit_score=fit_score,
                rationale=rationale,
                keywords=matched_keywords,
                suggested_roles=template["suggested_roles"],
                search_seed_terms=template["search_seed_terms"],
                metadata={
                    "generator": "rule_based_phase_4",
                    "minimum_score": minimum_score,
                },
            )
        )

    generated_paths.sort(
        key=lambda career_path: career_path.fit_score,
        reverse=True,
    )

    return generated_paths


def generate_target_career_paths(
    user_profile: UserProfile,
    user_preferences: dict[str, Any],
    client: TargetCareerPathClient | None = None,
    cache_file: Path = TARGET_CAREER_PATHS_FILE,
    force_refresh: bool = TARGET_CAREER_PATH_FORCE_REFRESH,
) -> list[TargetCareerPath]:
    """
    Generate TargetCareerPath objects with an LLM and cache the result.

    Cache invalidation is intentionally independent of SearchScope.
    SearchScope is applied later during SearchPlan generation.
    """

    model = client.model if client is not None else TARGET_CAREER_PATH_MODEL

    cache_key = _build_target_career_path_cache_key(
        user_profile=user_profile,
        user_preferences=user_preferences,
        model=model,
    )

    if not force_refresh:
        cached_paths = _load_cached_target_career_paths(
            cache_file=cache_file,
            cache_key=cache_key,
        )

        if cached_paths is not None:
            print(f"Loaded TargetCareerPaths from cache: {cache_file}")
            return cached_paths

    print("Calling LLM to generate TargetCareerPaths.")

    generation_client = client or TargetCareerPathClient(
        provider=LLM_PROVIDER,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        model=TARGET_CAREER_PATH_MODEL,
    )

    target_career_paths = generation_client.generate(
        user_profile=user_profile,
        user_preferences=user_preferences,
    )

    for career_path in target_career_paths:
        career_path.metadata.update(
            {
                "generator": "llm_target_career_path_v1",
                "provider": generation_client.provider,
                "model": generation_client.model,
                "prompt_version": TARGET_CAREER_PATH_PROMPT_VERSION,
                "schema_version": TARGET_CAREER_PATH_SCHEMA_VERSION,
                "cache_key": cache_key,
            }
        )

    _save_target_career_path_cache(
        cache_file=cache_file,
        cache_key=cache_key,
        target_career_paths=target_career_paths,
        provider=generation_client.provider,
        model=generation_client.model,
    )

    print(f"Saved TargetCareerPaths to: {cache_file}")

    return target_career_paths


def _build_target_career_path_cache_key(
    user_profile: UserProfile,
    user_preferences: dict[str, Any],
    model: str,
) -> str:
    """
    Build a cache key from profile, preferences, prompt/schema versions, and model.
    """

    cache_payload = {
        "user_profile": user_profile.to_dict(),
        "user_preferences": user_preferences,
        "prompt_version": TARGET_CAREER_PATH_PROMPT_VERSION,
        "schema_version": TARGET_CAREER_PATH_SCHEMA_VERSION,
        "model": model,
    }

    canonical_payload = json.dumps(
        cache_payload,
        ensure_ascii=False,
        sort_keys=True,
    )

    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def _load_cached_target_career_paths(
    cache_file: Path,
    cache_key: str,
) -> list[TargetCareerPath] | None:
    """
    Load cached TargetCareerPaths when the cache key still matches.
    """

    if not cache_file.exists():
        return None

    payload = load_json(cache_file)

    if not isinstance(payload, dict):
        return None

    metadata = payload.get("generation_metadata", {})

    if not isinstance(metadata, dict):
        return None

    if metadata.get("cache_key") != cache_key:
        return None

    raw_paths = payload.get("target_career_paths", [])

    if not isinstance(raw_paths, list):
        return None

    paths = [
        _build_target_career_path_from_dict(item, fallback_index=index)
        for index, item in enumerate(raw_paths)
        if isinstance(item, dict)
    ]

    for path in paths:
        path.metadata["used_cache"] = True

    return paths


def _save_target_career_path_cache(
    cache_file: Path,
    cache_key: str,
    target_career_paths: list[TargetCareerPath],
    provider: str,
    model: str,
) -> None:
    """
    Save generated TargetCareerPaths with metadata wrapper.
    """

    save_json(
        data={
            "generation_metadata": {
                "schema_version": TARGET_CAREER_PATH_SCHEMA_VERSION,
                "prompt_version": TARGET_CAREER_PATH_PROMPT_VERSION,
                "provider": provider,
                "model": model,
                "cache_key": cache_key,
                "generated_at": utc_now_iso(),
                "used_cache": False,
                "cache_key_inputs": [
                    "inputs/user_profile.json",
                    "inputs/user_preferences_final.json",
                    "prompt_version",
                    "schema_version",
                    "model",
                ],
                "excluded_cache_key_inputs": [
                    "inputs/search_scope.json",
                    "inputs/profile_supplement.json",
                    "inputs/user_profile.json.bak",
                ],
            },
            "target_career_paths": target_career_paths,
        },
        output_path=cache_file,
    )


def _build_target_career_path_prompt(
    user_profile: UserProfile,
    user_preferences: dict[str, Any],
) -> str:
    """
    Build the strict JSON prompt for TargetCareerPath generation.
    """

    return f"""
Generate TargetCareerPath hypotheses for this candidate.

Input contract:
- UserProfile is factual candidate evidence extracted from inputs/user_profile.json.
- UserPreferences is the authoritative preference and constraint policy from inputs/user_preferences_final.json.
- Do not use inputs/profile_supplement.json.
- Do not use inputs/user_profile.json.bak.
- Do not use SearchScope or search_scope.json for career path suitability.
- SearchScope will be applied later by SearchQuery/SearchPlan generation.

Task:
- Translate the factual profile and authoritative preferences into market-facing role families.
- Generate core, bridge, stretch, and exploratory career paths to test.
- Expand AI + business/industry/organization implementation into realistic market role families.
- Keep paths suitable for early-career/new-graduate positioning unless explicitly marked stretch.
- Respect hard constraints and explain risks instead of hiding them.
- Use bilingual Chinese/English search metadata in every path.

Fit score:
- Use the existing project scale: 0 to 100.
- 100 means strongest fit among tested hypotheses.
- Keep scores calibrated; do not give every path the same score.

Return only valid JSON. Do not use markdown. Do not include commentary.
The response must start with {{ and end with }}.

Return JSON with this exact top-level shape:
{{
  "target_career_paths": [
    {{
      "path_id": "stable_snake_case_id",
      "title": "Market-facing career path title",
      "category": "corporate_strategy | ai_strategy | venture_capital | tech_consulting | boutique_fa | market_research | unknown",
      "description": "1-3 sentence description",
      "fit_score": 0,
      "rationale": ["short reason"],
      "keywords": ["general keyword"],
      "suggested_roles": ["role title"],
      "search_seed_terms": ["English or bilingual seed term"],
      "metadata": {{
        "path_type": "core_match | bridge_role | stretch_opportunity | exploratory_opportunity",
        "confidence": "high | medium | low",
        "search_seed_terms_zh": ["中文搜索词"],
        "search_seed_terms_en": ["English search term"],
        "role_keywords_zh": ["中文岗位关键词"],
        "role_keywords_en": ["English role keyword"],
        "profile_evidence": ["evidence from UserProfile"],
        "preference_evidence": ["evidence from UserPreferences"],
        "risk_flags": ["risk or mismatch"],
        "constraint_notes": ["constraint handling note"],
        "why_not_higher": ["why fit_score is not higher"]
      }}
    }}
  ]
}}

UserProfile:
{json.dumps(user_profile.to_dict(), ensure_ascii=False, indent=2)}

UserPreferences:
{json.dumps(user_preferences, ensure_ascii=False, indent=2)}
""".strip()


def _build_target_career_paths_from_ai_json(
    parsed: dict[str, Any],
) -> list[TargetCareerPath]:
    """
    Convert parsed LLM JSON into TargetCareerPath objects.
    """

    if not isinstance(parsed, dict):
        raise TargetCareerPathGenerationError(
            "TargetCareerPath response must be a JSON object."
        )

    raw_paths = parsed.get("target_career_paths")

    if not isinstance(raw_paths, list):
        raise TargetCareerPathGenerationError(
            "TargetCareerPath response must contain target_career_paths list."
        )

    paths = [
        _build_target_career_path_from_dict(item, fallback_index=index)
        for index, item in enumerate(raw_paths)
        if isinstance(item, dict)
    ]

    if not paths:
        raise TargetCareerPathGenerationError(
            "TargetCareerPath response did not contain any valid paths."
        )

    paths.sort(
        key=lambda career_path: career_path.fit_score,
        reverse=True,
    )

    return _deduplicate_path_ids(paths)


def _build_target_career_path_from_dict(
    data: dict[str, Any],
    fallback_index: int,
) -> TargetCareerPath:
    """
    Validate and coerce one path dictionary into TargetCareerPath.
    """

    title = _safe_string(data.get("title"), f"Career Path {fallback_index + 1}")
    path_id = _slugify(_safe_string(data.get("path_id")) or title)

    suggested_roles = _safe_string_list(data.get("suggested_roles"))
    search_seed_terms = _safe_string_list(data.get("search_seed_terms"))
    keywords = _safe_string_list(data.get("keywords"))
    metadata = _safe_metadata(data.get("metadata"))

    search_seed_terms = _merge_unique_strings(
        search_seed_terms,
        metadata["search_seed_terms_en"],
        metadata["search_seed_terms_zh"],
        suggested_roles,
    )

    suggested_roles = _merge_unique_strings(
        suggested_roles,
        metadata["role_keywords_en"],
        metadata["role_keywords_zh"],
    )

    keywords = _merge_unique_strings(
        keywords,
        metadata["role_keywords_en"],
        metadata["role_keywords_zh"],
    )

    if not search_seed_terms:
        search_seed_terms = [title]

    if not suggested_roles:
        suggested_roles = [title]

    return TargetCareerPath(
        path_id=path_id,
        title=title,
        category=_parse_career_path_category(data.get("category")),
        description=_safe_string(data.get("description")),
        fit_score=_parse_fit_score(data.get("fit_score")),
        rationale=_safe_string_list(data.get("rationale")),
        keywords=keywords,
        suggested_roles=suggested_roles,
        search_seed_terms=search_seed_terms,
        metadata=metadata,
    )


def _safe_metadata(value: Any) -> dict[str, Any]:
    """
    Return normalized metadata with required bilingual search fields.
    """

    raw_metadata = value if isinstance(value, dict) else {}

    normalized_metadata = {
        "path_type": _normalize_choice(
            raw_metadata.get("path_type"),
            {
                "core_match",
                "bridge_role",
                "stretch_opportunity",
                "exploratory_opportunity",
            },
            "exploratory_opportunity",
        ),
        "confidence": _normalize_choice(
            raw_metadata.get("confidence"),
            {"high", "medium", "low"},
            "medium",
        ),
        "search_seed_terms_zh": _safe_string_list(
            raw_metadata.get("search_seed_terms_zh")
        ),
        "search_seed_terms_en": _safe_string_list(
            raw_metadata.get("search_seed_terms_en")
        ),
        "role_keywords_zh": _safe_string_list(raw_metadata.get("role_keywords_zh")),
        "role_keywords_en": _safe_string_list(raw_metadata.get("role_keywords_en")),
        "profile_evidence": _safe_string_list(raw_metadata.get("profile_evidence")),
        "preference_evidence": _safe_string_list(
            raw_metadata.get("preference_evidence")
        ),
        "risk_flags": _safe_string_list(raw_metadata.get("risk_flags")),
        "constraint_notes": _safe_string_list(raw_metadata.get("constraint_notes")),
        "why_not_higher": _safe_string_list(raw_metadata.get("why_not_higher")),
    }

    for key, item in raw_metadata.items():
        if key not in normalized_metadata:
            normalized_metadata[str(key)] = item

    return normalized_metadata


def _extract_llm_response_text(response: Any) -> str | None:
    """
    Extract message content from an OpenAI-compatible chat completion response.
    """

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
        text_parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        ]
        return "".join(text_parts)

    return None


def _build_empty_response_debug_info(
    response: Any,
    client: TargetCareerPathClient,
) -> str:
    """
    Build concise debug context for empty LLM responses.
    """

    choices = getattr(response, "choices", None)

    if hasattr(response, "model_dump_json"):
        raw_response = response.model_dump_json(indent=2)
    else:
        raw_response = repr(response)

    return (
        f"provider: {client.provider}\n"
        f"model: {client.model}\n"
        f"response_has_choices: {bool(choices)}\n"
        f"raw_response_preview: {raw_response[:500]!r}"
    )


def _normalize_json_response_text(response_text: str) -> str:
    """
    Strip whitespace, remove markdown fences, and recover a JSON object.
    """

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
    """
    Extract the first balanced top-level JSON object from surrounding text.
    """

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


def _parse_career_path_category(value: Any) -> CareerPathCategory:
    """
    Parse a category string into the existing enum.
    """

    try:
        return CareerPathCategory(str(value))
    except ValueError:
        return CareerPathCategory.UNKNOWN


def _parse_fit_score(value: Any) -> float:
    """
    Parse fit score on the existing 0-100 project scale.
    """

    try:
        fit_score = float(value)
    except (TypeError, ValueError):
        fit_score = 0.0

    return round(min(max(fit_score, 0.0), 100.0), 2)


def _safe_string(value: Any, default: str = "") -> str:
    """
    Return a string value while treating None as missing.
    """

    if value is None:
        return default

    return str(value)


def _safe_string_list(value: Any) -> list[str]:
    """
    Return a list of strings, dropping empty values.
    """

    if not isinstance(value, list):
        return []

    return [
        str(item).strip()
        for item in value
        if item is not None and str(item).strip()
    ]


def _normalize_choice(value: Any, allowed_values: set[str], default: str) -> str:
    """
    Normalize a constrained string field.
    """

    normalized = str(value).strip().lower()

    if normalized in allowed_values:
        return normalized

    return default


def _merge_unique_strings(*groups: list[str]) -> list[str]:
    """
    Merge string lists while preserving order.
    """

    seen: set[str] = set()
    merged: list[str] = []

    for group in groups:
        for item in group:
            normalized = item.strip()
            key = normalized.lower()

            if not normalized or key in seen:
                continue

            seen.add(key)
            merged.append(normalized)

    return merged


def _deduplicate_path_ids(
    paths: list[TargetCareerPath],
) -> list[TargetCareerPath]:
    """
    Ensure path IDs are unique after LLM generation.
    """

    seen: dict[str, int] = {}

    for path in paths:
        count = seen.get(path.path_id, 0)
        seen[path.path_id] = count + 1

        if count > 0:
            path.path_id = f"{path.path_id}_{count + 1}"

    return paths


def _slugify(text: str) -> str:
    """
    Convert text into a stable ID-friendly string.
    """

    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:80] if slug else "career_path"
