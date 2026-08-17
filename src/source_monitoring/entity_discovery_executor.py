from typing import Any

from src.config import BRAVE_API_KEY, SEARCH_API_DRY_RUN, SEARCH_API_TIMEOUT_SECONDS
from src.models import SearchPlan, SearchQueryType, SourceType, utc_now_iso
from src.search_api_client import BraveSearchClient, SearchAPIError
from src.source_monitoring.entity_discovery_models import (
    EntityDiscoveryEvidence,
    EntityDiscoveryPlan,
)
from src.source_monitoring.entity_identity import (
    build_entity_discovery_evidence_id,
    normalize_domain,
    normalize_evidence_url,
)


class EntityDiscoveryExecutionError(Exception):
    """
    Raised when Phase 2 evidence execution fails.
    """


def execute_entity_discovery_plans(
    *,
    plans: tuple[EntityDiscoveryPlan, ...],
    search_client: Any | None = None,
    search_provider: str = "brave",
) -> tuple[tuple[EntityDiscoveryEvidence, ...], tuple[str, ...]]:
    """
    Execute Phase 2 plans and retain plan/query provenance on every result.
    """

    client = search_client or BraveSearchClient(
        api_key=BRAVE_API_KEY,
        timeout_seconds=SEARCH_API_TIMEOUT_SECONDS,
        dry_run=SEARCH_API_DRY_RUN,
    )
    evidence: list[EntityDiscoveryEvidence] = []
    diagnostics: list[str] = []

    for plan in sorted(plans, key=lambda item: item.plan_id):
        for query in plan.queries:
            transport_plan = _to_transport_search_plan(plan, query.query_text)
            try:
                raw_items = client.search(transport_plan)
            except SearchAPIError as error:
                diagnostics.append(
                    f"EntityDiscoveryPlan {plan.plan_id} failed search execution: "
                    f"{error}"
                )
                continue

            for rank, item in enumerate(raw_items, start=1):
                metadata = dict(getattr(item, "metadata", {}) or {})
                url = normalize_evidence_url(getattr(item, "url", ""))
                title = str(getattr(item, "title", "") or "")
                snippet = str(getattr(item, "raw_text", "") or "")
                displayed_domain = normalize_domain(
                    getattr(item, "organization", "") or url
                )
                evidence.append(
                    EntityDiscoveryEvidence(
                        evidence_id=build_entity_discovery_evidence_id(
                            plan_id=plan.plan_id,
                            query_id=query.query_id,
                            result_rank=rank,
                            url=url,
                            title=title,
                        ),
                        plan_id=plan.plan_id,
                        query_id=query.query_id,
                        result_rank=rank,
                        title=title,
                        snippet=snippet,
                        url=url,
                        displayed_domain=displayed_domain,
                        search_provider=str(
                            metadata.get("provider") or search_provider
                        ),
                        retrieved_at=str(
                            metadata.get("retrieved_at") or utc_now_iso()
                        ),
                        raw_metadata={
                            **metadata,
                            "entity_discovery_plan_id": plan.plan_id,
                            "entity_discovery_query_id": query.query_id,
                            "entity_type_candidate_id": plan.entity_type_candidate_id,
                            "entity_type_code": plan.entity_type_code,
                            "language": query.language,
                            "region": query.region,
                            "raw_result_rank": rank,
                        },
                    )
                )

    return tuple(sorted(evidence, key=lambda item: item.evidence_id)), tuple(diagnostics)


def _to_transport_search_plan(
    plan: EntityDiscoveryPlan,
    query_text: str,
) -> SearchPlan:
    return SearchPlan(
        plan_id=plan.plan_id,
        query_id=plan.queries[0].query_id,
        query_text=query_text,
        query_type=SearchQueryType.GENERAL_RESEARCH,
        career_path_id="source_monitoring_phase2",
        career_path_title="Source Monitoring Entity Discovery",
        scope_id="source_monitoring_entity_discovery",
        source_types=[SourceType.SEARCH_API],
        locations=[plan.region],
        languages=[plan.language],
        max_results=plan.max_results,
        priority=plan.priority,
        metadata={
            "phase": "source_monitoring_phase2",
            "entity_type_candidate_id": plan.entity_type_candidate_id,
            "entity_type_code": plan.entity_type_code,
        },
    )
