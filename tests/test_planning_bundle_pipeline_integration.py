from contextlib import redirect_stdout
import importlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.config import DEFAULT_DATABASE_FILE
from src.database.connection import open_database_connection
from src.database.migrations import initialize_database
from src.database.planning_identity import (
    build_planning_input_fingerprint,
    hash_user_profile,
)
from src.database.repositories.planning_bundle_repository import (
    PlanningBundleRepository,
    PlanningBundleRepositoryError,
    PlanningBundleWrite,
)
from src.models import (
    AIFilterExecutionReport,
    CareerPathCategory,
    CareerSignal,
    RawItem,
    SearchAPIExecutionReport,
    SearchPlan,
    SearchQuery,
    SearchQueryType,
    SearchScope,
    SignalCategory,
    SourceType,
    TargetCareerPath,
    UserProfile,
)
from src.pipeline import MockPipeline
from src.storage import convert_to_json_ready, save_json


PLANNING_CONFIG = {
    "target_career_path_schema_version": "target_career_path_generation_v1",
    "search_query_max_queries_per_path": 8,
    "search_plan_builder": "rule_based_phase_6",
}


def make_profile(**overrides):
    data = {
        "profile_id": "profile_1",
        "name": "Test User",
        "background_summary": "Strategy analyst interested in AI.",
        "skills": ["strategy", "python"],
        "interests": ["AI"],
    }
    data.update(overrides)
    return UserProfile(**data)


def make_scope(**overrides):
    data = {
        "scope_id": "scope_1",
        "name": "Test scope",
        "locations": ["New York"],
        "languages": ["en"],
        "source_types": [SourceType.SEARCH_API],
        "freshness_days": 30,
        "max_results_per_query": 10,
    }
    data.update(overrides)
    return SearchScope(**data)


def make_path(path_id="path_ai", title="AI Strategy", **overrides):
    data = {
        "path_id": path_id,
        "title": title,
        "category": CareerPathCategory.AI_STRATEGY,
        "description": "AI strategy roles.",
        "fit_score": 91.0,
        "rationale": ["Matched AI strategy interest."],
        "keywords": ["AI", "strategy"],
        "suggested_roles": ["AI strategy analyst"],
        "search_seed_terms": ["AI strategy analyst"],
        "metadata": {"path_type": "core_match", "nested": {"level": 1}},
    }
    data.update(overrides)
    return TargetCareerPath(**data)


def make_query(path=None, query_id=None, query_text=None):
    path = path or make_path()
    return SearchQuery(
        query_id=query_id or f"q_{path.path_id}",
        career_path_id=path.path_id,
        career_path_title=path.title,
        query_text=query_text or f"{path.title} open role",
        query_type=SearchQueryType.JOB_SEARCH,
        priority=0.95,
        target_roles=path.suggested_roles,
        keywords=path.keywords,
        negative_keywords=["senior director"],
        rationale="Find open roles.",
        metadata={"generator": "rule_based_phase_5", "nested": {"x": "value"}},
    )


def make_plan(scope=None, query=None, plan_id=None):
    scope = scope or make_scope()
    query = query or make_query()
    return SearchPlan(
        plan_id=plan_id or f"plan_{scope.scope_id}_{query.query_id}",
        query_id=query.query_id,
        query_text=f"{query.query_text} (New York)",
        query_type=query.query_type,
        career_path_id=query.career_path_id,
        career_path_title=query.career_path_title,
        scope_id=scope.scope_id,
        source_types=[SourceType.SEARCH_API],
        locations=scope.locations,
        languages=scope.languages,
        freshness_days=scope.freshness_days,
        max_results=scope.max_results_per_query,
        priority=query.priority,
        negative_keywords=query.negative_keywords,
        metadata={"builder": "rule_based_phase_6", "mode": "test"},
    )


def make_bundle_write(
    *,
    profile=None,
    preferences=None,
    scope=None,
    paths=None,
    queries=None,
    plans=None,
):
    profile = profile or make_profile()
    scope = scope or make_scope()
    paths = paths or [make_path()]
    queries = queries or [make_query(path=paths[0])]
    plans = plans or [make_plan(scope=scope, query=queries[0])]
    return PlanningBundleWrite(
        user_profile=profile,
        user_preferences=preferences or {"market": "US", "weights": {"ai": 1}},
        search_scope=scope,
        target_career_paths=paths,
        search_queries=queries,
        search_plans=plans,
        generation_mode="generated",
        model_provider="deepseek",
        model_name="deepseek-v4-pro",
        prompt_version="target_career_path_prompt_v1",
        generator_config=PLANNING_CONFIG,
    )


def count_rows(database_path, table_name):
    connection = open_database_connection(database_path)
    try:
        return int(
            connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        )
    finally:
        connection.close()


def table_counts(database_path):
    return {
        table: count_rows(database_path, table)
        for table in (
            "user_profile_snapshots",
            "planning_bundles",
            "planning_target_career_paths",
            "planning_search_queries",
            "planning_search_plans",
        )
    }


class RecordingSourceRepository:
    def __init__(self):
        self.upsert_calls = []
        self.rows = {}
        self.next_id = 1

    def upsert_many(self, raw_items):
        batch = list(raw_items)
        self.upsert_calls.append(batch)
        return MagicMock(
            received_count=len(batch),
            unique_count=len(batch),
            inserted_count=len(batch),
            updated_count=0,
        )

    def get_by_fingerprint(self, fingerprint):
        if fingerprint not in self.rows:
            self.rows[fingerprint] = {"source_item_id": self.next_id}
            self.next_id += 1
        return self.rows[fingerprint]


class RecordingCareerRepository:
    def __init__(self):
        self.upsert_calls = []

    def upsert_many(self, records):
        batch = list(records)
        self.upsert_calls.append(batch)
        return MagicMock(
            received_count=len(batch),
            unique_count=len(batch),
            inserted_count=len(batch),
            updated_count=0,
        )


class FailingLookupRepository:
    def __init__(self):
        self.error = RuntimeError("lookup unavailable")

    def find_reusable_bundle(self, input_fingerprint):
        raise self.error


class FailingPersistRepository:
    def __init__(self):
        self.error = RuntimeError("write unavailable")

    def find_reusable_bundle(self, input_fingerprint):
        return None

    def persist_planning_bundle(self, bundle):
        raise self.error


def make_external_item():
    return RawItem(
        source_type=SourceType.SEARCH_API,
        title="AI strategy role",
        organization="Example Co",
        url="https://example.com/role",
        published_at=None,
        raw_text="AI strategy role",
    )


def build_pipeline(
    *,
    planning_repository=None,
    source_repository=None,
    career_repository=None,
    profile=None,
    preferences=None,
    scope=None,
    paths=None,
    queries=None,
    plans=None,
    force_refresh=False,
    events=None,
    search_api_raw_items=None,
):
    profile = profile or make_profile()
    preferences = preferences or {"market": "US", "weights": {"ai": 1}}
    scope = scope or make_scope()
    paths = paths or [make_path()]
    queries = queries or [make_query(path=paths[0])]
    plans = plans or [make_plan(scope=scope, query=queries[0])]
    events = events if events is not None else []
    search_api_raw_items = search_api_raw_items or []

    def career_path_generator(user_profile):
        events.append("career_path_generator")
        return paths

    def search_query_generator(target_career_paths):
        events.append("search_query_generator")
        return queries

    def search_plan_builder(search_queries, search_scope):
        events.append("search_plan_builder")
        return plans

    def search_api_executor(search_plans):
        events.append("external_search")
        return SearchAPIExecutionReport(
            raw_items=search_api_raw_items,
            executed_plan_count=1 if search_api_raw_items else 0,
        )

    def ai_filter_executor(raw_items, user_profile, target_career_paths):
        return AIFilterExecutionReport(
            filtered_raw_items=[],
            ai_filter_results=[],
            raw_item_statuses=[],
            executed_count=0,
        )

    return MockPipeline(
        raw_item_loader=lambda: [],
        user_profile_loader=lambda: profile,
        search_scope_loader=lambda: scope,
        career_path_generator=career_path_generator,
        search_query_generator=search_query_generator,
        search_plan_builder=search_plan_builder,
        search_api_executor=search_api_executor,
        rss_executor=lambda search_scope, search_plans: ([], 0),
        selected_website_executor=lambda search_scope, search_plans: ([], 0),
        ai_filter_executor=ai_filter_executor,
        normalizer=lambda raw_items, ai_results: [
            CareerSignal(
                signal_id="signal_1",
                category=SignalCategory.JOB,
                title="AI strategy role",
                organization="Example Co",
                url="https://example.com/role",
                published_at=None,
                summary="Relevant role.",
                source_type=SourceType.SEARCH_API,
            )
        ] if raw_items else [],
        source_item_repository=source_repository,
        career_signal_repository=career_repository,
        planning_bundle_repository=planning_repository,
        user_preferences_loader=lambda: preferences,
        planning_model_provider="deepseek",
        planning_model_name="deepseek-v4-pro",
        planning_prompt_version="target_career_path_prompt_v1",
        planning_generator_config=PLANNING_CONFIG,
        planning_force_refresh=force_refresh,
    )


class TemporaryPlanningPipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "planning.db"
        initialize_database(database_path=self.database_path)
        self.repository = PlanningBundleRepository(database_path=self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()


class DependencyAndMainTests(TemporaryPlanningPipelineTestCase):
    def test_pipeline_accepts_optional_planning_bundle_repository(self):
        pipeline = build_pipeline(planning_repository=self.repository)
        self.assertIs(pipeline.planning_bundle_repository, self.repository)

    def test_no_planning_repository_preserves_current_planning_behavior(self):
        events = []
        output = build_pipeline(events=events).run()
        self.assertEqual(
            events,
            [
                "career_path_generator",
                "search_query_generator",
                "search_plan_builder",
                "external_search",
            ],
        )
        self.assertEqual(output.summary.total_raw_items, 0)

    def test_planning_repository_is_independent_of_source_and_signal_repositories(self):
        source_repository = RecordingSourceRepository()
        career_repository = RecordingCareerRepository()
        raw_item = make_external_item()
        build_pipeline(
            planning_repository=self.repository,
            source_repository=source_repository,
            career_repository=career_repository,
            search_api_raw_items=[raw_item],
        ).run()
        self.assertEqual(len(source_repository.upsert_calls), 1)
        self.assertEqual(career_repository.upsert_calls, [])

    def test_main_constructs_and_injects_planning_bundle_repository(self):
        main_module = importlib.import_module("src.main")
        captured = {}

        class FakeRepository:
            def __init__(self, database_path):
                self.database_path = database_path

        class FakePipeline:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs

            def run(self):
                summary = MagicMock()
                summary.total_target_career_paths = 0
                summary.total_search_queries = 0
                summary.total_search_plans = 0
                summary.total_search_api_plans_executed = 0
                summary.total_search_api_plans_deferred = 0
                summary.total_search_api_result_failures = 0
                summary.total_rss_feeds_executed = 0
                summary.total_selected_websites_executed = 0
                summary.total_raw_items = 0
                summary.total_raw_items_sent_to_ai_filter = 0
                summary.total_ai_filter_results = 0
                summary.total_filtered_raw_items = 0
                summary.total_rejected_raw_items = 0
                summary.total_career_signals = 0
                return MagicMock(summary=summary)

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "main.db"
            with patch.object(main_module, "get_database_path", return_value=database_path), \
                patch.object(main_module, "initialize_database") as initialize_mock, \
                patch.object(main_module, "PlanningBundleRepository", FakeRepository), \
                patch.object(main_module, "SourceItemRepository", FakeRepository), \
                patch.object(main_module, "CareerSignalRepository", FakeRepository), \
                patch.object(main_module, "MockPipeline", FakePipeline), \
                patch.object(main_module, "ensure_project_directories"), \
                patch.object(main_module, "validate_required_planning_inputs"), \
                patch.object(main_module, "load_user_preferences_from_json", return_value={}), \
                patch.object(main_module, "_file_sha256", return_value="hash"), \
                patch.object(main_module, "save_json", return_value=Path(temp_dir) / "out.json"), \
                redirect_stdout(io.StringIO()):
                main_module.main()

        initialize_mock.assert_called_once_with(database_path=database_path)
        self.assertIsInstance(
            captured["kwargs"]["planning_bundle_repository"],
            FakeRepository,
        )

    def test_importing_src_main_has_no_database_side_effect(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = DEFAULT_DATABASE_FILE.stat().st_mtime if before_exists else None
        sys.modules.pop("src.main", None)
        importlib.import_module("src.main")
        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)
        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)


class FingerprintTests(TemporaryPlanningPipelineTestCase):
    def test_pipeline_fingerprint_equals_phase5a_helper(self):
        profile = make_profile()
        preferences = {"b": 2, "a": 1}
        scope = make_scope()
        pipeline = build_pipeline(planning_repository=self.repository)
        expected = build_planning_input_fingerprint(
            profile_content_hash=hash_user_profile(profile),
            user_preferences=preferences,
            search_scope=scope,
            model_provider="deepseek",
            model_name="deepseek-v4-pro",
            prompt_version="target_career_path_prompt_v1",
            generator_config=PLANNING_CONFIG,
        )
        actual = pipeline.build_planning_input_fingerprint(
            user_profile=profile,
            user_preferences=preferences,
            search_scope=scope,
        )
        self.assertEqual(actual, expected)

    def test_equivalent_json_formatting_produces_same_fingerprint(self):
        compact = json.loads('{"market":"US","weights":{"ai":1}}')
        pretty = json.loads(json.dumps(compact, indent=2))
        pipeline = build_pipeline(planning_repository=self.repository)
        first = pipeline.build_planning_input_fingerprint(
            user_profile=make_profile(),
            user_preferences=compact,
            search_scope=make_scope(),
        )
        second = pipeline.build_planning_input_fingerprint(
            user_profile=make_profile(),
            user_preferences=pretty,
            search_scope=make_scope(),
        )
        self.assertEqual(first, second)

    def test_material_input_changes_prevent_reuse(self):
        pipeline = build_pipeline(planning_repository=self.repository)
        base = pipeline.build_planning_input_fingerprint(
            user_profile=make_profile(),
            user_preferences={"market": "US"},
            search_scope=make_scope(),
        )
        changed_profile = pipeline.build_planning_input_fingerprint(
            user_profile=make_profile(name="Changed"),
            user_preferences={"market": "US"},
            search_scope=make_scope(),
        )
        changed_preferences = pipeline.build_planning_input_fingerprint(
            user_profile=make_profile(),
            user_preferences={"market": "EU"},
            search_scope=make_scope(),
        )
        changed_scope = pipeline.build_planning_input_fingerprint(
            user_profile=make_profile(),
            user_preferences={"market": "US"},
            search_scope=make_scope(locations=["Boston"]),
        )
        self.assertNotEqual(base, changed_profile)
        self.assertNotEqual(base, changed_preferences)
        self.assertNotEqual(base, changed_scope)

    def test_model_prompt_runtime_timestamp_and_output_path_rules(self):
        base = build_pipeline(
            planning_repository=self.repository,
        ).build_planning_input_fingerprint(
            user_profile=make_profile(),
            user_preferences={"market": "US"},
            search_scope=make_scope(),
        )
        changed_model = MockPipeline(
            **_minimal_pipeline_kwargs(),
            planning_model_provider="deepseek",
            planning_model_name="other-model",
            planning_prompt_version="target_career_path_prompt_v1",
            planning_generator_config=PLANNING_CONFIG,
        ).build_planning_input_fingerprint(
            user_profile=make_profile(),
            user_preferences={"market": "US"},
            search_scope=make_scope(),
        )
        changed_prompt = MockPipeline(
            **_minimal_pipeline_kwargs(),
            planning_model_provider="deepseek",
            planning_model_name="deepseek-v4-pro",
            planning_prompt_version="other-prompt",
            planning_generator_config=PLANNING_CONFIG,
        ).build_planning_input_fingerprint(
            user_profile=make_profile(),
            user_preferences={"market": "US"},
            search_scope=make_scope(),
        )
        repeat_with_runtime_noise = build_pipeline(
            planning_repository=self.repository,
        ).build_planning_input_fingerprint(
            user_profile=make_profile(),
            user_preferences={"market": "US", "runtime_timestamp": None},
            search_scope=make_scope(),
        )
        self.assertNotEqual(base, changed_model)
        self.assertNotEqual(base, changed_prompt)
        self.assertEqual(base, base)
        self.assertNotEqual(base, repeat_with_runtime_noise)


class ReuseTests(TemporaryPlanningPipelineTestCase):
    def test_reusable_bundle_skips_all_planning_builders_and_reports(self):
        summary = self.repository.persist_planning_bundle(make_bundle_write())
        before = table_counts(self.database_path)
        events = []
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            output = build_pipeline(
                planning_repository=self.repository,
                events=events,
            ).run()
        after = table_counts(self.database_path)
        self.assertEqual(events, ["external_search"])
        self.assertEqual(output.target_career_paths[0].path_id, "path_ai")
        self.assertEqual(output.search_queries[0].query_id, "q_path_ai")
        self.assertEqual(output.search_plans[0].plan_id, "plan_scope_1_q_path_ai")
        self.assertEqual(after, before)
        self.assertIn(
            f"Planning Bundle reused: bundle_id={summary.planning_bundle_id}",
            stdout.getvalue(),
        )

    def test_hydration_preserves_types_order_metadata_and_relationships(self):
        path_b = make_path(path_id="path_b", title="Bridge")
        path_a = make_path(path_id="path_a", title="Core")
        query_b = make_query(path=path_b, query_id="q_b")
        query_a = make_query(path=path_a, query_id="q_a")
        plan_b = make_plan(query=query_b, plan_id="plan_b")
        plan_a = make_plan(query=query_a, plan_id="plan_a")
        self.repository.persist_planning_bundle(
            make_bundle_write(
                paths=[path_b, path_a],
                queries=[query_b, query_a],
                plans=[plan_b, plan_a],
            )
        )
        output = build_pipeline(
            planning_repository=self.repository,
            paths=[path_b, path_a],
            queries=[query_b, query_a],
            plans=[plan_b, plan_a],
        ).run()
        self.assertEqual([path.path_id for path in output.target_career_paths], ["path_b", "path_a"])
        self.assertEqual([query.query_id for query in output.search_queries], ["q_b", "q_a"])
        self.assertEqual([plan.plan_id for plan in output.search_plans], ["plan_b", "plan_a"])
        self.assertIs(output.target_career_paths[0].category, CareerPathCategory.AI_STRATEGY)
        self.assertEqual(output.search_queries[0].query_type, SearchQueryType.JOB_SEARCH)
        self.assertEqual(output.search_plans[0].source_types, [SourceType.SEARCH_API])
        self.assertEqual(output.target_career_paths[0].metadata["nested"]["level"], 1)
        self.assertEqual(output.search_plans[0].query_id, output.search_queries[0].query_id)

    def test_reuse_does_not_insert_duplicate_low_frequency_rows(self):
        self.repository.persist_planning_bundle(make_bundle_write())
        before = table_counts(self.database_path)
        build_pipeline(planning_repository=self.repository).run()
        self.assertEqual(table_counts(self.database_path), before)


class MaterializeAndPersistTests(TemporaryPlanningPipelineTestCase):
    def test_no_reusable_bundle_runs_existing_flow_and_persists_before_search(self):
        events = []
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            output = build_pipeline(
                planning_repository=self.repository,
                paths=[make_path(metadata={"used_cache": True})],
                events=events,
            ).run()
        self.assertEqual(
            events,
            [
                "career_path_generator",
                "search_query_generator",
                "search_plan_builder",
                "external_search",
            ],
        )
        self.assertEqual(output.summary.total_search_plans, 1)
        self.assertEqual(count_rows(self.database_path, "planning_bundles"), 1)
        self.assertIn("generation_mode=file_cache", stdout.getvalue())

    def test_deduplicated_search_queries_persist_and_build_plans_without_network(self):
        from src.search_query_generator import generate_search_queries
        from src.search_plan_builder import build_search_plans

        path = make_path(
            path_id="investment_banking_analyst",
            title="Investment Banking Analyst",
            category=CareerPathCategory.UNKNOWN,
            suggested_roles=[],
            search_seed_terms=[],
            metadata={
                "search_seed_terms_zh": [
                    "投资银行 分析师",
                    "投资 银行 分析师",
                    "投资银行部 分析师",
                    "投资银行业务 分析师",
                ]
            },
        )
        queries = generate_search_queries([path], max_queries_per_path=8)
        plans = build_search_plans(queries, make_scope())
        events = []

        build_pipeline(
            planning_repository=self.repository,
            paths=[path],
            queries=queries,
            plans=plans,
            events=events,
        ).run()

        self.assertEqual(len(queries), 1)
        self.assertEqual(count_rows(self.database_path, "planning_search_queries"), 1)
        self.assertEqual(count_rows(self.database_path, "planning_search_plans"), 1)
        self.assertIn("search_plan_builder", events)
        self.assertIn("external_search", events)

    def test_repeated_identical_materialization_reuses_persisted_bundle(self):
        build_pipeline(planning_repository=self.repository).run()
        before = table_counts(self.database_path)
        events = []
        build_pipeline(planning_repository=self.repository, events=events).run()
        self.assertEqual(events, ["external_search"])
        self.assertEqual(table_counts(self.database_path), before)

    def test_same_input_changed_output_creates_new_bundle_when_forced(self):
        build_pipeline(planning_repository=self.repository).run()
        changed_path = make_path(title="Changed AI Strategy")
        build_pipeline(
            planning_repository=self.repository,
            force_refresh=True,
            paths=[changed_path],
            queries=[make_query(path=changed_path)],
            plans=[make_plan(query=make_query(path=changed_path))],
        ).run()
        self.assertEqual(count_rows(self.database_path, "user_profile_snapshots"), 1)
        self.assertEqual(count_rows(self.database_path, "planning_bundles"), 2)

    def test_changed_profile_or_scope_creates_expected_bundle_identity(self):
        build_pipeline(planning_repository=self.repository).run()
        build_pipeline(
            planning_repository=self.repository,
            scope=make_scope(locations=["Boston"]),
        ).run()
        self.assertEqual(count_rows(self.database_path, "user_profile_snapshots"), 1)
        self.assertEqual(count_rows(self.database_path, "planning_bundles"), 2)
        build_pipeline(
            planning_repository=self.repository,
            profile=make_profile(name="Changed"),
        ).run()
        self.assertEqual(count_rows(self.database_path, "user_profile_snapshots"), 2)
        self.assertEqual(count_rows(self.database_path, "planning_bundles"), 3)


class ForceRegenerationTests(TemporaryPlanningPipelineTestCase):
    def test_force_refresh_bypasses_reuse_but_identical_output_reuses_bundle(self):
        first = self.repository.persist_planning_bundle(make_bundle_write())
        events = []
        build_pipeline(
            planning_repository=self.repository,
            force_refresh=True,
            events=events,
        ).run()
        self.assertIn("career_path_generator", events)
        self.assertEqual(count_rows(self.database_path, "planning_bundles"), 1)
        self.assertEqual(
            self.repository.find_reusable_bundle(
                self.repository.get_planning_bundle(first.planning_bundle_id)["input_fingerprint"]
            )["planning_bundle_id"],
            first.planning_bundle_id,
        )

    def test_force_refresh_different_output_creates_new_bundle(self):
        self.repository.persist_planning_bundle(make_bundle_write())
        changed_path = make_path(title="Regenerated Path")
        query = make_query(path=changed_path)
        build_pipeline(
            planning_repository=self.repository,
            force_refresh=True,
            paths=[changed_path],
            queries=[query],
            plans=[make_plan(query=query)],
        ).run()
        self.assertEqual(count_rows(self.database_path, "planning_bundles"), 2)

    def test_project_has_no_new_public_force_regeneration_cli_flag(self):
        main_text = Path("src/main.py").read_text(encoding="utf-8")
        self.assertNotIn("argparse", main_text)
        self.assertNotIn("--force", main_text)


class ErrorAndCompatibilityTests(TemporaryPlanningPipelineTestCase):
    def test_malformed_reusable_bundle_raises_clear_error_with_cause(self):
        summary = self.repository.persist_planning_bundle(make_bundle_write())
        connection = open_database_connection(self.database_path)
        try:
            connection.execute(
                "UPDATE planning_target_career_paths SET payload_json = ?",
                ("not-json",),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(RuntimeError, "Malformed reusable Planning Bundle") as context:
            build_pipeline(planning_repository=self.repository).run()
        self.assertIsInstance(context.exception.__cause__, PlanningBundleRepositoryError)
        self.assertIsNotNone(summary)

    def test_lookup_failure_is_surfaced(self):
        with self.assertRaisesRegex(RuntimeError, "Planning Bundle lookup failed") as context:
            build_pipeline(planning_repository=FailingLookupRepository()).run()
        self.assertIs(context.exception.__cause__, context.exception.__cause__)

    def test_persistence_failure_prevents_external_search_and_preserves_cause(self):
        events = []
        with self.assertRaisesRegex(RuntimeError, "persistence failed") as context:
            build_pipeline(
                planning_repository=FailingPersistRepository(),
                events=events,
            ).run()
        self.assertNotIn("external_search", events)
        self.assertIsNotNone(context.exception.__cause__)

    def test_transaction_failure_leaves_no_partial_bundle(self):
        bad_query = make_query()
        bad_query.career_path_id = "missing"
        with self.assertRaises(RuntimeError):
            build_pipeline(
                planning_repository=self.repository,
                queries=[bad_query],
                plans=[make_plan(query=bad_query)],
            ).run()
        self.assertEqual(count_rows(self.database_path, "planning_bundles"), 0)
        self.assertEqual(count_rows(self.database_path, "planning_search_queries"), 0)

    def test_output_contract_and_saved_json_structure_remain_unchanged(self):
        with patch("src.pipeline.utc_now_iso", return_value="2026-07-28T00:00:00+00:00"):
            without_db = build_pipeline().run()
            with_db = build_pipeline(planning_repository=self.repository).run()
        self.assertEqual(convert_to_json_ready(with_db), convert_to_json_ready(without_db))
        with tempfile.TemporaryDirectory() as temp_dir:
            without_path = Path(temp_dir) / "without.json"
            with_path = Path(temp_dir) / "with.json"
            save_json(without_db, without_path)
            save_json(with_db, with_path)
            self.assertEqual(
                json.loads(with_path.read_text(encoding="utf-8")).keys(),
                json.loads(without_path.read_text(encoding="utf-8")).keys(),
            )

    def test_raw_item_and_career_signal_persistence_are_unchanged(self):
        source_repository = RecordingSourceRepository()
        career_repository = RecordingCareerRepository()
        build_pipeline(
            planning_repository=self.repository,
            source_repository=source_repository,
            career_repository=career_repository,
            search_api_raw_items=[make_external_item()],
        ).run()
        self.assertEqual(len(source_repository.upsert_calls), 1)
        self.assertEqual(career_repository.upsert_calls, [])

    def test_tests_never_touch_configured_production_database(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = DEFAULT_DATABASE_FILE.stat().st_mtime if before_exists else None
        build_pipeline(planning_repository=self.repository).run()
        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)
        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)


def _minimal_pipeline_kwargs():
    return {
        "raw_item_loader": lambda: [],
        "user_profile_loader": make_profile,
        "search_scope_loader": make_scope,
        "career_path_generator": lambda user_profile: [make_path()],
        "search_query_generator": lambda paths: [make_query(path=paths[0])],
        "search_plan_builder": lambda queries, scope: [make_plan(scope=scope, query=queries[0])],
        "search_api_executor": lambda plans: SearchAPIExecutionReport(),
        "rss_executor": lambda scope, plans: ([], 0),
        "selected_website_executor": lambda scope, plans: ([], 0),
        "ai_filter_executor": lambda raw_items, profile, paths: AIFilterExecutionReport(),
        "normalizer": lambda raw_items, ai_results: [],
    }


if __name__ == "__main__":
    unittest.main()
