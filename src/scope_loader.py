import json
from pathlib import Path
from typing import Any

from src.models import SearchScope
from src.search_scope_resolution import load_effective_search_scope


def load_search_scope_from_json(scope_path: Path) -> SearchScope:
    """
    Load search scope from a JSON file.
    """

    if not scope_path.exists():
        raise FileNotFoundError(
            f"Search scope file not found: {scope_path}"
        )

    with scope_path.open("r", encoding="utf-8") as file:
        scope_data = json.load(file)

    return SearchScope.from_dict(scope_data)


def load_effective_search_scope_from_json(
    scope_path: Path,
    user_preferences: dict[str, Any],
) -> SearchScope:
    """
    Load technical SearchScope config and derive semantic fields.
    """

    return load_effective_search_scope(
        scope_path=scope_path,
        user_preferences=user_preferences,
    )
