from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from src.career_signal_scoring import (
    PriorityScoreResult,
    SourceProvenanceInput,
    score_career_signal,
)
from src.models import (
    AIFilterResult,
    CareerSignal,
    RawItem,
    SignalCategory,
    TargetCareerPath,
    UserProfile,
)
from src.priority_assessment import (
    AssessmentProfile,
    PriorityAssessmentClient,
    PriorityAssessmentInput,
    PriorityAssessmentResult,
)
from src.signal_identity import build_signal_id


class PriorityIntegrationError(Exception):
    """Raised when Stage 2D priority integration cannot complete."""


@dataclass(frozen=True)
class ScoredCareerSignal:
    career_signal: CareerSignal
    priority_assessment: PriorityAssessmentResult
    priority_score: PriorityScoreResult
    assessment_profile: AssessmentProfile

    def to_dict(self) -> dict[str, Any]:
        return {
            "career_signal": self.career_signal.to_dict(),
            "priority_assessment": self.priority_assessment.to_dict(),
            "priority_score": self.priority_score.to_dict(),
            "assessment_profile": self.assessment_profile.value,
        }


@dataclass(frozen=True)
class PriorityAssessmentDiagnostic:
    signal_id: str
    category: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "category": self.category,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PriorityIntegrationBatchResult:
    scored_career_signals: tuple[ScoredCareerSignal, ...]
    diagnostics: tuple[PriorityAssessmentDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scored_career_signals": [
                scored.to_dict()
                for scored in self.scored_career_signals
            ],
            "diagnostics": [
                diagnostic.to_dict()
                for diagnostic in self.diagnostics
            ],
        }


def assessment_profile_for_signal(
    career_signal: CareerSignal,
) -> AssessmentProfile | None:
    """
    Minimal Stage 2D handoff from verified structured SignalCategory.

    Source type, URL, organization, and free text are intentionally ignored.
    """

    category = _signal_category(career_signal.category)
    if category == SignalCategory.JOB:
        return AssessmentProfile.OPPORTUNITY
    if category in {
        SignalCategory.NEWS,
        SignalCategory.COMPANY,
        SignalCategory.FUNDING,
        SignalCategory.MARKET_TREND,
    }:
        return AssessmentProfile.INTELLIGENCE
    return None


def assess_and_score_career_signal(
    *,
    career_signal: CareerSignal,
    assessment_profile: AssessmentProfile,
    user_profile: UserProfile,
    user_preferences: dict[str, Any],
    matched_career_path_ids: tuple[str, ...],
    target_career_paths: tuple[TargetCareerPath, ...],
    supporting_source_evidence: RawItem | dict[str, Any] | None,
    filter_confidence: float | None,
    provenance_quality: SourceProvenanceInput | dict[str, Any] | None,
    as_of: str,
    priority_assessment_client: PriorityAssessmentClient,
) -> ScoredCareerSignal:
    """
    Run the approved Stage 2B semantic assessment and Stage 2C scorer.
    """

    profile = _assessment_profile(assessment_profile)
    assessment_input = PriorityAssessmentInput(
        assessment_profile=profile,
        signal_id=career_signal.signal_id,
        as_of=as_of,
        career_signal=_semantic_assessment_signal(career_signal),
        matched_career_path_ids=matched_career_path_ids,
        target_career_paths=target_career_paths,
        user_preferences=user_preferences,
        user_profile=(
            user_profile
            if profile == AssessmentProfile.OPPORTUNITY
            else None
        ),
        supporting_source_evidence=supporting_source_evidence,
    )
    assessment = priority_assessment_client.assess(assessment_input)
    score = score_career_signal(
        assessment_result=assessment,
        career_signal=career_signal,
        matched_career_path_ids=matched_career_path_ids,
        target_career_paths=target_career_paths,
        as_of=as_of,
        upstream_ai_confidence=filter_confidence,
        source_provenance=provenance_quality,
    )
    return ScoredCareerSignal(
        career_signal=career_signal,
        priority_assessment=assessment,
        priority_score=score,
        assessment_profile=profile,
    )


def assess_and_score_career_signal_batch(
    *,
    career_signals: tuple[CareerSignal, ...] | list[CareerSignal],
    filtered_raw_items: tuple[RawItem, ...] | list[RawItem],
    ai_filter_results: tuple[AIFilterResult, ...] | list[AIFilterResult],
    user_profile: UserProfile,
    user_preferences: dict[str, Any],
    target_career_paths: tuple[TargetCareerPath, ...] | list[TargetCareerPath],
    as_of: str,
    priority_assessment_client: PriorityAssessmentClient,
    provenance_quality_by_signal_id: dict[
        str,
        SourceProvenanceInput | dict[str, Any],
    ] | None = None,
) -> PriorityIntegrationBatchResult:
    """
    Source-independent Stage 2D integration for canonical CareerSignals.
    """

    if not isinstance(user_preferences, dict):
        raise PriorityIntegrationError("user_preferences must be a JSON object.")

    raw_item_by_signal_id = {
        build_signal_id(raw_item): raw_item
        for raw_item in filtered_raw_items
    }
    filter_result_by_fingerprint = {
        result.raw_item_fingerprint: result
        for result in ai_filter_results
    }
    provenance_by_signal_id = provenance_quality_by_signal_id or {}

    scored: list[ScoredCareerSignal] = []
    diagnostics: list[PriorityAssessmentDiagnostic] = []
    target_paths = tuple(target_career_paths)

    for career_signal in career_signals:
        profile = assessment_profile_for_signal(career_signal)
        if profile is None:
            diagnostics.append(
                PriorityAssessmentDiagnostic(
                    signal_id=career_signal.signal_id,
                    category=_category_value(career_signal.category),
                    reason=(
                        "No verified Stage 2D assessment profile mapping exists "
                        "for this structured SignalCategory."
                    ),
                )
            )
            continue

        raw_item = raw_item_by_signal_id.get(career_signal.signal_id)
        filter_result = _filter_result_for_raw_item(
            raw_item,
            filter_result_by_fingerprint,
        )
        matched_path_ids = _matched_path_ids(
            career_signal=career_signal,
            filter_result=filter_result,
        )

        scored.append(
            assess_and_score_career_signal(
                career_signal=career_signal,
                assessment_profile=profile,
                user_profile=user_profile,
                user_preferences=user_preferences,
                matched_career_path_ids=matched_path_ids,
                target_career_paths=target_paths,
                supporting_source_evidence=raw_item,
                filter_confidence=_filter_confidence(
                    career_signal=career_signal,
                    filter_result=filter_result,
                ),
                provenance_quality=provenance_by_signal_id.get(
                    career_signal.signal_id
                ),
                as_of=as_of,
                priority_assessment_client=priority_assessment_client,
            )
        )

    return PriorityIntegrationBatchResult(
        scored_career_signals=tuple(scored),
        diagnostics=tuple(diagnostics),
    )


def _filter_result_for_raw_item(
    raw_item: RawItem | None,
    filter_result_by_fingerprint: dict[str, AIFilterResult],
) -> AIFilterResult | None:
    if raw_item is None:
        return None
    return filter_result_by_fingerprint.get(_ai_filter_raw_item_fingerprint(raw_item))


def _semantic_assessment_signal(career_signal: CareerSignal) -> CareerSignal:
    metadata = career_signal.metadata if isinstance(career_signal.metadata, dict) else {}
    summary = career_signal.summary
    if metadata.get("ai_filter_reason") and " Normalizer note: " in summary:
        summary = summary.split(" Normalizer note: ", 1)[0]

    return CareerSignal(
        signal_id=career_signal.signal_id,
        category=career_signal.category,
        title=career_signal.title,
        organization=career_signal.organization,
        url=career_signal.url,
        published_at=career_signal.published_at,
        summary=summary,
        source_type=career_signal.source_type,
        relevance_score=career_signal.relevance_score,
        metadata=dict(metadata),
    )


def _matched_path_ids(
    *,
    career_signal: CareerSignal,
    filter_result: AIFilterResult | None,
) -> tuple[str, ...]:
    if filter_result is not None:
        return tuple(str(path_id) for path_id in filter_result.matched_career_path_ids)

    metadata = career_signal.metadata if isinstance(career_signal.metadata, dict) else {}
    value = metadata.get("matched_career_path_ids")
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(path_id) for path_id in value if path_id is not None)


def _filter_confidence(
    *,
    career_signal: CareerSignal,
    filter_result: AIFilterResult | None,
) -> float | None:
    if filter_result is not None:
        return filter_result.confidence

    metadata = career_signal.metadata if isinstance(career_signal.metadata, dict) else {}
    value = metadata.get("ai_filter_confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _ai_filter_raw_item_fingerprint(raw_item: RawItem) -> str:
    fingerprint_source = (
        f"{raw_item.source_type.value}|"
        f"{raw_item.title}|"
        f"{raw_item.url}|"
        f"{raw_item.raw_text[:200]}"
    )
    return hashlib.sha256(
        fingerprint_source.encode("utf-8")
    ).hexdigest()[:16]


def _assessment_profile(value: AssessmentProfile | str | Any) -> AssessmentProfile:
    if isinstance(value, AssessmentProfile):
        return value
    return AssessmentProfile(str(value))


def _signal_category(value: SignalCategory | str | Any) -> SignalCategory:
    if isinstance(value, SignalCategory):
        return value
    try:
        return SignalCategory(str(value))
    except ValueError:
        return SignalCategory.UNKNOWN


def _category_value(value: SignalCategory | str | Any) -> str:
    return value.value if hasattr(value, "value") else str(value)
