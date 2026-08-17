from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from src.database.connection import database_connection, open_database_connection
from src.database.migrations import initialize_database
from src.database.planning_identity import canonical_json
from src.models import utc_now_iso


PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"

LEDGER_STATUSES = {PENDING, RUNNING, COMPLETED, FAILED, SKIPPED}
SOURCE_EXECUTION_STATUSES = {RUNNING, COMPLETED, FAILED}
FINAL_LEDGER_STATUSES = {COMPLETED, FAILED, SKIPPED}
MAX_REASON_LENGTH = 500
MAX_ERROR_MESSAGE_LENGTH = 1000


@dataclass(frozen=True)
class RunSearchPlanRegistration:
    run_id: str
    planning_bundle_id: int
    bundle_plan_count: int
    registered_plan_count: int
    inserted_count: int


@dataclass(frozen=True)
class RunSearchPlanStatusRecord:
    run_search_plan_status_id: int
    run_id: str
    planning_search_plan_id: int
    planning_bundle_id: int
    search_query_row_id: int
    plan_identity: str
    plan_position: int
    status: str
    skip_reason: str | None
    selection_order: int | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RunSearchPlanCoverage:
    run_id: str
    planning_bundle_id: int
    total_bundle_plans: int
    registered_plans: int
    pending: int
    running: int
    completed: int
    failed: int
    skipped: int
    missing_unregistered: int
    unexpected_registered: int
    missing_plan_ids: tuple[int, ...]
    unexpected_plan_ids: tuple[int, ...]


@dataclass(frozen=True)
class SearchQueryCoverageRecord:
    run_id: str
    planning_bundle_id: int
    search_query_row_id: int
    query_identity: str
    query_position: int
    total_search_plans: int
    completed: int
    failed: int
    skipped: int
    pending: int
    running: int
    missing_unregistered: int
    no_search_plans_generated: bool
    no_plans_entered_execution: bool


@dataclass(frozen=True)
class SourceExecutionStart:
    source_type: str
    provider: str | None = None
    source_key: str | None = None
    source_name: str | None = None
    source_locator: str | None = None
    execution_mode: str | None = None
    requested_result_limit: int | None = None
    request_fingerprint: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class SourceExecutionCompletion:
    returned_item_count: int
    discovered_item_count: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class SourceExecutionFailure:
    error_type: str
    error_message: str
    returned_item_count: int | None = None
    discovered_item_count: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class SourceExecutionRecord:
    source_execution_id: int
    run_id: str
    planning_search_plan_id: int | None
    source_type: str
    provider: str | None
    source_key: str | None
    source_name: str | None
    source_locator: str | None
    execution_mode: str | None
    status: str
    requested_result_limit: int | None
    returned_item_count: int | None
    discovered_item_count: int | None
    request_fingerprint: str | None
    started_at: str
    completed_at: str | None
    error_type: str | None
    error_message: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SearchPlanExecutionStartResult:
    ledger_status: RunSearchPlanStatusRecord
    source_execution: SourceExecutionRecord


@dataclass(frozen=True)
class SourceItemDiscoveryWrite:
    source_item_id: int
    result_position: int | None = None
    metadata: dict[str, Any] | None = None
    discovered_at: str | None = None


@dataclass(frozen=True)
class SourceItemDiscoveryRecord:
    source_execution_id: int
    source_item_id: int
    result_position: int | None
    discovered_at: str
    metadata: dict[str, Any]


class SourceExecutionRepositoryError(Exception):
    """Raised when execution-ledger or source-provenance persistence fails."""


class SourceExecutionRepository:
    """Repository for Run SearchPlan accounting and source provenance."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = database_path

    def register_run_search_plans(
        self,
        run_id: str,
        *,
        created_at: str | None = None,
    ) -> RunSearchPlanRegistration:
        registration_time = created_at or utc_now_iso()

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                run = _require_run_with_bundle(connection, run_id)
                planning_bundle_id = int(run["planning_bundle_id"])
                plans = _list_valid_bundle_plans(connection, planning_bundle_id)
                plan_ids = {
                    int(plan["search_plan_row_id"])
                    for plan in plans
                }
                existing_rows = connection.execute(
                    """
                    SELECT
                        ledger.planning_search_plan_id,
                        plan.planning_bundle_id
                    FROM run_search_plan_statuses AS ledger
                    JOIN planning_search_plans AS plan
                      ON plan.search_plan_row_id = ledger.planning_search_plan_id
                    WHERE ledger.run_id = ?
                    """,
                    (run_id,),
                ).fetchall()
                existing_ids = {
                    int(row["planning_search_plan_id"])
                    for row in existing_rows
                }
                outside_ids = {
                    int(row["planning_search_plan_id"])
                    for row in existing_rows
                    if int(row["planning_bundle_id"]) != planning_bundle_id
                    or int(row["planning_search_plan_id"]) not in plan_ids
                }

                if outside_ids:
                    raise SourceExecutionRepositoryError(
                        "PipelineRun ledger contains SearchPlans outside its "
                        f"Planning Bundle: {sorted(outside_ids)}."
                    )

                inserted_count = 0
                for plan in plans:
                    planning_search_plan_id = int(plan["search_plan_row_id"])
                    if planning_search_plan_id in existing_ids:
                        continue

                    cursor = connection.execute(
                        """
                        INSERT INTO run_search_plan_statuses (
                            run_id,
                            planning_search_plan_id,
                            status,
                            skip_reason,
                            selection_order,
                            started_at,
                            completed_at,
                            created_at,
                            updated_at,
                            metadata_json
                        )
                        VALUES (?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?)
                        """,
                        (
                            run_id,
                            planning_search_plan_id,
                            PENDING,
                            registration_time,
                            registration_time,
                            canonical_json({}),
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise SourceExecutionRepositoryError(
                            "Failed to register every SearchPlan for PipelineRun."
                        )
                    inserted_count += 1

                registered_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM run_search_plan_statuses AS ledger
                        JOIN planning_search_plans AS plan
                          ON plan.search_plan_row_id = ledger.planning_search_plan_id
                        WHERE ledger.run_id = ?
                          AND plan.planning_bundle_id = ?
                        """,
                        (run_id, planning_bundle_id),
                    ).fetchone()[0]
                )

                if registered_count != len(plans):
                    raise SourceExecutionRepositoryError(
                        "SearchPlan registration count does not match the attached "
                        "Planning Bundle."
                    )

                return RunSearchPlanRegistration(
                    run_id=run_id,
                    planning_bundle_id=planning_bundle_id,
                    bundle_plan_count=len(plans),
                    registered_plan_count=registered_count,
                    inserted_count=inserted_count,
                )
        except SourceExecutionRepositoryError:
            raise
        except Exception as error:
            raise SourceExecutionRepositoryError(
                "Failed to register PipelineRun SearchPlans."
            ) from error

    def mark_plans_skipped(
        self,
        run_id: str,
        planning_search_plan_ids: Iterable[int],
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
        completed_at: str | None = None,
    ) -> list[RunSearchPlanStatusRecord]:
        skip_reason = _bounded_required_text(
            reason,
            "skip reason",
            MAX_REASON_LENGTH,
        )
        requested_ids = _unique_integer_ids(planning_search_plan_ids)
        skipped_at = completed_at or utc_now_iso()

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                run = _require_running_run_with_bundle(connection, run_id)
                planning_bundle_id = int(run["planning_bundle_id"])

                if not requested_ids:
                    return []

                plans = [
                    _require_plan_for_bundle(
                        connection,
                        planning_search_plan_id,
                        planning_bundle_id,
                    )
                    for planning_search_plan_id in requested_ids
                ]
                plans.sort(
                    key=lambda plan: (
                        int(plan["position"]),
                        int(plan["search_plan_row_id"]),
                    )
                )

                ledger_rows = {
                    int(row["planning_search_plan_id"]): row
                    for row in _select_ledger_rows(
                        connection,
                        run_id,
                        requested_ids,
                    )
                }

                if set(ledger_rows) != set(requested_ids):
                    missing = sorted(set(requested_ids) - set(ledger_rows))
                    raise SourceExecutionRepositoryError(
                        f"Cannot skip unregistered SearchPlans: {missing}."
                    )

                invalid = {
                    plan_id: str(row["status"])
                    for plan_id, row in ledger_rows.items()
                    if str(row["status"]) != PENDING
                }
                if invalid:
                    raise SourceExecutionRepositoryError(
                        "Only pending SearchPlans may be skipped: "
                        f"{invalid}."
                    )

                metadata_json = canonical_json(metadata or {})
                for plan in plans:
                    planning_search_plan_id = int(plan["search_plan_row_id"])
                    cursor = connection.execute(
                        """
                        UPDATE run_search_plan_statuses
                        SET
                            status = ?,
                            skip_reason = ?,
                            completed_at = ?,
                            updated_at = ?,
                            metadata_json = ?
                        WHERE run_id = ?
                          AND planning_search_plan_id = ?
                          AND status = ?
                        """,
                        (
                            SKIPPED,
                            skip_reason,
                            skipped_at,
                            skipped_at,
                            metadata_json,
                            run_id,
                            planning_search_plan_id,
                            PENDING,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise SourceExecutionRepositoryError(
                            "SearchPlan skip batch could not be applied atomically."
                        )

                return _list_status_records_for_ids(
                    connection,
                    run_id,
                    [int(plan["search_plan_row_id"]) for plan in plans],
                )
        except SourceExecutionRepositoryError:
            raise
        except Exception as error:
            raise SourceExecutionRepositoryError(
                "Failed to mark SearchPlans skipped."
            ) from error

    def start_search_plan_execution(
        self,
        run_id: str,
        planning_search_plan_id: int,
        execution: SourceExecutionStart,
        *,
        selection_order: int | None = None,
        started_at: str | None = None,
    ) -> SearchPlanExecutionStartResult:
        execution_time = started_at or utc_now_iso()
        _validate_selection_order(selection_order)
        prepared = _prepare_source_execution_start(execution)

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                run = _require_running_run_with_bundle(connection, run_id)
                planning_bundle_id = int(run["planning_bundle_id"])
                _require_plan_for_bundle(
                    connection,
                    planning_search_plan_id,
                    planning_bundle_id,
                )
                ledger_row = _require_ledger_row(
                    connection,
                    run_id,
                    planning_search_plan_id,
                )
                if str(ledger_row["status"]) != PENDING:
                    raise SourceExecutionRepositoryError(
                        "Only a pending SearchPlan may start execution; "
                        f"current status is {ledger_row['status']!r}."
                    )

                cursor = connection.execute(
                    """
                    UPDATE run_search_plan_statuses
                    SET
                        status = ?,
                        selection_order = ?,
                        started_at = ?,
                        updated_at = ?,
                        metadata_json = ?
                    WHERE run_id = ?
                      AND planning_search_plan_id = ?
                      AND status = ?
                    """,
                    (
                        RUNNING,
                        selection_order,
                        execution_time,
                        execution_time,
                        canonical_json(execution.metadata or {}),
                        run_id,
                        planning_search_plan_id,
                        PENDING,
                    ),
                )
                if cursor.rowcount != 1:
                    raise SourceExecutionRepositoryError(
                        "SearchPlan ledger transition to running failed."
                    )

                source_execution_id = _insert_source_execution(
                    connection=connection,
                    run_id=run_id,
                    planning_search_plan_id=planning_search_plan_id,
                    prepared=prepared,
                    started_at=execution_time,
                )
                return SearchPlanExecutionStartResult(
                    ledger_status=_get_status_record(
                        connection,
                        run_id,
                        planning_search_plan_id,
                    ),
                    source_execution=_get_source_execution_record(
                        connection,
                        source_execution_id,
                    ),
                )
        except SourceExecutionRepositoryError:
            raise
        except Exception as error:
            raise SourceExecutionRepositoryError(
                "Failed to start SearchPlan SourceExecution."
            ) from error

    def start_source_execution(
        self,
        run_id: str,
        execution: SourceExecutionStart,
        *,
        started_at: str | None = None,
    ) -> SourceExecutionRecord:
        execution_time = started_at or utc_now_iso()
        prepared = _prepare_source_execution_start(execution)

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                _require_running_run(connection, run_id)
                source_execution_id = _insert_source_execution(
                    connection=connection,
                    run_id=run_id,
                    planning_search_plan_id=None,
                    prepared=prepared,
                    started_at=execution_time,
                )
                return _get_source_execution_record(
                    connection,
                    source_execution_id,
                )
        except SourceExecutionRepositoryError:
            raise
        except Exception as error:
            raise SourceExecutionRepositoryError(
                "Failed to start SourceExecution."
            ) from error

    def complete_execution(
        self,
        source_execution_id: int,
        completion: SourceExecutionCompletion,
        *,
        completed_at: str | None = None,
    ) -> SourceExecutionRecord:
        completion_time = completed_at or utc_now_iso()

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                execution = _require_running_source_execution(
                    connection,
                    source_execution_id,
                )
                _require_running_run(connection, str(execution["run_id"]))
                discovery_count = _validated_discovery_count(
                    connection,
                    source_execution_id,
                    completion.discovered_item_count,
                )
                returned_count = _nonnegative_integer(
                    completion.returned_item_count,
                    "returned_item_count",
                )
                metadata_json = _merged_metadata_json(
                    execution["metadata_json"],
                    completion.metadata,
                )

                cursor = connection.execute(
                    """
                    UPDATE source_executions
                    SET
                        status = ?,
                        returned_item_count = ?,
                        discovered_item_count = ?,
                        completed_at = ?,
                        error_type = NULL,
                        error_message = NULL,
                        metadata_json = ?,
                        updated_at = ?
                    WHERE source_execution_id = ?
                      AND status = ?
                    """,
                    (
                        COMPLETED,
                        returned_count,
                        discovery_count,
                        completion_time,
                        metadata_json,
                        completion_time,
                        source_execution_id,
                        RUNNING,
                    ),
                )
                if cursor.rowcount != 1:
                    raise SourceExecutionRepositoryError(
                        "SourceExecution completion transition failed."
                    )

                _transition_backed_ledger_to_final(
                    connection=connection,
                    execution=execution,
                    target_status=COMPLETED,
                    completed_at=completion_time,
                )
                return _get_source_execution_record(
                    connection,
                    source_execution_id,
                )
        except SourceExecutionRepositoryError:
            raise
        except Exception as error:
            raise SourceExecutionRepositoryError(
                "Failed to complete SourceExecution."
            ) from error

    def fail_execution(
        self,
        source_execution_id: int,
        failure: SourceExecutionFailure,
        *,
        failed_at: str | None = None,
    ) -> SourceExecutionRecord:
        failure_time = failed_at or utc_now_iso()
        error_type = _required_text(failure.error_type, "error_type")
        error_message = _bounded_required_text(
            failure.error_message,
            "error_message",
            MAX_ERROR_MESSAGE_LENGTH,
        )

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                execution = _require_running_source_execution(
                    connection,
                    source_execution_id,
                )
                _require_running_run(connection, str(execution["run_id"]))
                discovery_count = _validated_discovery_count(
                    connection,
                    source_execution_id,
                    failure.discovered_item_count,
                )
                returned_count = (
                    _nonnegative_integer(
                        failure.returned_item_count,
                        "returned_item_count",
                    )
                    if failure.returned_item_count is not None
                    else None
                )
                metadata_json = _merged_metadata_json(
                    execution["metadata_json"],
                    failure.metadata,
                )

                cursor = connection.execute(
                    """
                    UPDATE source_executions
                    SET
                        status = ?,
                        returned_item_count = ?,
                        discovered_item_count = ?,
                        completed_at = ?,
                        error_type = ?,
                        error_message = ?,
                        metadata_json = ?,
                        updated_at = ?
                    WHERE source_execution_id = ?
                      AND status = ?
                    """,
                    (
                        FAILED,
                        returned_count,
                        discovery_count,
                        failure_time,
                        error_type,
                        error_message,
                        metadata_json,
                        failure_time,
                        source_execution_id,
                        RUNNING,
                    ),
                )
                if cursor.rowcount != 1:
                    raise SourceExecutionRepositoryError(
                        "SourceExecution failure transition failed."
                    )

                _transition_backed_ledger_to_final(
                    connection=connection,
                    execution=execution,
                    target_status=FAILED,
                    completed_at=failure_time,
                )
                return _get_source_execution_record(
                    connection,
                    source_execution_id,
                )
        except SourceExecutionRepositoryError:
            raise
        except Exception as error:
            raise SourceExecutionRepositoryError(
                "Failed to mark SourceExecution failed."
            ) from error

    def record_discoveries(
        self,
        source_execution_id: int,
        discoveries: Iterable[SourceItemDiscoveryWrite],
    ) -> list[SourceItemDiscoveryRecord]:
        discovery_list = list(discoveries)
        source_item_ids = [
            _positive_integer(item.source_item_id, "source_item_id")
            for item in discovery_list
        ]

        if len(source_item_ids) != len(set(source_item_ids)):
            raise SourceExecutionRepositoryError(
                "Discovery batch contains duplicate source_item_id values."
            )

        for item in discovery_list:
            if item.result_position is not None:
                _nonnegative_integer(item.result_position, "result_position")

        try:
            initialize_database(database_path=self.database_path)
            with database_connection(self.database_path) as connection:
                _require_running_source_execution(
                    connection,
                    source_execution_id,
                )

                if not discovery_list:
                    return []

                placeholders = ",".join("?" for _ in source_item_ids)
                existing_source_ids = {
                    int(row["source_item_id"])
                    for row in connection.execute(
                        f"""
                        SELECT source_item_id
                        FROM source_items
                        WHERE source_item_id IN ({placeholders})
                        """,
                        tuple(source_item_ids),
                    ).fetchall()
                }
                missing_source_ids = sorted(
                    set(source_item_ids) - existing_source_ids
                )
                if missing_source_ids:
                    raise SourceExecutionRepositoryError(
                        "Discovery batch references unknown SourceItems: "
                        f"{missing_source_ids}."
                    )

                for item in discovery_list:
                    discovered_at = item.discovered_at or utc_now_iso()
                    connection.execute(
                        """
                        INSERT INTO source_item_discoveries (
                            source_execution_id,
                            source_item_id,
                            result_position,
                            discovered_at,
                            metadata_json
                        )
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (source_execution_id, source_item_id)
                        DO NOTHING
                        """,
                        (
                            source_execution_id,
                            item.source_item_id,
                            item.result_position,
                            discovered_at,
                            canonical_json(item.metadata or {}),
                        ),
                    )

                return _list_discovery_records_for_ids(
                    connection,
                    source_execution_id,
                    source_item_ids,
                )
        except SourceExecutionRepositoryError:
            raise
        except Exception as error:
            raise SourceExecutionRepositoryError(
                "Failed to record SourceItem discoveries."
            ) from error

    def list_run_search_plan_statuses(
        self,
        run_id: str,
    ) -> list[RunSearchPlanStatusRecord]:
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)
        try:
            _require_run_with_bundle(connection, run_id)
            return _list_all_status_records(connection, run_id)
        finally:
            connection.close()

    def list_unexecuted_search_plans(
        self,
        run_id: str,
    ) -> list[RunSearchPlanStatusRecord]:
        return [
            record
            for record in self.list_run_search_plan_statuses(run_id)
            if record.status in {PENDING, SKIPPED}
        ]

    def get_run_search_plan_coverage(
        self,
        run_id: str,
    ) -> RunSearchPlanCoverage:
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)
        try:
            run = _require_run_with_bundle(connection, run_id)
            planning_bundle_id = int(run["planning_bundle_id"])
            plans = _list_valid_bundle_plans(connection, planning_bundle_id)
            bundle_plan_ids = {
                int(plan["search_plan_row_id"])
                for plan in plans
            }
            rows = connection.execute(
                """
                SELECT
                    ledger.planning_search_plan_id,
                    ledger.status,
                    plan.planning_bundle_id
                FROM run_search_plan_statuses AS ledger
                JOIN planning_search_plans AS plan
                  ON plan.search_plan_row_id = ledger.planning_search_plan_id
                WHERE ledger.run_id = ?
                """,
                (run_id,),
            ).fetchall()
            matching_rows = [
                row
                for row in rows
                if int(row["planning_bundle_id"]) == planning_bundle_id
                and int(row["planning_search_plan_id"]) in bundle_plan_ids
            ]
            registered_ids = {
                int(row["planning_search_plan_id"])
                for row in matching_rows
            }
            unexpected_ids = tuple(
                sorted(
                    int(row["planning_search_plan_id"])
                    for row in rows
                    if int(row["planning_bundle_id"]) != planning_bundle_id
                    or int(row["planning_search_plan_id"]) not in bundle_plan_ids
                )
            )
            missing_ids = tuple(sorted(bundle_plan_ids - registered_ids))
            status_counts = {
                status: sum(
                    1
                    for row in matching_rows
                    if str(row["status"]) == status
                )
                for status in LEDGER_STATUSES
            }
            return RunSearchPlanCoverage(
                run_id=run_id,
                planning_bundle_id=planning_bundle_id,
                total_bundle_plans=len(bundle_plan_ids),
                registered_plans=len(registered_ids),
                pending=status_counts[PENDING],
                running=status_counts[RUNNING],
                completed=status_counts[COMPLETED],
                failed=status_counts[FAILED],
                skipped=status_counts[SKIPPED],
                missing_unregistered=len(missing_ids),
                unexpected_registered=len(unexpected_ids),
                missing_plan_ids=missing_ids,
                unexpected_plan_ids=unexpected_ids,
            )
        finally:
            connection.close()

    def assert_run_search_plan_accounting_complete(
        self,
        run_id: str,
    ) -> RunSearchPlanCoverage:
        coverage = self.get_run_search_plan_coverage(run_id)
        if (
            coverage.registered_plans != coverage.total_bundle_plans
            or coverage.missing_unregistered
            or coverage.unexpected_registered
            or coverage.pending
            or coverage.running
        ):
            raise SourceExecutionRepositoryError(
                "PipelineRun SearchPlan accounting is incomplete: "
                f"total={coverage.total_bundle_plans}, "
                f"registered={coverage.registered_plans}, "
                f"pending={coverage.pending}, running={coverage.running}, "
                f"missing={coverage.missing_unregistered}, "
                f"unexpected={coverage.unexpected_registered}."
            )
        return coverage

    def get_run_search_query_coverage(
        self,
        run_id: str,
    ) -> list[SearchQueryCoverageRecord]:
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)
        try:
            run = _require_run_with_bundle(connection, run_id)
            planning_bundle_id = int(run["planning_bundle_id"])
            queries = connection.execute(
                """
                SELECT search_query_row_id, query_identity, position
                FROM planning_search_queries
                WHERE planning_bundle_id = ?
                ORDER BY position, search_query_row_id
                """,
                (planning_bundle_id,),
            ).fetchall()
            plans = _list_valid_bundle_plans(connection, planning_bundle_id)
            ledger_rows = connection.execute(
                """
                SELECT planning_search_plan_id, status
                FROM run_search_plan_statuses
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchall()
            statuses_by_plan_id = {
                int(row["planning_search_plan_id"]): str(row["status"])
                for row in ledger_rows
            }
            plans_by_query: dict[int, list[sqlite3.Row]] = {}
            for plan in plans:
                plans_by_query.setdefault(
                    int(plan["search_query_row_id"]),
                    [],
                ).append(plan)

            results: list[SearchQueryCoverageRecord] = []
            for query in queries:
                search_query_row_id = int(query["search_query_row_id"])
                query_plans = plans_by_query.get(search_query_row_id, [])
                query_statuses = [
                    statuses_by_plan_id.get(int(plan["search_plan_row_id"]))
                    for plan in query_plans
                ]
                status_counts = {
                    status: query_statuses.count(status)
                    for status in LEDGER_STATUSES
                }
                missing_count = query_statuses.count(None)
                entered_execution = sum(
                    status_counts[status]
                    for status in (RUNNING, COMPLETED, FAILED)
                )
                results.append(
                    SearchQueryCoverageRecord(
                        run_id=run_id,
                        planning_bundle_id=planning_bundle_id,
                        search_query_row_id=search_query_row_id,
                        query_identity=str(query["query_identity"]),
                        query_position=int(query["position"]),
                        total_search_plans=len(query_plans),
                        completed=status_counts[COMPLETED],
                        failed=status_counts[FAILED],
                        skipped=status_counts[SKIPPED],
                        pending=status_counts[PENDING],
                        running=status_counts[RUNNING],
                        missing_unregistered=missing_count,
                        no_search_plans_generated=not query_plans,
                        no_plans_entered_execution=(
                            bool(query_plans) and entered_execution == 0
                        ),
                    )
                )
            return results
        finally:
            connection.close()

    def get_source_execution(
        self,
        source_execution_id: int,
    ) -> SourceExecutionRecord | None:
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)
        try:
            row = connection.execute(
                """
                SELECT *
                FROM source_executions
                WHERE source_execution_id = ?
                """,
                (source_execution_id,),
            ).fetchone()
            return _row_to_source_execution_record(row) if row is not None else None
        finally:
            connection.close()

    def list_source_executions(self, run_id: str) -> list[SourceExecutionRecord]:
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)
        try:
            _require_run(connection, run_id)
            rows = connection.execute(
                """
                SELECT *
                FROM source_executions
                WHERE run_id = ?
                ORDER BY started_at, source_execution_id
                """,
                (run_id,),
            ).fetchall()
            return [_row_to_source_execution_record(row) for row in rows]
        finally:
            connection.close()

    def list_discoveries(
        self,
        source_execution_id: int,
    ) -> list[SourceItemDiscoveryRecord]:
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)
        try:
            _require_source_execution(connection, source_execution_id)
            rows = connection.execute(
                """
                SELECT *
                FROM source_item_discoveries
                WHERE source_execution_id = ?
                ORDER BY
                    result_position IS NULL,
                    result_position,
                    source_item_id
                """,
                (source_execution_id,),
            ).fetchall()
            return [_row_to_discovery_record(row) for row in rows]
        finally:
            connection.close()


def _require_run(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    normalized_run_id = _required_text(run_id, "run_id")
    row = connection.execute(
        "SELECT * FROM pipeline_runs WHERE run_id = ?",
        (normalized_run_id,),
    ).fetchone()
    if row is None:
        raise SourceExecutionRepositoryError(
            f"PipelineRun {normalized_run_id!r} was not found."
        )
    return row


def _require_running_run(
    connection: sqlite3.Connection,
    run_id: str,
) -> sqlite3.Row:
    row = _require_run(connection, run_id)
    if str(row["status"]) != RUNNING:
        raise SourceExecutionRepositoryError(
            f"PipelineRun {run_id!r} is not running."
        )
    return row


def _require_run_with_bundle(
    connection: sqlite3.Connection,
    run_id: str,
) -> sqlite3.Row:
    row = _require_run(connection, run_id)
    if row["planning_bundle_id"] is None:
        raise SourceExecutionRepositoryError(
            f"PipelineRun {run_id!r} has no attached Planning Bundle."
        )
    bundle = connection.execute(
        "SELECT planning_bundle_id FROM planning_bundles WHERE planning_bundle_id = ?",
        (row["planning_bundle_id"],),
    ).fetchone()
    if bundle is None:
        raise SourceExecutionRepositoryError(
            f"PipelineRun {run_id!r} references a missing Planning Bundle."
        )
    return row


def _require_running_run_with_bundle(
    connection: sqlite3.Connection,
    run_id: str,
) -> sqlite3.Row:
    row = _require_running_run(connection, run_id)
    if row["planning_bundle_id"] is None:
        raise SourceExecutionRepositoryError(
            f"PipelineRun {run_id!r} has no attached Planning Bundle."
        )
    return row


def _list_valid_bundle_plans(
    connection: sqlite3.Connection,
    planning_bundle_id: int,
) -> list[sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT
            plan.*,
            query.planning_bundle_id AS query_bundle_id,
            query.career_path_row_id AS query_career_path_row_id,
            path.planning_bundle_id AS path_bundle_id
        FROM planning_search_plans AS plan
        LEFT JOIN planning_search_queries AS query
          ON query.search_query_row_id = plan.search_query_row_id
        LEFT JOIN planning_target_career_paths AS path
          ON path.career_path_row_id = plan.career_path_row_id
        WHERE plan.planning_bundle_id = ?
        ORDER BY plan.position, plan.search_plan_row_id
        """,
        (planning_bundle_id,),
    ).fetchall()
    for row in rows:
        if (
            row["query_bundle_id"] is None
            or int(row["query_bundle_id"]) != planning_bundle_id
            or row["query_career_path_row_id"] is None
            or int(row["query_career_path_row_id"]) != int(row["career_path_row_id"])
            or row["path_bundle_id"] is None
            or int(row["path_bundle_id"]) != planning_bundle_id
        ):
            raise SourceExecutionRepositoryError(
                "Planning Bundle contains an inconsistent SearchPlan relationship."
            )
    return rows


def _require_plan_for_bundle(
    connection: sqlite3.Connection,
    planning_search_plan_id: int,
    planning_bundle_id: int,
) -> sqlite3.Row:
    plan_id = _positive_integer(
        planning_search_plan_id,
        "planning_search_plan_id",
    )
    row = connection.execute(
        """
        SELECT
            plan.*,
            query.planning_bundle_id AS query_bundle_id,
            query.career_path_row_id AS query_career_path_row_id,
            path.planning_bundle_id AS path_bundle_id
        FROM planning_search_plans AS plan
        LEFT JOIN planning_search_queries AS query
          ON query.search_query_row_id = plan.search_query_row_id
        LEFT JOIN planning_target_career_paths AS path
          ON path.career_path_row_id = plan.career_path_row_id
        WHERE plan.search_plan_row_id = ?
        """,
        (plan_id,),
    ).fetchone()
    if row is None:
        raise SourceExecutionRepositoryError(
            f"SearchPlan row {plan_id} was not found."
        )
    if int(row["planning_bundle_id"]) != planning_bundle_id:
        raise SourceExecutionRepositoryError(
            f"SearchPlan row {plan_id} belongs to another Planning Bundle."
        )
    if (
        row["query_bundle_id"] is None
        or int(row["query_bundle_id"]) != planning_bundle_id
        or row["query_career_path_row_id"] is None
        or int(row["query_career_path_row_id"]) != int(row["career_path_row_id"])
        or row["path_bundle_id"] is None
        or int(row["path_bundle_id"]) != planning_bundle_id
    ):
        raise SourceExecutionRepositoryError(
            f"SearchPlan row {plan_id} has inconsistent parent relationships."
        )
    return row


def _select_ledger_rows(
    connection: sqlite3.Connection,
    run_id: str,
    planning_search_plan_ids: list[int],
) -> list[sqlite3.Row]:
    if not planning_search_plan_ids:
        return []
    placeholders = ",".join("?" for _ in planning_search_plan_ids)
    return connection.execute(
        f"""
        SELECT *
        FROM run_search_plan_statuses
        WHERE run_id = ?
          AND planning_search_plan_id IN ({placeholders})
        """,
        (run_id, *planning_search_plan_ids),
    ).fetchall()


def _require_ledger_row(
    connection: sqlite3.Connection,
    run_id: str,
    planning_search_plan_id: int,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT *
        FROM run_search_plan_statuses
        WHERE run_id = ? AND planning_search_plan_id = ?
        """,
        (run_id, planning_search_plan_id),
    ).fetchone()
    if row is None:
        raise SourceExecutionRepositoryError(
            f"SearchPlan row {planning_search_plan_id} is not registered for "
            f"PipelineRun {run_id!r}."
        )
    return row


def _prepare_source_execution_start(
    execution: SourceExecutionStart,
) -> dict[str, Any]:
    requested_result_limit = (
        _nonnegative_integer(
            execution.requested_result_limit,
            "requested_result_limit",
        )
        if execution.requested_result_limit is not None
        else None
    )
    return {
        "source_type": _required_text(execution.source_type, "source_type"),
        "provider": _optional_text(execution.provider),
        "source_key": _optional_text(execution.source_key),
        "source_name": _optional_text(execution.source_name),
        "source_locator": _optional_text(execution.source_locator),
        "execution_mode": _optional_text(execution.execution_mode),
        "requested_result_limit": requested_result_limit,
        "request_fingerprint": _optional_text(execution.request_fingerprint),
        "metadata_json": canonical_json(execution.metadata or {}),
    }


def _insert_source_execution(
    *,
    connection: sqlite3.Connection,
    run_id: str,
    planning_search_plan_id: int | None,
    prepared: dict[str, Any],
    started_at: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO source_executions (
            run_id,
            planning_search_plan_id,
            source_type,
            provider,
            source_key,
            source_name,
            source_locator,
            execution_mode,
            status,
            requested_result_limit,
            returned_item_count,
            discovered_item_count,
            request_fingerprint,
            started_at,
            completed_at,
            error_type,
            error_message,
            metadata_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, NULL, NULL, ?, ?, ?)
        """,
        (
            run_id,
            planning_search_plan_id,
            prepared["source_type"],
            prepared["provider"],
            prepared["source_key"],
            prepared["source_name"],
            prepared["source_locator"],
            prepared["execution_mode"],
            RUNNING,
            prepared["requested_result_limit"],
            prepared["request_fingerprint"],
            started_at,
            prepared["metadata_json"],
            started_at,
            started_at,
        ),
    )
    if cursor.rowcount != 1:
        raise SourceExecutionRepositoryError(
            "Failed to create running SourceExecution."
        )
    return int(cursor.lastrowid)


def _require_source_execution(
    connection: sqlite3.Connection,
    source_execution_id: int,
) -> sqlite3.Row:
    execution_id = _positive_integer(
        source_execution_id,
        "source_execution_id",
    )
    row = connection.execute(
        "SELECT * FROM source_executions WHERE source_execution_id = ?",
        (execution_id,),
    ).fetchone()
    if row is None:
        raise SourceExecutionRepositoryError(
            f"SourceExecution {execution_id} was not found."
        )
    return row


def _require_running_source_execution(
    connection: sqlite3.Connection,
    source_execution_id: int,
) -> sqlite3.Row:
    row = _require_source_execution(connection, source_execution_id)
    if str(row["status"]) != RUNNING:
        raise SourceExecutionRepositoryError(
            f"SourceExecution {source_execution_id} is not running."
        )
    return row


def _transition_backed_ledger_to_final(
    *,
    connection: sqlite3.Connection,
    execution: sqlite3.Row,
    target_status: str,
    completed_at: str,
) -> None:
    planning_search_plan_id = execution["planning_search_plan_id"]
    if planning_search_plan_id is None:
        return
    cursor = connection.execute(
        """
        UPDATE run_search_plan_statuses
        SET
            status = ?,
            completed_at = ?,
            updated_at = ?
        WHERE run_id = ?
          AND planning_search_plan_id = ?
          AND status = ?
        """,
        (
            target_status,
            completed_at,
            completed_at,
            execution["run_id"],
            planning_search_plan_id,
            RUNNING,
        ),
    )
    if cursor.rowcount != 1:
        raise SourceExecutionRepositoryError(
            "SearchPlan ledger final transition failed."
        )


def _validated_discovery_count(
    connection: sqlite3.Connection,
    source_execution_id: int,
    expected_count: int | None,
) -> int:
    actual_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM source_item_discoveries
            WHERE source_execution_id = ?
            """,
            (source_execution_id,),
        ).fetchone()[0]
    )
    if expected_count is not None:
        normalized_expected = _nonnegative_integer(
            expected_count,
            "discovered_item_count",
        )
        if normalized_expected != actual_count:
            raise SourceExecutionRepositoryError(
                "discovered_item_count does not match committed discoveries."
            )
    return actual_count


def _list_all_status_records(
    connection: sqlite3.Connection,
    run_id: str,
) -> list[RunSearchPlanStatusRecord]:
    rows = connection.execute(
        """
        SELECT
            ledger.*,
            plan.planning_bundle_id,
            plan.search_query_row_id,
            plan.plan_identity,
            plan.position AS plan_position
        FROM run_search_plan_statuses AS ledger
        JOIN planning_search_plans AS plan
          ON plan.search_plan_row_id = ledger.planning_search_plan_id
        WHERE ledger.run_id = ?
        ORDER BY plan.position, plan.search_plan_row_id
        """,
        (run_id,),
    ).fetchall()
    return [_row_to_status_record(row) for row in rows]


def _list_status_records_for_ids(
    connection: sqlite3.Connection,
    run_id: str,
    planning_search_plan_ids: list[int],
) -> list[RunSearchPlanStatusRecord]:
    requested = set(planning_search_plan_ids)
    return [
        record
        for record in _list_all_status_records(connection, run_id)
        if record.planning_search_plan_id in requested
    ]


def _get_status_record(
    connection: sqlite3.Connection,
    run_id: str,
    planning_search_plan_id: int,
) -> RunSearchPlanStatusRecord:
    records = _list_status_records_for_ids(
        connection,
        run_id,
        [planning_search_plan_id],
    )
    if len(records) != 1:
        raise SourceExecutionRepositoryError(
            "SearchPlan ledger row could not be reconstructed."
        )
    return records[0]


def _get_source_execution_record(
    connection: sqlite3.Connection,
    source_execution_id: int,
) -> SourceExecutionRecord:
    row = _require_source_execution(connection, source_execution_id)
    return _row_to_source_execution_record(row)


def _list_discovery_records_for_ids(
    connection: sqlite3.Connection,
    source_execution_id: int,
    source_item_ids: list[int],
) -> list[SourceItemDiscoveryRecord]:
    requested = set(source_item_ids)
    rows = connection.execute(
        """
        SELECT *
        FROM source_item_discoveries
        WHERE source_execution_id = ?
        ORDER BY
            result_position IS NULL,
            result_position,
            source_item_id
        """,
        (source_execution_id,),
    ).fetchall()
    return [
        _row_to_discovery_record(row)
        for row in rows
        if int(row["source_item_id"]) in requested
    ]


def _row_to_status_record(row: sqlite3.Row) -> RunSearchPlanStatusRecord:
    return RunSearchPlanStatusRecord(
        run_search_plan_status_id=int(row["run_search_plan_status_id"]),
        run_id=str(row["run_id"]),
        planning_search_plan_id=int(row["planning_search_plan_id"]),
        planning_bundle_id=int(row["planning_bundle_id"]),
        search_query_row_id=int(row["search_query_row_id"]),
        plan_identity=str(row["plan_identity"]),
        plan_position=int(row["plan_position"]),
        status=str(row["status"]),
        skip_reason=row["skip_reason"],
        selection_order=(
            int(row["selection_order"])
            if row["selection_order"] is not None
            else None
        ),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        metadata=_json_object(row["metadata_json"]),
    )


def _row_to_source_execution_record(row: sqlite3.Row) -> SourceExecutionRecord:
    return SourceExecutionRecord(
        source_execution_id=int(row["source_execution_id"]),
        run_id=str(row["run_id"]),
        planning_search_plan_id=(
            int(row["planning_search_plan_id"])
            if row["planning_search_plan_id"] is not None
            else None
        ),
        source_type=str(row["source_type"]),
        provider=row["provider"],
        source_key=row["source_key"],
        source_name=row["source_name"],
        source_locator=row["source_locator"],
        execution_mode=row["execution_mode"],
        status=str(row["status"]),
        requested_result_limit=(
            int(row["requested_result_limit"])
            if row["requested_result_limit"] is not None
            else None
        ),
        returned_item_count=(
            int(row["returned_item_count"])
            if row["returned_item_count"] is not None
            else None
        ),
        discovered_item_count=(
            int(row["discovered_item_count"])
            if row["discovered_item_count"] is not None
            else None
        ),
        request_fingerprint=row["request_fingerprint"],
        started_at=str(row["started_at"]),
        completed_at=row["completed_at"],
        error_type=row["error_type"],
        error_message=row["error_message"],
        metadata=_json_object(row["metadata_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_discovery_record(row: sqlite3.Row) -> SourceItemDiscoveryRecord:
    return SourceItemDiscoveryRecord(
        source_execution_id=int(row["source_execution_id"]),
        source_item_id=int(row["source_item_id"]),
        result_position=(
            int(row["result_position"])
            if row["result_position"] is not None
            else None
        ),
        discovered_at=str(row["discovered_at"]),
        metadata=_json_object(row["metadata_json"]),
    )


def _merged_metadata_json(
    existing_json: str,
    updates: dict[str, Any] | None,
) -> str:
    metadata = _json_object(existing_json)
    metadata.update(updates or {})
    return canonical_json(metadata)


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise SourceExecutionRepositoryError(
            "Stored metadata_json is not a JSON object."
        )
    return parsed


def _unique_integer_ids(values: Iterable[int]) -> list[int]:
    unique: list[int] = []
    seen: set[int] = set()
    for value in values:
        normalized = _positive_integer(value, "planning_search_plan_id")
        if normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)
    return unique


def _validate_selection_order(selection_order: int | None) -> None:
    if selection_order is not None:
        _nonnegative_integer(selection_order, "selection_order")


def _positive_integer(value: int, field_name: str) -> int:
    normalized = _nonnegative_integer(value, field_name)
    if normalized == 0:
        raise SourceExecutionRepositoryError(
            f"{field_name} must be a positive integer."
        )
    return normalized


def _nonnegative_integer(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SourceExecutionRepositoryError(
            f"{field_name} must be a non-negative integer."
        )
    return value


def _required_text(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise SourceExecutionRepositoryError(f"{field_name} is required.")
    return normalized


def _bounded_required_text(value: str, field_name: str, limit: int) -> str:
    normalized = _required_text(value, field_name)
    if len(normalized) > limit:
        raise SourceExecutionRepositoryError(
            f"{field_name} must not exceed {limit} characters."
        )
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
