from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.career_intelligence_interpretation import (
    CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
    EMPTY_INPUT_WARNING,
    CareerIntelligenceInterpretationError,
    CareerIntelligenceInterpretationResult,
    InterpretationRequestContext,
)
from src.career_signal_routing import CareerSignalRoutingResult
from src.models import TargetCareerPath


InterpretationExecutor = Callable[
    [InterpretationRequestContext],
    CareerIntelligenceInterpretationResult,
]


def interpret_routed_intelligence(
    *,
    routing_result: CareerSignalRoutingResult,
    target_career_paths: tuple[TargetCareerPath, ...] | list[TargetCareerPath],
    user_preferences: dict[str, Any],
    interpretation_executor: InterpretationExecutor | None,
) -> CareerIntelligenceInterpretationResult:
    """Interpret one current-run Intelligence batch through the Stage 4B API."""

    if not isinstance(routing_result, CareerSignalRoutingResult):
        raise CareerIntelligenceInterpretationError(
            "Stage 4 runtime requires a CareerSignalRoutingResult."
        )
    if not isinstance(user_preferences, dict):
        raise CareerIntelligenceInterpretationError(
            "Stage 4 runtime requires current UserPreferences."
        )

    intelligence_signals = routing_result.intelligence
    if not intelligence_signals:
        return CareerIntelligenceInterpretationResult(
            schema_version=CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
            input_signal_ids=(),
            themes=(),
            key_developments=(),
            career_implications=(),
            warnings=(EMPTY_INPUT_WARNING,),
        )

    if interpretation_executor is None:
        raise CareerIntelligenceInterpretationError(
            "Career Intelligence Interpretation is not configured for a "
            "non-empty Intelligence batch."
        )

    context = InterpretationRequestContext(
        intelligence_signals=tuple(intelligence_signals),
        target_career_paths=tuple(target_career_paths),
        user_preferences=user_preferences,
    )
    result = interpretation_executor(context)
    if not isinstance(result, CareerIntelligenceInterpretationResult):
        raise CareerIntelligenceInterpretationError(
            "Career Intelligence Interpretation executor returned an invalid result."
        )
    return result
