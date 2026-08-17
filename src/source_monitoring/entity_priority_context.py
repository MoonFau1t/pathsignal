from dataclasses import replace
from typing import Any

from src.source_monitoring.entity_discovery_models import (
    EntityCandidate,
    EntityCandidateVerificationStatus,
    EntityDiscoveryEvidence,
    EntityDiscoveryPlan,
    EntityUniverseResult,
    OfficialDomainCandidate,
    OfficialDomainVerificationStatus,
)
from src.source_monitoring.entity_identity import normalize_domain
from src.source_monitoring.entity_prioritization_models import (
    COMPACT_CONTEXT_POLICY_VERSION,
)


DUPLICATE_INPUT_CONSOLIDATION_DIAGNOSTIC = "duplicate_input_records_consolidated"


def consolidate_entity_universe_for_prioritization(
    *,
    entity_universe_result: EntityUniverseResult,
) -> tuple[EntityUniverseResult, tuple[str, ...]]:
    """
    Phase 3 ranks unique entity IDs, not duplicate Phase 2 records.
    """

    grouped: dict[str, list[EntityCandidate]] = {}
    for candidate in entity_universe_result.entity_candidates:
        grouped.setdefault(candidate.entity_id, []).append(candidate)

    diagnostics: list[str] = []
    consolidated: list[EntityCandidate] = []
    for entity_id in sorted(grouped):
        records = sorted(
            grouped[entity_id],
            key=lambda item: (
                normalize_domain(item.official_domain_candidates[0].domain)
                if item.official_domain_candidates
                else "",
                item.canonical_name,
                item.confidence,
            ),
        )
        if len(records) > 1:
            diagnostics.append(
                f"{DUPLICATE_INPUT_CONSOLIDATION_DIAGNOSTIC}: "
                f"{entity_id} records={len(records)}"
            )
        consolidated.append(_merge_duplicate_entity_records(records))

    if len(consolidated) == len(entity_universe_result.entity_candidates):
        return entity_universe_result, ()

    return (
        replace(
            entity_universe_result,
            entity_candidates=tuple(consolidated),
            diagnostics=tuple(entity_universe_result.diagnostics)
            + tuple(diagnostics),
            generation_mode=entity_universe_result.generation_mode,
        ),
        tuple(diagnostics),
    )


def build_compact_entity_contexts(
    *,
    entity_universe_result: EntityUniverseResult,
    max_evidence_per_entity: int,
) -> tuple[dict[str, Any], ...]:
    evidence_by_id = {
        item.evidence_id: item
        for item in entity_universe_result.entity_discovery_evidence
    }
    plan_by_id = {
        item.plan_id: item
        for item in entity_universe_result.entity_discovery_plans
    }
    conflicted_entity_ids = {
        entity_id
        for conflict in entity_universe_result.unresolved_identity_conflicts
        for entity_id in conflict.candidate_entity_ids
    }

    contexts = [
        build_compact_entity_context(
            entity=candidate,
            evidence_by_id=evidence_by_id,
            plan_by_id=plan_by_id,
            identity_conflicted=candidate.entity_id in conflicted_entity_ids,
            max_evidence_per_entity=max_evidence_per_entity,
        )
        for candidate in entity_universe_result.entity_candidates
    ]
    return tuple(sorted(contexts, key=lambda item: str(item["entity_id"])))


def _merge_duplicate_entity_records(
    records: list[EntityCandidate],
) -> EntityCandidate:
    primary = sorted(
        records,
        key=lambda item: (
            -item.confidence,
            item.canonical_name,
            item.entity_id,
        ),
    )[0]
    provenance = {
        key: value
        for record in records
        for key, value in sorted(record.provenance.items())
    }
    provenance.update(
        {
            "phase3_input_policy": DUPLICATE_INPUT_CONSOLIDATION_DIAGNOSTIC,
            "duplicate_record_count": len(records),
            "consolidated_entity_id": primary.entity_id,
        }
    )
    return replace(
        primary,
        names_by_language=_merge_language_names(records),
        entity_type_codes=_sorted_unique(
            value
            for record in records
            for value in record.entity_type_codes
        ),
        classification_facets=_merge_facets(records),
        related_entity_type_candidate_ids=_sorted_unique(
            value
            for record in records
            for value in record.related_entity_type_candidate_ids
        ),
        related_information_need_ids=_sorted_unique(
            value
            for record in records
            for value in record.related_information_need_ids
        ),
        related_target_career_path_ids=_sorted_unique(
            value
            for record in records
            for value in record.related_target_career_path_ids
        ),
        official_domain_candidates=_merge_official_domains(records),
        evidence_ids=_sorted_unique(
            value for record in records for value in record.evidence_ids
        ),
        evidence_urls=_sorted_unique(
            value for record in records for value in record.evidence_urls
        ),
        geographic_scope=_merge_geographic_scope(records),
        confidence=max(record.confidence for record in records),
        verification_status=sorted(
            (record.verification_status for record in records),
            key=_entity_verification_status_rank,
        )[0],
        provenance=provenance,
    )


def _merge_language_names(
    records: list[EntityCandidate],
) -> dict[str, tuple[str, ...]]:
    languages = sorted(
        {
            language
            for record in records
            for language in record.names_by_language
        }
    )
    return {
        language: _sorted_unique(
            name
            for record in records
            for name in record.names_by_language.get(language, ())
        )
        for language in languages
    }


def _merge_facets(
    records: list[EntityCandidate],
) -> dict[str, tuple[str, ...]]:
    dimensions = sorted(
        {
            dimension
            for record in records
            for dimension in record.classification_facets
        }
    )
    return {
        dimension: _sorted_unique(
            value
            for record in records
            for value in record.classification_facets.get(dimension, ())
        )
        for dimension in dimensions
    }


def _merge_official_domains(
    records: list[EntityCandidate],
) -> tuple[OfficialDomainCandidate, ...]:
    by_domain: dict[str, list[OfficialDomainCandidate]] = {}
    for record in records:
        for candidate in record.official_domain_candidates:
            domain = normalize_domain(candidate.domain)
            if domain:
                by_domain.setdefault(domain, []).append(candidate)

    merged = []
    for domain, candidates in sorted(by_domain.items()):
        strongest = sorted(
            candidates,
            key=lambda item: (
                _domain_status_rank(item.verification_status),
                -item.confidence,
                item.evidence_url,
            ),
        )[0]
        merged.append(
            OfficialDomainCandidate(
                domain=domain,
                evidence_url=strongest.evidence_url,
                confidence=max(item.confidence for item in candidates),
                verification_status=strongest.verification_status,
                reason="; ".join(
                    _sorted_unique(item.reason for item in candidates if item.reason)
                ),
                schema_version=strongest.schema_version,
            )
        )
    return tuple(merged)


def _merge_geographic_scope(records: list[EntityCandidate]) -> str:
    scopes = _sorted_unique(record.geographic_scope for record in records)
    if len(scopes) == 1:
        return scopes[0]
    return " | ".join(scopes)


def _sorted_unique(values) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if str(value)}))


def build_compact_entity_context(
    *,
    entity: EntityCandidate,
    evidence_by_id: dict[str, EntityDiscoveryEvidence],
    plan_by_id: dict[str, EntityDiscoveryPlan],
    identity_conflicted: bool,
    max_evidence_per_entity: int,
) -> dict[str, Any]:
    return {
        "context_policy_version": COMPACT_CONTEXT_POLICY_VERSION,
        "entity_id": entity.entity_id,
        "canonical_name": entity.canonical_name,
        "names_by_language": {
            language: list(names)
            for language, names in sorted(entity.names_by_language.items())
        },
        "primary_entity_kind": entity.primary_entity_kind.value,
        "entity_type_codes": list(entity.entity_type_codes),
        "classification_facets": {
            dimension: list(values)
            for dimension, values in sorted(entity.classification_facets.items())
        },
        "geographic_scope": entity.geographic_scope,
        "related_entity_type_candidate_ids": list(
            entity.related_entity_type_candidate_ids
        ),
        "related_information_need_ids": list(entity.related_information_need_ids),
        "related_target_career_path_ids": list(
            entity.related_target_career_path_ids
        ),
        "confidence": entity.confidence,
        "verification_status": entity.verification_status.value,
        "official_domain_candidates": _domain_summaries(entity),
        "identity_conflict_status": (
            "unresolved_conflict" if identity_conflicted else "none"
        ),
        "representative_evidence": _representative_evidence_summaries(
            entity=entity,
            evidence_by_id=evidence_by_id,
            plan_by_id=plan_by_id,
            max_evidence_per_entity=max_evidence_per_entity,
        ),
        "provenance": {
            key: value
            for key, value in sorted(entity.provenance.items())
            if key in {"source", "identity_group_key", "merged_entity_ids"}
        },
    }


def _domain_summaries(entity: EntityCandidate) -> list[dict[str, Any]]:
    return [
        {
            "domain": normalize_domain(item.domain),
            "evidence_url": item.evidence_url,
            "confidence": item.confidence,
            "verification_status": item.verification_status.value,
            "reason": _bounded_text(item.reason),
        }
        for item in sorted(
            entity.official_domain_candidates,
            key=lambda item: (
                _domain_status_rank(item.verification_status),
                normalize_domain(item.domain),
                item.evidence_url,
            ),
        )
    ]


def _representative_evidence_summaries(
    *,
    entity: EntityCandidate,
    evidence_by_id: dict[str, EntityDiscoveryEvidence],
    plan_by_id: dict[str, EntityDiscoveryPlan],
    max_evidence_per_entity: int,
) -> list[dict[str, Any]]:
    official_urls = {
        item.evidence_url
        for item in entity.official_domain_candidates
        if item.evidence_url
    }
    evidence_items = [
        evidence_by_id[evidence_id]
        for evidence_id in entity.evidence_ids
        if evidence_id in evidence_by_id
    ]
    ranked = sorted(
        evidence_items,
        key=lambda item: (
            0 if item.url in official_urls else 1,
            0 if _evidence_language(item, plan_by_id) == "zh" else 1,
            0 if _evidence_language(item, plan_by_id) == "en" else 1,
            item.result_rank,
            normalize_domain(item.displayed_domain or item.url),
            item.evidence_id,
        ),
    )

    selected: list[EntityDiscoveryEvidence] = []
    selected_domains: set[str] = set()
    for item in ranked:
        domain = normalize_domain(item.displayed_domain or item.url)
        if domain in selected_domains and len(selected) >= 2:
            continue
        selected.append(item)
        selected_domains.add(domain)
        if len(selected) >= max(1, max_evidence_per_entity):
            break

    return [
        {
            "evidence_id": item.evidence_id,
            "title": _bounded_text(item.title),
            "snippet": _bounded_text(item.snippet, limit=280),
            "url": item.url,
            "displayed_domain": normalize_domain(item.displayed_domain or item.url),
            "result_rank": item.result_rank,
            "language": _evidence_language(item, plan_by_id),
        }
        for item in sorted(selected, key=lambda item: item.evidence_id)
    ]


def _evidence_language(
    evidence: EntityDiscoveryEvidence,
    plan_by_id: dict[str, EntityDiscoveryPlan],
) -> str:
    plan = plan_by_id.get(evidence.plan_id)
    if plan is not None and plan.language:
        return plan.language
    if _contains_cjk(f"{evidence.title} {evidence.snippet}"):
        return "zh"
    return "unknown"


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _bounded_text(value: str, limit: int = 180) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _domain_status_rank(status: OfficialDomainVerificationStatus) -> int:
    order = {
        OfficialDomainVerificationStatus.VERIFIED_OFFICIAL: 0,
        OfficialDomainVerificationStatus.PROBABLE_OFFICIAL: 1,
        OfficialDomainVerificationStatus.THIRD_PARTY: 2,
        OfficialDomainVerificationStatus.UNRESOLVED: 3,
    }
    return order.get(status, 4)


def _entity_verification_status_rank(
    status: EntityCandidateVerificationStatus,
) -> int:
    order = {
        EntityCandidateVerificationStatus.CONFLICTED: 0,
        EntityCandidateVerificationStatus.EVIDENCE_SUPPORTED: 1,
        EntityCandidateVerificationStatus.UNVERIFIED: 2,
    }
    return order.get(status, 3)
