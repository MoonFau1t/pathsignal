from dataclasses import fields
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.config import DEFAULT_DATABASE_FILE
from src.database.connection import open_database_connection
from src.database.migrations import initialize_database
from src.database.repositories.career_signal_repository import (
    CareerSignalRepository,
)
from src.database.repositories.filter_decision_repository import (
    DEFERRED,
    HISTORICAL_DUPLICATE_REASON,
    PENDING,
    FilterDecisionRepository,
)
from src.database.repositories.pipeline_run_repository import (
    PipelineRunRepository,
    PipelineRunStart,
)
from src.database.repositories.source_execution_repository import (
    SourceExecutionCompletion,
    SourceExecutionRepository,
    SourceExecutionStart,
)
from src.database.repositories.source_item_repository import (
    SourceItemPersistenceResult,
    SourceItemRepository,
    SourceItemUpsertSummary,
)
from src.database.source_identity import canonicalize_url, fingerprint_raw_item
from src.models import (
    PipelineRunOutput,
    PipelineSummary,
    RSSFeed,
    RawItem,
    SelectedWebsite,
    SourceType,
)
from src.monitoring_runtime import (
    MonitoringAcquisitionDispatcher,
    MonitoringCandidateRegistrar,
    MonitoringRuntimeCompatibilityError,
    build_monitoring_source_execution_start,
)
from src.source_monitoring.acquisition_models import (
    AcquisitionMethod,
    Phase7MonitoringHandoff,
)
from src.source_monitoring.source_discovery_models import SourceRole
from src.rss_client import RSSClient
from src.selected_website_client import SelectedWebsiteClient


def make_raw_item(
    article: str = "a",
    *,
    source_type: SourceType = SourceType.RSS,
    provider: str = "rss",
    url: str | None = None,
    title: str | None = None,
    organization: str = "Example Publisher",
    published_at: str | None = "2026-08-10T00:00:00+00:00",
    raw_text: str | None = None,
    metadata: dict | None = None,
) -> RawItem:
    item_metadata = {"provider": provider, "guid": f"guid-{article}"}
    item_metadata.update(metadata or {})
    return RawItem(
        source_type=source_type,
        title=title or f"Article {article.upper()}",
        organization=organization,
        url=url if url is not None else f"https://example.com/articles/{article}",
        published_at=published_at,
        raw_text=raw_text or f"Summary for article {article}.",
        metadata=item_metadata,
    )


def make_handoff(
    method: AcquisitionMethod = AcquisitionMethod.RSS,
) -> Phase7MonitoringHandoff:
    is_feed = method in {AcquisitionMethod.RSS, AcquisitionMethod.ATOM}
    return Phase7MonitoringHandoff(
        phase7_monitoring_handoff_id=f"handoff-{method.value}",
        acquisition_resolution_id=f"resolution-{method.value}",
        candidate_source_id=f"candidate-{method.value}",
        entity_id="entity-example",
        source_url="https://example.com/news",
        acquisition_method=method,
        acquisition_config_ref=f"config-{method.value}",
        supported_information_need_ids=("need-one",),
        source_role=SourceRole.NEWSROOM,
        provenance={
            "final_source_evaluation_id": "evaluation-one",
            "verified_feed_url": (
                "https://example.com/feed.xml" if is_feed else None
            ),
            "selected_feed_verification_result_id": (
                "feed-result-one" if is_feed else None
            ),
            "selected_website_resolution_result_id": (
                "website-result-one" if not is_feed else None
            ),
            "selected_website_acquisition_config_id": (
                "website-config-one" if not is_feed else None
            ),
            "max_discovered_items_per_run": 20,
            "complete_prompt": "must not be persisted",
        },
    )


class TemporaryMonitoringDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "phase9a-test.db"
        initialize_database(self.database_path)
        self.source_items = SourceItemRepository(self.database_path)
        self.executions = SourceExecutionRepository(self.database_path)
        self.filters = FilterDecisionRepository(self.database_path)
        self.runs = PipelineRunRepository(self.database_path)
        self.registrar = MonitoringCandidateRegistrar(
            source_item_repository=self.source_items,
            source_execution_repository=self.executions,
            filter_decision_repository=self.filters,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def start_run_and_execution(
        self,
        run_id: str,
        *,
        source_key: str | None = None,
        handoff: Phase7MonitoringHandoff | None = None,
    ):
        run = self.runs.start_run(
            PipelineRunStart(
                pipeline_version="1.0",
                phase="database_phase9a_test",
                execution_mode="fake",
                run_id=run_id,
            )
        )
        execution_start = (
            build_monitoring_source_execution_start(
                handoff,
                execution_mode="fake",
                requested_result_limit=10,
            )
            if handoff is not None
            else SourceExecutionStart(
                source_type="rss",
                provider="rss",
                source_key=source_key or run_id,
                source_locator="https://example.com/feed.xml",
                execution_mode="fake",
                requested_result_limit=10,
            )
        )
        execution = self.executions.start_source_execution(
            run.run_id,
            execution_start,
        )
        return run, execution

    def persist(self, run_id, execution_id, raw_items):
        return self.registrar.persist_candidates(
            run_id=run_id,
            source_execution_id=execution_id,
            raw_items=raw_items,
        )

    def count_rows(self, table: str) -> int:
        connection = open_database_connection(self.database_path)
        try:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            connection.close()


class MonitoringSourceItemIdentityTests(TemporaryMonitoringDatabaseTestCase):
    def test_canonical_url_is_primary_identity(self):
        first = make_raw_item("a", metadata={"guid": "first-guid"})
        second = make_raw_item("a", metadata={"guid": "second-guid"})
        self.assertEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_normalized_equivalent_urls_share_identity(self):
        first = make_raw_item(url="HTTPS://Example.com/articles/a/?utm_source=rss#top")
        second = make_raw_item(url="https://example.com/articles/a")
        self.assertEqual(canonicalize_url(first.url), canonicalize_url(second.url))
        self.assertEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_rss_and_selected_website_same_url_share_identity(self):
        rss_item = make_raw_item(source_type=SourceType.RSS, provider="rss")
        website_item = make_raw_item(
            source_type=SourceType.SELECTED_WEBSITE,
            provider="selected_website",
        )
        self.assertEqual(fingerprint_raw_item(rss_item), fingerprint_raw_item(website_item))

    def test_cross_method_persistence_reuses_one_source_item(self):
        rss_result = self.source_items.upsert_one_with_outcome(make_raw_item())
        website_result = self.source_items.upsert_one_with_outcome(
            make_raw_item(
                source_type=SourceType.SELECTED_WEBSITE,
                provider="selected_website",
            )
        )
        self.assertEqual(rss_result.source_item_id, website_result.source_item_id)
        self.assertEqual(self.source_items.count(), 1)

    def test_different_canonical_urls_remain_distinct(self):
        first = make_raw_item("a")
        second = make_raw_item("b")
        self.assertNotEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_title_only_similarity_does_not_merge_unrelated_items(self):
        first = make_raw_item(
            url="",
            title="Quarterly update",
            organization="First Publisher",
            published_at="2026-08-01",
            metadata={"guid": ""},
        )
        second = make_raw_item(
            url="",
            title="Quarterly update",
            organization="Second Publisher",
            published_at="2026-08-02",
            metadata={"guid": ""},
        )
        self.assertNotEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_raw_text_does_not_change_url_identity(self):
        first = make_raw_item(raw_text="Short feed summary")
        second = make_raw_item(raw_text="Full selected website content")
        self.assertEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_external_id_is_secondary_when_url_is_missing(self):
        first = make_raw_item(url="", metadata={"guid": "shared-guid"})
        second = make_raw_item(url="", metadata={"guid": "shared-guid"})
        self.assertEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_external_ids_remain_provider_scoped_without_url(self):
        first = make_raw_item(url="", provider="rss", metadata={"guid": "shared"})
        second = make_raw_item(
            url="",
            provider="selected_website",
            metadata={"guid": "shared"},
        )
        self.assertNotEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))


class SourceItemPersistenceOutcomeTests(TemporaryMonitoringDatabaseTestCase):
    def test_first_persistence_reports_new(self):
        result = self.source_items.upsert_one_with_outcome(make_raw_item())
        self.assertIsInstance(result, SourceItemPersistenceResult)
        self.assertTrue(result.created_new)

    def test_second_persistence_reports_existing(self):
        self.source_items.upsert_one_with_outcome(make_raw_item())
        result = self.source_items.upsert_one_with_outcome(make_raw_item())
        self.assertFalse(result.created_new)

    def test_existing_source_item_id_is_reused(self):
        first = self.source_items.upsert_one_with_outcome(make_raw_item())
        second = self.source_items.upsert_one_with_outcome(make_raw_item())
        self.assertEqual(first.source_item_id, second.source_item_id)

    def test_repeated_persistence_creates_no_duplicate_row(self):
        self.source_items.upsert_one_with_outcome(make_raw_item())
        self.source_items.upsert_one_with_outcome(make_raw_item())
        self.assertEqual(self.source_items.count(), 1)

    def test_existing_upsert_api_remains_compatible(self):
        result = self.source_items.upsert_one(make_raw_item())
        self.assertIsInstance(result, SourceItemUpsertSummary)
        self.assertEqual(result.inserted_count, 1)

    def test_search_api_identity_still_reuses_repeated_url(self):
        item = make_raw_item(
            source_type=SourceType.SEARCH_API,
            provider="brave",
        )
        first = self.source_items.upsert_one_with_outcome(item)
        second = self.source_items.upsert_one_with_outcome(item)
        self.assertTrue(first.created_new)
        self.assertFalse(second.created_new)

    def test_legacy_provider_scoped_row_is_reused_by_canonical_url(self):
        first = self.source_items.upsert_one_with_outcome(make_raw_item())
        connection = open_database_connection(self.database_path)
        try:
            connection.execute(
                "UPDATE source_items SET fingerprint = ? WHERE source_item_id = ?",
                ("legacy-provider-scoped-fingerprint", first.source_item_id),
            )
            connection.commit()
        finally:
            connection.close()

        result = self.source_items.upsert_one_with_outcome(
            make_raw_item(
                source_type=SourceType.SELECTED_WEBSITE,
                provider="selected_website",
            )
        )
        self.assertFalse(result.created_new)
        self.assertEqual(result.source_item_id, first.source_item_id)
        self.assertEqual(self.source_items.count(), 1)


class MonitoringDiscoveryAndHistoryTests(TemporaryMonitoringDatabaseTestCase):
    def test_within_execution_duplicates_are_collapsed(self):
        run, execution = self.start_run_and_execution("run-within")
        result = self.persist(
            run.run_id,
            execution.source_execution_id,
            [make_raw_item("a"), make_raw_item("a"), make_raw_item("b")],
        )
        self.assertEqual(len(result.outcomes), 2)
        self.assertEqual(self.count_rows("source_item_discoveries"), 2)

    def test_within_execution_preserves_first_result_positions(self):
        run, execution = self.start_run_and_execution("run-positions")
        result = self.persist(
            run.run_id,
            execution.source_execution_id,
            [make_raw_item("a"), make_raw_item("a"), make_raw_item("b")],
        )
        self.assertEqual([item.result_position for item in result.outcomes], [0, 2])

    def test_same_source_item_can_have_two_execution_discoveries(self):
        run_one, execution_one = self.start_run_and_execution("run-one")
        first = self.persist(run_one.run_id, execution_one.source_execution_id, [make_raw_item()])
        run_two, execution_two = self.start_run_and_execution("run-two")
        second = self.persist(run_two.run_id, execution_two.source_execution_id, [make_raw_item()])
        self.assertEqual(first.outcomes[0].source_item_id, second.outcomes[0].source_item_id)
        self.assertEqual(self.count_rows("source_item_discoveries"), 2)

    def test_each_execution_keeps_its_own_discovery_provenance(self):
        run_one, execution_one = self.start_run_and_execution("run-prov-one")
        self.persist(run_one.run_id, execution_one.source_execution_id, [make_raw_item()])
        run_two, execution_two = self.start_run_and_execution("run-prov-two")
        self.persist(run_two.run_id, execution_two.source_execution_id, [make_raw_item()])
        first = self.executions.list_discoveries(execution_one.source_execution_id)
        second = self.executions.list_discoveries(execution_two.source_execution_id)
        self.assertEqual(first[0].source_execution_id, execution_one.source_execution_id)
        self.assertEqual(second[0].source_execution_id, execution_two.source_execution_id)

    def test_repeating_same_execution_is_idempotent(self):
        run, execution = self.start_run_and_execution("run-idempotent")
        self.persist(run.run_id, execution.source_execution_id, [make_raw_item()])
        self.persist(run.run_id, execution.source_execution_id, [make_raw_item()])
        self.assertEqual(len(self.executions.list_discoveries(execution.source_execution_id)), 1)

    def test_discovery_metadata_records_identity_outcome(self):
        run, execution = self.start_run_and_execution("run-meta")
        self.persist(run.run_id, execution.source_execution_id, [make_raw_item()])
        discovery = self.executions.list_discoveries(execution.source_execution_id)[0]
        self.assertEqual(discovery.metadata["identity_outcome"], "created_new")
        self.assertEqual(len(discovery.metadata["source_item_fingerprint"]), 64)

    def test_new_item_remains_filter_eligible(self):
        run, execution = self.start_run_and_execution("run-new")
        result = self.persist(run.run_id, execution.source_execution_id, [make_raw_item()])
        self.assertTrue(result.outcomes[0].created_new)
        self.assertTrue(result.outcomes[0].filter_eligible)
        self.assertEqual(self.filters.list_run_filter_statuses(run.run_id)[0].status, PENDING)

    def test_historical_rediscovery_is_deferred(self):
        first_run, first_execution = self.start_run_and_execution("run-history-one")
        self.persist(first_run.run_id, first_execution.source_execution_id, [make_raw_item()])
        second_run, second_execution = self.start_run_and_execution("run-history-two")
        result = self.persist(
            second_run.run_id,
            second_execution.source_execution_id,
            [make_raw_item()],
        )
        self.assertFalse(result.outcomes[0].created_new)
        self.assertTrue(result.outcomes[0].historical_duplicate)
        self.assertFalse(result.outcomes[0].filter_eligible)

    def test_historical_rediscovery_uses_exact_deferred_reason(self):
        first_run, first_execution = self.start_run_and_execution("run-reason-one")
        self.persist(first_run.run_id, first_execution.source_execution_id, [make_raw_item()])
        second_run, second_execution = self.start_run_and_execution("run-reason-two")
        self.persist(second_run.run_id, second_execution.source_execution_id, [make_raw_item()])
        status = self.filters.list_run_filter_statuses(second_run.run_id)[0]
        self.assertEqual(status.status, DEFERRED)
        self.assertEqual(status.deferred_reason, HISTORICAL_DUPLICATE_REASON)

    def test_historical_rediscovery_creates_no_filter_execution(self):
        first_run, first_execution = self.start_run_and_execution("run-no-filter-one")
        self.persist(first_run.run_id, first_execution.source_execution_id, [make_raw_item()])
        second_run, second_execution = self.start_run_and_execution("run-no-filter-two")
        self.persist(second_run.run_id, second_execution.source_execution_id, [make_raw_item()])
        self.assertEqual(self.filters.list_filter_executions(second_run.run_id), [])

    def test_historical_rediscovery_creates_no_filter_decision(self):
        first_run, first_execution = self.start_run_and_execution("run-no-decision-one")
        self.persist(first_run.run_id, first_execution.source_execution_id, [make_raw_item()])
        second_run, second_execution = self.start_run_and_execution("run-no-decision-two")
        self.persist(second_run.run_id, second_execution.source_execution_id, [make_raw_item()])
        self.assertEqual(self.filters.list_filter_decisions_for_run(second_run.run_id), [])

    def test_historical_rediscovery_is_visible_and_not_rejected(self):
        first_run, first_execution = self.start_run_and_execution("run-visible-one")
        self.persist(first_run.run_id, first_execution.source_execution_id, [make_raw_item()])
        second_run, second_execution = self.start_run_and_execution("run-visible-two")
        self.persist(second_run.run_id, second_execution.source_execution_id, [make_raw_item()])
        self.assertEqual(
            len(
                self.executions.list_discoveries(
                    second_execution.source_execution_id
                )
            ),
            1,
        )
        status = self.filters.list_run_filter_statuses(second_run.run_id)[0]
        self.assertNotEqual(status.status, "rejected")

    def test_execution_must_belong_to_requested_run(self):
        run, execution = self.start_run_and_execution("run-owner")
        with self.assertRaises(MonitoringRuntimeCompatibilityError):
            self.persist("another-run", execution.source_execution_id, [make_raw_item()])
        self.assertEqual(self.source_items.count(), 0)


class MonitoringHandoffCompatibilityTests(TemporaryMonitoringDatabaseTestCase):
    def test_rss_handoff_dispatches_to_feed_adapter(self):
        feed = Mock(return_value="feed-result")
        selected = Mock()
        result = MonitoringAcquisitionDispatcher(
            feed_adapter=feed,
            selected_website_adapter=selected,
        ).dispatch(make_handoff(AcquisitionMethod.RSS))
        self.assertEqual(result, "feed-result")
        feed.assert_called_once()
        selected.assert_not_called()

    def test_atom_handoff_dispatches_to_feed_adapter(self):
        feed = Mock(return_value="atom-result")
        selected = Mock()
        result = MonitoringAcquisitionDispatcher(
            feed_adapter=feed,
            selected_website_adapter=selected,
        ).dispatch(make_handoff(AcquisitionMethod.ATOM))
        self.assertEqual(result, "atom-result")
        selected.assert_not_called()

    def test_selected_website_handoff_dispatches_to_website_adapter(self):
        feed = Mock()
        selected = Mock(return_value="website-result")
        result = MonitoringAcquisitionDispatcher(
            feed_adapter=feed,
            selected_website_adapter=selected,
        ).dispatch(make_handoff(AcquisitionMethod.SELECTED_WEBSITE))
        self.assertEqual(result, "website-result")
        selected.assert_called_once()
        feed.assert_not_called()

    def test_dispatcher_passes_the_existing_resolved_handoff(self):
        handoff = make_handoff(AcquisitionMethod.RSS)
        feed = Mock()
        MonitoringAcquisitionDispatcher(
            feed_adapter=feed,
            selected_website_adapter=Mock(),
        ).dispatch(handoff)
        self.assertIs(feed.call_args.args[0], handoff)

    def test_rss_handoff_maps_to_config_driven_source_execution(self):
        handoff = make_handoff(AcquisitionMethod.RSS)
        run, execution = self.start_run_and_execution("run-feed-handoff", handoff=handoff)
        self.assertEqual(execution.run_id, run.run_id)
        self.assertIsNone(execution.planning_search_plan_id)
        self.assertEqual(execution.source_type, "rss")
        self.assertEqual(execution.source_locator, "https://example.com/feed.xml")

    def test_selected_handoff_maps_to_config_driven_source_execution(self):
        handoff = make_handoff(AcquisitionMethod.SELECTED_WEBSITE)
        _, execution = self.start_run_and_execution("run-site-handoff", handoff=handoff)
        self.assertIsNone(execution.planning_search_plan_id)
        self.assertEqual(execution.source_type, "selected_website")
        self.assertEqual(execution.source_locator, handoff.source_url)

    def test_source_execution_metadata_preserves_stable_handoff_references(self):
        handoff = make_handoff(AcquisitionMethod.RSS)
        start = build_monitoring_source_execution_start(handoff, execution_mode="fake")
        self.assertEqual(
            start.metadata["source_monitoring_handoff_id"],
            handoff.phase7_monitoring_handoff_id,
        )
        self.assertEqual(start.metadata["candidate_source_id"], handoff.candidate_source_id)
        self.assertEqual(start.metadata["entity_id"], handoff.entity_id)
        self.assertEqual(
            start.metadata["acquisition_resolution_id"],
            handoff.acquisition_resolution_id,
        )
        self.assertEqual(start.metadata["acquisition_method"], "rss")
        self.assertEqual(start.metadata["final_source_evaluation_id"], "evaluation-one")
        self.assertEqual(start.metadata["selected_feed_verification_result_id"], "feed-result-one")

    def test_source_execution_metadata_excludes_complete_planning_payloads(self):
        start = build_monitoring_source_execution_start(make_handoff())
        self.assertNotIn("complete_prompt", start.metadata)
        self.assertNotIn("provenance", start.metadata)

    def test_dispatcher_performs_no_network_or_llm_work(self):
        feed = Mock(return_value=[])
        with patch("requests.get", side_effect=AssertionError("network called")):
            result = MonitoringAcquisitionDispatcher(
                feed_adapter=feed,
                selected_website_adapter=Mock(),
            ).dispatch(make_handoff())
        self.assertEqual(result, [])

    def test_selected_website_exposes_item_url_after_one_surface_fetch(self):
        response = Mock(
            text=(
                "<html><head><title>News</title></head><body>"
                "<a href='/articles/a'>Article A</a>"
                "<a href='/articles/b'>Article B</a>"
                "</body></html>"
            )
        )
        response.raise_for_status.return_value = None
        with patch("src.selected_website_client.requests.get", return_value=response) as get:
            items = SelectedWebsiteClient(dry_run=False).fetch_website(
                SelectedWebsite(name="Example", url="https://example.com/news"),
                [],
            )
        self.assertEqual(get.call_count, 1)
        self.assertEqual(
            [item.url for item in items],
            [
                "https://example.com/articles/a",
                "https://example.com/articles/b",
            ],
        )

    def test_rss_entry_materializes_without_article_detail_fetch(self):
        response = Mock(content=b"fake-feed")
        response.raise_for_status.return_value = None
        parsed_feed = Mock(
            entries=[
                {
                    "id": "entry-a",
                    "title": "Article A",
                    "link": "https://example.com/articles/a",
                    "summary": "Feed-provided content",
                    "published": "2026-08-10T00:00:00+00:00",
                }
            ]
        )
        with patch("src.rss_client.requests.get", return_value=response) as get, patch(
            "src.rss_client.feedparser.parse",
            return_value=parsed_feed,
        ):
            items = RSSClient(dry_run=False).fetch_feed(
                RSSFeed(name="Example Feed", url="https://example.com/feed.xml"),
                [],
            )
        self.assertEqual(get.call_count, 1)
        self.assertEqual(items[0].raw_text, "Feed-provided content")
        self.assertEqual(items[0].metadata["raw_entry"]["id"], "entry-a")


class MonitoringCompatibilityContractTests(TemporaryMonitoringDatabaseTestCase):
    def test_source_item_discovery_primary_key_is_execution_and_item(self):
        connection = open_database_connection(self.database_path)
        try:
            indexes = connection.execute("PRAGMA index_list(source_item_discoveries)").fetchall()
            primary_index = next(row for row in indexes if row["origin"] == "pk")
            columns = connection.execute(f"PRAGMA index_info({primary_index['name']})").fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [row["name"] for row in columns],
            ["source_execution_id", "source_item_id"],
        )

    def test_source_item_fingerprint_has_unique_sql_constraint(self):
        connection = open_database_connection(self.database_path)
        try:
            indexes = connection.execute("PRAGMA index_list(source_items)").fetchall()
            unique_columns = []
            for index in indexes:
                if index["unique"]:
                    columns = connection.execute(f"PRAGMA index_info({index['name']})").fetchall()
                    unique_columns.append([row["name"] for row in columns])
        finally:
            connection.close()
        self.assertIn(["fingerprint"], unique_columns)

    def test_migrations_end_at_007(self):
        connection = open_database_connection(self.database_path)
        try:
            versions = [
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
        finally:
            connection.close()
        self.assertEqual(versions[-1], "007")
        self.assertNotIn("008", versions)

    def test_monitoring_registration_does_not_create_career_signals(self):
        run, execution = self.start_run_and_execution("run-career-signal")
        self.persist(run.run_id, execution.source_execution_id, [make_raw_item()])
        self.assertEqual(CareerSignalRepository(self.database_path).count(), 0)

    def test_pipeline_run_output_contract_is_unchanged(self):
        output = PipelineRunOutput(
            pipeline_version="1.0",
            phase="test",
            generated_at="2026-08-10T00:00:00+00:00",
            summary=PipelineSummary(total_raw_items=0),
        )
        expected_fields = {
            "pipeline_version", "phase", "generated_at", "summary",
            "user_profile", "search_scope", "target_career_paths",
            "search_queries", "search_plans", "search_api_plan_statuses",
            "search_api_result_diagnostics", "raw_items",
            "raw_item_filter_statuses", "ai_filter_results",
            "filtered_raw_items", "career_signals",
            "scored_career_signals", "priority_assessment_diagnostics",
            "career_signal_routing",
            "career_intelligence_interpretation",
            "career_intelligence_brief",
        }
        self.assertEqual({field.name for field in fields(PipelineRunOutput)}, expected_fields)
        self.assertEqual(set(output.to_dict()), expected_fields)

    def test_configured_development_database_is_not_used(self):
        configured_path = Path(DEFAULT_DATABASE_FILE)
        before = (
            (
                configured_path.exists(),
                configured_path.stat().st_size,
                configured_path.stat().st_mtime_ns,
            )
            if configured_path.exists()
            else (False, None, None)
        )
        run, execution = self.start_run_and_execution("run-temp-only")
        self.persist(run.run_id, execution.source_execution_id, [make_raw_item()])
        after = (
            (
                configured_path.exists(),
                configured_path.stat().st_size,
                configured_path.stat().st_mtime_ns,
            )
            if configured_path.exists()
            else (False, None, None)
        )
        self.assertEqual(after, before)


class ControlledTwoExecutionDemonstrationTests(TemporaryMonitoringDatabaseTestCase):
    def test_two_runs_classify_historical_items_and_keep_new_item_eligible(self):
        run_one, execution_one = self.start_run_and_execution("demo-run-one")
        first = self.persist(
            run_one.run_id,
            execution_one.source_execution_id,
            [make_raw_item("a"), make_raw_item("b")],
        )
        self.executions.complete_execution(
            execution_one.source_execution_id,
            SourceExecutionCompletion(returned_item_count=2, discovered_item_count=2),
        )
        self.runs.complete_run(run_one.run_id, require_planning_bundle=False)

        run_two, execution_two = self.start_run_and_execution("demo-run-two")
        second = self.persist(
            run_two.run_id,
            execution_two.source_execution_id,
            [make_raw_item("a"), make_raw_item("b"), make_raw_item("c")],
        )

        self.assertEqual([item.created_new for item in first.outcomes], [True, True])
        self.assertEqual([item.created_new for item in second.outcomes], [False, False, True])
        self.assertEqual(self.source_items.count(), 3)
        self.assertEqual(self.count_rows("source_item_discoveries"), 5)
        statuses = self.filters.list_run_filter_statuses(run_two.run_id)
        self.assertEqual([item.status for item in statuses], [DEFERRED, DEFERRED, PENDING])
        self.assertEqual(
            [item.deferred_reason for item in statuses],
            [HISTORICAL_DUPLICATE_REASON, HISTORICAL_DUPLICATE_REASON, None],
        )
        self.assertEqual(self.filters.list_filter_executions(run_two.run_id), [])
        self.assertEqual(self.filters.list_filter_decisions_for_run(run_two.run_id), [])


if __name__ == "__main__":
    unittest.main()
