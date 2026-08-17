import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.config import DEFAULT_DATABASE_FILE
from src.database.connection import database_connection, open_database_connection
from src.database.migrations import initialize_database
from src.database.planning_identity import (
    build_planning_input_fingerprint,
    build_planning_output_hash,
    canonical_json,
    hash_user_profile,
)
from src.database.repositories.planning_bundle_repository import (
    PlanningArtifactWrite,
    PlanningBundleRepository,
    PlanningBundleRepositoryError,
    PlanningBundleWrite,
)
from src.models import (
    CareerPathCategory,
    RSSFeed,
    SearchPlan,
    SearchQuery,
    SearchQueryType,
    SearchScope,
    SelectedWebsite,
    SourceType,
    TargetCareerPath,
    UserProfile,
)


CREATED_AT = "2026-07-24T00:00:00+00:00"


class TemporaryPlanningDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "planning.db"
        initialize_database(database_path=self.database_path)
        self.repository = PlanningBundleRepository(database_path=self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def count_rows(self, table_name):
        connection = open_database_connection(self.database_path)

        try:
            return int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table_name}"
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def fetch_rows(self, table_name, order_by):
        connection = open_database_connection(self.database_path)

        try:
            return [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {table_name} ORDER BY {order_by}"
                ).fetchall()
            ]
        finally:
            connection.close()


def make_profile(**overrides):
    data = {
        "profile_id": "profile_1",
        "name": "Test User",
        "background_summary": "Strategy analyst with AI interests.",
        "education": [{"school": "Example University", "degree": "BA"}],
        "work_experience": [{"company": "Example Co", "role": "Analyst"}],
        "skills": ["strategy", "python"],
        "interests": ["AI", "venture"],
        "preferred_locations": ["New York", "Remote"],
        "preferred_roles": ["AI strategy analyst"],
        "constraints": ["early career"],
        "raw_resume_text": "Resume text",
        "metadata": {"language": "中文", "nested": {"level": 1}},
    }
    data.update(overrides)
    return UserProfile(**data)


def make_scope(**overrides):
    data = {
        "scope_id": "scope_1",
        "name": "Default scope",
        "description": "Search configured sources.",
        "locations": ["New York"],
        "languages": ["en", "zh"],
        "source_types": [
            SourceType.SEARCH_API,
            SourceType.RSS,
            SourceType.SELECTED_WEBSITE,
        ],
        "allowed_domains": ["example.com"],
        "excluded_domains": ["blocked.example"],
        "selected_websites": [
            SelectedWebsite(name="Example Jobs", url="https://example.com/jobs")
        ],
        "rss_feeds": [
            RSSFeed(name="Example Feed", url="https://example.com/feed.xml")
        ],
        "freshness_days": 30,
        "max_results_per_query": 10,
        "seniority_levels": ["entry"],
        "enable_search_api": True,
        "enable_rss": True,
        "enable_selected_websites": True,
        "metadata": {"scope_note": "stable"},
    }
    data.update(overrides)
    return SearchScope(**data)


def make_path(path_id="path_ai", title="AI Strategy", fit_score=91.0, **overrides):
    data = {
        "path_id": path_id,
        "title": title,
        "category": CareerPathCategory.AI_STRATEGY,
        "description": "AI strategy roles.",
        "fit_score": fit_score,
        "rationale": ["Matched strategy experience."],
        "keywords": ["AI", "strategy"],
        "suggested_roles": ["AI strategy analyst"],
        "search_seed_terms": ["AI strategy analyst"],
        "metadata": {
            "path_type": "core_match",
            "search_seed_terms_zh": ["AI战略"],
            "nested": {"confidence": "high"},
        },
    }
    data.update(overrides)
    return TargetCareerPath(**data)


def make_query(path=None, query_id=None, query_text="AI strategy analyst open role"):
    path = path or make_path()
    return SearchQuery(
        query_id=query_id or f"q_{path.path_id}_ai_strategy",
        career_path_id=path.path_id,
        career_path_title=path.title,
        query_text=query_text,
        query_type=SearchQueryType.JOB_SEARCH,
        priority=0.95,
        target_roles=path.suggested_roles,
        keywords=path.keywords,
        negative_keywords=["senior director"],
        rationale="Find open roles.",
        metadata={"generator": "rule_based_phase_5", "nested": {"x": "值"}},
    )


def make_plan(scope=None, query=None, plan_id=None, query_text=None):
    scope = scope or make_scope()
    query = query or make_query()
    return SearchPlan(
        plan_id=plan_id or f"plan_{scope.scope_id}_{query.query_id}",
        query_id=query.query_id,
        query_text=query_text or f"{query.query_text} (New York)",
        query_type=query.query_type,
        career_path_id=query.career_path_id,
        career_path_title=query.career_path_title,
        scope_id=scope.scope_id,
        source_types=[SourceType.SEARCH_API],
        locations=scope.locations,
        languages=scope.languages,
        allowed_domains=scope.allowed_domains,
        excluded_domains=scope.excluded_domains,
        freshness_days=scope.freshness_days,
        max_results=scope.max_results_per_query,
        priority=query.priority,
        negative_keywords=query.negative_keywords,
        metadata={"builder": "rule_based_phase_6", "mode": "live"},
    )


def make_bundle_write(
    *,
    profile=None,
    preferences=None,
    scope=None,
    paths=None,
    queries=None,
    plans=None,
    artifacts=None,
    model_name="deepseek-v4-pro",
    prompt_version="target_career_path_prompt_v1",
    generator_config=None,
):
    profile = profile or make_profile()
    scope = scope or make_scope()
    paths = paths or [make_path()]
    queries = queries or [make_query(path=paths[0])]
    plans = plans or [make_plan(scope=scope, query=queries[0])]
    artifacts = artifacts if artifacts is not None else (
        PlanningArtifactWrite(
            artifact_type="target_career_paths_cache",
            file_path="outputs/planning/target_career_paths.json",
            content_hash="artifact_hash_1",
        ),
    )

    return PlanningBundleWrite(
        user_profile=profile,
        user_preferences=preferences or {"markets": ["US"], "weights": {"ai": 1}},
        search_scope=scope,
        target_career_paths=paths,
        search_queries=queries,
        search_plans=plans,
        generation_mode="llm",
        model_provider="deepseek",
        model_name=model_name,
        prompt_version=prompt_version,
        generator_config=generator_config or {"max_queries_per_path": 8},
        source_path="inputs/user_profile.json",
        source_file_hash="source_hash_1",
        schema_version="user_profile_v1",
        artifacts=tuple(artifacts),
    )


class PlanningBundleMigrationTests(TemporaryPlanningDatabaseTestCase):
    def test_migration_004_is_ordered_and_idempotent(self):
        second_applied = initialize_database(database_path=self.database_path)
        connection = open_database_connection(self.database_path)

        try:
            rows = connection.execute(
                """
                SELECT version, name
                FROM schema_migrations
                ORDER BY version
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(
            [row["version"] for row in rows],
            ["001", "002", "003", "004", "005", "006", "007"],
        )
        self.assertEqual(rows[-1]["name"], "filter_decision_provenance")
        self.assertEqual(second_applied, [])

    def test_all_planning_tables_exist(self):
        connection = open_database_connection(self.database_path)

        try:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()

        self.assertTrue(
            {
                "user_profile_snapshots",
                "planning_bundles",
                "planning_target_career_paths",
                "planning_search_queries",
                "planning_search_plans",
                "planning_artifacts",
            }.issubset(tables)
        )

    def test_useful_planning_indexes_exist(self):
        connection = open_database_connection(self.database_path)

        try:
            indexes = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
        finally:
            connection.close()

        self.assertIn("idx_planning_bundles_profile_snapshot_id", indexes)
        self.assertIn("idx_planning_bundles_input_fingerprint", indexes)
        self.assertIn("idx_planning_search_plans_search_query_row_id", indexes)
        self.assertIn("idx_planning_artifacts_bundle_id", indexes)

    def test_planning_foreign_keys_exist_and_are_enforced(self):
        connection = open_database_connection(self.database_path)

        try:
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            bundle_fks = connection.execute(
                "PRAGMA foreign_key_list(planning_bundles)"
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(foreign_keys, 1)
        self.assertEqual(bundle_fks[0]["table"], "user_profile_snapshots")

    def test_invalid_foreign_key_rolls_back(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with database_connection(self.database_path) as connection:
                connection.execute(
                    """
                    INSERT INTO planning_bundles (
                        profile_snapshot_id,
                        input_fingerprint,
                        output_hash,
                        planning_context_json,
                        created_at
                    )
                    VALUES (999, 'input', 'output', '{}', ?)
                    """,
                    (CREATED_AT,),
                )

        self.assertEqual(self.count_rows("planning_bundles"), 0)


class PlanningIdentityTests(TemporaryPlanningDatabaseTestCase):
    def test_canonical_profile_serialization_uses_enum_values_and_unicode(self):
        profile = make_profile()
        serialized = canonical_json(profile)

        self.assertIn('"language":"中文"', serialized)
        self.assertEqual(serialized, canonical_json(UserProfile.from_dict(json.loads(serialized))))

    def test_identical_profile_content_reuses_one_snapshot(self):
        first = self.repository.get_or_create_profile_snapshot(make_profile())
        second = self.repository.get_or_create_profile_snapshot(make_profile())

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.profile_snapshot_id, second.profile_snapshot_id)
        self.assertEqual(self.count_rows("user_profile_snapshots"), 1)

    def test_changed_profile_content_creates_new_snapshot(self):
        first = self.repository.get_or_create_profile_snapshot(make_profile())
        second = self.repository.get_or_create_profile_snapshot(
            make_profile(background_summary="Changed")
        )

        self.assertNotEqual(first.profile_snapshot_id, second.profile_snapshot_id)
        self.assertEqual(self.count_rows("user_profile_snapshots"), 2)

    def test_key_order_path_and_timestamp_do_not_affect_profile_identity(self):
        profile_data = make_profile().to_dict()
        reordered = UserProfile.from_dict(dict(reversed(list(profile_data.items()))))

        first = self.repository.get_or_create_profile_snapshot(
            make_profile(),
            source_path="inputs/a.json",
            created_at="2026-01-01T00:00:00+00:00",
        )
        second = self.repository.get_or_create_profile_snapshot(
            reordered,
            source_path="inputs/b.json",
            created_at="2026-02-01T00:00:00+00:00",
        )

        self.assertEqual(first.profile_snapshot_id, second.profile_snapshot_id)
        self.assertEqual(self.count_rows("user_profile_snapshots"), 1)

    def test_profile_whitespace_in_source_json_does_not_affect_identity(self):
        compact_profile = UserProfile.from_dict(json.loads(json.dumps(make_profile().to_dict())))
        pretty_profile = UserProfile.from_dict(
            json.loads(json.dumps(make_profile().to_dict(), indent=2))
        )

        self.assertEqual(hash_user_profile(compact_profile), hash_user_profile(pretty_profile))

    def test_input_fingerprint_is_stable_for_equivalent_material_input(self):
        profile_hash = hash_user_profile(make_profile())

        first = build_planning_input_fingerprint(
            profile_content_hash=profile_hash,
            user_preferences={"b": 2, "a": 1},
            search_scope=make_scope(),
            model_provider="deepseek",
            model_name="model",
            prompt_version="prompt",
            generator_config={"limit": 8},
        )
        second = build_planning_input_fingerprint(
            profile_content_hash=profile_hash,
            user_preferences={"a": 1, "b": 2},
            search_scope=make_scope(),
            model_provider="deepseek",
            model_name="model",
            prompt_version="prompt",
            generator_config={"limit": 8},
        )

        self.assertEqual(first, second)

    def test_profile_change_changes_input_fingerprint(self):
        base = make_input_fingerprint(profile=make_profile())
        changed = make_input_fingerprint(profile=make_profile(name="Changed"))

        self.assertNotEqual(base, changed)

    def test_preferences_change_changes_input_fingerprint(self):
        base = make_input_fingerprint(preferences={"market": "US"})
        changed = make_input_fingerprint(preferences={"market": "EU"})

        self.assertNotEqual(base, changed)

    def test_search_scope_change_changes_input_fingerprint(self):
        base = make_input_fingerprint(scope=make_scope())
        changed = make_input_fingerprint(scope=make_scope(locations=["San Francisco"]))

        self.assertNotEqual(base, changed)

    def test_model_or_prompt_change_changes_input_fingerprint(self):
        base = make_input_fingerprint(model_name="model-a", prompt_version="prompt-a")
        changed_model = make_input_fingerprint(model_name="model-b", prompt_version="prompt-a")
        changed_prompt = make_input_fingerprint(model_name="model-a", prompt_version="prompt-b")

        self.assertNotEqual(base, changed_model)
        self.assertNotEqual(base, changed_prompt)

    def test_runtime_timestamp_does_not_affect_input_fingerprint(self):
        first_runtime_started_at = "2026-07-24T00:00:00+00:00"
        second_runtime_started_at = "2026-07-24T01:00:00+00:00"

        self.assertNotEqual(first_runtime_started_at, second_runtime_started_at)
        self.assertEqual(make_input_fingerprint(), make_input_fingerprint())

    def test_output_hash_is_stable_for_identical_ordered_output(self):
        bundle = make_bundle_write()

        self.assertEqual(
            make_output_hash(bundle),
            make_output_hash(make_bundle_write()),
        )

    def test_materially_changed_output_changes_output_hash(self):
        base = make_bundle_write()
        changed = make_bundle_write(paths=[make_path(title="Changed path")])

        self.assertNotEqual(make_output_hash(base), make_output_hash(changed))

    def test_meaningful_output_order_changes_output_hash(self):
        first_path = make_path(path_id="path_a", title="A")
        second_path = make_path(path_id="path_b", title="B")
        first = make_bundle_write(
            paths=[first_path, second_path],
            queries=[
                make_query(path=first_path, query_id="q_a"),
                make_query(path=second_path, query_id="q_b"),
            ],
        )
        second = make_bundle_write(
            paths=[second_path, first_path],
            queries=[
                make_query(path=second_path, query_id="q_b"),
                make_query(path=first_path, query_id="q_a"),
            ],
        )

        self.assertNotEqual(make_output_hash(first), make_output_hash(second))


class PlanningBundlePersistenceTests(TemporaryPlanningDatabaseTestCase):
    def test_first_persistence_creates_one_complete_bundle(self):
        summary = self.repository.persist_planning_bundle(
            make_bundle_write(),
            created_at=CREATED_AT,
        )

        self.assertTrue(summary.profile_snapshot_created)
        self.assertTrue(summary.bundle_created)
        self.assertFalse(summary.bundle_reused)
        self.assertEqual(summary.path_count, 1)
        self.assertEqual(summary.query_count, 1)
        self.assertEqual(summary.plan_count, 1)
        self.assertEqual(summary.artifact_count, 1)
        self.assertEqual(self.count_rows("planning_bundles"), 1)

    def test_repeated_identical_persistence_reuses_the_bundle(self):
        first = self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        second = self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)

        self.assertEqual(first.planning_bundle_id, second.planning_bundle_id)
        self.assertFalse(second.bundle_created)
        self.assertTrue(second.bundle_reused)

    def test_repeated_identical_persistence_does_not_duplicate_children(self):
        self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)

        self.assertEqual(self.count_rows("planning_target_career_paths"), 1)
        self.assertEqual(self.count_rows("planning_search_queries"), 1)
        self.assertEqual(self.count_rows("planning_search_plans"), 1)

    def test_repeated_identical_persistence_does_not_duplicate_artifacts(self):
        self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)

        self.assertEqual(self.count_rows("planning_artifacts"), 1)

    def test_same_input_with_different_output_creates_second_bundle(self):
        first = self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        changed = make_bundle_write(paths=[make_path(title="Changed path")])
        second = self.repository.persist_planning_bundle(changed, created_at=CREATED_AT)

        self.assertNotEqual(first.planning_bundle_id, second.planning_bundle_id)
        self.assertEqual(self.count_rows("planning_bundles"), 2)

    def test_different_output_bundles_reuse_one_profile_snapshot(self):
        self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        self.repository.persist_planning_bundle(
            make_bundle_write(paths=[make_path(title="Changed path")]),
            created_at=CREATED_AT,
        )

        self.assertEqual(self.count_rows("user_profile_snapshots"), 1)

    def test_changed_profile_creates_new_snapshot_and_bundle(self):
        first = self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        second = self.repository.persist_planning_bundle(
            make_bundle_write(profile=make_profile(name="Changed")),
            created_at=CREATED_AT,
        )

        self.assertNotEqual(first.planning_bundle_id, second.planning_bundle_id)
        self.assertEqual(self.count_rows("user_profile_snapshots"), 2)
        self.assertEqual(self.count_rows("planning_bundles"), 2)

    def test_same_path_id_is_allowed_in_different_bundles(self):
        self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        self.repository.persist_planning_bundle(
            make_bundle_write(paths=[make_path(title="Same ID changed title")]),
            created_at=CREATED_AT,
        )

        path_rows = self.fetch_rows("planning_target_career_paths", "career_path_row_id")

        self.assertEqual([row["path_id"] for row in path_rows], ["path_ai", "path_ai"])

    def test_duplicate_path_id_inside_one_bundle_rolls_back(self):
        first_path = make_path(path_id="duplicate")
        second_path = make_path(path_id="duplicate", title="Duplicate")
        bundle = make_bundle_write(paths=[first_path, second_path])

        with self.assertRaises(PlanningBundleRepositoryError) as context:
            self.repository.persist_planning_bundle(bundle, created_at=CREATED_AT)

        self.assertIsInstance(context.exception.__cause__, sqlite3.IntegrityError)
        self.assertEqual(self.count_rows("planning_bundles"), 0)
        self.assertEqual(self.count_rows("planning_target_career_paths"), 0)

    def test_path_ordering_is_preserved(self):
        first_path = make_path(path_id="path_a", title="A")
        second_path = make_path(path_id="path_b", title="B")

        summary = self.repository.persist_planning_bundle(
            make_bundle_write(
                paths=[first_path, second_path],
                queries=[make_query(path=first_path, query_id="q_a")],
                plans=[make_plan(query=make_query(path=first_path, query_id="q_a"))],
            ),
            created_at=CREATED_AT,
        )

        rows = self.repository.list_paths_for_bundle(summary.planning_bundle_id)

        self.assertEqual([row["path_id"] for row in rows], ["path_a", "path_b"])
        self.assertEqual([row["position"] for row in rows], [0, 1])

    def test_query_to_path_linkage_is_correct(self):
        first_path = make_path(path_id="path_a", title="A")
        query = make_query(path=first_path, query_id="q_a")
        summary = self.repository.persist_planning_bundle(
            make_bundle_write(
                paths=[first_path],
                queries=[query],
                plans=[make_plan(query=query)],
            ),
            created_at=CREATED_AT,
        )

        path_row = self.repository.list_paths_for_bundle(summary.planning_bundle_id)[0]
        query_row = self.repository.list_queries_for_path(path_row["career_path_row_id"])[0]

        self.assertEqual(query_row["career_path_row_id"], path_row["career_path_row_id"])
        self.assertEqual(query_row["query_identity"], "q_a")

    def test_plan_to_query_linkage_is_correct(self):
        query = make_query(query_id="q_linked")
        plan = make_plan(query=query, plan_id="plan_linked")
        summary = self.repository.persist_planning_bundle(
            make_bundle_write(queries=[query], plans=[plan]),
            created_at=CREATED_AT,
        )

        query_row = self.fetch_rows("planning_search_queries", "search_query_row_id")[0]
        plan_row = self.repository.list_plans_for_bundle(summary.planning_bundle_id)[0]

        self.assertEqual(plan_row["search_query_row_id"], query_row["search_query_row_id"])
        self.assertEqual(plan_row["plan_identity"], "plan_linked")

    def test_complete_payload_json_round_trips(self):
        summary = self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        path_payload = json.loads(
            self.repository.list_paths_for_bundle(summary.planning_bundle_id)[0]["payload_json"]
        )
        query_payload = json.loads(self.fetch_rows("planning_search_queries", "position")[0]["payload_json"])
        plan_payload = json.loads(self.repository.list_plans_for_bundle(summary.planning_bundle_id)[0]["payload_json"])

        self.assertEqual(path_payload["path_id"], "path_ai")
        self.assertEqual(query_payload["query_id"], "q_path_ai_ai_strategy")
        self.assertEqual(plan_payload["scope_id"], "scope_1")

    def test_unicode_and_nested_values_survive(self):
        summary = self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        path_payload = json.loads(
            self.repository.list_paths_for_bundle(summary.planning_bundle_id)[0]["payload_json"]
        )
        query_payload = json.loads(self.fetch_rows("planning_search_queries", "position")[0]["payload_json"])

        self.assertEqual(path_payload["metadata"]["search_seed_terms_zh"], ["AI战略"])
        self.assertEqual(query_payload["metadata"]["nested"]["x"], "值")

    def test_generation_metadata_and_context_survive(self):
        summary = self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        bundle_row = self.repository.get_planning_bundle(summary.planning_bundle_id)
        context = json.loads(bundle_row["planning_context_json"])

        self.assertEqual(bundle_row["generation_mode"], "llm")
        self.assertEqual(bundle_row["model_provider"], "deepseek")
        self.assertEqual(bundle_row["model_name"], "deepseek-v4-pro")
        self.assertEqual(bundle_row["prompt_version"], "target_career_path_prompt_v1")
        self.assertEqual(context["generator_config"]["max_queries_per_path"], 8)

    def test_invalid_query_child_data_rolls_back_new_bundle(self):
        query = make_query()
        invalid_query = SearchQuery(
            query_id="q_missing",
            career_path_id="missing_path",
            career_path_title="Missing",
            query_text="missing",
            query_type=SearchQueryType.JOB_SEARCH,
            priority=0.5,
        )
        bundle = make_bundle_write(queries=[query, invalid_query])

        with self.assertRaisesRegex(PlanningBundleRepositoryError, "unknown TargetCareerPath"):
            self.repository.persist_planning_bundle(bundle, created_at=CREATED_AT)

        self.assertEqual(self.count_rows("planning_bundles"), 0)
        self.assertEqual(self.count_rows("planning_search_queries"), 0)

    def test_invalid_plan_child_data_rolls_back_new_bundle(self):
        invalid_plan = make_plan(query=make_query(query_id="missing"), plan_id="plan_missing")
        invalid_plan.query_id = "missing_query"
        bundle = make_bundle_write(plans=[invalid_plan])

        with self.assertRaisesRegex(PlanningBundleRepositoryError, "unknown SearchQuery"):
            self.repository.persist_planning_bundle(bundle, created_at=CREATED_AT)

        self.assertEqual(self.count_rows("planning_bundles"), 0)
        self.assertEqual(self.count_rows("planning_search_plans"), 0)

    def test_existing_committed_bundle_remains_after_later_failure(self):
        first = self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        invalid_query = make_query()
        invalid_query.career_path_id = "missing_path"

        with self.assertRaises(PlanningBundleRepositoryError):
            self.repository.persist_planning_bundle(
                make_bundle_write(
                    profile=make_profile(name="Changed"),
                    queries=[invalid_query],
                ),
                created_at=CREATED_AT,
            )

        self.assertEqual(self.count_rows("planning_bundles"), 1)
        self.assertIsNotNone(self.repository.get_planning_bundle(first.planning_bundle_id))

    def test_find_reusable_bundle_returns_latest_for_input_fingerprint(self):
        first = self.repository.persist_planning_bundle(make_bundle_write(), created_at="2026-01-01T00:00:00+00:00")
        second = self.repository.persist_planning_bundle(
            make_bundle_write(paths=[make_path(title="Changed path")]),
            created_at="2026-01-02T00:00:00+00:00",
        )
        bundle_row = self.repository.get_planning_bundle(first.planning_bundle_id)
        reusable = self.repository.find_reusable_bundle(bundle_row["input_fingerprint"])

        self.assertEqual(reusable["planning_bundle_id"], second.planning_bundle_id)

    def test_get_bundle_by_input_and_output_returns_expected_bundle(self):
        summary = self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        bundle_row = self.repository.get_planning_bundle(summary.planning_bundle_id)

        found = self.repository.get_bundle_by_input_and_output(
            bundle_row["input_fingerprint"],
            bundle_row["output_hash"],
        )

        self.assertEqual(found["planning_bundle_id"], summary.planning_bundle_id)

    def test_list_artifacts_for_bundle_returns_artifact_rows(self):
        summary = self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)
        artifacts = self.repository.list_artifacts_for_bundle(summary.planning_bundle_id)

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["artifact_type"], "target_career_paths_cache")

    def test_tests_never_touch_configured_production_database(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = DEFAULT_DATABASE_FILE.stat().st_mtime if before_exists else None

        self.repository.persist_planning_bundle(make_bundle_write(), created_at=CREATED_AT)

        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)
        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)


def make_input_fingerprint(
    *,
    profile=None,
    preferences=None,
    scope=None,
    model_name="model",
    prompt_version="prompt",
):
    profile = profile or make_profile()

    return build_planning_input_fingerprint(
        profile_content_hash=hash_user_profile(profile),
        user_preferences=preferences or {"market": "US"},
        search_scope=scope or make_scope(),
        model_provider="deepseek",
        model_name=model_name,
        prompt_version=prompt_version,
        generator_config={"limit": 8},
    )


def make_output_hash(bundle):
    return build_planning_output_hash(
        target_career_paths=bundle.target_career_paths,
        search_queries=bundle.search_queries,
        search_plans=bundle.search_plans,
    )


if __name__ == "__main__":
    unittest.main()
