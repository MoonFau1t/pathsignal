from src.models import RawItem, SourceType, utc_now_iso


def load_mock_raw_items() -> list[RawItem]:
    """
    Load mock raw items for V1 Phase 2.

    These mock items now use the RawItem data model instead of plain dictionaries.
    """

    current_time = utc_now_iso()

    return [
        RawItem(
            source_type=SourceType.MOCK_JOB,
            title="Corporate Strategy Analyst - AI Startup",
            organization="NovaAI Labs",
            url="https://example.com/jobs/corporate-strategy-ai",
            published_at=current_time,
            raw_text=(
                "NovaAI Labs is hiring a Corporate Strategy Analyst to support "
                "market research, competitive analysis, and AI product expansion strategy."
            ),
            metadata={
                "mock_reason": "career opportunity example",
                "expected_category": "job",
            },
        ),
        RawItem(
            source_type=SourceType.MOCK_JOB,
            title="Venture Capital Analyst Intern",
            organization="FutureBridge Capital",
            url="https://example.com/jobs/vc-analyst-intern",
            published_at=current_time,
            raw_text=(
                "FutureBridge Capital is looking for a VC Analyst Intern to evaluate "
                "early-stage AI, SaaS, and enterprise software startups."
            ),
            metadata={
                "mock_reason": "VC opportunity example",
                "expected_category": "job",
            },
        ),
        RawItem(
            source_type=SourceType.MOCK_NEWS,
            title="AI Consulting Demand Rises Among Traditional Enterprises",
            organization="Mock Business Daily",
            url="https://example.com/news/ai-consulting-demand",
            published_at=current_time,
            raw_text=(
                "Traditional enterprises are increasing spending on AI transformation, "
                "creating stronger demand for AI strategy consultants."
            ),
            metadata={
                "mock_reason": "industry trend example",
                "expected_category": "market_trend",
            },
        ),
    ]