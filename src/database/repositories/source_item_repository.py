from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import sqlite3

from src.database.connection import database_connection, open_database_connection
from src.database.migrations import initialize_database
from src.database.source_identity import (
    CROSS_METHOD_URL_IDENTITY_SOURCE_TYPES,
    canonicalize_url,
    extract_external_id,
    extract_provider,
    fingerprint_raw_item,
    serialize_raw_item,
)
from src.models import RawItem, utc_now_iso


@dataclass(frozen=True)
class SourceItemUpsertSummary:
    received_count: int
    unique_count: int
    inserted_count: int
    updated_count: int


@dataclass(frozen=True)
class SourceItemPersistenceResult:
    source_item_id: int
    fingerprint: str
    created_new: bool


@dataclass(frozen=True)
class _PreparedSourceItem:
    fingerprint: str
    source_type: str
    provider: str | None
    external_id: str | None
    title: str
    organization: str | None
    url: str | None
    canonical_url: str | None
    published_at: str | None
    raw_text: str | None
    payload_json: str


class SourceItemRepositoryError(Exception):
    """
    Raised when SourceItem persistence fails.
    """


class SourceItemRepository:
    """
    Repository for persisting RawItem snapshots into source_items.
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = database_path

    def upsert_one(
        self,
        raw_item: RawItem,
        seen_at: str | None = None,
    ) -> SourceItemUpsertSummary:
        return self.upsert_many([raw_item], seen_at=seen_at)

    def upsert_many(
        self,
        raw_items: Iterable[RawItem],
        seen_at: str | None = None,
    ) -> SourceItemUpsertSummary:
        raw_item_list = list(raw_items)
        received_count = len(raw_item_list)

        try:
            prepared_by_fingerprint: dict[str, _PreparedSourceItem] = {}

            for raw_item in raw_item_list:
                prepared_item = _prepare_source_item(raw_item)
                prepared_by_fingerprint[prepared_item.fingerprint] = prepared_item

            unique_count = len(prepared_by_fingerprint)

            if unique_count == 0:
                return SourceItemUpsertSummary(
                    received_count=received_count,
                    unique_count=0,
                    inserted_count=0,
                    updated_count=0,
                )

            initialize_database(database_path=self.database_path)
            sighting_time = seen_at or utc_now_iso()
            inserted_count = 0
            updated_count = 0

            with database_connection(self.database_path) as connection:
                for prepared_item in prepared_by_fingerprint.values():
                    if _upsert_prepared_item(
                        connection=connection,
                        prepared_item=prepared_item,
                        seen_at=sighting_time,
                    ):
                        inserted_count += 1
                    else:
                        updated_count += 1

            return SourceItemUpsertSummary(
                received_count=received_count,
                unique_count=unique_count,
                inserted_count=inserted_count,
                updated_count=updated_count,
            )
        except Exception as error:
            raise SourceItemRepositoryError(
                "Failed to upsert source_items batch."
            ) from error

    def upsert_one_with_outcome(
        self,
        raw_item: RawItem,
        seen_at: str | None = None,
    ) -> SourceItemPersistenceResult:
        try:
            prepared_item = _prepare_source_item(raw_item)
            initialize_database(database_path=self.database_path)

            with database_connection(self.database_path) as connection:
                created_new = _upsert_prepared_item(
                    connection=connection,
                    prepared_item=prepared_item,
                    seen_at=seen_at or utc_now_iso(),
                )
                row = connection.execute(
                    """
                    SELECT source_item_id, fingerprint
                    FROM source_items
                    WHERE fingerprint = ?
                    """,
                    (prepared_item.fingerprint,),
                ).fetchone()
                if row is None:
                    raise SourceItemRepositoryError(
                        "Persisted SourceItem could not be reloaded."
                    )

                return SourceItemPersistenceResult(
                    source_item_id=int(row["source_item_id"]),
                    fingerprint=str(row["fingerprint"]),
                    created_new=created_new,
                )
        except SourceItemRepositoryError:
            raise
        except Exception as error:
            raise SourceItemRepositoryError(
                "Failed to upsert SourceItem with persistence outcome."
            ) from error

    def get_by_fingerprint(self, fingerprint: str) -> dict | None:
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            row = connection.execute(
                """
                SELECT *
                FROM source_items
                WHERE fingerprint = ?
                """,
                (fingerprint,),
            ).fetchone()

            return dict(row) if row is not None else None
        finally:
            connection.close()

    def count(self) -> int:
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM source_items"
                ).fetchone()[0]
            )
        finally:
            connection.close()


def _prepare_source_item(raw_item: RawItem) -> _PreparedSourceItem:
    return _PreparedSourceItem(
        fingerprint=fingerprint_raw_item(raw_item),
        source_type=_source_type_value(raw_item),
        provider=_empty_to_none(extract_provider(raw_item)),
        external_id=_empty_to_none(extract_external_id(raw_item)),
        title=raw_item.title,
        organization=_empty_to_none(raw_item.organization),
        url=_empty_to_none(raw_item.url),
        canonical_url=canonicalize_url(raw_item.url),
        published_at=raw_item.published_at,
        raw_text=raw_item.raw_text,
        payload_json=serialize_raw_item(raw_item),
    )


def _upsert_prepared_item(
    connection: sqlite3.Connection,
    prepared_item: _PreparedSourceItem,
    seen_at: str,
) -> bool:
    existing_row = _find_existing_source_item(connection, prepared_item)

    if existing_row is None:
        connection.execute(
            """
            INSERT INTO source_items (
                fingerprint,
                source_type,
                provider,
                external_id,
                title,
                organization,
                url,
                canonical_url,
                published_at,
                raw_text,
                payload_json,
                first_seen_at,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prepared_item.fingerprint,
                prepared_item.source_type,
                prepared_item.provider,
                prepared_item.external_id,
                prepared_item.title,
                prepared_item.organization,
                prepared_item.url,
                prepared_item.canonical_url,
                prepared_item.published_at,
                prepared_item.raw_text,
                prepared_item.payload_json,
                seen_at,
                seen_at,
            ),
        )
        return True

    connection.execute(
        """
        UPDATE source_items
        SET
            source_type = ?,
            provider = ?,
            external_id = ?,
            title = ?,
            organization = ?,
            url = ?,
            canonical_url = ?,
            published_at = ?,
            raw_text = ?,
            payload_json = ?,
            last_seen_at = ?,
            seen_count = seen_count + 1
        WHERE source_item_id = ?
        """,
        (
            prepared_item.source_type,
            prepared_item.provider,
            prepared_item.external_id,
            prepared_item.title,
            prepared_item.organization,
            prepared_item.url,
            prepared_item.canonical_url,
            prepared_item.published_at,
            prepared_item.raw_text,
            prepared_item.payload_json,
            seen_at,
            int(existing_row["source_item_id"]),
        ),
    )

    return False


def _find_existing_source_item(
    connection: sqlite3.Connection,
    prepared_item: _PreparedSourceItem,
) -> sqlite3.Row | None:
    row = connection.execute(
        """
        SELECT source_item_id, fingerprint
        FROM source_items
        WHERE fingerprint = ?
        """,
        (prepared_item.fingerprint,),
    ).fetchone()
    if (
        row is not None
        or prepared_item.canonical_url is None
        or prepared_item.source_type
        not in CROSS_METHOD_URL_IDENTITY_SOURCE_TYPES
    ):
        return row

    monitoring_source_types = sorted(CROSS_METHOD_URL_IDENTITY_SOURCE_TYPES)
    placeholders = ",".join("?" for _ in monitoring_source_types)
    canonical_rows = connection.execute(
        f"""
        SELECT source_item_id, fingerprint
        FROM source_items
        WHERE canonical_url = ?
          AND source_type IN ({placeholders})
        ORDER BY source_item_id
        LIMIT 2
        """,
        (prepared_item.canonical_url, *monitoring_source_types),
    ).fetchall()
    if len(canonical_rows) > 1:
        raise SourceItemRepositoryError(
            "Multiple legacy SourceItems share canonical URL "
            f"{prepared_item.canonical_url!r}."
        )
    if not canonical_rows:
        return None

    legacy_row = canonical_rows[0]
    connection.execute(
        """
        UPDATE source_items
        SET fingerprint = ?
        WHERE source_item_id = ?
        """,
        (
            prepared_item.fingerprint,
            int(legacy_row["source_item_id"]),
        ),
    )
    return legacy_row


def _source_type_value(raw_item: RawItem) -> str:
    source_type = raw_item.source_type

    if hasattr(source_type, "value"):
        return str(source_type.value)

    return str(source_type)


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None

    stripped_value = str(value).strip()

    return stripped_value or None
