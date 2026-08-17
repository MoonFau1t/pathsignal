from pathlib import Path

from fetchers import fetch_mock_signals
from scorer import score_signals
from storage import save_signals_json
from briefing import build_markdown_brief, write_markdown_brief


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "signals.json"
BRIEF_PATH = PROJECT_ROOT / "output" / "daily_brief.md"


def main() -> None:
    print("Starting AgentWorkflow v0.1...")

    raw_signals = fetch_mock_signals()
    print(f"Fetched {len(raw_signals)} mock signals.")

    scored_signals = score_signals(raw_signals)
    print("Scored signals.")

    saved_data_path = save_signals_json(scored_signals, DATA_PATH)
    print(f"Saved signals to: {saved_data_path}")

    markdown = build_markdown_brief(scored_signals)
    saved_brief_path = write_markdown_brief(markdown, BRIEF_PATH)
    print(f"Generated brief at: {saved_brief_path}")

    print("Done.")


if __name__ == "__main__":
    main()