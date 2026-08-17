from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
import uuid

from src.database.connection import database_connection, open_database_connection
from src.database.migrations import initialize_database
from src.database.planning_identity import canonical_json
from src.models import utc_now_iso


RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
VALID_STATUSES = {RUNNING, COMPLETED, FAILED}


@dataclass(frozen=True)
class PipelineRunStart:
    pipeline_version: str
    phase: str
    execution_mode: str | None = None
    metadata: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class PipelineRunCompletion:
    summary: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class PipelineRunFailure:
    failure_stage: str
    error_type: str
    error_message: str
    summary: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class PipelineRunRecord:
    run_id: str
    pipeline_version: str
    phase: str
    status: str
    started_at: str
    completed_at: str | None
    planning_bundle_id: int | None
    execution_mode: str | None
    failure_stage: str | None
    error_type: str | None
    error_message: str | None
    summary: dict[str, Any]
    metadata: dict[str, Any]
    updated_at: str | None


class PipelineRunRepositoryError(Exception):
    """
    Raised when PipelineRun persistence fails.
    """


class PipelineRunRepository:
    """
    Repository for the top-level PipelineRun lifecycle.
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = database_path

    def start_run(
        self,
        run: PipelineRunStart,
        *,
        started_at: str | None = None,
    ) -> PipelineRunRecord:
        run_id = _normalize_text(run.run_id) or _new_run_id()
        started_time = started_at or utc_now_iso()

        try:
            initialize_database(database_path=self.database_path)

            with database_connection(self.database_path) as connection:
                connection.execute(
                    """
                    INSERT INTO pipeline_runs (
                        run_id,
                        pipeline_version,
                        phase,
                        status,
                        started_at,
                        completed_at,
                        summary_json,
                        metadata_json,
                        planning_bundle_id,
                        execution_mode,
                        failure_stage,
                        error_type,
                        error_message,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?, NULL, NULL, NULL, ?)
                    """,
                    (
                        run_id,
                        _required_text(run.pipeline_version, "pipeline_version"),
                        _required_text(run.phase, "phase"),
                        RUNNING,
                        started_time,
                        _json_or_empty(run.summary),
                        _json_or_empty(run.metadata),
                        _empty_to_none(run.execution_mode),
                        started_time,
                    ),
                )
                row = _get_run_row(connection, run_id)

            return _row_to_record(row)
        except PipelineRunRepositoryError:
            raise
        except Exception as error:
            raise PipelineRunRepositoryError(
                "Failed to start PipelineRun."
            ) from error

    def attach_planning_bundle(
        self,
        run_id: str,
        planning_bundle_id: int,
        *,
        updated_at: str | None = None,
    ) -> PipelineRunRecord:
        update_time = updated_at or utc_now_iso()

        try:
            initialize_database(database_path=self.database_path)

            with database_connection(self.database_path) as connection:
                cursor = connection.execute(
                    """
                    UPDATE pipeline_runs
                    SET
                        planning_bundle_id = ?,
                        updated_at = ?
                    WHERE run_id = ?
                      AND status = ?
                      AND (
                          planning_bundle_id IS NULL
                          OR planning_bundle_id = ?
                      )
                    """,
                    (
                        planning_bundle_id,
                        update_time,
                        run_id,
                        RUNNING,
                        planning_bundle_id,
                    ),
                )

                if cursor.rowcount != 1:
                    _raise_attach_transition_error(
                        connection,
                        run_id,
                        planning_bundle_id,
                    )

                row = _get_run_row(connection, run_id)

            return _row_to_record(row)
        except PipelineRunRepositoryError:
            raise
        except Exception as error:
            raise PipelineRunRepositoryError(
                "Failed to attach Planning Bundle to PipelineRun."
            ) from error

    def complete_run(
        self,
        run_id: str,
        completion: PipelineRunCompletion | None = None,
        *,
        completed_at: str | None = None,
        require_planning_bundle: bool = True,
    ) -> PipelineRunRecord:
        completion = completion or PipelineRunCompletion()
        completed_time = completed_at or utc_now_iso()

        try:
            initialize_database(database_path=self.database_path)

            with database_connection(self.database_path) as connection:
                cursor = connection.execute(
                    """
                    UPDATE pipeline_runs
                    SET
                        status = ?,
                        completed_at = ?,
                        summary_json = ?,
                        metadata_json = ?,
                        failure_stage = NULL,
                        error_type = NULL,
                        error_message = NULL,
                        updated_at = ?
                    WHERE run_id = ?
                      AND status = ?
                      AND (
                          ? = 0
                          OR planning_bundle_id IS NOT NULL
                      )
                    """,
                    (
                        COMPLETED,
                        completed_time,
                        _json_or_empty(completion.summary),
                        _json_or_empty(completion.metadata),
                        completed_time,
                        run_id,
                        RUNNING,
                        1 if require_planning_bundle else 0,
                    ),
                )

                if cursor.rowcount != 1:
                    _raise_complete_transition_error(
                        connection,
                        run_id,
                        require_planning_bundle=require_planning_bundle,
                    )

                row = _get_run_row(connection, run_id)

            return _row_to_record(row)
        except PipelineRunRepositoryError:
            raise
        except Exception as error:
            raise PipelineRunRepositoryError(
                "Failed to complete PipelineRun."
            ) from error

    def fail_run(
        self,
        run_id: str,
        failure: PipelineRunFailure,
        *,
        failed_at: str | None = None,
    ) -> PipelineRunRecord:
        failed_time = failed_at or utc_now_iso()

        try:
            initialize_database(database_path=self.database_path)

            with database_connection(self.database_path) as connection:
                cursor = connection.execute(
                    """
                    UPDATE pipeline_runs
                    SET
                        status = ?,
                        completed_at = ?,
                        summary_json = ?,
                        metadata_json = ?,
                        failure_stage = ?,
                        error_type = ?,
                        error_message = ?,
                        updated_at = ?
                    WHERE run_id = ?
                      AND status = ?
                    """,
                    (
                        FAILED,
                        failed_time,
                        _json_or_empty(failure.summary),
                        _json_or_empty(failure.metadata),
                        _required_text(failure.failure_stage, "failure_stage"),
                        _required_text(failure.error_type, "error_type"),
                        _concise_error_message(failure.error_message),
                        failed_time,
                        run_id,
                        RUNNING,
                    ),
                )

                if cursor.rowcount != 1:
                    _raise_running_transition_error(
                        connection,
                        run_id,
                        action="fail",
                    )

                row = _get_run_row(connection, run_id)

            return _row_to_record(row)
        except PipelineRunRepositoryError:
            raise
        except Exception as error:
            raise PipelineRunRepositoryError(
                "Failed to mark PipelineRun failed."
            ) from error

    def get_run(self, run_id: str) -> PipelineRunRecord | None:
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            row = connection.execute(
                """
                SELECT *
                FROM pipeline_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

            return _row_to_record(row) if row is not None else None
        finally:
            connection.close()

    def list_recent_runs(self, *, limit: int = 20) -> list[PipelineRunRecord]:
        _validate_limit(limit)
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            rows = connection.execute(
                """
                SELECT *
                FROM pipeline_runs
                ORDER BY started_at DESC, run_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            return [_row_to_record(row) for row in rows]
        finally:
            connection.close()

    def list_runs_by_status(
        self,
        status: str,
        *,
        limit: int = 20,
    ) -> list[PipelineRunRecord]:
        _validate_status(status)
        _validate_limit(limit)
        initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            rows = connection.execute(
                """
                SELECT *
                FROM pipeline_runs
                WHERE status = ?
                ORDER BY started_at DESC, run_id DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()

            return [_row_to_record(row) for row in rows]
        finally:
            connection.close()


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex}"


def _get_run_row(connection, run_id: str):
    return connection.execute(
        """
        SELECT *
        FROM pipeline_runs
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()


def _row_to_record(row) -> PipelineRunRecord:
    return PipelineRunRecord(
        run_id=str(row["run_id"]),
        pipeline_version=str(row["pipeline_version"]),
        phase=str(row["phase"]),
        status=str(row["status"]),
        started_at=str(row["started_at"]),
        completed_at=row["completed_at"],
        planning_bundle_id=(
            int(row["planning_bundle_id"])
            if row["planning_bundle_id"] is not None
            else None
        ),
        execution_mode=row["execution_mode"],
        failure_stage=row["failure_stage"],
        error_type=row["error_type"],
        error_message=row["error_message"],
        summary=_json_dict(row["summary_json"]),
        metadata=_json_dict(row["metadata_json"]),
        updated_at=row["updated_at"],
    )


def _json_or_empty(value: dict[str, Any] | None) -> str:
    return canonical_json(value or {})


def _json_dict(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}

    parsed = json.loads(value)

    if not isinstance(parsed, dict):
        raise PipelineRunRepositoryError(
            "PipelineRun JSON payload must decode to an object."
        )

    return parsed


def _required_text(value: str | None, field_name: str) -> str:
    stripped = _normalize_text(value)

    if stripped is None:
        raise PipelineRunRepositoryError(f"{field_name} is required.")

    return stripped


def _empty_to_none(value: str | None) -> str | None:
    return _normalize_text(value)


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = str(value).strip()
    return stripped or None


def _concise_error_message(error_message: str) -> str:
    return _required_text(error_message, "error_message")[:1000]


def _validate_limit(limit: int) -> None:
    if not isinstance(limit, int) or limit <= 0:
        raise PipelineRunRepositoryError("Recent run limit must be a positive integer.")


def _validate_status(status: str) -> None:
    if status not in VALID_STATUSES:
        raise PipelineRunRepositoryError(f"Invalid PipelineRun status: {status!r}.")


def _raise_attach_transition_error(
    connection,
    run_id: str,
    planning_bundle_id: int,
) -> None:
    run_row = _get_run_row(connection, run_id)

    if run_row is None:
        raise PipelineRunRepositoryError(
            f"PipelineRun {run_id!r} was not found."
        )

    if run_row["status"] != RUNNING:
        raise PipelineRunRepositoryError(
            "Planning Bundle can only be attached to a running PipelineRun."
        )

    existing_bundle_id = run_row["planning_bundle_id"]

    if (
        existing_bundle_id is not None
        and int(existing_bundle_id) != int(planning_bundle_id)
    ):
        raise PipelineRunRepositoryError(
            "PipelineRun already references a different Planning Bundle."
        )

    raise PipelineRunRepositoryError(
        "Planning Bundle attachment did not update a PipelineRun."
    )


def _raise_complete_transition_error(
    connection,
    run_id: str,
    *,
    require_planning_bundle: bool,
) -> None:
    run_row = _get_run_row(connection, run_id)

    if run_row is None:
        raise PipelineRunRepositoryError(
            f"PipelineRun {run_id!r} was not found."
        )

    if run_row["status"] != RUNNING:
        raise PipelineRunRepositoryError(
            "Only a running PipelineRun can be completed."
        )

    if require_planning_bundle and run_row["planning_bundle_id"] is None:
        raise PipelineRunRepositoryError(
            "PipelineRun cannot complete before a Planning Bundle is attached."
        )

    raise PipelineRunRepositoryError("PipelineRun completion did not update a row.")


def _raise_running_transition_error(
    connection,
    run_id: str,
    *,
    action: str,
) -> None:
    run_row = _get_run_row(connection, run_id)

    if run_row is None:
        raise PipelineRunRepositoryError(
            f"PipelineRun {run_id!r} was not found."
        )

    raise PipelineRunRepositoryError(
        f"Only a running PipelineRun can be marked {action}."
    )
