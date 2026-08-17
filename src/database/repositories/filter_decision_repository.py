from dataclasses import dataclass
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from src.database.connection import database_connection, open_database_connection
from src.database.migrations import initialize_database
from src.database.planning_identity import canonical_json
from src.models import utc_now_iso


PENDING = "pending"
RUNNING = "running"
ACCEPTED = "accepted"
REJECTED = "rejected"
DEFERRED = "deferred"
FAILED = "failed"
COMPLETED = "completed"

LEDGER_STATUSES = {PENDING, RUNNING, ACCEPTED, REJECTED, DEFERRED, FAILED}
FINAL_LEDGER_STATUSES = {ACCEPTED, REJECTED, DEFERRED, FAILED}
EXECUTION_STATUSES = {RUNNING, COMPLETED, FAILED}
DECISIONS = {ACCEPTED, REJECTED}

MAX_DEFERRED_REASON_LENGTH = 500
MAX_DECISION_REASON_LENGTH = 1000
MAX_ERROR_TYPE_LENGTH = 200
MAX_ERROR_MESSAGE_LENGTH = 1000
MAX_TEXT_LENGTH = 500
MAX_FINGERPRINT_LENGTH = 256
MAX_METADATA_JSON_LENGTH = 10000
MAX_MATCHED_PATH_COUNT = 100
HISTORICAL_DUPLICATE_REASON = "historical_duplicate"


@dataclass(frozen=True)
class RunFilterRegistration:
    run_id: str
    discovered_source_item_count: int
    registered_filter_item_count: int
    inserted_count: int


@dataclass(frozen=True)
class RunSourceItemFilterStatusRecord:
    run_source_item_filter_status_id: int
    run_id: str
    source_item_id: int
    filter_execution_id: int | None
    status: str
    deferred_reason: str | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FilterExecutionStart:
    execution_mode: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    prompt_fingerprint: str | None = None
    input_fingerprint: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class FilterExecutionRecord:
    filter_execution_id: int
    run_id: str
    status: str
    execution_mode: str | None
    provider: str | None
    model: str | None
    prompt_version: str | None
    prompt_fingerprint: str | None
    input_fingerprint: str | None
    item_count: int
    started_at: str
    completed_at: str | None
    error_type: str | None
    error_message: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FilterExecutionStartResult:
    execution: FilterExecutionRecord
    filter_items: tuple[RunSourceItemFilterStatusRecord, ...]


@dataclass(frozen=True)
class FilterDecisionInput:
    source_item_id: int
    decision: str
    reason: str | None = None
    confidence: float | None = None
    matched_career_path_ids: tuple[str, ...] | list[str] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class FilterDecisionRecord:
    filter_decision_id: int
    filter_execution_id: int
    run_id: str
    source_item_id: int
    decision: str
    reason: str | None
    confidence: float | None
    matched_career_path_ids: tuple[str, ...] | None
    metadata: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class FilterCoverage:
    run_id: str
    discovered_source_items: int
    registered_filter_items: int
    pending: int
    running: int
    accepted: int
    rejected: int
    deferred: int
    failed: int
    missing_unregistered: int
    unexpected_registered: int
    filter_execution_count: int
    filter_decision_count: int
    missing_source_item_ids: tuple[int, ...]
    unexpected_source_item_ids: tuple[int, ...]


@dataclass(frozen=True)
class CareerSignalMaterializationCoverage:
    run_id: str
    accepted_source_items: int
    accepted_with_career_signal: int
    accepted_without_career_signal: int
    missing_source_item_ids: tuple[int, ...]


class FilterDecisionRepositoryError(Exception):
    """Raised when filter-decision provenance persistence fails."""


class FilterDecisionRepository:
    """Repository for Run filter accounting and completed decisions."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = database_path

    def register_run_filter_items(
        self,
        run_id: str,
        *,
        created_at: str | None = None,
    ) -> RunFilterRegistration:
        registration_time = created_at or utc_now_iso()

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                run = _require_run(connection, run_id)
                normalized_run_id = str(run["run_id"])
                discovered_ids = _discovered_source_item_ids(
                    connection,
                    normalized_run_id,
                )
                existing_rows = connection.execute(
                    """
                    SELECT *
                    FROM run_source_item_filter_statuses
                    WHERE run_id = ?
                    ORDER BY source_item_id
                    """,
                    (normalized_run_id,),
                ).fetchall()
                existing_ids = {
                    int(row["source_item_id"])
                    for row in existing_rows
                }
                unexpected_ids = existing_ids - set(discovered_ids)
                if unexpected_ids:
                    raise FilterDecisionRepositoryError(
                        "Run filter ledger contains SourceItems without Run "
                        f"discovery provenance: {sorted(unexpected_ids)}."
                    )

                inserted_count = 0
                for source_item_id in discovered_ids:
                    if source_item_id in existing_ids:
                        continue
                    cursor = connection.execute(
                        """
                        INSERT INTO run_source_item_filter_statuses (
                            run_id,
                            source_item_id,
                            filter_execution_id,
                            status,
                            deferred_reason,
                            started_at,
                            completed_at,
                            created_at,
                            updated_at,
                            metadata_json
                        )
                        VALUES (?, ?, NULL, ?, NULL, NULL, NULL, ?, ?, ?)
                        """,
                        (
                            normalized_run_id,
                            source_item_id,
                            PENDING,
                            registration_time,
                            registration_time,
                            canonical_json({}),
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise FilterDecisionRepositoryError(
                            "Failed to register every discovered SourceItem."
                        )
                    inserted_count += 1

                registered_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM run_source_item_filter_statuses
                        WHERE run_id = ?
                        """,
                        (normalized_run_id,),
                    ).fetchone()[0]
                )
                if registered_count != len(discovered_ids):
                    raise FilterDecisionRepositoryError(
                        "Run filter registration count does not match unique "
                        "discovered SourceItems."
                    )

                return RunFilterRegistration(
                    run_id=normalized_run_id,
                    discovered_source_item_count=len(discovered_ids),
                    registered_filter_item_count=registered_count,
                    inserted_count=inserted_count,
                )
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to register Run filter items."
            ) from error

    def mark_items_deferred(
        self,
        run_id: str,
        source_item_ids: Iterable[int],
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
        completed_at: str | None = None,
    ) -> list[RunSourceItemFilterStatusRecord]:
        deferred_reason = _bounded_required_text(
            reason,
            "deferred reason",
            MAX_DEFERRED_REASON_LENGTH,
        )
        requested_ids = _unique_source_item_ids(source_item_ids)
        deferred_time = completed_at or utc_now_iso()
        metadata_json = _safe_metadata_json(metadata)

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                run = _require_running_run(connection, run_id)
                normalized_run_id = str(run["run_id"])
                if not requested_ids:
                    return []
                rows = _require_registered_filter_items(
                    connection,
                    normalized_run_id,
                    requested_ids,
                )
                invalid = {
                    int(row["source_item_id"]): str(row["status"])
                    for row in rows
                    if str(row["status"]) != PENDING
                }
                if invalid:
                    raise FilterDecisionRepositoryError(
                        "Only pending filter items may be deferred: "
                        f"{invalid}."
                    )

                placeholders = _placeholders(requested_ids)
                cursor = connection.execute(
                    f"""
                    UPDATE run_source_item_filter_statuses
                    SET
                        status = ?,
                        deferred_reason = ?,
                        completed_at = ?,
                        updated_at = ?,
                        metadata_json = ?
                    WHERE run_id = ?
                      AND source_item_id IN ({placeholders})
                      AND status = ?
                    """,
                    (
                        DEFERRED,
                        deferred_reason,
                        deferred_time,
                        deferred_time,
                        metadata_json,
                        normalized_run_id,
                        *requested_ids,
                        PENDING,
                    ),
                )
                if cursor.rowcount != len(requested_ids):
                    raise FilterDecisionRepositoryError(
                        "Deferred filter-item transition was incomplete."
                    )
                return _list_status_records_for_ids(
                    connection,
                    normalized_run_id,
                    requested_ids,
                )
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to defer Run filter items."
            ) from error

    def start_filter_execution(
        self,
        run_id: str,
        source_item_ids: Iterable[int],
        start: FilterExecutionStart | None = None,
        *,
        started_at: str | None = None,
    ) -> FilterExecutionStartResult:
        requested_ids = _strict_source_item_ids(source_item_ids)
        if not requested_ids:
            raise FilterDecisionRepositoryError(
                "FilterExecution requires at least one SourceItem."
            )
        prepared = _prepare_execution_start(start or FilterExecutionStart())
        start_time = started_at or utc_now_iso()

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                run = _require_running_run(connection, run_id)
                normalized_run_id = str(run["run_id"])
                _require_source_items(connection, requested_ids)
                rows = _require_registered_filter_items(
                    connection,
                    normalized_run_id,
                    requested_ids,
                )
                invalid = {
                    int(row["source_item_id"]): str(row["status"])
                    for row in rows
                    if str(row["status"]) != PENDING
                }
                if invalid:
                    raise FilterDecisionRepositoryError(
                        "Only pending filter items may start execution: "
                        f"{invalid}."
                    )

                cursor = connection.execute(
                    """
                    INSERT INTO filter_executions (
                        run_id,
                        status,
                        execution_mode,
                        provider,
                        model,
                        prompt_version,
                        prompt_fingerprint,
                        input_fingerprint,
                        item_count,
                        started_at,
                        completed_at,
                        error_type,
                        error_message,
                        metadata_json,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)
                    """,
                    (
                        normalized_run_id,
                        RUNNING,
                        prepared["execution_mode"],
                        prepared["provider"],
                        prepared["model"],
                        prepared["prompt_version"],
                        prepared["prompt_fingerprint"],
                        prepared["input_fingerprint"],
                        len(requested_ids),
                        start_time,
                        prepared["metadata_json"],
                        start_time,
                        start_time,
                    ),
                )
                if cursor.rowcount != 1:
                    raise FilterDecisionRepositoryError(
                        "Failed to create running FilterExecution."
                    )
                filter_execution_id = int(cursor.lastrowid)

                placeholders = _placeholders(requested_ids)
                transition = connection.execute(
                    f"""
                    UPDATE run_source_item_filter_statuses
                    SET
                        filter_execution_id = ?,
                        status = ?,
                        started_at = ?,
                        updated_at = ?
                    WHERE run_id = ?
                      AND source_item_id IN ({placeholders})
                      AND status = ?
                    """,
                    (
                        filter_execution_id,
                        RUNNING,
                        start_time,
                        start_time,
                        normalized_run_id,
                        *requested_ids,
                        PENDING,
                    ),
                )
                if transition.rowcount != len(requested_ids):
                    raise FilterDecisionRepositoryError(
                        "FilterExecution membership transition was incomplete."
                    )

                return FilterExecutionStartResult(
                    execution=_get_execution_record(
                        connection,
                        filter_execution_id,
                    ),
                    filter_items=tuple(
                        _list_status_records_for_ids(
                            connection,
                            normalized_run_id,
                            requested_ids,
                        )
                    ),
                )
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to start FilterExecution."
            ) from error

    def complete_filter_execution(
        self,
        filter_execution_id: int,
        decisions: Iterable[FilterDecisionInput],
        *,
        completed_at: str | None = None,
    ) -> FilterExecutionRecord:
        execution_id = _positive_integer(
            filter_execution_id,
            "filter_execution_id",
        )
        decision_time = completed_at or utc_now_iso()
        prepared_decisions = _prepare_decisions(decisions)

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                execution = _require_running_execution(connection, execution_id)
                run_id = str(execution["run_id"])
                _require_running_run(connection, run_id)
                attached_rows = _execution_filter_item_rows(
                    connection,
                    execution_id,
                )
                attached_ids = {
                    int(row["source_item_id"])
                    for row in attached_rows
                }
                decision_ids = set(prepared_decisions)
                if (
                    len(attached_rows) != int(execution["item_count"])
                    or any(str(row["status"]) != RUNNING for row in attached_rows)
                    or decision_ids != attached_ids
                ):
                    raise FilterDecisionRepositoryError(
                        "FilterExecution completion requires exactly one decision "
                        "for every attached running SourceItem."
                    )

                for source_item_id in sorted(attached_ids):
                    prepared = prepared_decisions[source_item_id]
                    cursor = connection.execute(
                        """
                        INSERT INTO filter_decisions (
                            filter_execution_id,
                            run_id,
                            source_item_id,
                            decision,
                            reason,
                            confidence,
                            matched_career_path_ids_json,
                            metadata_json,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            execution_id,
                            run_id,
                            source_item_id,
                            prepared["decision"],
                            prepared["reason"],
                            prepared["confidence"],
                            prepared["matched_career_path_ids_json"],
                            prepared["metadata_json"],
                            decision_time,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise FilterDecisionRepositoryError(
                            "Failed to persist every FilterDecision."
                        )
                    transition = connection.execute(
                        """
                        UPDATE run_source_item_filter_statuses
                        SET
                            status = ?,
                            completed_at = ?,
                            updated_at = ?
                        WHERE run_id = ?
                          AND source_item_id = ?
                          AND filter_execution_id = ?
                          AND status = ?
                        """,
                        (
                            prepared["decision"],
                            decision_time,
                            decision_time,
                            run_id,
                            source_item_id,
                            execution_id,
                            RUNNING,
                        ),
                    )
                    if transition.rowcount != 1:
                        raise FilterDecisionRepositoryError(
                            "Filter ledger decision transition failed."
                        )

                completion = connection.execute(
                    """
                    UPDATE filter_executions
                    SET
                        status = ?,
                        completed_at = ?,
                        updated_at = ?
                    WHERE filter_execution_id = ?
                      AND status = ?
                    """,
                    (
                        COMPLETED,
                        decision_time,
                        decision_time,
                        execution_id,
                        RUNNING,
                    ),
                )
                if completion.rowcount != 1:
                    raise FilterDecisionRepositoryError(
                        "FilterExecution completion transition failed."
                    )
                return _get_execution_record(connection, execution_id)
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to complete FilterExecution."
            ) from error

    def fail_filter_execution(
        self,
        filter_execution_id: int,
        error_type: str,
        error_message: str,
        *,
        metadata: dict[str, Any] | None = None,
        failed_at: str | None = None,
    ) -> FilterExecutionRecord:
        execution_id = _positive_integer(
            filter_execution_id,
            "filter_execution_id",
        )
        normalized_error_type = _bounded_required_text(
            error_type,
            "error_type",
            MAX_ERROR_TYPE_LENGTH,
        )
        normalized_error_message = _bounded_required_text(
            error_message,
            "error_message",
            MAX_ERROR_MESSAGE_LENGTH,
        )
        failure_time = failed_at or utc_now_iso()

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                execution = _require_running_execution(connection, execution_id)
                _require_running_run(connection, str(execution["run_id"]))
                attached_rows = _execution_filter_item_rows(
                    connection,
                    execution_id,
                )
                if (
                    len(attached_rows) != int(execution["item_count"])
                    or any(str(row["status"]) != RUNNING for row in attached_rows)
                ):
                    raise FilterDecisionRepositoryError(
                        "Running FilterExecution has inconsistent batch membership."
                    )
                decision_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM filter_decisions
                        WHERE filter_execution_id = ?
                        """,
                        (execution_id,),
                    ).fetchone()[0]
                )
                if decision_count:
                    raise FilterDecisionRepositoryError(
                        "A running FilterExecution cannot fail after decisions exist."
                    )

                ledger_transition = connection.execute(
                    """
                    UPDATE run_source_item_filter_statuses
                    SET
                        status = ?,
                        completed_at = ?,
                        updated_at = ?
                    WHERE filter_execution_id = ?
                      AND status = ?
                    """,
                    (
                        FAILED,
                        failure_time,
                        failure_time,
                        execution_id,
                        RUNNING,
                    ),
                )
                if ledger_transition.rowcount != len(attached_rows):
                    raise FilterDecisionRepositoryError(
                        "Failed FilterExecution ledger transition was incomplete."
                    )

                metadata_json = _merged_metadata_json(
                    execution["metadata_json"],
                    metadata,
                )
                execution_transition = connection.execute(
                    """
                    UPDATE filter_executions
                    SET
                        status = ?,
                        completed_at = ?,
                        error_type = ?,
                        error_message = ?,
                        metadata_json = ?,
                        updated_at = ?
                    WHERE filter_execution_id = ?
                      AND status = ?
                    """,
                    (
                        FAILED,
                        failure_time,
                        normalized_error_type,
                        normalized_error_message,
                        metadata_json,
                        failure_time,
                        execution_id,
                        RUNNING,
                    ),
                )
                if execution_transition.rowcount != 1:
                    raise FilterDecisionRepositoryError(
                        "FilterExecution failure transition failed."
                    )
                return _get_execution_record(connection, execution_id)
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to fail FilterExecution."
            ) from error

    def get_run_filter_coverage(self, run_id: str) -> FilterCoverage:
        try:
            initialize_database(database_path=self.database_path)
            connection = open_database_connection(self.database_path)
            try:
                _require_run(connection, run_id)
                return _build_filter_coverage(connection, run_id)
            finally:
                connection.close()
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to calculate Run filter coverage."
            ) from error

    def get_run_career_signal_materialization(
        self,
        run_id: str,
    ) -> CareerSignalMaterializationCoverage:
        try:
            initialize_database(database_path=self.database_path)
            connection = open_database_connection(self.database_path)
            try:
                _require_run(connection, run_id)
                accepted_ids = {
                    int(row["source_item_id"])
                    for row in connection.execute(
                        """
                        SELECT source_item_id
                        FROM run_source_item_filter_statuses
                        WHERE run_id = ? AND status = ?
                        ORDER BY source_item_id
                        """,
                        (run_id, ACCEPTED),
                    ).fetchall()
                }
                materialized_ids = {
                    int(row["source_item_id"])
                    for row in connection.execute(
                        """
                        SELECT DISTINCT signal.source_item_id
                        FROM career_signals AS signal
                        WHERE signal.source_item_id IS NOT NULL
                          AND signal.source_item_id IN (
                              SELECT source_item_id
                              FROM run_source_item_filter_statuses
                              WHERE run_id = ? AND status = ?
                          )
                        """,
                        (run_id, ACCEPTED),
                    ).fetchall()
                }
                missing_ids = tuple(sorted(accepted_ids - materialized_ids))
                return CareerSignalMaterializationCoverage(
                    run_id=run_id,
                    accepted_source_items=len(accepted_ids),
                    accepted_with_career_signal=len(
                        accepted_ids & materialized_ids
                    ),
                    accepted_without_career_signal=len(missing_ids),
                    missing_source_item_ids=missing_ids,
                )
            finally:
                connection.close()
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to audit accepted SourceItem CareerSignal materialization."
            ) from error

    def assert_run_filter_accounting_complete(
        self,
        run_id: str,
    ) -> FilterCoverage:
        try:
            initialize_database(database_path=self.database_path)
            connection = open_database_connection(self.database_path)
            try:
                _require_run(connection, run_id)
                coverage = _build_filter_coverage(connection, run_id)
                _validate_complete_filter_accounting(
                    connection,
                    run_id,
                    coverage,
                )
                return coverage
            finally:
                connection.close()
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to assert Run filter accounting."
            ) from error

    def list_run_filter_statuses(
        self,
        run_id: str,
    ) -> list[RunSourceItemFilterStatusRecord]:
        try:
            initialize_database(database_path=self.database_path)
            connection = open_database_connection(self.database_path)
            try:
                _require_run(connection, run_id)
                return _list_all_status_records(connection, run_id)
            finally:
                connection.close()
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to list Run filter statuses."
            ) from error

    def get_filter_execution(
        self,
        filter_execution_id: int,
    ) -> FilterExecutionRecord | None:
        execution_id = _positive_integer(
            filter_execution_id,
            "filter_execution_id",
        )
        try:
            initialize_database(database_path=self.database_path)
            connection = open_database_connection(self.database_path)
            try:
                row = connection.execute(
                    """
                    SELECT *
                    FROM filter_executions
                    WHERE filter_execution_id = ?
                    """,
                    (execution_id,),
                ).fetchone()
                return _row_to_execution_record(row) if row is not None else None
            finally:
                connection.close()
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to load FilterExecution."
            ) from error

    def list_filter_executions(
        self,
        run_id: str,
    ) -> list[FilterExecutionRecord]:
        try:
            initialize_database(database_path=self.database_path)
            connection = open_database_connection(self.database_path)
            try:
                _require_run(connection, run_id)
                rows = connection.execute(
                    """
                    SELECT *
                    FROM filter_executions
                    WHERE run_id = ?
                    ORDER BY started_at, filter_execution_id
                    """,
                    (run_id,),
                ).fetchall()
                return [_row_to_execution_record(row) for row in rows]
            finally:
                connection.close()
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to list FilterExecutions."
            ) from error

    def list_filter_decisions_for_run(
        self,
        run_id: str,
    ) -> list[FilterDecisionRecord]:
        try:
            initialize_database(database_path=self.database_path)
            connection = open_database_connection(self.database_path)
            try:
                _require_run(connection, run_id)
                rows = connection.execute(
                    """
                    SELECT *
                    FROM filter_decisions
                    WHERE run_id = ?
                    ORDER BY source_item_id, filter_decision_id
                    """,
                    (run_id,),
                ).fetchall()
                return [_row_to_decision_record(row) for row in rows]
            finally:
                connection.close()
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to list Run FilterDecisions."
            ) from error

    def list_filter_decisions_for_source_item(
        self,
        source_item_id: int,
        limit: int | None = None,
    ) -> list[FilterDecisionRecord]:
        normalized_source_item_id = _positive_integer(
            source_item_id,
            "source_item_id",
        )
        normalized_limit = (
            _nonnegative_integer(limit, "limit")
            if limit is not None
            else None
        )
        if normalized_limit == 0:
            return []

        try:
            initialize_database(database_path=self.database_path)
            connection = open_database_connection(self.database_path)
            try:
                _require_source_items(connection, [normalized_source_item_id])
                sql = """
                    SELECT decision.*
                    FROM filter_decisions AS decision
                    JOIN pipeline_runs AS run
                      ON run.run_id = decision.run_id
                    WHERE decision.source_item_id = ?
                    ORDER BY
                        run.started_at DESC,
                        decision.created_at DESC,
                        decision.filter_decision_id DESC
                """
                parameters: tuple[Any, ...] = (normalized_source_item_id,)
                if normalized_limit is not None:
                    sql += " LIMIT ?"
                    parameters = (normalized_source_item_id, normalized_limit)
                rows = connection.execute(sql, parameters).fetchall()
                return [_row_to_decision_record(row) for row in rows]
            finally:
                connection.close()
        except FilterDecisionRepositoryError:
            raise
        except Exception as error:
            raise FilterDecisionRepositoryError(
                "Failed to list SourceItem FilterDecision history."
            ) from error


def _require_run(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    normalized_run_id = _required_text(run_id, "run_id")
    row = connection.execute(
        "SELECT * FROM pipeline_runs WHERE run_id = ?",
        (normalized_run_id,),
    ).fetchone()
    if row is None:
        raise FilterDecisionRepositoryError(
            f"PipelineRun {normalized_run_id!r} was not found."
        )
    return row


def _require_running_run(
    connection: sqlite3.Connection,
    run_id: str,
) -> sqlite3.Row:
    row = _require_run(connection, run_id)
    if str(row["status"]) != RUNNING:
        raise FilterDecisionRepositoryError(
            f"PipelineRun {run_id!r} is not running."
        )
    return row


def _discovered_source_item_ids(
    connection: sqlite3.Connection,
    run_id: str,
) -> list[int]:
    rows = connection.execute(
        """
        SELECT
            discovery.source_item_id,
            item.source_item_id AS valid_source_item_id
        FROM source_item_discoveries AS discovery
        JOIN source_executions AS execution
          ON execution.source_execution_id = discovery.source_execution_id
        LEFT JOIN source_items AS item
          ON item.source_item_id = discovery.source_item_id
        WHERE execution.run_id = ?
        ORDER BY discovery.source_item_id
        """,
        (run_id,),
    ).fetchall()
    invalid_ids = {
        int(row["source_item_id"])
        for row in rows
        if row["valid_source_item_id"] is None
    }
    if invalid_ids:
        raise FilterDecisionRepositoryError(
            "Run discovery provenance references missing SourceItems: "
            f"{sorted(invalid_ids)}."
        )
    return sorted({int(row["source_item_id"]) for row in rows})


def _require_source_items(
    connection: sqlite3.Connection,
    source_item_ids: list[int],
) -> None:
    if not source_item_ids:
        return
    placeholders = _placeholders(source_item_ids)
    rows = connection.execute(
        f"""
        SELECT source_item_id
        FROM source_items
        WHERE source_item_id IN ({placeholders})
        """,
        source_item_ids,
    ).fetchall()
    found = {int(row["source_item_id"]) for row in rows}
    missing = set(source_item_ids) - found
    if missing:
        raise FilterDecisionRepositoryError(
            f"SourceItems were not found: {sorted(missing)}."
        )


def _require_registered_filter_items(
    connection: sqlite3.Connection,
    run_id: str,
    source_item_ids: list[int],
) -> list[sqlite3.Row]:
    if not source_item_ids:
        return []
    placeholders = _placeholders(source_item_ids)
    rows = connection.execute(
        f"""
        SELECT *
        FROM run_source_item_filter_statuses
        WHERE run_id = ?
          AND source_item_id IN ({placeholders})
        ORDER BY source_item_id
        """,
        (run_id, *source_item_ids),
    ).fetchall()
    found = {int(row["source_item_id"]) for row in rows}
    missing = set(source_item_ids) - found
    if missing:
        raise FilterDecisionRepositoryError(
            "SourceItems are not registered for this PipelineRun: "
            f"{sorted(missing)}."
        )
    return rows


def _require_execution(
    connection: sqlite3.Connection,
    filter_execution_id: int,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT *
        FROM filter_executions
        WHERE filter_execution_id = ?
        """,
        (filter_execution_id,),
    ).fetchone()
    if row is None:
        raise FilterDecisionRepositoryError(
            f"FilterExecution {filter_execution_id} was not found."
        )
    return row


def _require_running_execution(
    connection: sqlite3.Connection,
    filter_execution_id: int,
) -> sqlite3.Row:
    row = _require_execution(connection, filter_execution_id)
    if str(row["status"]) != RUNNING:
        raise FilterDecisionRepositoryError(
            f"FilterExecution {filter_execution_id} is not running."
        )
    return row


def _execution_filter_item_rows(
    connection: sqlite3.Connection,
    filter_execution_id: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT *
        FROM run_source_item_filter_statuses
        WHERE filter_execution_id = ?
        ORDER BY source_item_id
        """,
        (filter_execution_id,),
    ).fetchall()


def _prepare_execution_start(start: FilterExecutionStart) -> dict[str, Any]:
    return {
        "execution_mode": _bounded_optional_text(
            start.execution_mode,
            "execution_mode",
            MAX_TEXT_LENGTH,
        ),
        "provider": _bounded_optional_text(
            start.provider,
            "provider",
            MAX_TEXT_LENGTH,
        ),
        "model": _bounded_optional_text(
            start.model,
            "model",
            MAX_TEXT_LENGTH,
        ),
        "prompt_version": _bounded_optional_text(
            start.prompt_version,
            "prompt_version",
            MAX_TEXT_LENGTH,
        ),
        "prompt_fingerprint": _bounded_optional_text(
            start.prompt_fingerprint,
            "prompt_fingerprint",
            MAX_FINGERPRINT_LENGTH,
        ),
        "input_fingerprint": _bounded_optional_text(
            start.input_fingerprint,
            "input_fingerprint",
            MAX_FINGERPRINT_LENGTH,
        ),
        "metadata_json": _safe_metadata_json(start.metadata),
    }


def _prepare_decisions(
    decisions: Iterable[FilterDecisionInput],
) -> dict[int, dict[str, Any]]:
    prepared: dict[int, dict[str, Any]] = {}
    for decision in decisions:
        source_item_id = _positive_integer(
            decision.source_item_id,
            "source_item_id",
        )
        if source_item_id in prepared:
            raise FilterDecisionRepositoryError(
                f"Duplicate FilterDecision for SourceItem {source_item_id}."
            )
        normalized_decision = str(decision.decision).strip().lower()
        if normalized_decision not in DECISIONS:
            raise FilterDecisionRepositoryError(
                "FilterDecision must be accepted or rejected."
            )
        confidence = _optional_confidence(decision.confidence)
        matched_ids = _matched_career_path_ids(
            decision.matched_career_path_ids
        )
        prepared[source_item_id] = {
            "decision": normalized_decision,
            "reason": _bounded_optional_text(
                decision.reason,
                "decision reason",
                MAX_DECISION_REASON_LENGTH,
            ),
            "confidence": confidence,
            "matched_career_path_ids_json": (
                canonical_json(matched_ids)
                if matched_ids is not None
                else None
            ),
            "metadata_json": _safe_metadata_json(decision.metadata),
        }
    return prepared


def _matched_career_path_ids(
    values: tuple[str, ...] | list[str] | None,
) -> list[str] | None:
    if values is None:
        return None
    if not isinstance(values, (list, tuple)):
        raise FilterDecisionRepositoryError(
            "matched_career_path_ids must be a list or tuple when supplied."
        )
    if len(values) > MAX_MATCHED_PATH_COUNT:
        raise FilterDecisionRepositoryError(
            "matched_career_path_ids contains too many values."
        )
    normalized: list[str] = []
    for value in values:
        path_id = _bounded_required_text(
            value,
            "matched career path ID",
            MAX_TEXT_LENGTH,
        )
        normalized.append(path_id)
    return normalized


def _build_filter_coverage(
    connection: sqlite3.Connection,
    run_id: str,
) -> FilterCoverage:
    discovered_ids = set(_discovered_source_item_ids(connection, run_id))
    ledger_rows = connection.execute(
        """
        SELECT source_item_id, status
        FROM run_source_item_filter_statuses
        WHERE run_id = ?
        ORDER BY source_item_id
        """,
        (run_id,),
    ).fetchall()
    registered_ids = {int(row["source_item_id"]) for row in ledger_rows}
    missing_ids = tuple(sorted(discovered_ids - registered_ids))
    unexpected_ids = tuple(sorted(registered_ids - discovered_ids))
    status_counts = {
        status: sum(1 for row in ledger_rows if str(row["status"]) == status)
        for status in LEDGER_STATUSES
    }
    execution_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM filter_executions WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    )
    decision_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM filter_decisions WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    )
    return FilterCoverage(
        run_id=run_id,
        discovered_source_items=len(discovered_ids),
        registered_filter_items=len(registered_ids),
        pending=status_counts[PENDING],
        running=status_counts[RUNNING],
        accepted=status_counts[ACCEPTED],
        rejected=status_counts[REJECTED],
        deferred=status_counts[DEFERRED],
        failed=status_counts[FAILED],
        missing_unregistered=len(missing_ids),
        unexpected_registered=len(unexpected_ids),
        filter_execution_count=execution_count,
        filter_decision_count=decision_count,
        missing_source_item_ids=missing_ids,
        unexpected_source_item_ids=unexpected_ids,
    )


def _validate_complete_filter_accounting(
    connection: sqlite3.Connection,
    run_id: str,
    coverage: FilterCoverage,
) -> None:
    if (
        coverage.registered_filter_items != coverage.discovered_source_items
        or coverage.missing_unregistered
        or coverage.unexpected_registered
        or coverage.pending
        or coverage.running
    ):
        raise FilterDecisionRepositoryError(
            "PipelineRun filter accounting is incomplete: "
            f"discovered={coverage.discovered_source_items}, "
            f"registered={coverage.registered_filter_items}, "
            f"pending={coverage.pending}, running={coverage.running}, "
            f"missing={coverage.missing_unregistered}, "
            f"unexpected={coverage.unexpected_registered}."
        )

    ledger_rows = connection.execute(
        """
        SELECT *
        FROM run_source_item_filter_statuses
        WHERE run_id = ?
        ORDER BY source_item_id
        """,
        (run_id,),
    ).fetchall()
    executions = connection.execute(
        """
        SELECT *
        FROM filter_executions
        WHERE run_id = ?
        ORDER BY filter_execution_id
        """,
        (run_id,),
    ).fetchall()
    decisions = connection.execute(
        """
        SELECT *
        FROM filter_decisions
        WHERE run_id = ?
        ORDER BY filter_decision_id
        """,
        (run_id,),
    ).fetchall()
    ledger_by_source = {
        int(row["source_item_id"]): row
        for row in ledger_rows
    }
    executions_by_id = {
        int(row["filter_execution_id"]): row
        for row in executions
    }
    decisions_by_execution: dict[int, list[sqlite3.Row]] = {}
    for decision in decisions:
        decisions_by_execution.setdefault(
            int(decision["filter_execution_id"]),
            [],
        ).append(decision)
    unexpected_decision_execution_ids = (
        set(decisions_by_execution) - set(executions_by_id)
    )
    if unexpected_decision_execution_ids:
        raise FilterDecisionRepositoryError(
            "FilterDecisions reference executions outside their PipelineRun: "
            f"{sorted(unexpected_decision_execution_ids)}."
        )

    for row in ledger_rows:
        status = str(row["status"])
        execution_id = row["filter_execution_id"]
        if status == DEFERRED:
            if execution_id is not None:
                raise FilterDecisionRepositoryError(
                    "Deferred filter item is attached to an execution."
                )
            continue
        if status not in {ACCEPTED, REJECTED, FAILED} or execution_id is None:
            raise FilterDecisionRepositoryError(
                "Final filter ledger relationship is inconsistent."
            )
        execution = executions_by_id.get(int(execution_id))
        if execution is None or str(execution["run_id"]) != run_id:
            raise FilterDecisionRepositoryError(
                "Filter ledger references an unexpected FilterExecution."
            )

    for execution_id, execution in executions_by_id.items():
        attached = [
            row
            for row in ledger_rows
            if row["filter_execution_id"] is not None
            and int(row["filter_execution_id"]) == execution_id
        ]
        execution_decisions = decisions_by_execution.get(execution_id, [])
        if len(attached) != int(execution["item_count"]):
            raise FilterDecisionRepositoryError(
                "FilterExecution item_count does not match attached ledger items."
            )
        execution_status = str(execution["status"])
        if execution_status == RUNNING:
            raise FilterDecisionRepositoryError(
                "Running FilterExecution remains during final accounting."
            )
        if execution_status == FAILED:
            if (
                any(str(row["status"]) != FAILED for row in attached)
                or execution_decisions
            ):
                raise FilterDecisionRepositoryError(
                    "Failed FilterExecution has inconsistent items or decisions."
                )
            continue
        if execution_status != COMPLETED:
            raise FilterDecisionRepositoryError(
                "FilterExecution has an unknown accounting status."
            )
        attached_ids = {int(row["source_item_id"]) for row in attached}
        decision_ids = {
            int(row["source_item_id"])
            for row in execution_decisions
        }
        if attached_ids != decision_ids or len(execution_decisions) != len(attached):
            raise FilterDecisionRepositoryError(
                "Completed FilterExecution lacks one decision per attached item."
            )
        for decision in execution_decisions:
            source_item_id = int(decision["source_item_id"])
            ledger = ledger_by_source.get(source_item_id)
            if (
                ledger is None
                or int(ledger["filter_execution_id"]) != execution_id
                or str(ledger["status"]) != str(decision["decision"])
            ):
                raise FilterDecisionRepositoryError(
                    "FilterDecision does not match its execution membership."
                )


def _list_all_status_records(
    connection: sqlite3.Connection,
    run_id: str,
) -> list[RunSourceItemFilterStatusRecord]:
    rows = connection.execute(
        """
        SELECT *
        FROM run_source_item_filter_statuses
        WHERE run_id = ?
        ORDER BY source_item_id, run_source_item_filter_status_id
        """,
        (run_id,),
    ).fetchall()
    return [_row_to_status_record(row) for row in rows]


def _list_status_records_for_ids(
    connection: sqlite3.Connection,
    run_id: str,
    source_item_ids: list[int],
) -> list[RunSourceItemFilterStatusRecord]:
    requested = set(source_item_ids)
    return [
        row
        for row in _list_all_status_records(connection, run_id)
        if row.source_item_id in requested
    ]


def _get_execution_record(
    connection: sqlite3.Connection,
    filter_execution_id: int,
) -> FilterExecutionRecord:
    return _row_to_execution_record(
        _require_execution(connection, filter_execution_id)
    )


def _row_to_status_record(
    row: sqlite3.Row,
) -> RunSourceItemFilterStatusRecord:
    return RunSourceItemFilterStatusRecord(
        run_source_item_filter_status_id=int(
            row["run_source_item_filter_status_id"]
        ),
        run_id=str(row["run_id"]),
        source_item_id=int(row["source_item_id"]),
        filter_execution_id=(
            int(row["filter_execution_id"])
            if row["filter_execution_id"] is not None
            else None
        ),
        status=str(row["status"]),
        deferred_reason=row["deferred_reason"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        metadata=_json_object(row["metadata_json"]),
    )


def _row_to_execution_record(row: sqlite3.Row) -> FilterExecutionRecord:
    return FilterExecutionRecord(
        filter_execution_id=int(row["filter_execution_id"]),
        run_id=str(row["run_id"]),
        status=str(row["status"]),
        execution_mode=row["execution_mode"],
        provider=row["provider"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        prompt_fingerprint=row["prompt_fingerprint"],
        input_fingerprint=row["input_fingerprint"],
        item_count=int(row["item_count"]),
        started_at=str(row["started_at"]),
        completed_at=row["completed_at"],
        error_type=row["error_type"],
        error_message=row["error_message"],
        metadata=_json_object(row["metadata_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_decision_record(row: sqlite3.Row) -> FilterDecisionRecord:
    matched_ids = _json_string_tuple(row["matched_career_path_ids_json"])
    return FilterDecisionRecord(
        filter_decision_id=int(row["filter_decision_id"]),
        filter_execution_id=int(row["filter_execution_id"]),
        run_id=str(row["run_id"]),
        source_item_id=int(row["source_item_id"]),
        decision=str(row["decision"]),
        reason=row["reason"],
        confidence=(
            float(row["confidence"])
            if row["confidence"] is not None
            else None
        ),
        matched_career_path_ids=matched_ids,
        metadata=_json_object(row["metadata_json"]),
        created_at=str(row["created_at"]),
    )


def _safe_metadata_json(metadata: dict[str, Any] | None) -> str:
    value = metadata or {}
    if not isinstance(value, dict):
        raise FilterDecisionRepositoryError("metadata must be a JSON object.")
    _validate_safe_metadata(value)
    try:
        serialized = canonical_json(value)
    except Exception as error:
        raise FilterDecisionRepositoryError(
            "metadata must be JSON serializable."
        ) from error
    if len(serialized) > MAX_METADATA_JSON_LENGTH:
        raise FilterDecisionRepositoryError(
            f"metadata must not exceed {MAX_METADATA_JSON_LENGTH} characters."
        )
    return serialized


def _validate_safe_metadata(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if (
                any(
                    marker in normalized_key
                    for marker in (
                        "api_key",
                        "authorization",
                        "access_token",
                        "auth_token",
                        "password",
                        "secret",
                    )
                )
                or normalized_key
                in {
                    ".env",
                    "env",
                    "prompt",
                    "raw_prompt",
                    "raw_response",
                    "raw_ai_response",
                    "raw_resume_text",
                    "resume_text",
                    "user_preferences",
                    "user_profile",
                }
            ):
                raise FilterDecisionRepositoryError(
                    f"Sensitive or unbounded metadata field {key!r} is not allowed."
                )
            _validate_safe_metadata(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_safe_metadata(item)


def _merged_metadata_json(
    existing_json: str,
    updates: dict[str, Any] | None,
) -> str:
    existing = _json_object(existing_json)
    existing.update(updates or {})
    return _safe_metadata_json(existing)


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise FilterDecisionRepositoryError(
            "Stored metadata_json is not a JSON object."
        )
    return parsed


def _json_string_tuple(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, list) or any(
        not isinstance(item, str)
        for item in parsed
    ):
        raise FilterDecisionRepositoryError(
            "Stored matched career path IDs are malformed."
        )
    return tuple(parsed)


def _strict_source_item_ids(values: Iterable[int]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for value in values:
        source_item_id = _positive_integer(value, "source_item_id")
        if source_item_id in seen:
            raise FilterDecisionRepositoryError(
                f"Duplicate SourceItem ID {source_item_id} in execution batch."
            )
        normalized.append(source_item_id)
        seen.add(source_item_id)
    return normalized


def _unique_source_item_ids(values: Iterable[int]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for value in values:
        source_item_id = _positive_integer(value, "source_item_id")
        if source_item_id not in seen:
            normalized.append(source_item_id)
            seen.add(source_item_id)
    return normalized


def _positive_integer(value: int, field_name: str) -> int:
    normalized = _nonnegative_integer(value, field_name)
    if normalized == 0:
        raise FilterDecisionRepositoryError(
            f"{field_name} must be a positive integer."
        )
    return normalized


def _nonnegative_integer(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FilterDecisionRepositoryError(
            f"{field_name} must be a non-negative integer."
        )
    return value


def _required_text(value: str, field_name: str) -> str:
    if value is None:
        raise FilterDecisionRepositoryError(f"{field_name} is required.")
    normalized = str(value).strip()
    if not normalized:
        raise FilterDecisionRepositoryError(f"{field_name} is required.")
    return normalized


def _bounded_required_text(
    value: str,
    field_name: str,
    limit: int,
) -> str:
    normalized = _required_text(value, field_name)
    if len(normalized) > limit:
        raise FilterDecisionRepositoryError(
            f"{field_name} must not exceed {limit} characters."
        )
    return normalized


def _bounded_optional_text(
    value: str | None,
    field_name: str,
    limit: int,
) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if len(normalized) > limit:
        raise FilterDecisionRepositoryError(
            f"{field_name} must not exceed {limit} characters."
        )
    return normalized


def _optional_confidence(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FilterDecisionRepositoryError(
            "confidence must be numeric when supplied."
        )
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0 or normalized > 1.0:
        raise FilterDecisionRepositoryError(
            "confidence must be between 0.0 and 1.0."
        )
    return normalized


def _placeholders(values: list[int]) -> str:
    return ",".join("?" for _ in values)
