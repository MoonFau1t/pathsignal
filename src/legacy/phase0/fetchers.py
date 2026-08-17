from datetime import datetime, timezone


def fetch_mock_signals() -> list[dict]:
    """
    Return mock career intelligence signals.

    In the future, this function will fetch real information from:
    - RSS feeds
    - company career pages
    - job boards
    - news websites

    For now, we use fixed mock data to test the pipeline.
    """
    collected_at = datetime.now(timezone.utc).isoformat()

    return [
        {
            "title": "OpenAI opens Strategy & Operations role",
            "source": "Mock Careers Page",
            "url": "https://example.com/openai-strategy-role",
            "category": "job",
            "content": (
                "OpenAI is hiring for a Strategy & Operations role focused on "
                "go-to-market planning, business growth, and cross-functional execution."
            ),
            "collected_at": collected_at,
        },
        {
            "title": "Sequoia publishes new AI investment thesis",
            "source": "Mock VC Blog",
            "url": "https://example.com/sequoia-ai-thesis",
            "category": "vc_news",
            "content": (
                "Sequoia released a new article about AI infrastructure, "
                "startup formation, and investment opportunities in applied AI."
            ),
            "collected_at": collected_at,
        },
        {
            "title": "McKinsey launches report on generative AI transformation",
            "source": "Mock Consulting Insights",
            "url": "https://example.com/mckinsey-genai-report",
            "category": "consulting_news",
            "content": (
                "McKinsey published a report on how enterprises adopt generative AI, "
                "with implications for technology consulting and corporate strategy."
            ),
            "collected_at": collected_at,
        },
        {
            "title": "Boutique investment bank hiring technology analyst",
            "source": "Mock Job Board",
            "url": "https://example.com/boutique-bank-tech-analyst",
            "category": "job",
            "content": (
                "A boutique investment bank is hiring an analyst for technology M&A, "
                "startup financing, and strategic advisory projects."
            ),
            "collected_at": collected_at,
        },
    ]