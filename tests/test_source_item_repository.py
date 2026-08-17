import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from src.config import DEFAULT_DATABASE_FILE
from src.database.connection import open_database_connection
from src.database.migrations import (
    MIGRATIONS_DIR,
    discover_migrations,
    get_applied_migrations,
    initialize_database,
)
from src.database.repositories.source_item_repository import (
    SourceItemRepository,
    SourceItemRepositoryError,
)
from src.database.source_identity import (
    canonicalize_url,
    fingerprint_raw_item,
)
from src.models import RawItem, SourceType


FIRST_SEEN_AT = "2026-07-21T00:00:00+00:00"
SECOND_SEEN_AT = "2026-07-22T00:00:00+00:00"


def make_raw_item(
    *,
    source_type: SourceType = SourceType.SEARCH_API,
    provider: str = "brave",
    external_id: str | None = None,
    title: str = "Strategy Role",
    organization: str = "Example Co",
    url: str = "https://example.com/jobs/123",
    published_at: str | None = "2026-07-20T00:00:00+00:00",
    raw_text: str = "A strategy role working with AI products.",
    metadata: dict | None = None,
) -> RawItem:
    item_metadata = {
        "provider": provider,
        "position": 1,
        "nested": {
            "language": "en",
            "tags": ["strategy", "AI"],
        },
    }

    if external_id is not None:
        item_metadata["external_id"] = external_id

    if metadata:
        item_metadata.update(metadata)

    return RawItem(
        source_type=source_type,
        title=title,
        organization=organization,
        url=url,
        published_at=published_at,
        raw_text=raw_text,
        metadata=item_metadata,
    )


class TemporaryDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "agentworkflow-test.db"
        self.repository = SourceItemRepository(database_path=self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def fetch_source_items(self) -> list[dict]:
        connection = open_database_connection(self.database_path)

        try:
            rows = connection.execute(
                "SELECT * FROM source_items ORDER BY source_item_id"
            ).fetchall()

            return [
                dict(row)
                for row in rows
            ]
        finally:
            connection.close()

    def fetch_source_item(self) -> dict:
        rows = self.fetch_source_items()
        self.assertEqual(len(rows), 1)
        return rows[0]


class SourceItemMigrationTests(TemporaryDatabaseTestCase):
    def test_migration_002_is_discovered_after_001(self):
        migrations = discover_migrations()

        self.assertEqual(
            [migration.version for migration in migrations[:2]],
            ["001", "002"],
        )

    def test_migration_002_applies_to_database_with_001(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_dir = root / "first"
            first_dir.mkdir()
            shutil.copy(
                MIGRATIONS_DIR / "001_initial_schema.sql",
                first_dir / "001_initial_schema.sql",
            )

            initialize_database(
                database_path=self.database_path,
                migrations_dir=first_dir,
            )
            applied = initialize_database(database_path=self.database_path)

        self.assertEqual(
            [migration.version for migration in applied],
            ["002", "003", "004", "005", "006", "007"],
        )

    def test_source_items_table_exists(self):
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            table = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'source_items'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertIsNotNone(table)

    def test_expected_columns_and_nullability_exist(self):
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            columns = {
                row["name"]: dict(row)
                for row in connection.execute("PRAGMA table_info(source_items)")
            }
        finally:
            connection.close()

        expected_columns = {
            "source_item_id",
            "fingerprint",
            "source_type",
            "provider",
            "external_id",
            "title",
            "organization",
            "url",
            "canonical_url",
            "published_at",
            "raw_text",
            "payload_json",
            "processing_status",
            "first_seen_at",
            "last_seen_at",
            "seen_count",
        }
        self.assertEqual(set(columns), expected_columns)
        self.assertEqual(columns["source_item_id"]["pk"], 1)
        self.assertEqual(columns["fingerprint"]["notnull"], 1)
        self.assertEqual(columns["source_type"]["notnull"], 1)
        self.assertEqual(columns["title"]["notnull"], 1)
        self.assertEqual(columns["payload_json"]["notnull"], 1)
        self.assertEqual(columns["processing_status"]["dflt_value"], "'pending'")
        self.assertEqual(columns["seen_count"]["dflt_value"], "1")
        self.assertEqual(columns["organization"]["notnull"], 0)
        self.assertEqual(columns["published_at"]["notnull"], 0)

    def test_expected_indexes_exist(self):
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            indexes = {
                row["name"]
                for row in connection.execute("PRAGMA index_list(source_items)")
            }
        finally:
            connection.close()

        self.assertIn("idx_source_items_source_type", indexes)
        self.assertIn("idx_source_items_processing_status", indexes)
        self.assertIn("idx_source_items_last_seen_at", indexes)
        self.assertIn("idx_source_items_canonical_url", indexes)

    def test_migration_002_is_recorded_exactly_once(self):
        initialize_database(database_path=self.database_path)
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            rows = connection.execute(
                """
                SELECT version, name
                FROM schema_migrations
                WHERE version = '002'
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "source_items")

    def test_repeated_initialization_remains_idempotent(self):
        first_applied = initialize_database(database_path=self.database_path)
        second_applied = initialize_database(database_path=self.database_path)

        self.assertEqual(
            [migration.version for migration in first_applied],
            ["001", "002", "003", "004", "005", "006", "007"],
        )
        self.assertEqual(second_applied, [])

    def test_pipeline_runs_still_exists_after_migration_002(self):
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            table = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'pipeline_runs'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertIsNotNone(table)


class SourceItemSerializationTests(TemporaryDatabaseTestCase):
    def test_complete_raw_item_data_is_stored_as_payload_json(self):
        raw_item = make_raw_item()

        self.repository.upsert_one(raw_item, seen_at=FIRST_SEEN_AT)
        row = self.fetch_source_item()
        payload = json.loads(row["payload_json"])

        self.assertEqual(payload["source_type"], raw_item.source_type.value)
        self.assertEqual(payload["title"], raw_item.title)
        self.assertEqual(payload["organization"], raw_item.organization)
        self.assertEqual(payload["url"], raw_item.url)
        self.assertEqual(payload["published_at"], raw_item.published_at)
        self.assertEqual(payload["raw_text"], raw_item.raw_text)
        self.assertEqual(payload["metadata"]["provider"], "brave")

    def test_nested_metadata_survives_json_round_trip(self):
        raw_item = make_raw_item(
            metadata={
                "nested": {
                    "levels": [
                        {"name": "one"},
                        {"name": "two"},
                    ]
                }
            }
        )

        self.repository.upsert_one(raw_item)
        payload = json.loads(self.fetch_source_item()["payload_json"])

        self.assertEqual(
            payload["metadata"]["nested"]["levels"][1]["name"],
            "two",
        )

    def test_enums_serialize_to_string_values(self):
        raw_item = make_raw_item(source_type=SourceType.RSS, provider="rss")

        self.repository.upsert_one(raw_item)
        payload = json.loads(self.fetch_source_item()["payload_json"])

        self.assertEqual(payload["source_type"], "rss")

    def test_nullable_published_at_is_accepted(self):
        raw_item = make_raw_item(published_at=None)

        self.repository.upsert_one(raw_item)
        row = self.fetch_source_item()
        payload = json.loads(row["payload_json"])

        self.assertIsNone(row["published_at"])
        self.assertIsNone(payload["published_at"])

    def test_unicode_content_survives_round_trip(self):
        raw_item = make_raw_item(
            title="战略岗位",
            raw_text="支持 AI 战略与市场研究",
            metadata={"note": "中文内容"},
        )

        self.repository.upsert_one(raw_item)
        payload = json.loads(self.fetch_source_item()["payload_json"])

        self.assertEqual(payload["title"], "战略岗位")
        self.assertEqual(payload["raw_text"], "支持 AI 战略与市场研究")
        self.assertEqual(payload["metadata"]["note"], "中文内容")


class SourceItemIdentityTests(TemporaryDatabaseTestCase):
    def test_identical_raw_items_from_same_provenance_have_same_fingerprint(self):
        first = make_raw_item()
        second = make_raw_item()

        self.assertEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_tracking_only_url_differences_deduplicate(self):
        first = make_raw_item(url="https://Example.com/jobs/123?utm_source=google")
        second = make_raw_item(url="https://example.com/jobs/123")

        self.assertEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_meaningful_query_parameter_differences_remain_distinct(self):
        first = make_raw_item(url="https://example.com/jobs?id=123")
        second = make_raw_item(url="https://example.com/jobs?id=456")

        self.assertNotEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_same_url_from_different_providers_remains_distinct(self):
        first = make_raw_item(provider="brave")
        second = make_raw_item(provider="rss")

        self.assertNotEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_same_url_from_different_source_types_remains_distinct(self):
        first = make_raw_item(source_type=SourceType.SEARCH_API)
        second = make_raw_item(source_type=SourceType.RSS)

        self.assertNotEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_fallback_identity_works_without_external_id_or_url(self):
        raw_item = make_raw_item(url="", metadata={"provider": "manual"})

        fingerprint = fingerprint_raw_item(raw_item)

        self.assertEqual(len(fingerprint), 64)


class SourceItemRepositoryWriteTests(TemporaryDatabaseTestCase):
    def test_new_raw_item_inserts_one_row(self):
        summary = self.repository.upsert_one(make_raw_item(), seen_at=FIRST_SEEN_AT)

        self.assertEqual(self.repository.count(), 1)
        self.assertEqual(summary.inserted_count, 1)

    def test_reupserting_same_identity_keeps_one_row(self):
        raw_item = make_raw_item()

        self.repository.upsert_one(raw_item, seen_at=FIRST_SEEN_AT)
        self.repository.upsert_one(raw_item, seen_at=SECOND_SEEN_AT)

        self.assertEqual(self.repository.count(), 1)

    def test_source_item_id_remains_stable(self):
        raw_item = make_raw_item()

        self.repository.upsert_one(raw_item, seen_at=FIRST_SEEN_AT)
        first_id = self.fetch_source_item()["source_item_id"]
        self.repository.upsert_one(raw_item, seen_at=SECOND_SEEN_AT)
        second_id = self.fetch_source_item()["source_item_id"]

        self.assertEqual(first_id, second_id)

    def test_first_seen_at_remains_stable(self):
        raw_item = make_raw_item()

        self.repository.upsert_one(raw_item, seen_at=FIRST_SEEN_AT)
        self.repository.upsert_one(raw_item, seen_at=SECOND_SEEN_AT)

        self.assertEqual(self.fetch_source_item()["first_seen_at"], FIRST_SEEN_AT)

    def test_last_seen_at_updates(self):
        raw_item = make_raw_item()

        self.repository.upsert_one(raw_item, seen_at=FIRST_SEEN_AT)
        self.repository.upsert_one(raw_item, seen_at=SECOND_SEEN_AT)

        self.assertEqual(self.fetch_source_item()["last_seen_at"], SECOND_SEEN_AT)

    def test_seen_count_increments_from_1_to_2(self):
        raw_item = make_raw_item()

        self.repository.upsert_one(raw_item, seen_at=FIRST_SEEN_AT)
        self.repository.upsert_one(raw_item, seen_at=SECOND_SEEN_AT)

        self.assertEqual(self.fetch_source_item()["seen_count"], 2)

    def test_mutable_values_and_payload_json_refresh_on_reupsert(self):
        original = make_raw_item(external_id="stable-1", title="Old title")
        updated = make_raw_item(
            external_id="stable-1",
            title="New title",
            raw_text="Updated body",
        )

        self.repository.upsert_one(original, seen_at=FIRST_SEEN_AT)
        self.repository.upsert_one(updated, seen_at=SECOND_SEEN_AT)
        row = self.fetch_source_item()
        payload = json.loads(row["payload_json"])

        self.assertEqual(row["title"], "New title")
        self.assertEqual(payload["raw_text"], "Updated body")

    def test_processing_status_is_not_reset_during_update(self):
        raw_item = make_raw_item()
        fingerprint = fingerprint_raw_item(raw_item)

        self.repository.upsert_one(raw_item, seen_at=FIRST_SEEN_AT)
        connection = open_database_connection(self.database_path)

        try:
            connection.execute(
                """
                UPDATE source_items
                SET processing_status = 'held'
                WHERE fingerprint = ?
                """,
                (fingerprint,),
            )
            connection.commit()
        finally:
            connection.close()

        self.repository.upsert_one(raw_item, seen_at=SECOND_SEEN_AT)

        self.assertEqual(self.fetch_source_item()["processing_status"], "held")

    def test_get_by_fingerprint_returns_stored_row(self):
        raw_item = make_raw_item()
        fingerprint = fingerprint_raw_item(raw_item)

        self.repository.upsert_one(raw_item)
        row = self.repository.get_by_fingerprint(fingerprint)

        self.assertIsNotNone(row)
        self.assertEqual(row["fingerprint"], fingerprint)

    def test_count_returns_expected_row_count(self):
        self.repository.upsert_many(
            [
                make_raw_item(url="https://example.com/jobs/1"),
                make_raw_item(url="https://example.com/jobs/2"),
            ]
        )

        self.assertEqual(self.repository.count(), 2)


class SourceItemBatchTests(TemporaryDatabaseTestCase):
    def test_duplicate_fingerprints_inside_one_batch_are_written_once(self):
        raw_items = [
            make_raw_item(title="First"),
            make_raw_item(title="Second"),
            make_raw_item(title="Third"),
        ]

        self.repository.upsert_many(raw_items, seen_at=FIRST_SEEN_AT)

        self.assertEqual(self.repository.count(), 1)

    def test_duplicate_fingerprints_inside_one_batch_increment_seen_count_once(self):
        raw_item = make_raw_item()

        self.repository.upsert_one(raw_item, seen_at=FIRST_SEEN_AT)
        self.repository.upsert_many(
            [
                make_raw_item(title="Batch one"),
                make_raw_item(title="Batch two"),
            ],
            seen_at=SECOND_SEEN_AT,
        )

        self.assertEqual(self.fetch_source_item()["seen_count"], 2)

    def test_summary_reports_received_unique_inserted_and_updated_counts(self):
        existing = make_raw_item(url="https://example.com/jobs/existing")
        self.repository.upsert_one(existing, seen_at=FIRST_SEEN_AT)

        summary = self.repository.upsert_many(
            [
                existing,
                make_raw_item(url="https://example.com/jobs/new"),
                make_raw_item(url="https://example.com/jobs/new", title="New latest"),
            ],
            seen_at=SECOND_SEEN_AT,
        )

        self.assertEqual(summary.received_count, 3)
        self.assertEqual(summary.unique_count, 2)
        self.assertEqual(summary.inserted_count, 1)
        self.assertEqual(summary.updated_count, 1)

    def test_empty_batch_is_handled_safely(self):
        summary = self.repository.upsert_many([])

        self.assertEqual(summary.received_count, 0)
        self.assertEqual(summary.unique_count, 0)
        self.assertEqual(summary.inserted_count, 0)
        self.assertEqual(summary.updated_count, 0)
        self.assertEqual(self.repository.count(), 0)

    def test_one_malformed_item_rolls_back_entire_new_batch(self):
        malformed = make_raw_item(metadata={"bad": {"not", "json"}})

        with self.assertRaises(SourceItemRepositoryError):
            self.repository.upsert_many(
                [
                    make_raw_item(url="https://example.com/jobs/good"),
                    malformed,
                ]
            )

        self.assertEqual(self.repository.count(), 0)

    def test_failed_batch_does_not_alter_previously_committed_rows(self):
        committed = make_raw_item(url="https://example.com/jobs/committed")

        self.repository.upsert_one(committed, seen_at=FIRST_SEEN_AT)

        with self.assertRaises(SourceItemRepositoryError):
            self.repository.upsert_many(
                [
                    make_raw_item(url="https://example.com/jobs/new"),
                    make_raw_item(metadata={"bad": {"not", "json"}}),
                ]
            )

        self.assertEqual(self.repository.count(), 1)
        self.assertEqual(self.fetch_source_item()["seen_count"], 1)


class SourceItemIsolationTests(TemporaryDatabaseTestCase):
    def test_tests_use_explicit_temporary_database_paths(self):
        self.assertNotEqual(self.database_path, DEFAULT_DATABASE_FILE)
        self.repository.upsert_one(make_raw_item())

        self.assertTrue(self.database_path.exists())

    def test_tests_do_not_create_or_modify_configured_production_database(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = (
            DEFAULT_DATABASE_FILE.stat().st_mtime
            if before_exists
            else None
        )

        self.repository.upsert_one(make_raw_item())

        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)

        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)

    def test_canonicalize_url_documents_tracking_and_query_behavior(self):
        self.assertEqual(
            canonicalize_url("https://Example.com/jobs/123?utm_source=google#top"),
            "https://example.com/jobs/123",
        )
        self.assertNotEqual(
            canonicalize_url("https://example.com/jobs?id=123"),
            canonicalize_url("https://example.com/jobs?id=456"),
        )


if __name__ == "__main__":
    unittest.main()
