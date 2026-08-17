import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.models import SearchScope


class SearchScopeResolutionError(Exception):
    """
    Raised when an effective SearchScope cannot be derived safely.
    """


def derive_search_locations(user_preferences: Mapping[str, Any]) -> list[str]:
    """
    Derive search geography from explicit semantic location preferences.

    Current mapping:
    - location_preferences.preferred_cities are primary search locations.
    - location_preferences.region_priority is included only when overseas work
      or short-term overseas assignment willingness is explicit.
    - remote_interview_willingness does not imply remote work search.
    """

    location_preferences = _required_mapping(
        user_preferences,
        "location_preferences",
    )
    locations = _string_list(
        location_preferences.get("preferred_cities"),
        "location_preferences.preferred_cities",
    )

    overseas_work_willingness = bool(
        location_preferences.get("overseas_work_willingness")
    )
    short_term_overseas_willingness = bool(
        location_preferences.get("short_term_overseas_assignment_willingness")
    )

    if overseas_work_willingness or short_term_overseas_willingness:
        locations.extend(
            _string_list(
                location_preferences.get("region_priority", []),
                "location_preferences.region_priority",
                required=False,
            )
        )

    work_mode_preference = str(
        location_preferences.get("work_mode_preference", "")
    ).strip().lower()

    if work_mode_preference == "remote":
        locations.append("Remote")

    return _dedupe_preserving_order(locations)


def derive_search_seniority_levels(
    user_preferences: Mapping[str, Any],
) -> list[str]:
    """
    Derive search seniority from explicit early-career preferences.

    Current mapping:
    - career_status.career_stage=new_graduate contributes entry_level.
    - seniority_preferences.preferred_levels are normalized to snake_case.
    - accept_internship_to_full_time contributes intern.
    - 3-5 year stretch tolerance contributes associate.
    """

    career_status = _required_mapping(user_preferences, "career_status")
    seniority_preferences = _required_mapping(
        user_preferences,
        "seniority_preferences",
    )
    experience_tolerance = _required_mapping(
        user_preferences,
        "experience_requirement_tolerance",
    )

    levels: list[str] = []

    career_stage = str(career_status.get("career_stage", "")).strip().lower()

    if career_stage == "new_graduate":
        levels.append("entry_level")

    preferred_levels = _string_list(
        seniority_preferences.get("preferred_levels"),
        "seniority_preferences.preferred_levels",
    )
    levels.extend(_normalize_level(level) for level in preferred_levels)

    if bool(seniority_preferences.get("accept_internship_to_full_time")):
        levels.append("intern")

    three_to_five = _mapping_or_empty(
        experience_tolerance.get("three_to_five_years")
    )
    if three_to_five.get("default_action") == "keep_only_if_highly_matched":
        levels.append("associate")

    return _dedupe_preserving_order(levels)


def build_effective_search_scope(
    user_preferences: Mapping[str, Any],
    search_scope_config: Mapping[str, Any],
    *,
    allow_legacy_matching_semantic_fields: bool = True,
) -> SearchScope:
    """
    Merge derived semantic scope values with technical search configuration.
    """

    derived_locations = derive_search_locations(user_preferences)
    derived_seniority_levels = derive_search_seniority_levels(user_preferences)
    config_payload = deepcopy(dict(search_scope_config))

    _validate_legacy_semantic_field(
        config_payload=config_payload,
        field_name="locations",
        derived_value=derived_locations,
        allow_matching=allow_legacy_matching_semantic_fields,
    )
    _validate_legacy_semantic_field(
        config_payload=config_payload,
        field_name="seniority_levels",
        derived_value=derived_seniority_levels,
        allow_matching=allow_legacy_matching_semantic_fields,
    )

    config_payload["locations"] = derived_locations
    config_payload["seniority_levels"] = derived_seniority_levels

    try:
        return SearchScope.from_dict(config_payload)
    except Exception as error:
        raise SearchScopeResolutionError(
            "Effective SearchScope construction failed."
        ) from error


def load_search_scope_config_from_json(scope_path: Path) -> dict[str, Any]:
    """
    Load the technical SearchScope configuration payload.
    """

    if not scope_path.exists():
        raise FileNotFoundError(f"Search scope file not found: {scope_path}")

    with scope_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise SearchScopeResolutionError(
            f"Search scope config must contain a JSON object: {scope_path}"
        )

    return payload


def load_effective_search_scope(
    scope_path: Path,
    user_preferences: Mapping[str, Any],
) -> SearchScope:
    """
    Load technical scope config and add semantic values from UserPreferences.
    """

    return build_effective_search_scope(
        user_preferences=user_preferences,
        search_scope_config=load_search_scope_config_from_json(scope_path),
    )


def _required_mapping(
    mapping: Mapping[str, Any],
    field_name: str,
) -> Mapping[str, Any]:
    value = mapping.get(field_name)

    if not isinstance(value, Mapping):
        raise SearchScopeResolutionError(
            f"Cannot derive SearchScope: {field_name} must be an object."
        )

    return value


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_list(
    value: Any,
    field_name: str,
    *,
    required: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        if required:
            raise SearchScopeResolutionError(
                f"Cannot derive SearchScope: {field_name} must be a list."
            )
        return []

    items = [
        str(item).strip()
        for item in value
        if item is not None and str(item).strip()
    ]

    if required and not items:
        raise SearchScopeResolutionError(
            f"Cannot derive SearchScope: {field_name} must not be empty."
        )

    return items


def _normalize_level(value: str) -> str:
    normalized = value.strip().lower().replace("-", " ")
    return "_".join(normalized.split())


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []

    for value in values:
        key = value.casefold()

        if key in seen:
            continue

        seen.add(key)
        deduped.append(value)

    return deduped


def _validate_legacy_semantic_field(
    *,
    config_payload: dict[str, Any],
    field_name: str,
    derived_value: list[str],
    allow_matching: bool,
) -> None:
    if field_name not in config_payload:
        return

    configured_value = _string_list(config_payload[field_name], field_name)

    if configured_value != derived_value:
        raise SearchScopeResolutionError(
            "Legacy SearchScope semantic field conflicts with "
            f"UserPreferences-derived {field_name}: "
            f"configured={configured_value!r}, derived={derived_value!r}."
        )

    if not allow_matching:
        raise SearchScopeResolutionError(
            f"Legacy SearchScope semantic field is not allowed: {field_name}."
        )
