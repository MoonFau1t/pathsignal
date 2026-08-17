import json
from pathlib import Path
from typing import Any

from src.models import UserProfile


def load_user_profile_from_json(profile_path: Path) -> UserProfile:
    """
    Load user profile from a JSON file.
    """

    if not profile_path.exists():
        raise FileNotFoundError(
            f"User profile file not found: {profile_path}"
        )

    with profile_path.open("r", encoding="utf-8") as file:
        profile_data = json.load(file)

    return UserProfile.from_dict(profile_data)


def load_extracted_user_profile_from_json(profile_path: Path) -> UserProfile:
    """
    Load a UserProfile from an extraction wrapper JSON file.
    """

    if not profile_path.exists():
        raise FileNotFoundError(
            f"Extracted user profile file not found: {profile_path}"
        )

    with profile_path.open("r", encoding="utf-8") as file:
        profile_data = json.load(file)

    if not isinstance(profile_data, dict):
        raise ValueError(
            f"Extracted user profile file must contain a JSON object: {profile_path}"
        )

    user_profile_data = profile_data.get("user_profile", profile_data)

    if not isinstance(user_profile_data, dict):
        raise ValueError(
            "Extracted user profile JSON must contain a user_profile object."
        )

    return UserProfile.from_dict(user_profile_data)


def load_user_preferences_from_json(preferences_path: Path) -> dict[str, Any]:
    """
    Load the authoritative career preference and constraint model.
    """

    if not preferences_path.exists():
        raise FileNotFoundError(
            f"User preferences file not found: {preferences_path}"
        )

    with preferences_path.open("r", encoding="utf-8") as file:
        preferences_data = json.load(file)

    if not isinstance(preferences_data, dict):
        raise ValueError(
            f"User preferences file must contain a JSON object: {preferences_path}"
        )

    return preferences_data
