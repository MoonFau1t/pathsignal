import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.ai_filter import AIFilterClient, execute_ai_filter
from src.config import DEFAULT_DATABASE_FILE
from src.database.connection import open_database_connection
from src.database.migrations import discover_migrations, initialize_database
from src.database.repositories.career_signal_repository import CareerSignalRepository
from src.database.repositories.filter_decision_repository import (
    FilterCoverage,
    FilterDecisionInput,
    FilterDecisionRecord,
    FilterDecisionRepository,
    FilterDecisionRepositoryError,
    FilterExecutionRecord,
    FilterExecutionStart,
    FilterExecutionStartResult,
    RunFilterRegistration,
    RunSourceItemFilterStatusRecord,
)
from src.database.repositories.pipeline_run_repository import (
    PipelineRunFailure,
    PipelineRunRepository,
    PipelineRunStart,
)
from src.database.repositories.source_execution_repository import (
    SourceExecutionCompletion,
    SourceExecutionRepository,
    SourceExecutionStart,
    SourceItemDiscoveryWrite,
)
from src.database.repositories.source_item_repository import SourceItemRepository
from src.database.source_identity import fingerprint_raw_item
from src.models import (
    AIFilterResult,
    CareerPathCategory,
    RawItem,
    SignalCategory,
    SourceType,
    TargetCareerPath,
    UserProfile,
)
from src.normalizer import normalize_raw_items_to_career_signals


STARTED_AT = "2026-08-02T00:00:00+00:00"
DISCOVERED_AT = "2026-08-02T00:01:00+00:00"
FILTER_STARTED_AT = "2026-08-02T00:02:00+00:00"
FILTER_COMPLETED_AT = "2026-08-02T00:03:00+00:00"


class TemporaryFilterDecisionDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.database_path = Path(self.temp_dir.name) / "filter-decisions.db"
        initialize_database(self.database_path)
        self.repository = FilterDecisionRepository(self.database_path)
        self.run_repository = PipelineRunRepository(self.database_path)
        self.execution_repository = SourceExecutionRepository(self.database_path)
        self.source_item_repository = SourceItemRepository(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_run(self, run_id="run_filter"):
        return self.run_repository.start_run(
            PipelineRunStart(
                run_id=run_id,
                pipeline_version="v1",
                phase="database_phase8a",
                execution_mode="test",
            ),
            started_at=STARTED_AT,
        )

    def create_source_item(self, suffix="1"):
        raw_item = make_raw_item(suffix)
        self.source_item_repository.upsert_one(raw_item, seen_at=STARTED_AT)
        row = self.source_item_repository.get_by_fingerprint(
            fingerprint_raw_item(raw_item)
        )
        return int(row["source_item_id"])

    def discover_items(self, run_id, source_item_ids, source_key="feed-1"):
        execution = self.execution_repository.start_source_execution(
            run_id,
            SourceExecutionStart(
                source_type=SourceType.RSS.value,
                provider="rss",
                source_key=source_key,
                execution_mode="mocked",
            ),
            started_at=STARTED_AT,
        )
        self.execution_repository.record_discoveries(
            execution.source_execution_id,
            [
                SourceItemDiscoveryWrite(
                    source_item_id=source_item_id,
                    result_position=position,
                    discovered_at=DISCOVERED_AT,
                )
                for position, source_item_id in enumerate(source_item_ids)
            ],
        )
        self.execution_repository.complete_execution(
            execution.source_execution_id,
            SourceExecutionCompletion(
                returned_item_count=len(source_item_ids),
                discovered_item_count=len(source_item_ids),
            ),
            completed_at=DISCOVERED_AT,
        )
        return execution

    def create_discovered_run(
        self,
        item_count=3,
        run_id="run_filter",
        duplicate_first_discovery=False,
    ):
        run = self.create_run(run_id)
        source_item_ids = [
            self.create_source_item(str(index))
            for index in range(1, item_count + 1)
        ]
        if source_item_ids:
            self.discover_items(run.run_id, source_item_ids)
            if duplicate_first_discovery:
                self.discover_items(
                    run.run_id,
                    [source_item_ids[0]],
                    source_key="feed-2",
                )
        return run, source_item_ids

    def register_discovered_run(self, **kwargs):
        run, source_item_ids = self.create_discovered_run(**kwargs)
        registration = self.repository.register_run_filter_items(
            run.run_id,
            created_at=DISCOVERED_AT,
        )
        return run, source_item_ids, registration

    def start_filter(self, run_id, source_item_ids, **overrides):
        defaults = {
            "execution_mode": "live",
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "prompt_version": None,
            "prompt_fingerprint": "prompt-hash",
            "input_fingerprint": "input-hash",
            "metadata": {"call_shape": "per_item"},
        }
        defaults.update(overrides)
        return self.repository.start_filter_execution(
            run_id,
            source_item_ids,
            FilterExecutionStart(**defaults),
            started_at=FILTER_STARTED_AT,
        )

    def complete_filter(self, execution_id, decisions):
        return self.repository.complete_filter_execution(
            execution_id,
            decisions,
            completed_at=FILTER_COMPLETED_AT,
        )

    def count_rows(self, table):
        connection = open_database_connection(self.database_path)
        try:
            return int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def rows(self, sql, parameters=()):
        connection = open_database_connection(self.database_path)
        try:
            return [
                dict(row)
                for row in connection.execute(sql, parameters).fetchall()
            ]
        finally:
            connection.close()

    def execute_sql(self, sql, parameters=(), *, foreign_keys=True):
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}"
            )
            connection.execute(sql, parameters)
            connection.commit()
        finally:
            connection.close()


class FilterDecisionMigrationTests(TemporaryFilterDecisionDatabaseTestCase):
    def test_migration_007_is_discovered(self):
        migrations = discover_migrations()
        self.assertEqual(migrations[-1].version, "007")
        self.assertEqual(migrations[-1].name, "filter_decision_provenance")

    def test_migrations_apply_in_order_through_007(self):
        versions = [
            row["version"]
            for row in self.rows(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        self.assertEqual(
            versions,
            ["001", "002", "003", "004", "005", "006", "007"],
        )

    def test_repeated_migrations_are_idempotent(self):
        self.assertEqual(initialize_database(self.database_path), [])

    def test_migrations_001_through_006_are_unchanged(self):
        expected = {
            "001_initial_schema.sql": "72E2B78C00FEF25B0ACE2F05B8F796271224A4B5F41D516840547234A95D2CD9",
            "002_source_items.sql": "01EBD4F88022D25CA7D77AB253ABAB1AF6F737B71478BD5E492DB4FAFC0978DA",
            "003_career_signals.sql": "120B41B6EA81A0B835CEE6A2616B8886BFA906C5952EA0F99C060F9AD2361D7E",
            "004_planning_bundles.sql": "AC820A9914EDDC002F2F102DE8D4D7187B9F42EF5E72CB473F7EB32E5AF14809",
            "005_pipeline_run_lifecycle.sql": "E1AEF4216EDA00F45983FB1C07FE197153CDBD460228CDC77D100B25BAD56AB7",
            "006_execution_ledger_provenance.sql": "1C42C25B63D2B7803629B5D45D78BEEF837B1F64294D365B908AF1C691B4E6EF",
        }
        migration_dir = Path("src/database/sql")
        actual = {
            filename: hashlib.sha256(
                (migration_dir / filename).read_bytes()
            ).hexdigest().upper()
            for filename in expected
        }
        self.assertEqual(actual, expected)

    def test_all_phase8_tables_exist(self):
        tables = {
            row["name"]
            for row in self.rows(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {
                "run_source_item_filter_statuses",
                "filter_executions",
                "filter_decisions",
            }
            <= tables
        )

    def test_required_foreign_keys_exist(self):
        ledger_targets = {
            row["table"]
            for row in self.rows(
                "PRAGMA foreign_key_list(run_source_item_filter_statuses)"
            )
        }
        execution_targets = {
            row["table"]
            for row in self.rows("PRAGMA foreign_key_list(filter_executions)")
        }
        decision_targets = {
            row["table"]
            for row in self.rows("PRAGMA foreign_key_list(filter_decisions)")
        }
        self.assertEqual(
            ledger_targets,
            {"pipeline_runs", "source_items", "filter_executions"},
        )
        self.assertEqual(execution_targets, {"pipeline_runs"})
        self.assertEqual(
            decision_targets,
            {
                "filter_executions",
                "pipeline_runs",
                "source_items",
                "run_source_item_filter_statuses",
            },
        )

    def test_useful_indexes_exist(self):
        indexes = {
            row["name"]
            for row in self.rows(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        self.assertIn("idx_run_filter_statuses_run_status", indexes)
        self.assertIn("idx_filter_executions_run_status", indexes)
        self.assertIn("idx_filter_decisions_source_item", indexes)
        self.assertIn("uq_run_filter_status_execution_membership", indexes)

    def test_migration_008_does_not_exist(self):
        names = [migration.path.name for migration in discover_migrations()]
        self.assertFalse(any(name.startswith("008_") for name in names))

    def test_existing_tables_remain_intact(self):
        tables = {
            row["name"]
            for row in self.rows(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {
                "pipeline_runs",
                "planning_bundles",
                "source_executions",
                "source_item_discoveries",
                "source_items",
                "career_signals",
            }
            <= tables
        )

    def test_status_checks_reject_unknown_values(self):
        run = self.create_run()
        source_item_id = self.create_source_item()
        with self.assertRaises(sqlite3.IntegrityError):
            self.execute_sql(
                """
                INSERT INTO run_source_item_filter_statuses (
                    run_id, source_item_id, status, created_at, updated_at
                ) VALUES (?, ?, 'ignored', ?, ?)
                """,
                (run.run_id, source_item_id, STARTED_AT, STARTED_AT),
            )

    def test_foreign_keys_are_enabled_and_valid(self):
        connection = open_database_connection(self.database_path)
        try:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()


class RunFilterRegistrationTests(TemporaryFilterDecisionDatabaseTestCase):
    def test_unique_discovered_source_items_are_registered(self):
        run, source_item_ids, registration = self.register_discovered_run()
        rows = self.repository.list_run_filter_statuses(run.run_id)
        self.assertEqual([row.source_item_id for row in rows], source_item_ids)
        self.assertEqual(registration.registered_filter_item_count, 3)

    def test_registration_returns_typed_summary_and_rows(self):
        run, _, registration = self.register_discovered_run()
        rows = self.repository.list_run_filter_statuses(run.run_id)
        self.assertIsInstance(registration, RunFilterRegistration)
        self.assertTrue(
            all(isinstance(row, RunSourceItemFilterStatusRecord) for row in rows)
        )
        self.assertTrue(all(row.status == "pending" for row in rows))

    def test_duplicate_discoveries_produce_one_ledger_row(self):
        run, source_item_ids, registration = self.register_discovered_run(
            duplicate_first_discovery=True
        )
        self.assertEqual(registration.discovered_source_item_count, 3)
        self.assertEqual(
            len(self.repository.list_run_filter_statuses(run.run_id)),
            len(source_item_ids),
        )

    def test_same_source_item_can_be_registered_in_another_run(self):
        first_run, source_item_ids = self.create_discovered_run(item_count=1)
        second_run = self.create_run("run_filter_2")
        self.discover_items(second_run.run_id, source_item_ids, "second-run")
        self.repository.register_run_filter_items(first_run.run_id)
        self.repository.register_run_filter_items(second_run.run_id)
        self.assertEqual(self.count_rows("run_source_item_filter_statuses"), 2)

    def test_registration_order_is_deterministic(self):
        run, source_item_ids = self.create_discovered_run()
        self.repository.register_run_filter_items(run.run_id)
        rows = self.repository.list_run_filter_statuses(run.run_id)
        self.assertEqual(
            [row.source_item_id for row in rows],
            sorted(source_item_ids),
        )

    def test_registration_is_idempotent(self):
        run, _, first = self.register_discovered_run()
        second = self.repository.register_run_filter_items(run.run_id)
        self.assertEqual(first.registered_filter_item_count, 3)
        self.assertEqual(second.registered_filter_item_count, 3)
        self.assertEqual(second.inserted_count, 0)

    def test_no_discovery_run_produces_zero_rows(self):
        run = self.create_run()
        registration = self.repository.register_run_filter_items(run.run_id)
        self.assertEqual(registration.discovered_source_item_count, 0)
        self.assertEqual(registration.registered_filter_item_count, 0)

    def test_unknown_run_is_rejected(self):
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.register_run_filter_items("missing-run")

    def test_inconsistent_existing_provenance_rolls_back_registration(self):
        run, source_item_ids = self.create_discovered_run(item_count=2)
        unexpected_source_item_id = self.create_source_item("unexpected")
        self.execute_sql(
            """
            INSERT INTO run_source_item_filter_statuses (
                run_id, source_item_id, status, created_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?)
            """,
            (
                run.run_id,
                unexpected_source_item_id,
                DISCOVERED_AT,
                DISCOVERED_AT,
            ),
        )
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.register_run_filter_items(run.run_id)
        rows = self.repository.list_run_filter_statuses(run.run_id)
        self.assertEqual([row.source_item_id for row in rows], [unexpected_source_item_id])
        self.assertTrue(set(source_item_ids).isdisjoint({row.source_item_id for row in rows}))

    def test_registration_accounts_for_every_discovery(self):
        run, _, registration = self.register_discovered_run(item_count=4)
        coverage = self.repository.get_run_filter_coverage(run.run_id)
        self.assertEqual(registration.discovered_source_item_count, 4)
        self.assertEqual(coverage.missing_unregistered, 0)


class DeferredFilterItemTests(TemporaryFilterDecisionDatabaseTestCase):
    def test_pending_items_can_become_deferred(self):
        run, source_item_ids, _ = self.register_discovered_run()
        rows = self.repository.mark_items_deferred(
            run.run_id,
            source_item_ids[1:],
            "manual_review_defer",
            completed_at=FILTER_COMPLETED_AT,
        )
        self.assertEqual({row.status for row in rows}, {"deferred"})

    def test_manual_review_defer_round_trips(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=1)
        [row] = self.repository.mark_items_deferred(
            run.run_id,
            source_item_ids,
            "manual_review_defer",
        )
        self.assertEqual(row.deferred_reason, "manual_review_defer")

    def test_deferred_items_receive_no_filter_decision(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=1)
        self.repository.mark_items_deferred(
            run.run_id,
            source_item_ids,
            "manual_review_defer",
        )
        self.assertEqual(self.repository.list_filter_decisions_for_run(run.run_id), [])

    def test_deferred_is_distinct_from_rejected(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=2)
        self.repository.mark_items_deferred(
            run.run_id,
            [source_item_ids[1]],
            "manual_review_defer",
        )
        started = self.start_filter(run.run_id, [source_item_ids[0]])
        self.complete_filter(
            started.execution.filter_execution_id,
            [FilterDecisionInput(source_item_ids[0], "rejected")],
        )
        statuses = [
            row.status
            for row in self.repository.list_run_filter_statuses(run.run_id)
        ]
        self.assertEqual(statuses, ["rejected", "deferred"])

    def test_invalid_bulk_defer_rolls_back_fully(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=2)
        unregistered_id = self.create_source_item("unregistered")
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.mark_items_deferred(
                run.run_id,
                [source_item_ids[0], unregistered_id],
                "manual_review_defer",
            )
        self.assertEqual(
            {row.status for row in self.repository.list_run_filter_statuses(run.run_id)},
            {"pending"},
        )

    def test_final_state_item_cannot_be_deferred_again(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=1)
        self.repository.mark_items_deferred(
            run.run_id,
            source_item_ids,
            "manual_review_defer",
        )
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.mark_items_deferred(
                run.run_id,
                source_item_ids,
                "manual_review_defer",
            )

    def test_deferred_reason_is_required_and_bounded(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=1)
        for reason in ("", "x" * 501):
            with self.subTest(reason_length=len(reason)):
                with self.assertRaises(FilterDecisionRepositoryError):
                    self.repository.mark_items_deferred(
                        run.run_id,
                        source_item_ids,
                        reason,
                    )


class FilterExecutionStartTests(TemporaryFilterDecisionDatabaseTestCase):
    def test_valid_batch_starts_one_filter_execution(self):
        run, source_item_ids, _ = self.register_discovered_run()
        result = self.start_filter(run.run_id, source_item_ids[:2])
        self.assertIsInstance(result, FilterExecutionStartResult)
        self.assertIsInstance(result.execution, FilterExecutionRecord)
        self.assertEqual(result.execution.status, "running")
        self.assertEqual(self.count_rows("filter_executions"), 1)

    def test_all_selected_items_become_running(self):
        run, source_item_ids, _ = self.register_discovered_run()
        result = self.start_filter(run.run_id, source_item_ids[:2])
        self.assertEqual({row.status for row in result.filter_items}, {"running"})
        self.assertEqual(
            {row.filter_execution_id for row in result.filter_items},
            {result.execution.filter_execution_id},
        )

    def test_batch_membership_and_available_metadata_are_preserved(self):
        run, source_item_ids, _ = self.register_discovered_run()
        result = self.start_filter(run.run_id, source_item_ids[:2])
        self.assertEqual(result.execution.item_count, 2)
        self.assertEqual(result.execution.provider, "deepseek")
        self.assertEqual(result.execution.model, "deepseek-v4-pro")
        self.assertIsNone(result.execution.prompt_version)
        self.assertEqual(result.execution.metadata, {"call_shape": "per_item"})

    def test_unknown_or_nonrunning_run_fails(self):
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.start_filter_execution("missing", [1])
        run, source_item_ids, _ = self.register_discovered_run(item_count=1)
        self.run_repository.fail_run(
            run.run_id,
            PipelineRunFailure("test", "Error", "closed"),
        )
        with self.assertRaises(FilterDecisionRepositoryError):
            self.start_filter(run.run_id, source_item_ids)

    def test_unknown_source_item_fails(self):
        run = self.create_run()
        with self.assertRaises(FilterDecisionRepositoryError):
            self.start_filter(run.run_id, [999])

    def test_unregistered_source_item_fails(self):
        run, source_item_ids = self.create_discovered_run(item_count=1)
        with self.assertRaises(FilterDecisionRepositoryError):
            self.start_filter(run.run_id, source_item_ids)

    def test_duplicate_item_input_fails(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=1)
        with self.assertRaises(FilterDecisionRepositoryError):
            self.start_filter(run.run_id, source_item_ids * 2)

    def test_deferred_item_cannot_start(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=1)
        self.repository.mark_items_deferred(
            run.run_id,
            source_item_ids,
            "manual_review_defer",
        )
        with self.assertRaises(FilterDecisionRepositoryError):
            self.start_filter(run.run_id, source_item_ids)

    def test_invalid_start_creates_no_partial_execution(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=2)
        self.repository.mark_items_deferred(
            run.run_id,
            [source_item_ids[1]],
            "manual_review_defer",
        )
        with self.assertRaises(FilterDecisionRepositoryError):
            self.start_filter(run.run_id, source_item_ids)
        self.assertEqual(self.count_rows("filter_executions"), 0)
        self.assertEqual(
            [row.status for row in self.repository.list_run_filter_statuses(run.run_id)],
            ["pending", "deferred"],
        )

    def test_empty_batch_is_rejected(self):
        run = self.create_run()
        with self.assertRaises(FilterDecisionRepositoryError):
            self.start_filter(run.run_id, [])

    def test_sensitive_or_unbounded_metadata_is_rejected(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=1)
        for metadata in (
            {"api_key": "secret"},
            {"raw_ai_response": {}},
            {"user_preferences": {"private": True}},
        ):
            with self.subTest(metadata=metadata):
                with self.assertRaises(FilterDecisionRepositoryError):
                    self.start_filter(
                        run.run_id,
                        source_item_ids,
                        metadata=metadata,
                    )


class FilterExecutionCompletionTests(TemporaryFilterDecisionDatabaseTestCase):
    def create_running_batch(self, item_count=2):
        run, source_item_ids, _ = self.register_discovered_run(
            item_count=item_count
        )
        result = self.start_filter(run.run_id, source_item_ids)
        return run, source_item_ids, result.execution

    def test_every_attached_item_receives_one_decision(self):
        run, source_item_ids, execution = self.create_running_batch()
        completed = self.complete_filter(
            execution.filter_execution_id,
            [
                FilterDecisionInput(source_item_ids[0], "accepted"),
                FilterDecisionInput(source_item_ids[1], "rejected"),
            ],
        )
        decisions = self.repository.list_filter_decisions_for_run(run.run_id)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(len(decisions), 2)

    def test_accepted_and_rejected_update_ledger_states(self):
        run, source_item_ids, execution = self.create_running_batch()
        self.complete_filter(
            execution.filter_execution_id,
            [
                FilterDecisionInput(source_item_ids[0], "accepted"),
                FilterDecisionInput(source_item_ids[1], "rejected"),
            ],
        )
        self.assertEqual(
            [row.status for row in self.repository.list_run_filter_statuses(run.run_id)],
            ["accepted", "rejected"],
        )

    def test_genuine_reason_confidence_and_matched_paths_round_trip(self):
        run, source_item_ids, execution = self.create_running_batch(item_count=1)
        self.complete_filter(
            execution.filter_execution_id,
            [
                FilterDecisionInput(
                    source_item_ids[0],
                    "accepted",
                    reason="Relevant strategy role",
                    confidence=0.91,
                    matched_career_path_ids=["path_strategy"],
                    metadata={"action": "keep"},
                )
            ],
        )
        [decision] = self.repository.list_filter_decisions_for_run(run.run_id)
        self.assertEqual(decision.reason, "Relevant strategy role")
        self.assertEqual(decision.confidence, 0.91)
        self.assertEqual(decision.matched_career_path_ids, ("path_strategy",))
        self.assertEqual(decision.metadata, {"action": "keep"})

    def test_unavailable_reason_confidence_and_paths_remain_null(self):
        run, source_item_ids, execution = self.create_running_batch(item_count=1)
        self.complete_filter(
            execution.filter_execution_id,
            [FilterDecisionInput(source_item_ids[0], "rejected")],
        )
        [decision] = self.repository.list_filter_decisions_for_run(run.run_id)
        self.assertIsNone(decision.reason)
        self.assertIsNone(decision.confidence)
        self.assertIsNone(decision.matched_career_path_ids)

    def test_incomplete_decision_output_fails_and_rolls_back(self):
        run, source_item_ids, execution = self.create_running_batch()
        with self.assertRaises(FilterDecisionRepositoryError):
            self.complete_filter(
                execution.filter_execution_id,
                [FilterDecisionInput(source_item_ids[0], "accepted")],
            )
        self.assertEqual(self.count_rows("filter_decisions"), 0)
        self.assertEqual(
            {row.status for row in self.repository.list_run_filter_statuses(run.run_id)},
            {"running"},
        )

    def test_unrelated_decision_fails(self):
        _, source_item_ids, execution = self.create_running_batch(item_count=1)
        unrelated_id = self.create_source_item("unrelated")
        with self.assertRaises(FilterDecisionRepositoryError):
            self.complete_filter(
                execution.filter_execution_id,
                [FilterDecisionInput(unrelated_id, "accepted")],
            )
        self.assertNotEqual(source_item_ids[0], unrelated_id)

    def test_duplicate_decision_fails(self):
        _, source_item_ids, execution = self.create_running_batch(item_count=1)
        decision = FilterDecisionInput(source_item_ids[0], "accepted")
        with self.assertRaises(FilterDecisionRepositoryError):
            self.complete_filter(
                execution.filter_execution_id,
                [decision, decision],
            )

    def test_repeated_completion_fails(self):
        _, source_item_ids, execution = self.create_running_batch(item_count=1)
        decisions = [FilterDecisionInput(source_item_ids[0], "accepted")]
        self.complete_filter(execution.filter_execution_id, decisions)
        with self.assertRaises(FilterDecisionRepositoryError):
            self.complete_filter(execution.filter_execution_id, decisions)

    def test_invalid_confidence_does_not_change_running_state(self):
        run, source_item_ids, execution = self.create_running_batch(item_count=1)
        with self.assertRaises(FilterDecisionRepositoryError):
            self.complete_filter(
                execution.filter_execution_id,
                [
                    FilterDecisionInput(
                        source_item_ids[0],
                        "accepted",
                        confidence=1.5,
                    )
                ],
            )
        [status] = self.repository.list_run_filter_statuses(run.run_id)
        self.assertEqual(status.status, "running")

    def test_execution_cannot_complete_after_run_is_closed(self):
        run, source_item_ids, execution = self.create_running_batch(item_count=1)
        self.run_repository.fail_run(
            run.run_id,
            PipelineRunFailure("test", "Error", "closed"),
        )
        with self.assertRaises(FilterDecisionRepositoryError):
            self.complete_filter(
                execution.filter_execution_id,
                [FilterDecisionInput(source_item_ids[0], "accepted")],
            )


class FilterExecutionFailureTests(TemporaryFilterDecisionDatabaseTestCase):
    def create_running_batch(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=2)
        execution = self.start_filter(run.run_id, source_item_ids).execution
        return run, source_item_ids, execution

    def test_running_execution_can_fail_with_all_items(self):
        run, _, execution = self.create_running_batch()
        failed = self.repository.fail_filter_execution(
            execution.filter_execution_id,
            "TimeoutError",
            "Filter request timed out.",
            failed_at=FILTER_COMPLETED_AT,
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(
            {row.status for row in self.repository.list_run_filter_statuses(run.run_id)},
            {"failed"},
        )

    def test_failed_execution_creates_no_decisions(self):
        run, _, execution = self.create_running_batch()
        self.repository.fail_filter_execution(
            execution.filter_execution_id,
            "TimeoutError",
            "Timed out",
        )
        self.assertEqual(self.repository.list_filter_decisions_for_run(run.run_id), [])

    def test_concise_error_data_round_trips(self):
        _, _, execution = self.create_running_batch()
        failed = self.repository.fail_filter_execution(
            execution.filter_execution_id,
            "TimeoutError",
            "Timed out",
            metadata={"attempt": 1},
        )
        self.assertEqual(failed.error_type, "TimeoutError")
        self.assertEqual(failed.error_message, "Timed out")
        self.assertEqual(failed.metadata["attempt"], 1)

    def test_repeated_failure_fails(self):
        _, _, execution = self.create_running_batch()
        self.repository.fail_filter_execution(
            execution.filter_execution_id,
            "Error",
            "failed",
        )
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.fail_filter_execution(
                execution.filter_execution_id,
                "Error",
                "again",
            )

    def test_completed_execution_cannot_fail(self):
        _, source_item_ids, execution = self.create_running_batch()
        self.complete_filter(
            execution.filter_execution_id,
            [
                FilterDecisionInput(source_item_ids[0], "accepted"),
                FilterDecisionInput(source_item_ids[1], "rejected"),
            ],
        )
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.fail_filter_execution(
                execution.filter_execution_id,
                "Error",
                "too late",
            )

    def test_failed_failure_update_preserves_original_running_state(self):
        run, _, execution = self.create_running_batch()
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.fail_filter_execution(
                execution.filter_execution_id,
                "Error",
                "failed",
                metadata={"authorization": "Bearer secret"},
            )
        current = self.repository.get_filter_execution(
            execution.filter_execution_id
        )
        self.assertEqual(current.status, "running")
        self.assertEqual(
            {row.status for row in self.repository.list_run_filter_statuses(run.run_id)},
            {"running"},
        )

    def test_execution_cannot_fail_after_run_is_closed(self):
        run, _, execution = self.create_running_batch()
        self.run_repository.fail_run(
            run.run_id,
            PipelineRunFailure("test", "Error", "closed"),
        )
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.fail_filter_execution(
                execution.filter_execution_id,
                "Error",
                "failed",
            )


class FilterCoverageAndRetrievalTests(TemporaryFilterDecisionDatabaseTestCase):
    def complete_accounted_run(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=3)
        self.repository.mark_items_deferred(
            run.run_id,
            [source_item_ids[2]],
            "manual_review_defer",
        )
        execution = self.start_filter(run.run_id, source_item_ids[:2]).execution
        self.complete_filter(
            execution.filter_execution_id,
            [
                FilterDecisionInput(source_item_ids[0], "accepted"),
                FilterDecisionInput(source_item_ids[1], "rejected"),
            ],
        )
        return run, source_item_ids, execution

    def test_coverage_totals_are_accurate(self):
        run, _, _ = self.complete_accounted_run()
        coverage = self.repository.get_run_filter_coverage(run.run_id)
        self.assertIsInstance(coverage, FilterCoverage)
        self.assertEqual(coverage.discovered_source_items, 3)
        self.assertEqual(coverage.registered_filter_items, 3)
        self.assertEqual((coverage.accepted, coverage.rejected), (1, 1))
        self.assertEqual(coverage.deferred, 1)
        self.assertEqual(coverage.filter_execution_count, 1)
        self.assertEqual(coverage.filter_decision_count, 2)

    def test_missing_registration_is_detected(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=2)
        self.execute_sql(
            "DELETE FROM run_source_item_filter_statuses WHERE source_item_id = ?",
            (source_item_ids[1],),
        )
        coverage = self.repository.get_run_filter_coverage(run.run_id)
        self.assertEqual(coverage.missing_unregistered, 1)
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.assert_run_filter_accounting_complete(run.run_id)

    def test_pending_and_running_items_are_detected(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=2)
        self.start_filter(run.run_id, [source_item_ids[0]])
        coverage = self.repository.get_run_filter_coverage(run.run_id)
        self.assertEqual((coverage.pending, coverage.running), (1, 1))
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.assert_run_filter_accounting_complete(run.run_id)

    def test_complete_accepted_rejected_deferred_accounting_succeeds(self):
        run, _, _ = self.complete_accounted_run()
        coverage = self.repository.assert_run_filter_accounting_complete(
            run.run_id
        )
        self.assertEqual(coverage.pending, 0)
        self.assertEqual(coverage.running, 0)

    def test_failed_items_are_valid_final_accounting(self):
        run, source_item_ids, _ = self.register_discovered_run(item_count=1)
        execution = self.start_filter(run.run_id, source_item_ids).execution
        self.repository.fail_filter_execution(
            execution.filter_execution_id,
            "Error",
            "failed",
        )
        coverage = self.repository.assert_run_filter_accounting_complete(
            run.run_id
        )
        self.assertEqual(coverage.failed, 1)

    def test_decisions_are_typed_and_deterministically_ordered(self):
        run, source_item_ids, _ = self.complete_accounted_run()
        decisions = self.repository.list_filter_decisions_for_run(run.run_id)
        self.assertTrue(all(isinstance(row, FilterDecisionRecord) for row in decisions))
        self.assertEqual(
            [row.source_item_id for row in decisions],
            source_item_ids[:2],
        )
        self.assertTrue(all(not isinstance(row, sqlite3.Row) for row in decisions))

    def test_source_item_history_across_runs_and_limit_is_visible(self):
        first_run, source_item_ids = self.create_discovered_run(item_count=1)
        self.repository.register_run_filter_items(first_run.run_id)
        first_execution = self.start_filter(first_run.run_id, source_item_ids).execution
        self.complete_filter(
            first_execution.filter_execution_id,
            [FilterDecisionInput(source_item_ids[0], "accepted")],
        )

        second_run = self.create_run("run_filter_2")
        self.discover_items(second_run.run_id, source_item_ids, "second-run")
        self.repository.register_run_filter_items(second_run.run_id)
        second_execution = self.start_filter(second_run.run_id, source_item_ids).execution
        self.complete_filter(
            second_execution.filter_execution_id,
            [FilterDecisionInput(source_item_ids[0], "rejected")],
        )
        history = self.repository.list_filter_decisions_for_source_item(
            source_item_ids[0]
        )
        self.assertEqual([row.run_id for row in history], [second_run.run_id, first_run.run_id])
        self.assertEqual(
            len(
                self.repository.list_filter_decisions_for_source_item(
                    source_item_ids[0],
                    limit=1,
                )
            ),
            1,
        )

    def test_completed_execution_missing_decision_is_detected(self):
        run, _, execution = self.complete_accounted_run()
        self.execute_sql(
            """
            DELETE FROM filter_decisions
            WHERE filter_decision_id = (
                SELECT filter_decision_id
                FROM filter_decisions
                WHERE filter_execution_id = ?
                ORDER BY filter_decision_id
                LIMIT 1
            )
            """,
            (execution.filter_execution_id,),
        )
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.assert_run_filter_accounting_complete(run.run_id)

    def test_unexpected_registered_source_item_is_detected(self):
        run, _, _ = self.register_discovered_run(item_count=1)
        unexpected_id = self.create_source_item("unexpected")
        self.execute_sql(
            """
            INSERT INTO run_source_item_filter_statuses (
                run_id, source_item_id, status, deferred_reason,
                completed_at, created_at, updated_at
            ) VALUES (?, ?, 'deferred', 'manual', ?, ?, ?)
            """,
            (
                run.run_id,
                unexpected_id,
                FILTER_COMPLETED_AT,
                DISCOVERED_AT,
                FILTER_COMPLETED_AT,
            ),
        )
        coverage = self.repository.get_run_filter_coverage(run.run_id)
        self.assertEqual(coverage.unexpected_registered, 1)
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.assert_run_filter_accounting_complete(run.run_id)

    def test_decision_with_unexpected_run_relationship_is_detected(self):
        first_run, _, _ = self.complete_accounted_run()
        second_run = self.create_run("run_filter_2")
        self.execute_sql(
            "UPDATE filter_decisions SET run_id = ? WHERE run_id = ?",
            (second_run.run_id, first_run.run_id),
            foreign_keys=False,
        )
        with self.assertRaises(FilterDecisionRepositoryError):
            self.repository.assert_run_filter_accounting_complete(
                second_run.run_id
            )


class LLMBoundaryAuditTests(unittest.TestCase):
    def test_live_ai_filter_uses_openai_compatible_client_per_item(self):
        response = llm_response(
            is_relevant=True,
            confidence=0.88,
            reason="Relevant",
        )
        with patch("src.ai_filter.OpenAI") as openai_factory:
            create = openai_factory.return_value.chat.completions.create
            create.return_value = response
            client = AIFilterClient(
                provider="deepseek",
                api_key="test-key",
                base_url="https://api.deepseek.example",
                model="deepseek-v4-pro",
                dry_run=False,
            )
            result = client.filter_item(
                make_raw_item(),
                make_user_profile(),
                [make_career_path()],
            )
        openai_factory.assert_called_once_with(
            api_key="test-key",
            base_url="https://api.deepseek.example",
        )
        create.assert_called_once()
        self.assertEqual(create.call_args.kwargs["model"], "deepseek-v4-pro")
        self.assertTrue(result.is_relevant)

    def test_execute_ai_filter_performs_one_live_invocation_per_selected_item(self):
        responses = [
            llm_response(is_relevant=True, reason="keep"),
            llm_response(is_relevant=False, reason="drop"),
        ]
        with patch("src.ai_filter.OpenAI") as openai_factory:
            create = openai_factory.return_value.chat.completions.create
            create.side_effect = responses
            client = AIFilterClient(
                provider="deepseek",
                api_key="test-key",
                base_url="https://api.deepseek.example",
                model="deepseek-v4-pro",
                dry_run=False,
            )
            report = execute_ai_filter(
                [make_raw_item("1"), make_raw_item("2")],
                make_user_profile(),
                [make_career_path()],
                client,
            )
        self.assertEqual(create.call_count, 2)
        self.assertEqual(report.executed_count, 2)
        self.assertEqual(
            [status.status for status in report.raw_item_statuses],
            ["processed_accepted", "processed_rejected"],
        )

    def test_llm_output_fields_affect_filter_result(self):
        response = llm_response(
            is_relevant=True,
            confidence=0.93,
            reason="Matched target",
            suggested_category="job",
            matched_career_path_ids=["path_strategy"],
            action="keep",
        )
        with patch("src.ai_filter.OpenAI") as openai_factory:
            openai_factory.return_value.chat.completions.create.return_value = response
            client = AIFilterClient(
                "deepseek",
                "test-key",
                "https://api.deepseek.example",
                "deepseek-v4-pro",
                dry_run=False,
            )
            result = client.filter_item(
                make_raw_item(),
                make_user_profile(),
                [make_career_path()],
            )
        self.assertEqual(result.confidence, 0.93)
        self.assertEqual(result.reason, "Matched target")
        self.assertEqual(result.suggested_category, SignalCategory.JOB)
        self.assertEqual(result.matched_career_path_ids, ["path_strategy"])
        self.assertEqual(result.action, "keep")

    def test_provider_and_model_exist_but_prompt_version_is_unavailable(self):
        with patch("src.ai_filter.OpenAI"):
            client = AIFilterClient(
                "deepseek",
                "test-key",
                "https://api.deepseek.example",
                "deepseek-v4-pro",
                dry_run=False,
            )
        self.assertEqual(client.provider, "deepseek")
        self.assertEqual(client.model, "deepseek-v4-pro")
        self.assertFalse(hasattr(client, "prompt_version"))

    def test_normalizer_makes_no_llm_or_network_call(self):
        raw_item = make_raw_item()
        result = make_filter_result(raw_item, is_relevant=True)
        with patch("src.ai_filter.OpenAI") as openai_factory, patch(
            "requests.Session.request"
        ) as network_request:
            signals = normalize_raw_items_to_career_signals(
                [raw_item],
                [result],
            )
        openai_factory.assert_not_called()
        network_request.assert_not_called()
        self.assertEqual(len(signals), 1)

    def test_career_signal_construction_is_deterministic_from_accepted_input(self):
        raw_item = make_raw_item()
        result = make_filter_result(raw_item, is_relevant=True)
        first = normalize_raw_items_to_career_signals([raw_item], [result])[0]
        second = normalize_raw_items_to_career_signals([raw_item], [result])[0]
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.relevance_score, 90.0)
        self.assertIn(result.reason, first.summary)

    def test_no_direct_llm_career_signal_generation_is_required(self):
        raw_item = make_raw_item()
        result = make_filter_result(raw_item, is_relevant=True)
        signal = normalize_raw_items_to_career_signals([raw_item], [result])[0]
        self.assertEqual(signal.title, raw_item.title)
        self.assertEqual(signal.organization, raw_item.organization)
        self.assertEqual(signal.url, raw_item.url)

    def test_dry_run_uses_rules_without_constructing_openai_client(self):
        with patch("src.ai_filter.OpenAI") as openai_factory:
            client = AIFilterClient(
                "deepseek",
                "",
                "https://api.deepseek.example",
                "deepseek-v4-pro",
                dry_run=True,
            )
            result = client.filter_item(
                make_raw_item(),
                make_user_profile(),
                [make_career_path()],
            )
        openai_factory.assert_not_called()
        self.assertEqual(result.metadata["mode"], "dry_run")

    def test_filter_failure_is_recorded_per_item_and_processing_continues(self):
        client = Mock()
        second_item = make_raw_item("2")
        client.filter_item.side_effect = [
            RuntimeError("controlled failure"),
            make_filter_result(second_item, is_relevant=True),
        ]
        report = execute_ai_filter(
            [make_raw_item("1"), second_item],
            make_user_profile(),
            [make_career_path()],
            client,
        )
        self.assertEqual(client.filter_item.call_count, 2)
        self.assertEqual(
            [status.status for status in report.raw_item_statuses],
            ["failed", "processed_accepted"],
        )

    def test_all_items_are_invoked_without_runtime_truncation(self):
        client = Mock()
        client.filter_item.side_effect = [
            make_filter_result(make_raw_item("1")),
            make_filter_result(make_raw_item("2")),
        ]
        report = execute_ai_filter(
            [make_raw_item("1"), make_raw_item("2")],
            make_user_profile(),
            [make_career_path()],
            client,
        )
        self.assertEqual(client.filter_item.call_count, 2)
        self.assertEqual(report.executed_count, 2)
        self.assertNotIn(
            "deferred_due_to_limit",
            {status.status for status in report.raw_item_statuses},
        )


class FilterDecisionCompatibilityTests(TemporaryFilterDecisionDatabaseTestCase):
    def test_existing_repositories_remain_compatible(self):
        self.assertIsInstance(PipelineRunRepository(self.database_path), PipelineRunRepository)
        self.assertIsInstance(
            SourceExecutionRepository(self.database_path),
            SourceExecutionRepository,
        )
        self.assertIsInstance(SourceItemRepository(self.database_path), SourceItemRepository)
        self.assertIsInstance(CareerSignalRepository(self.database_path), CareerSignalRepository)

    def test_database_package_exports_are_typed_and_lazy(self):
        database = importlib.import_module("src.database")
        repositories = importlib.import_module("src.database.repositories")
        self.assertIs(database.FilterDecisionRepository, FilterDecisionRepository)
        self.assertIs(repositories.FilterCoverage, FilterCoverage)

    def test_pipeline_filter_decision_repository_dependency_is_optional(self):
        from inspect import signature
        from src.pipeline import MockPipeline

        parameter = signature(MockPipeline.__init__).parameters[
            "filter_decision_repository"
        ]
        self.assertIsNone(
            parameter.default,
        )

    def test_source_items_and_career_signals_have_no_direct_run_ownership(self):
        for table in ("source_items", "career_signals"):
            columns = {
                row["name"]
                for row in self.rows(f"PRAGMA table_info({table})")
            }
            self.assertNotIn("run_id", columns)

    def test_filter_decisions_do_not_require_a_career_signal(self):
        columns = {
            row["name"]
            for row in self.rows("PRAGMA table_info(filter_decisions)")
        }
        self.assertNotIn("career_signal_id", columns)

    def test_imports_create_no_default_database_side_effect(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = (
            DEFAULT_DATABASE_FILE.stat().st_mtime
            if before_exists
            else None
        )
        importlib.import_module(
            "src.database.repositories.filter_decision_repository"
        )
        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)
        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)

    def test_development_database_is_not_used_by_repository_tests(self):
        self.assertNotEqual(self.database_path.resolve(), DEFAULT_DATABASE_FILE.resolve())


def make_raw_item(suffix="1"):
    return RawItem(
        source_type=SourceType.SEARCH_API,
        title=f"Strategy analyst role {suffix}",
        organization="Example",
        url=f"https://example.com/jobs/{suffix}",
        published_at="2026-08-01T00:00:00+00:00",
        raw_text=f"Career strategy opportunity {suffix}",
        metadata={"provider": "brave"},
    )


def make_user_profile():
    return UserProfile(
        profile_id="profile_1",
        name="Test User",
        background_summary="Strategy analyst",
        skills=["strategy"],
        interests=["venture capital"],
        preferred_roles=["Strategy Analyst"],
        preferred_locations=["Shanghai"],
        constraints=[],
    )


def make_career_path():
    return TargetCareerPath(
        path_id="path_strategy",
        title="Strategy",
        description="Strategy roles",
        category=CareerPathCategory.AI_STRATEGY,
        fit_score=90,
        rationale=["Relevant"],
        keywords=["strategy"],
        suggested_roles=["Strategy Analyst"],
        search_seed_terms=["strategy analyst"],
    )


def make_filter_result(raw_item, is_relevant=True):
    return AIFilterResult(
        raw_item_fingerprint=ai_filter_fingerprint(raw_item),
        title=raw_item.title,
        url=raw_item.url,
        is_relevant=is_relevant,
        confidence=0.9,
        reason="Relevant to target path",
        suggested_category=SignalCategory.JOB,
        matched_career_path_ids=["path_strategy"],
        action="keep" if is_relevant else "drop",
        metadata={"mode": "test"},
    )


def ai_filter_fingerprint(raw_item):
    source = (
        f"{raw_item.source_type.value}|{raw_item.title}|"
        f"{raw_item.url}|{raw_item.raw_text[:200]}"
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def llm_response(
    *,
    is_relevant,
    confidence=0.8,
    reason,
    suggested_category="job",
    matched_career_path_ids=None,
    action=None,
):
    payload = {
        "is_relevant": is_relevant,
        "confidence": confidence,
        "reason": reason,
        "suggested_category": suggested_category,
        "matched_career_path_ids": matched_career_path_ids or [],
        "action": action or ("keep" if is_relevant else "drop"),
    }
    response = Mock()
    response.choices = [Mock(message=Mock(content=json.dumps(payload)))]
    return response


if __name__ == "__main__":
    unittest.main()
