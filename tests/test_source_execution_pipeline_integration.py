import importlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.config import DEFAULT_DATABASE_FILE
from src.database.connection import open_database_connection
from src.database.migrations import initialize_database
from src.database.repositories.career_signal_repository import CareerSignalRepository
from src.database.repositories.pipeline_run_repository import PipelineRunRepository
from src.database.repositories.planning_bundle_repository import PlanningBundleRepository
from src.database.repositories.source_execution_repository import (
    SourceExecutionRepository,
    SourceExecutionRepositoryError,
)
from src.database.repositories.source_item_repository import SourceItemRepository
from src.models import (
    AIFilterExecutionReport,
    RSSFeed,
    RawItem,
    SearchAPIExecutionReport,
    SearchPlan,
    SelectedWebsite,
    SourceType,
)
from src.pipeline import MockPipeline, execute_pipeline_runtime
from src.rss_client import execute_rss_feeds
from src.search_api_client import execute_search_api_plans
from src.selected_website_client import execute_selected_websites
from tests.test_source_execution_repository import make_bundle_write


class ControlledSearchError(RuntimeError):
    pass


class FakeSearchClient:
    def __init__(
        self,
        *,
        dry_run=False,
        fail_plan_ids=(),
        zero_result_plan_ids=(),
        duplicate_results=False,
        shared_result=False,
        before_search=None,
    ):
        self.dry_run = dry_run
        self.fail_plan_ids = set(fail_plan_ids)
        self.zero_result_plan_ids = set(zero_result_plan_ids)
        self.duplicate_results = duplicate_results
        self.shared_result = shared_result
        self.before_search = before_search
        self.calls = []
        self.last_result_diagnostics = []
        self.original_error = ControlledSearchError("controlled search failure")

    def search(self, search_plan):
        self.calls.append(search_plan.plan_id)
        if self.before_search is not None:
            self.before_search(search_plan)
        if search_plan.plan_id in self.fail_plan_ids:
            raise self.original_error
        if search_plan.plan_id in self.zero_result_plan_ids:
            return []

        suffix = "shared" if self.shared_result else search_plan.plan_id
        item = RawItem(
            source_type=SourceType.SEARCH_API,
            title=f"Result {suffix}",
            organization="Example",
            url=f"https://example.com/{suffix}",
            published_at=None,
            raw_text=f"Result body {suffix}",
            metadata={
                "provider": "brave",
                "mode": "dry_run" if self.dry_run else "live",
                "position": 4,
                "search_plan_id": search_plan.plan_id,
                "query_id": search_plan.query_id,
            },
        )
        return [item, item] if self.duplicate_results else [item]


class FakeRSSClient:
    def __init__(self, *, failures=()):
        self.dry_run = False
        self.max_items_per_feed = 5
        self.failures = set(failures)
        self.calls = []

    def fetch_feed(self, rss_feed, search_plans):
        self.calls.append(rss_feed.url)
        if rss_feed.url in self.failures:
            raise RuntimeError("controlled RSS failure")
        return [
            RawItem(
                source_type=SourceType.RSS,
                title=f"RSS {rss_feed.name}",
                organization=rss_feed.name,
                url=f"{rss_feed.url}/item",
                published_at=None,
                raw_text="RSS body",
                metadata={
                    "provider": "rss",
                    "feed_name": rss_feed.name,
                    "feed_url": rss_feed.url,
                    "position": 2,
                },
            )
        ]


class FakeWebsiteClient:
    def __init__(self, *, failures=()):
        self.dry_run = False
        self.max_items_per_site = 5
        self.failures = set(failures)
        self.calls = []

    def fetch_website(self, website, search_plans):
        self.calls.append(website.url)
        if website.url in self.failures:
            raise RuntimeError("controlled website failure")
        return [
            RawItem(
                source_type=SourceType.SELECTED_WEBSITE,
                title=f"Site {website.name}",
                organization=website.name,
                url=f"{website.url}/item",
                published_at=None,
                raw_text="Website body",
                metadata={
                    "provider": "selected_website",
                    "website_name": website.name,
                    "website_url": website.url,
                    "position": 3,
                },
            )
        ]


class TemporaryExecutionPipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "phase7b.db"
        initialize_database(database_path=self.database_path)
        self.run_repository = PipelineRunRepository(self.database_path)
        self.planning_repository = PlanningBundleRepository(self.database_path)
        self.source_item_repository = SourceItemRepository(self.database_path)
        self.career_signal_repository = CareerSignalRepository(self.database_path)
        self.execution_repository = SourceExecutionRepository(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def count_rows(self, table):
        connection = open_database_connection(self.database_path)
        try:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            connection.close()

    def rows(self, sql, params=()):
        connection = open_database_connection(self.database_path)
        try:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]
        finally:
            connection.close()

    def execute_sql(self, sql, params=()):
        connection = open_database_connection(self.database_path)
        try:
            connection.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def make_scope(
        self,
        *,
        enable_search_api=True,
        enable_rss=True,
        enable_selected_websites=True,
        rss_feeds=(),
        selected_websites=(),
    ):
        scope = make_bundle_write().search_scope
        scope.enable_search_api = enable_search_api
        scope.enable_rss = enable_rss
        scope.enable_selected_websites = enable_selected_websites
        scope.rss_feeds = list(rss_feeds)
        scope.selected_websites = list(selected_websites)
        return scope

    def build_pipeline(
        self,
        *,
        search_client=None,
        rss_client=None,
        website_client=None,
        max_plans=2,
        plan_offset=0,
        scope=None,
        execution_repository="actual",
        pipeline_run_repository="actual",
        planning_repository="actual",
        source_item_repository="actual",
        career_signal_repository=None,
        execution_mode="live",
        search_api_executor=None,
        rss_executor=None,
        selected_website_executor=None,
    ):
        bundle = make_bundle_write()
        scope = scope or bundle.search_scope
        search_client = search_client or FakeSearchClient()
        rss_client = rss_client or FakeRSSClient()
        website_client = website_client or FakeWebsiteClient()
        execution_repository = (
            self.execution_repository
            if execution_repository == "actual"
            else execution_repository
        )
        pipeline_run_repository = (
            self.run_repository
            if pipeline_run_repository == "actual"
            else pipeline_run_repository
        )
        planning_repository = (
            self.planning_repository
            if planning_repository == "actual"
            else planning_repository
        )
        source_item_repository = (
            self.source_item_repository
            if source_item_repository == "actual"
            else source_item_repository
        )

        if search_api_executor is None:
            search_api_executor = lambda plans, lifecycle=None: execute_search_api_plans(
                plans,
                search_client,
                max_plans=max_plans,
                plan_offset=plan_offset,
                execution_lifecycle=lifecycle,
            )
        if rss_executor is None:
            rss_executor = lambda active_scope, plans, lifecycle=None: execute_rss_feeds(
                active_scope.rss_feeds,
                plans,
                rss_client,
                max_feeds=5,
                execution_lifecycle=lifecycle,
            )
        if selected_website_executor is None:
            selected_website_executor = (
                lambda active_scope, plans, lifecycle=None: execute_selected_websites(
                    active_scope.selected_websites,
                    plans,
                    website_client,
                    max_sites=5,
                    execution_lifecycle=lifecycle,
                )
            )

        pipeline = MockPipeline(
            raw_item_loader=lambda: [],
            user_profile_loader=lambda: bundle.user_profile,
            search_scope_loader=lambda: scope,
            career_path_generator=lambda profile: bundle.target_career_paths,
            search_query_generator=lambda paths: bundle.search_queries,
            search_plan_builder=lambda queries, active_scope: bundle.search_plans,
            search_api_executor=search_api_executor,
            rss_executor=rss_executor,
            selected_website_executor=selected_website_executor,
            ai_filter_executor=lambda raw_items, profile, paths: AIFilterExecutionReport(),
            normalizer=lambda raw_items, results: [],
            source_item_repository=source_item_repository,
            career_signal_repository=career_signal_repository,
            planning_bundle_repository=planning_repository,
            user_preferences_loader=lambda: bundle.user_preferences,
            planning_model_provider=bundle.model_provider,
            planning_model_name=bundle.model_name,
            planning_prompt_version=bundle.prompt_version,
            planning_generator_config=bundle.generator_config,
            pipeline_run_repository=pipeline_run_repository,
            source_execution_repository=execution_repository,
            execution_mode=execution_mode,
        )
        return pipeline, search_client, rss_client, website_client

    def execute(self, pipeline, *, output_persister=None):
        with redirect_stdout(io.StringIO()):
            return execute_pipeline_runtime(
                pipeline,
                output_persister=(
                    output_persister or (lambda output: output.to_dict())
                ),
            )

    def only_run(self):
        rows = self.run_repository.list_recent_runs(limit=20)
        self.assertEqual(len(rows), 1)
        return rows[0]


class RegistrationIntegrationTests(TemporaryExecutionPipelineTestCase):
    def test_every_bundle_plan_is_registered_after_attachment(self):
        pipeline, _, _, _ = self.build_pipeline()
        self.execute(pipeline)
        run = self.only_run()
        coverage = self.execution_repository.get_run_search_plan_coverage(run.run_id)
        self.assertEqual(coverage.registered_plans, coverage.total_bundle_plans)
        self.assertEqual(coverage.total_bundle_plans, 3)

    def test_registration_occurs_before_external_search(self):
        observed_counts = []

        def observe(_plan):
            observed_counts.append(self.count_rows("run_search_plan_statuses"))

        pipeline, _, _, _ = self.build_pipeline(
            search_client=FakeSearchClient(before_search=observe)
        )
        self.execute(pipeline)
        self.assertEqual(observed_counts, [3, 3])

    def test_exactly_one_ledger_row_exists_per_run_and_plan(self):
        pipeline, _, _, _ = self.build_pipeline()
        self.execute(pipeline)
        duplicates = self.rows(
            """
            SELECT run_id, planning_search_plan_id, COUNT(*) AS count
            FROM run_search_plan_statuses
            GROUP BY run_id, planning_search_plan_id
            HAVING COUNT(*) <> 1
            """
        )
        self.assertEqual(duplicates, [])

    def test_reused_bundle_gets_a_new_run_specific_ledger(self):
        first, _, _, _ = self.build_pipeline()
        self.execute(first)
        second, _, _, _ = self.build_pipeline()
        self.execute(second)
        self.assertEqual(self.count_rows("planning_bundles"), 1)
        self.assertEqual(self.count_rows("pipeline_runs"), 2)
        self.assertEqual(self.count_rows("run_search_plan_statuses"), 6)

    def test_registration_is_called_once_by_pipeline(self):
        recording = Mock(wraps=self.execution_repository, unsafe=True)
        pipeline, _, _, _ = self.build_pipeline(execution_repository=recording)
        self.execute(pipeline)
        recording.register_run_search_plans.assert_called_once()

    def test_registration_failure_prevents_external_execution_and_fails_run(self):
        failing = Mock(wraps=self.execution_repository)
        failing.register_run_search_plans.side_effect = RuntimeError("registration failed")
        search_client = FakeSearchClient()
        pipeline, _, _, _ = self.build_pipeline(
            execution_repository=failing,
            search_client=search_client,
        )
        with self.assertRaisesRegex(RuntimeError, "registration"):
            self.execute(pipeline)
        self.assertEqual(search_client.calls, [])
        run = self.only_run()
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.failure_stage, "search_plan_registration")


class SelectionSkipIntegrationTests(TemporaryExecutionPipelineTestCase):
    def test_selected_plans_enter_execution_in_priority_order(self):
        pipeline, client, _, _ = self.build_pipeline(max_plans=2)
        self.execute(pipeline)
        self.assertEqual(client.calls, ["plan_strategy_primary", "plan_strategy_secondary"])

    def test_max_plan_exclusions_are_skipped(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        statuses = self.execution_repository.list_run_search_plan_statuses(
            self.only_run().run_id
        )
        self.assertEqual([row.status for row in statuses], ["completed", "skipped", "skipped"])
        self.assertEqual({row.skip_reason for row in statuses[1:]}, {"max_plans_limit"})

    def test_offset_exclusions_receive_plan_offset_reason(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1, plan_offset=1)
        self.execute(pipeline)
        statuses = self.execution_repository.list_run_search_plan_statuses(
            self.only_run().run_id
        )
        reasons = {row.plan_identity: row.skip_reason for row in statuses}
        self.assertEqual(reasons["plan_strategy_primary"], "plan_offset")
        self.assertEqual(reasons["plan_product"], "max_plans_limit")

    def test_skipped_plans_are_never_executed(self):
        pipeline, client, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        self.assertEqual(client.calls, ["plan_strategy_primary"])

    def test_intentional_skips_are_distinct_from_missing_plans(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        coverage = self.execution_repository.get_run_search_plan_coverage(
            self.only_run().run_id
        )
        self.assertEqual(coverage.skipped, 2)
        self.assertEqual(coverage.missing_unregistered, 0)

    def test_non_search_api_plan_is_skipped_with_source_reason(self):
        bundle = make_bundle_write()
        bundle.search_plans[2].source_types = [SourceType.RSS]
        pipeline, _, _, _ = self.build_pipeline(max_plans=3)
        pipeline.search_plan_builder = lambda queries, scope: bundle.search_plans
        pipeline.search_query_generator = lambda paths: bundle.search_queries
        pipeline.career_path_generator = lambda profile: bundle.target_career_paths
        self.execute(pipeline)
        statuses = self.execution_repository.list_run_search_plan_statuses(
            self.only_run().run_id
        )
        self.assertEqual(statuses[2].skip_reason, "not_executable_for_search_api")

    def test_disabled_search_api_skips_all_plans_and_executes_none(self):
        scope = self.make_scope(enable_search_api=False)
        pipeline, client, _, _ = self.build_pipeline(scope=scope)
        self.execute(pipeline)
        statuses = self.execution_repository.list_run_search_plan_statuses(
            self.only_run().run_id
        )
        self.assertEqual(client.calls, [])
        self.assertEqual({row.status for row in statuses}, {"skipped"})
        self.assertEqual({row.skip_reason for row in statuses}, {"search_api_disabled"})


class SearchAPIExecutionIntegrationTests(TemporaryExecutionPipelineTestCase):
    def test_actual_attempt_creates_one_source_execution(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        self.assertEqual(self.count_rows("source_executions"), 1)

    def test_success_transitions_ledger_to_completed(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        execution = self.rows("SELECT * FROM source_executions")[0]
        status = self.rows(
            "SELECT status FROM run_search_plan_statuses WHERE planning_search_plan_id = ?",
            (execution["planning_search_plan_id"],),
        )[0]
        self.assertEqual((execution["status"], status["status"]), ("completed", "completed"))

    def test_failed_attempt_transitions_execution_and_ledger_to_failed(self):
        client = FakeSearchClient(fail_plan_ids={"plan_strategy_primary"})
        pipeline, _, _, _ = self.build_pipeline(search_client=client, max_plans=1)
        with self.assertRaises(ControlledSearchError):
            self.execute(pipeline)
        execution = self.rows("SELECT * FROM source_executions")[0]
        status = self.rows(
            "SELECT status FROM run_search_plan_statuses WHERE planning_search_plan_id = ?",
            (execution["planning_search_plan_id"],),
        )[0]
        self.assertEqual((execution["status"], status["status"]), ("failed", "failed"))

    def test_zero_result_attempt_completes_with_zero_counts(self):
        client = FakeSearchClient(zero_result_plan_ids={"plan_strategy_primary"})
        pipeline, _, _, _ = self.build_pipeline(search_client=client, max_plans=1)
        self.execute(pipeline)
        execution = self.execution_repository.list_source_executions(
            self.only_run().run_id
        )[0]
        self.assertEqual(execution.status, "completed")
        self.assertEqual((execution.returned_item_count, execution.discovered_item_count), (0, 0))

    def test_exact_planning_search_plan_row_id_is_used(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        run = self.only_run()
        bundle_plans = self.planning_repository.list_plans_for_bundle(run.planning_bundle_id)
        execution = self.execution_repository.list_source_executions(run.run_id)[0]
        self.assertEqual(execution.planning_search_plan_id, bundle_plans[0]["search_plan_row_id"])

    def test_cross_bundle_plan_cannot_be_started_through_pipeline_mapping(self):
        other = make_bundle_write("_other").search_plans[0]

        def executor(plans, lifecycle):
            lifecycle.account_search_api_plan_selection(
                search_plans=plans,
                executable_plans=plans,
                selected_plans=[],
                plan_offset=0,
                max_plans=0,
            )
            lifecycle.start_search_plan_source_execution(
                search_plan=other,
                selection_order=0,
                provider="brave",
                execution_mode="mocked",
            )

        pipeline, _, _, _ = self.build_pipeline(search_api_executor=executor)
        with self.assertRaisesRegex(RuntimeError, "exact Planning Bundle"):
            self.execute(pipeline)
        self.assertEqual(self.count_rows("source_executions"), 0)

    def test_repeated_execution_of_one_plan_is_rejected(self):
        def executor(plans, lifecycle):
            lifecycle.account_search_api_plan_selection(
                search_plans=plans,
                executable_plans=plans,
                selected_plans=[plans[0]],
                plan_offset=0,
                max_plans=1,
            )
            lifecycle.start_search_plan_source_execution(
                search_plan=plans[0], selection_order=0, provider="brave", execution_mode="mocked"
            )
            lifecycle.start_search_plan_source_execution(
                search_plan=plans[0], selection_order=0, provider="brave", execution_mode="mocked"
            )

        pipeline, _, _, _ = self.build_pipeline(search_api_executor=executor)
        with self.assertRaisesRegex(RuntimeError, "start failed"):
            self.execute(pipeline)
        self.assertEqual(self.count_rows("source_executions"), 1)

    def test_dry_run_result_completes_without_source_item_persistence(self):
        client = FakeSearchClient(dry_run=True)
        pipeline, _, _, _ = self.build_pipeline(search_client=client, max_plans=1)
        self.execute(pipeline)
        execution = self.execution_repository.list_source_executions(
            self.only_run().run_id
        )[0]
        self.assertEqual(execution.status, "completed")
        self.assertEqual(execution.discovered_item_count, 0)
        self.assertEqual(self.count_rows("source_items"), 0)


class DiscoveryIntegrationTests(TemporaryExecutionPipelineTestCase):
    def test_persisted_source_item_is_linked_to_execution(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        self.assertEqual(self.count_rows("source_items"), 1)
        self.assertEqual(self.count_rows("source_item_discoveries"), 1)

    def test_result_position_is_preserved(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        discovery = self.rows("SELECT * FROM source_item_discoveries")[0]
        self.assertEqual(discovery["result_position"], 4)

    def test_duplicate_raw_items_create_one_discovery(self):
        pipeline, _, _, _ = self.build_pipeline(
            search_client=FakeSearchClient(duplicate_results=True),
            max_plans=1,
        )
        self.execute(pipeline)
        self.assertEqual(self.count_rows("source_items"), 1)
        self.assertEqual(self.count_rows("source_item_discoveries"), 1)

    def test_one_source_item_links_to_executions_from_multiple_runs(self):
        first, _, _, _ = self.build_pipeline(
            search_client=FakeSearchClient(shared_result=True), max_plans=1
        )
        self.execute(first)
        second, _, _, _ = self.build_pipeline(
            search_client=FakeSearchClient(shared_result=True), max_plans=1
        )
        self.execute(second)
        self.assertEqual(self.count_rows("source_items"), 1)
        self.assertEqual(self.count_rows("source_item_discoveries"), 2)

    def test_discovery_failure_is_surfaced_and_execution_failed(self):
        failing = Mock(wraps=self.execution_repository)
        failing.record_discoveries.side_effect = RuntimeError("discovery failed")
        pipeline, _, _, _ = self.build_pipeline(
            execution_repository=failing, max_plans=1
        )
        with self.assertRaisesRegex(RuntimeError, "discovery failed"):
            self.execute(pipeline)
        execution = self.rows("SELECT status FROM source_executions")[0]
        self.assertEqual(execution["status"], "failed")
        self.assertEqual(self.only_run().failure_stage, "source_item_discovery")

    def test_source_item_content_is_not_duplicated_after_batch_stage(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        self.assertEqual(self.count_rows("source_items"), 1)


class ConfigSourceIntegrationTests(TemporaryExecutionPipelineTestCase):
    def test_rss_creates_execution_without_search_plan(self):
        feed = RSSFeed(name="Feed", url="https://example.com/feed")
        scope = self.make_scope(rss_feeds=[feed])
        pipeline, _, _, _ = self.build_pipeline(scope=scope, max_plans=0)
        self.execute(pipeline)
        rss = next(
            row for row in self.execution_repository.list_source_executions(self.only_run().run_id)
            if row.source_type == SourceType.RSS.value
        )
        self.assertIsNone(rss.planning_search_plan_id)
        self.assertEqual((rss.source_name, rss.source_locator), (feed.name, feed.url))

    def test_selected_website_creates_execution_without_search_plan(self):
        site = SelectedWebsite(name="Site", url="https://example.com/site")
        scope = self.make_scope(selected_websites=[site])
        pipeline, _, _, _ = self.build_pipeline(scope=scope, max_plans=0)
        self.execute(pipeline)
        execution = next(
            row for row in self.execution_repository.list_source_executions(self.only_run().run_id)
            if row.source_type == SourceType.SELECTED_WEBSITE.value
        )
        self.assertIsNone(execution.planning_search_plan_id)
        self.assertEqual(execution.source_key, site.url)

    def test_config_sources_create_no_fake_ledger_rows(self):
        feed = RSSFeed(name="Feed", url="https://example.com/feed")
        site = SelectedWebsite(name="Site", url="https://example.com/site")
        scope = self.make_scope(rss_feeds=[feed], selected_websites=[site])
        pipeline, _, _, _ = self.build_pipeline(scope=scope, max_plans=0)
        self.execute(pipeline)
        self.assertEqual(self.count_rows("run_search_plan_statuses"), 3)
        self.assertEqual(self.count_rows("source_executions"), 2)

    def test_disabled_rss_creates_no_attempt(self):
        feed = RSSFeed(name="Feed", url="https://example.com/feed")
        client = FakeRSSClient()
        scope = self.make_scope(enable_rss=False, rss_feeds=[feed])
        pipeline, _, _, _ = self.build_pipeline(scope=scope, rss_client=client)
        self.execute(pipeline)
        self.assertEqual(client.calls, [])
        executions = self.execution_repository.list_source_executions(
            self.only_run().run_id
        )
        self.assertFalse(any(row.source_type == "rss" for row in executions))

    def test_disabled_selected_website_creates_no_attempt(self):
        site = SelectedWebsite(name="Site", url="https://example.com/site")
        client = FakeWebsiteClient()
        scope = self.make_scope(enable_selected_websites=False, selected_websites=[site])
        pipeline, _, _, _ = self.build_pipeline(scope=scope, website_client=client)
        self.execute(pipeline)
        self.assertEqual(client.calls, [])

    def test_failed_rss_attempt_is_recorded_and_current_continue_policy_is_preserved(self):
        feed = RSSFeed(name="Feed", url="https://example.com/feed")
        scope = self.make_scope(rss_feeds=[feed])
        pipeline, _, _, _ = self.build_pipeline(
            scope=scope,
            rss_client=FakeRSSClient(failures={feed.url}),
            max_plans=0,
        )
        self.execute(pipeline)
        rss = next(
            row
            for row in self.execution_repository.list_source_executions(
                self.only_run().run_id
            )
            if row.source_type == "rss"
        )
        self.assertEqual(rss.status, "failed")
        self.assertEqual(self.only_run().status, "completed")

    def test_config_source_discoveries_are_persisted(self):
        feed = RSSFeed(name="Feed", url="https://example.com/feed")
        scope = self.make_scope(rss_feeds=[feed])
        pipeline, _, _, _ = self.build_pipeline(scope=scope, max_plans=0)
        self.execute(pipeline)
        self.assertEqual(self.count_rows("source_item_discoveries"), 1)
        rows = self.rows(
            "SELECT result_position FROM source_item_discoveries"
        )
        self.assertEqual(rows[0]["result_position"], 2)


class AccountingCompletionIntegrationTests(TemporaryExecutionPipelineTestCase):
    def test_run_completes_when_all_plans_are_completed_or_skipped(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        self.assertEqual(self.only_run().status, "completed")
        self.execution_repository.assert_run_search_plan_accounting_complete(self.only_run().run_id)

    def test_accounting_validation_occurs_before_run_completion(self):
        events = []
        execution = Mock(wraps=self.execution_repository, unsafe=True)
        runs = Mock(wraps=self.run_repository)
        execution.assert_run_search_plan_accounting_complete.side_effect = (
            lambda run_id: (
                events.append("accounting"),
                self.execution_repository.assert_run_search_plan_accounting_complete(run_id),
            )[1]
        )
        runs.complete_run.side_effect = lambda *args, **kwargs: (
            events.append("completion"),
            self.run_repository.complete_run(*args, **kwargs),
        )[1]
        pipeline, _, _, _ = self.build_pipeline(
            execution_repository=execution,
            pipeline_run_repository=runs,
        )
        self.execute(pipeline)
        self.assertEqual(events, ["accounting", "completion"])

    def test_missing_ledger_row_prevents_completion_and_fails_run(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)

        def corrupt(output):
            run_id = self.run_repository.list_recent_runs(limit=1)[0].run_id
            self.execute_sql(
                """
                DELETE FROM run_search_plan_statuses
                WHERE run_search_plan_status_id = (
                    SELECT run_search_plan_status_id
                    FROM run_search_plan_statuses
                    WHERE run_id = ? AND status = 'skipped'
                    LIMIT 1
                )
                """,
                (run_id,),
            )
            return output.to_dict()

        with self.assertRaisesRegex(RuntimeError, "accounting validation"):
            self.execute(pipeline, output_persister=corrupt)
        run = self.only_run()
        self.assertEqual(
            (run.status, run.failure_stage),
            ("failed", "search_plan_accounting_validation"),
        )

    def test_pending_plan_prevents_completion(self):
        def executor(plans, lifecycle):
            lifecycle.account_search_api_plan_selection(
                search_plans=plans,
                executable_plans=plans,
                selected_plans=plans[:2],
                plan_offset=0,
                max_plans=2,
            )
            source_execution_id = lifecycle.start_search_plan_source_execution(
                search_plan=plans[0], selection_order=0, provider="brave", execution_mode="mocked"
            )
            lifecycle.complete_source_execution(
                source_execution_id=source_execution_id,
                raw_items=[],
            )
            return SearchAPIExecutionReport(executed_plan_count=1)

        pipeline, _, _, _ = self.build_pipeline(search_api_executor=executor)
        with self.assertRaisesRegex(RuntimeError, "accounting validation"):
            self.execute(pipeline)
        coverage = self.execution_repository.get_run_search_plan_coverage(self.only_run().run_id)
        self.assertEqual(coverage.pending, 1)

    def test_running_plan_prevents_completion(self):
        def executor(plans, lifecycle):
            lifecycle.account_search_api_plan_selection(
                search_plans=plans,
                executable_plans=plans,
                selected_plans=plans[:1],
                plan_offset=0,
                max_plans=1,
            )
            lifecycle.start_search_plan_source_execution(
                search_plan=plans[0], selection_order=0, provider="brave", execution_mode="mocked"
            )
            return SearchAPIExecutionReport(executed_plan_count=1)

        pipeline, _, _, _ = self.build_pipeline(search_api_executor=executor)
        with self.assertRaisesRegex(RuntimeError, "accounting validation"):
            self.execute(pipeline)
        coverage = self.execution_repository.get_run_search_plan_coverage(self.only_run().run_id)
        self.assertEqual(coverage.running, 1)

    def test_summary_contains_accurate_execution_coverage(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        summary = self.only_run().summary
        self.assertEqual(summary["total_search_plan_count"], 3)
        self.assertEqual(summary["completed_search_plan_count"], 1)
        self.assertEqual(summary["failed_search_plan_count"], 0)
        self.assertEqual(summary["skipped_search_plan_count"], 2)
        self.assertEqual(summary["source_execution_count"], 1)
        self.assertEqual(summary["discovery_count"], 1)

    def test_query_coverage_remains_visible_after_completion(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        self.execute(pipeline)
        coverage = self.execution_repository.get_run_search_query_coverage(self.only_run().run_id)
        self.assertEqual(len(coverage), 3)
        self.assertTrue(any(row.no_search_plans_generated for row in coverage))


class FailureCompatibilityIntegrationTests(TemporaryExecutionPipelineTestCase):
    def test_original_search_exception_is_preserved(self):
        client = FakeSearchClient(fail_plan_ids={"plan_strategy_primary"})
        pipeline, _, _, _ = self.build_pipeline(search_client=client, max_plans=1)
        with self.assertRaises(ControlledSearchError) as context:
            self.execute(pipeline)
        self.assertIs(context.exception, client.original_error)
        self.assertEqual(self.only_run().failure_stage, "external_search")

    def test_failure_persistence_error_keeps_original_exception_primary(self):
        client = FakeSearchClient(fail_plan_ids={"plan_strategy_primary"})
        failing = Mock(wraps=self.execution_repository)
        failing.fail_execution.side_effect = RuntimeError("failure persistence failed")
        pipeline, _, _, _ = self.build_pipeline(
            search_client=client,
            execution_repository=failing,
            max_plans=1,
        )
        with self.assertRaises(ControlledSearchError) as context:
            self.execute(pipeline)
        self.assertIs(context.exception, client.original_error)
        self.assertTrue(
            any(
                "failure persistence also failed" in note
                for note in context.exception.__notes__
            )
        )

    def test_planning_bundle_reuse_behavior_is_unchanged(self):
        first, _, _, _ = self.build_pipeline()
        self.execute(first)
        second, _, _, _ = self.build_pipeline()
        self.execute(second)
        self.assertEqual(second.planning_generation_mode, "database_reuse")
        self.assertEqual(self.count_rows("planning_bundles"), 1)

    def test_source_item_persistence_without_execution_repository_is_unchanged(self):
        pipeline, _, _, _ = self.build_pipeline(execution_repository=None, max_plans=1)
        self.execute(pipeline)
        self.assertEqual(self.count_rows("source_items"), 1)
        self.assertEqual(self.count_rows("source_executions"), 0)

    def test_execution_repository_does_not_require_planning_repository(self):
        pipeline, _, _, _ = self.build_pipeline(planning_repository=None, max_plans=1)
        self.execute(pipeline)
        self.assertEqual(self.only_run().status, "completed")
        self.assertIsNone(self.only_run().planning_bundle_id)
        self.assertEqual(self.count_rows("source_executions"), 0)

    def test_career_signal_repository_remains_independently_injectable(self):
        pipeline, _, _, _ = self.build_pipeline(
            career_signal_repository=self.career_signal_repository,
            max_plans=1,
        )
        self.execute(pipeline)
        self.assertEqual(self.count_rows("career_signals"), 0)

    def test_pipeline_output_contract_keys_are_unchanged(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        output, _ = self.execute(pipeline)
        self.assertNotIn("pipeline_run_id", output.to_dict())
        self.assertNotIn("source_executions", output.to_dict())

    def test_saved_json_contains_no_execution_ledger_fields(self):
        pipeline, _, _, _ = self.build_pipeline(max_plans=1)
        _, saved = self.execute(
            pipeline,
            output_persister=lambda output: json.loads(json.dumps(output.to_dict(), default=str)),
        )
        self.assertNotIn("pipeline_run_id", saved)
        self.assertNotIn("source_execution_count", saved["summary"])

    def test_mock_mode_creates_no_run_or_execution_rows(self):
        pipeline, _, _, _ = self.build_pipeline(execution_mode="mock", max_plans=1)
        self.execute(pipeline)
        self.assertEqual(self.count_rows("pipeline_runs"), 0)
        self.assertEqual(self.count_rows("source_executions"), 0)

    def test_source_items_and_career_signals_have_no_run_ownership(self):
        for table in ("source_items", "career_signals"):
            columns = self.rows(f"PRAGMA table_info({table})")
            self.assertNotIn("run_id", {row["name"] for row in columns})

    def test_filter_integration_is_optional_and_has_no_migration_008(self):
        tables = {
            row["name"]
            for row in self.rows(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("filter_decisions", tables)
        self.assertEqual(list(Path("src/database/sql").glob("008*.sql")), [])
        self.assertIsNone(
            self.build_pipeline()[0].filter_decision_repository
        )

    def test_main_constructs_and_injects_source_execution_repository(self):
        main_module = importlib.import_module("src.main")
        captured = {}

        class FakeRepository:
            def __init__(self, database_path):
                self.database_path = database_path

        class FakePipeline:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def run(self):
                return Mock(summary=Mock(
                    total_target_career_paths=0,
                    total_search_queries=0,
                    total_search_plans=0,
                    total_search_api_plans_executed=0,
                    total_search_api_plans_deferred=0,
                    total_search_api_result_failures=0,
                    total_rss_feeds_executed=0,
                    total_selected_websites_executed=0,
                    total_raw_items=0,
                    total_raw_items_sent_to_ai_filter=0,
                    total_ai_filter_results=0,
                    total_filtered_raw_items=0,
                    total_rejected_raw_items=0,
                    total_career_signals=0,
                ))

        with patch.object(main_module, "get_database_path", return_value=self.database_path), \
            patch.object(main_module, "initialize_database"), \
            patch.object(main_module, "PipelineRunRepository", FakeRepository), \
            patch.object(main_module, "PlanningBundleRepository", FakeRepository), \
            patch.object(main_module, "SourceItemRepository", FakeRepository), \
            patch.object(main_module, "CareerSignalRepository", FakeRepository), \
            patch.object(main_module, "SourceExecutionRepository", FakeRepository), \
            patch.object(main_module, "MockPipeline", FakePipeline), \
            patch.object(main_module, "ensure_project_directories"), \
            patch.object(main_module, "validate_required_planning_inputs"), \
            patch.object(main_module, "load_user_preferences_from_json", return_value={}), \
            patch.object(main_module, "_file_sha256", return_value="hash"), \
            patch.object(main_module, "save_json", return_value=Path("out.json")), \
            redirect_stdout(io.StringIO()):
            main_module.main()

        repository = captured["source_execution_repository"]
        self.assertIsInstance(repository, FakeRepository)
        self.assertEqual(repository.database_path, self.database_path)

    def test_tests_do_not_modify_development_database_or_call_network(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = DEFAULT_DATABASE_FILE.stat().st_mtime if before_exists else None
        with patch("requests.get") as request_get, patch(
            "src.career_path_generator.TargetCareerPathClient"
        ) as llm_client:
            pipeline, _, _, _ = self.build_pipeline(max_plans=1)
            self.execute(pipeline)
        request_get.assert_not_called()
        llm_client.assert_not_called()
        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)
        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)


if __name__ == "__main__":
    unittest.main()
