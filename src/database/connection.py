from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from src.config import get_database_path


def open_database_connection(
    database_path: str | Path | None = None,
) -> sqlite3.Connection:
    """
    Open a short-lived SQLite connection for AgentWorkflow.
    """

    resolved_path = _resolve_connection_path(database_path)

    if resolved_path != ":memory:":
        Path(resolved_path).parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(resolved_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    foreign_keys_enabled = connection.execute(
        "PRAGMA foreign_keys"
    ).fetchone()[0]

    if foreign_keys_enabled != 1:
        connection.close()
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled.")

    return connection


@contextmanager
def database_connection(
    database_path: str | Path | None = None,
) -> Iterator[sqlite3.Connection]:
    """
    Open, commit or roll back, and always close a SQLite connection.
    """

    connection = open_database_connection(database_path)

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _resolve_connection_path(database_path: str | Path | None) -> str:
    if str(database_path).strip() == ":memory:":
        return ":memory:"

    return str(get_database_path(database_path))
