import hashlib
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
    PipelineRunRepository,
    PipelineRunStart,
)
from src.database.repositories.planning_bundle_repository import (
    PlanningBundleRepository,
    PlanningBundleWrite,
)
from src.database.repositories.source_execution_repository import (
    RunSearchPlanCoverage,
    RunSearchPlanRegistration,
    RunSearchPlanStatusRecord,
    SearchPlanExecutionStartResult,
    SearchQueryCoverageRecord,
    SourceExecutionCompletion,
    SourceExecutionFailure,
    SourceExecutionRecord,
    SourceExecutionRepository,
    SourceExecutionRepositoryError,
    SourceExecutionStart,
    SourceItemDiscoveryRecord,
    SourceItemDiscoveryWrite,
)
from src.database.repositories.source_item_repository import SourceItemRepository
from src.database.source_identity import fingerprint_raw_item
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


STARTED_AT = "2026-07-31T00:00:00+00:00"
UPDATED_AT = "2026-07-31T00:01:00+00:00"
COMPLETED_AT = "2026-07-31T00:02:00+00:00"


class TemporarySourceExecutionDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "execution-ledger.db"
        initialize_database(database_path=self.database_path)
        self.execution_repository = SourceExecutionRepository(self.database_path)
        self.run_repository = PipelineRunRepository(self.database_path)
        self.planning_repository = PlanningBundleRepository(self.database_path)
        self.source_item_repository = SourceItemRepository(self.database_path)

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

    def execute_sql(self, sql, params=()):
        connection = open_database_connection(self.database_path)
        try:
            connection.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def create_bundle(self, suffix=""):
        return self.planning_repository.persist_planning_bundle(
            make_bundle_write(suffix),
            created_at=STARTED_AT,
        )

    def create_run(self, run_id="run_test", *, attach_bundle=True, suffix=""):
        run = self.run_repository.start_run(
            PipelineRunStart(
                pipeline_version="v1",
                phase="database_phase7a",
                execution_mode="test",
                metadata={"fixture": True},
                run_id=run_id,
            ),
            started_at=STARTED_AT,
        )
        bundle = self.create_bundle(suffix) if attach_bundle else None
        if bundle is not None:
            self.run_repository.attach_planning_bundle(
                run.run_id,
                bundle.planning_bundle_id,
            )
        return run, bundle

    def create_registered_run(self, run_id="run_test", suffix=""):
        run, bundle = self.create_run(run_id, suffix=suffix)
        registration = self.execution_repository.register_run_search_plans(
            run.run_id,
            created_at=STARTED_AT,
        )
        plans = self.planning_repository.list_plans_for_bundle(
            bundle.planning_bundle_id
        )
        return run, bundle, registration, plans

    def start_plan(self, run_id, plan_id, *, selection_order=0):
        return self.execution_repository.start_search_plan_execution(
            run_id,
            plan_id,
            SourceExecutionStart(
                source_type=SourceType.SEARCH_API.value,
                provider="brave",
                source_key="search-api",
                execution_mode="mocked",
                requested_result_limit=10,
                request_fingerprint="request-hash",
                metadata={"request": {"page": 1}},
            ),
            selection_order=selection_order,
            started_at=UPDATED_AT,
        )

    def persist_source_item(self, suffix="1"):
        raw_item = make_raw_item(suffix)
        self.source_item_repository.upsert_one(raw_item, seen_at=STARTED_AT)
        row = self.source_item_repository.get_by_fingerprint(
            fingerprint_raw_item(raw_item)
        )
        return int(row["source_item_id"])


class SourceExecutionMigrationTests(TemporarySourceExecutionDatabaseTestCase):
    def test_migrations_apply_in_order_through_007(self):
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

    def test_repeated_migration_execution_is_idempotent(self):
        self.assertEqual(initialize_database(database_path=self.database_path), [])

    def test_migrations_001_through_005_are_unchanged(self):
        expected = {
            "001_initial_schema.sql": "72E2B78C00FEF25B0ACE2F05B8F796271224A4B5F41D516840547234A95D2CD9",
            "002_source_items.sql": "01EBD4F88022D25CA7D77AB253ABAB1AF6F737B71478BD5E492DB4FAFC0978DA",
            "003_career_signals.sql": "120B41B6EA81A0B835CEE6A2616B8886BFA906C5952EA0F99C060F9AD2361D7E",
            "004_planning_bundles.sql": "AC820A9914EDDC002F2F102DE8D4D7187B9F42EF5E72CB473F7EB32E5AF14809",
            "005_pipeline_run_lifecycle.sql": "E1AEF4216EDA00F45983FB1C07FE197153CDBD460228CDC77D100B25BAD56AB7",
        }
        migration_dir = Path("src/database/sql")
        actual = {
            name: hashlib.sha256((migration_dir / name).read_bytes()).hexdigest().upper()
            for name in expected
        }
        self.assertEqual(actual, expected)

    def test_all_three_phase7_tables_exist(self):
        connection = open_database_connection(self.database_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertTrue(
            {"run_search_plan_statuses", "source_executions", "source_item_discoveries"}
            <= tables
        )

    def test_required_foreign_keys_exist(self):
        connection = open_database_connection(self.database_path)
        try:
            ledger_targets = {
                row[2]
                for row in connection.execute(
                    "PRAGMA foreign_key_list(run_search_plan_statuses)"
                )
            }
            execution_targets = {
                row[2]
                for row in connection.execute(
                    "PRAGMA foreign_key_list(source_executions)"
                )
            }
            discovery_targets = {
                row[2]
                for row in connection.execute(
                    "PRAGMA foreign_key_list(source_item_discoveries)"
                )
            }
        finally:
            connection.close()
        self.assertEqual(ledger_targets, {"pipeline_runs", "planning_search_plans"})
        self.assertEqual(execution_targets, {"pipeline_runs", "planning_search_plans"})
        self.assertEqual(discovery_targets, {"source_executions", "source_items"})

    def test_useful_indexes_exist(self):
        connection = open_database_connection(self.database_path)
        try:
            indexes = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertIn("idx_run_search_plan_statuses_run_status", indexes)
        self.assertIn("uq_source_executions_run_plan", indexes)
        self.assertIn("idx_source_item_discoveries_source_item_id", indexes)

    def test_migration_008_does_not_exist(self):
        names = [migration.path.name for migration in discover_migrations()]
        self.assertFalse(any(name.startswith("008_") for name in names))

    def test_status_checks_reject_unknown_values(self):
        run, bundle = self.create_run()
        plan_id = self.planning_repository.list_plans_for_bundle(
            bundle.planning_bundle_id
        )[0]["search_plan_row_id"]
        with self.assertRaises(sqlite3.IntegrityError):
            self.execute_sql(
                """
                INSERT INTO run_search_plan_statuses (
                    run_id, planning_search_plan_id, status, created_at, updated_at
                ) VALUES (?, ?, 'paused', ?, ?)
                """,
                (run.run_id, plan_id, STARTED_AT, STARTED_AT),
            )


class RunSearchPlanRegistrationTests(TemporarySourceExecutionDatabaseTestCase):
    def test_every_bundle_plan_is_registered(self):
        run, bundle, registration, plans = self.create_registered_run()
        statuses = self.execution_repository.list_run_search_plan_statuses(run.run_id)
        self.assertEqual(len(statuses), len(plans))
        self.assertEqual(registration.bundle_plan_count, len(plans))

    def test_registration_returns_typed_summary(self):
        _, _, registration, _ = self.create_registered_run()
        self.assertIsInstance(registration, RunSearchPlanRegistration)
        self.assertEqual(registration.inserted_count, 3)
        self.assertEqual(registration.registered_plan_count, 3)

    def test_registration_preserves_bundle_order(self):
        run, _, _, plans = self.create_registered_run()
        statuses = self.execution_repository.list_run_search_plan_statuses(run.run_id)
        self.assertEqual(
            [status.planning_search_plan_id for status in statuses],
            [plan["search_plan_row_id"] for plan in plans],
        )
        self.assertEqual([status.plan_position for status in statuses], [0, 1, 2])

    def test_registration_creates_pending_typed_rows(self):
        run, _, _, _ = self.create_registered_run()
        statuses = self.execution_repository.list_run_search_plan_statuses(run.run_id)
        self.assertTrue(all(isinstance(row, RunSearchPlanStatusRecord) for row in statuses))
        self.assertEqual({row.status for row in statuses}, {"pending"})
        self.assertTrue(all(not isinstance(row, sqlite3.Row) for row in statuses))

    def test_repeated_registration_is_idempotent(self):
        run, _, first, _ = self.create_registered_run()
        second = self.execution_repository.register_run_search_plans(run.run_id)
        self.assertEqual(first.registered_plan_count, second.registered_plan_count)
        self.assertEqual(second.inserted_count, 0)
        self.assertEqual(self.count_rows("run_search_plan_statuses"), 3)

    def test_run_without_bundle_cannot_register(self):
        run, _ = self.create_run(attach_bundle=False)
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "Planning Bundle"):
            self.execution_repository.register_run_search_plans(run.run_id)

    def test_unknown_run_cannot_register(self):
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "was not found"):
            self.execution_repository.register_run_search_plans("missing")

    def test_cross_bundle_ledger_row_is_rejected_without_partial_registration(self):
        run, bundle = self.create_run()
        other = self.create_bundle("_other")
        outside_plan_id = self.planning_repository.list_plans_for_bundle(
            other.planning_bundle_id
        )[0]["search_plan_row_id"]
        self.execute_sql(
            """
            INSERT INTO run_search_plan_statuses (
                run_id, planning_search_plan_id, status, created_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?)
            """,
            (run.run_id, outside_plan_id, STARTED_AT, STARTED_AT),
        )
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "outside"):
            self.execution_repository.register_run_search_plans(run.run_id)
        self.assertEqual(self.count_rows("run_search_plan_statuses"), 1)
        self.assertEqual(bundle.plan_count, 3)

    def test_failed_registration_rolls_back_all_new_rows(self):
        run, bundle = self.create_run()
        plan_ids = [
            row["search_plan_row_id"]
            for row in self.planning_repository.list_plans_for_bundle(
                bundle.planning_bundle_id
            )
        ]
        self.execute_sql(
            f"""
            CREATE TRIGGER fail_registration
            BEFORE INSERT ON run_search_plan_statuses
            WHEN NEW.planning_search_plan_id = {plan_ids[1]}
            BEGIN SELECT RAISE(ABORT, 'forced registration failure'); END
            """
        )
        with self.assertRaises(SourceExecutionRepositoryError) as context:
            self.execution_repository.register_run_search_plans(run.run_id)
        self.assertIsInstance(context.exception.__cause__, sqlite3.IntegrityError)
        self.assertEqual(self.count_rows("run_search_plan_statuses"), 0)

    def test_inconsistent_query_to_path_relationship_is_rejected(self):
        run, bundle = self.create_run()
        plans = self.planning_repository.list_plans_for_bundle(
            bundle.planning_bundle_id
        )
        paths = self.planning_repository.list_paths_for_bundle(
            bundle.planning_bundle_id
        )
        self.execute_sql(
            "UPDATE planning_search_plans SET career_path_row_id = ? WHERE search_plan_row_id = ?",
            (paths[1]["career_path_row_id"], plans[0]["search_plan_row_id"]),
        )
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "inconsistent"):
            self.execution_repository.register_run_search_plans(run.run_id)
        self.assertEqual(self.count_rows("run_search_plan_statuses"), 0)


class RunSearchPlanSkipTests(TemporarySourceExecutionDatabaseTestCase):
    def test_pending_plan_can_be_skipped_with_reason(self):
        run, _, _, plans = self.create_registered_run()
        rows = self.execution_repository.mark_plans_skipped(
            run.run_id,
            [plans[1]["search_plan_row_id"]],
            "max_plans_limit",
            metadata={"limit": 1},
            completed_at=COMPLETED_AT,
        )
        self.assertEqual(rows[0].status, "skipped")
        self.assertEqual(rows[0].skip_reason, "max_plans_limit")
        self.assertEqual(rows[0].metadata, {"limit": 1})

    def test_skip_reason_is_required_and_bounded(self):
        run, _, _, plans = self.create_registered_run()
        plan_id = plans[0]["search_plan_row_id"]
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "required"):
            self.execution_repository.mark_plans_skipped(run.run_id, [plan_id], " ")
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "500"):
            self.execution_repository.mark_plans_skipped(run.run_id, [plan_id], "x" * 501)

    def test_empty_skip_batch_is_a_noop(self):
        run, _, _, _ = self.create_registered_run()
        self.assertEqual(
            self.execution_repository.mark_plans_skipped(run.run_id, [], "unused"),
            [],
        )

    def test_cross_bundle_plan_cannot_be_skipped(self):
        run, _, _, _ = self.create_registered_run()
        other = self.create_bundle("_other")
        outside_plan = self.planning_repository.list_plans_for_bundle(
            other.planning_bundle_id
        )[0]["search_plan_row_id"]
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "another Planning Bundle"):
            self.execution_repository.mark_plans_skipped(
                run.run_id, [outside_plan], "max_plans_limit"
            )

    def test_unregistered_plan_cannot_be_skipped(self):
        run, bundle = self.create_run()
        plan_id = self.planning_repository.list_plans_for_bundle(
            bundle.planning_bundle_id
        )[0]["search_plan_row_id"]
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "unregistered"):
            self.execution_repository.mark_plans_skipped(
                run.run_id, [plan_id], "max_plans_limit"
            )

    def test_running_and_final_plans_cannot_be_skipped(self):
        run, _, _, plans = self.create_registered_run()
        first = plans[0]["search_plan_row_id"]
        second = plans[1]["search_plan_row_id"]
        self.start_plan(run.run_id, first)
        self.execution_repository.mark_plans_skipped(
            run.run_id, [second], "max_plans_limit"
        )
        for plan_id in (first, second):
            with self.assertRaisesRegex(SourceExecutionRepositoryError, "pending"):
                self.execution_repository.mark_plans_skipped(
                    run.run_id, [plan_id], "max_plans_limit"
                )

    def test_bulk_skip_is_atomic(self):
        run, _, _, plans = self.create_registered_run()
        first, second = [row["search_plan_row_id"] for row in plans[:2]]
        self.execute_sql(
            f"""
            CREATE TRIGGER fail_skip
            BEFORE UPDATE OF status ON run_search_plan_statuses
            WHEN NEW.status = 'skipped' AND NEW.planning_search_plan_id = {second}
            BEGIN SELECT RAISE(ABORT, 'forced skip failure'); END
            """
        )
        with self.assertRaises(SourceExecutionRepositoryError):
            self.execution_repository.mark_plans_skipped(
                run.run_id, [first, second], "max_plans_limit"
            )
        statuses = self.execution_repository.list_run_search_plan_statuses(run.run_id)
        self.assertEqual({row.status for row in statuses}, {"pending"})

    def test_max_plans_example_accounts_for_selected_and_skipped(self):
        run, _, _, plans = self.create_registered_run()
        selected = plans[0]["search_plan_row_id"]
        skipped = [row["search_plan_row_id"] for row in plans[1:]]
        self.execution_repository.mark_plans_skipped(
            run.run_id, skipped, "max_plans_limit"
        )
        started = self.start_plan(run.run_id, selected)
        self.execution_repository.complete_execution(
            started.source_execution.source_execution_id,
            SourceExecutionCompletion(returned_item_count=0),
        )
        coverage = self.execution_repository.assert_run_search_plan_accounting_complete(
            run.run_id
        )
        self.assertEqual((coverage.completed, coverage.skipped), (1, 2))


class SearchPlanExecutionLifecycleTests(TemporarySourceExecutionDatabaseTestCase):
    def test_plan_start_atomically_creates_execution_and_running_ledger(self):
        run, _, _, plans = self.create_registered_run()
        result = self.start_plan(run.run_id, plans[0]["search_plan_row_id"])
        self.assertIsInstance(result, SearchPlanExecutionStartResult)
        self.assertEqual(result.ledger_status.status, "running")
        self.assertEqual(result.source_execution.status, "running")
        self.assertEqual(result.source_execution.provider, "brave")
        self.assertEqual(result.source_execution.metadata, {"request": {"page": 1}})

    def test_plan_start_preserves_selection_order(self):
        run, _, _, plans = self.create_registered_run()
        result = self.start_plan(
            run.run_id, plans[0]["search_plan_row_id"], selection_order=4
        )
        self.assertEqual(result.ledger_status.selection_order, 4)

    def test_unknown_cross_bundle_and_unregistered_plans_fail(self):
        run, bundle = self.create_run()
        own_plan = self.planning_repository.list_plans_for_bundle(
            bundle.planning_bundle_id
        )[0]["search_plan_row_id"]
        outside = self.create_bundle("_other")
        outside_plan = self.planning_repository.list_plans_for_bundle(
            outside.planning_bundle_id
        )[0]["search_plan_row_id"]
        for plan_id in (999999, outside_plan, own_plan):
            with self.assertRaises(SourceExecutionRepositoryError):
                self.start_plan(run.run_id, plan_id)
        self.assertEqual(self.count_rows("source_executions"), 0)

    def test_unknown_and_final_runs_cannot_start_plan_execution(self):
        run, _, _, plans = self.create_registered_run()
        plan_id = plans[0]["search_plan_row_id"]
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "was not found"):
            self.start_plan("missing", plan_id)
        self.run_repository.complete_run(
            run.run_id, PipelineRunCompletion(summary={"done": True})
        )
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "running"):
            self.start_plan(run.run_id, plan_id)

    def test_skipped_running_completed_and_failed_plans_reject_restart(self):
        run, _, _, plans = self.create_registered_run()
        skipped, completed, failed = [row["search_plan_row_id"] for row in plans]
        self.execution_repository.mark_plans_skipped(
            run.run_id, [skipped], "max_plans_limit"
        )
        completed_start = self.start_plan(run.run_id, completed, selection_order=0)
        failed_start = self.start_plan(run.run_id, failed, selection_order=1)
        for plan_id in (skipped, completed, failed):
            with self.assertRaisesRegex(SourceExecutionRepositoryError, "pending"):
                self.start_plan(run.run_id, plan_id)
        self.execution_repository.complete_execution(
            completed_start.source_execution.source_execution_id,
            SourceExecutionCompletion(returned_item_count=0),
        )
        self.execution_repository.fail_execution(
            failed_start.source_execution.source_execution_id,
            SourceExecutionFailure("ProviderError", "failed"),
        )
        for plan_id in (completed, failed):
            with self.assertRaisesRegex(SourceExecutionRepositoryError, "pending"):
                self.start_plan(run.run_id, plan_id)

    def test_failed_start_rolls_back_ledger_transition(self):
        run, _, _, plans = self.create_registered_run()
        plan_id = plans[0]["search_plan_row_id"]
        self.execute_sql(
            """
            CREATE TRIGGER fail_execution_insert
            BEFORE INSERT ON source_executions
            BEGIN SELECT RAISE(ABORT, 'forced execution failure'); END
            """
        )
        with self.assertRaises(SourceExecutionRepositoryError) as context:
            self.start_plan(run.run_id, plan_id)
        self.assertIsInstance(context.exception.__cause__, sqlite3.IntegrityError)
        status = self.execution_repository.list_run_search_plan_statuses(run.run_id)[0]
        self.assertEqual(status.status, "pending")
        self.assertEqual(self.count_rows("source_executions"), 0)

    def test_search_plan_is_attempted_at_most_once_per_run(self):
        run, _, _, plans = self.create_registered_run()
        plan_id = plans[0]["search_plan_row_id"]
        self.start_plan(run.run_id, plan_id)
        with self.assertRaises(SourceExecutionRepositoryError):
            self.start_plan(run.run_id, plan_id)
        self.assertEqual(self.count_rows("source_executions"), 1)

    def test_completion_updates_execution_and_ledger(self):
        run, _, _, plans = self.create_registered_run()
        started = self.start_plan(run.run_id, plans[0]["search_plan_row_id"])
        completed = self.execution_repository.complete_execution(
            started.source_execution.source_execution_id,
            SourceExecutionCompletion(
                returned_item_count=0,
                metadata={"response": "empty"},
            ),
            completed_at=COMPLETED_AT,
        )
        ledger = self.execution_repository.list_run_search_plan_statuses(run.run_id)[0]
        self.assertEqual((completed.status, ledger.status), ("completed", "completed"))
        self.assertEqual(completed.discovered_item_count, 0)
        self.assertEqual(completed.metadata["response"], "empty")

    def test_failure_updates_execution_and_ledger(self):
        run, _, _, plans = self.create_registered_run()
        started = self.start_plan(run.run_id, plans[0]["search_plan_row_id"])
        failed = self.execution_repository.fail_execution(
            started.source_execution.source_execution_id,
            SourceExecutionFailure("TimeoutError", "provider timed out"),
            failed_at=COMPLETED_AT,
        )
        ledger = self.execution_repository.list_run_search_plan_statuses(run.run_id)[0]
        self.assertEqual((failed.status, ledger.status), ("failed", "failed"))
        self.assertEqual(failed.error_type, "TimeoutError")
        self.assertEqual(failed.error_message, "provider timed out")

    def test_repeated_final_transitions_are_rejected(self):
        run, _, _, plans = self.create_registered_run()
        started = self.start_plan(run.run_id, plans[0]["search_plan_row_id"])
        execution_id = started.source_execution.source_execution_id
        self.execution_repository.complete_execution(
            execution_id, SourceExecutionCompletion(returned_item_count=0)
        )
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "running"):
            self.execution_repository.complete_execution(
                execution_id, SourceExecutionCompletion(returned_item_count=0)
            )
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "running"):
            self.execution_repository.fail_execution(
                execution_id, SourceExecutionFailure("Error", "late failure")
            )

    def test_failed_completion_rolls_both_records_back_to_running(self):
        run, _, _, plans = self.create_registered_run()
        started = self.start_plan(run.run_id, plans[0]["search_plan_row_id"])
        self.execute_sql(
            """
            CREATE TRIGGER fail_ledger_completion
            BEFORE UPDATE OF status ON run_search_plan_statuses
            WHEN NEW.status = 'completed'
            BEGIN SELECT RAISE(ABORT, 'forced completion failure'); END
            """
        )
        with self.assertRaises(SourceExecutionRepositoryError):
            self.execution_repository.complete_execution(
                started.source_execution.source_execution_id,
                SourceExecutionCompletion(returned_item_count=0),
            )
        execution = self.execution_repository.get_source_execution(
            started.source_execution.source_execution_id
        )
        ledger = self.execution_repository.list_run_search_plan_statuses(run.run_id)[0]
        self.assertEqual((execution.status, ledger.status), ("running", "running"))

    def test_failed_failure_transition_rolls_both_records_back_to_running(self):
        run, _, _, plans = self.create_registered_run()
        started = self.start_plan(run.run_id, plans[0]["search_plan_row_id"])
        self.execute_sql(
            """
            CREATE TRIGGER fail_ledger_failure
            BEFORE UPDATE OF status ON run_search_plan_statuses
            WHEN NEW.status = 'failed'
            BEGIN SELECT RAISE(ABORT, 'forced failure transition'); END
            """
        )
        with self.assertRaises(SourceExecutionRepositoryError):
            self.execution_repository.fail_execution(
                started.source_execution.source_execution_id,
                SourceExecutionFailure("ProviderError", "failed"),
            )
        execution = self.execution_repository.get_source_execution(
            started.source_execution.source_execution_id
        )
        ledger = self.execution_repository.list_run_search_plan_statuses(run.run_id)[0]
        self.assertEqual((execution.status, ledger.status), ("running", "running"))


class IndependentSourceExecutionTests(TemporarySourceExecutionDatabaseTestCase):
    def test_rss_execution_works_without_search_plan(self):
        run, _ = self.create_run(attach_bundle=False)
        execution = self.execution_repository.start_source_execution(
            run.run_id,
            SourceExecutionStart(
                source_type=SourceType.RSS.value,
                source_name="Feed",
                source_locator="https://example.com/feed.xml",
            ),
        )
        self.assertIsNone(execution.planning_search_plan_id)
        self.assertEqual(execution.source_type, SourceType.RSS.value)

    def test_selected_website_execution_works_without_search_plan(self):
        run, _ = self.create_run(attach_bundle=False)
        execution = self.execution_repository.start_source_execution(
            run.run_id,
            SourceExecutionStart(
                source_type=SourceType.SELECTED_WEBSITE.value,
                source_name="Example",
                source_locator="https://example.com",
            ),
        )
        self.assertIsNone(execution.planning_search_plan_id)

    def test_independent_sources_create_no_fake_ledger_rows(self):
        run, _ = self.create_run(attach_bundle=False)
        for source_type in (SourceType.RSS.value, SourceType.SELECTED_WEBSITE.value):
            self.execution_repository.start_source_execution(
                run.run_id, SourceExecutionStart(source_type=source_type)
            )
        self.assertEqual(self.count_rows("run_search_plan_statuses"), 0)
        self.assertEqual(self.count_rows("planning_search_plans"), 0)

    def test_independent_execution_completion_updates_only_execution(self):
        run, _ = self.create_run(attach_bundle=False)
        started = self.execution_repository.start_source_execution(
            run.run_id, SourceExecutionStart(source_type=SourceType.RSS.value)
        )
        completed = self.execution_repository.complete_execution(
            started.source_execution_id,
            SourceExecutionCompletion(returned_item_count=0),
        )
        self.assertEqual(completed.status, "completed")
        self.assertEqual(self.count_rows("run_search_plan_statuses"), 0)

    def test_independent_execution_failure_updates_only_execution(self):
        run, _ = self.create_run(attach_bundle=False)
        started = self.execution_repository.start_source_execution(
            run.run_id,
            SourceExecutionStart(source_type=SourceType.SELECTED_WEBSITE.value),
        )
        failed = self.execution_repository.fail_execution(
            started.source_execution_id,
            SourceExecutionFailure("HTTPError", "status 500"),
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(self.count_rows("run_search_plan_statuses"), 0)

    def test_final_run_cannot_start_independent_execution(self):
        run, _ = self.create_run(attach_bundle=False)
        self.run_repository.fail_run(
            run.run_id,
            PipelineRunFailure("source_execution", "RuntimeError", "failed"),
        )
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "running"):
            self.execution_repository.start_source_execution(
                run.run_id, SourceExecutionStart(source_type=SourceType.RSS.value)
            )


class SourceItemDiscoveryTests(TemporarySourceExecutionDatabaseTestCase):
    def start_rss_execution(self, run_id):
        return self.execution_repository.start_source_execution(
            run_id, SourceExecutionStart(source_type=SourceType.RSS.value)
        )

    def test_one_execution_can_discover_many_source_items(self):
        run, _ = self.create_run(attach_bundle=False)
        execution = self.start_rss_execution(run.run_id)
        item_ids = [self.persist_source_item(str(index)) for index in range(3)]
        discoveries = self.execution_repository.record_discoveries(
            execution.source_execution_id,
            [
                SourceItemDiscoveryWrite(item_id, result_position=index)
                for index, item_id in enumerate(item_ids)
            ],
        )
        self.assertEqual(len(discoveries), 3)
        self.assertTrue(all(isinstance(row, SourceItemDiscoveryRecord) for row in discoveries))

    def test_result_positions_and_metadata_round_trip(self):
        run, _ = self.create_run(attach_bundle=False)
        execution = self.start_rss_execution(run.run_id)
        item_id = self.persist_source_item()
        discovery = self.execution_repository.record_discoveries(
            execution.source_execution_id,
            [
                SourceItemDiscoveryWrite(
                    item_id,
                    result_position=7,
                    metadata={"ranker": "source"},
                    discovered_at=UPDATED_AT,
                )
            ],
        )[0]
        self.assertEqual(discovery.result_position, 7)
        self.assertEqual(discovery.metadata, {"ranker": "source"})
        self.assertEqual(discovery.discovered_at, UPDATED_AT)

    def test_one_source_item_can_be_discovered_by_many_executions(self):
        run, _ = self.create_run(attach_bundle=False)
        item_id = self.persist_source_item()
        first = self.start_rss_execution(run.run_id)
        second = self.execution_repository.start_source_execution(
            run.run_id,
            SourceExecutionStart(source_type=SourceType.SELECTED_WEBSITE.value),
        )
        for execution in (first, second):
            self.execution_repository.record_discoveries(
                execution.source_execution_id, [SourceItemDiscoveryWrite(item_id)]
            )
        self.assertEqual(self.count_rows("source_items"), 1)
        self.assertEqual(self.count_rows("source_item_discoveries"), 2)

    def test_one_source_item_can_be_rediscovered_in_another_run(self):
        first_run, _ = self.create_run("run_first", attach_bundle=False)
        second_run, _ = self.create_run("run_second", attach_bundle=False)
        item_id = self.persist_source_item()
        for run in (first_run, second_run):
            execution = self.start_rss_execution(run.run_id)
            self.execution_repository.record_discoveries(
                execution.source_execution_id, [SourceItemDiscoveryWrite(item_id)]
            )
        self.assertEqual(self.count_rows("source_items"), 1)
        self.assertEqual(self.count_rows("source_item_discoveries"), 2)

    def test_invalid_discovery_batch_rolls_back_whole_batch(self):
        run, _ = self.create_run(attach_bundle=False)
        execution = self.start_rss_execution(run.run_id)
        valid_id = self.persist_source_item()
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "unknown"):
            self.execution_repository.record_discoveries(
                execution.source_execution_id,
                [SourceItemDiscoveryWrite(valid_id), SourceItemDiscoveryWrite(999999)],
            )
        self.assertEqual(self.count_rows("source_item_discoveries"), 0)

    def test_duplicate_discovery_link_is_idempotent(self):
        run, _ = self.create_run(attach_bundle=False)
        execution = self.start_rss_execution(run.run_id)
        item_id = self.persist_source_item()
        write = SourceItemDiscoveryWrite(item_id, result_position=0)
        self.execution_repository.record_discoveries(execution.source_execution_id, [write])
        self.execution_repository.record_discoveries(execution.source_execution_id, [write])
        self.assertEqual(self.count_rows("source_item_discoveries"), 1)

    def test_duplicate_ids_in_one_batch_are_rejected(self):
        run, _ = self.create_run(attach_bundle=False)
        execution = self.start_rss_execution(run.run_id)
        item_id = self.persist_source_item()
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "duplicate"):
            self.execution_repository.record_discoveries(
                execution.source_execution_id,
                [SourceItemDiscoveryWrite(item_id), SourceItemDiscoveryWrite(item_id)],
            )

    def test_final_state_execution_rejects_new_discoveries(self):
        run, _ = self.create_run(attach_bundle=False)
        execution = self.start_rss_execution(run.run_id)
        self.execution_repository.complete_execution(
            execution.source_execution_id,
            SourceExecutionCompletion(returned_item_count=0),
        )
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "running"):
            self.execution_repository.record_discoveries(
                execution.source_execution_id,
                [SourceItemDiscoveryWrite(self.persist_source_item())],
            )

    def test_discoveries_are_preserved_on_completion_and_failure(self):
        run, _ = self.create_run(attach_bundle=False)
        item_id = self.persist_source_item()
        first = self.start_rss_execution(run.run_id)
        second = self.start_rss_execution(run.run_id)
        for execution in (first, second):
            self.execution_repository.record_discoveries(
                execution.source_execution_id, [SourceItemDiscoveryWrite(item_id)]
            )
        self.execution_repository.complete_execution(
            first.source_execution_id, SourceExecutionCompletion(returned_item_count=1)
        )
        self.execution_repository.fail_execution(
            second.source_execution_id,
            SourceExecutionFailure("ParseError", "bad feed", returned_item_count=1),
        )
        self.assertEqual(self.count_rows("source_item_discoveries"), 2)

    def test_declared_discovery_count_must_match_committed_links(self):
        run, _ = self.create_run(attach_bundle=False)
        execution = self.start_rss_execution(run.run_id)
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "does not match"):
            self.execution_repository.complete_execution(
                execution.source_execution_id,
                SourceExecutionCompletion(returned_item_count=1, discovered_item_count=1),
            )
        self.assertEqual(
            self.execution_repository.get_source_execution(execution.source_execution_id).status,
            "running",
        )


class SearchPlanCoverageTests(TemporarySourceExecutionDatabaseTestCase):
    def test_coverage_reports_all_status_counts(self):
        run, _, _, plans = self.create_registered_run()
        completed = self.start_plan(run.run_id, plans[0]["search_plan_row_id"])
        failed = self.start_plan(run.run_id, plans[1]["search_plan_row_id"])
        self.execution_repository.mark_plans_skipped(
            run.run_id, [plans[2]["search_plan_row_id"]], "max_plans_limit"
        )
        self.execution_repository.complete_execution(
            completed.source_execution.source_execution_id,
            SourceExecutionCompletion(returned_item_count=0),
        )
        self.execution_repository.fail_execution(
            failed.source_execution.source_execution_id,
            SourceExecutionFailure("ProviderError", "failed"),
        )
        coverage = self.execution_repository.get_run_search_plan_coverage(run.run_id)
        self.assertIsInstance(coverage, RunSearchPlanCoverage)
        self.assertEqual(
            (coverage.completed, coverage.failed, coverage.skipped), (1, 1, 1)
        )

    def test_accounting_check_detects_missing_ledger_row(self):
        run, _, _, plans = self.create_registered_run()
        self.execute_sql(
            "DELETE FROM run_search_plan_statuses WHERE run_id = ? AND planning_search_plan_id = ?",
            (run.run_id, plans[0]["search_plan_row_id"]),
        )
        coverage = self.execution_repository.get_run_search_plan_coverage(run.run_id)
        self.assertEqual(coverage.missing_unregistered, 1)
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "incomplete"):
            self.execution_repository.assert_run_search_plan_accounting_complete(run.run_id)

    def test_accounting_check_detects_pending_and_running(self):
        run, _, _, plans = self.create_registered_run()
        self.start_plan(run.run_id, plans[0]["search_plan_row_id"])
        coverage = self.execution_repository.get_run_search_plan_coverage(run.run_id)
        self.assertEqual((coverage.pending, coverage.running), (2, 1))
        with self.assertRaisesRegex(SourceExecutionRepositoryError, "pending=2, running=1"):
            self.execution_repository.assert_run_search_plan_accounting_complete(run.run_id)

    def test_accounting_check_detects_cross_bundle_ledger_row(self):
        run, _, _, _ = self.create_registered_run()
        other = self.create_bundle("_other")
        outside_plan = self.planning_repository.list_plans_for_bundle(
            other.planning_bundle_id
        )[0]["search_plan_row_id"]
        self.execute_sql(
            """
            INSERT INTO run_search_plan_statuses (
                run_id, planning_search_plan_id, status, created_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?)
            """,
            (run.run_id, outside_plan, STARTED_AT, STARTED_AT),
        )
        coverage = self.execution_repository.get_run_search_plan_coverage(run.run_id)
        self.assertEqual(coverage.unexpected_registered, 1)
        with self.assertRaises(SourceExecutionRepositoryError):
            self.execution_repository.assert_run_search_plan_accounting_complete(run.run_id)

    def test_accounting_succeeds_only_with_final_statuses(self):
        run, _, _, plans = self.create_registered_run()
        for index, plan in enumerate(plans[:2]):
            started = self.start_plan(run.run_id, plan["search_plan_row_id"], selection_order=index)
            if index == 0:
                self.execution_repository.complete_execution(
                    started.source_execution.source_execution_id,
                    SourceExecutionCompletion(returned_item_count=0),
                )
            else:
                self.execution_repository.fail_execution(
                    started.source_execution.source_execution_id,
                    SourceExecutionFailure("Error", "failed"),
                )
        self.execution_repository.mark_plans_skipped(
            run.run_id, [plans[2]["search_plan_row_id"]], "max_plans_limit"
        )
        coverage = self.execution_repository.assert_run_search_plan_accounting_complete(
            run.run_id
        )
        self.assertEqual(coverage.registered_plans, coverage.total_bundle_plans)

    def test_unexecuted_list_contains_pending_and_skipped_only(self):
        run, _, _, plans = self.create_registered_run()
        started = self.start_plan(run.run_id, plans[0]["search_plan_row_id"])
        self.execution_repository.complete_execution(
            started.source_execution.source_execution_id,
            SourceExecutionCompletion(returned_item_count=0),
        )
        self.execution_repository.mark_plans_skipped(
            run.run_id, [plans[1]["search_plan_row_id"]], "max_plans_limit"
        )
        rows = self.execution_repository.list_unexecuted_search_plans(run.run_id)
        self.assertEqual({row.status for row in rows}, {"pending", "skipped"})

    def test_unrelated_committed_run_remains_untouched(self):
        first, _, _, first_plans = self.create_registered_run("run_first")
        second, _, _, _ = self.create_registered_run("run_second")
        self.execution_repository.mark_plans_skipped(
            first.run_id,
            [row["search_plan_row_id"] for row in first_plans],
            "max_plans_limit",
        )
        second_statuses = self.execution_repository.list_run_search_plan_statuses(
            second.run_id
        )
        self.assertEqual({row.status for row in second_statuses}, {"pending"})


class SearchQueryCoverageTests(TemporarySourceExecutionDatabaseTestCase):
    def test_query_coverage_is_typed_and_ordered(self):
        run, bundle, _, _ = self.create_registered_run()
        coverage = self.execution_repository.get_run_search_query_coverage(run.run_id)
        queries = self.planning_repository.list_queries_for_bundle(
            bundle.planning_bundle_id
        )
        self.assertTrue(all(isinstance(row, SearchQueryCoverageRecord) for row in coverage))
        self.assertEqual([row.search_query_row_id for row in coverage], [q["search_query_row_id"] for q in queries])

    def test_query_with_completed_plans_is_reported(self):
        run, _, _, plans = self.create_registered_run()
        started = self.start_plan(run.run_id, plans[0]["search_plan_row_id"])
        self.execution_repository.complete_execution(
            started.source_execution.source_execution_id,
            SourceExecutionCompletion(returned_item_count=0),
        )
        coverage = self.execution_repository.get_run_search_query_coverage(run.run_id)
        self.assertEqual(coverage[0].completed, 1)

    def test_query_with_only_skipped_plans_is_reported(self):
        run, _, _, plans = self.create_registered_run()
        first_query_plan_ids = [
            row["search_plan_row_id"]
            for row in plans
            if row["search_query_row_id"] == plans[0]["search_query_row_id"]
        ]
        self.execution_repository.mark_plans_skipped(
            run.run_id, first_query_plan_ids, "max_plans_limit"
        )
        coverage = self.execution_repository.get_run_search_query_coverage(run.run_id)
        self.assertEqual(coverage[0].skipped, 2)
        self.assertTrue(coverage[0].no_plans_entered_execution)

    def test_query_whose_plans_never_entered_execution_is_visible(self):
        run, _, _, _ = self.create_registered_run()
        coverage = self.execution_repository.get_run_search_query_coverage(run.run_id)
        self.assertTrue(coverage[0].no_plans_entered_execution)
        self.assertEqual(coverage[0].pending, 2)

    def test_query_with_no_generated_plans_is_visible(self):
        run, _, _, _ = self.create_registered_run()
        coverage = self.execution_repository.get_run_search_query_coverage(run.run_id)
        empty = next(row for row in coverage if row.total_search_plans == 0)
        self.assertTrue(empty.no_search_plans_generated)
        self.assertFalse(empty.no_plans_entered_execution)

    def test_missing_plan_registration_is_reflected_per_query(self):
        run, _, _, plans = self.create_registered_run()
        self.execute_sql(
            "DELETE FROM run_search_plan_statuses WHERE run_id = ? AND planning_search_plan_id = ?",
            (run.run_id, plans[0]["search_plan_row_id"]),
        )
        coverage = self.execution_repository.get_run_search_query_coverage(run.run_id)
        self.assertEqual(coverage[0].missing_unregistered, 1)

    def test_no_duplicate_query_status_table_exists(self):
        connection = open_database_connection(self.database_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertNotIn("run_search_query_statuses", tables)


class SourceExecutionCompatibilityScopeTests(TemporarySourceExecutionDatabaseTestCase):
    def test_public_exports_resolve_to_typed_repository(self):
        from src.database import SourceExecutionRepository as TopLevelRepository
        from src.database.repositories import (
            SourceExecutionRepository as RepositoryPackageExport,
        )
        self.assertIs(TopLevelRepository, SourceExecutionRepository)
        self.assertIs(RepositoryPackageExport, SourceExecutionRepository)

    def test_pipeline_run_repository_remains_compatible(self):
        run, bundle = self.create_run()
        fetched = self.run_repository.get_run(run.run_id)
        self.assertEqual(fetched.planning_bundle_id, bundle.planning_bundle_id)

    def test_planning_bundle_repository_remains_compatible(self):
        bundle = self.create_bundle()
        self.assertEqual((bundle.path_count, bundle.query_count, bundle.plan_count), (2, 3, 3))

    def test_source_item_repository_remains_owner_of_content(self):
        item_id = self.persist_source_item()
        self.assertGreater(item_id, 0)
        self.assertEqual(self.source_item_repository.count(), 1)

    def test_career_signal_repository_remains_compatible(self):
        repository = CareerSignalRepository(self.database_path)
        repository.upsert_one(make_career_signal())
        self.assertEqual(repository.count(), 1)

    def test_pipeline_and_main_integrate_phase7_without_raw_sql(self):
        pipeline_text = Path("src/pipeline.py").read_text(encoding="utf-8")
        main_text = Path("src/main.py").read_text(encoding="utf-8")
        self.assertIn("SourceExecutionRepository", pipeline_text)
        self.assertIn("SourceExecutionRepository", main_text)
        self.assertIn("register_run_search_plans", pipeline_text)
        self.assertNotIn("import sqlite3", pipeline_text)

    def test_source_items_and_career_signals_have_no_direct_run_id(self):
        connection = open_database_connection(self.database_path)
        try:
            for table in ("source_items", "career_signals"):
                columns = {
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                self.assertNotIn("run_id", columns)
        finally:
            connection.close()

    def test_filter_decision_foundation_tables_exist(self):
        connection = open_database_connection(self.database_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()
        self.assertTrue(
            {
                "filter_decisions",
                "filter_executions",
                "run_source_item_filter_statuses",
            }
            <= tables
        )

    def test_repository_returns_records_not_sqlite_rows(self):
        run, _, _, plans = self.create_registered_run()
        started = self.start_plan(run.run_id, plans[0]["search_plan_row_id"])
        fetched = self.execution_repository.get_source_execution(
            started.source_execution.source_execution_id
        )
        self.assertIsInstance(fetched, SourceExecutionRecord)
        self.assertNotIsInstance(fetched, sqlite3.Row)

    def test_no_live_network_or_llm_call_occurs(self):
        with patch("requests.get") as request_get, patch(
            "src.career_path_generator.TargetCareerPathClient"
        ) as llm_client:
            self.create_registered_run()
        request_get.assert_not_called()
        llm_client.assert_not_called()

    def test_tests_do_not_touch_configured_development_database(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = DEFAULT_DATABASE_FILE.stat().st_mtime if before_exists else None
        self.create_registered_run()
        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)
        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)


def make_bundle_write(suffix=""):
    scope_id = f"scope{suffix or '_primary'}"
    profile = UserProfile(
        profile_id="profile_1",
        name="Test User",
        background_summary="Strategy analyst interested in AI.",
    )
    scope = SearchScope(
        scope_id=scope_id,
        name="Execution Scope",
        locations=["Shanghai"],
        languages=["en"],
        source_types=[SourceType.SEARCH_API],
    )
    first_path = TargetCareerPath(
        path_id=f"path_strategy{suffix}",
        title="AI Strategy",
        category=CareerPathCategory.AI_STRATEGY,
        description="AI strategy roles.",
        fit_score=90,
        suggested_roles=["AI strategist"],
        search_seed_terms=["AI strategist"],
    )
    second_path = TargetCareerPath(
        path_id=f"path_product{suffix}",
        title="AI Product",
        category=CareerPathCategory.MARKET_RESEARCH,
        description="AI product roles.",
        fit_score=85,
        suggested_roles=["AI product manager"],
        search_seed_terms=["AI product manager"],
    )
    first_query = make_query(first_path, f"query_strategy{suffix}", "AI strategist")
    second_query = make_query(second_path, f"query_product{suffix}", "AI product manager")
    empty_query = make_query(second_path, f"query_empty{suffix}", "AI product research")
    plans = [
        make_plan(scope, first_query, f"plan_strategy_primary{suffix}", 0.95),
        make_plan(scope, first_query, f"plan_strategy_secondary{suffix}", 0.85),
        make_plan(scope, second_query, f"plan_product{suffix}", 0.75),
    ]
    return PlanningBundleWrite(
        user_profile=profile,
        user_preferences={"market": f"global{suffix}"},
        search_scope=scope,
        target_career_paths=[first_path, second_path],
        search_queries=[first_query, second_query, empty_query],
        search_plans=plans,
        generation_mode="mocked",
        model_provider="test",
        model_name="test-model",
        prompt_version="test-prompt-v1",
        generator_config={"max_queries_per_path": 3},
    )


def make_query(path, query_id, query_text):
    return SearchQuery(
        query_id=query_id,
        career_path_id=path.path_id,
        career_path_title=path.title,
        query_text=query_text,
        query_type=SearchQueryType.JOB_SEARCH,
        priority=0.9,
    )


def make_plan(scope, query, plan_id, priority):
    return SearchPlan(
        plan_id=plan_id,
        query_id=query.query_id,
        query_text=query.query_text,
        query_type=query.query_type,
        career_path_id=query.career_path_id,
        career_path_title=query.career_path_title,
        scope_id=scope.scope_id,
        source_types=[SourceType.SEARCH_API],
        locations=scope.locations,
        languages=scope.languages,
        max_results=10,
        priority=priority,
    )


def make_raw_item(suffix="1"):
    return RawItem(
        source_type=SourceType.SEARCH_API,
        title=f"Role {suffix}",
        organization="Example",
        url=f"https://example.com/role/{suffix}",
        published_at=None,
        raw_text=f"Role body {suffix}",
        metadata={"provider": "mock"},
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
