CAREER_KEYWORDS = {
    "strategy": 4,
    "operations": 3,
    "go-to-market": 4,
    "growth": 3,
    "vc": 4,
    "investment": 4,
    "startup": 3,
    "consulting": 3,
    "generative ai": 4,
    "ai": 3,
    "technology": 3,
    "m&a": 4,
    "advisory": 3,
    "analyst": 2,
}


def score_signal(signal: dict) -> dict:
    """
    Add a relevance score and reason to one signal.

    Score meaning:
    1 = low relevance
    2 = somewhat relevant
    3 = relevant
    4 = highly relevant
    5 = urgent / extremely relevant
    """
    text = f"{signal.get('title', '')} {signal.get('content', '')}".lower()

    matched_keywords = [
        keyword for keyword in CAREER_KEYWORDS
        if keyword in text
    ]

    if not matched_keywords:
        score = 1
        reason = "No strong career-direction keywords found."
    else:
        highest_keyword_score = max(
            CAREER_KEYWORDS[keyword] for keyword in matched_keywords
        )
        score = min(5, highest_keyword_score + len(matched_keywords) // 3)
        reason = "Matched keywords: " + ", ".join(matched_keywords)

    scored_signal = signal.copy()
    scored_signal["relevance_score"] = score
    scored_signal["score_reason"] = reason

    return scored_signal


def score_signals(signals: list[dict]) -> list[dict]:
    """
    Score all collected signals.
    """
    return [score_signal(signal) for signal in signals]