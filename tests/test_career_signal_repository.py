import json
from pathlib import Path
import shutil
import tempfile
import unittest

from src.config import DEFAULT_DATABASE_FILE
from src.database.connection import open_database_connection
from src.database.migrations import (
    MIGRATIONS_DIR,
    discover_migrations,
    initialize_database,
)
from src.database.repositories.career_signal_repository import (
    CareerSignalRepository,
    CareerSignalRepositoryError,
    CareerSignalWrite,
    serialize_career_signal,
)
from src.database.repositories.source_item_repository import SourceItemRepository
from src.database.source_identity import fingerprint_raw_item
from src.models import (
    AIFilterResult,
    CareerSignal,
    RawItem,
    SignalCategory,
    SourceType,
)
from src.normalizer import normalize_raw_items_to_career_signals
from src.storage import convert_to_json_ready


CREATED_AT = "2026-07-22T00:00:00+00:00"
UPDATED_AT = "2026-07-23T00:00:00+00:00"


def make_career_signal(
    *,
    signal_id: str = "signal_test_1",
    category: SignalCategory = SignalCategory.JOB,
    title: str = "Strategy Analyst",
    organization: str = "Example Co",
    url: str = "https://example.com/jobs/1",
    published_at: str | None = "2026-07-20T00:00:00+00:00",
    summary: str = "A strategy role with AI product exposure.",
    source_type: SourceType = SourceType.SEARCH_API,
    relevance_score: float | None = 92.5,
    metadata: dict | None = None,
) -> CareerSignal:
    signal_metadata = {
        "normalizer": "test",
        "nested": {
            "tags": ["strategy", "AI"],
        },
    }

    if metadata:
        signal_metadata.update(metadata)

    return CareerSignal(
        signal_id=signal_id,
        category=category,
        title=title,
        organization=organization,
        url=url,
        published_at=published_at,
        summary=summary,
        source_type=source_type,
        relevance_score=relevance_score,
        metadata=signal_metadata,
    )


def make_raw_item(
    *,
    url: str = "https://example.com/jobs/1",
    title: str = "Strategy Analyst",
) -> RawItem:
    return RawItem(
        source_type=SourceType.SEARCH_API,
        title=title,
        organization="Example Co",
        url=url,
        published_at=None,
        raw_text=title,
        metadata={"provider": "brave"},
    )


class TemporaryCareerSignalDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "career-signals.db"
        initialize_database(database_path=self.database_path)
        self.repository = CareerSignalRepository(database_path=self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def fetch_career_signals(self) -> list[dict]:
        connection = open_database_connection(self.database_path)

        try:
            rows = connection.execute(
                "SELECT * FROM career_signals ORDER BY career_signal_row_id"
            ).fetchall()

            return [
                dict(row)
                for row in rows
            ]
        finally:
            connection.close()

    def fetch_career_signal(self) -> dict:
        rows = self.fetch_career_signals()
        self.assertEqual(len(rows), 1)
        return rows[0]

    def create_source_item(self, raw_item: RawItem | None = None) -> int:
        raw_item = raw_item or make_raw_item()
        source_repository = SourceItemRepository(database_path=self.database_path)
        source_repository.upsert_one(raw_item, seen_at=CREATED_AT)
        row = source_repository.get_by_fingerprint(fingerprint_raw_item(raw_item))

        return int(row["source_item_id"])


class CareerSignalMigrationTests(TemporaryCareerSignalDatabaseTestCase):
    def test_migration_003_is_discovered_after_001_and_002(self):
        migrations = discover_migrations()

        self.assertEqual(
            [migration.version for migration in migrations[:3]],
            ["001", "002", "003"],
        )

    def test_migration_003_applies_to_database_containing_001_and_002(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_dir = root / "first"
            first_dir.mkdir()

            for filename in [
                "001_initial_schema.sql",
                "002_source_items.sql",
            ]:
                shutil.copy(MIGRATIONS_DIR / filename, first_dir / filename)

            database_path = root / "staged.db"
            initialize_database(
                database_path=database_path,
                migrations_dir=first_dir,
            )
            applied = initialize_database(database_path=database_path)

        self.assertEqual(
            [migration.version for migration in applied],
            ["003", "004", "005", "006", "007"],
        )

    def test_career_signals_table_exists(self):
        connection = open_database_connection(self.database_path)

        try:
            table = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'career_signals'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertIsNotNone(table)

    def test_expected_columns_and_nullability_exist(self):
        connection = open_database_connection(self.database_path)

        try:
            columns = {
                row["name"]: dict(row)
                for row in connection.execute("PRAGMA table_info(career_signals)")
            }
        finally:
            connection.close()

        self.assertEqual(
            set(columns),
            {
                "career_signal_row_id",
                "signal_id",
                "source_item_id",
                "category",
                "title",
                "organization",
                "url",
                "published_at",
                "summary",
                "source_type",
                "relevance_score",
                "payload_json",
                "created_at",
                "updated_at",
            },
        )
        self.assertEqual(columns["career_signal_row_id"]["pk"], 1)
        self.assertEqual(columns["signal_id"]["notnull"], 1)
        self.assertEqual(columns["category"]["notnull"], 1)
        self.assertEqual(columns["title"]["notnull"], 1)
        self.assertEqual(columns["source_type"]["notnull"], 1)
        self.assertEqual(columns["payload_json"]["notnull"], 1)
        self.assertEqual(columns["created_at"]["notnull"], 1)
        self.assertEqual(columns["updated_at"]["notnull"], 1)
        self.assertEqual(columns["source_item_id"]["notnull"], 0)
        self.assertEqual(columns["organization"]["notnull"], 0)
        self.assertEqual(columns["published_at"]["notnull"], 0)

    def test_source_item_id_foreign_key_targets_source_items(self):
        connection = open_database_connection(self.database_path)

        try:
            foreign_keys = [
                dict(row)
                for row in connection.execute("PRAGMA foreign_key_list(career_signals)")
            ]
        finally:
            connection.close()

        self.assertEqual(len(foreign_keys), 1)
        self.assertEqual(foreign_keys[0]["from"], "source_item_id")
        self.assertEqual(foreign_keys[0]["table"], "source_items")
        self.assertEqual(foreign_keys[0]["to"], "source_item_id")

    def test_on_delete_behavior_is_set_null(self):
        connection = open_database_connection(self.database_path)

        try:
            foreign_key = connection.execute(
                "PRAGMA foreign_key_list(career_signals)"
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(foreign_key["on_delete"], "SET NULL")

    def test_expected_indexes_exist(self):
        connection = open_database_connection(self.database_path)

        try:
            indexes = {
                row["name"]
                for row in connection.execute("PRAGMA index_list(career_signals)")
            }
        finally:
            connection.close()

        self.assertIn("idx_career_signals_category", indexes)
        self.assertIn("idx_career_signals_source_type", indexes)
        self.assertIn("idx_career_signals_source_item_id", indexes)
        self.assertIn("idx_career_signals_updated_at", indexes)

    def test_migration_003_is_recorded_exactly_once(self):
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            rows = connection.execute(
                """
                SELECT version, name
                FROM schema_migrations
                WHERE version = '003'
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "career_signals")

    def test_repeated_initialization_is_idempotent(self):
        applied = initialize_database(database_path=self.database_path)

        self.assertEqual(applied, [])

    def test_pipeline_runs_and_source_items_still_exist(self):
        connection = open_database_connection(self.database_path)

        try:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()

        self.assertIn("pipeline_runs", tables)
        self.assertIn("source_items", tables)


class CareerSignalSerializationTests(TemporaryCareerSignalDatabaseTestCase):
    def test_complete_career_signal_is_valid_payload_json(self):
        career_signal = make_career_signal()

        self.repository.upsert_one(career_signal, persisted_at=CREATED_AT)
        payload = json.loads(self.fetch_career_signal()["payload_json"])

        self.assertEqual(payload["signal_id"], career_signal.signal_id)
        self.assertEqual(payload["title"], career_signal.title)
        self.assertEqual(payload["summary"], career_signal.summary)
        self.assertEqual(payload["relevance_score"], career_signal.relevance_score)

    def test_enum_values_serialize_as_strings(self):
        career_signal = make_career_signal(
            category=SignalCategory.FUNDING,
            source_type=SourceType.RSS,
        )

        self.repository.upsert_one(career_signal)
        payload = json.loads(self.fetch_career_signal()["payload_json"])

        self.assertEqual(payload["category"], "funding")
        self.assertEqual(payload["source_type"], "rss")

    def test_nested_metadata_survives_round_trip(self):
        career_signal = make_career_signal(
            metadata={
                "nested": {
                    "evidence": [
                        {"label": "one"},
                        {"label": "two"},
                    ]
                }
            }
        )

        self.repository.upsert_one(career_signal)
        payload = json.loads(self.fetch_career_signal()["payload_json"])

        self.assertEqual(
            payload["metadata"]["nested"]["evidence"][1]["label"],
            "two",
        )

    def test_unicode_survives_round_trip(self):
        career_signal = make_career_signal(
            title="战略岗位",
            summary="支持 AI 战略与市场研究",
            metadata={"note": "中文内容"},
        )

        self.repository.upsert_one(career_signal)
        payload = json.loads(self.fetch_career_signal()["payload_json"])

        self.assertEqual(payload["title"], "战略岗位")
        self.assertEqual(payload["summary"], "支持 AI 战略与市场研究")
        self.assertEqual(payload["metadata"]["note"], "中文内容")

    def test_nullable_published_at_is_accepted(self):
        career_signal = make_career_signal(published_at=None)

        self.repository.upsert_one(career_signal)
        row = self.fetch_career_signal()
        payload = json.loads(row["payload_json"])

        self.assertIsNone(row["published_at"])
        self.assertIsNone(payload["published_at"])

    def test_blank_organization_is_stored_as_null_column_and_preserved_in_payload(self):
        career_signal = make_career_signal(organization="")

        self.repository.upsert_one(career_signal)
        row = self.fetch_career_signal()
        payload = json.loads(row["payload_json"])

        self.assertIsNone(row["organization"])
        self.assertEqual(payload["organization"], "")

    def test_existing_json_model_serialization_contract_remains_unchanged(self):
        career_signal = make_career_signal()

        self.assertEqual(
            convert_to_json_ready(career_signal),
            career_signal.to_dict(),
        )


class CareerSignalIdentityTests(TemporaryCareerSignalDatabaseTestCase):
    def test_repeated_persistence_uses_same_signal_identity(self):
        career_signal = make_career_signal()

        self.repository.upsert_one(career_signal, persisted_at=CREATED_AT)
        self.repository.upsert_one(career_signal, persisted_at=UPDATED_AT)

        self.assertEqual(self.repository.count(), 1)

    def test_mutable_summary_change_does_not_create_second_row(self):
        original = make_career_signal(summary="Original summary")
        updated = make_career_signal(summary="Updated summary")

        self.repository.upsert_one(original, persisted_at=CREATED_AT)
        self.repository.upsert_one(updated, persisted_at=UPDATED_AT)
        row = self.fetch_career_signal()

        self.assertEqual(self.repository.count(), 1)
        self.assertEqual(row["summary"], "Updated summary")

    def test_distinct_signals_remain_separate(self):
        self.repository.upsert_many(
            [
                CareerSignalWrite(make_career_signal(signal_id="signal_one")),
                CareerSignalWrite(make_career_signal(signal_id="signal_two")),
            ]
        )

        self.assertEqual(self.repository.count(), 2)

    def test_identity_decision_matches_normalizer_signal_id_behavior(self):
        raw_item = make_raw_item()
        raw_item_fingerprint = (
            "4ba59babc9769768"
        )
        filter_result = AIFilterResult(
            raw_item_fingerprint=raw_item_fingerprint,
            title=raw_item.title,
            url=raw_item.url,
            is_relevant=True,
            confidence=0.9,
            reason="Relevant",
        )

        first = normalize_raw_items_to_career_signals([raw_item], [filter_result])[0]
        second = normalize_raw_items_to_career_signals([raw_item], [filter_result])[0]

        self.assertEqual(first.signal_id, second.signal_id)
        self.assertTrue(first.signal_id.startswith("signal_"))


class CareerSignalSourceLinkageTests(TemporaryCareerSignalDatabaseTestCase):
    def test_valid_source_item_id_is_stored(self):
        source_item_id = self.create_source_item()

        self.repository.upsert_one(
            make_career_signal(),
            source_item_id=source_item_id,
        )

        self.assertEqual(self.fetch_career_signal()["source_item_id"], source_item_id)

    def test_nonexistent_source_item_id_fails(self):
        with self.assertRaises(CareerSignalRepositoryError):
            self.repository.upsert_one(
                make_career_signal(),
                source_item_id=999,
            )

    def test_foreign_key_failure_rolls_back_batch(self):
        with self.assertRaises(CareerSignalRepositoryError):
            self.repository.upsert_many(
                [
                    CareerSignalWrite(make_career_signal(signal_id="signal_good")),
                    CareerSignalWrite(
                        make_career_signal(signal_id="signal_bad"),
                        source_item_id=999,
                    ),
                ]
            )

        self.assertEqual(self.repository.count(), 0)

    def test_existing_non_null_link_survives_incoming_null_link(self):
        source_item_id = self.create_source_item()
        career_signal = make_career_signal()

        self.repository.upsert_one(career_signal, source_item_id=source_item_id)
        self.repository.upsert_one(career_signal, source_item_id=None)

        self.assertEqual(self.fetch_career_signal()["source_item_id"], source_item_id)

    def test_existing_null_link_can_be_filled_by_valid_incoming_link(self):
        source_item_id = self.create_source_item()
        career_signal = make_career_signal()

        self.repository.upsert_one(career_signal, source_item_id=None)
        self.repository.upsert_one(career_signal, source_item_id=source_item_id)

        self.assertEqual(self.fetch_career_signal()["source_item_id"], source_item_id)

    def test_same_link_can_be_repeated_safely(self):
        source_item_id = self.create_source_item()
        career_signal = make_career_signal()

        self.repository.upsert_one(career_signal, source_item_id=source_item_id)
        self.repository.upsert_one(career_signal, source_item_id=source_item_id)

        self.assertEqual(self.fetch_career_signal()["source_item_id"], source_item_id)

    def test_conflicting_non_null_links_raise_repository_error(self):
        first_source_item_id = self.create_source_item(
            make_raw_item(url="https://example.com/jobs/one")
        )
        second_source_item_id = self.create_source_item(
            make_raw_item(url="https://example.com/jobs/two")
        )
        career_signal = make_career_signal()

        self.repository.upsert_one(career_signal, source_item_id=first_source_item_id)

        with self.assertRaises(CareerSignalRepositoryError):
            self.repository.upsert_one(
                career_signal,
                source_item_id=second_source_item_id,
            )

    def test_on_delete_set_null_behaves_as_designed(self):
        source_item_id = self.create_source_item()
        self.repository.upsert_one(
            make_career_signal(),
            source_item_id=source_item_id,
        )
        connection = open_database_connection(self.database_path)

        try:
            connection.execute(
                "DELETE FROM source_items WHERE source_item_id = ?",
                (source_item_id,),
            )
            connection.commit()
        finally:
            connection.close()

        self.assertIsNone(self.fetch_career_signal()["source_item_id"])


class CareerSignalRepositoryBehaviorTests(TemporaryCareerSignalDatabaseTestCase):
    def test_new_career_signal_inserts_one_row(self):
        summary = self.repository.upsert_one(
            make_career_signal(),
            persisted_at=CREATED_AT,
        )

        self.assertEqual(self.repository.count(), 1)
        self.assertEqual(summary.inserted_count, 1)

    def test_repeated_upsert_keeps_one_row(self):
        career_signal = make_career_signal()

        self.repository.upsert_one(career_signal)
        self.repository.upsert_one(career_signal)

        self.assertEqual(self.repository.count(), 1)

    def test_career_signal_row_id_remains_stable(self):
        career_signal = make_career_signal()

        self.repository.upsert_one(career_signal, persisted_at=CREATED_AT)
        first_id = self.fetch_career_signal()["career_signal_row_id"]
        self.repository.upsert_one(career_signal, persisted_at=UPDATED_AT)
        second_id = self.fetch_career_signal()["career_signal_row_id"]

        self.assertEqual(first_id, second_id)

    def test_created_at_remains_stable(self):
        career_signal = make_career_signal()

        self.repository.upsert_one(career_signal, persisted_at=CREATED_AT)
        self.repository.upsert_one(career_signal, persisted_at=UPDATED_AT)

        self.assertEqual(self.fetch_career_signal()["created_at"], CREATED_AT)

    def test_updated_at_changes(self):
        career_signal = make_career_signal()

        self.repository.upsert_one(career_signal, persisted_at=CREATED_AT)
        self.repository.upsert_one(career_signal, persisted_at=UPDATED_AT)

        self.assertEqual(self.fetch_career_signal()["updated_at"], UPDATED_AT)

    def test_mutable_columns_refresh(self):
        original = make_career_signal(title="Old title", relevance_score=50.0)
        updated = make_career_signal(
            title="New title",
            category=SignalCategory.NEWS,
            relevance_score=88.0,
        )

        self.repository.upsert_one(original)
        self.repository.upsert_one(updated)
        row = self.fetch_career_signal()

        self.assertEqual(row["title"], "New title")
        self.assertEqual(row["category"], "news")
        self.assertEqual(row["relevance_score"], 88.0)

    def test_payload_json_refreshes(self):
        original = make_career_signal(summary="Old")
        updated = make_career_signal(summary="New")

        self.repository.upsert_one(original)
        self.repository.upsert_one(updated)
        payload = json.loads(self.fetch_career_signal()["payload_json"])

        self.assertEqual(payload["summary"], "New")

    def test_get_by_signal_id_returns_stored_row(self):
        career_signal = make_career_signal(signal_id="signal_lookup")

        self.repository.upsert_one(career_signal)
        row = self.repository.get_by_signal_id("signal_lookup")

        self.assertIsNotNone(row)
        self.assertEqual(row["signal_id"], "signal_lookup")

    def test_count_returns_expected_number(self):
        self.repository.upsert_many(
            [
                CareerSignalWrite(make_career_signal(signal_id="signal_one")),
                CareerSignalWrite(make_career_signal(signal_id="signal_two")),
            ]
        )

        self.assertEqual(self.repository.count(), 2)


class CareerSignalBatchTests(TemporaryCareerSignalDatabaseTestCase):
    def test_duplicates_within_one_batch_write_once(self):
        self.repository.upsert_many(
            [
                CareerSignalWrite(make_career_signal(summary="First")),
                CareerSignalWrite(make_career_signal(summary="Second")),
            ]
        )

        self.assertEqual(self.repository.count(), 1)
        self.assertEqual(self.fetch_career_signal()["summary"], "Second")

    def test_summary_counts_received_and_unique_records(self):
        summary = self.repository.upsert_many(
            [
                CareerSignalWrite(make_career_signal(signal_id="signal_one")),
                CareerSignalWrite(make_career_signal(signal_id="signal_one")),
                CareerSignalWrite(make_career_signal(signal_id="signal_two")),
            ]
        )

        self.assertEqual(summary.received_count, 3)
        self.assertEqual(summary.unique_count, 2)

    def test_inserted_and_updated_counts_are_correct(self):
        self.repository.upsert_one(make_career_signal(signal_id="signal_existing"))

        summary = self.repository.upsert_many(
            [
                CareerSignalWrite(make_career_signal(signal_id="signal_existing")),
                CareerSignalWrite(make_career_signal(signal_id="signal_new")),
            ]
        )

        self.assertEqual(summary.inserted_count, 1)
        self.assertEqual(summary.updated_count, 1)

    def test_compatible_duplicate_links_are_accepted(self):
        source_item_id = self.create_source_item()

        self.repository.upsert_many(
            [
                CareerSignalWrite(
                    make_career_signal(summary="First"),
                    source_item_id=source_item_id,
                ),
                CareerSignalWrite(
                    make_career_signal(summary="Second"),
                    source_item_id=source_item_id,
                ),
            ]
        )

        row = self.fetch_career_signal()
        self.assertEqual(row["source_item_id"], source_item_id)
        self.assertEqual(row["summary"], "Second")

    def test_conflicting_duplicate_links_fail_batch(self):
        first_source_item_id = self.create_source_item(
            make_raw_item(url="https://example.com/source/one")
        )
        second_source_item_id = self.create_source_item(
            make_raw_item(url="https://example.com/source/two")
        )

        with self.assertRaises(CareerSignalRepositoryError):
            self.repository.upsert_many(
                [
                    CareerSignalWrite(
                        make_career_signal(),
                        source_item_id=first_source_item_id,
                    ),
                    CareerSignalWrite(
                        make_career_signal(),
                        source_item_id=second_source_item_id,
                    ),
                ]
            )

        self.assertEqual(self.repository.count(), 0)

    def test_empty_batch_returns_zero_summary_without_database_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "missing.db"
            repository = CareerSignalRepository(database_path=database_path)

            summary = repository.upsert_many([])

            self.assertEqual(summary.received_count, 0)
            self.assertEqual(summary.unique_count, 0)
            self.assertEqual(summary.inserted_count, 0)
            self.assertEqual(summary.updated_count, 0)
            self.assertFalse(database_path.exists())

    def test_one_malformed_record_rolls_back_all_new_inserts(self):
        malformed_signal = make_career_signal(metadata={"bad": {"not", "json"}})

        with self.assertRaises(CareerSignalRepositoryError):
            self.repository.upsert_many(
                [
                    CareerSignalWrite(make_career_signal(signal_id="signal_good")),
                    CareerSignalWrite(malformed_signal),
                ]
            )

        self.assertEqual(self.repository.count(), 0)

    def test_one_malformed_record_does_not_alter_committed_rows(self):
        committed = make_career_signal(signal_id="signal_committed")

        self.repository.upsert_one(committed, persisted_at=CREATED_AT)

        with self.assertRaises(CareerSignalRepositoryError):
            self.repository.upsert_many(
                [
                    CareerSignalWrite(make_career_signal(signal_id="signal_new")),
                    CareerSignalWrite(
                        make_career_signal(
                            signal_id="signal_bad",
                            metadata={"bad": {"not", "json"}},
                        )
                    ),
                ]
            )

        self.assertEqual(self.repository.count(), 1)
        self.assertEqual(self.fetch_career_signal()["signal_id"], "signal_committed")


class CareerSignalIsolationTests(TemporaryCareerSignalDatabaseTestCase):
    def test_all_tests_use_explicit_temporary_database_paths(self):
        self.assertNotEqual(self.database_path, DEFAULT_DATABASE_FILE)

        self.repository.upsert_one(make_career_signal())

        self.assertTrue(self.database_path.exists())

    def test_tests_do_not_create_or_modify_configured_production_database(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = (
            DEFAULT_DATABASE_FILE.stat().st_mtime
            if before_exists
            else None
        )

        self.repository.upsert_one(make_career_signal())

        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)

        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)

    def test_serialize_career_signal_returns_json_not_python_repr(self):
        serialized_signal = serialize_career_signal(make_career_signal())

        self.assertIsInstance(json.loads(serialized_signal), dict)
        self.assertNotIn("CareerSignal(", serialized_signal)


if __name__ == "__main__":
    unittest.main()
