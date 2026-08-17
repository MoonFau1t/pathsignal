import re

from src.models import (
    CareerPathCategory,
    SearchQuery,
    SearchQueryType,
    TargetCareerPath,
)


DEFAULT_NEGATIVE_KEYWORDS = [
    "senior director",
    "director",
    "vp",
    "vice president",
    "principal",
    "partner",
    "head of",
    "course",
    "bootcamp",
    "certificate",
    "unpaid",
]


CATEGORY_QUERY_TEMPLATES: dict[CareerPathCategory, list[dict]] = {
    CareerPathCategory.CORPORATE_STRATEGY: [
        {
            "template": "{term} open role",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.20,
            "rationale": "Find open corporate strategy roles.",
        },
        {
            "template": "{term} technology company",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.12,
            "rationale": "Focus corporate strategy search on technology companies.",
        },
        {
            "template": "{term} AI startup",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.10,
            "rationale": "Find strategy roles inside AI startup environments.",
        },
    ],
    CareerPathCategory.AI_STRATEGY: [
        {
            "template": "{term} open role",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.20,
            "rationale": "Find open AI strategy roles.",
        },
        {
            "template": "{term} consulting",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.14,
            "rationale": "Find AI strategy roles in consulting or advisory contexts.",
        },
        {
            "template": "{term} digital transformation",
            "query_type": SearchQueryType.INDUSTRY_NEWS,
            "priority_boost": 0.08,
            "rationale": "Track AI transformation demand signals.",
        },
    ],
    CareerPathCategory.VENTURE_CAPITAL: [
        {
            "template": "{term} open role",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.20,
            "rationale": "Find open venture capital roles.",
        },
        {
            "template": "{term} early stage fund",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.12,
            "rationale": "Focus on early-stage investment roles.",
        },
        {
            "template": "{term} AI startup investment",
            "query_type": SearchQueryType.FUNDING_SIGNAL,
            "priority_boost": 0.10,
            "rationale": "Track VC and AI startup investment signals.",
        },
    ],
    CareerPathCategory.TECH_CONSULTING: [
        {
            "template": "{term} open role",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.20,
            "rationale": "Find open technology consulting roles.",
        },
        {
            "template": "{term} AI transformation",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.14,
            "rationale": "Focus on AI transformation consulting opportunities.",
        },
        {
            "template": "{term} enterprise AI adoption",
            "query_type": SearchQueryType.INDUSTRY_NEWS,
            "priority_boost": 0.08,
            "rationale": "Track enterprise technology consulting demand signals.",
        },
    ],
    CareerPathCategory.BOUTIQUE_FA: [
        {
            "template": "{term} open role",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.20,
            "rationale": "Find open boutique finance or FA roles.",
        },
        {
            "template": "{term} technology M&A",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.12,
            "rationale": "Focus on technology-related transaction roles.",
        },
        {
            "template": "{term} startup financing",
            "query_type": SearchQueryType.FUNDING_SIGNAL,
            "priority_boost": 0.08,
            "rationale": "Track financing signals related to FA opportunities.",
        },
    ],
    CareerPathCategory.MARKET_RESEARCH: [
        {
            "template": "{term} open role",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.18,
            "rationale": "Find open market research roles.",
        },
        {
            "template": "{term} technology industry",
            "query_type": SearchQueryType.JOB_SEARCH,
            "priority_boost": 0.10,
            "rationale": "Focus market research search on technology industry.",
        },
        {
            "template": "{term} AI market trend",
            "query_type": SearchQueryType.INDUSTRY_NEWS,
            "priority_boost": 0.08,
            "rationale": "Track AI-related market research signals.",
        },
    ],
    CareerPathCategory.UNKNOWN: [
        {
            "template": "{term} open role",
            "query_type": SearchQueryType.GENERAL_RESEARCH,
            "priority_boost": 0.05,
            "rationale": "Run a general search for this career direction.",
        }
    ],
}


def _slugify(text: str) -> str:
    """
    Convert text into a stable ID-friendly string.
    """

    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:80] if slug else "query"


def _calculate_priority(
    career_path: TargetCareerPath,
    priority_boost: float,
) -> float:
    """
    Convert career path fit score into query priority.

    Output range:
    - 0.00 means lowest priority
    - 1.00 means highest priority
    """

    base_priority = career_path.fit_score / 100
    priority = base_priority + priority_boost

    return round(min(max(priority, 0.0), 1.0), 2)


def _deduplicate_queries(search_queries: list[SearchQuery]) -> list[SearchQuery]:
    """
    Remove duplicated query_text items while keeping the highest-priority version.
    """

    query_map: dict[str, SearchQuery] = {}

    for query in search_queries:
        normalized_text = query.query_text.lower().strip()

        existing_query = query_map.get(normalized_text)

        if existing_query is None:
            query_map[normalized_text] = query
            continue

        if query.priority > existing_query.priority:
            query_map[normalized_text] = query

    deduplicated_queries = list(query_map.values())

    deduplicated_queries.sort(
        key=lambda query: query.priority,
        reverse=True,
    )

    return deduplicated_queries


def _deduplicate_queries_by_persistence_identity(
    search_queries: list[SearchQuery],
) -> list[SearchQuery]:
    """
    Remove duplicate SearchQuery identities within one career path.

    Planning persistence enforces uniqueness by career path row plus
    query_identity. Keep the first generated query for that canonical scope.
    """

    seen: set[tuple[str, str]] = set()
    deduplicated: list[SearchQuery] = []

    for query in search_queries:
        key = (query.career_path_id, query.query_id)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(query)

    return deduplicated


def _metadata_string_list(
    career_path: TargetCareerPath,
    key: str,
) -> list[str]:
    """
    Read a string list from TargetCareerPath metadata.
    """

    value = career_path.metadata.get(key, [])

    if not isinstance(value, list):
        return []

    return [
        str(item).strip()
        for item in value
        if item is not None and str(item).strip()
    ]


def _collect_seed_terms(career_path: TargetCareerPath) -> list[str]:
    """
    Collect query seed terms from LLM metadata and legacy fields.
    """

    seed_terms: list[str] = []
    seen: set[str] = set()

    for term in (
        _metadata_string_list(career_path, "search_seed_terms_en")
        + _metadata_string_list(career_path, "search_seed_terms_zh")
        + career_path.search_seed_terms
        + career_path.suggested_roles
    ):
        normalized = term.strip()
        key = normalized.lower()

        if not normalized or key in seen:
            continue

        seen.add(key)
        seed_terms.append(normalized)

    return seed_terms


def generate_search_queries(
    target_career_paths: list[TargetCareerPath],
    max_queries_per_path: int = 8,
) -> list[SearchQuery]:
    """
    Generate SearchQuery objects from TargetCareerPath objects.

    Phase 5 only creates query instructions.
    It does not execute searches.
    """

    generated_queries: list[SearchQuery] = []

    for career_path in target_career_paths:
        seed_terms = _collect_seed_terms(career_path)

        templates = CATEGORY_QUERY_TEMPLATES.get(
            career_path.category,
            CATEGORY_QUERY_TEMPLATES[CareerPathCategory.UNKNOWN],
        )

        query_count_for_path = 0

        for seed_term in seed_terms:
            for template_config in templates:
                if query_count_for_path >= max_queries_per_path:
                    break

                query_text = template_config["template"].format(
                    term=seed_term
                )

                query_id = (
                    f"q_{career_path.path_id}_"
                    f"{_slugify(query_text)}"
                )

                generated_queries.append(
                    SearchQuery(
                        query_id=query_id,
                        career_path_id=career_path.path_id,
                        career_path_title=career_path.title,
                        query_text=query_text,
                        query_type=template_config["query_type"],
                        priority=_calculate_priority(
                            career_path=career_path,
                            priority_boost=template_config["priority_boost"],
                        ),
                        target_roles=career_path.suggested_roles,
                        keywords=career_path.keywords,
                        negative_keywords=DEFAULT_NEGATIVE_KEYWORDS,
                        rationale=template_config["rationale"],
                        metadata={
                            "generator": "rule_based_phase_5",
                            "source_career_path_fit_score": career_path.fit_score,
                            "seed_term": seed_term,
                        },
                    )
                )

                query_count_for_path += 1

            if query_count_for_path >= max_queries_per_path:
                break

    return _deduplicate_queries_by_persistence_identity(
        _deduplicate_queries(generated_queries)
    )
