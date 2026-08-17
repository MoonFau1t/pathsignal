import hashlib

from src.models import (
    AIFilterResult,
    CareerSignal,
    RawItem,
    SignalCategory,
)
from src.signal_identity import build_signal_id


def normalize_raw_items_to_career_signals(
    filtered_raw_items: list[RawItem],
    ai_filter_results: list[AIFilterResult],
) -> list[CareerSignal]:
    """
    Convert filtered RawItem objects into normalized CareerSignal objects.

    Phase 10 does not call an LLM.
    It uses AI Filter results and deterministic rules to create CareerSignal.
    """

    filter_result_map = {
        result.raw_item_fingerprint: result
        for result in ai_filter_results
    }

    career_signals: list[CareerSignal] = []

    for raw_item in filtered_raw_items:
        raw_item_fingerprint = _fingerprint_raw_item(raw_item)
        filter_result = filter_result_map.get(raw_item_fingerprint)

        category = _resolve_signal_category(
            raw_item=raw_item,
            filter_result=filter_result,
        )

        signal_id = build_signal_id(raw_item)

        summary = _build_signal_summary(
            raw_item=raw_item,
            filter_result=filter_result,
        )

        relevance_score = _build_relevance_score(filter_result)

        career_signals.append(
            CareerSignal(
                signal_id=signal_id,
                category=category,
                title=_clean_text(raw_item.title),
                organization=_clean_text(raw_item.organization),
                url=raw_item.url,
                published_at=raw_item.published_at,
                summary=summary,
                source_type=raw_item.source_type,
                relevance_score=relevance_score,
                metadata={
                    "normalizer": "rule_based_phase_10",
                    "raw_item_fingerprint": raw_item_fingerprint,
                    "matched_career_path_ids": (
                        filter_result.matched_career_path_ids
                        if filter_result is not None
                        else []
                    ),
                    "ai_filter_confidence": (
                        filter_result.confidence
                        if filter_result is not None
                        else None
                    ),
                    "ai_filter_reason": (
                        filter_result.reason
                        if filter_result is not None
                        else ""
                    ),
                    "ai_filter_action": (
                        filter_result.action
                        if filter_result is not None
                        else ""
                    ),
                    "raw_item_metadata": raw_item.metadata,
                },
            )
        )

    return career_signals


def _resolve_signal_category(
    raw_item: RawItem,
    filter_result: AIFilterResult | None,
) -> SignalCategory:
    """
    Decide CareerSignal category.

    Priority:
    1. AI Filter suggested category
    2. Rule-based text guess
    """

    if (
        filter_result is not None
        and filter_result.suggested_category != SignalCategory.UNKNOWN
    ):
        return filter_result.suggested_category

    text = (
        f"{raw_item.title} "
        f"{raw_item.organization} "
        f"{raw_item.raw_text} "
        f"{raw_item.url}"
    ).lower()

    if any(term in text for term in ["job", "career", "hiring", "role", "analyst"]):
        return SignalCategory.JOB

    if any(term in text for term in ["funding", "raised", "raises", "series a", "series b"]):
        return SignalCategory.FUNDING

    if any(term in text for term in ["market", "trend", "demand", "industry"]):
        return SignalCategory.MARKET_TREND

    if any(term in text for term in ["company", "startup", "launch", "expansion", "expands"]):
        return SignalCategory.COMPANY

    if any(term in text for term in ["news", "announced", "report"]):
        return SignalCategory.NEWS

    return SignalCategory.UNKNOWN


def _build_signal_summary(
    raw_item: RawItem,
    filter_result: AIFilterResult | None,
) -> str:
    """
    Build a short normalized summary for CareerSignal.
    """

    raw_summary = _clean_text(raw_item.raw_text)

    if not raw_summary:
        raw_summary = _clean_text(raw_item.title)

    if filter_result is None:
        return raw_summary

    reason = _clean_text(filter_result.reason)

    if reason:
        return f"{raw_summary} Normalizer note: {reason}"

    return raw_summary


def _build_relevance_score(
    filter_result: AIFilterResult | None,
) -> float | None:
    """
    Convert AI Filter confidence into CareerSignal relevance score.

    Phase 12 will replace or enrich this with rule-based scoring.
    """

    if filter_result is None:
        return None

    return round(filter_result.confidence * 100, 2)


def _fingerprint_raw_item(raw_item: RawItem) -> str:
    """
    Create the same stable fingerprint format used by AI Filter.
    """

    fingerprint_source = (
        f"{raw_item.source_type.value}|"
        f"{raw_item.title}|"
        f"{raw_item.url}|"
        f"{raw_item.raw_text[:200]}"
    )

    return hashlib.sha256(
        fingerprint_source.encode("utf-8")
    ).hexdigest()[:16]


def _clean_text(text: str) -> str:
    """
    Normalize whitespace in text fields.
    """

    return " ".join(str(text).split())
