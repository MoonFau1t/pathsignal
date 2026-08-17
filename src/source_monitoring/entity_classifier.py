from dataclasses import replace

from src.source_monitoring.entity_discovery_models import (
    CLASSIFICATION_FACET_TAXONOMY_VERSION,
    PRIMARY_ENTITY_KIND_TAXONOMY_VERSION,
    EntityCandidate,
    PrimaryEntityKind,
)


ENTITY_TYPE_CLASSIFICATION_MAP: dict[str, dict[str, object]] = {
    "technology_company": {
        "primary_kind": PrimaryEntityKind.OPERATING_COMPANY,
        "facets": {"business_focus": ("technology",)},
    },
    "ai_native_company": {
        "primary_kind": PrimaryEntityKind.OPERATING_COMPANY,
        "facets": {"business_focus": ("artificial_intelligence",)},
    },
    "enterprise_software_company": {
        "primary_kind": PrimaryEntityKind.OPERATING_COMPANY,
        "facets": {"business_focus": ("enterprise_software",)},
    },
    "industrial_technology_company": {
        "primary_kind": PrimaryEntityKind.OPERATING_COMPANY,
        "facets": {
            "business_focus": ("technology",),
            "sector_focus": ("industrial_technology",),
        },
    },
    "growth_stage_company": {
        "primary_kind": PrimaryEntityKind.OPERATING_COMPANY,
        "facets": {"lifecycle_stage": ("growth_stage",)},
    },
    "portfolio_company": {
        "primary_kind": PrimaryEntityKind.OPERATING_COMPANY,
        "facets": {"capital_relationship": ("portfolio_backed",)},
    },
    "venture_capital_firm": {
        "primary_kind": PrimaryEntityKind.INVESTMENT_FIRM,
        "facets": {"investment_model": ("venture_capital",)},
    },
    "corporate_venture_capital": {
        "primary_kind": PrimaryEntityKind.INVESTMENT_FIRM,
        "facets": {
            "investment_model": ("corporate_venture_capital",),
            "capital_relationship": ("corporate_capital",),
        },
    },
    "private_equity_or_growth_equity_firm": {
        "primary_kind": PrimaryEntityKind.INVESTMENT_FIRM,
        "facets": {"investment_model": ("private_equity", "growth_equity")},
    },
    "startup_accelerator": {
        "primary_kind": PrimaryEntityKind.ECOSYSTEM_SUPPORT_ORGANIZATION,
        "facets": {"service_model": ("accelerator",)},
    },
    "venture_studio": {
        "primary_kind": PrimaryEntityKind.ECOSYSTEM_SUPPORT_ORGANIZATION,
        "facets": {"service_model": ("venture_building",)},
    },
    "management_consulting_firm": {
        "primary_kind": PrimaryEntityKind.PROFESSIONAL_SERVICES_FIRM,
        "facets": {"service_model": ("strategy_consulting",)},
    },
    "technology_consulting_firm": {
        "primary_kind": PrimaryEntityKind.PROFESSIONAL_SERVICES_FIRM,
        "facets": {
            "service_model": (
                "technology_consulting",
                "digital_transformation",
            )
        },
    },
    "boutique_investment_bank_or_fa": {
        "primary_kind": PrimaryEntityKind.PROFESSIONAL_SERVICES_FIRM,
        "facets": {
            "service_model": ("financial_advisory", "investment_banking")
        },
    },
    "research_institute": {
        "primary_kind": PrimaryEntityKind.KNOWLEDGE_INSTITUTION,
        "facets": {"information_role": ("research_producer",)},
    },
    "policy_think_tank": {
        "primary_kind": PrimaryEntityKind.KNOWLEDGE_INSTITUTION,
        "facets": {"information_role": ("policy_research",)},
    },
    "industry_association": {
        "primary_kind": PrimaryEntityKind.ECOSYSTEM_SUPPORT_ORGANIZATION,
        "facets": {"information_role": ("industry_coordination",)},
    },
    "government_or_regulatory_body": {
        "primary_kind": PrimaryEntityKind.PUBLIC_SECTOR_BODY,
        "facets": {"information_role": ("regulatory_information",)},
    },
    "professional_media": {
        "primary_kind": PrimaryEntityKind.INFORMATION_PLATFORM,
        "facets": {"information_role": ("market_intelligence", "news")},
    },
    "investment_data_provider": {
        "primary_kind": PrimaryEntityKind.INFORMATION_PLATFORM,
        "facets": {"information_role": ("funding_data", "market_data")},
    },
    "recruiting_platform": {
        "primary_kind": PrimaryEntityKind.TALENT_MARKET_PLATFORM,
        "facets": {"information_role": ("talent_market_data",)},
    },
}


PRIMARY_KIND_PRECEDENCE: tuple[PrimaryEntityKind, ...] = (
    PrimaryEntityKind.OPERATING_COMPANY,
    PrimaryEntityKind.INVESTMENT_FIRM,
    PrimaryEntityKind.PROFESSIONAL_SERVICES_FIRM,
    PrimaryEntityKind.KNOWLEDGE_INSTITUTION,
    PrimaryEntityKind.PUBLIC_SECTOR_BODY,
    PrimaryEntityKind.INFORMATION_PLATFORM,
    PrimaryEntityKind.TALENT_MARKET_PLATFORM,
    PrimaryEntityKind.ECOSYSTEM_SUPPORT_ORGANIZATION,
)


def classify_entity_type_codes(
    entity_type_codes: tuple[str, ...],
) -> tuple[PrimaryEntityKind, dict[str, tuple[str, ...]]]:
    kinds: set[PrimaryEntityKind] = set()
    facets: dict[str, set[str]] = {}

    for code in entity_type_codes:
        mapping = ENTITY_TYPE_CLASSIFICATION_MAP.get(code)
        if mapping is None:
            continue

        kinds.add(mapping["primary_kind"])
        for dimension, values in mapping["facets"].items():
            facets.setdefault(str(dimension), set()).update(values)

    primary_kind = _select_primary_kind(kinds)
    normalized_facets = {
        dimension: tuple(sorted(values))
        for dimension, values in sorted(facets.items())
    }
    return primary_kind, normalized_facets


def classify_entity_candidate(candidate: EntityCandidate) -> EntityCandidate:
    primary_kind, facets = classify_entity_type_codes(candidate.entity_type_codes)
    merged_facets: dict[str, set[str]] = {
        dimension: set(values)
        for dimension, values in candidate.classification_facets.items()
    }
    for dimension, values in facets.items():
        merged_facets.setdefault(dimension, set()).update(values)

    return replace(
        candidate,
        primary_entity_kind=primary_kind,
        classification_facets={
            dimension: tuple(sorted(values))
            for dimension, values in sorted(merged_facets.items())
        },
    )


def describe_entity_type_classification_mapping() -> dict[str, object]:
    return {
        "primary_kind_taxonomy_version": PRIMARY_ENTITY_KIND_TAXONOMY_VERSION,
        "facet_taxonomy_version": CLASSIFICATION_FACET_TAXONOMY_VERSION,
        "mapping": {
            code: {
                "primary_kind": mapping["primary_kind"].value,
                "facets": mapping["facets"],
            }
            for code, mapping in sorted(ENTITY_TYPE_CLASSIFICATION_MAP.items())
        },
    }


def _select_primary_kind(kinds: set[PrimaryEntityKind]) -> PrimaryEntityKind:
    if not kinds:
        return PrimaryEntityKind.INFORMATION_PLATFORM

    for kind in PRIMARY_KIND_PRECEDENCE:
        if kind in kinds:
            return kind

    return sorted(kinds, key=lambda item: item.value)[0]
