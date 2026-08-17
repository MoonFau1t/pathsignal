from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.config import DEFAULT_DATABASE_FILE
from src.database.connection import open_database_connection
from src.database.migrations import discover_migrations, initialize_database
from src.database.repositories.career_signal_repository import CareerSignalRepository
from src.database.repositories.pipeline_run_repository import (
    PipelineRunCompletion,
    PipelineRunFailure,
    PipelineRunRecord,
    PipelineRunRepository,
    PipelineRunRepositoryError,
    PipelineRunStart,
)
from src.database.repositories.planning_bundle_repository import (
    PlanningBundleRepository,
    PlanningBundleWrite,
)
from src.database.repositories.source_item_repository import SourceItemRepository
from src.models import (
    CareerPathCategory,
    CareerSignal,
    RawItem,
    SearchPlan,
    SearchQuery,
    SearchQueryType,
    SearchScope,
    SignalCategory,
    SourceType,
    TargetCareerPath,
    UserProfile,
)


STARTED_AT = "2026-07-30T00:00:00+00:00"
UPDATED_AT = "2026-07-30T00:01:00+00:00"
COMPLETED_AT = "2026-07-30T00:02:00+00:00"


class TemporaryPipelineRunDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "pipeline-run.db"
        initialize_database(database_path=self.database_path)
        self.repository = PipelineRunRepository(database_path=self.database_path)
        self.planning_repository = PlanningBundleRepository(
            database_path=self.database_path
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def count_rows(self, table_name):
        connection = open_database_connection(self.database_path)

        try:
            return int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table_name}"
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def table_exists(self, table_name):
        connection = open_database_connection(self.database_path)

        try:
            return (
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM sqlite_master
                    WHERE type = 'table' AND name = ?
                    """,
                    (table_name,),
                ).fetchone()[0]
                == 1
            )
        finally:
            connection.close()

    def create_run(self, run_id="run_test"):
        return self.repository.start_run(
            PipelineRunStart(
                run_id=run_id,
                pipeline_version="v1",
                phase="phase_10_normalizer_to_career_signal",
                execution_mode="dry_run",
                metadata={"mode": "test"},
            ),
            started_at=STARTED_AT,
        )

    def create_bundle(self):
        return self.planning_repository.persist_planning_bundle(
            make_bundle_write(),
            created_at=STARTED_AT,
        )


class PipelineRunMigrationSchemaTests(TemporaryPipelineRunDatabaseTestCase):
    def test_migration_007_is_discovered_after_006(self):
        migrations = discover_migrations()

        self.assertIn("006", [migration.version for migration in migrations])
        self.assertEqual(migrations[-1].name, "filter_decision_provenance")

    def test_migrations_apply_in_order_through_007(self):
        connection = open_database_connection(self.database_path)

        try:
            rows = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(
            [row["version"] for row in rows],
            ["001", "002", "003", "004", "005", "006", "007"],
        )

    def test_repeated_migration_execution_is_idempotent(self):
        second = initialize_database(database_path=self.database_path)

        self.assertEqual(second, [])

    def test_existing_migrations_remain_present(self):
        names = [migration.path.name for migration in discover_migrations()]

        self.assertIn("001_initial_schema.sql", names)
        self.assertIn("002_source_items.sql", names)
        self.assertIn("003_career_signals.sql", names)
        self.assertIn("004_planning_bundles.sql", names)

    def test_pipeline_runs_contains_required_lifecycle_fields(self):
        connection = open_database_connection(self.database_path)

        try:
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(pipeline_runs)"
                ).fetchall()
            }
        finally:
            connection.close()

        self.assertTrue(
            {
                "run_id",
                "pipeline_version",
                "phase",
                "status",
                "started_at",
                "completed_at",
                "summary_json",
                "metadata_json",
                "planning_bundle_id",
                "execution_mode",
                "failure_stage",
                "error_type",
                "error_message",
                "updated_at",
            }.issubset(columns)
        )

    def test_planning_bundle_foreign_key_is_enabled_and_valid(self):
        connection = open_database_connection(self.database_path)

        try:
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            fks = connection.execute(
                "PRAGMA foreign_key_list(pipeline_runs)"
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(foreign_keys, 1)
        self.assertIn("planning_bundles", {row["table"] for row in fks})

    def test_useful_indexes_exist_without_redundant_indexes(self):
        connection = open_database_connection(self.database_path)

        try:
            indexes = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA index_list(pipeline_runs)"
                ).fetchall()
            }
        finally:
            connection.close()

        self.assertIn("idx_pipeline_runs_status_started_at", indexes)
        self.assertIn("idx_pipeline_runs_planning_bundle_id", indexes)
        self.assertEqual(len([name for name in indexes if name.startswith("idx_pipeline_runs_status")]), 1)

    def test_existing_tables_remain_intact(self):
        self.assertTrue(self.table_exists("source_items"))
        self.assertTrue(self.table_exists("career_signals"))
        self.assertTrue(self.table_exists("planning_bundles"))


class PipelineRunStartTests(TemporaryPipelineRunDatabaseTestCase):
    def test_start_run_creates_a_running_row(self):
        record = self.create_run()

        self.assertEqual(record.status, "running")
        self.assertEqual(self.count_rows("pipeline_runs"), 1)

    def test_started_at_is_populated(self):
        record = self.create_run()

        self.assertEqual(record.started_at, STARTED_AT)
        self.assertEqual(record.updated_at, STARTED_AT)

    def test_execution_mode_is_preserved(self):
        record = self.create_run()

        self.assertEqual(record.execution_mode, "dry_run")

    def test_metadata_json_round_trips(self):
        record = self.repository.start_run(
            PipelineRunStart(
                pipeline_version="v1",
                phase="phase",
                metadata={"nested": {"enabled": True}},
            ),
            started_at=STARTED_AT,
        )

        self.assertEqual(record.metadata, {"nested": {"enabled": True}})

    def test_unicode_json_round_trips(self):
        record = self.repository.start_run(
            PipelineRunStart(
                pipeline_version="v1",
                phase="phase",
                metadata={"note": "中文"},
                summary={"mode": "测试"},
            ),
            started_at=STARTED_AT,
        )

        self.assertEqual(record.metadata["note"], "中文")
        self.assertEqual(record.summary["mode"], "测试")

    def test_null_planning_bundle_id_is_allowed_initially(self):
        record = self.create_run()

        self.assertIsNone(record.planning_bundle_id)

    def test_two_starts_create_different_run_ids(self):
        first = self.repository.start_run(
            PipelineRunStart(pipeline_version="v1", phase="phase"),
            started_at=STARTED_AT,
        )
        second = self.repository.start_run(
            PipelineRunStart(pipeline_version="v1", phase="phase"),
            started_at=STARTED_AT,
        )

        self.assertNotEqual(first.run_id, second.run_id)


class PipelineRunAttachmentTests(TemporaryPipelineRunDatabaseTestCase):
    def test_existing_bundle_can_be_attached(self):
        run = self.create_run()
        bundle = self.create_bundle()

        updated = self.repository.attach_planning_bundle(
            run.run_id,
            bundle.planning_bundle_id,
            updated_at=UPDATED_AT,
        )

        self.assertEqual(updated.planning_bundle_id, bundle.planning_bundle_id)
        self.assertEqual(updated.updated_at, UPDATED_AT)

    def test_relationship_round_trips(self):
        run = self.create_run()
        bundle = self.create_bundle()
        self.repository.attach_planning_bundle(run.run_id, bundle.planning_bundle_id)

        fetched = self.repository.get_run(run.run_id)

        self.assertEqual(fetched.planning_bundle_id, bundle.planning_bundle_id)

    def test_one_bundle_can_be_referenced_by_multiple_runs(self):
        first = self.create_run("run_first")
        second = self.create_run("run_second")
        bundle = self.create_bundle()

        self.repository.attach_planning_bundle(first.run_id, bundle.planning_bundle_id)
        self.repository.attach_planning_bundle(second.run_id, bundle.planning_bundle_id)

        self.assertEqual(self.repository.get_run(first.run_id).planning_bundle_id, bundle.planning_bundle_id)
        self.assertEqual(self.repository.get_run(second.run_id).planning_bundle_id, bundle.planning_bundle_id)

    def test_unknown_bundle_attachment_fails(self):
        run = self.create_run()

        with self.assertRaises(PipelineRunRepositoryError) as context:
            self.repository.attach_planning_bundle(run.run_id, 999)

        self.assertIsInstance(context.exception.__cause__, sqlite3.IntegrityError)

    def test_unknown_run_attachment_fails(self):
        bundle = self.create_bundle()

        with self.assertRaisesRegex(PipelineRunRepositoryError, "was not found"):
            self.repository.attach_planning_bundle("missing", bundle.planning_bundle_id)

    def test_attaching_to_non_running_run_fails(self):
        run = self.create_run()
        bundle = self.create_bundle()
        self.repository.attach_planning_bundle(run.run_id, bundle.planning_bundle_id)
        self.repository.complete_run(
            run.run_id,
            PipelineRunCompletion(summary={"ok": True}),
        )

        with self.assertRaisesRegex(PipelineRunRepositoryError, "running"):
            self.repository.attach_planning_bundle(run.run_id, bundle.planning_bundle_id)

    def test_reattaching_same_bundle_is_idempotent(self):
        run = self.create_run()
        bundle = self.create_bundle()
        first = self.repository.attach_planning_bundle(run.run_id, bundle.planning_bundle_id)
        second = self.repository.attach_planning_bundle(run.run_id, bundle.planning_bundle_id)

        self.assertEqual(second.planning_bundle_id, first.planning_bundle_id)

    def test_reassignment_to_different_bundle_fails(self):
        run = self.create_run()
        first_bundle = self.create_bundle()
        second_bundle = self.planning_repository.persist_planning_bundle(
            make_bundle_write(path_title="Changed"),
            created_at=UPDATED_AT,
        )
        self.repository.attach_planning_bundle(run.run_id, first_bundle.planning_bundle_id)

        with self.assertRaisesRegex(PipelineRunRepositoryError, "different Planning Bundle"):
            self.repository.attach_planning_bundle(run.run_id, second_bundle.planning_bundle_id)

    def test_failed_attachment_leaves_run_unchanged(self):
        run = self.create_run()

        with self.assertRaises(PipelineRunRepositoryError):
            self.repository.attach_planning_bundle(run.run_id, 999)

        unchanged = self.repository.get_run(run.run_id)
        self.assertIsNone(unchanged.planning_bundle_id)
        self.assertEqual(unchanged.status, "running")


class PipelineRunCompletionTests(TemporaryPipelineRunDatabaseTestCase):
    def create_attached_run(self):
        run = self.create_run()
        bundle = self.create_bundle()
        self.repository.attach_planning_bundle(run.run_id, bundle.planning_bundle_id)
        return run

    def test_running_run_can_complete(self):
        run = self.create_attached_run()

        completed = self.repository.complete_run(
            run.run_id,
            PipelineRunCompletion(summary={"career_signal_count": 2}),
            completed_at=COMPLETED_AT,
        )

        self.assertEqual(completed.status, "completed")

    def test_completed_at_is_populated(self):
        run = self.create_attached_run()
        completed = self.repository.complete_run(
            run.run_id,
            completed_at=COMPLETED_AT,
        )

        self.assertEqual(completed.completed_at, COMPLETED_AT)
        self.assertEqual(completed.updated_at, COMPLETED_AT)

    def test_summary_json_round_trips(self):
        run = self.create_attached_run()

        completed = self.repository.complete_run(
            run.run_id,
            PipelineRunCompletion(summary={"nested": {"ok": True}}),
        )

        self.assertEqual(completed.summary, {"nested": {"ok": True}})

    def test_stable_counts_can_be_stored_in_summary_json(self):
        run = self.create_attached_run()
        summary = {
            "raw_item_count": 10,
            "accepted_item_count": 4,
            "rejected_item_count": 6,
            "career_signal_count": 4,
            "planning_bundle_reuse_mode": "database_reuse",
        }

        completed = self.repository.complete_run(
            run.run_id,
            PipelineRunCompletion(summary=summary),
        )

        self.assertEqual(completed.summary, summary)

    def test_repeated_completion_fails(self):
        run = self.create_attached_run()
        self.repository.complete_run(run.run_id)

        with self.assertRaisesRegex(PipelineRunRepositoryError, "running"):
            self.repository.complete_run(run.run_id)

    def test_failed_run_cannot_complete(self):
        run = self.create_run()
        self.repository.fail_run(
            run.run_id,
            PipelineRunFailure(
                failure_stage="planning",
                error_type="RuntimeError",
                error_message="failed",
            ),
        )

        with self.assertRaisesRegex(PipelineRunRepositoryError, "running"):
            self.repository.complete_run(run.run_id)

    def test_unknown_run_cannot_complete(self):
        with self.assertRaisesRegex(PipelineRunRepositoryError, "was not found"):
            self.repository.complete_run("missing")

    def test_completion_requires_planning_bundle_by_default(self):
        run = self.create_run()

        with self.assertRaisesRegex(PipelineRunRepositoryError, "Planning Bundle"):
            self.repository.complete_run(run.run_id)

    def test_failed_completion_leaves_original_run_state_intact(self):
        run = self.create_run()

        with self.assertRaises(PipelineRunRepositoryError):
            self.repository.complete_run(run.run_id)

        unchanged = self.repository.get_run(run.run_id)
        self.assertEqual(unchanged.status, "running")
        self.assertIsNone(unchanged.completed_at)


class PipelineRunFailureTests(TemporaryPipelineRunDatabaseTestCase):
    def test_running_run_can_fail(self):
        run = self.create_run()

        failed = self.repository.fail_run(
            run.run_id,
            PipelineRunFailure(
                failure_stage="planning",
                error_type="ValueError",
                error_message="bad input",
            ),
            failed_at=COMPLETED_AT,
        )

        self.assertEqual(failed.status, "failed")

    def test_failure_stage_is_preserved(self):
        run = self.create_run()
        failed = self.repository.fail_run(
            run.run_id,
            PipelineRunFailure("external_search", "RuntimeError", "boom"),
        )

        self.assertEqual(failed.failure_stage, "external_search")

    def test_error_type_is_preserved(self):
        run = self.create_run()
        failed = self.repository.fail_run(
            run.run_id,
            PipelineRunFailure("planning", "SearchScopeResolutionError", "bad"),
        )

        self.assertEqual(failed.error_type, "SearchScopeResolutionError")

    def test_concise_error_message_is_preserved(self):
        run = self.create_run()
        failed = self.repository.fail_run(
            run.run_id,
            PipelineRunFailure("planning", "RuntimeError", "x" * 1500),
        )

        self.assertEqual(len(failed.error_message), 1000)

    def test_attached_bundle_remains_attached_after_failure(self):
        run = self.create_run()
        bundle = self.create_bundle()
        self.repository.attach_planning_bundle(run.run_id, bundle.planning_bundle_id)

        failed = self.repository.fail_run(
            run.run_id,
            PipelineRunFailure("external_search", "RuntimeError", "failed"),
        )

        self.assertEqual(failed.planning_bundle_id, bundle.planning_bundle_id)

    def test_completed_run_cannot_fail(self):
        run = self.create_run()
        bundle = self.create_bundle()
        self.repository.attach_planning_bundle(run.run_id, bundle.planning_bundle_id)
        self.repository.complete_run(run.run_id)

        with self.assertRaisesRegex(PipelineRunRepositoryError, "running"):
            self.repository.fail_run(
                run.run_id,
                PipelineRunFailure("post", "RuntimeError", "nope"),
            )

    def test_repeated_failure_fails(self):
        run = self.create_run()
        self.repository.fail_run(
            run.run_id,
            PipelineRunFailure("planning", "RuntimeError", "failed"),
        )

        with self.assertRaisesRegex(PipelineRunRepositoryError, "running"):
            self.repository.fail_run(
                run.run_id,
                PipelineRunFailure("planning", "RuntimeError", "again"),
            )

    def test_unknown_run_cannot_fail(self):
        with self.assertRaisesRegex(PipelineRunRepositoryError, "was not found"):
            self.repository.fail_run(
                "missing",
                PipelineRunFailure("planning", "RuntimeError", "failed"),
            )

    def test_failed_failure_update_leaves_original_state_intact(self):
        run = self.create_run()

        with self.assertRaises(PipelineRunRepositoryError):
            self.repository.fail_run(
                run.run_id,
                PipelineRunFailure("", "RuntimeError", "failed"),
            )

        unchanged = self.repository.get_run(run.run_id)
        self.assertEqual(unchanged.status, "running")
        self.assertIsNone(unchanged.completed_at)


class PipelineRunRetrievalTests(TemporaryPipelineRunDatabaseTestCase):
    def test_get_run_returns_typed_record(self):
        run = self.create_run()

        fetched = self.repository.get_run(run.run_id)

        self.assertIsInstance(fetched, PipelineRunRecord)

    def test_unknown_run_lookup_returns_none(self):
        self.assertIsNone(self.repository.get_run("missing"))

    def test_recent_runs_are_deterministically_ordered(self):
        self.repository.start_run(
            PipelineRunStart(run_id="run_a", pipeline_version="v1", phase="phase"),
            started_at="2026-07-30T00:00:00+00:00",
        )
        self.repository.start_run(
            PipelineRunStart(run_id="run_c", pipeline_version="v1", phase="phase"),
            started_at="2026-07-30T00:01:00+00:00",
        )
        self.repository.start_run(
            PipelineRunStart(run_id="run_b", pipeline_version="v1", phase="phase"),
            started_at="2026-07-30T00:01:00+00:00",
        )

        runs = self.repository.list_recent_runs(limit=3)

        self.assertEqual([run.run_id for run in runs], ["run_c", "run_b", "run_a"])

    def test_recent_run_limit_is_enforced(self):
        self.create_run("run_one")
        self.create_run("run_two")

        runs = self.repository.list_recent_runs(limit=1)

        self.assertEqual(len(runs), 1)

    def test_invalid_limits_fail_clearly(self):
        with self.assertRaisesRegex(PipelineRunRepositoryError, "positive integer"):
            self.repository.list_recent_runs(limit=0)

    def test_status_filtering_works(self):
        running = self.create_run("run_running")
        failed = self.create_run("run_failed")
        self.repository.fail_run(
            failed.run_id,
            PipelineRunFailure("planning", "RuntimeError", "failed"),
        )

        runs = self.repository.list_runs_by_status("running")

        self.assertEqual([run.run_id for run in runs], [running.run_id])

    def test_invalid_status_filter_fails_clearly(self):
        with self.assertRaisesRegex(PipelineRunRepositoryError, "Invalid"):
            self.repository.list_runs_by_status("paused")


class PipelineRunIsolationCompatibilityTests(TemporaryPipelineRunDatabaseTestCase):
    def test_transaction_rollback_preserves_prior_runs(self):
        self.create_run("run_existing")

        with self.assertRaises(PipelineRunRepositoryError):
            self.repository.start_run(
                PipelineRunStart(
                    run_id="run_existing",
                    pipeline_version="v1",
                    phase="phase",
                )
            )

        self.assertEqual(self.count_rows("pipeline_runs"), 1)

    def test_failed_start_leaves_no_row(self):
        with self.assertRaises(PipelineRunRepositoryError):
            self.repository.start_run(
                PipelineRunStart(pipeline_version="", phase="phase")
            )

        self.assertEqual(self.count_rows("pipeline_runs"), 0)

    def test_failed_completion_update_leaves_run_running(self):
        run = self.create_run()

        with patch(
            "src.database.repositories.pipeline_run_repository.canonical_json",
            side_effect=RuntimeError("serialization failed"),
        ):
            with self.assertRaises(PipelineRunRepositoryError):
                self.repository.complete_run(
                    run.run_id,
                    PipelineRunCompletion(summary={"ok": True}),
                    require_planning_bundle=False,
                )

        self.assertEqual(self.repository.get_run(run.run_id).status, "running")

    def test_failed_failure_update_leaves_run_running(self):
        run = self.create_run()

        with patch(
            "src.database.repositories.pipeline_run_repository.canonical_json",
            side_effect=RuntimeError("serialization failed"),
        ):
            with self.assertRaises(PipelineRunRepositoryError):
                self.repository.fail_run(
                    run.run_id,
                    PipelineRunFailure("planning", "RuntimeError", "failed"),
                )

        self.assertEqual(self.repository.get_run(run.run_id).status, "running")

    def test_repository_does_not_expose_sqlite_row(self):
        run = self.create_run()

        self.assertIsInstance(run, PipelineRunRecord)
        self.assertNotIsInstance(run, sqlite3.Row)

    def test_repository_uses_explicit_temporary_database_path(self):
        self.create_run()

        self.assertEqual(self.count_rows("pipeline_runs"), 1)
        self.assertTrue(self.database_path.exists())

    def test_tests_do_not_modify_configured_development_database(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = DEFAULT_DATABASE_FILE.stat().st_mtime if before_exists else None

        self.create_run()

        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)
        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)

    def test_source_item_repository_remains_compatible(self):
        repository = SourceItemRepository(database_path=self.database_path)
        repository.upsert_one(make_raw_item())

        self.assertEqual(repository.count(), 1)

    def test_career_signal_repository_remains_compatible(self):
        repository = CareerSignalRepository(database_path=self.database_path)
        repository.upsert_one(make_career_signal())

        self.assertEqual(repository.count(), 1)

    def test_planning_bundle_repository_remains_compatible(self):
        summary = self.create_bundle()

        self.assertEqual(summary.path_count, 1)

    def test_pipeline_integration_uses_repository_injection(self):
        pipeline_text = Path("src/pipeline.py").read_text(encoding="utf-8")
        main_text = Path("src/main.py").read_text(encoding="utf-8")

        self.assertIn("pipeline_run_repository", pipeline_text)
        self.assertIn("PipelineRunRepository", main_text)
        self.assertNotIn("import sqlite3", pipeline_text)
        self.assertNotIn("initialize_database", pipeline_text)

    def test_source_execution_tables_are_created_by_latest_schema(self):
        self.assertTrue(self.table_exists("source_executions"))
        self.assertTrue(self.table_exists("source_item_discoveries"))

    def test_filter_decision_foundation_tables_are_created(self):
        self.assertTrue(self.table_exists("filter_decisions"))
        self.assertTrue(self.table_exists("filter_executions"))
        self.assertTrue(self.table_exists("run_source_item_filter_statuses"))

    def test_existing_json_output_contract_module_is_unchanged_by_repository(self):
        pipeline_text = Path("src/pipeline.py").read_text(encoding="utf-8")

        self.assertIn("PipelineRunOutput", pipeline_text)


def make_profile():
    return UserProfile(
        profile_id="profile_1",
        name="Test User",
        background_summary="Strategy analyst interested in AI.",
    )


def make_scope():
    return SearchScope(
        scope_id="scope_1",
        name="Scope",
        locations=["Shanghai"],
        languages=["en", "zh"],
        source_types=[SourceType.SEARCH_API],
    )


def make_path(title="AI Strategy"):
    return TargetCareerPath(
        path_id="path_ai",
        title=title,
        category=CareerPathCategory.AI_STRATEGY,
        description="AI strategy roles.",
        fit_score=90,
        suggested_roles=["AI strategy analyst"],
        search_seed_terms=["AI strategy analyst"],
    )


def make_query(path=None):
    path = path or make_path()
    return SearchQuery(
        query_id=f"q_{path.path_id}",
        career_path_id=path.path_id,
        career_path_title=path.title,
        query_text="AI strategy analyst open role",
        query_type=SearchQueryType.JOB_SEARCH,
        priority=0.95,
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
        priority=query.priority,
    )


def make_bundle_write(path_title="AI Strategy"):
    profile = make_profile()
    scope = make_scope()
    path = make_path(title=path_title)
    query = make_query(path=path)
    plan = make_plan(scope=scope, query=query)
    return PlanningBundleWrite(
        user_profile=profile,
        user_preferences={"market": "US"},
        search_scope=scope,
        target_career_paths=[path],
        search_queries=[query],
        search_plans=[plan],
        generation_mode="generated",
        model_provider="deepseek",
        model_name="deepseek-v4-pro",
        prompt_version="target_career_path_prompt_v1",
        generator_config={"search_query_max_queries_per_path": 8},
    )


def make_raw_item():
    return RawItem(
        source_type=SourceType.SEARCH_API,
        title="Role",
        organization="Example",
        url="https://example.com/role",
        published_at=None,
        raw_text="Role",
    )


def make_career_signal():
    return CareerSignal(
        signal_id="signal_1",
        category=SignalCategory.JOB,
        title="Role",
        organization="Example",
        url="https://example.com/role",
        published_at=None,
        summary="Useful role.",
        source_type=SourceType.SEARCH_API,
    )


if __name__ == "__main__":
    unittest.main()
