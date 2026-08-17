from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any

from src.models import CareerSignal, TargetCareerPath
from src.priority_assessment import (
    ALLOWED_SEMANTIC_SCORES,
    AssessmentProfile,
    ComponentStatus,
    INTELLIGENCE_COMPONENTS,
    OPPORTUNITY_COMPONENTS,
    PriorityAssessmentResult,
)


PRIORITY_SCORING_POLICY_VERSION = "career_signal_priority_v1"

COMPONENT_PATH_ALIGNMENT = "path_alignment"
COMPONENT_RECENCY = "recency"
COMPONENT_SOURCE_PROVENANCE = "source_provenance"
COMPONENT_AI_CONFIDENCE = "ai_confidence"

OWNER_AI_SEMANTIC_ASSESSMENT = "ai_semantic_assessment"
OWNER_DETERMINISTIC = "deterministic"
OWNER_UPSTREAM_AI_FILTER = "upstream_ai_filter"


class PriorityScoringError(Exception):
    """
    Raised when deterministic priority scoring cannot produce a valid result.
    """


class PriorityTier(str, Enum):
    HIGH = "high"
    MEDIUM_HIGH = "medium_high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class ScoringComponentPolicy:
    name: str
    configured_weight: float
    owner: str


@dataclass(frozen=True)
class RecencyBucket:
    min_age_days: float
    max_age_days: float | None
    score: float


@dataclass(frozen=True)
class TierBoundary:
    tier: PriorityTier
    min_score: float
    max_score_exclusive: float | None


@dataclass(frozen=True)
class PriorityScoringPolicy:
    policy_version: str
    opportunity_components: tuple[ScoringComponentPolicy, ...]
    intelligence_components: tuple[ScoringComponentPolicy, ...]
    path_type_modifiers: dict[str, float]
    recency_buckets: tuple[RecencyBucket, ...]
    tier_boundaries: tuple[TierBoundary, ...]

    def components_for_profile(
        self,
        profile: AssessmentProfile,
    ) -> tuple[ScoringComponentPolicy, ...]:
        if profile == AssessmentProfile.OPPORTUNITY:
            return self.opportunity_components
        return self.intelligence_components


@dataclass(frozen=True)
class SourceProvenanceInput:
    """
    Explicit normalized provenance-quality evidence supplied by a caller.

    Stage 2C does not derive source quality from source_type or domain.
    """

    normalized_score: float
    reason: str
    evidence: tuple[str, ...] = ()
    reference_id: str | None = None


@dataclass(frozen=True)
class PriorityScoreComponent:
    name: str
    owner: str
    status: ComponentStatus
    normalized_score: float | None
    configured_weight: float
    weighted_contribution: float | None
    reason: str
    evidence: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "owner": self.owner,
            "status": self.status.value,
            "normalized_score": self.normalized_score,
            "configured_weight": self.configured_weight,
            "weighted_contribution": self.weighted_contribution,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "references": list(self.references),
        }


@dataclass(frozen=True)
class PriorityScoreResult:
    signal_id: str
    priority_score: float
    tier: PriorityTier
    profile: AssessmentProfile
    components: dict[str, PriorityScoreComponent]
    matched_path_ids: tuple[str, ...]
    policy_version: str
    renormalization_denominator: float
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "priority_score": self.priority_score,
            "tier": self.tier.value,
            "profile": self.profile.value,
            "components": {
                key: component.to_dict()
                for key, component in self.components.items()
            },
            "matched_path_ids": list(self.matched_path_ids),
            "policy_version": self.policy_version,
            "renormalization_denominator": self.renormalization_denominator,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class _ScoringContext:
    components: dict[str, PriorityScoreComponent] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    resolved_matched_path_ids: tuple[str, ...] = ()


PRIORITY_SCORING_POLICY_V1 = PriorityScoringPolicy(
    policy_version=PRIORITY_SCORING_POLICY_VERSION,
    opportunity_components=(
        ScoringComponentPolicy(COMPONENT_PATH_ALIGNMENT, 30.0, OWNER_DETERMINISTIC),
        ScoringComponentPolicy("user_policy_fit", 25.0, OWNER_AI_SEMANTIC_ASSESSMENT),
        ScoringComponentPolicy("opportunity_feasibility", 20.0, OWNER_AI_SEMANTIC_ASSESSMENT),
        ScoringComponentPolicy(COMPONENT_RECENCY, 15.0, OWNER_DETERMINISTIC),
        ScoringComponentPolicy(COMPONENT_SOURCE_PROVENANCE, 5.0, OWNER_DETERMINISTIC),
        ScoringComponentPolicy(COMPONENT_AI_CONFIDENCE, 5.0, OWNER_UPSTREAM_AI_FILTER),
    ),
    intelligence_components=(
        ScoringComponentPolicy("career_relevance_strength", 25.0, OWNER_AI_SEMANTIC_ASSESSMENT),
        ScoringComponentPolicy("signal_significance", 25.0, OWNER_AI_SEMANTIC_ASSESSMENT),
        ScoringComponentPolicy(COMPONENT_PATH_ALIGNMENT, 20.0, OWNER_DETERMINISTIC),
        ScoringComponentPolicy(COMPONENT_RECENCY, 15.0, OWNER_DETERMINISTIC),
        ScoringComponentPolicy(COMPONENT_SOURCE_PROVENANCE, 10.0, OWNER_DETERMINISTIC),
        ScoringComponentPolicy(COMPONENT_AI_CONFIDENCE, 5.0, OWNER_UPSTREAM_AI_FILTER),
    ),
    path_type_modifiers={
        "core": 1.0,
        "core_match": 1.0,
        "bridge_role": 0.9,
        "stretch_opportunity": 0.75,
        "exploratory_opportunity": 0.65,
    },
    recency_buckets=(
        RecencyBucket(0.0, 3.0, 1.0),
        RecencyBucket(3.0, 7.0, 0.9),
        RecencyBucket(7.0, 14.0, 0.75),
        RecencyBucket(14.0, 30.0, 0.55),
        RecencyBucket(30.0, 60.0, 0.35),
        RecencyBucket(60.0, None, 0.2),
    ),
    tier_boundaries=(
        TierBoundary(PriorityTier.HIGH, 85.0, None),
        TierBoundary(PriorityTier.MEDIUM_HIGH, 70.0, 85.0),
        TierBoundary(PriorityTier.MEDIUM, 50.0, 70.0),
        TierBoundary(PriorityTier.LOW, 0.0, 50.0),
    ),
)


def score_career_signal(
    *,
    assessment_result: PriorityAssessmentResult,
    career_signal: CareerSignal,
    matched_career_path_ids: tuple[str, ...],
    target_career_paths: tuple[TargetCareerPath, ...],
    as_of: str,
    upstream_ai_confidence: float | None = None,
    source_provenance: SourceProvenanceInput | dict[str, Any] | None = None,
    policy: PriorityScoringPolicy = PRIORITY_SCORING_POLICY_V1,
) -> PriorityScoreResult:
    """
    Compute deterministic V1 priority scoring for one accepted CareerSignal.
    """

    if assessment_result.signal_id != career_signal.signal_id:
        raise PriorityScoringError(
            "PriorityAssessmentResult signal_id must match CareerSignal.signal_id."
        )

    profile = _assessment_profile(assessment_result.assessment_profile)
    _validate_assessment_components(assessment_result)

    context = _build_scoring_context(
        assessment_result=assessment_result,
        career_signal=career_signal,
        matched_career_path_ids=matched_career_path_ids,
        target_career_paths=target_career_paths,
        as_of=as_of,
        upstream_ai_confidence=upstream_ai_confidence,
        source_provenance=source_provenance,
        policy=policy,
    )

    ordered_policies = policy.components_for_profile(profile)
    ordered_components = {
        component_policy.name: context.components[component_policy.name]
        for component_policy in ordered_policies
    }

    active_denominator = sum(
        component.configured_weight
        for component in ordered_components.values()
        if component.status == ComponentStatus.AVAILABLE
    )
    if active_denominator <= 0:
        raise PriorityScoringError(
            "Priority Score cannot be calculated because no components are available."
        )

    components_with_contributions = {
        name: _with_weighted_contribution(component, active_denominator)
        for name, component in ordered_components.items()
    }
    priority_score = sum(
        component.weighted_contribution or 0.0
        for component in components_with_contributions.values()
    )

    return PriorityScoreResult(
        signal_id=career_signal.signal_id,
        priority_score=priority_score,
        tier=priority_tier(priority_score),
        profile=profile,
        components=components_with_contributions,
        matched_path_ids=context.resolved_matched_path_ids,
        policy_version=policy.policy_version,
        renormalization_denominator=active_denominator,
        warnings=tuple(dict.fromkeys((*assessment_result.warnings, *context.warnings))),
    )


def priority_tier(priority_score: float) -> PriorityTier:
    if not _is_number(priority_score) or priority_score < 0 or priority_score > 100:
        raise PriorityScoringError("priority_score must be between 0 and 100.")

    for boundary in PRIORITY_SCORING_POLICY_V1.tier_boundaries:
        if priority_score < boundary.min_score:
            continue
        if boundary.max_score_exclusive is None:
            return boundary.tier
        if priority_score < boundary.max_score_exclusive:
            return boundary.tier

    raise PriorityScoringError("priority_score did not match a configured tier.")


def _build_scoring_context(
    *,
    assessment_result: PriorityAssessmentResult,
    career_signal: CareerSignal,
    matched_career_path_ids: tuple[str, ...],
    target_career_paths: tuple[TargetCareerPath, ...],
    as_of: str,
    upstream_ai_confidence: float | None,
    source_provenance: SourceProvenanceInput | dict[str, Any] | None,
    policy: PriorityScoringPolicy,
) -> _ScoringContext:
    warnings: list[str] = []
    components: dict[str, PriorityScoreComponent] = {}

    path_component, resolved_path_ids, path_warnings = score_path_alignment_component(
        matched_career_path_ids=matched_career_path_ids,
        target_career_paths=target_career_paths,
        configured_weight=_weight_for_component(
            policy,
            assessment_result.assessment_profile,
            COMPONENT_PATH_ALIGNMENT,
        ),
        policy=policy,
    )
    warnings.extend(path_warnings)
    components[COMPONENT_PATH_ALIGNMENT] = path_component

    recency_component, recency_warnings = score_recency_component(
        published_at=career_signal.published_at,
        as_of=as_of,
        configured_weight=_weight_for_component(
            policy,
            assessment_result.assessment_profile,
            COMPONENT_RECENCY,
        ),
        policy=policy,
    )
    warnings.extend(recency_warnings)
    components[COMPONENT_RECENCY] = recency_component

    source_component, source_warnings = score_source_provenance_component(
        source_provenance=source_provenance,
        configured_weight=_weight_for_component(
            policy,
            assessment_result.assessment_profile,
            COMPONENT_SOURCE_PROVENANCE,
        ),
    )
    warnings.extend(source_warnings)
    components[COMPONENT_SOURCE_PROVENANCE] = source_component

    confidence_component, confidence_warnings = score_ai_confidence_component(
        upstream_ai_confidence=upstream_ai_confidence,
        career_signal_relevance_score=career_signal.relevance_score,
        configured_weight=_weight_for_component(
            policy,
            assessment_result.assessment_profile,
            COMPONENT_AI_CONFIDENCE,
        ),
    )
    warnings.extend(confidence_warnings)
    components[COMPONENT_AI_CONFIDENCE] = confidence_component

    for semantic_name, semantic_component in assessment_result.components.items():
        weight = _weight_for_component(
            policy,
            assessment_result.assessment_profile,
            semantic_name,
        )
        components[semantic_name] = _semantic_component_to_score_component(
            semantic_name,
            semantic_component,
            weight,
        )

    return _ScoringContext(
        components=components,
        warnings=tuple(warnings),
        resolved_matched_path_ids=resolved_path_ids,
    )


def score_path_alignment_component(
    *,
    matched_career_path_ids: tuple[str, ...],
    target_career_paths: tuple[TargetCareerPath, ...],
    configured_weight: float,
    policy: PriorityScoringPolicy = PRIORITY_SCORING_POLICY_V1,
) -> tuple[PriorityScoreComponent, tuple[str, ...], tuple[str, ...]]:
    paths_by_id = {path.path_id: path for path in target_career_paths}
    warnings: list[str] = []
    resolved_path_ids: list[str] = []
    scored_paths: list[tuple[float, TargetCareerPath, float, str]] = []
    seen: set[str] = set()

    for path_id in matched_career_path_ids:
        if path_id in seen:
            continue
        seen.add(path_id)
        path = paths_by_id.get(path_id)
        if path is None:
            warnings.append(f"unresolved_matched_career_path_id:{path_id}")
            continue
        resolved_path_ids.append(path_id)

        normalized_fit = _normalize_fit_score(path.fit_score)
        if normalized_fit is None:
            warnings.append(f"invalid_fit_score:{path_id}")
            continue

        path_type = _path_type(path)
        modifier = policy.path_type_modifiers.get(path_type)
        if modifier is None:
            warnings.append(f"unsupported_path_type:{path_id}:{path_type or 'missing'}")
            continue

        scored_paths.append((normalized_fit * modifier, path, modifier, path_type))

    if scored_paths:
        best_score, best_path, modifier, path_type = max(
            scored_paths,
            key=lambda item: item[0],
        )
        return (
            PriorityScoreComponent(
                name=COMPONENT_PATH_ALIGNMENT,
                owner=OWNER_DETERMINISTIC,
                status=ComponentStatus.AVAILABLE,
                normalized_score=best_score,
                configured_weight=configured_weight,
                weighted_contribution=None,
                reason=(
                    "Strongest resolved matched TargetCareerPath selected "
                    "after applying the V1 path_type modifier."
                ),
                evidence=(
                    f"path_id={best_path.path_id}",
                    f"fit_score={best_path.fit_score}",
                    f"normalized_fit_score={_normalize_fit_score(best_path.fit_score)}",
                    f"path_type={path_type}",
                    f"path_type_modifier={modifier}",
                ),
                references=(best_path.path_id,),
            ),
            tuple(resolved_path_ids),
            tuple(warnings),
        )

    reason = (
        "No matched TargetCareerPath could be resolved."
        if not resolved_path_ids
        else "Resolved matched TargetCareerPaths had invalid fit_score or unsupported path_type."
    )
    return (
        _unavailable_component(
            COMPONENT_PATH_ALIGNMENT,
            OWNER_DETERMINISTIC,
            configured_weight,
            reason,
        ),
        tuple(resolved_path_ids),
        tuple(warnings),
    )


def score_recency_component(
    *,
    published_at: str | None,
    as_of: str,
    configured_weight: float,
    policy: PriorityScoringPolicy = PRIORITY_SCORING_POLICY_V1,
) -> tuple[PriorityScoreComponent, tuple[str, ...]]:
    if not published_at:
        return (
            _unavailable_component(
                COMPONENT_RECENCY,
                OWNER_DETERMINISTIC,
                configured_weight,
                "CareerSignal.published_at is missing.",
            ),
            (),
        )

    try:
        published = _parse_datetime(published_at)
        reference = _parse_datetime(as_of)
    except ValueError:
        return (
            _unavailable_component(
                COMPONENT_RECENCY,
                OWNER_DETERMINISTIC,
                configured_weight,
                "CareerSignal.published_at or as_of could not be parsed.",
            ),
            (),
        )

    warnings: list[str] = []
    age_seconds = (reference - published).total_seconds()
    if age_seconds < 0:
        age_days = 0.0
        warnings.append("future_published_at_treated_as_age_zero")
    else:
        age_days = age_seconds / 86400

    for bucket in policy.recency_buckets:
        if age_days < bucket.min_age_days:
            continue
        if bucket.max_age_days is None or age_days <= bucket.max_age_days:
            return (
                PriorityScoreComponent(
                    name=COMPONENT_RECENCY,
                    owner=OWNER_DETERMINISTIC,
                    status=ComponentStatus.AVAILABLE,
                    normalized_score=bucket.score,
                    configured_weight=configured_weight,
                    weighted_contribution=None,
                    reason="Recency scored from CareerSignal.published_at against explicit as_of.",
                    evidence=(
                        f"published_at={published_at}",
                        f"as_of={as_of}",
                        f"age_days={age_days}",
                    ),
                ),
                tuple(warnings),
            )

    raise PriorityScoringError("Recency bucket policy is incomplete.")


def score_ai_confidence_component(
    *,
    upstream_ai_confidence: float | None,
    career_signal_relevance_score: float | None,
    configured_weight: float,
) -> tuple[PriorityScoreComponent, tuple[str, ...]]:
    warnings: list[str] = []

    if upstream_ai_confidence is not None:
        if _is_normalized_score(upstream_ai_confidence):
            return (
                PriorityScoreComponent(
                    name=COMPONENT_AI_CONFIDENCE,
                    owner=OWNER_UPSTREAM_AI_FILTER,
                    status=ComponentStatus.AVAILABLE,
                    normalized_score=float(upstream_ai_confidence),
                    configured_weight=configured_weight,
                    weighted_contribution=None,
                    reason="Used explicit upstream AI Filter confidence.",
                    evidence=(f"upstream_ai_confidence={upstream_ai_confidence}",),
                ),
                (),
            )
        warnings.append("invalid_upstream_ai_confidence")
        return (
            _unavailable_component(
                COMPONENT_AI_CONFIDENCE,
                OWNER_UPSTREAM_AI_FILTER,
                configured_weight,
                "Explicit upstream AI Filter confidence was outside the 0-1 range.",
            ),
            tuple(warnings),
        )

    if career_signal_relevance_score is not None:
        if _is_number(career_signal_relevance_score) and 0 <= float(career_signal_relevance_score) <= 100:
            normalized = float(career_signal_relevance_score) / 100
            return (
                PriorityScoreComponent(
                    name=COMPONENT_AI_CONFIDENCE,
                    owner=OWNER_UPSTREAM_AI_FILTER,
                    status=ComponentStatus.AVAILABLE,
                    normalized_score=normalized,
                    configured_weight=configured_weight,
                    weighted_contribution=None,
                    reason=(
                        "Used CareerSignal.relevance_score fallback under the "
                        "normalizer contract relevance_score = confidence * 100."
                    ),
                    evidence=(f"career_signal.relevance_score={career_signal_relevance_score}",),
                ),
                (),
            )
        warnings.append("invalid_career_signal_relevance_score")

    return (
        _unavailable_component(
            COMPONENT_AI_CONFIDENCE,
            OWNER_UPSTREAM_AI_FILTER,
            configured_weight,
            "No valid upstream AI confidence or relevance_score fallback was available.",
        ),
        tuple(warnings),
    )


def score_source_provenance_component(
    *,
    source_provenance: SourceProvenanceInput | dict[str, Any] | None,
    configured_weight: float,
) -> tuple[PriorityScoreComponent, tuple[str, ...]]:
    if source_provenance is None:
        return (
            _unavailable_component(
                COMPONENT_SOURCE_PROVENANCE,
                OWNER_DETERMINISTIC,
                configured_weight,
                "No explicit structured provenance-quality evidence was supplied.",
            ),
            (),
        )

    parsed = _source_provenance_input(source_provenance)
    if parsed is None:
        return (
            _unavailable_component(
                COMPONENT_SOURCE_PROVENANCE,
                OWNER_DETERMINISTIC,
                configured_weight,
                "Structured provenance-quality evidence was malformed or outside the 0-1 range.",
            ),
            ("malformed_source_provenance_context",),
        )

    evidence = parsed.evidence
    if not evidence:
        evidence = ("explicit_normalized_source_provenance_score",)

    references = (parsed.reference_id,) if parsed.reference_id else ()
    return (
        PriorityScoreComponent(
            name=COMPONENT_SOURCE_PROVENANCE,
            owner=OWNER_DETERMINISTIC,
            status=ComponentStatus.AVAILABLE,
            normalized_score=float(parsed.normalized_score),
            configured_weight=configured_weight,
            weighted_contribution=None,
            reason=parsed.reason.strip(),
            evidence=tuple(evidence),
            references=references,
        ),
        (),
    )


def _semantic_component_to_score_component(
    name: str,
    semantic_component: Any,
    configured_weight: float,
) -> PriorityScoreComponent:
    status = semantic_component.status
    if status == ComponentStatus.AVAILABLE:
        if not _is_normalized_score(semantic_component.score):
            raise PriorityScoringError(f"Semantic component {name!r} score is invalid.")
        if float(semantic_component.score) not in ALLOWED_SEMANTIC_SCORES:
            raise PriorityScoringError(f"Semantic component {name!r} score is not V1-discrete.")
        normalized_score = float(semantic_component.score)
    else:
        normalized_score = None

    return PriorityScoreComponent(
        name=name,
        owner=OWNER_AI_SEMANTIC_ASSESSMENT,
        status=status,
        normalized_score=normalized_score,
        configured_weight=configured_weight,
        weighted_contribution=None,
        reason=semantic_component.reason,
        evidence=tuple(semantic_component.evidence),
    )


def _with_weighted_contribution(
    component: PriorityScoreComponent,
    active_denominator: float,
) -> PriorityScoreComponent:
    if component.status == ComponentStatus.UNAVAILABLE:
        return component
    if component.normalized_score is None:
        raise PriorityScoringError(f"Available component {component.name!r} has no score.")

    contribution = (
        100
        * component.configured_weight
        * component.normalized_score
        / active_denominator
    )
    return PriorityScoreComponent(
        name=component.name,
        owner=component.owner,
        status=component.status,
        normalized_score=component.normalized_score,
        configured_weight=component.configured_weight,
        weighted_contribution=contribution,
        reason=component.reason,
        evidence=component.evidence,
        references=component.references,
    )


def _validate_assessment_components(
    assessment_result: PriorityAssessmentResult,
) -> None:
    profile = _assessment_profile(assessment_result.assessment_profile)
    expected = (
        OPPORTUNITY_COMPONENTS
        if profile == AssessmentProfile.OPPORTUNITY
        else INTELLIGENCE_COMPONENTS
    )
    actual = tuple(assessment_result.components)
    if set(actual) != set(expected):
        raise PriorityScoringError(
            "PriorityAssessmentResult components do not match assessment_profile."
        )


def _weight_for_component(
    policy: PriorityScoringPolicy,
    profile: AssessmentProfile,
    component_name: str,
) -> float:
    for component in policy.components_for_profile(_assessment_profile(profile)):
        if component.name == component_name:
            return component.configured_weight
    raise PriorityScoringError(f"Component {component_name!r} is not configured.")


def _assessment_profile(value: AssessmentProfile | str | Any) -> AssessmentProfile:
    if isinstance(value, AssessmentProfile):
        return value
    try:
        return AssessmentProfile(str(value))
    except ValueError as error:
        raise PriorityScoringError(f"Unknown assessment_profile: {value!r}.") from error


def _normalize_fit_score(value: Any) -> float | None:
    if not _is_number(value):
        return None
    fit_score = float(value)
    if fit_score < 0 or fit_score > 100:
        return None
    return fit_score / 100


def _path_type(path: TargetCareerPath) -> str:
    metadata = path.metadata if isinstance(path.metadata, dict) else {}
    value = metadata.get("path_type")
    if value is None:
        value = metadata.get("tier")
    return str(value).strip() if value is not None else ""


def _parse_datetime(value: str) -> datetime:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("empty datetime")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(normalized)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("invalid datetime") from error
        if parsed is None:
            raise ValueError("invalid datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _source_provenance_input(
    value: SourceProvenanceInput | dict[str, Any],
) -> SourceProvenanceInput | None:
    if isinstance(value, SourceProvenanceInput):
        parsed = value
    elif isinstance(value, dict):
        parsed = SourceProvenanceInput(
            normalized_score=value.get("normalized_score"),
            reason=str(value.get("reason", "")),
            evidence=_string_tuple(value.get("evidence")),
            reference_id=(
                str(value.get("reference_id"))
                if value.get("reference_id") is not None
                else None
            ),
        )
    else:
        return None

    if not _is_normalized_score(parsed.normalized_score):
        return None
    if not isinstance(parsed.reason, str) or not parsed.reason.strip():
        return None
    if any(not isinstance(item, str) or not item.strip() for item in parsed.evidence):
        return None
    return parsed


def _unavailable_component(
    name: str,
    owner: str,
    configured_weight: float,
    reason: str,
) -> PriorityScoreComponent:
    return PriorityScoreComponent(
        name=name,
        owner=owner,
        status=ComponentStatus.UNAVAILABLE,
        normalized_score=None,
        configured_weight=configured_weight,
        weighted_contribution=None,
        reason=reason,
        evidence=(),
    )


def _is_normalized_score(value: Any) -> bool:
    if not _is_number(value):
        return False
    score = float(value)
    return 0 <= score <= 1


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if item is not None)
