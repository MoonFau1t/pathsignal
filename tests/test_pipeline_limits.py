import unittest
from unittest.mock import patch

from src.ai_filter import AIFilterClient, execute_ai_filter
from src.models import (
    CareerPathCategory,
    RawItem,
    SearchAPIExecutionReport,
    SearchPlan,
    SearchPlanExecutionStatus,
    SearchQueryType,
    SearchScope,
    SourceType,
    TargetCareerPath,
    UserProfile,
)
from src.normalizer import normalize_raw_items_to_career_signals
from src.pipeline import MockPipeline
from src.search_api_client import BraveSearchClient, execute_search_api_plans


class FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class FakeSearchClient:
    def __init__(self):
        self.searched_plan_ids = []
        self.last_result_diagnostics = []

    def search(self, search_plan):
        self.searched_plan_ids.append(search_plan.plan_id)
        return [
            build_raw_item(
                title=f"Strategy analyst result for {search_plan.plan_id}",
                url=f"https://example.com/{search_plan.plan_id}",
            )
        ]


def build_raw_item(
    title="Strategy analyst role",
    url="https://example.com/job",
    source_type=SourceType.SEARCH_API,
):
    return RawItem(
        source_type=source_type,
        title=title,
        organization="example.com",
        url=url,
        published_at=None,
        raw_text=title,
    )


def build_user_profile():
    return UserProfile(
        profile_id="profile_1",
        name="Test User",
        background_summary="Strategy analyst interested in AI.",
    )


def build_career_path(path_id="path_1", title="AI Strategy", fit_score=90):
    return TargetCareerPath(
        path_id=path_id,
        title=title,
        category=CareerPathCategory.AI_STRATEGY,
        description=f"{title} roles.",
        fit_score=fit_score,
        keywords=["strategy", "ai"],
        suggested_roles=["Strategy Analyst"],
        search_seed_terms=["AI strategy"],
    )


def build_plan(index, career_path_id="path_1", priority=None):
    return SearchPlan(
        plan_id=f"plan_{index:02d}",
        query_id=f"query_{index:02d}",
        query_text=f"strategy analyst {index}",
        query_type=SearchQueryType.JOB_SEARCH,
        career_path_id=career_path_id,
        career_path_title=f"Career Path {career_path_id}",
        scope_id="scope_1",
        source_types=[SourceType.SEARCH_API],
        priority=(100 - index if priority is None else priority),
    )


def build_ai_filter_client():
    return AIFilterClient(
        provider="test",
        api_key="",
        base_url="",
        model="test",
        dry_run=True,
    )


class SearchPlanLimitTests(unittest.TestCase):
    def test_72_plans_with_limit_5_records_67_deferred(self):
        plans = [build_plan(index) for index in range(72)]
        client = FakeSearchClient()

        report = execute_search_api_plans(
            search_plans=plans,
            client=client,
            max_plans=5,
        )

        self.assertEqual(report.executed_plan_count, 5)
        self.assertEqual(
            client.searched_plan_ids,
            [f"plan_{index:02d}" for index in range(5)],
        )
        deferred_statuses = [
            status
            for status in report.plan_statuses
            if status.status == "deferred_due_to_limit"
        ]
        self.assertEqual(len(deferred_statuses), 67)

    def test_repeated_runs_repeat_first_batch_and_offset_selects_next_batch(self):
        plans = [build_plan(index) for index in range(12)]
        first_client = FakeSearchClient()
        repeated_client = FakeSearchClient()
        next_batch_client = FakeSearchClient()

        execute_search_api_plans(plans, first_client, max_plans=5, plan_offset=0)
        execute_search_api_plans(plans, repeated_client, max_plans=5, plan_offset=0)
        execute_search_api_plans(plans, next_batch_client, max_plans=5, plan_offset=5)

        self.assertEqual(
            first_client.searched_plan_ids,
            repeated_client.searched_plan_ids,
        )
        self.assertEqual(
            next_batch_client.searched_plan_ids,
            [f"plan_{index:02d}" for index in range(5, 10)],
        )

    def test_multiple_career_path_selection_is_visible_in_plan_statuses(self):
        plans = [
            build_plan(0, career_path_id="path_a", priority=1.0),
            build_plan(1, career_path_id="path_b", priority=0.9),
            build_plan(2, career_path_id="path_c", priority=0.8),
            build_plan(3, career_path_id="path_a", priority=0.7),
            build_plan(4, career_path_id="path_b", priority=0.6),
            build_plan(5, career_path_id="path_c", priority=0.5),
        ]

        report = execute_search_api_plans(
            search_plans=plans,
            client=FakeSearchClient(),
            max_plans=4,
        )

        executed_path_ids = [
            status.career_path_id
            for status in report.plan_statuses
            if status.status == "executed"
        ]
        self.assertEqual(executed_path_ids, ["path_a", "path_b", "path_c", "path_a"])


class BraveResultRetentionTests(unittest.TestCase):
    def test_one_plan_returning_multiple_brave_results_keeps_all_valid_results(self):
        payload = {
            "web": {
                "results": [
                    {
                        "title": f"Strategy Analyst {index}",
                        "url": f"https://example.com/jobs/{index}",
                        "description": f"Relevant role {index}.",
                    }
                    for index in range(10)
                ]
            }
        }
        client = BraveSearchClient(api_key="key", dry_run=False)

        with patch(
            "src.search_api_client.requests.get",
            return_value=FakeResponse(payload=payload),
        ):
            items = client.search(build_plan(0))

        self.assertEqual(len(items), 10)
        self.assertEqual(
            [item.metadata["position"] for item in items],
            list(range(1, 11)),
        )

    def test_failed_brave_result_parsing_is_recorded(self):
        payload = {
            "web": {
                "results": [
                    {
                        "title": "Strategy Analyst",
                        "url": "https://example.com/jobs/1",
                    },
                    "not an object",
                    {
                        "description": "Missing title and URL.",
                    },
                ]
            }
        }
        client = BraveSearchClient(api_key="key", dry_run=False)

        with patch(
            "src.search_api_client.requests.get",
            return_value=FakeResponse(payload=payload),
        ):
            items = client.search(build_plan(0))

        self.assertEqual(len(items), 1)
        failed_diagnostics = [
            diagnostic
            for diagnostic in client.last_result_diagnostics
            if diagnostic.status == "failed_parse"
        ]
        self.assertEqual(len(failed_diagnostics), 2)


class AIFilterFullCoverageTests(unittest.TestCase):
    def test_all_raw_items_are_processed(self):
        raw_items = [
            build_raw_item("Strategy analyst role", "https://example.com/1"),
            build_raw_item("Unrelated page", "https://example.com/2"),
            build_raw_item("AI market trend", "https://example.com/3"),
            build_raw_item("Venture capital role", "https://example.com/4"),
        ]

        report = execute_ai_filter(
            raw_items=raw_items,
            user_profile=build_user_profile(),
            target_career_paths=[build_career_path()],
            client=build_ai_filter_client(),
        )

        self.assertEqual(report.executed_count, 4)
        self.assertEqual(len(report.ai_filter_results), 4)
        self.assertEqual(len(report.raw_item_statuses), 4)
        self.assertTrue(
            all(status.status.startswith("processed_") for status in report.raw_item_statuses)
        )

    def test_duplicate_urls_are_each_processed_by_the_direct_executor(self):
        raw_items = [
            build_raw_item("Strategy analyst role", "https://example.com/dup"),
            build_raw_item("Strategy analyst role copy", "https://example.com/dup"),
        ]

        report = execute_ai_filter(
            raw_items=raw_items,
            user_profile=build_user_profile(),
            target_career_paths=[build_career_path()],
            client=build_ai_filter_client(),
        )

        self.assertEqual(report.executed_count, 2)
        self.assertEqual(len(report.ai_filter_results), 2)
        self.assertEqual(
            [status.raw_item_index for status in report.raw_item_statuses],
            [0, 1],
        )

    def test_ai_filter_rejection_is_distinguishable_from_acceptance(self):
        raw_items = [
            build_raw_item("Bootcamp course certificate", "https://example.com/course"),
            build_raw_item("Strategy analyst role", "https://example.com/job"),
            build_raw_item("AI market trend", "https://example.com/trend"),
        ]

        report = execute_ai_filter(
            raw_items=raw_items,
            user_profile=build_user_profile(),
            target_career_paths=[build_career_path()],
            client=build_ai_filter_client(),
        )

        self.assertEqual(report.raw_item_statuses[0].status, "processed_rejected")
        self.assertEqual(report.raw_item_statuses[1].status, "processed_accepted")
        self.assertEqual(report.raw_item_statuses[2].status, "processed_accepted")


class PipelineSummaryTests(unittest.TestCase):
    def test_output_summary_counts_reconcile(self):
        plans = [build_plan(index) for index in range(5)]
        search_raw_items = [
            build_raw_item("Strategy analyst role", "https://example.com/job"),
            build_raw_item("Strategy analyst duplicate", "https://example.com/job"),
            build_raw_item("AI market trend", "https://example.com/trend"),
        ]
        plan_statuses = [
            SearchPlanExecutionStatus(
                plan_id=plan.plan_id,
                query_id=plan.query_id,
                career_path_id=plan.career_path_id,
                career_path_title=plan.career_path_title,
                status="executed" if index < 2 else "deferred_due_to_limit",
                reason="test",
                priority=plan.priority,
                selection_index=index,
                batch_limit=2,
                raw_items_collected=1 if index < 2 else 0,
            )
            for index, plan in enumerate(plans)
        ]
        ai_client = build_ai_filter_client()
        pipeline = MockPipeline(
            raw_item_loader=lambda: [
                build_raw_item(
                    title="Mock strategy item",
                    url="https://example.com/mock",
                    source_type=SourceType.MOCK_JOB,
                ),
            ],
            user_profile_loader=build_user_profile,
            search_scope_loader=lambda: SearchScope(
                scope_id="scope_1",
                name="Test scope",
                source_types=[SourceType.SEARCH_API],
            ),
            career_path_generator=lambda user_profile: [build_career_path()],
            search_query_generator=lambda target_career_paths: [],
            search_plan_builder=lambda search_queries, search_scope: plans,
            search_api_executor=lambda search_plans: SearchAPIExecutionReport(
                raw_items=search_raw_items,
                executed_plan_count=2,
                plan_statuses=plan_statuses,
            ),
            rss_executor=lambda search_scope, search_plans: ([], 0),
            selected_website_executor=lambda search_scope, search_plans: ([], 0),
            ai_filter_executor=lambda raw_items, user_profile, career_paths: (
                execute_ai_filter(
                    raw_items=raw_items,
                    user_profile=user_profile,
                    target_career_paths=career_paths,
                    client=ai_client,
                )
            ),
            normalizer=normalize_raw_items_to_career_signals,
        )

        output = pipeline.run()
        summary = output.summary

        self.assertEqual(summary.total_search_api_plans_executed, 2)
        self.assertEqual(summary.total_search_api_plans_deferred, 3)
        self.assertEqual(summary.total_raw_items, 4)
        self.assertEqual(summary.total_raw_items_sent_to_ai_filter, 4)
        self.assertEqual(summary.total_raw_items_failed_before_filter, 0)
        self.assertEqual(
            summary.total_raw_items,
            summary.total_raw_items_sent_to_ai_filter
            + summary.total_raw_items_failed_before_filter,
        )
        self.assertEqual(summary.total_duplicate_raw_item_urls, 1)
        self.assertEqual(len(output.raw_item_filter_statuses), 4)


if __name__ == "__main__":
    unittest.main()
