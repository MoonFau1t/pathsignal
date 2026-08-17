from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.career_signal_priority import ScoredCareerSignal
from src.models import SignalCategory


UNSUPPORTED_OR_UNKNOWN_CATEGORY_REASON = "unsupported_or_unknown_category"

OPPORTUNITY_CATEGORIES = frozenset({SignalCategory.JOB})
INTELLIGENCE_CATEGORIES = frozenset(
    {
        SignalCategory.NEWS,
        SignalCategory.COMPANY,
        SignalCategory.FUNDING,
        SignalCategory.MARKET_TREND,
    }
)


@dataclass(frozen=True)
class UnroutedCareerSignal:
    scored_career_signal: ScoredCareerSignal
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scored_career_signal": self.scored_career_signal.to_dict(),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CareerSignalRoutingResult:
    opportunities: tuple[ScoredCareerSignal, ...] = ()
    intelligence: tuple[ScoredCareerSignal, ...] = ()
    unrouted: tuple[UnroutedCareerSignal, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunities": [
                scored.to_dict()
                for scored in self.opportunities
            ],
            "intelligence": [
                scored.to_dict()
                for scored in self.intelligence
            ],
            "unrouted": [
                unrouted.to_dict()
                for unrouted in self.unrouted
            ],
        }


def route_scored_career_signals(
    scored_career_signals: tuple[ScoredCareerSignal, ...] | list[ScoredCareerSignal],
) -> CareerSignalRoutingResult:
    opportunities: list[ScoredCareerSignal] = []
    intelligence: list[ScoredCareerSignal] = []
    unrouted: list[UnroutedCareerSignal] = []

    for scored in scored_career_signals:
        category = _signal_category(scored.career_signal.category)
        if category in OPPORTUNITY_CATEGORIES:
            opportunities.append(scored)
        elif category in INTELLIGENCE_CATEGORIES:
            intelligence.append(scored)
        else:
            unrouted.append(
                UnroutedCareerSignal(
                    scored_career_signal=scored,
                    reason=UNSUPPORTED_OR_UNKNOWN_CATEGORY_REASON,
                )
            )

    return CareerSignalRoutingResult(
        opportunities=tuple(_sort_by_priority(opportunities)),
        intelligence=tuple(_sort_by_priority(intelligence)),
        unrouted=tuple(_sort_unrouted(unrouted)),
    )


def _sort_by_priority(
    scored_career_signals: list[ScoredCareerSignal],
) -> list[ScoredCareerSignal]:
    return sorted(
        scored_career_signals,
        key=lambda scored: (
            -float(scored.priority_score.priority_score),
            scored.career_signal.signal_id,
        ),
    )


def _sort_unrouted(
    unrouted: list[UnroutedCareerSignal],
) -> list[UnroutedCareerSignal]:
    return sorted(
        unrouted,
        key=lambda item: (
            -float(item.scored_career_signal.priority_score.priority_score),
            item.scored_career_signal.career_signal.signal_id,
        ),
    )


def _signal_category(value: SignalCategory | str | Any) -> SignalCategory | None:
    if isinstance(value, SignalCategory):
        return value
    try:
        return SignalCategory(str(value))
    except ValueError:
        return None
