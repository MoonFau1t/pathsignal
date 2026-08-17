import re
import unicodedata
from collections import defaultdict
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from src.database.planning_identity import hash_canonical_value
from src.models import TargetCareerPath
from src.source_monitoring.entity_discovery_models import (
    CLASSIFICATION_FACET_TAXONOMY_VERSION,
    ENTITY_CANDIDATE_SCHEMA_VERSION,
    ENTITY_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
    ENTITY_DISCOVERY_PLAN_SCHEMA_VERSION,
    ENTITY_DISCOVERY_PLANNING_PROMPT_VERSION,
    ENTITY_DISCOVERY_QUERY_SCHEMA_VERSION,
    ENTITY_UNIVERSE_RESULT_SCHEMA_VERSION,
    EntityCandidate,
    EntityDiscoveryEvidence,
    EntityDiscoveryPlan,
    OfficialDomainVerificationStatus,
    PRIMARY_ENTITY_KIND_TAXONOMY_VERSION,
    UnresolvedIdentityConflict,
)
from src.source_monitoring.models import EntityTypeExpansionResult, InformationNeed


_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid"}
_LEGAL_SUFFIX_PATTERN = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|company|co)\b\.?",
    re.IGNORECASE,
)


def normalize_organization_name(value: str | None) -> str:
    """
    Normalize organization names while preserving Chinese characters.
    """

    if value is None:
        return ""

    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = _LEGAL_SUFFIX_PATTERN.sub("", text)
    text = re.sub(r"[，,。.;；:：()\[\]{}<>]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def normalize_domain(value: str | None) -> str:
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    if "://" not in text:
        text = f"https://{text}"

    parsed = urlparse(text)
    domain = parsed.netloc or parsed.path.split("/", 1)[0]
    domain = domain.split("@")[-1].split(":")[0].strip().casefold()

    if domain.startswith("www."):
        domain = domain[4:]

    return domain.rstrip(".")


def normalize_evidence_url(value: str | None) -> str:
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    parsed = urlparse(text if "://" in text else f"https://{text}")
    query_items = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in _TRACKING_QUERY_KEYS
        and not any(key.startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES)
    ]
    normalized = parsed._replace(
        scheme=parsed.scheme.lower() or "https",
        netloc=normalize_domain(parsed.netloc or parsed.path.split("/", 1)[0]),
        query=urlencode(query_items),
        fragment="",
    )
    return urlunparse(normalized)


def build_entity_discovery_query_id(
    *,
    entity_type_candidate_id: str,
    entity_type_code: str,
    language: str,
    region: str,
    query_text: str,
    schema_version: str = ENTITY_DISCOVERY_QUERY_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "entity_type_candidate_id": entity_type_candidate_id,
            "entity_type_code": entity_type_code,
            "language": language,
            "region": region,
            "query_text": query_text,
        }
    )
    return f"entity_query_{digest[:16]}"


def build_entity_discovery_plan_id(
    *,
    query_id: str,
    entity_type_candidate_id: str,
    schema_version: str = ENTITY_DISCOVERY_PLAN_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "query_id": query_id,
            "entity_type_candidate_id": entity_type_candidate_id,
        }
    )
    return f"entity_plan_{digest[:16]}"


def build_entity_discovery_evidence_id(
    *,
    plan_id: str,
    query_id: str,
    result_rank: int,
    url: str,
    title: str,
    schema_version: str = ENTITY_DISCOVERY_EVIDENCE_SCHEMA_VERSION,
) -> str:
    digest = hash_canonical_value(
        {
            "schema_version": schema_version,
            "plan_id": plan_id,
            "query_id": query_id,
            "result_rank": result_rank,
            "url": normalize_evidence_url(url),
            "title": title,
        }
    )
    return f"entity_evidence_{digest[:16]}"


def build_entity_candidate_id(
    *,
    canonical_name: str,
    official_domains: tuple[str, ...],
    entity_type_codes: tuple[str, ...],
    schema_version: str = ENTITY_CANDIDATE_SCHEMA_VERSION,
) -> str:
    identity_value: dict[str, Any] = {
        "schema_version": schema_version,
        "entity_type_codes": sorted(set(entity_type_codes)),
    }

    verified_domains = tuple(sorted(domain for domain in official_domains if domain))
    if verified_domains:
        identity_value["verified_official_domains"] = verified_domains
    else:
        identity_value["canonical_name"] = normalize_organization_name(canonical_name)

    digest = hash_canonical_value(identity_value)
    return f"entity_{digest[:16]}"


def build_identity_conflict_id(
    *,
    candidate_entity_ids: tuple[str, ...],
    reason: str,
) -> str:
    digest = hash_canonical_value(
        {
            "candidate_entity_ids": sorted(candidate_entity_ids),
            "reason": reason,
        }
    )
    return f"identity_conflict_{digest[:16]}"


def build_entity_universe_input_fingerprint(
    *,
    entity_type_expansion_result: EntityTypeExpansionResult,
    information_needs: tuple[InformationNeed, ...],
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    provider_configuration: dict[str, Any],
    generation_limits: dict[str, Any],
    planning_prompt_version: str = ENTITY_DISCOVERY_PLANNING_PROMPT_VERSION,
    primary_kind_taxonomy_version: str = PRIMARY_ENTITY_KIND_TAXONOMY_VERSION,
    facet_taxonomy_version: str = CLASSIFICATION_FACET_TAXONOMY_VERSION,
) -> str:
    return hash_canonical_value(
        {
            "schema_version": ENTITY_UNIVERSE_RESULT_SCHEMA_VERSION,
            "canonical_entity_type_candidates": sorted(
                entity_type_expansion_result.canonical_candidates,
                key=lambda item: item.candidate_id,
            ),
            "phase1_output_hash": entity_type_expansion_result.output_hash,
            "information_needs": sorted(
                information_needs,
                key=lambda item: item.information_need_id,
            ),
            "target_career_paths": sorted(
                target_career_paths,
                key=lambda item: item.path_id,
            ),
            "user_preferences": user_preferences,
            "primary_kind_taxonomy_version": primary_kind_taxonomy_version,
            "facet_taxonomy_version": facet_taxonomy_version,
            "planning_prompt_version": planning_prompt_version,
            "provider_configuration": provider_configuration,
            "generation_limits": generation_limits,
        }
    )


def build_entity_universe_output_hash(
    *,
    entity_discovery_plans: tuple[EntityDiscoveryPlan, ...],
    entity_discovery_evidence: tuple[EntityDiscoveryEvidence, ...],
    entity_candidates: tuple[EntityCandidate, ...],
    rejected_candidates: tuple[Any, ...],
    unresolved_identity_conflicts: tuple[UnresolvedIdentityConflict, ...],
    uncovered_entity_type_candidate_ids: tuple[str, ...],
) -> str:
    stable_evidence = tuple(
        _stable_evidence_for_hash(item)
        for item in sorted(
            entity_discovery_evidence,
            key=lambda item: item.evidence_id,
        )
    )
    return hash_canonical_value(
        {
            "schema_version": ENTITY_UNIVERSE_RESULT_SCHEMA_VERSION,
            "entity_discovery_plans": sorted(
                entity_discovery_plans,
                key=lambda item: item.plan_id,
            ),
            "entity_discovery_evidence": stable_evidence,
            "entity_candidates": sorted(
                entity_candidates,
                key=lambda item: item.entity_id,
            ),
            "rejected_candidates": rejected_candidates,
            "unresolved_identity_conflicts": sorted(
                unresolved_identity_conflicts,
                key=lambda item: item.conflict_id,
            ),
            "uncovered_entity_type_candidate_ids": sorted(
                uncovered_entity_type_candidate_ids
            ),
        }
    )


def resolve_entity_identities(
    candidates: tuple[EntityCandidate, ...],
) -> tuple[tuple[EntityCandidate, ...], tuple[UnresolvedIdentityConflict, ...]]:
    """
    Merge only when deterministic identity evidence is strong enough.
    """

    if not candidates:
        return (), ()

    candidates_by_name: dict[str, list[EntityCandidate]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_name[normalize_organization_name(candidate.canonical_name)].append(
            candidate
        )

    conflicts: list[UnresolvedIdentityConflict] = []
    conflicted_ids: set[str] = set()
    for same_name_candidates in candidates_by_name.values():
        verified_sets = {
            tuple(_verified_domains(candidate))
            for candidate in same_name_candidates
            if _verified_domains(candidate)
        }
        if len(verified_sets) > 1:
            ids = tuple(sorted(candidate.entity_id for candidate in same_name_candidates))
            evidence_ids = tuple(
                sorted(
                    {
                        evidence_id
                        for candidate in same_name_candidates
                        for evidence_id in candidate.evidence_ids
                    }
                )
            )
            conflicts.append(
                UnresolvedIdentityConflict(
                    conflict_id=build_identity_conflict_id(
                        candidate_entity_ids=ids,
                        reason="conflicting verified official domains",
                    ),
                    candidate_entity_ids=ids,
                    reason="conflicting verified official domains",
                    evidence_ids=evidence_ids,
                    diagnostics=(
                        "Candidates share a normalized organization name but "
                        "claim different verified official domains.",
                    ),
                )
            )
            conflicted_ids.update(ids)

    merge_groups: dict[tuple[str, str], list[EntityCandidate]] = defaultdict(list)
    passthrough: list[EntityCandidate] = []

    for candidate in candidates:
        if candidate.entity_id in conflicted_ids:
            passthrough.append(candidate)
            continue

        verified_domains = _verified_domains(candidate)
        identity_group_key = str(candidate.provenance.get("identity_group_key", ""))

        if verified_domains:
            merge_groups[("domain", verified_domains[0])].append(candidate)
        elif identity_group_key:
            merge_groups[("equivalence", identity_group_key)].append(candidate)
        else:
            passthrough.append(candidate)

    merged = passthrough[:]
    for group_candidates in merge_groups.values():
        merged.append(_merge_candidate_group(tuple(group_candidates)))

    ordered = tuple(sorted(merged, key=lambda item: item.entity_id))
    return ordered, tuple(sorted(conflicts, key=lambda item: item.conflict_id))


def _verified_domains(candidate: EntityCandidate) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                normalize_domain(domain_candidate.domain)
                for domain_candidate in candidate.official_domain_candidates
                if domain_candidate.verification_status
                == OfficialDomainVerificationStatus.VERIFIED_OFFICIAL
            }
        )
    )


def _merge_candidate_group(
    candidates: tuple[EntityCandidate, ...],
) -> EntityCandidate:
    if len(candidates) == 1:
        candidate = candidates[0]
        verified_domains = _verified_domains(candidate)
        return replace(
            candidate,
            entity_id=build_entity_candidate_id(
                canonical_name=candidate.canonical_name,
                official_domains=verified_domains,
                entity_type_codes=candidate.entity_type_codes,
            ),
        )

    ordered = tuple(sorted(candidates, key=lambda item: item.entity_id))
    base = ordered[0]
    names_by_language: dict[str, set[str]] = defaultdict(set)
    facets: dict[str, set[str]] = defaultdict(set)

    for candidate in ordered:
        for language, names in candidate.names_by_language.items():
            names_by_language[language].update(names)
        for dimension, values in candidate.classification_facets.items():
            facets[dimension].update(values)

    official_domains = tuple(
        sorted(
            {
                candidate
                for item in ordered
                for candidate in item.official_domain_candidates
            },
            key=lambda item: (item.domain, item.verification_status.value),
        )
    )
    entity_type_codes = tuple(
        sorted({code for item in ordered for code in item.entity_type_codes})
    )
    verified_domains = tuple(
        sorted({domain for item in ordered for domain in _verified_domains(item)})
    )

    return replace(
        base,
        entity_id=build_entity_candidate_id(
            canonical_name=base.canonical_name,
            official_domains=verified_domains,
            entity_type_codes=entity_type_codes,
        ),
        names_by_language={
            language: tuple(sorted(names))
            for language, names in sorted(names_by_language.items())
        },
        entity_type_codes=entity_type_codes,
        classification_facets={
            dimension: tuple(sorted(values))
            for dimension, values in sorted(facets.items())
        },
        related_entity_type_candidate_ids=tuple(
            sorted(
                {
                    value
                    for item in ordered
                    for value in item.related_entity_type_candidate_ids
                }
            )
        ),
        related_information_need_ids=tuple(
            sorted(
                {
                    value
                    for item in ordered
                    for value in item.related_information_need_ids
                }
            )
        ),
        related_target_career_path_ids=tuple(
            sorted(
                {
                    value
                    for item in ordered
                    for value in item.related_target_career_path_ids
                }
            )
        ),
        official_domain_candidates=official_domains,
        evidence_ids=tuple(
            sorted({value for item in ordered for value in item.evidence_ids})
        ),
        evidence_urls=tuple(
            sorted({value for item in ordered for value in item.evidence_urls})
        ),
        confidence=max(item.confidence for item in ordered),
        provenance={
            **base.provenance,
            "merged_entity_ids": tuple(item.entity_id for item in ordered),
        },
    )


def _stable_evidence_for_hash(evidence: EntityDiscoveryEvidence) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "plan_id": evidence.plan_id,
        "query_id": evidence.query_id,
        "result_rank": evidence.result_rank,
        "title": evidence.title,
        "snippet": evidence.snippet,
        "url": normalize_evidence_url(evidence.url),
        "displayed_domain": evidence.displayed_domain,
        "search_provider": evidence.search_provider,
        "raw_metadata": {
            key: value
            for key, value in sorted(evidence.raw_metadata.items())
            if key != "retrieved_at"
        },
        "schema_version": evidence.schema_version,
    }
