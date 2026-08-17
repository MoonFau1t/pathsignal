import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.config import DEFAULT_DATABASE_FILE, PROJECT_ROOT, get_database_path
from src.database.connection import database_connection, open_database_connection
from src.database.migrations import (
    MigrationError,
    discover_migrations,
    get_applied_migrations,
    initialize_database,
)


class DatabasePathTests(unittest.TestCase):
    def test_default_database_path_is_project_data_database(self):
        with patch.dict(os.environ, {"AGENTWORKFLOW_DB_PATH": ""}, clear=False):
            self.assertEqual(
                get_database_path(),
                PROJECT_ROOT / "data" / "agentworkflow.db",
            )

    def test_relative_configured_database_path_resolves_under_project_root(self):
        with patch.dict(
            os.environ,
            {"AGENTWORKFLOW_DB_PATH": "data/test-agentworkflow.db"},
            clear=False,
        ):
            self.assertEqual(
                get_database_path(),
                PROJECT_ROOT / "data" / "test-agentworkflow.db",
            )

    def test_absolute_explicit_database_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "explicit.db"

            self.assertEqual(
                get_database_path(database_path),
                database_path,
            )


class DatabaseConnectionTests(unittest.TestCase):
    def test_parent_directory_is_created_for_nested_database_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "nested" / "db" / "test.db"

            connection = open_database_connection(database_path)
            connection.close()

            self.assertTrue(database_path.parent.exists())
            self.assertTrue(database_path.exists())

    def test_open_database_connection_returns_sqlite_connection(self):
        connection = open_database_connection(":memory:")

        try:
            self.assertIsInstance(connection, sqlite3.Connection)
        finally:
            connection.close()

    def test_row_factory_is_sqlite_row(self):
        connection = open_database_connection(":memory:")

        try:
            self.assertIs(connection.row_factory, sqlite3.Row)
            row = connection.execute("SELECT 1 AS value").fetchone()
            self.assertEqual(row["value"], 1)
        finally:
            connection.close()

    def test_foreign_keys_pragma_is_enabled(self):
        connection = open_database_connection(":memory:")

        try:
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            self.assertEqual(foreign_keys, 1)
        finally:
            connection.close()

    def test_foreign_key_enforcement_rejects_invalid_child_row(self):
        connection = open_database_connection(":memory:")

        try:
            connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            connection.execute(
                """
                CREATE TABLE child (
                    id INTEGER PRIMARY KEY,
                    parent_id INTEGER NOT NULL REFERENCES parent(id)
                )
                """
            )

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO child (id, parent_id) VALUES (1, 999)"
                )
        finally:
            connection.close()

    def test_context_manager_commits_successful_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "commit.db"

            with database_connection(database_path) as connection:
                connection.execute("CREATE TABLE items (name TEXT)")
                connection.execute("INSERT INTO items (name) VALUES ('kept')")

            connection = open_database_connection(database_path)

            try:
                row = connection.execute("SELECT name FROM items").fetchone()
            finally:
                connection.close()

            self.assertEqual(row["name"], "kept")

    def test_context_manager_rolls_back_on_exception(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "rollback.db"

            connection = open_database_connection(database_path)

            try:
                connection.execute("CREATE TABLE items (name TEXT)")
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(RuntimeError):
                with database_connection(database_path) as connection:
                    connection.execute("INSERT INTO items (name) VALUES ('lost')")
                    raise RuntimeError("force rollback")

            connection = open_database_connection(database_path)

            try:
                count = connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(count, 0)

    def test_context_manager_closes_connection_after_exit(self):
        connection_holder = []

        with database_connection(":memory:") as connection:
            connection_holder.append(connection)
            connection.execute("SELECT 1")

        with self.assertRaises(sqlite3.ProgrammingError):
            connection_holder[0].execute("SELECT 1")


class DatabaseMigrationTests(unittest.TestCase):
    def test_initialize_database_creates_schema_migrations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "schema.db"

            initialize_database(database_path=database_path)

            connection = open_database_connection(database_path)

            try:
                table = connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'schema_migrations'
                    """
                ).fetchone()
            finally:
                connection.close()

            self.assertIsNotNone(table)

    def test_initialize_database_applies_initial_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "initial.db"

            initialize_database(database_path=database_path)

            connection = open_database_connection(database_path)

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

    def test_applied_migration_001_is_recorded_exactly_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "recorded.db"

            initialize_database(database_path=database_path)
            initialize_database(database_path=database_path)

            connection = open_database_connection(database_path)

            try:
                rows = connection.execute(
                    """
                    SELECT version, name
                    FROM schema_migrations
                    WHERE version = '001'
                    """
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], "initial_schema")

    def test_repeated_initialization_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "idempotent.db"

            first_applied = initialize_database(database_path=database_path)
            second_applied = initialize_database(database_path=database_path)

        self.assertEqual(
            [migration.version for migration in first_applied],
            ["001", "002", "003", "004", "005", "006", "007"],
        )
        self.assertEqual(second_applied, [])

    def test_migration_discovery_orders_files_lexicographically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            migrations_dir = Path(temp_dir)
            (migrations_dir / "002_second.sql").write_text("SELECT 2;", encoding="utf-8")
            (migrations_dir / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
            (migrations_dir / "notes.txt").write_text("ignored", encoding="utf-8")

            migrations = discover_migrations(migrations_dir)

            self.assertEqual(
                [migration.version for migration in migrations],
                ["001", "002"],
            )

    def test_duplicate_migration_versions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            migrations_dir = Path(temp_dir)
            (migrations_dir / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
            (migrations_dir / "001_duplicate.sql").write_text(
                "SELECT 2;",
                encoding="utf-8",
            )

            with self.assertRaises(MigrationError):
                discover_migrations(migrations_dir)

    def test_failing_migration_rolls_back_and_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "failure.db"
            migrations_dir = root / "migrations"
            migrations_dir.mkdir()
            (migrations_dir / "001_create_success.sql").write_text(
                "CREATE TABLE successful_table (id INTEGER PRIMARY KEY);",
                encoding="utf-8",
            )
            (migrations_dir / "002_fail_after_create.sql").write_text(
                """
                CREATE TABLE should_not_exist (id INTEGER PRIMARY KEY);
                INSERT INTO missing_table (id) VALUES (1);
                """,
                encoding="utf-8",
            )

            with self.assertRaises(MigrationError):
                initialize_database(
                    database_path=database_path,
                    migrations_dir=migrations_dir,
                )

            connection = open_database_connection(database_path)

            try:
                applied_versions = get_applied_migrations(connection)
                failed_table = connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'should_not_exist'
                    """
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(applied_versions, {"001"})
            self.assertIsNone(failed_table)

    def test_database_tests_use_explicit_temp_paths_not_production_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "isolated.db"

            self.assertNotEqual(database_path, DEFAULT_DATABASE_FILE)
            initialize_database(database_path=database_path)

            self.assertTrue(database_path.exists())


if __name__ == "__main__":
    unittest.main()
