from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any

from src.career_intelligence_interpretation import (
    CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
    CareerImplicationInterpretation,
    CareerIntelligenceInterpretationResult,
    InterpretationConfidence,
    KeyDevelopmentInterpretation,
    ThemeInterpretation,
)
from src.career_signal_priority import ScoredCareerSignal
from src.career_signal_routing import CareerSignalRoutingResult
from src.career_signal_scoring import PriorityScoreResult, PriorityTier
from src.models import CareerSignal, SignalCategory, TargetCareerPath
from src.priority_assessment import (
    ALLOWED_SEMANTIC_SCORES,
    OPPORTUNITY_COMPONENTS,
    PRIORITY_ASSESSMENT_SCHEMA_VERSION,
    AssessmentProfile,
    ComponentStatus,
    PriorityAssessmentResult,
    SemanticComponentResult,
)


CAREER_INTELLIGENCE_BRIEF_SCHEMA_VERSION = "career_intelligence_brief_v1"


class CareerIntelligenceBriefError(Exception):
    """Raised when deterministic Brief assembly cannot satisfy its contract."""


@dataclass(frozen=True)
class BriefCareerPath:
    path_id: str
    title: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_id": self.path_id,
            "title": self.title,
        }


@dataclass(frozen=True)
class BriefOpportunity:
    signal_id: str
    title: str
    organization: str | None
    summary: str | None
    url: str | None
    published_at: str | None
    priority_score: float
    priority_tier: PriorityTier
    matched_career_paths: tuple[BriefCareerPath, ...]
    user_policy_fit_reason: str | None
    opportunity_feasibility_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "title": self.title,
            "organization": self.organization,
            "summary": self.summary,
            "url": self.url,
            "published_at": self.published_at,
            "priority_score": self.priority_score,
            "priority_tier": self.priority_tier.value,
            "matched_career_paths": [
                path.to_dict() for path in self.matched_career_paths
            ],
            "user_policy_fit_reason": self.user_policy_fit_reason,
            "opportunity_feasibility_reason": (
                self.opportunity_feasibility_reason
            ),
        }


@dataclass(frozen=True)
class CareerIntelligenceBrief:
    schema_version: str
    generated_at: str
    opportunities: tuple[BriefOpportunity, ...]
    key_developments: tuple[KeyDevelopmentInterpretation, ...]
    themes: tuple[ThemeInterpretation, ...]
    career_implications: tuple[CareerImplicationInterpretation, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "opportunities": [
                opportunity.to_dict() for opportunity in self.opportunities
            ],
            "key_developments": [
                development.to_dict() for development in self.key_developments
            ],
            "themes": [theme.to_dict() for theme in self.themes],
            "career_implications": [
                implication.to_dict() for implication in self.career_implications
            ],
            "warnings": list(self.warnings),
        }


def build_career_intelligence_brief(
    *,
    routing_result: CareerSignalRoutingResult,
    interpretation: CareerIntelligenceInterpretationResult,
    target_career_paths: tuple[TargetCareerPath, ...] | list[TargetCareerPath] = (),
    generated_at: str,
) -> CareerIntelligenceBrief:
    """Assemble authoritative Stage 3 and Stage 4 results without semantic work."""

    if not isinstance(routing_result, CareerSignalRoutingResult):
        raise CareerIntelligenceBriefError(
            "Brief assembly requires a CareerSignalRoutingResult."
        )
    if not isinstance(routing_result.opportunities, tuple):
        raise CareerIntelligenceBriefError(
            "CareerSignalRoutingResult opportunities must be a tuple."
        )

    timestamp = _timezone_aware_timestamp(generated_at)
    path_titles = _target_career_path_titles(target_career_paths)
    _validate_interpretation(
        interpretation,
        routing_result=routing_result,
    )

    seen_signal_ids: set[str] = set()
    opportunities: list[BriefOpportunity] = []
    for scored in routing_result.opportunities:
        opportunity = _brief_opportunity(scored, path_titles=path_titles)
        if opportunity.signal_id in seen_signal_ids:
            raise CareerIntelligenceBriefError(
                "Brief Opportunities must have unique signal IDs."
            )
        seen_signal_ids.add(opportunity.signal_id)
        opportunities.append(opportunity)

    return CareerIntelligenceBrief(
        schema_version=CAREER_INTELLIGENCE_BRIEF_SCHEMA_VERSION,
        generated_at=timestamp,
        opportunities=tuple(opportunities),
        key_developments=interpretation.key_developments,
        themes=interpretation.themes,
        career_implications=interpretation.career_implications,
        warnings=interpretation.warnings,
    )


def _brief_opportunity(
    scored: ScoredCareerSignal,
    *,
    path_titles: dict[str, str],
) -> BriefOpportunity:
    if not isinstance(scored, ScoredCareerSignal):
        raise CareerIntelligenceBriefError(
            "Brief Opportunities must contain ScoredCareerSignal objects."
        )

    signal = scored.career_signal
    assessment = scored.priority_assessment
    score = scored.priority_score
    _validate_opportunity_identity(
        scored=scored,
        signal=signal,
        assessment=assessment,
        score=score,
    )
    _validate_opportunity_assessment(assessment)
    _validate_opportunity_score(score)

    matched_paths = tuple(
        BriefCareerPath(
            path_id=path_id,
            title=path_titles.get(path_id),
        )
        for path_id in score.matched_path_ids
    )
    return BriefOpportunity(
        signal_id=signal.signal_id,
        title=signal.title,
        organization=_nullable_display_string(
            signal.organization,
            field_name="Opportunity organization",
        ),
        summary=_nullable_display_string(
            signal.summary,
            field_name="Opportunity summary",
        ),
        url=_nullable_display_string(
            signal.url,
            field_name="Opportunity url",
        ),
        published_at=_nullable_display_string(
            signal.published_at,
            field_name="Opportunity published_at",
        ),
        priority_score=score.priority_score,
        priority_tier=score.tier,
        matched_career_paths=matched_paths,
        user_policy_fit_reason=_available_component_reason(
            assessment.components["user_policy_fit"]
        ),
        opportunity_feasibility_reason=_available_component_reason(
            assessment.components["opportunity_feasibility"]
        ),
    )


def _validate_opportunity_identity(
    *,
    scored: ScoredCareerSignal,
    signal: Any,
    assessment: Any,
    score: Any,
) -> None:
    if not isinstance(signal, CareerSignal):
        raise CareerIntelligenceBriefError(
            "Brief Opportunity career_signal must be a CareerSignal."
        )
    signal_id = _required_string(
        signal.signal_id,
        field_name="Opportunity signal_id",
    )
    _required_string(signal.title, field_name="Opportunity title")
    if signal.category != SignalCategory.JOB:
        raise CareerIntelligenceBriefError(
            "Brief Opportunities must contain only job CareerSignals."
        )
    if not isinstance(assessment, PriorityAssessmentResult):
        raise CareerIntelligenceBriefError(
            "Brief Opportunity requires a PriorityAssessmentResult."
        )
    if not isinstance(score, PriorityScoreResult):
        raise CareerIntelligenceBriefError(
            "Brief Opportunity requires a PriorityScoreResult."
        )
    if assessment.signal_id != signal_id or score.signal_id != signal_id:
        raise CareerIntelligenceBriefError(
            "Brief Opportunity upstream signal IDs do not match."
        )
    if (
        scored.assessment_profile != AssessmentProfile.OPPORTUNITY
        or assessment.assessment_profile != AssessmentProfile.OPPORTUNITY
        or score.profile != AssessmentProfile.OPPORTUNITY
    ):
        raise CareerIntelligenceBriefError(
            "Brief Opportunity upstream assessment profiles do not match."
        )


def _validate_opportunity_assessment(
    assessment: PriorityAssessmentResult,
) -> None:
    if assessment.schema_version != PRIORITY_ASSESSMENT_SCHEMA_VERSION:
        raise CareerIntelligenceBriefError(
            "Brief Opportunity Priority Assessment schema_version is invalid."
        )
    if not isinstance(assessment.components, dict) or set(
        assessment.components
    ) != set(OPPORTUNITY_COMPONENTS):
        raise CareerIntelligenceBriefError(
            "Brief Opportunity assessment components do not match the contract."
        )

    for name in OPPORTUNITY_COMPONENTS:
        component = assessment.components[name]
        if not isinstance(component, SemanticComponentResult):
            raise CareerIntelligenceBriefError(
                f"Brief Opportunity component {name!r} has an invalid shape."
            )
        _required_string(
            component.reason,
            field_name=f"Brief Opportunity component {name!r} reason",
        )
        if not isinstance(component.evidence, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in component.evidence
        ):
            raise CareerIntelligenceBriefError(
                f"Brief Opportunity component {name!r} evidence is invalid."
            )
        if component.status == ComponentStatus.AVAILABLE:
            if (
                not _is_finite_number(component.score)
                or component.score not in ALLOWED_SEMANTIC_SCORES
                or not component.evidence
            ):
                raise CareerIntelligenceBriefError(
                    f"Brief Opportunity component {name!r} is malformed."
                )
        elif component.status == ComponentStatus.UNAVAILABLE:
            if component.score is not None or component.evidence:
                raise CareerIntelligenceBriefError(
                    f"Brief Opportunity component {name!r} is malformed."
                )
        else:
            raise CareerIntelligenceBriefError(
                f"Brief Opportunity component {name!r} status is invalid."
            )


def _validate_opportunity_score(score: PriorityScoreResult) -> None:
    if not _is_finite_number(score.priority_score) or not (
        0 <= score.priority_score <= 100
    ):
        raise CareerIntelligenceBriefError(
            "Brief Opportunity priority_score must be between 0 and 100."
        )
    if not isinstance(score.tier, PriorityTier):
        raise CareerIntelligenceBriefError(
            "Brief Opportunity priority_tier is invalid."
        )
    if not isinstance(score.matched_path_ids, tuple) or any(
        not isinstance(path_id, str) or not path_id.strip()
        for path_id in score.matched_path_ids
    ):
        raise CareerIntelligenceBriefError(
            "Brief Opportunity matched_path_ids are invalid."
        )


def _validate_interpretation(
    interpretation: CareerIntelligenceInterpretationResult,
    *,
    routing_result: CareerSignalRoutingResult,
) -> None:
    if not isinstance(interpretation, CareerIntelligenceInterpretationResult):
        raise CareerIntelligenceBriefError(
            "Brief assembly requires a CareerIntelligenceInterpretationResult."
        )
    if (
        interpretation.schema_version
        != CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION
    ):
        raise CareerIntelligenceBriefError(
            "Brief interpretation schema_version is invalid."
        )

    input_signal_ids = _validated_id_tuple(
        interpretation.input_signal_ids,
        field_name="Interpretation input_signal_ids",
    )
    routed_intelligence_ids = _routed_intelligence_ids(routing_result)
    if set(input_signal_ids) != set(routed_intelligence_ids):
        raise CareerIntelligenceBriefError(
            "Brief interpretation signal IDs do not match routed Intelligence."
        )
    input_id_set = set(input_signal_ids)

    if not isinstance(interpretation.key_developments, tuple):
        raise CareerIntelligenceBriefError(
            "Brief key_developments must be a tuple."
        )
    for development in interpretation.key_developments:
        if not isinstance(development, KeyDevelopmentInterpretation):
            raise CareerIntelligenceBriefError(
                "Brief key_developments contain an invalid object."
            )
        _required_string(development.title, field_name="Key Development title")
        _required_string(development.summary, field_name="Key Development summary")
        _required_string(
            development.why_it_matters,
            field_name="Key Development why_it_matters",
        )
        _validate_supporting_signal_ids(
            development.supporting_signal_ids,
            input_signal_ids=input_id_set,
            minimum=1,
            field_name="Key Development supporting_signal_ids",
        )
        _validate_confidence(development.confidence, "Key Development")

    if not isinstance(interpretation.themes, tuple):
        raise CareerIntelligenceBriefError("Brief themes must be a tuple.")
    for theme in interpretation.themes:
        if not isinstance(theme, ThemeInterpretation):
            raise CareerIntelligenceBriefError(
                "Brief themes contain an invalid object."
            )
        _required_string(theme.title, field_name="Theme title")
        _required_string(theme.summary, field_name="Theme summary")
        _validate_supporting_signal_ids(
            theme.supporting_signal_ids,
            input_signal_ids=input_id_set,
            minimum=2,
            field_name="Theme supporting_signal_ids",
        )
        _validated_id_tuple(
            theme.relevant_career_path_ids,
            field_name="Theme relevant_career_path_ids",
        )
        _validate_confidence(theme.confidence, "Theme")

    if not isinstance(interpretation.career_implications, tuple):
        raise CareerIntelligenceBriefError(
            "Brief career_implications must be a tuple."
        )
    for implication in interpretation.career_implications:
        if not isinstance(implication, CareerImplicationInterpretation):
            raise CareerIntelligenceBriefError(
                "Brief career_implications contain an invalid object."
            )
        _required_string(implication.summary, field_name="Career Implication summary")
        path_ids = _validated_id_tuple(
            implication.relevant_career_path_ids,
            field_name="Career Implication relevant_career_path_ids",
        )
        if not path_ids:
            raise CareerIntelligenceBriefError(
                "Career Implication requires a relevant CareerPath ID."
            )
        _validate_supporting_signal_ids(
            implication.supporting_signal_ids,
            input_signal_ids=input_id_set,
            minimum=1,
            field_name="Career Implication supporting_signal_ids",
        )
        _validate_confidence(implication.confidence, "Career Implication")

    if not isinstance(interpretation.warnings, tuple) or any(
        not isinstance(warning, str) or not warning.strip()
        for warning in interpretation.warnings
    ):
        raise CareerIntelligenceBriefError("Brief warnings are invalid.")


def _routed_intelligence_ids(
    routing_result: CareerSignalRoutingResult,
) -> tuple[str, ...]:
    if not isinstance(routing_result.intelligence, tuple):
        raise CareerIntelligenceBriefError(
            "CareerSignalRoutingResult intelligence must be a tuple."
        )
    signal_ids: list[str] = []
    for scored in routing_result.intelligence:
        if not isinstance(scored, ScoredCareerSignal) or not isinstance(
            scored.career_signal,
            CareerSignal,
        ):
            raise CareerIntelligenceBriefError(
                "Routed Intelligence must contain ScoredCareerSignal objects."
            )
        signal_ids.append(
            _required_string(
                scored.career_signal.signal_id,
                field_name="Routed Intelligence signal_id",
            )
        )
    if len(signal_ids) != len(set(signal_ids)):
        raise CareerIntelligenceBriefError(
            "Routed Intelligence signal IDs must be unique."
        )
    return tuple(signal_ids)


def _target_career_path_titles(
    target_career_paths: tuple[TargetCareerPath, ...] | list[TargetCareerPath],
) -> dict[str, str]:
    if not isinstance(target_career_paths, (tuple, list)):
        raise CareerIntelligenceBriefError(
            "target_career_paths must be a tuple or list."
        )
    titles: dict[str, str] = {}
    for path in target_career_paths:
        if not isinstance(path, TargetCareerPath):
            raise CareerIntelligenceBriefError(
                "target_career_paths must contain TargetCareerPath objects."
            )
        path_id = _required_string(
            path.path_id,
            field_name="TargetCareerPath path_id",
        )
        title = _required_string(
            path.title,
            field_name="TargetCareerPath title",
        )
        if path_id in titles:
            raise CareerIntelligenceBriefError(
                "Supplied TargetCareerPath IDs must be unique."
            )
        titles[path_id] = title
    return titles


def _timezone_aware_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CareerIntelligenceBriefError(
            "Brief generated_at must be a timezone-aware ISO 8601 string."
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise CareerIntelligenceBriefError(
            "Brief generated_at must be a timezone-aware ISO 8601 string."
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CareerIntelligenceBriefError(
            "Brief generated_at must include a timezone offset."
        )
    return value


def _available_component_reason(
    component: SemanticComponentResult,
) -> str | None:
    if component.status == ComponentStatus.UNAVAILABLE:
        return None
    return component.reason


def _nullable_display_string(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CareerIntelligenceBriefError(
            f"{field_name} must be a string or null."
        )
    if not value.strip():
        return None
    return value


def _required_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CareerIntelligenceBriefError(
            f"{field_name} must be a non-empty string."
        )
    return value


def _validated_id_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, str) or not item.strip()
        for item in value
    ):
        raise CareerIntelligenceBriefError(
            f"{field_name} must be a tuple of non-empty strings."
        )
    if len(value) != len(set(value)):
        raise CareerIntelligenceBriefError(f"{field_name} must be unique.")
    return value


def _validate_supporting_signal_ids(
    value: Any,
    *,
    input_signal_ids: set[str],
    minimum: int,
    field_name: str,
) -> None:
    signal_ids = _validated_id_tuple(value, field_name=field_name)
    if len(signal_ids) < minimum or not set(signal_ids).issubset(input_signal_ids):
        raise CareerIntelligenceBriefError(
            f"{field_name} do not satisfy Interpretation grounding."
        )


def _validate_confidence(value: Any, context: str) -> None:
    if not isinstance(value, InterpretationConfidence):
        raise CareerIntelligenceBriefError(f"{context} confidence is invalid.")


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _is_finite_number(value: Any) -> bool:
    if not _is_number(value):
        return False
    return not isinstance(value, float) or math.isfinite(value)
