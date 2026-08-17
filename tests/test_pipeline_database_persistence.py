from contextlib import redirect_stdout
from dataclasses import dataclass
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.config import DEFAULT_DATABASE_FILE
from src.database.migrations import initialize_database
from src.database.repositories.source_item_repository import SourceItemRepository
from src.database.source_identity import fingerprint_raw_item
from src.models import (
    AIFilterExecutionReport,
    CareerPathCategory,
    RawItem,
    RawItemFilterStatus,
    SearchAPIExecutionReport,
    SearchPlan,
    SearchQueryType,
    SearchScope,
    SourceType,
    TargetCareerPath,
    UserProfile,
)
from src.pipeline import MockPipeline
from src.storage import convert_to_json_ready, save_json


@dataclass
class FakeSourceItemUpsertSummary:
    received_count: int
    unique_count: int
    inserted_count: int
    updated_count: int


class FakeSourceItemRepository:
    def __init__(self, summary: FakeSourceItemUpsertSummary | None = None):
        self.upsert_calls = []
        self.summary = summary or FakeSourceItemUpsertSummary(
            received_count=0,
            unique_count=0,
            inserted_count=0,
            updated_count=0,
        )

    def upsert_many(self, raw_items):
        batch = list(raw_items)
        self.upsert_calls.append(batch)
        return FakeSourceItemUpsertSummary(
            received_count=len(batch),
            unique_count=self.summary.unique_count or len(batch),
            inserted_count=self.summary.inserted_count,
            updated_count=self.summary.updated_count,
        )


class FailingSourceItemRepository:
    def __init__(self):
        self.error = RuntimeError("repository unavailable")

    def upsert_many(self, raw_items):
        raise self.error


def build_raw_item(
    *,
    source_type: SourceType = SourceType.SEARCH_API,
    title: str = "External item",
    url: str = "https://example.com/external",
    provider: str = "brave",
    mode: str | None = None,
) -> RawItem:
    metadata = {
        "provider": provider,
    }

    if mode is not None:
        metadata["mode"] = mode

    return RawItem(
        source_type=source_type,
        title=title,
        organization="Example Co",
        url=url,
        published_at=None,
        raw_text=title,
        metadata=metadata,
    )


def build_user_profile() -> UserProfile:
    return UserProfile(
        profile_id="profile_1",
        name="Test User",
        background_summary="Strategy analyst interested in AI.",
    )


def build_career_path() -> TargetCareerPath:
    return TargetCareerPath(
        path_id="path_1",
        title="AI Strategy",
        category=CareerPathCategory.AI_STRATEGY,
        description="AI strategy roles.",
        fit_score=90,
    )


def build_search_plan() -> SearchPlan:
    return SearchPlan(
        plan_id="plan_1",
        query_id="query_1",
        query_text="strategy analyst",
        query_type=SearchQueryType.JOB_SEARCH,
        career_path_id="path_1",
        career_path_title="AI Strategy",
        scope_id="scope_1",
        source_types=[
            SourceType.SEARCH_API,
            SourceType.RSS,
            SourceType.SELECTED_WEBSITE,
        ],
    )


def build_ai_filter_report(raw_items: list[RawItem]) -> AIFilterExecutionReport:
    statuses = [
        RawItemFilterStatus(
            raw_item_fingerprint=f"raw_{index}",
            raw_item_index=index,
            source_type=raw_item.source_type,
            title=raw_item.title,
            url=raw_item.url,
            status=(
                "processed_rejected"
                if raw_item.source_type == SourceType.SEARCH_API
                else "processed_accepted"
            ),
            reason="test",
            is_relevant=raw_item.source_type != SourceType.SEARCH_API,
        )
        for index, raw_item in enumerate(raw_items)
    ]

    return AIFilterExecutionReport(
        filtered_raw_items=[
            raw_item
            for raw_item in raw_items
            if raw_item.source_type != SourceType.SEARCH_API
        ],
        ai_filter_results=[],
        raw_item_statuses=statuses,
        executed_count=len(raw_items),
    )


def build_pipeline(
    *,
    repository=None,
    mock_raw_items=None,
    search_api_raw_items=None,
    rss_raw_items=None,
    selected_website_raw_items=None,
    ai_filter_events=None,
) -> MockPipeline:
    mock_raw_items = [] if mock_raw_items is None else mock_raw_items
    search_api_raw_items = (
        [] if search_api_raw_items is None else search_api_raw_items
    )
    rss_raw_items = [] if rss_raw_items is None else rss_raw_items
    selected_website_raw_items = (
        [] if selected_website_raw_items is None else selected_website_raw_items
    )
    ai_filter_events = [] if ai_filter_events is None else ai_filter_events

    def ai_filter_executor(raw_items, user_profile, career_paths):
        ai_filter_events.append("ai_filter")
        return build_ai_filter_report(raw_items)

    return MockPipeline(
        raw_item_loader=lambda: mock_raw_items,
        user_profile_loader=build_user_profile,
        search_scope_loader=lambda: SearchScope(
            scope_id="scope_1",
            name="Test scope",
            source_types=[
                SourceType.SEARCH_API,
                SourceType.RSS,
                SourceType.SELECTED_WEBSITE,
            ],
        ),
        career_path_generator=lambda user_profile: [build_career_path()],
        search_query_generator=lambda career_paths: [],
        search_plan_builder=lambda search_queries, search_scope: [build_search_plan()],
        search_api_executor=lambda search_plans: SearchAPIExecutionReport(
            raw_items=search_api_raw_items,
            executed_plan_count=1 if search_api_raw_items else 0,
        ),
        rss_executor=lambda search_scope, search_plans: (
            rss_raw_items,
            1 if rss_raw_items else 0,
        ),
        selected_website_executor=lambda search_scope, search_plans: (
            selected_website_raw_items,
            1 if selected_website_raw_items else 0,
        ),
        ai_filter_executor=ai_filter_executor,
        normalizer=lambda raw_items, ai_results: [],
        source_item_repository=repository,
    )


class PipelineRepositoryInjectionTests(unittest.TestCase):
    def test_mock_pipeline_accepts_optional_source_item_repository(self):
        repository = FakeSourceItemRepository()

        pipeline = build_pipeline(repository=repository)

        self.assertIs(pipeline.source_item_repository, repository)

    def test_existing_construction_without_repository_still_works(self):
        pipeline = build_pipeline()

        output = pipeline.run()

        self.assertEqual(output.summary.total_raw_items, 0)

    def test_search_api_raw_items_are_passed_to_persistence(self):
        repository = FakeSourceItemRepository()
        search_item = build_raw_item(source_type=SourceType.SEARCH_API)

        build_pipeline(
            repository=repository,
            search_api_raw_items=[search_item],
        ).run()

        self.assertEqual(repository.upsert_calls[0], [search_item])

    def test_rss_raw_items_are_passed_to_persistence(self):
        repository = FakeSourceItemRepository()
        rss_item = build_raw_item(source_type=SourceType.RSS, provider="rss")

        build_pipeline(repository=repository, rss_raw_items=[rss_item]).run()

        self.assertEqual(repository.upsert_calls[0], [rss_item])

    def test_selected_website_raw_items_are_passed_to_persistence(self):
        repository = FakeSourceItemRepository()
        website_item = build_raw_item(
            source_type=SourceType.SELECTED_WEBSITE,
            provider="selected_website",
        )

        build_pipeline(
            repository=repository,
            selected_website_raw_items=[website_item],
        ).run()

        self.assertEqual(repository.upsert_calls[0], [website_item])

    def test_all_external_source_raw_items_are_passed_in_one_batch(self):
        repository = FakeSourceItemRepository()
        search_item = build_raw_item(
            source_type=SourceType.SEARCH_API,
            url="https://example.com/search",
        )
        rss_item = build_raw_item(
            source_type=SourceType.RSS,
            provider="rss",
            url="https://example.com/rss",
        )
        website_item = build_raw_item(
            source_type=SourceType.SELECTED_WEBSITE,
            provider="selected_website",
            url="https://example.com/site",
        )

        build_pipeline(
            repository=repository,
            search_api_raw_items=[search_item],
            rss_raw_items=[rss_item],
            selected_website_raw_items=[website_item],
        ).run()

        self.assertEqual(repository.upsert_calls, [[search_item, rss_item, website_item]])

    def test_mock_fixture_raw_items_are_excluded(self):
        repository = FakeSourceItemRepository()
        mock_item = build_raw_item(
            source_type=SourceType.MOCK_JOB,
            provider="mock",
            url="https://example.com/mock",
        )
        external_item = build_raw_item(url="https://example.com/external")

        build_pipeline(
            repository=repository,
            mock_raw_items=[mock_item],
            search_api_raw_items=[external_item],
        ).run()

        self.assertEqual(repository.upsert_calls[0], [external_item])

    def test_repository_is_called_once_when_external_items_exist(self):
        repository = FakeSourceItemRepository()

        build_pipeline(
            repository=repository,
            search_api_raw_items=[build_raw_item()],
            rss_raw_items=[
                build_raw_item(
                    source_type=SourceType.RSS,
                    provider="rss",
                    url="https://example.com/rss",
                )
            ],
        ).run()

        self.assertEqual(len(repository.upsert_calls), 1)

    def test_repository_is_not_called_when_external_batch_is_empty(self):
        repository = FakeSourceItemRepository()

        build_pipeline(repository=repository).run()

        self.assertEqual(repository.upsert_calls, [])

    def test_persistence_occurs_before_ai_filtering(self):
        events = []

        class OrderedRepository(FakeSourceItemRepository):
            def upsert_many(self, raw_items):
                events.append("persist")
                return super().upsert_many(raw_items)

        build_pipeline(
            repository=OrderedRepository(),
            search_api_raw_items=[build_raw_item()],
            ai_filter_events=events,
        ).run()

        self.assertEqual(events, ["persist", "ai_filter"])

    def test_ai_rejected_external_raw_item_is_still_persisted(self):
        repository = FakeSourceItemRepository()
        rejected_item = build_raw_item(source_type=SourceType.SEARCH_API)

        output = build_pipeline(
            repository=repository,
            search_api_raw_items=[rejected_item],
        ).run()

        self.assertEqual(repository.upsert_calls[0], [rejected_item])
        self.assertEqual(
            output.raw_item_filter_statuses[0].status,
            "processed_rejected",
        )

    def test_repository_summary_is_reported_to_console(self):
        repository = FakeSourceItemRepository(
            summary=FakeSourceItemUpsertSummary(
                received_count=1,
                unique_count=1,
                inserted_count=1,
                updated_count=0,
            )
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            build_pipeline(
                repository=repository,
                search_api_raw_items=[build_raw_item()],
            ).run()

        self.assertIn(
            "SourceItem persistence: received=1, unique=1, inserted=1, updated=0",
            stdout.getvalue(),
        )

    def test_persistence_does_not_change_pipeline_run_output(self):
        external_item = build_raw_item()

        with patch("src.pipeline.utc_now_iso", return_value="2026-07-22T00:00:00+00:00"):
            without_repository = build_pipeline(
                search_api_raw_items=[external_item]
            ).run()
            with_repository = build_pipeline(
                repository=FakeSourceItemRepository(),
                search_api_raw_items=[external_item],
            ).run()

        self.assertEqual(
            convert_to_json_ready(with_repository),
            convert_to_json_ready(without_repository),
        )

    def test_persistence_does_not_change_saved_json_structure(self):
        external_item = build_raw_item()

        with tempfile.TemporaryDirectory() as temp_dir:
            without_path = Path(temp_dir) / "without.json"
            with_path = Path(temp_dir) / "with.json"

            with patch(
                "src.pipeline.utc_now_iso",
                return_value="2026-07-22T00:00:00+00:00",
            ):
                save_json(
                    build_pipeline(search_api_raw_items=[external_item]).run(),
                    without_path,
                )
                save_json(
                    build_pipeline(
                        repository=FakeSourceItemRepository(),
                        search_api_raw_items=[external_item],
                    ).run(),
                    with_path,
                )

            without_payload = json.loads(without_path.read_text(encoding="utf-8"))
            with_payload = json.loads(with_path.read_text(encoding="utf-8"))

        self.assertEqual(with_payload.keys(), without_payload.keys())
        self.assertEqual(with_payload["summary"].keys(), without_payload["summary"].keys())

    def test_no_repository_means_no_database_side_effect(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "should-not-exist.db"

            build_pipeline(search_api_raw_items=[build_raw_item()]).run()

            self.assertFalse(database_path.exists())

    def test_dry_run_external_raw_items_do_not_persist(self):
        repository = FakeSourceItemRepository()
        dry_run_item = build_raw_item(mode="dry_run")

        build_pipeline(
            repository=repository,
            search_api_raw_items=[dry_run_item],
        ).run()

        self.assertEqual(repository.upsert_calls, [])

    def test_repository_failure_is_surfaced_with_clear_context(self):
        repository = FailingSourceItemRepository()

        with self.assertRaisesRegex(RuntimeError, "RawItem persistence failed"):
            build_pipeline(
                repository=repository,
                search_api_raw_items=[build_raw_item()],
            ).run()

    def test_repository_failure_prevents_ai_filter_from_continuing(self):
        ai_filter_events = []

        with self.assertRaises(RuntimeError):
            build_pipeline(
                repository=FailingSourceItemRepository(),
                search_api_raw_items=[build_raw_item()],
                ai_filter_events=ai_filter_events,
            ).run()

        self.assertEqual(ai_filter_events, [])

    def test_original_repository_exception_is_available_as_cause(self):
        repository = FailingSourceItemRepository()

        with self.assertRaises(RuntimeError) as context:
            build_pipeline(
                repository=repository,
                search_api_raw_items=[build_raw_item()],
            ).run()

        self.assertIs(context.exception.__cause__, repository.error)

    def test_duplicate_external_raw_items_are_delegated_together(self):
        repository = FakeSourceItemRepository()
        duplicate = build_raw_item(url="https://example.com/duplicate")

        build_pipeline(
            repository=repository,
            search_api_raw_items=[duplicate, duplicate],
        ).run()

        self.assertEqual(repository.upsert_calls, [[duplicate, duplicate]])

    def test_tests_do_not_touch_configured_production_database(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = (
            DEFAULT_DATABASE_FILE.stat().st_mtime
            if before_exists
            else None
        )

        build_pipeline(
            repository=FakeSourceItemRepository(),
            search_api_raw_items=[build_raw_item()],
        ).run()

        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)

        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)

    def test_mock_only_raw_items_are_never_inserted_into_source_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "mock-only.db"
            initialize_database(database_path=database_path)
            repository = SourceItemRepository(database_path=database_path)
            mock_item = build_raw_item(
                source_type=SourceType.MOCK_JOB,
                provider="mock",
                url="https://example.com/mock-only",
            )

            build_pipeline(
                repository=repository,
                mock_raw_items=[mock_item],
            ).run()

            self.assertEqual(repository.count(), 0)


class MainDatabaseIntegrationTests(unittest.TestCase):
    def test_importing_src_main_does_not_initialize_or_modify_database(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = (
            DEFAULT_DATABASE_FILE.stat().st_mtime
            if before_exists
            else None
        )

        sys.modules.pop("src.main", None)
        importlib.import_module("src.main")

        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)

        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)

    def test_normal_main_initializes_migrations_and_injects_repository(self):
        main_module = importlib.import_module("src.main")
        captured = {}

        class FakeRepository:
            def __init__(self, database_path):
                self.database_path = database_path

        class FakePipeline:
            def __init__(self, **kwargs):
                captured["pipeline_kwargs"] = kwargs

            def run(self):
                summary = MagicMock()
                summary.total_target_career_paths = 0
                summary.total_search_queries = 0
                summary.total_search_plans = 0
                summary.total_search_api_plans_executed = 0
                summary.total_search_api_plans_deferred = 0
                summary.total_search_api_result_failures = 0
                summary.total_rss_feeds_executed = 0
                summary.total_selected_website_raw_items = 0
                summary.total_selected_websites_executed = 0
                summary.total_raw_items = 0
                summary.total_raw_items_sent_to_ai_filter = 0
                summary.total_ai_filter_results = 0
                summary.total_filtered_raw_items = 0
                summary.total_rejected_raw_items = 0
                summary.total_career_signals = 0
                return MagicMock(summary=summary)

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "main.db"

            with patch.object(main_module, "get_database_path", return_value=database_path), \
                patch.object(main_module, "initialize_database") as initialize_mock, \
                patch.object(main_module, "SourceItemRepository", FakeRepository), \
                patch.object(main_module, "MockPipeline", FakePipeline), \
                patch.object(main_module, "ensure_project_directories"), \
                patch.object(main_module, "validate_required_planning_inputs"), \
                patch.object(main_module, "load_user_preferences_from_json", return_value={}), \
                patch.object(main_module, "save_json", return_value=Path(temp_dir) / "out.json"), \
                redirect_stdout(io.StringIO()):
                main_module.main()

        initialize_mock.assert_called_once_with(database_path=database_path)
        repository = captured["pipeline_kwargs"]["source_item_repository"]
        self.assertIsInstance(repository, FakeRepository)
        self.assertEqual(repository.database_path, database_path)


if __name__ == "__main__":
    unittest.main()
