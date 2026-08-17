from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
import sys

from src.config import get_database_path
from src.database.connection import open_database_connection
from src.models import utc_now_iso


MIGRATION_FILE_PATTERN = re.compile(r"^(?P<version>\d{3})_(?P<name>.+)\.sql$")
MIGRATIONS_DIR = Path(__file__).resolve().parent / "sql"


class MigrationError(Exception):
    """
    Raised when a database migration cannot be applied.
    """


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path


def discover_migrations(migrations_dir: str | Path = MIGRATIONS_DIR) -> list[Migration]:
    """
    Return sorted SQL migrations matching NNN_description.sql.
    """

    migration_dir = Path(migrations_dir)

    if not migration_dir.exists():
        return []

    migrations: list[Migration] = []
    seen_versions: set[str] = set()

    for path in sorted(migration_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".sql":
            continue

        match = MIGRATION_FILE_PATTERN.match(path.name)

        if match is None:
            continue

        version = match.group("version")
        name = match.group("name")

        if version in seen_versions:
            raise MigrationError(
                f"Duplicate migration version {version!r} in {migration_dir}."
            )

        seen_versions.add(version)
        migrations.append(
            Migration(
                version=version,
                name=name,
                path=path,
            )
        )

    return migrations


def get_applied_migrations(connection: sqlite3.Connection) -> set[str]:
    """
    Return migration versions already recorded in schema_migrations.
    """

    _bootstrap_schema_migrations(connection)

    rows = connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()

    return {
        str(row["version"])
        for row in rows
    }


def apply_migrations(
    connection: sqlite3.Connection,
    migrations_dir: str | Path = MIGRATIONS_DIR,
) -> list[Migration]:
    """
    Apply pending migrations and return the migrations newly applied.
    """

    _bootstrap_schema_migrations(connection)
    applied_versions = get_applied_migrations(connection)
    newly_applied: list[Migration] = []

    for migration in discover_migrations(migrations_dir):
        if migration.version in applied_versions:
            continue

        _apply_one_migration(connection, migration)
        applied_versions.add(migration.version)
        newly_applied.append(migration)

    return newly_applied


def initialize_database(
    database_path: str | Path | None = None,
    migrations_dir: str | Path = MIGRATIONS_DIR,
) -> list[Migration]:
    """
    Initialize the SQLite database and apply pending migrations.
    """

    connection = open_database_connection(database_path)

    try:
        return apply_migrations(
            connection=connection,
            migrations_dir=migrations_dir,
        )
    finally:
        connection.close()


def main() -> int:
    """
    CLI entrypoint for initializing the configured SQLite database.
    """

    database_path = get_database_path()

    try:
        applied_migrations = initialize_database(database_path=database_path)
    except Exception as error:
        print(f"Database initialization failed: {error}", file=sys.stderr)
        return 1

    print(f"Database path: {database_path}")

    if applied_migrations:
        versions = ", ".join(migration.version for migration in applied_migrations)
        print(f"Applied migrations: {versions}")
    else:
        print("Applied migrations: none")

    return 0


def _bootstrap_schema_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _apply_one_migration(
    connection: sqlite3.Connection,
    migration: Migration,
) -> None:
    sql_text = migration.path.read_text(encoding="utf-8")
    statements = _split_sql_statements(sql_text)

    try:
        connection.execute("BEGIN")

        for statement in statements:
            connection.execute(statement)

        connection.execute(
            """
            INSERT INTO schema_migrations (version, name, applied_at)
            VALUES (?, ?, ?)
            """,
            (migration.version, migration.name, utc_now_iso()),
        )
        connection.commit()
    except Exception as error:
        connection.rollback()
        raise MigrationError(
            "Failed to apply migration "
            f"{migration.version} ({migration.path.name}): {error}"
        ) from error


def _split_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    current_statement: list[str] = []

    for line in sql_text.splitlines():
        stripped_line = line.strip()

        if not stripped_line or stripped_line.startswith("--"):
            continue

        current_statement.append(line)
        candidate = "\n".join(current_statement)

        if sqlite3.complete_statement(candidate):
            statement = candidate.strip()

            if _contains_transaction_control(statement):
                raise MigrationError(
                    "Migration files must not contain transaction-control statements."
                )

            statements.append(statement)
            current_statement = []

    trailing_statement = "\n".join(current_statement).strip()

    if trailing_statement:
        raise MigrationError("Migration SQL contains an incomplete statement.")

    return statements


def _contains_transaction_control(statement: str) -> bool:
    normalized_statement = statement.strip().rstrip(";").strip().upper()
    first_word = normalized_statement.split(maxsplit=1)[0] if normalized_statement else ""

    return first_word in {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"}


if __name__ == "__main__":
    raise SystemExit(main())
