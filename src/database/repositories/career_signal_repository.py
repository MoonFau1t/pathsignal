from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Iterable
import sqlite3

from src.database.connection import database_connection, open_database_connection
from src.models import CareerSignal, utc_now_iso


@dataclass(frozen=True)
class CareerSignalWrite:
    career_signal: CareerSignal
    source_item_id: int | None = None


@dataclass(frozen=True)
class CareerSignalUpsertSummary:
    received_count: int
    unique_count: int
    inserted_count: int
    updated_count: int


@dataclass(frozen=True)
class _PreparedCareerSignal:
    signal_id: str
    source_item_id: int | None
    category: str
    title: str
    organization: str | None
    url: str | None
    published_at: str | None
    summary: str | None
    source_type: str
    relevance_score: float | None
    payload_json: str


class CareerSignalRepositoryError(Exception):
    """
    Raised when CareerSignal persistence fails.
    """


class CareerSignalRepository:
    """
    Repository for persisting normalized CareerSignal snapshots.
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = database_path

    def upsert_one(
        self,
        career_signal: CareerSignal,
        source_item_id: int | None = None,
        persisted_at: str | None = None,
    ) -> CareerSignalUpsertSummary:
        return self.upsert_many(
            [
                CareerSignalWrite(
                    career_signal=career_signal,
                    source_item_id=source_item_id,
                )
            ],
            persisted_at=persisted_at,
        )

    def upsert_many(
        self,
        records: Iterable[CareerSignalWrite],
        persisted_at: str | None = None,
    ) -> CareerSignalUpsertSummary:
        record_list = list(records)
        received_count = len(record_list)

        try:
            prepared_by_signal_id = _prepare_unique_records(record_list)
        except CareerSignalRepositoryError:
            raise
        except Exception as error:
            raise CareerSignalRepositoryError(
                "Failed to prepare career_signals batch."
            ) from error

        unique_count = len(prepared_by_signal_id)

        if unique_count == 0:
            return CareerSignalUpsertSummary(
                received_count=received_count,
                unique_count=0,
                inserted_count=0,
                updated_count=0,
            )

        persisted_time = persisted_at or utc_now_iso()
        inserted_count = 0
        updated_count = 0

        try:
            with database_connection(self.database_path) as connection:
                for prepared_signal in prepared_by_signal_id.values():
                    if _upsert_prepared_signal(
                        connection=connection,
                        prepared_signal=prepared_signal,
                        persisted_at=persisted_time,
                    ):
                        inserted_count += 1
                    else:
                        updated_count += 1
        except CareerSignalRepositoryError:
            raise
        except Exception as error:
            raise CareerSignalRepositoryError(
                "Failed to upsert career_signals batch."
            ) from error

        return CareerSignalUpsertSummary(
            received_count=received_count,
            unique_count=unique_count,
            inserted_count=inserted_count,
            updated_count=updated_count,
        )

    def get_by_signal_id(self, signal_id: str) -> dict | None:
        connection = open_database_connection(self.database_path)

        try:
            row = connection.execute(
                """
                SELECT *
                FROM career_signals
                WHERE signal_id = ?
                """,
                (signal_id,),
            ).fetchone()

            return dict(row) if row is not None else None
        finally:
            connection.close()

    def count(self) -> int:
        connection = open_database_connection(self.database_path)

        try:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM career_signals"
                ).fetchone()[0]
            )
        finally:
            connection.close()


def serialize_career_signal(career_signal: CareerSignal) -> str:
    """
    Serialize a CareerSignal snapshot as deterministic JSON.
    """

    payload = _to_json_value(career_signal.to_dict())

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _prepare_unique_records(
    records: list[CareerSignalWrite],
) -> dict[str, _PreparedCareerSignal]:
    prepared_by_signal_id: dict[str, _PreparedCareerSignal] = {}

    for record in records:
        prepared_signal = _prepare_career_signal(record)
        existing_signal = prepared_by_signal_id.get(prepared_signal.signal_id)

        if existing_signal is not None:
            _validate_compatible_source_item_ids(
                existing_signal.source_item_id,
                prepared_signal.source_item_id,
            )
            prepared_signal = _merge_duplicate_prepared_signal(
                existing_signal=existing_signal,
                incoming_signal=prepared_signal,
            )

        prepared_by_signal_id[prepared_signal.signal_id] = prepared_signal

    return prepared_by_signal_id


def _prepare_career_signal(record: CareerSignalWrite) -> _PreparedCareerSignal:
    career_signal = record.career_signal

    return _PreparedCareerSignal(
        signal_id=career_signal.signal_id,
        source_item_id=record.source_item_id,
        category=_enum_value(career_signal.category),
        title=career_signal.title,
        organization=_empty_to_none(career_signal.organization),
        url=_empty_to_none(career_signal.url),
        published_at=career_signal.published_at,
        summary=_empty_to_none(career_signal.summary),
        source_type=_enum_value(career_signal.source_type),
        relevance_score=career_signal.relevance_score,
        payload_json=serialize_career_signal(career_signal),
    )


def _upsert_prepared_signal(
    connection: sqlite3.Connection,
    prepared_signal: _PreparedCareerSignal,
    persisted_at: str,
) -> bool:
    existing_row = connection.execute(
        """
        SELECT career_signal_row_id, source_item_id
        FROM career_signals
        WHERE signal_id = ?
        """,
        (prepared_signal.signal_id,),
    ).fetchone()

    if existing_row is None:
        connection.execute(
            """
            INSERT INTO career_signals (
                signal_id,
                source_item_id,
                category,
                title,
                organization,
                url,
                published_at,
                summary,
                source_type,
                relevance_score,
                payload_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prepared_signal.signal_id,
                prepared_signal.source_item_id,
                prepared_signal.category,
                prepared_signal.title,
                prepared_signal.organization,
                prepared_signal.url,
                prepared_signal.published_at,
                prepared_signal.summary,
                prepared_signal.source_type,
                prepared_signal.relevance_score,
                prepared_signal.payload_json,
                persisted_at,
                persisted_at,
            ),
        )
        return True

    source_item_id = _resolve_update_source_item_id(
        existing_source_item_id=existing_row["source_item_id"],
        incoming_source_item_id=prepared_signal.source_item_id,
        signal_id=prepared_signal.signal_id,
    )

    connection.execute(
        """
        UPDATE career_signals
        SET
            source_item_id = ?,
            category = ?,
            title = ?,
            organization = ?,
            url = ?,
            published_at = ?,
            summary = ?,
            source_type = ?,
            relevance_score = ?,
            payload_json = ?,
            updated_at = ?
        WHERE signal_id = ?
        """,
        (
            source_item_id,
            prepared_signal.category,
            prepared_signal.title,
            prepared_signal.organization,
            prepared_signal.url,
            prepared_signal.published_at,
            prepared_signal.summary,
            prepared_signal.source_type,
            prepared_signal.relevance_score,
            prepared_signal.payload_json,
            persisted_at,
            prepared_signal.signal_id,
        ),
    )

    return False


def _merge_duplicate_prepared_signal(
    existing_signal: _PreparedCareerSignal,
    incoming_signal: _PreparedCareerSignal,
) -> _PreparedCareerSignal:
    if incoming_signal.source_item_id is not None:
        return incoming_signal

    return _PreparedCareerSignal(
        signal_id=incoming_signal.signal_id,
        source_item_id=existing_signal.source_item_id,
        category=incoming_signal.category,
        title=incoming_signal.title,
        organization=incoming_signal.organization,
        url=incoming_signal.url,
        published_at=incoming_signal.published_at,
        summary=incoming_signal.summary,
        source_type=incoming_signal.source_type,
        relevance_score=incoming_signal.relevance_score,
        payload_json=incoming_signal.payload_json,
    )


def _resolve_update_source_item_id(
    existing_source_item_id: int | None,
    incoming_source_item_id: int | None,
    signal_id: str,
) -> int | None:
    if existing_source_item_id is None:
        return incoming_source_item_id

    if incoming_source_item_id is None:
        return existing_source_item_id

    if existing_source_item_id == incoming_source_item_id:
        return existing_source_item_id

    raise CareerSignalRepositoryError(
        "Conflicting source_item_id for "
        f"CareerSignal {signal_id!r}: existing={existing_source_item_id}, "
        f"incoming={incoming_source_item_id}."
    )


def _validate_compatible_source_item_ids(
    first_source_item_id: int | None,
    second_source_item_id: int | None,
) -> None:
    if (
        first_source_item_id is not None
        and second_source_item_id is not None
        and first_source_item_id != second_source_item_id
    ):
        raise CareerSignalRepositoryError(
            "Conflicting source_item_id values for duplicate CareerSignal "
            "records in one batch."
        )


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)

    return str(value)


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None

    stripped_value = str(value).strip()

    return stripped_value or None


def _to_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value

    if is_dataclass(value) and not isinstance(value, type):
        return _to_json_value(asdict(value))

    if isinstance(value, dict):
        return {
            str(key): _to_json_value(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            _to_json_value(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            _to_json_value(item)
            for item in value
        ]

    return value
