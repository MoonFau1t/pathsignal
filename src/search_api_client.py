from typing import Any
from urllib.parse import urlparse

import requests

from src.models import (
    RawItem,
    SearchAPIExecutionReport,
    SearchAPIResultDiagnostic,
    SearchPlan,
    SearchPlanExecutionStatus,
    SourceType,
    utc_now_iso,
)


class SearchAPIError(Exception):
    """
    Raised when Search API execution fails.
    """


class BraveSearchClient:
    """
    Search API client for Brave Search.

    This client sends SearchPlan query_text to Brave Web Search and converts
    web search results into provider-independent RawItem objects.
    """

    SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(
        self,
        api_key: str,
        timeout_seconds: int = 20,
        dry_run: bool = False,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.dry_run = dry_run
        self.last_result_diagnostics: list[SearchAPIResultDiagnostic] = []

    def search(self, search_plan: SearchPlan) -> list[RawItem]:
        """
        Execute one SearchPlan through the Search API.
        """

        self.last_result_diagnostics = []

        query_text = search_plan.query_text.strip()
        if not query_text:
            raise SearchAPIError(
                f"Search plan {search_plan.plan_id} has an empty query."
            )

        if self.dry_run:
            return self._build_dry_run_items(search_plan)

        if not self.api_key or self.api_key == "your_brave_api_key_here":
            raise SearchAPIError(
                "BRAVE_API_KEY is missing. Add your real key to .env, "
                "or set SEARCH_API_DRY_RUN=true for a no-cost test run."
            )

        params = {
            "q": query_text,
            "count": max(1, min(search_plan.max_results, 20)),
            "result_filter": "web",
            "text_decorations": "false",
            "safesearch": "moderate",
        }

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.api_key,
        }

        try:
            response = requests.get(
                self.SEARCH_ENDPOINT,
                headers=headers,
                params=params,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as error:
            raise SearchAPIError(
                f"Brave Search request timed out for plan {search_plan.plan_id}."
            ) from error
        except requests.ConnectionError as error:
            raise SearchAPIError(
                "Brave Search connection failed for "
                f"plan {search_plan.plan_id}."
            ) from error
        except requests.RequestException as error:
            raise SearchAPIError(
                f"Brave Search request failed for plan {search_plan.plan_id}."
            ) from error

        if response.status_code >= 400:
            raise SearchAPIError(_build_status_error(response.status_code))

        try:
            response_data = response.json()
        except ValueError as error:
            raise SearchAPIError(
                "Brave Search returned malformed provider payload: invalid JSON."
            ) from error

        return self._parse_brave_response(
            response_data=response_data,
            search_plan=search_plan,
        )

    def _parse_brave_response(
        self,
        response_data: Any,
        search_plan: SearchPlan,
    ) -> list[RawItem]:
        """
        Convert Brave response JSON into RawItem objects.
        """

        if not isinstance(response_data, dict):
            raise SearchAPIError(
                "Brave Search returned malformed provider payload: "
                "top-level JSON is not an object."
            )

        web_section = response_data.get("web")
        if web_section is None:
            raise SearchAPIError(
                "Brave Search returned malformed provider payload: missing web section."
            )
        if not isinstance(web_section, dict):
            raise SearchAPIError(
                "Brave Search returned malformed provider payload: web section "
                "is not an object."
            )

        if "results" not in web_section:
            raise SearchAPIError(
                "Brave Search returned malformed provider payload: "
                "missing web.results."
            )

        web_results = web_section.get("results")
        if not isinstance(web_results, list):
            raise SearchAPIError(
                "Brave Search returned malformed provider payload: "
                "web.results is not a list."
            )

        raw_items: list[RawItem] = []
        result_diagnostics: list[SearchAPIResultDiagnostic] = []

        for position, result in enumerate(web_results, start=1):
            if not isinstance(result, dict):
                result_diagnostics.append(
                    SearchAPIResultDiagnostic(
                        plan_id=search_plan.plan_id,
                        query_id=search_plan.query_id,
                        position=position,
                        status="failed_parse",
                        reason="Brave web result is not an object.",
                        metadata={
                            "raw_result": result,
                        },
                    )
                )
                continue

            title = _safe_string(result.get("title"))
            url = _safe_string(result.get("url"))
            description = _safe_string(result.get("description"))

            if not title and not url:
                result_diagnostics.append(
                    SearchAPIResultDiagnostic(
                        plan_id=search_plan.plan_id,
                        query_id=search_plan.query_id,
                        position=position,
                        status="failed_parse",
                        reason="Brave web result is missing both title and url.",
                        metadata={
                            "raw_result": result,
                        },
                    )
                )
                continue

            result_diagnostics.append(
                SearchAPIResultDiagnostic(
                    plan_id=search_plan.plan_id,
                    query_id=search_plan.query_id,
                    position=position,
                    status="converted",
                    reason="Brave web result converted to RawItem.",
                    title=title,
                    url=url,
                )
            )

            raw_items.append(
                RawItem(
                    source_type=SourceType.SEARCH_API,
                    title=title or "Untitled search result",
                    organization=_extract_domain(url),
                    url=url,
                    published_at=None,
                    raw_text=description or title,
                    metadata={
                        "provider": "brave",
                        "search_plan_id": search_plan.plan_id,
                        "query_id": search_plan.query_id,
                        "career_path_id": search_plan.career_path_id,
                        "career_path_title": search_plan.career_path_title,
                        "query_text": search_plan.query_text,
                        "query_type": search_plan.query_type.value,
                        "position": position,
                        "retrieved_at": utc_now_iso(),
                        "age": result.get("age"),
                        "page_age": result.get("page_age"),
                        "language": result.get("language"),
                        "profile": result.get("profile"),
                        "meta_url": result.get("meta_url"),
                        "raw_result": result,
                    },
                )
            )

        self.last_result_diagnostics = result_diagnostics

        return raw_items

    def _build_dry_run_items(self, search_plan: SearchPlan) -> list[RawItem]:
        """
        Return one fake Search API RawItem for local testing without API cost.
        """

        return [
            RawItem(
                source_type=SourceType.SEARCH_API,
                title=f"[DRY RUN] Search result for: {search_plan.query_text}",
                organization="dry-run.local",
                url="https://example.com/dry-run-search-result",
                published_at=utc_now_iso(),
                raw_text=(
                    "This is a dry-run Search API result. "
                    "Set SEARCH_API_DRY_RUN=false and provide BRAVE_API_KEY "
                    "to execute real searches."
                ),
                metadata={
                    "provider": "brave",
                    "mode": "dry_run",
                    "search_plan_id": search_plan.plan_id,
                    "query_id": search_plan.query_id,
                    "career_path_id": search_plan.career_path_id,
                    "career_path_title": search_plan.career_path_title,
                    "query_text": search_plan.query_text,
                },
            )
        ]


def _extract_domain(url: str) -> str:
    """
    Extract domain name from a URL.
    """

    if not url:
        return ""

    parsed_url = urlparse(url)
    return parsed_url.netloc.replace("www.", "")


def _safe_string(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def _build_status_error(status_code: int) -> str:
    if status_code in {401, 403}:
        return (
            "Brave Search authentication failed. Check BRAVE_API_KEY "
            "without exposing it in logs."
        )

    if status_code == 429:
        return "Brave Search rate limit was reached."

    if status_code >= 500:
        return f"Brave Search server error: HTTP {status_code}."

    return f"Brave Search request failed with HTTP {status_code}."


def execute_search_api_plans(
    search_plans: list[SearchPlan],
    client: BraveSearchClient,
    max_plans: int = 5,
    plan_offset: int = 0,
    execution_lifecycle: Any | None = None,
) -> SearchAPIExecutionReport:
    """
    Execute Search API plans and return RawItem results.

    Only plans containing SourceType.SEARCH_API will be executed.
    """

    executable_plans = [
        plan
        for plan in search_plans
        if SourceType.SEARCH_API in plan.source_types
    ]

    executable_plans.sort(
        key=lambda plan: plan.priority,
        reverse=True,
    )

    safe_max_plans = max(0, max_plans)
    safe_plan_offset = max(0, plan_offset)
    selected_plans = executable_plans[
        safe_plan_offset:safe_plan_offset + safe_max_plans
    ]
    selected_plan_ids = {
        plan.plan_id
        for plan in selected_plans
    }

    if execution_lifecycle is not None:
        execution_lifecycle.account_search_api_plan_selection(
            search_plans=search_plans,
            executable_plans=executable_plans,
            selected_plans=selected_plans,
            plan_offset=safe_plan_offset,
            max_plans=safe_max_plans,
        )

    all_raw_items: list[RawItem] = []
    plan_statuses: list[SearchPlanExecutionStatus] = []
    result_diagnostics: list[SearchAPIResultDiagnostic] = []
    raw_item_count_by_plan_id: dict[str, int] = {}

    for selection_index, plan in enumerate(selected_plans, start=safe_plan_offset):
        source_execution_id = None
        if execution_lifecycle is not None:
            source_execution_id = (
                execution_lifecycle.start_search_plan_source_execution(
                    search_plan=plan,
                    selection_order=selection_index,
                    provider="brave",
                    execution_mode=(
                        "dry_run" if client.dry_run else "live"
                    ),
                )
            )

        try:
            plan_results = client.search(plan)
        except Exception as error:
            if execution_lifecycle is not None:
                execution_lifecycle.fail_source_execution(
                    source_execution_id=source_execution_id,
                    error=error,
                )
            raise

        if execution_lifecycle is not None:
            execution_lifecycle.complete_source_execution(
                source_execution_id=source_execution_id,
                raw_items=plan_results,
            )

        raw_item_count_by_plan_id[plan.plan_id] = len(plan_results)
        all_raw_items.extend(plan_results)
        result_diagnostics.extend(client.last_result_diagnostics)

        status = "executed" if plan_results else "executed_no_results"
        reason = (
            "SearchPlan was selected for this Search API batch."
            if plan_results
            else "SearchPlan was selected but Brave returned no converted RawItems."
        )

        plan_statuses.append(
            SearchPlanExecutionStatus(
                plan_id=plan.plan_id,
                query_id=plan.query_id,
                career_path_id=plan.career_path_id,
                career_path_title=plan.career_path_title,
                status=status,
                reason=reason,
                priority=plan.priority,
                selection_index=selection_index,
                batch_offset=safe_plan_offset,
                batch_limit=safe_max_plans,
                raw_items_collected=len(plan_results),
            )
        )

    selected_or_recorded_plan_ids = {
        status.plan_id
        for status in plan_statuses
    }

    for selection_index, plan in enumerate(executable_plans):
        if plan.plan_id in selected_or_recorded_plan_ids:
            continue

        if plan.plan_id in selected_plan_ids:
            continue

        if selection_index < safe_plan_offset:
            status = "skipped_due_to_offset"
            reason = "SearchPlan is before SEARCH_API_PLAN_OFFSET for this batch."
        else:
            status = "deferred_due_to_limit"
            reason = "SearchPlan was outside SEARCH_API_MAX_PLANS for this batch."

        plan_statuses.append(
            SearchPlanExecutionStatus(
                plan_id=plan.plan_id,
                query_id=plan.query_id,
                career_path_id=plan.career_path_id,
                career_path_title=plan.career_path_title,
                status=status,
                reason=reason,
                priority=plan.priority,
                selection_index=selection_index,
                batch_offset=safe_plan_offset,
                batch_limit=safe_max_plans,
                raw_items_collected=raw_item_count_by_plan_id.get(plan.plan_id, 0),
            )
        )

    executable_plan_ids = {
        plan.plan_id
        for plan in executable_plans
    }

    for plan in search_plans:
        if plan.plan_id in executable_plan_ids:
            continue

        plan_statuses.append(
            SearchPlanExecutionStatus(
                plan_id=plan.plan_id,
                query_id=plan.query_id,
                career_path_id=plan.career_path_id,
                career_path_title=plan.career_path_title,
                status="not_executable_for_search_api",
                reason="SearchPlan does not include SourceType.SEARCH_API.",
                priority=plan.priority,
                batch_offset=safe_plan_offset,
                batch_limit=safe_max_plans,
            )
        )

    plan_statuses.sort(
        key=lambda status: (
            status.selection_index is None,
            status.selection_index if status.selection_index is not None else 10**9,
            status.plan_id,
        )
    )

    return SearchAPIExecutionReport(
        raw_items=all_raw_items,
        executed_plan_count=len(selected_plans),
        plan_statuses=plan_statuses,
        result_diagnostics=result_diagnostics,
    )
