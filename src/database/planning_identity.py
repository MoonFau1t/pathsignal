from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from src.models import SearchPlan, SearchQuery, SearchScope, TargetCareerPath, UserProfile


def canonical_json(value: Any) -> str:
    """
    Serialize a planning value as deterministic JSON.
    """

    return json.dumps(
        _to_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def hash_canonical_value(value: Any) -> str:
    """
    Hash a value after canonical planning serialization.
    """

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def serialize_user_profile(user_profile: UserProfile) -> str:
    return canonical_json(user_profile)


def hash_user_profile(user_profile: UserProfile) -> str:
    """
    Build the immutable UserProfile snapshot content hash.
    """

    return hash_canonical_value(user_profile)


def build_planning_input_fingerprint(
    *,
    profile_content_hash: str,
    user_preferences: dict[str, Any],
    search_scope: SearchScope,
    model_provider: str | None,
    model_name: str | None,
    prompt_version: str | None,
    generator_config: dict[str, Any] | None = None,
) -> str:
    """
    Hash material inputs that affect planning output.
    """

    return hash_canonical_value(
        {
            "profile_content_hash": profile_content_hash,
            "user_preferences": user_preferences,
            "search_scope": search_scope,
            "model_provider": model_provider,
            "model_name": model_name,
            "prompt_version": prompt_version,
            "generator_config": generator_config or {},
        }
    )


def build_planning_context(
    *,
    profile_content_hash: str,
    user_preferences: dict[str, Any],
    search_scope: SearchScope,
    model_provider: str | None,
    model_name: str | None,
    prompt_version: str | None,
    generator_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Return the material planning context stored for provenance.
    """

    return {
        "profile_content_hash": profile_content_hash,
        "user_preferences": user_preferences,
        "search_scope": search_scope,
        "model_provider": model_provider,
        "model_name": model_name,
        "prompt_version": prompt_version,
        "generator_config": generator_config or {},
    }


def build_planning_output_hash(
    *,
    target_career_paths: list[TargetCareerPath],
    search_queries: list[SearchQuery],
    search_plans: list[SearchPlan],
    extra_outputs: dict[str, Any] | None = None,
) -> str:
    """
    Hash the complete ordered planning output.
    """

    return hash_canonical_value(
        {
            "target_career_paths": target_career_paths,
            "search_queries": search_queries,
            "search_plans": search_plans,
            "extra_outputs": extra_outputs or {},
        }
    )


def _to_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value

    if is_dataclass(value) and not isinstance(value, type):
        return _to_json_value(asdict(value))

    if isinstance(value, dict):
        return {
            str(key): _to_json_value(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            _to_json_value(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            _to_json_value(item)
            for item in value
        ]

    return value
