import json
from pathlib import Path
from typing import Any


def convert_to_json_ready(data: Any) -> Any:
    """
    Convert supported project objects into JSON-ready data.

    Objects with a to_dict() method will be converted automatically.
    Lists and dictionaries will be recursively converted.
    """

    if hasattr(data, "to_dict") and callable(data.to_dict):
        return data.to_dict()

    if isinstance(data, list):
        return [convert_to_json_ready(item) for item in data]

    if isinstance(data, dict):
        return {
            key: convert_to_json_ready(value)
            for key, value in data.items()
        }

    return data


def save_json(data: Any, output_path: Path) -> Path:
    """
    Save project data as a JSON file.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)

    json_ready_data = convert_to_json_ready(data)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(json_ready_data, file, ensure_ascii=False, indent=2)

    return output_path


def load_json(input_path: Path) -> Any:
    """
    Load a JSON file.
    """

    with input_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_text(text: str, output_path: Path) -> Path:
    """
    Save text to a UTF-8 file.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as file:
        file.write(text)

    return output_path
