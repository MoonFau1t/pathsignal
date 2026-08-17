from dataclasses import replace
from typing import Any

from src.config import (
    ENTITY_PRIORITY_TIER_A_LIMIT,
    ENTITY_PRIORITY_TIER_B_LIMIT,
    ENTITY_PRIORITY_TIER_C_LIMIT,
)
from src.source_monitoring.entity_discovery_models import (
    EntityCandidate,
    EntityCandidateVerificationStatus,
    EntityUniverseResult,
    OfficialDomainVerificationStatus,
)
from src.source_monitoring.entity_identity import (
    normalize_domain,
    normalize_organization_name,
)
from src.source_monitoring.entity_prioritization_models import (
    ENTITY_PRIORITY_SCORING_POLICY_VERSION,
    EVIDENCE_READINESS_POLICY_VERSION,
    PRIORITY_TIER_POLICY_VERSION,
    EvidenceReadinessAssessment,
    GeographyAssessment,
    PriorityTier,
    SemanticAssessmentStatus,
    SemanticDimensionAssessment,
)


ENTITY_PRIORITY_DIMENSION_WEIGHTS: dict[str, float] = {
    "path_relevance": 0.30,
    "geography_relevance": 0.15,
    "stage_relevance": 0.10,
    "expected_signal_potential": 0.25,
    "strategic_importance": 0.20,
}


def scoring_policy_snapshot() -> dict[str, Any]:
    return {
        "scoring_policy_version": ENTITY_PRIORITY_SCORING_POLICY_VERSION,
        "weights": ENTITY_PRIORITY_DIMENSION_WEIGHTS,
        "semantic_scale": {"min": 0, "max": 5},
        "score_range": {"min": 0, "max": 100},
    }


def evidence_readiness_policy_snapshot() -> dict[str, Any]:
    return {
        "evidence_readiness_policy_version": EVIDENCE_READINESS_POLICY_VERSION,
        "positive_factors": (
            "verified_or_probable_official_domain",
            "multiple_supporting_evidence_items",
            "distinct_evidence_domains",
            "multilingual_name_support",
            "evidence_supported_verification",
            "complete_provenance",
            "high_phase2_confidence",
        ),
        "negative_factors": (
            "no_domain_candidate",
            "unresolved_domains_only",
            "single_evidence_domain",
            "unresolved_identity_conflict",
            "low_phase2_confidence",
        ),
    }


def tier_policy_snapshot() -> dict[str, Any]:
    return {
        "priority_tier_policy_version": PRIORITY_TIER_POLICY_VERSION,
        "tier_a_limit": ENTITY_PRIORITY_TIER_A_LIMIT,
        "tier_b_limit": ENTITY_PRIORITY_TIER_B_LIMIT,
        "tier_c_limit": ENTITY_PRIORITY_TIER_C_LIMIT,
        "tie_breaking": (
            "entity_priority_score desc",
            "expected_signal_potential desc",
            "evidence_readiness_score desc",
            "normalized canonical_name asc",
            "entity_id asc",
        ),
    }


def normalize_semantic_score(score: int | None) -> int | None:
    if score is None:
        return None
    return max(0, min(100, round((score / 5) * 100)))


def calculate_geography_assessment(
    *,
    entity: EntityCandidate,
    user_preferences: dict[str, Any],
) -> GeographyAssessment:
    preference_text = " ".join(_flatten_strings(user_preferences)).casefold()
    scope = entity.geographic_scope.casefold()
    matched: list[str] = []
    conflicts: list[str] = []

    prefers_china = any(
        token in preference_text
        for token in (
            "china",
            "greater china",
            "apac",
            "shanghai",
            "beijing",
            "\u4e2d\u56fd",
        )
    )
    accepts_global = any(
        token in preference_text
        for token in (
            "global",
            "international",
            "remote",
            "overseas",
            "\u5168\u7403",
        )
    )
    excludes_china = any(
        token in preference_text
        for token in (
            "exclude china",
            "not china",
            "avoid china",
            "\u4e0d\u8003\u8651\u4e2d\u56fd",
        )
    )

    if "china" in scope or "greater china" in scope or "\u4e2d\u56fd" in scope:
        if excludes_china:
            conflicts.append("entity geography conflicts with excluded China scope")
            score = 25
        elif prefers_china:
            matched.append("matches China or Greater China preference")
            score = 90
        else:
            matched.append("China-relevant entity retained for monitoring context")
            score = 70
    elif "global" in scope or "international" in scope:
        if accepts_global or prefers_china:
            matched.append("global entity can support preferred geographies")
            score = 85
        else:
            matched.append("global entity has broad geography relevance")
            score = 75
    elif "apac" in scope:
        matched.append("APAC scope overlaps regional monitoring needs")
        score = 82 if prefers_china else 72
    elif not scope or scope in {"unknown", "unresolved"}:
        conflicts.append("entity geography is insufficiently specific")
        score = 55
    else:
        matched.append(f"entity has explicit geography: {entity.geographic_scope}")
        score = 65

    return GeographyAssessment(
        score=max(0, min(100, score)),
        matched_preferences=tuple(sorted(set(matched))),
        conflicts=tuple(sorted(set(conflicts))),
        rationale=(
            "Deterministic geography score from entity geographic_scope and "
            "user preference geography text."
        ),
    )


def calculate_evidence_readiness_assessment(
    *,
    entity: EntityCandidate,
    entity_universe_result: EntityUniverseResult,
) -> EvidenceReadinessAssessment:
    conflicted_entity_ids = {
        entity_id
        for conflict in entity_universe_result.unresolved_identity_conflicts
        for entity_id in conflict.candidate_entity_ids
    }
    status = _highest_domain_status(entity)
    evidence_count = len(entity.evidence_ids)
    distinct_domains = {
        normalize_domain(url)
        for url in entity.evidence_urls
        if normalize_domain(url)
    }
    multilingual = sum(1 for names in entity.names_by_language.values() if names) >= 2
    strengths: list[str] = []
    weaknesses: list[str] = []
    score = 20

    if status == "verified_official":
        score += 30
        strengths.append("verified official-domain candidate")
    elif status == "probable_official":
        score += 22
        strengths.append("probable official-domain candidate")
    elif status in {"third_party", "unresolved"}:
        score += 5
        weaknesses.append("official-domain evidence is not verified")
    else:
        weaknesses.append("no official-domain candidate")
        score -= 12

    score += min(evidence_count, 4) * 5
    if evidence_count >= 3:
        strengths.append("multiple supporting evidence records")
    elif evidence_count <= 1:
        weaknesses.append("limited supporting evidence")

    score += min(len(distinct_domains), 3) * 5
    if len(distinct_domains) >= 2:
        strengths.append("evidence spans distinct domains")
    else:
        weaknesses.append("evidence is concentrated in one domain")

    if multilingual:
        score += 8
        strengths.append("multilingual names are present")
    else:
        weaknesses.append("multilingual name support is limited")

    if entity.verification_status == EntityCandidateVerificationStatus.EVIDENCE_SUPPORTED:
        score += 8
        strengths.append("entity is evidence-supported")
    elif entity.verification_status == EntityCandidateVerificationStatus.CONFLICTED:
        score -= 20
        weaknesses.append("entity verification is conflicted")

    if entity.entity_id in conflicted_entity_ids:
        score -= 25
        weaknesses.append("unresolved identity conflict requires review")

    if entity.confidence >= 0.85:
        score += 8
        strengths.append("high Phase 2 confidence")
    elif entity.confidence < 0.6:
        score -= 10
        weaknesses.append("low Phase 2 confidence")

    if not entity.provenance.get("source"):
        score -= 5
        weaknesses.append("provenance source is incomplete")

    return EvidenceReadinessAssessment(
        score=max(0, min(100, round(score))),
        official_domain_status=status or "none",
        supporting_evidence_count=evidence_count,
        distinct_domain_count=len(distinct_domains),
        multilingual_name_support=multilingual,
        identity_conflict_status=(
            "unresolved_conflict" if entity.entity_id in conflicted_entity_ids else "none"
        ),
        strengths=tuple(sorted(set(strengths))),
        weaknesses=tuple(sorted(set(weaknesses))),
    )


def calculate_entity_priority_score(
    *,
    path_relevance: SemanticDimensionAssessment,
    geography_relevance: GeographyAssessment,
    stage_relevance: SemanticDimensionAssessment,
    expected_signal_potential: SemanticDimensionAssessment,
    strategic_importance: SemanticDimensionAssessment,
) -> tuple[int, dict[str, float]]:
    raw_dimensions: dict[str, int | None] = {
        "path_relevance": normalize_semantic_score(path_relevance.score),
        "geography_relevance": geography_relevance.score,
        "stage_relevance": _applicable_stage_score(stage_relevance),
        "expected_signal_potential": normalize_semantic_score(
            expected_signal_potential.score
        ),
        "strategic_importance": normalize_semantic_score(strategic_importance.score),
    }
    applicable = {
        key: value
        for key, value in raw_dimensions.items()
        if value is not None
    }
    weight_total = sum(
        ENTITY_PRIORITY_DIMENSION_WEIGHTS[key]
        for key in applicable
    )
    weights_used = {
        key: round(ENTITY_PRIORITY_DIMENSION_WEIGHTS[key] / weight_total, 6)
        for key in sorted(applicable)
    }
    score = round(
        sum(applicable[key] * weights_used[key] for key in applicable)
    )
    return max(0, min(100, score)), weights_used


def assign_ranks_and_tiers(
    assessments,
    *,
    tier_a_limit: int = ENTITY_PRIORITY_TIER_A_LIMIT,
    tier_b_limit: int = ENTITY_PRIORITY_TIER_B_LIMIT,
    tier_c_limit: int = ENTITY_PRIORITY_TIER_C_LIMIT,
):
    ordered = sorted(
        assessments,
        key=lambda item: (
            -item.entity_priority_score,
            -normalize_semantic_score(
                item.semantic_assessment.expected_signal_potential.score
            ),
            -item.evidence_readiness_score,
            normalize_organization_name(item.canonical_name),
            item.entity_id,
        ),
    )
    ranked = []
    for index, assessment in enumerate(ordered, start=1):
        tier = _tier_for_rank_and_score(
            rank=index,
            score=assessment.entity_priority_score,
            tier_a_limit=tier_a_limit,
            tier_b_limit=tier_b_limit,
            tier_c_limit=tier_c_limit,
        )
        ranked.append(replace(assessment, rank=index, priority_tier=tier))
    return tuple(ranked)


def _tier_for_rank_and_score(
    *,
    rank: int,
    score: int,
    tier_a_limit: int,
    tier_b_limit: int,
    tier_c_limit: int,
) -> PriorityTier:
    if rank <= tier_a_limit and score >= 70:
        return PriorityTier.TIER_A_IMMEDIATE
    if rank <= tier_a_limit + tier_b_limit and score >= 50:
        return PriorityTier.TIER_B_STANDARD
    if rank <= tier_a_limit + tier_b_limit + tier_c_limit and score >= 30:
        return PriorityTier.TIER_C_SELECTIVE
    return PriorityTier.TIER_D_DEFERRED


def _applicable_stage_score(stage_relevance: SemanticDimensionAssessment) -> int | None:
    if stage_relevance.status in {
        SemanticAssessmentStatus.NOT_APPLICABLE,
        SemanticAssessmentStatus.INSUFFICIENT_EVIDENCE,
    }:
        return None
    return normalize_semantic_score(stage_relevance.score)


def _highest_domain_status(entity: EntityCandidate) -> str:
    order = {
        OfficialDomainVerificationStatus.VERIFIED_OFFICIAL: 0,
        OfficialDomainVerificationStatus.PROBABLE_OFFICIAL: 1,
        OfficialDomainVerificationStatus.THIRD_PARTY: 2,
        OfficialDomainVerificationStatus.UNRESOLVED: 3,
    }
    statuses = [
        item.verification_status
        for item in entity.official_domain_candidates
    ]
    if not statuses:
        return "none"
    return sorted(statuses, key=lambda item: order.get(item, 9))[0].value


def _flatten_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        items: list[str] = []
        for child in value.values():
            items.extend(_flatten_strings(child))
        return tuple(items)
    if isinstance(value, (list, tuple)):
        items = []
        for child in value:
            items.extend(_flatten_strings(child))
        return tuple(items)
    return ()
