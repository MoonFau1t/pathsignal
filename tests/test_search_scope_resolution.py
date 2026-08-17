import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from src.database.planning_identity import (
    build_planning_input_fingerprint,
    hash_user_profile,
)
from src.models import (
    CareerPathCategory,
    SearchQuery,
    SearchQueryType,
    SearchScope,
    SourceType,
    TargetCareerPath,
    UserProfile,
)
from src.profile_loader import load_user_preferences_from_json
from src.scope_loader import (
    load_effective_search_scope_from_json,
    load_search_scope_from_json,
)
from src.search_query_generator import (
    _deduplicate_queries_by_persistence_identity,
    generate_search_queries,
)
from src.search_plan_builder import build_search_plans
from src.search_scope_resolution import (
    SearchScopeResolutionError,
    build_effective_search_scope,
    derive_search_locations,
    derive_search_seniority_levels,
)
from src.storage import convert_to_json_ready


PREFERENCES_PATH = Path("inputs/user_preferences_final.json")
SCOPE_PATH = Path("inputs/search_scope.json")
EXPECTED_DERIVED_LOCATIONS = [
    "Beijing",
    "Shanghai",
    "Shenzhen",
    "Hangzhou",
    "Nanjing",
    "Hong Kong",
    "Asia-Pacific",
    "EMEA",
]
EXPECTED_DERIVED_SENIORITY = [
    "entry_level",
    "management_trainee",
    "analyst",
    "research_assistant",
    "intern",
    "associate",
]


def load_preferences():
    return load_user_preferences_from_json(PREFERENCES_PATH)


def load_scope_config():
    return json.loads(SCOPE_PATH.read_text(encoding="utf-8"))


def make_profile():
    return UserProfile(
        profile_id="profile_1",
        name="Test User",
        background_summary="Strategy analyst interested in AI.",
    )


def make_target_path():
    return TargetCareerPath(
        path_id="path_ai_strategy",
        title="AI Strategy",
        category=CareerPathCategory.AI_STRATEGY,
        description="AI strategy roles.",
        fit_score=90,
        suggested_roles=["AI strategy analyst"],
        search_seed_terms=["AI strategy analyst"],
        metadata={
            "search_seed_terms_en": ["AI strategy analyst"],
            "search_seed_terms_zh": [],
        },
    )


class SearchScopeContractTests(unittest.TestCase):
    def test_user_preferences_final_json_is_not_modified_by_resolution(self):
        before = PREFERENCES_PATH.read_bytes()
        build_effective_search_scope(load_preferences(), load_scope_config())
        after = PREFERENCES_PATH.read_bytes()

        self.assertEqual(after, before)

    def test_checked_in_search_scope_no_longer_contains_locations(self):
        self.assertNotIn("locations", load_scope_config())

    def test_checked_in_search_scope_no_longer_contains_seniority_levels(self):
        self.assertNotIn("seniority_levels", load_scope_config())

    def test_languages_remain_available_from_technical_config(self):
        scope = load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())

        self.assertEqual(scope.languages, ["en", "zh"])

    def test_no_explicit_final_preference_search_language_field_exists(self):
        preferences_text = json.dumps(load_preferences(), sort_keys=True).lower()

        self.assertNotIn("search_languages", preferences_text)
        self.assertNotIn("target_search_languages", preferences_text)


class SearchScopeDerivationTests(unittest.TestCase):
    def test_locations_derive_deterministically_from_current_preferences(self):
        self.assertEqual(
            derive_search_locations(load_preferences()),
            EXPECTED_DERIVED_LOCATIONS,
        )

    def test_seniority_levels_derive_deterministically_from_current_preferences(self):
        self.assertEqual(
            derive_search_seniority_levels(load_preferences()),
            EXPECTED_DERIVED_SENIORITY,
        )

    def test_equivalent_preference_dictionaries_with_different_order_match(self):
        preferences = load_preferences()
        reordered = dict(reversed(list(preferences.items())))

        self.assertEqual(
            derive_search_locations(reordered),
            derive_search_locations(preferences),
        )
        self.assertEqual(
            derive_search_seniority_levels(reordered),
            derive_search_seniority_levels(preferences),
        )

    def test_formatting_only_changes_do_not_affect_derived_scope(self):
        preferences = load_preferences()
        reparsed = json.loads(json.dumps(preferences, indent=2, ensure_ascii=False))

        self.assertEqual(
            build_effective_search_scope(reparsed, load_scope_config()).to_dict(),
            build_effective_search_scope(preferences, load_scope_config()).to_dict(),
        )

    def test_input_dictionaries_are_not_mutated(self):
        preferences = load_preferences()
        config = load_scope_config()
        preferences_before = copy.deepcopy(preferences)
        config_before = copy.deepcopy(config)

        build_effective_search_scope(preferences, config)

        self.assertEqual(preferences, preferences_before)
        self.assertEqual(config, config_before)

    def test_incomplete_location_preferences_raise_clear_error(self):
        preferences = load_preferences()
        preferences["location_preferences"] = {}

        with self.assertRaisesRegex(
            SearchScopeResolutionError,
            "preferred_cities",
        ):
            derive_search_locations(preferences)

    def test_incomplete_seniority_preferences_raise_clear_error(self):
        preferences = load_preferences()
        preferences["seniority_preferences"] = {}

        with self.assertRaisesRegex(
            SearchScopeResolutionError,
            "preferred_levels",
        ):
            derive_search_seniority_levels(preferences)


class SearchScopeLegacyConflictTests(unittest.TestCase):
    def test_legacy_matching_semantic_fields_are_accepted(self):
        config = load_scope_config()
        config["locations"] = EXPECTED_DERIVED_LOCATIONS
        config["seniority_levels"] = EXPECTED_DERIVED_SENIORITY

        scope = build_effective_search_scope(load_preferences(), config)

        self.assertEqual(scope.locations, EXPECTED_DERIVED_LOCATIONS)
        self.assertEqual(scope.seniority_levels, EXPECTED_DERIVED_SENIORITY)

    def test_legacy_conflicting_locations_are_detected(self):
        config = load_scope_config()
        config["locations"] = ["Conflicting"]

        with self.assertRaisesRegex(SearchScopeResolutionError, "locations"):
            build_effective_search_scope(load_preferences(), config)

    def test_legacy_conflicting_seniority_levels_are_detected(self):
        config = load_scope_config()
        config["seniority_levels"] = ["senior"]

        with self.assertRaisesRegex(SearchScopeResolutionError, "seniority_levels"):
            build_effective_search_scope(load_preferences(), config)


class SearchScopeTechnicalConfigTests(unittest.TestCase):
    def test_search_scope_enum_and_nested_hydration_remains_correct(self):
        scope = load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())

        self.assertEqual(scope.source_types[0], SourceType.SEARCH_API)
        self.assertEqual(scope.selected_websites[0].source_type, SourceType.SELECTED_WEBSITE)
        self.assertEqual(scope.rss_feeds[0].name, "Example AI News RSS")

    def test_allowed_domains_remain_unchanged(self):
        config = load_scope_config()
        scope = load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())

        self.assertEqual(scope.allowed_domains, config["allowed_domains"])

    def test_excluded_domains_remain_unchanged(self):
        config = load_scope_config()
        scope = load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())

        self.assertEqual(scope.excluded_domains, config["excluded_domains"])

    def test_rss_feeds_remain_unchanged(self):
        config = load_scope_config()
        scope = load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())

        self.assertEqual(
            [feed.to_dict() for feed in scope.rss_feeds],
            config["rss_feeds"],
        )

    def test_selected_websites_remain_unchanged(self):
        config = load_scope_config()
        scope = load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())

        self.assertEqual(
            [site.to_dict() for site in scope.selected_websites],
            config["selected_websites"],
        )

    def test_source_enable_flags_remain_unchanged(self):
        config = load_scope_config()
        scope = load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())

        self.assertEqual(scope.enable_search_api, config["enable_search_api"])
        self.assertEqual(scope.enable_rss, config["enable_rss"])
        self.assertEqual(
            scope.enable_selected_websites,
            config["enable_selected_websites"],
        )

    def test_freshness_days_remains_unchanged(self):
        config = load_scope_config()
        scope = load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())

        self.assertEqual(scope.freshness_days, config["freshness_days"])

    def test_max_results_per_query_remains_unchanged(self):
        config = load_scope_config()
        scope = load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())

        self.assertEqual(
            scope.max_results_per_query,
            config["max_results_per_query"],
        )


class SearchPlanAndIdentityCompatibilityTests(unittest.TestCase):
    def test_search_queries_remain_unchanged_for_equivalent_target_paths(self):
        target_paths = [make_target_path()]
        first_queries = generate_search_queries(target_paths)
        second_queries = generate_search_queries(target_paths)

        self.assertEqual(
            convert_to_json_ready(first_queries),
            convert_to_json_ready(second_queries),
        )

    def test_duplicate_canonical_query_identity_within_path_keeps_first_only(self):
        path = TargetCareerPath(
            path_id="path_investment_banking",
            title="Investment Banking",
            category=CareerPathCategory.UNKNOWN,
            description="Investment banking roles.",
            fit_score=90,
            metadata={
                "search_seed_terms_zh": [
                    "投资银行 分析师",
                    "投资 银行 分析师",
                    "投资银行部 分析师",
                    "投资银行业务 分析师",
                ],
            },
        )

        queries = generate_search_queries([path], max_queries_per_path=8)

        self.assertEqual(
            [query.query_id for query in queries],
            ["q_path_investment_banking_open_role"],
        )
        self.assertEqual(queries[0].query_text, "投资银行 分析师 open role")

    def test_distinct_canonical_query_identities_within_path_remain_distinct(self):
        path = TargetCareerPath(
            path_id="path_alpha",
            title="Alpha",
            category=CareerPathCategory.UNKNOWN,
            description="Alpha roles.",
            fit_score=80,
            metadata={"search_seed_terms_en": ["alpha analyst", "beta analyst"]},
        )

        queries = generate_search_queries([path], max_queries_per_path=8)

        self.assertEqual(
            [query.query_id for query in queries],
            [
                "q_path_alpha_alpha_analyst_open_role",
                "q_path_alpha_beta_analyst_open_role",
            ],
        )

    def test_same_query_identity_under_different_career_paths_is_not_deduplicated(self):
        first = SearchQuery(
            query_id="q_shared",
            career_path_id="path_a",
            career_path_title="Path A",
            query_text="shared open role",
            query_type=SearchQueryType.JOB_SEARCH,
            priority=0.9,
        )
        second = SearchQuery(
            query_id="q_shared",
            career_path_id="path_b",
            career_path_title="Path B",
            query_text="shared open role",
            query_type=SearchQueryType.JOB_SEARCH,
            priority=0.9,
        )

        self.assertEqual(
            _deduplicate_queries_by_persistence_identity([first, second]),
            [first, second],
        )

    def test_existing_unique_query_behavior_remains_unchanged(self):
        queries = generate_search_queries([make_target_path()])

        self.assertEqual(len(queries), 3)
        self.assertEqual(
            [query.query_id for query in queries],
            [
                "q_path_ai_strategy_ai_strategy_analyst_open_role",
                "q_path_ai_strategy_ai_strategy_analyst_consulting",
                "q_path_ai_strategy_ai_strategy_analyst_digital_transformation",
            ],
        )

    def test_search_plans_retain_technical_settings(self):
        scope = load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())
        queries = generate_search_queries([make_target_path()])
        plan = build_search_plans(queries[:1], scope)[0]

        self.assertEqual(plan.locations, EXPECTED_DERIVED_LOCATIONS)
        self.assertEqual(plan.languages, ["en", "zh"])
        self.assertEqual(plan.allowed_domains, load_scope_config()["allowed_domains"])
        self.assertEqual(plan.freshness_days, 30)
        self.assertEqual(plan.max_results, 10)
        self.assertIn(SourceType.SEARCH_API, plan.source_types)

    def test_planning_fingerprint_unchanged_when_effective_scope_equivalent(self):
        preferences = load_preferences()
        new_config = load_scope_config()
        legacy_config = copy.deepcopy(new_config)
        legacy_config["locations"] = EXPECTED_DERIVED_LOCATIONS
        legacy_config["seniority_levels"] = EXPECTED_DERIVED_SENIORITY
        profile = make_profile()

        new_scope = build_effective_search_scope(preferences, new_config)
        legacy_scope = build_effective_search_scope(preferences, legacy_config)

        first = _fingerprint(profile, preferences, new_scope)
        second = _fingerprint(profile, preferences, legacy_scope)

        self.assertEqual(first, second)

    def test_material_location_preference_change_alters_fingerprint(self):
        preferences = load_preferences()
        changed = copy.deepcopy(preferences)
        changed["location_preferences"]["preferred_cities"] = ["Shanghai"]
        profile = make_profile()

        base_scope = build_effective_search_scope(preferences, load_scope_config())
        changed_scope = build_effective_search_scope(changed, load_scope_config())

        self.assertNotEqual(
            _fingerprint(profile, preferences, base_scope),
            _fingerprint(profile, changed, changed_scope),
        )

    def test_material_seniority_preference_change_alters_fingerprint(self):
        preferences = load_preferences()
        changed = copy.deepcopy(preferences)
        changed["seniority_preferences"]["preferred_levels"] = ["Analyst"]
        profile = make_profile()

        base_scope = build_effective_search_scope(preferences, load_scope_config())
        changed_scope = build_effective_search_scope(changed, load_scope_config())

        self.assertNotEqual(
            _fingerprint(profile, preferences, base_scope),
            _fingerprint(profile, changed, changed_scope),
        )

    def test_technical_scope_change_alters_fingerprint(self):
        preferences = load_preferences()
        config = load_scope_config()
        changed_config = copy.deepcopy(config)
        changed_config["freshness_days"] = 14
        profile = make_profile()

        base_scope = build_effective_search_scope(preferences, config)
        changed_scope = build_effective_search_scope(preferences, changed_config)

        self.assertNotEqual(
            _fingerprint(profile, preferences, base_scope),
            _fingerprint(profile, preferences, changed_scope),
        )

    def test_no_additional_llm_call_is_introduced(self):
        with patch("src.career_path_generator.TargetCareerPathClient") as client:
            load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())

        client.assert_not_called()

    def test_json_output_contract_remains_unchanged(self):
        scope = load_effective_search_scope_from_json(SCOPE_PATH, load_preferences())

        self.assertEqual(
            set(scope.to_dict()),
            set(SearchScope.from_dict(scope.to_dict()).to_dict()),
        )


class LoaderCompatibilityTests(unittest.TestCase):
    def test_legacy_loader_still_loads_complete_search_scope_payloads(self):
        path = Path(".tmp") / "legacy-search-scope-test.json"
        path.parent.mkdir(exist_ok=True)
        payload = load_scope_config()
        payload["locations"] = ["Test"]
        payload["seniority_levels"] = ["entry_level"]
        path.write_text(json.dumps(payload), encoding="utf-8")

        try:
            scope = load_search_scope_from_json(path)
        finally:
            path.unlink()

        self.assertEqual(scope.locations, ["Test"])
        self.assertEqual(scope.seniority_levels, ["entry_level"])


def _fingerprint(
    profile: UserProfile,
    preferences: dict,
    scope: SearchScope,
) -> str:
    return build_planning_input_fingerprint(
        profile_content_hash=hash_user_profile(profile),
        user_preferences=preferences,
        search_scope=scope,
        model_provider="deepseek",
        model_name="deepseek-v4-pro",
        prompt_version="target_career_path_prompt_v1",
        generator_config={
            "target_career_path_schema_version": (
                "target_career_path_generation_v1"
            ),
            "search_query_max_queries_per_path": 8,
            "search_plan_builder": "rule_based_phase_6",
        },
    )


if __name__ == "__main__":
    unittest.main()
