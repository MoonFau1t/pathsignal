import re

from src.models import (
    SearchPlan,
    SearchQuery,
    SearchQueryType,
    SearchScope,
    SourceType,
)


def _slugify(text: str) -> str:
    """
    Convert text into a stable ID-friendly string.
    """

    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:80] if slug else "search_plan"


def _get_source_types_for_query(
    search_query: SearchQuery,
    search_scope: SearchScope,
) -> list[SourceType]:
    """
    Decide which source types should be used for a query.

    Phase 6 only plans source routing.
    It does not execute any search.
    """

    allowed_source_types = set(search_scope.source_types)
    selected_source_types: list[SourceType] = []

    if (
        search_query.query_type == SearchQueryType.JOB_SEARCH
        and search_scope.enable_search_api
        and SourceType.SEARCH_API in allowed_source_types
    ):
        selected_source_types.append(SourceType.SEARCH_API)

    if (
        search_query.query_type
        in {
            SearchQueryType.INDUSTRY_NEWS,
            SearchQueryType.FUNDING_SIGNAL,
            SearchQueryType.COMPANY_DISCOVERY,
        }
        and search_scope.enable_rss
        and SourceType.RSS in allowed_source_types
    ):
        selected_source_types.append(SourceType.RSS)

    if (
        search_scope.enable_selected_websites
        and SourceType.SELECTED_WEBSITE in allowed_source_types
    ):
        selected_source_types.append(SourceType.SELECTED_WEBSITE)

    if not selected_source_types:
        selected_source_types.append(SourceType.SEARCH_API)

    return selected_source_types


def _build_location_terms(locations: list[str]) -> str:
    """
    Convert scope locations into a readable query suffix.
    """

    if not locations:
        return ""

    return " OR ".join(locations)


def build_search_plans(
    search_queries: list[SearchQuery],
    search_scope: SearchScope,
) -> list[SearchPlan]:
    """
    Build SearchPlan objects from SearchQuery and SearchScope.

    SearchPlan = SearchQuery + SearchScope.
    """

    search_plans: list[SearchPlan] = []

    location_terms = _build_location_terms(search_scope.locations)

    for search_query in search_queries:
        source_types = _get_source_types_for_query(
            search_query=search_query,
            search_scope=search_scope,
        )

        scoped_query_text = search_query.query_text

        if location_terms:
            scoped_query_text = f"{scoped_query_text} ({location_terms})"

        plan_id = (
            f"plan_{search_scope.scope_id}_"
            f"{_slugify(search_query.query_text)}"
        )

        search_plans.append(
            SearchPlan(
                plan_id=plan_id,
                query_id=search_query.query_id,
                query_text=scoped_query_text,
                query_type=search_query.query_type,
                career_path_id=search_query.career_path_id,
                career_path_title=search_query.career_path_title,
                scope_id=search_scope.scope_id,
                source_types=source_types,
                locations=search_scope.locations,
                languages=search_scope.languages,
                allowed_domains=search_scope.allowed_domains,
                excluded_domains=search_scope.excluded_domains,
                freshness_days=search_scope.freshness_days,
                max_results=search_scope.max_results_per_query,
                priority=search_query.priority,
                negative_keywords=search_query.negative_keywords,
                metadata={
                    "builder": "rule_based_phase_6",
                    "original_query_text": search_query.query_text,
                    "selected_website_count": len(search_scope.selected_websites),
                    "rss_feed_count": len(search_scope.rss_feeds),
                },
            )
        )

    search_plans.sort(
        key=lambda plan: plan.priority,
        reverse=True,
    )

    return search_plans