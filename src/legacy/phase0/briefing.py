from datetime import datetime
from pathlib import Path


def build_markdown_brief(signals: list[dict]) -> str:
    """
    Build a Markdown career intelligence brief from scored signals.
    """
    sorted_signals = sorted(
        signals,
        key=lambda signal: signal.get("relevance_score", 0),
        reverse=True,
    )

    top_signals = [
        signal for signal in sorted_signals
        if signal.get("relevance_score", 0) >= 3
    ]

    lines = [
        "# Career Intelligence Brief",
        "",
        f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        f"- Collected signals: {len(signals)}",
        f"- Relevant signals: {len(top_signals)}",
        "",
        "## Top Signals",
        "",
    ]

    if not top_signals:
        lines.append("No highly relevant signals found today.")
        lines.append("")
    else:
        for index, signal in enumerate(top_signals, start=1):
            lines.extend(
                [
                    f"### {index}. {signal['title']}",
                    "",
                    f"- Source: {signal['source']}",
                    f"- Category: {signal['category']}",
                    f"- Relevance score: {signal['relevance_score']}",
                    f"- Reason: {signal['score_reason']}",
                    f"- URL: {signal['url']}",
                    "",
                    "**Why it matters:**",
                    "",
                    signal["content"],
                    "",
                ]
            )

    lines.extend(
        [
            "## Suggested Actions",
            "",
            "- Review the highest-scoring opportunities first.",
            "- Save links that are directly related to strategy, VC, consulting, or technology finance.",
            "- Ignore low-relevance signals unless they appear repeatedly across sources.",
            "",
        ]
    )

    return "\n".join(lines)


def write_markdown_brief(markdown: str, output_path: Path) -> Path:
    """
    Write the Markdown brief to a local file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    return output_path