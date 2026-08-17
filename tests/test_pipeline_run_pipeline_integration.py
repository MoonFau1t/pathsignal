from contextlib import redirect_stdout
import importlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.config import DEFAULT_DATABASE_FILE
from src.database.connection import open_database_connection
from src.database.migrations import initialize_database
from src.database.repositories.pipeline_run_repository import (
    COMPLETED,
    FAILED,
    RUNNING,
    PipelineRunRepository,
    PipelineRunRepositoryError,
)
from src.database.repositories.planning_bundle_repository import (
    PlanningBundleRepository,
)
from src.database.source_identity import fingerprint_raw_item
from src.models import (
    AIFilterExecutionReport,
    AIFilterResult,
    CareerPathCategory,
    CareerSignal,
    RawItem,
    RawItemFilterStatus,
    SearchAPIExecutionReport,
    SearchPlan,
    SearchQuery,
    SearchQueryType,
    SearchScope,
    SignalCategory,
    SourceType,
    TargetCareerPath,
    UserProfile,
)
from src.pipeline import (
    PIPELINE_PHASE,
    PIPELINE_VERSION,
    MockPipeline,
    execute_pipeline_runtime,
)
from src.signal_identity import build_signal_id
from src.storage import convert_to_json_ready


PLANNING_CONFIG = {
    "target_career_path_schema_version": "target_career_path_generation_v1",
    "search_query_max_queries_per_path": 8,
    "search_plan_builder": "rule_based_phase_6",
}


def make_profile(**overrides):
    values = {
        "profile_id": "profile_1",
        "name": "Test User",
        "background_summary": "Strategy analyst interested in AI.",
        "skills": ["strategy", "python"],
        "interests": ["AI"],
    }
    values.update(overrides)
    return UserProfile(**values)


def make_scope(**overrides):
    values = {
        "scope_id": "scope_1",
        "name": "Test scope",
        "locations": ["New York"],
        "languages": ["en"],
        "source_types": [SourceType.SEARCH_API],
        "freshness_days": 30,
        "max_results_per_query": 10,
    }
    values.update(overrides)
    return SearchScope(**values)


def make_path(**overrides):
    values = {
        "path_id": "path_ai",
        "title": "AI Strategy",
        "category": CareerPathCategory.AI_STRATEGY,
        "description": "AI strategy roles.",
        "fit_score": 91.0,
        "rationale": ["Matched AI strategy interest."],
        "keywords": ["AI", "strategy"],
        "suggested_roles": ["AI strategy analyst"],
        "search_seed_terms": ["AI strategy analyst"],
        "metadata": {"path_type": "core_match"},
    }
    values.update(overrides)
    return TargetCareerPath(**values)


def make_query(path=None):
    path = path or make_path()
    return SearchQuery(
        query_id=f"q_{path.path_id}",
        career_path_id=path.path_id,
        career_path_title=path.title,
        query_text=f"{path.title} open role",
        query_type=SearchQueryType.JOB_SEARCH,
        priority=0.95,
        target_roles=path.suggested_roles,
        keywords=path.keywords,
        rationale="Find open roles.",
    )


def make_plan(scope=None, query=None):
    scope = scope or make_scope()
    query = query or make_query()
    return SearchPlan(
        plan_id=f"plan_{scope.scope_id}_{query.query_id}",
        query_id=query.query_id,
        query_text=query.query_text,
        query_type=query.query_type,
        career_path_id=query.career_path_id,
        career_path_title=query.career_path_title,
        scope_id=scope.scope_id,
        source_types=[SourceType.SEARCH_API],
        locations=scope.locations,
        languages=scope.languages,
        freshness_days=scope.freshness_days,
        max_results=scope.max_results_per_query,
        priority=query.priority,
    )


def make_raw_item(index=1, *, source_type=SourceType.MOCK_JOB, mode=None):
    metadata = {}
    if mode is not None:
        metadata["mode"] = mode
    return RawItem(
        source_type=source_type,
        title=f"AI strategy role {index}",
        organization="Example Co",
        url=f"https://example.com/role-{index}",
        published_at=None,
        raw_text=f"AI strategy role {index}",
        metadata=metadata,
    )


def make_filter_report(raw_items, *, accepted_indexes=()):
    accepted_indexes = set(accepted_indexes)
    results = []
    statuses = []
    filtered = []

    for index, raw_item in enumerate(raw_items):
        accepted = index in accepted_indexes
        results.append(
            AIFilterResult(
                raw_item_fingerprint=fingerprint_raw_item(raw_item),
                title=raw_item.title,
                url=raw_item.url,
                is_relevant=accepted,
                confidence=0.9 if accepted else 0.2,
                reason="accepted" if accepted else "rejected",
                suggested_category=SignalCategory.JOB,
            )
        )
        statuses.append(
            RawItemFilterStatus(
                raw_item_fingerprint=fingerprint_raw_item(raw_item),
                raw_item_index=index,
                source_type=raw_item.source_type,
                title=raw_item.title,
                url=raw_item.url,
                status=(
                    "processed_accepted" if accepted else "processed_rejected"
                ),
                reason="test decision",
                is_relevant=accepted,
            )
        )
        if accepted:
            filtered.append(raw_item)

    return AIFilterExecutionReport(
        filtered_raw_items=filtered,
        ai_filter_results=results,
        raw_item_statuses=statuses,
        executed_count=len(raw_items),
    )


def make_signal(raw_item):
    return CareerSignal(
        signal_id=build_signal_id(raw_item),
        category=SignalCategory.JOB,
        title=raw_item.title,
        organization=raw_item.organization,
        url=raw_item.url,
        published_at=raw_item.published_at,
        summary="Relevant role.",
        source_type=raw_item.source_type,
    )


def count_rows(database_path, table_name):
    connection = open_database_connection(database_path)
    try:
        return int(
            connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        )
    finally:
        connection.close()


class EventingPipelineRunRepository(PipelineRunRepository):
    def __init__(self, database_path, events=None):
        super().__init__(database_path=database_path)
        self.events = events if events is not None else []
        self.started_records = []

    def start_run(self, run, **kwargs):
        record = super().start_run(run, **kwargs)
        self.events.append("run_started")
        self.started_records.append(record)
        return record

    def attach_planning_bundle(self, run_id, planning_bundle_id, **kwargs):
        self.events.append(f"bundle_attached:{planning_bundle_id}")
        return super().attach_planning_bundle(
            run_id,
            planning_bundle_id,
            **kwargs,
        )

    def complete_run(self, run_id, completion=None, **kwargs):
        self.events.append("run_completed")
        return super().complete_run(run_id, completion, **kwargs)

    def fail_run(self, run_id, failure, **kwargs):
        self.events.append(f"run_failed:{failure.failure_stage}")
        return super().fail_run(run_id, failure, **kwargs)


class FailingStartRepository(EventingPipelineRunRepository):
    def start_run(self, run, **kwargs):
        raise PipelineRunRepositoryError("start unavailable")


class FailingAttachRepository(EventingPipelineRunRepository):
    def attach_planning_bundle(self, run_id, planning_bundle_id, **kwargs):
        self.events.append("attachment_failed")
        raise PipelineRunRepositoryError("attachment unavailable")


class FailingCompleteRepository(EventingPipelineRunRepository):
    def complete_run(self, run_id, completion=None, **kwargs):
        self.events.append("completion_failed")
        raise PipelineRunRepositoryError("completion unavailable")


class FailingFailRepository(EventingPipelineRunRepository):
    def fail_run(self, run_id, failure, **kwargs):
        self.events.append("failure_write_failed")
        raise PipelineRunRepositoryError("failure update unavailable")


class FailingHydrationPlanningRepository(PlanningBundleRepository):
    def hydrate_planning_bundle(self, planning_bundle_id):
        raise ValueError("corrupt planning child row")


class FailingPlanningPersistRepository:
    def find_reusable_bundle(self, input_fingerprint):
        return None

    def persist_planning_bundle(self, bundle):
        raise sqlite3.DatabaseError("planning write unavailable")


class RecordingSourceRepository:
    def __init__(self, *, fail=False, events=None):
        self.fail = fail
        self.events = events if events is not None else []
        self.rows = {}
        self.upsert_calls = []

    def upsert_many(self, raw_items):
        batch = list(raw_items)
        self.events.append("source_item_persistence")
        if self.fail:
            raise sqlite3.DatabaseError("source write unavailable")
        self.upsert_calls.append(batch)
        return MagicMock(
            received_count=len(batch),
            unique_count=len(batch),
            inserted_count=len(batch),
            updated_count=0,
        )

    def get_by_fingerprint(self, fingerprint):
        self.rows.setdefault(fingerprint, {"source_item_id": len(self.rows) + 1})
        return self.rows[fingerprint]


class RecordingCareerRepository:
    def __init__(self, *, fail=False, events=None):
        self.fail = fail
        self.events = events if events is not None else []
        self.upsert_calls = []

    def upsert_many(self, records):
        batch = list(records)
        self.events.append("career_signal_persistence")
        if self.fail:
            raise sqlite3.DatabaseError("signal write unavailable")
        self.upsert_calls.append(batch)
        return MagicMock(
            received_count=len(batch),
            unique_count=len(batch),
            inserted_count=len(batch),
            updated_count=0,
        )


def build_pipeline(
    *,
    pipeline_run_repository=None,
    planning_repository=None,
    execution_mode="live",
    events=None,
    profile=None,
    scope=None,
    preferences=None,
    paths=None,
    raw_item_loader=None,
    search_api_executor=None,
    rss_executor=None,
    selected_website_executor=None,
    ai_filter_executor=None,
    normalizer=None,
    source_repository=None,
    career_repository=None,
    user_profile_loader=None,
    search_scope_loader=None,
    career_path_generator=None,
    search_query_generator=None,
    search_plan_builder=None,
):
    events = events if events is not None else []
    profile = profile or make_profile()
    scope = scope or make_scope()
    preferences = preferences or {"market": "US", "weights": {"ai": 1}}
    paths = paths or [make_path()]
    queries = [make_query(path=paths[0])]
    plans = [make_plan(scope=scope, query=queries[0])]

    def default_profile_loader():
        events.append("profile_loaded")
        return profile

    def default_scope_loader():
        events.append("scope_loaded")
        return scope

    def default_career_path_generator(user_profile):
        events.append("career_path_generator")
        return paths

    def default_search_query_generator(target_paths):
        events.append("search_query_generator")
        return queries

    def default_search_plan_builder(search_queries, search_scope):
        events.append("search_plan_builder")
        return plans

    def default_search_api_executor(search_plans):
        events.append("external_search")
        return SearchAPIExecutionReport()

    return MockPipeline(
        raw_item_loader=raw_item_loader or (lambda: []),
        user_profile_loader=user_profile_loader or default_profile_loader,
        search_scope_loader=search_scope_loader or default_scope_loader,
        career_path_generator=(
            career_path_generator or default_career_path_generator
        ),
        search_query_generator=(
            search_query_generator or default_search_query_generator
        ),
        search_plan_builder=search_plan_builder or default_search_plan_builder,
        search_api_executor=search_api_executor or default_search_api_executor,
        rss_executor=rss_executor or (lambda search_scope, search_plans: ([], 0)),
        selected_website_executor=(
            selected_website_executor
            or (lambda search_scope, search_plans: ([], 0))
        ),
        ai_filter_executor=(
            ai_filter_executor
            or (
                lambda raw_items, user_profile, target_paths: (
                    AIFilterExecutionReport()
                )
            )
        ),
        normalizer=normalizer or (lambda raw_items, ai_results: []),
        source_item_repository=source_repository,
        career_signal_repository=career_repository,
        planning_bundle_repository=planning_repository,
        user_preferences_loader=lambda: preferences,
        planning_model_provider="deepseek",
        planning_model_name="deepseek-v4-pro",
        planning_prompt_version="target_career_path_prompt_v1",
        planning_generator_config=PLANNING_CONFIG,
        pipeline_run_repository=pipeline_run_repository,
        execution_mode=execution_mode,
    )


def execute_pipeline(pipeline, *, output_persister=None, success_reporter=None):
    output_persister = output_persister or (lambda output: Path("out.json"))
    with redirect_stdout(io.StringIO()) as stdout:
        output, persisted_output = execute_pipeline_runtime(
            pipeline,
            output_persister=output_persister,
            success_reporter=success_reporter,
        )
    return output, persisted_output, stdout.getvalue()


class TemporaryPipelineRunIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "pipeline-run.db"
        initialize_database(database_path=self.database_path)
        self.events = []
        self.run_repository = EventingPipelineRunRepository(
            self.database_path,
            self.events,
        )
        self.planning_repository = PlanningBundleRepository(
            database_path=self.database_path
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def build_live_pipeline(self, **overrides):
        values = {
            "pipeline_run_repository": self.run_repository,
            "planning_repository": self.planning_repository,
            "events": self.events,
        }
        values.update(overrides)
        return build_pipeline(**values)

    def only_run(self):
        records = self.run_repository.list_recent_runs()
        self.assertEqual(len(records), 1)
        return records[0]


class DependencyAndInitializationTests(TemporaryPipelineRunIntegrationTestCase):
    def test_runtime_accepts_optional_pipeline_run_repository(self):
        pipeline = self.build_live_pipeline()
        self.assertIs(pipeline.pipeline_run_repository, self.run_repository)

    def test_omitting_run_repository_preserves_existing_output(self):
        output = build_pipeline().run()
        self.assertEqual(output.pipeline_version, PIPELINE_VERSION)
        self.assertEqual(output.phase, PIPELINE_PHASE)

    def test_run_repository_is_independent_of_planning_repository(self):
        pipeline = build_pipeline(pipeline_run_repository=self.run_repository)
        execute_pipeline(pipeline)
        record = self.only_run()
        self.assertEqual(record.status, COMPLETED)
        self.assertIsNone(record.planning_bundle_id)

    def test_run_repository_is_independent_of_source_repository(self):
        source_repository = RecordingSourceRepository()
        pipeline = self.build_live_pipeline(source_repository=source_repository)
        execute_pipeline(pipeline)
        self.assertEqual(self.only_run().status, COMPLETED)

    def test_run_repository_is_independent_of_career_repository(self):
        pipeline = self.build_live_pipeline()
        execute_pipeline(pipeline)
        self.assertEqual(self.only_run().status, COMPLETED)

    def test_main_constructs_and_injects_pipeline_run_repository(self):
        main_module = importlib.import_module("src.main")
        captured = {}

        class FakeRepository:
            def __init__(self, database_path):
                self.database_path = database_path

        class FakePipeline:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs

            def run(self):
                return _fake_pipeline_output()

        with patch.object(main_module, "get_database_path", return_value=self.database_path), \
            patch.object(main_module, "initialize_database") as initialize_mock, \
            patch.object(main_module, "PipelineRunRepository", FakeRepository), \
            patch.object(main_module, "PlanningBundleRepository", FakeRepository), \
            patch.object(main_module, "SourceItemRepository", FakeRepository), \
            patch.object(main_module, "CareerSignalRepository", FakeRepository), \
            patch.object(main_module, "MockPipeline", FakePipeline), \
            patch.object(main_module, "ensure_project_directories"), \
            patch.object(main_module, "validate_required_planning_inputs"), \
            patch.object(main_module, "load_user_preferences_from_json", return_value={}), \
            patch.object(main_module, "_file_sha256", return_value="hash"), \
            patch.object(main_module, "save_json", return_value=Path("out.json")), \
            redirect_stdout(io.StringIO()):
            main_module.main()

        initialize_mock.assert_called_once_with(database_path=self.database_path)
        repository = captured["kwargs"]["pipeline_run_repository"]
        self.assertIsInstance(repository, FakeRepository)
        self.assertEqual(repository.database_path, self.database_path)

    def test_main_injects_live_execution_mode(self):
        main_module = importlib.import_module("src.main")
        captured = {}

        class FakeRepository:
            def __init__(self, database_path):
                self.database_path = database_path

        class FakePipeline:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def run(self):
                return _fake_pipeline_output()

        with patch.object(main_module, "get_database_path", return_value=self.database_path), \
            patch.object(main_module, "initialize_database"), \
            patch.object(main_module, "PipelineRunRepository", FakeRepository), \
            patch.object(main_module, "PlanningBundleRepository", FakeRepository), \
            patch.object(main_module, "SourceItemRepository", FakeRepository), \
            patch.object(main_module, "CareerSignalRepository", FakeRepository), \
            patch.object(main_module, "MockPipeline", FakePipeline), \
            patch.object(main_module, "ensure_project_directories"), \
            patch.object(main_module, "validate_required_planning_inputs"), \
            patch.object(main_module, "load_user_preferences_from_json", return_value={}), \
            patch.object(main_module, "save_json", return_value=Path("out.json")), \
            redirect_stdout(io.StringIO()):
            main_module.main()

        self.assertEqual(captured["execution_mode"], "live")

    def test_importing_main_does_not_create_a_pipeline_run(self):
        before_count = count_rows(self.database_path, "pipeline_runs")
        sys.modules.pop("src.main", None)
        importlib.import_module("src.main")
        self.assertEqual(count_rows(self.database_path, "pipeline_runs"), before_count)

    def test_importing_main_does_not_create_default_database(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = (
            DEFAULT_DATABASE_FILE.stat().st_mtime if before_exists else None
        )
        sys.modules.pop("src.main", None)
        importlib.import_module("src.main")
        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)
        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)

    def test_migrations_006_and_007_exist_and_008_is_absent(self):
        migration_dir = Path("src/database/sql")
        self.assertTrue((migration_dir / "005_pipeline_run_lifecycle.sql").exists())
        self.assertTrue(
            (migration_dir / "006_execution_ledger_provenance.sql").exists()
        )
        self.assertTrue(
            (migration_dir / "007_filter_decision_provenance.sql").exists()
        )
        self.assertEqual(list(migration_dir.glob("008*.sql")), [])


class StartAndModeTests(TemporaryPipelineRunIntegrationTestCase):
    def test_live_execution_starts_exactly_one_run(self):
        execute_pipeline(self.build_live_pipeline())
        self.assertEqual(count_rows(self.database_path, "pipeline_runs"), 1)

    def test_run_starts_before_planning(self):
        execute_pipeline(self.build_live_pipeline())
        self.assertLess(
            self.events.index("run_started"),
            self.events.index("career_path_generator"),
        )

    def test_run_starts_before_input_loading(self):
        execute_pipeline(self.build_live_pipeline())
        self.assertLess(
            self.events.index("run_started"),
            self.events.index("profile_loaded"),
        )

    def test_initial_status_is_running(self):
        execute_pipeline(self.build_live_pipeline())
        started = self.run_repository.started_records[0]
        self.assertEqual(started.status, RUNNING)

    def test_initial_planning_bundle_is_null(self):
        execute_pipeline(self.build_live_pipeline())
        started = self.run_repository.started_records[0]
        self.assertIsNone(started.planning_bundle_id)

    def test_pipeline_version_is_preserved(self):
        execute_pipeline(self.build_live_pipeline())
        self.assertEqual(self.only_run().pipeline_version, PIPELINE_VERSION)

    def test_pipeline_phase_is_preserved(self):
        execute_pipeline(self.build_live_pipeline())
        self.assertEqual(self.only_run().phase, PIPELINE_PHASE)

    def test_live_execution_mode_is_preserved(self):
        execute_pipeline(self.build_live_pipeline())
        self.assertEqual(self.only_run().execution_mode, "live")

    def test_start_metadata_is_non_sensitive_and_stable(self):
        execute_pipeline(self.build_live_pipeline())
        metadata = self.only_run().metadata
        self.assertEqual(
            set(metadata),
            {
                "planning_persistence_enabled",
                "source_item_persistence_enabled",
                "career_signal_persistence_enabled",
                "source_execution_persistence_enabled",
                "filter_decision_persistence_enabled",
            },
        )
        self.assertNotIn("profile", json.dumps(metadata).lower())

    def test_mock_execution_does_not_create_run(self):
        pipeline = self.build_live_pipeline(execution_mode="mock")
        execute_pipeline(pipeline)
        self.assertEqual(count_rows(self.database_path, "pipeline_runs"), 0)

    def test_dry_run_execution_does_not_create_run(self):
        pipeline = self.build_live_pipeline(execution_mode="dry_run")
        execute_pipeline(pipeline)
        self.assertEqual(count_rows(self.database_path, "pipeline_runs"), 0)

    def test_source_level_dry_run_does_not_reclassify_live_runtime(self):
        dry_item = make_raw_item(source_type=SourceType.SEARCH_API, mode="dry_run")
        pipeline = self.build_live_pipeline(
            search_api_executor=lambda plans: SearchAPIExecutionReport(
                raw_items=[dry_item],
                executed_plan_count=1,
            )
        )
        execute_pipeline(pipeline)
        self.assertEqual(self.only_run().execution_mode, "live")


class PlanningBundleAttachmentTests(TemporaryPipelineRunIntegrationTestCase):
    def test_newly_persisted_bundle_is_attached(self):
        pipeline = self.build_live_pipeline()
        execute_pipeline(pipeline)
        self.assertEqual(self.only_run().planning_bundle_id, pipeline.planning_bundle_id)

    def test_reused_bundle_is_attached(self):
        first = self.build_live_pipeline()
        execute_pipeline(first)
        second = self.build_live_pipeline()
        execute_pipeline(second)
        records = self.run_repository.list_recent_runs(limit=2)
        self.assertEqual(
            {record.planning_bundle_id for record in records},
            {first.planning_bundle_id},
        )

    def test_file_cache_materialization_attaches_bundle(self):
        cached_path = make_path(metadata={"used_cache": True})
        pipeline = self.build_live_pipeline(paths=[cached_path])
        execute_pipeline(pipeline)
        record = self.only_run()
        self.assertEqual(record.planning_bundle_id, pipeline.planning_bundle_id)
        self.assertEqual(record.summary["planning_generation_mode"], "file_cache")

    def test_exact_selected_bundle_id_is_used(self):
        first = self.build_live_pipeline(scope=make_scope(scope_id="first"))
        execute_pipeline(first)
        second = self.build_live_pipeline(scope=make_scope(scope_id="second"))
        execute_pipeline(second)
        self.assertNotEqual(first.planning_bundle_id, second.planning_bundle_id)
        self.assertEqual(
            self.run_repository.list_recent_runs(limit=1)[0].planning_bundle_id,
            second.planning_bundle_id,
        )

    def test_attachment_occurs_before_external_search(self):
        execute_pipeline(self.build_live_pipeline())
        attachment_index = next(
            index
            for index, event in enumerate(self.events)
            if event.startswith("bundle_attached:")
        )
        self.assertLess(attachment_index, self.events.index("external_search"))

    def test_bundle_lookup_and_generation_are_not_duplicated(self):
        execute_pipeline(self.build_live_pipeline())
        self.assertEqual(self.events.count("career_path_generator"), 1)
        self.assertEqual(count_rows(self.database_path, "planning_bundles"), 1)

    def test_reuse_skips_all_planning_builders(self):
        execute_pipeline(self.build_live_pipeline())
        self.events.clear()
        execute_pipeline(self.build_live_pipeline())
        self.assertNotIn("career_path_generator", self.events)
        self.assertNotIn("search_query_generator", self.events)
        self.assertNotIn("search_plan_builder", self.events)

    def test_one_bundle_can_be_used_by_multiple_completed_runs(self):
        pipeline = self.build_live_pipeline()
        execute_pipeline(pipeline)
        execute_pipeline(pipeline)
        records = self.run_repository.list_recent_runs(limit=2)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record.status == COMPLETED for record in records))
        self.assertEqual(len({record.planning_bundle_id for record in records}), 1)

    def test_attachment_failure_prevents_external_search(self):
        failing_repository = FailingAttachRepository(
            self.database_path,
            self.events,
        )
        pipeline = build_pipeline(
            pipeline_run_repository=failing_repository,
            planning_repository=self.planning_repository,
            events=self.events,
        )
        with self.assertRaisesRegex(RuntimeError, "attachment"):
            execute_pipeline(pipeline)
        self.assertNotIn("external_search", self.events)

    def test_attachment_failure_records_attachment_stage(self):
        failing_repository = FailingAttachRepository(
            self.database_path,
            self.events,
        )
        pipeline = build_pipeline(
            pipeline_run_repository=failing_repository,
            planning_repository=self.planning_repository,
            events=self.events,
        )
        with self.assertRaises(RuntimeError):
            execute_pipeline(pipeline)
        record = failing_repository.list_recent_runs(limit=1)[0]
        self.assertEqual(record.status, FAILED)
        self.assertEqual(record.failure_stage, "planning_bundle_attachment")


class SuccessfulCompletionTests(TemporaryPipelineRunIntegrationTestCase):
    def build_counting_pipeline(self):
        raw_items = [make_raw_item(1), make_raw_item(2)]
        report = make_filter_report(raw_items, accepted_indexes={0})
        return self.build_live_pipeline(
            raw_item_loader=lambda: raw_items,
            ai_filter_executor=lambda items, profile, paths: report,
            normalizer=lambda items, results: [make_signal(raw_items[0])],
        )

    def test_fully_successful_execution_completes_run(self):
        execute_pipeline(self.build_live_pipeline())
        self.assertEqual(self.only_run().status, COMPLETED)

    def test_completed_at_is_populated(self):
        execute_pipeline(self.build_live_pipeline())
        self.assertIsNotNone(self.only_run().completed_at)

    def test_completed_run_preserves_bundle(self):
        pipeline = self.build_live_pipeline()
        execute_pipeline(pipeline)
        self.assertEqual(self.only_run().planning_bundle_id, pipeline.planning_bundle_id)

    def test_summary_has_accurate_raw_item_count(self):
        execute_pipeline(self.build_counting_pipeline())
        self.assertEqual(self.only_run().summary["raw_item_count"], 2)

    def test_summary_has_accurate_accepted_count(self):
        execute_pipeline(self.build_counting_pipeline())
        self.assertEqual(self.only_run().summary["accepted_item_count"], 1)

    def test_summary_has_accurate_rejected_count(self):
        execute_pipeline(self.build_counting_pipeline())
        self.assertEqual(self.only_run().summary["rejected_item_count"], 1)

    def test_summary_has_accurate_career_signal_count(self):
        execute_pipeline(self.build_counting_pipeline())
        self.assertEqual(self.only_run().summary["career_signal_count"], 1)

    def test_summary_does_not_invent_persistence_counts(self):
        execute_pipeline(self.build_counting_pipeline())
        summary = self.only_run().summary
        self.assertNotIn("source_item_persisted_count", summary)
        self.assertNotIn("career_signal_persisted_count", summary)

    def test_completion_occurs_after_output_persistence(self):
        def persist_output(output):
            self.events.append("output_persisted")
            return Path("out.json")

        execute_pipeline(self.build_live_pipeline(), output_persister=persist_output)
        self.assertLess(
            self.events.index("output_persisted"),
            self.events.index("run_completed"),
        )

    def test_completion_occurs_after_final_reporting(self):
        def report_success(output, output_path):
            self.events.append("final_reported")

        execute_pipeline(
            self.build_live_pipeline(),
            success_reporter=report_success,
        )
        self.assertLess(
            self.events.index("final_reported"),
            self.events.index("run_completed"),
        )

    def test_pipeline_output_contract_has_no_run_id(self):
        output, _, _ = execute_pipeline(self.build_live_pipeline())
        self.assertNotIn("run_id", output.to_dict())
        self.assertNotIn("planning_bundle_id", output.to_dict())

    def test_saved_json_structure_is_unchanged(self):
        captured = {}

        def persist_output(output):
            captured["payload"] = convert_to_json_ready(output)
            return Path("out.json")

        output, _, _ = execute_pipeline(
            self.build_live_pipeline(),
            output_persister=persist_output,
        )
        self.assertEqual(captured["payload"], output.to_dict())
        self.assertNotIn("run_id", captured["payload"])

    def test_concise_start_and_completion_reporting(self):
        _, _, stdout = execute_pipeline(self.build_live_pipeline())
        self.assertIn("Pipeline Run started: run_id=", stdout)
        self.assertIn("Pipeline Run completed: run_id=", stdout)
        self.assertNotIn("Test User", stdout)


class FailureStageTests(TemporaryPipelineRunIntegrationTestCase):
    def _assert_failure(self, pipeline, expected_stage, exception_type):
        with self.assertRaises(exception_type):
            execute_pipeline(pipeline)
        record = self.run_repository.list_recent_runs(limit=1)[0]
        self.assertEqual(record.status, FAILED)
        self.assertEqual(record.failure_stage, expected_stage)
        return record

    def test_input_loading_failure_is_recorded(self):
        def fail_profile_loading():
            raise ValueError("profile unavailable")

        pipeline = self.build_live_pipeline(user_profile_loader=fail_profile_loading)
        record = self._assert_failure(pipeline, "input_loading", ValueError)
        self.assertIsNone(record.planning_bundle_id)

    def test_planning_failure_is_recorded(self):
        def fail_planning(profile):
            raise ValueError("planning unavailable")

        pipeline = self.build_live_pipeline(career_path_generator=fail_planning)
        record = self._assert_failure(pipeline, "planning", ValueError)
        self.assertIsNone(record.planning_bundle_id)

    def test_planning_persistence_failure_is_recorded(self):
        pipeline = build_pipeline(
            pipeline_run_repository=self.run_repository,
            planning_repository=FailingPlanningPersistRepository(),
            events=self.events,
        )
        record = self._assert_failure(pipeline, "planning", RuntimeError)
        self.assertIsNone(record.planning_bundle_id)

    def test_bundle_hydration_failure_is_recorded(self):
        execute_pipeline(self.build_live_pipeline())
        failing_planning_repository = FailingHydrationPlanningRepository(
            database_path=self.database_path
        )
        pipeline = build_pipeline(
            pipeline_run_repository=self.run_repository,
            planning_repository=failing_planning_repository,
            events=self.events,
        )
        with self.assertRaises(RuntimeError):
            execute_pipeline(pipeline)
        record = self.run_repository.list_recent_runs(limit=1)[0]
        self.assertEqual(record.failure_stage, "planning")

    def test_external_search_failure_is_recorded(self):
        original = TimeoutError("search timeout")

        def fail_search(plans):
            raise original

        pipeline = self.build_live_pipeline(search_api_executor=fail_search)
        record = self._assert_failure(pipeline, "external_search", TimeoutError)
        self.assertEqual(record.planning_bundle_id, pipeline.planning_bundle_id)

    def test_source_item_persistence_failure_is_recorded(self):
        external_item = make_raw_item(1, source_type=SourceType.SEARCH_API)
        pipeline = self.build_live_pipeline(
            search_api_executor=lambda plans: SearchAPIExecutionReport(
                raw_items=[external_item],
                executed_plan_count=1,
            ),
            source_repository=RecordingSourceRepository(fail=True),
        )
        record = self._assert_failure(
            pipeline,
            "source_item_persistence",
            RuntimeError,
        )
        self.assertIsNotNone(record.planning_bundle_id)

    def test_ai_filter_failure_is_recorded(self):
        def fail_filter(raw_items, profile, paths):
            raise LookupError("filter unavailable")

        pipeline = self.build_live_pipeline(ai_filter_executor=fail_filter)
        self._assert_failure(pipeline, "ai_filter", LookupError)

    def test_normalization_failure_is_recorded(self):
        def fail_normalization(raw_items, results):
            raise ArithmeticError("normalization unavailable")

        pipeline = self.build_live_pipeline(normalizer=fail_normalization)
        self._assert_failure(pipeline, "normalization", ArithmeticError)

    def test_career_signal_persistence_failure_is_recorded(self):
        external_item = make_raw_item(1, source_type=SourceType.SEARCH_API)
        filter_report = make_filter_report([external_item], accepted_indexes={0})
        pipeline = self.build_live_pipeline(
            search_api_executor=lambda plans: SearchAPIExecutionReport(
                raw_items=[external_item],
                executed_plan_count=1,
            ),
            ai_filter_executor=lambda raw_items, profile, paths: filter_report,
            normalizer=lambda raw_items, results: [make_signal(external_item)],
            source_repository=RecordingSourceRepository(),
            career_repository=RecordingCareerRepository(fail=True),
        )
        self._assert_failure(
            pipeline,
            "career_signal_persistence",
            RuntimeError,
        )

    def test_output_persistence_failure_is_recorded(self):
        original = OSError("disk full")

        def fail_output(output):
            raise original

        pipeline = self.build_live_pipeline()
        with self.assertRaises(OSError):
            execute_pipeline(pipeline, output_persister=fail_output)
        record = self.only_run()
        self.assertEqual(record.failure_stage, "output_persistence")
        self.assertEqual(record.planning_bundle_id, pipeline.planning_bundle_id)

    def test_final_reporting_failure_is_recorded(self):
        def fail_reporting(output, output_path):
            raise RuntimeError("reporting unavailable")

        pipeline = self.build_live_pipeline()
        with self.assertRaises(RuntimeError):
            execute_pipeline(pipeline, success_reporter=fail_reporting)
        self.assertEqual(self.only_run().failure_stage, "final_reporting")

    def test_failure_error_type_is_original_exception_class(self):
        def fail_filter(raw_items, profile, paths):
            raise KeyError("filter key")

        pipeline = self.build_live_pipeline(ai_filter_executor=fail_filter)
        record = self._assert_failure(pipeline, "ai_filter", KeyError)
        self.assertEqual(record.error_type, "KeyError")

    def test_failure_error_message_is_bounded(self):
        def fail_filter(raw_items, profile, paths):
            raise ValueError("x" * 2000)

        pipeline = self.build_live_pipeline(ai_filter_executor=fail_filter)
        record = self._assert_failure(pipeline, "ai_filter", ValueError)
        self.assertLessEqual(len(record.error_message), 1000)

    def test_failure_after_attachment_preserves_bundle(self):
        def fail_filter(raw_items, profile, paths):
            raise ValueError("filter unavailable")

        pipeline = self.build_live_pipeline(ai_filter_executor=fail_filter)
        record = self._assert_failure(pipeline, "ai_filter", ValueError)
        self.assertEqual(record.planning_bundle_id, pipeline.planning_bundle_id)

    def test_failed_run_is_not_completed(self):
        def fail_filter(raw_items, profile, paths):
            raise ValueError("filter unavailable")

        pipeline = self.build_live_pipeline(ai_filter_executor=fail_filter)
        record = self._assert_failure(pipeline, "ai_filter", ValueError)
        self.assertEqual(record.status, FAILED)
        self.assertNotIn("run_completed", self.events)


class ExceptionPreservationTests(TemporaryPipelineRunIntegrationTestCase):
    def test_original_exception_object_is_reraised(self):
        original = ValueError("filter unavailable")

        def fail_filter(raw_items, profile, paths):
            raise original

        pipeline = self.build_live_pipeline(ai_filter_executor=fail_filter)
        with self.assertRaises(ValueError) as context:
            execute_pipeline(pipeline)
        self.assertIs(context.exception, original)

    def test_original_traceback_is_available(self):
        def fail_filter(raw_items, profile, paths):
            raise ValueError("filter unavailable")

        pipeline = self.build_live_pipeline(ai_filter_executor=fail_filter)
        try:
            execute_pipeline(pipeline)
        except ValueError as error:
            self.assertIsNotNone(error.__traceback__)
        else:
            self.fail("ValueError was not re-raised")

    def test_start_failure_prevents_planning(self):
        repository = FailingStartRepository(self.database_path, self.events)
        pipeline = build_pipeline(
            pipeline_run_repository=repository,
            planning_repository=self.planning_repository,
            events=self.events,
        )
        with self.assertRaisesRegex(RuntimeError, "start failed") as context:
            execute_pipeline(pipeline)
        self.assertIsInstance(context.exception.__cause__, PipelineRunRepositoryError)
        self.assertNotIn("career_path_generator", self.events)

    def test_start_failure_prevents_external_search(self):
        repository = FailingStartRepository(self.database_path, self.events)
        pipeline = build_pipeline(
            pipeline_run_repository=repository,
            planning_repository=self.planning_repository,
            events=self.events,
        )
        with self.assertRaises(RuntimeError):
            execute_pipeline(pipeline)
        self.assertNotIn("external_search", self.events)

    def test_fail_run_failure_does_not_hide_original_exception(self):
        repository = FailingFailRepository(self.database_path, self.events)
        original = ValueError("filter unavailable")

        def fail_filter(raw_items, profile, paths):
            raise original

        pipeline = build_pipeline(
            pipeline_run_repository=repository,
            planning_repository=self.planning_repository,
            events=self.events,
            ai_filter_executor=fail_filter,
        )
        with self.assertRaises(ValueError) as context:
            execute_pipeline(pipeline)
        self.assertIs(context.exception, original)
        self.assertTrue(
            any("failure persistence also failed" in note for note in original.__notes__)
        )

    def test_fail_run_failure_leaves_run_uncompleted(self):
        repository = FailingFailRepository(self.database_path, self.events)

        def fail_filter(raw_items, profile, paths):
            raise ValueError("filter unavailable")

        pipeline = build_pipeline(
            pipeline_run_repository=repository,
            planning_repository=self.planning_repository,
            events=self.events,
            ai_filter_executor=fail_filter,
        )
        with self.assertRaises(ValueError):
            execute_pipeline(pipeline)
        record = repository.list_recent_runs(limit=1)[0]
        self.assertEqual(record.status, RUNNING)

    def test_complete_run_failure_is_surfaced_and_chained(self):
        repository = FailingCompleteRepository(self.database_path, self.events)
        pipeline = build_pipeline(
            pipeline_run_repository=repository,
            planning_repository=self.planning_repository,
            events=self.events,
        )
        with self.assertRaisesRegex(RuntimeError, "completion failed") as context:
            execute_pipeline(pipeline)
        self.assertIsInstance(context.exception.__cause__, PipelineRunRepositoryError)

    def test_complete_run_failure_marks_still_running_run_failed(self):
        repository = FailingCompleteRepository(self.database_path, self.events)
        pipeline = build_pipeline(
            pipeline_run_repository=repository,
            planning_repository=self.planning_repository,
            events=self.events,
        )
        with self.assertRaises(RuntimeError):
            execute_pipeline(pipeline)
        record = repository.list_recent_runs(limit=1)[0]
        self.assertEqual(record.status, FAILED)
        self.assertEqual(record.failure_stage, "pipeline_run_completion")

    def test_failed_transition_cannot_later_complete(self):
        def fail_filter(raw_items, profile, paths):
            raise ValueError("filter unavailable")

        pipeline = self.build_live_pipeline(ai_filter_executor=fail_filter)
        with self.assertRaises(ValueError):
            execute_pipeline(pipeline)
        record = self.only_run()
        with self.assertRaises(PipelineRunRepositoryError):
            self.run_repository.complete_run(record.run_id)


class CompatibilityAndScopeTests(TemporaryPipelineRunIntegrationTestCase):
    def test_planning_bundle_reuse_still_avoids_duplicate_bundle_rows(self):
        execute_pipeline(self.build_live_pipeline())
        execute_pipeline(self.build_live_pipeline())
        self.assertEqual(count_rows(self.database_path, "planning_bundles"), 1)

    def test_source_item_persistence_behavior_is_unchanged(self):
        external_item = make_raw_item(1, source_type=SourceType.SEARCH_API)
        source_repository = RecordingSourceRepository()
        pipeline = self.build_live_pipeline(
            search_api_executor=lambda plans: SearchAPIExecutionReport(
                raw_items=[external_item],
                executed_plan_count=1,
            ),
            source_repository=source_repository,
        )
        execute_pipeline(pipeline)
        self.assertEqual(source_repository.upsert_calls, [[external_item]])

    def test_career_signal_persistence_behavior_is_unchanged(self):
        external_item = make_raw_item(1, source_type=SourceType.SEARCH_API)
        source_repository = RecordingSourceRepository()
        career_repository = RecordingCareerRepository()
        report = make_filter_report([external_item], accepted_indexes={0})
        pipeline = self.build_live_pipeline(
            search_api_executor=lambda plans: SearchAPIExecutionReport(
                raw_items=[external_item],
                executed_plan_count=1,
            ),
            ai_filter_executor=lambda raw_items, profile, paths: report,
            normalizer=lambda raw_items, results: [make_signal(external_item)],
            source_repository=source_repository,
            career_repository=career_repository,
        )
        execute_pipeline(pipeline)
        self.assertEqual(len(career_repository.upsert_calls), 1)

    def test_dry_run_source_items_are_still_not_persisted(self):
        dry_item = make_raw_item(
            1,
            source_type=SourceType.SEARCH_API,
            mode="dry_run",
        )
        source_repository = RecordingSourceRepository()
        pipeline = self.build_live_pipeline(
            search_api_executor=lambda plans: SearchAPIExecutionReport(
                raw_items=[dry_item],
                executed_plan_count=1,
            ),
            source_repository=source_repository,
        )
        execute_pipeline(pipeline)
        self.assertEqual(source_repository.upsert_calls, [])

    def test_source_items_have_no_run_id_column(self):
        self.assertNotIn("run_id", self._table_columns("source_items"))

    def test_career_signals_have_no_run_id_column(self):
        self.assertNotIn("run_id", self._table_columns("career_signals"))

    def test_source_execution_repository_is_integrated_without_raw_sql(self):
        tables = self._tables()
        self.assertIn("source_executions", tables)
        self.assertIn("source_item_discoveries", tables)
        pipeline_text = Path("src/pipeline.py").read_text(encoding="utf-8")
        main_text = Path("src/main.py").read_text(encoding="utf-8")
        self.assertIn("SourceExecutionRepository", pipeline_text)
        self.assertIn("SourceExecutionRepository", main_text)
        self.assertNotIn("import sqlite3", pipeline_text)

    def test_filter_decision_pipeline_dependency_is_optional(self):
        tables = self._tables()
        self.assertIn("filter_decisions", tables)
        self.assertIn("filter_executions", tables)
        self.assertIn("run_source_item_filter_statuses", tables)
        self.assertIsNone(
            self.build_live_pipeline().filter_decision_repository
        )

    def test_no_live_network_or_llm_call_occurs(self):
        with patch("requests.get") as request_get, patch(
            "src.career_path_generator.TargetCareerPathClient"
        ) as llm_client:
            execute_pipeline(self.build_live_pipeline())
        request_get.assert_not_called()
        llm_client.assert_not_called()

    def test_temp_integration_does_not_modify_configured_database(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = (
            DEFAULT_DATABASE_FILE.stat().st_mtime if before_exists else None
        )
        execute_pipeline(self.build_live_pipeline())
        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)
        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)

    def test_migrations_apply_through_007(self):
        connection = open_database_connection(self.database_path)
        try:
            versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
        finally:
            connection.close()
        self.assertEqual(
            versions,
            ["001", "002", "003", "004", "005", "006", "007"],
        )

    def test_pipeline_runs_has_planning_bundle_foreign_key(self):
        connection = open_database_connection(self.database_path)
        try:
            foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(pipeline_runs)"
            ).fetchall()
        finally:
            connection.close()
        self.assertTrue(
            any(
                row[2] == "planning_bundles"
                and row[3] == "planning_bundle_id"
                for row in foreign_keys
            )
        )

    def _table_columns(self, table_name):
        connection = open_database_connection(self.database_path)
        try:
            return {
                row[1]
                for row in connection.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
            }
        finally:
            connection.close()

    def _tables(self):
        connection = open_database_connection(self.database_path)
        try:
            return {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            connection.close()


def _fake_pipeline_output():
    summary = MagicMock()
    for attribute in (
        "total_target_career_paths",
        "total_search_queries",
        "total_search_plans",
        "total_search_api_plans_executed",
        "total_search_api_plans_deferred",
        "total_search_api_result_failures",
        "total_rss_feeds_executed",
        "total_selected_websites_executed",
        "total_raw_items",
        "total_raw_items_sent_to_ai_filter",
        "total_ai_filter_results",
        "total_filtered_raw_items",
        "total_rejected_raw_items",
        "total_career_signals",
    ):
        setattr(summary, attribute, 0)
    return MagicMock(summary=summary)


if __name__ == "__main__":
    unittest.main()
