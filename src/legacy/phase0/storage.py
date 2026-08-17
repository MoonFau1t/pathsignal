import json
from pathlib import Path


def save_signals_json(signals: list[dict], output_path: Path) -> Path:
    """
    Save collected and scored signals to a local JSON file.

    This is the simplest version of storage.
    Later, we will replace or extend this with SQLite.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        json.dumps(signals, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return output_path