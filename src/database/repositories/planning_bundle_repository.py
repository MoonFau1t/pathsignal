from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable
import sqlite3

from src.database.connection import database_connection, open_database_connection
from src.database.planning_identity import (
    build_planning_context,
    build_planning_input_fingerprint,
    build_planning_output_hash,
    canonical_json,
    hash_user_profile,
    serialize_user_profile,
)
from src.models import (
    CareerPathCategory,
    SearchPlan,
    SearchQuery,
    SearchQueryType,
    SearchScope,
    SourceType,
    TargetCareerPath,
    UserProfile,
)
from src.models import utc_now_iso


@dataclass(frozen=True)
class PlanningArtifactWrite:
    artifact_type: str
    file_path: str
    content_hash: str


@dataclass(frozen=True)
class ProfileSnapshotResult:
    profile_snapshot_id: int
    content_hash: str
    created: bool


@dataclass(frozen=True)
class PlanningBundleWrite:
    user_profile: UserProfile
    user_preferences: dict[str, Any]
    search_scope: SearchScope
    target_career_paths: list[TargetCareerPath]
    search_queries: list[SearchQuery]
    search_plans: list[SearchPlan]
    generation_mode: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    generator_config: dict[str, Any] | None = None
    source_path: str | None = None
    source_file_hash: str | None = None
    schema_version: str | None = None
    artifacts: tuple[PlanningArtifactWrite, ...] = ()


@dataclass(frozen=True)
class PlanningBundlePersistSummary:
    profile_snapshot_created: bool
    bundle_created: bool
    bundle_reused: bool
    planning_bundle_id: int
    path_count: int
    query_count: int
    plan_count: int
    artifact_count: int


@dataclass(frozen=True)
class HydratedPlanningBundle:
    planning_bundle_id: int
    bundle_row: dict
    target_career_paths: list[TargetCareerPath]
    search_queries: list[SearchQuery]
    search_plans: list[SearchPlan]


class PlanningBundleRepositoryError(Exception):
    """
    Raised when Planning Bundle persistence fails.
    """


class PlanningBundleRepository:
    """
    Repository for immutable, content-addressed planning bundles.
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = database_path

    def get_or_create_profile_snapshot(
        self,
        user_profile: UserProfile,
        *,
        source_path: str | None = None,
        source_file_hash: str | None = None,
        schema_version: str | None = None,
        created_at: str | None = None,
    ) -> ProfileSnapshotResult:
        content_hash = hash_user_profile(user_profile)
        payload_json = serialize_user_profile(user_profile)
        snapshot_time = created_at or utc_now_iso()

        try:
            with database_connection(self.database_path) as connection:
                return _get_or_create_profile_snapshot(
                    connection=connection,
                    content_hash=content_hash,
                    payload_json=payload_json,
                    source_path=source_path,
                    source_file_hash=source_file_hash,
                    schema_version=schema_version,
                    created_at=snapshot_time,
                )
        except Exception as error:
            raise PlanningBundleRepositoryError(
                "Failed to get or create user_profile_snapshots row."
            ) from error

    def find_reusable_bundle(self, input_fingerprint: str) -> dict | None:
        connection = open_database_connection(self.database_path)

        try:
            row = connection.execute(
                """
                SELECT *
                FROM planning_bundles
                WHERE input_fingerprint = ?
                ORDER BY created_at DESC, planning_bundle_id DESC
                LIMIT 1
                """,
                (input_fingerprint,),
            ).fetchone()

            return dict(row) if row is not None else None
        finally:
            connection.close()

    def get_bundle_by_input_and_output(
        self,
        input_fingerprint: str,
        output_hash: str,
    ) -> dict | None:
        connection = open_database_connection(self.database_path)

        try:
            row = _get_bundle_by_input_and_output(
                connection=connection,
                input_fingerprint=input_fingerprint,
                output_hash=output_hash,
            )

            return dict(row) if row is not None else None
        finally:
            connection.close()

    def persist_planning_bundle(
        self,
        bundle: PlanningBundleWrite,
        *,
        created_at: str | None = None,
    ) -> PlanningBundlePersistSummary:
        created_time = created_at or utc_now_iso()
        profile_content_hash = hash_user_profile(bundle.user_profile)
        profile_payload_json = serialize_user_profile(bundle.user_profile)
        input_fingerprint = build_planning_input_fingerprint(
            profile_content_hash=profile_content_hash,
            user_preferences=bundle.user_preferences,
            search_scope=bundle.search_scope,
            model_provider=bundle.model_provider,
            model_name=bundle.model_name,
            prompt_version=bundle.prompt_version,
            generator_config=bundle.generator_config,
        )
        output_hash = build_planning_output_hash(
            target_career_paths=bundle.target_career_paths,
            search_queries=bundle.search_queries,
            search_plans=bundle.search_plans,
        )
        planning_context_json = canonical_json(
            build_planning_context(
                profile_content_hash=profile_content_hash,
                user_preferences=bundle.user_preferences,
                search_scope=bundle.search_scope,
                model_provider=bundle.model_provider,
                model_name=bundle.model_name,
                prompt_version=bundle.prompt_version,
                generator_config=bundle.generator_config,
            )
        )

        try:
            with database_connection(self.database_path) as connection:
                profile_snapshot = _get_or_create_profile_snapshot(
                    connection=connection,
                    content_hash=profile_content_hash,
                    payload_json=profile_payload_json,
                    source_path=bundle.source_path,
                    source_file_hash=bundle.source_file_hash,
                    schema_version=bundle.schema_version,
                    created_at=created_time,
                )
                existing_bundle = _get_bundle_by_input_and_output(
                    connection=connection,
                    input_fingerprint=input_fingerprint,
                    output_hash=output_hash,
                )

                if existing_bundle is not None:
                    planning_bundle_id = int(existing_bundle["planning_bundle_id"])
                    return PlanningBundlePersistSummary(
                        profile_snapshot_created=profile_snapshot.created,
                        bundle_created=False,
                        bundle_reused=True,
                        planning_bundle_id=planning_bundle_id,
                        path_count=_count_bundle_rows(
                            connection,
                            "planning_target_career_paths",
                            planning_bundle_id,
                        ),
                        query_count=_count_bundle_rows(
                            connection,
                            "planning_search_queries",
                            planning_bundle_id,
                        ),
                        plan_count=_count_bundle_rows(
                            connection,
                            "planning_search_plans",
                            planning_bundle_id,
                        ),
                        artifact_count=_count_bundle_rows(
                            connection,
                            "planning_artifacts",
                            planning_bundle_id,
                        ),
                    )

                planning_bundle_id = _insert_planning_bundle(
                    connection=connection,
                    profile_snapshot_id=profile_snapshot.profile_snapshot_id,
                    input_fingerprint=input_fingerprint,
                    output_hash=output_hash,
                    generation_mode=bundle.generation_mode,
                    model_provider=bundle.model_provider,
                    model_name=bundle.model_name,
                    prompt_version=bundle.prompt_version,
                    planning_context_json=planning_context_json,
                    created_at=created_time,
                )
                path_row_ids = _insert_target_career_paths(
                    connection=connection,
                    planning_bundle_id=planning_bundle_id,
                    target_career_paths=bundle.target_career_paths,
                    created_at=created_time,
                )
                query_row_ids = _insert_search_queries(
                    connection=connection,
                    planning_bundle_id=planning_bundle_id,
                    search_queries=bundle.search_queries,
                    path_row_ids=path_row_ids,
                    created_at=created_time,
                )
                _insert_search_plans(
                    connection=connection,
                    planning_bundle_id=planning_bundle_id,
                    search_plans=bundle.search_plans,
                    path_row_ids=path_row_ids,
                    query_row_ids=query_row_ids,
                    created_at=created_time,
                )
                artifact_count = _insert_artifacts(
                    connection=connection,
                    planning_bundle_id=planning_bundle_id,
                    artifacts=bundle.artifacts,
                    created_at=created_time,
                )

                return PlanningBundlePersistSummary(
                    profile_snapshot_created=profile_snapshot.created,
                    bundle_created=True,
                    bundle_reused=False,
                    planning_bundle_id=planning_bundle_id,
                    path_count=len(bundle.target_career_paths),
                    query_count=len(bundle.search_queries),
                    plan_count=len(bundle.search_plans),
                    artifact_count=artifact_count,
                )
        except PlanningBundleRepositoryError:
            raise
        except Exception as error:
            raise PlanningBundleRepositoryError(
                "Failed to persist Planning Bundle."
            ) from error

    def get_planning_bundle(self, planning_bundle_id: int) -> dict | None:
        connection = open_database_connection(self.database_path)

        try:
            row = connection.execute(
                """
                SELECT *
                FROM planning_bundles
                WHERE planning_bundle_id = ?
                """,
                (planning_bundle_id,),
            ).fetchone()

            return dict(row) if row is not None else None
        finally:
            connection.close()

    def list_paths_for_bundle(self, planning_bundle_id: int) -> list[dict]:
        return self._list_rows(
            """
            SELECT *
            FROM planning_target_career_paths
            WHERE planning_bundle_id = ?
            ORDER BY position, career_path_row_id
            """,
            (planning_bundle_id,),
        )

    def list_queries_for_path(self, career_path_row_id: int) -> list[dict]:
        return self._list_rows(
            """
            SELECT *
            FROM planning_search_queries
            WHERE career_path_row_id = ?
            ORDER BY position, search_query_row_id
            """,
            (career_path_row_id,),
        )

    def list_queries_for_bundle(self, planning_bundle_id: int) -> list[dict]:
        return self._list_rows(
            """
            SELECT *
            FROM planning_search_queries
            WHERE planning_bundle_id = ?
            ORDER BY position, search_query_row_id
            """,
            (planning_bundle_id,),
        )

    def list_plans_for_bundle(self, planning_bundle_id: int) -> list[dict]:
        return self._list_rows(
            """
            SELECT *
            FROM planning_search_plans
            WHERE planning_bundle_id = ?
            ORDER BY position, search_plan_row_id
            """,
            (planning_bundle_id,),
        )

    def list_artifacts_for_bundle(self, planning_bundle_id: int) -> list[dict]:
        return self._list_rows(
            """
            SELECT *
            FROM planning_artifacts
            WHERE planning_bundle_id = ?
            ORDER BY planning_artifact_id
            """,
            (planning_bundle_id,),
        )

    def _list_rows(self, sql: str, params: tuple[Any, ...]) -> list[dict]:
        connection = open_database_connection(self.database_path)

        try:
            return [
                dict(row)
                for row in connection.execute(sql, params).fetchall()
            ]
        finally:
            connection.close()

    def hydrate_planning_bundle(
        self,
        planning_bundle_id: int,
    ) -> HydratedPlanningBundle:
        try:
            bundle_row = self.get_planning_bundle(planning_bundle_id)

            if bundle_row is None:
                raise PlanningBundleRepositoryError(
                    f"Planning Bundle {planning_bundle_id} was not found."
                )

            path_rows = self.list_paths_for_bundle(planning_bundle_id)
            query_rows = self.list_queries_for_bundle(planning_bundle_id)
            plan_rows = self.list_plans_for_bundle(planning_bundle_id)

            if not path_rows:
                raise PlanningBundleRepositoryError(
                    f"Planning Bundle {planning_bundle_id} has no TargetCareerPaths."
                )

            if not query_rows:
                raise PlanningBundleRepositoryError(
                    f"Planning Bundle {planning_bundle_id} has no SearchQueries."
                )

            if not plan_rows:
                raise PlanningBundleRepositoryError(
                    f"Planning Bundle {planning_bundle_id} has no SearchPlans."
                )

            career_paths_by_row_id: dict[int, TargetCareerPath] = {}
            target_career_paths: list[TargetCareerPath] = []

            for path_row in path_rows:
                career_path = _target_career_path_from_payload(
                    _decode_payload_json(path_row, "TargetCareerPath")
                )
                row_id = int(path_row["career_path_row_id"])

                if str(path_row["path_id"]) != career_path.path_id:
                    raise PlanningBundleRepositoryError(
                        "TargetCareerPath payload path_id does not match "
                        f"stored row identity for row {row_id}."
                    )

                if row_id in career_paths_by_row_id:
                    raise PlanningBundleRepositoryError(
                        "Duplicate TargetCareerPath row relationship detected "
                        f"for row {row_id}."
                    )

                career_paths_by_row_id[row_id] = career_path
                target_career_paths.append(career_path)

            search_queries_by_row_id: dict[int, SearchQuery] = {}
            search_queries: list[SearchQuery] = []

            for query_row in query_rows:
                query = _search_query_from_payload(
                    _decode_payload_json(query_row, "SearchQuery")
                )
                query_row_id = int(query_row["search_query_row_id"])
                path_row_id = int(query_row["career_path_row_id"])
                career_path = career_paths_by_row_id.get(path_row_id)

                if career_path is None:
                    raise PlanningBundleRepositoryError(
                        "SearchQuery references unknown TargetCareerPath row "
                        f"{path_row_id}."
                    )

                if query.career_path_id != career_path.path_id:
                    raise PlanningBundleRepositoryError(
                        "SearchQuery payload career_path_id does not match "
                        f"stored TargetCareerPath relationship for {query.query_id!r}."
                    )

                if str(query_row["query_identity"]) != query.query_id:
                    raise PlanningBundleRepositoryError(
                        "SearchQuery payload query_id does not match stored "
                        f"row identity for row {query_row_id}."
                    )

                if query_row_id in search_queries_by_row_id:
                    raise PlanningBundleRepositoryError(
                        "Duplicate SearchQuery row relationship detected "
                        f"for row {query_row_id}."
                    )

                search_queries_by_row_id[query_row_id] = query
                search_queries.append(query)

            if len({query.query_id for query in search_queries}) != len(search_queries):
                raise PlanningBundleRepositoryError(
                    "Planning Bundle contains ambiguous duplicate SearchQuery IDs."
                )

            search_plans: list[SearchPlan] = []

            for plan_row in plan_rows:
                plan = _search_plan_from_payload(
                    _decode_payload_json(plan_row, "SearchPlan")
                )
                path_row_id = int(plan_row["career_path_row_id"])
                query_row_id = int(plan_row["search_query_row_id"])
                career_path = career_paths_by_row_id.get(path_row_id)
                query = search_queries_by_row_id.get(query_row_id)

                if career_path is None:
                    raise PlanningBundleRepositoryError(
                        "SearchPlan references unknown TargetCareerPath row "
                        f"{path_row_id}."
                    )

                if query is None:
                    raise PlanningBundleRepositoryError(
                        "SearchPlan references unknown SearchQuery row "
                        f"{query_row_id}."
                    )

                if plan.career_path_id != career_path.path_id:
                    raise PlanningBundleRepositoryError(
                        "SearchPlan payload career_path_id does not match "
                        f"stored TargetCareerPath relationship for {plan.plan_id!r}."
                    )

                if plan.query_id != query.query_id:
                    raise PlanningBundleRepositoryError(
                        "SearchPlan payload query_id does not match stored "
                        f"SearchQuery relationship for {plan.plan_id!r}."
                    )

                if str(plan_row["plan_identity"]) != plan.plan_id:
                    raise PlanningBundleRepositoryError(
                        "SearchPlan payload plan_id does not match stored "
                        f"row identity for row {plan_row['search_plan_row_id']}."
                    )

                search_plans.append(plan)

            return HydratedPlanningBundle(
                planning_bundle_id=planning_bundle_id,
                bundle_row=bundle_row,
                target_career_paths=target_career_paths,
                search_queries=search_queries,
                search_plans=search_plans,
            )
        except PlanningBundleRepositoryError:
            raise
        except Exception as error:
            raise PlanningBundleRepositoryError(
                f"Failed to hydrate Planning Bundle {planning_bundle_id}."
            ) from error


def _get_or_create_profile_snapshot(
    *,
    connection: sqlite3.Connection,
    content_hash: str,
    payload_json: str,
    source_path: str | None,
    source_file_hash: str | None,
    schema_version: str | None,
    created_at: str,
) -> ProfileSnapshotResult:
    existing_row = connection.execute(
        """
        SELECT profile_snapshot_id, content_hash
        FROM user_profile_snapshots
        WHERE content_hash = ?
        """,
        (content_hash,),
    ).fetchone()

    if existing_row is not None:
        return ProfileSnapshotResult(
            profile_snapshot_id=int(existing_row["profile_snapshot_id"]),
            content_hash=str(existing_row["content_hash"]),
            created=False,
        )

    cursor = connection.execute(
        """
        INSERT INTO user_profile_snapshots (
            content_hash,
            payload_json,
            source_path,
            source_file_hash,
            schema_version,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            content_hash,
            payload_json,
            source_path,
            source_file_hash,
            schema_version,
            created_at,
        ),
    )

    return ProfileSnapshotResult(
        profile_snapshot_id=int(cursor.lastrowid),
        content_hash=content_hash,
        created=True,
    )


def _get_bundle_by_input_and_output(
    *,
    connection: sqlite3.Connection,
    input_fingerprint: str,
    output_hash: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM planning_bundles
        WHERE input_fingerprint = ?
          AND output_hash = ?
        """,
        (input_fingerprint, output_hash),
    ).fetchone()


def _insert_planning_bundle(
    *,
    connection: sqlite3.Connection,
    profile_snapshot_id: int,
    input_fingerprint: str,
    output_hash: str,
    generation_mode: str | None,
    model_provider: str | None,
    model_name: str | None,
    prompt_version: str | None,
    planning_context_json: str,
    created_at: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO planning_bundles (
            profile_snapshot_id,
            input_fingerprint,
            output_hash,
            generation_mode,
            model_provider,
            model_name,
            prompt_version,
            planning_context_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            profile_snapshot_id,
            input_fingerprint,
            output_hash,
            generation_mode,
            model_provider,
            model_name,
            prompt_version,
            planning_context_json,
            created_at,
        ),
    )

    return int(cursor.lastrowid)


def _insert_target_career_paths(
    *,
    connection: sqlite3.Connection,
    planning_bundle_id: int,
    target_career_paths: list[TargetCareerPath],
    created_at: str,
) -> dict[str, int]:
    path_row_ids: dict[str, int] = {}

    for position, career_path in enumerate(target_career_paths):
        path_type_or_tier = _path_type_or_tier(career_path)
        cursor = connection.execute(
            """
            INSERT INTO planning_target_career_paths (
                planning_bundle_id,
                path_id,
                position,
                path_type_or_tier,
                name_or_title,
                payload_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                planning_bundle_id,
                career_path.path_id,
                position,
                path_type_or_tier,
                career_path.title,
                canonical_json(career_path),
                created_at,
            ),
        )
        path_row_ids[career_path.path_id] = int(cursor.lastrowid)

    return path_row_ids


def _insert_search_queries(
    *,
    connection: sqlite3.Connection,
    planning_bundle_id: int,
    search_queries: list[SearchQuery],
    path_row_ids: dict[str, int],
    created_at: str,
) -> dict[str, int]:
    query_row_ids: dict[str, int] = {}

    for position, search_query in enumerate(search_queries):
        career_path_row_id = path_row_ids.get(search_query.career_path_id)

        if career_path_row_id is None:
            raise PlanningBundleRepositoryError(
                "SearchQuery references unknown TargetCareerPath "
                f"{search_query.career_path_id!r}."
            )

        cursor = connection.execute(
            """
            INSERT INTO planning_search_queries (
                planning_bundle_id,
                career_path_row_id,
                query_identity,
                position,
                query_text,
                payload_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                planning_bundle_id,
                career_path_row_id,
                search_query.query_id,
                position,
                search_query.query_text,
                canonical_json(search_query),
                created_at,
            ),
        )
        query_row_ids[search_query.query_id] = int(cursor.lastrowid)

    return query_row_ids


def _insert_search_plans(
    *,
    connection: sqlite3.Connection,
    planning_bundle_id: int,
    search_plans: list[SearchPlan],
    path_row_ids: dict[str, int],
    query_row_ids: dict[str, int],
    created_at: str,
) -> None:
    for position, search_plan in enumerate(search_plans):
        career_path_row_id = path_row_ids.get(search_plan.career_path_id)
        search_query_row_id = query_row_ids.get(search_plan.query_id)

        if career_path_row_id is None:
            raise PlanningBundleRepositoryError(
                "SearchPlan references unknown TargetCareerPath "
                f"{search_plan.career_path_id!r}."
            )

        if search_query_row_id is None:
            raise PlanningBundleRepositoryError(
                "SearchPlan references unknown SearchQuery "
                f"{search_plan.query_id!r}."
            )

        connection.execute(
            """
            INSERT INTO planning_search_plans (
                planning_bundle_id,
                career_path_row_id,
                search_query_row_id,
                plan_identity,
                position,
                provider,
                mode,
                query_text,
                payload_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                planning_bundle_id,
                career_path_row_id,
                search_query_row_id,
                search_plan.plan_id,
                position,
                _plan_provider(search_plan),
                _plan_mode(search_plan),
                search_plan.query_text,
                canonical_json(search_plan),
                created_at,
            ),
        )


def _insert_artifacts(
    *,
    connection: sqlite3.Connection,
    planning_bundle_id: int,
    artifacts: Iterable[PlanningArtifactWrite],
    created_at: str,
) -> int:
    unique_artifacts = {
        (artifact.artifact_type, artifact.file_path, artifact.content_hash): artifact
        for artifact in artifacts
    }
    inserted_count = 0

    for artifact in unique_artifacts.values():
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO planning_artifacts (
                planning_bundle_id,
                artifact_type,
                file_path,
                content_hash,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                planning_bundle_id,
                artifact.artifact_type,
                artifact.file_path,
                artifact.content_hash,
                created_at,
            ),
        )
        inserted_count += cursor.rowcount

    return inserted_count


def _count_bundle_rows(
    connection: sqlite3.Connection,
    table_name: str,
    planning_bundle_id: int,
) -> int:
    return int(
        connection.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE planning_bundle_id = ?",
            (planning_bundle_id,),
        ).fetchone()[0]
    )


def _path_type_or_tier(career_path: TargetCareerPath) -> str | None:
    for key in ("path_type", "tier"):
        value = career_path.metadata.get(key)

        if value is not None and str(value).strip():
            return str(value)

    return None


def _plan_provider(search_plan: SearchPlan) -> str | None:
    if not search_plan.source_types:
        return None

    return ",".join(
        source_type.value if hasattr(source_type, "value") else str(source_type)
        for source_type in search_plan.source_types
    )


def _plan_mode(search_plan: SearchPlan) -> str | None:
    mode = search_plan.metadata.get("mode")

    if mode is None:
        return None

    stripped_mode = str(mode).strip()

    return stripped_mode or None


def _decode_payload_json(row: dict, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(row["payload_json"]))
    except Exception as error:
        raise PlanningBundleRepositoryError(
            f"{label} payload_json is malformed."
        ) from error

    if not isinstance(payload, dict):
        raise PlanningBundleRepositoryError(
            f"{label} payload_json must decode to a JSON object."
        )

    return payload


def _target_career_path_from_payload(payload: dict[str, Any]) -> TargetCareerPath:
    try:
        category = CareerPathCategory(str(payload.get("category", "unknown")))
    except ValueError:
        category = CareerPathCategory.UNKNOWN

    return TargetCareerPath(
        path_id=_string(payload.get("path_id")),
        title=_string(payload.get("title")),
        category=category,
        description=_string(payload.get("description")),
        fit_score=_float(payload.get("fit_score")),
        rationale=_string_list(payload.get("rationale")),
        keywords=_string_list(payload.get("keywords")),
        suggested_roles=_string_list(payload.get("suggested_roles")),
        search_seed_terms=_string_list(payload.get("search_seed_terms")),
        metadata=_dict(payload.get("metadata")),
    )


def _search_query_from_payload(payload: dict[str, Any]) -> SearchQuery:
    return SearchQuery(
        query_id=_string(payload.get("query_id")),
        career_path_id=_string(payload.get("career_path_id")),
        career_path_title=_string(payload.get("career_path_title")),
        query_text=_string(payload.get("query_text")),
        query_type=_search_query_type(payload.get("query_type")),
        priority=_float(payload.get("priority")),
        target_roles=_string_list(payload.get("target_roles")),
        keywords=_string_list(payload.get("keywords")),
        negative_keywords=_string_list(payload.get("negative_keywords")),
        rationale=_string(payload.get("rationale")),
        metadata=_dict(payload.get("metadata")),
    )


def _search_plan_from_payload(payload: dict[str, Any]) -> SearchPlan:
    return SearchPlan(
        plan_id=_string(payload.get("plan_id")),
        query_id=_string(payload.get("query_id")),
        query_text=_string(payload.get("query_text")),
        query_type=_search_query_type(payload.get("query_type")),
        career_path_id=_string(payload.get("career_path_id")),
        career_path_title=_string(payload.get("career_path_title")),
        scope_id=_string(payload.get("scope_id")),
        source_types=[
            _source_type(item)
            for item in _list(payload.get("source_types"))
        ],
        locations=_string_list(payload.get("locations")),
        languages=_string_list(payload.get("languages")),
        allowed_domains=_string_list(payload.get("allowed_domains")),
        excluded_domains=_string_list(payload.get("excluded_domains")),
        freshness_days=_int(payload.get("freshness_days"), 30),
        max_results=_int(payload.get("max_results"), 10),
        priority=_float(payload.get("priority")),
        negative_keywords=_string_list(payload.get("negative_keywords")),
        metadata=_dict(payload.get("metadata")),
    )


def _search_query_type(value: Any) -> SearchQueryType:
    try:
        return SearchQueryType(str(value))
    except ValueError as error:
        raise PlanningBundleRepositoryError(
            f"Unknown SearchQueryType value: {value!r}."
        ) from error


def _source_type(value: Any) -> SourceType:
    try:
        return SourceType(str(value))
    except ValueError as error:
        raise PlanningBundleRepositoryError(
            f"Unknown SourceType value: {value!r}."
        ) from error


def _string(value: Any) -> str:
    if value is None:
        return ""

    return str(value)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    return [
        str(item)
        for item in _list(value)
        if item is not None
    ]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
