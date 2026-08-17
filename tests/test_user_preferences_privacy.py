import io
import importlib
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from src.career_path_generator import _build_target_career_path_cache_key
from src.models import UserProfile


def make_profile() -> UserProfile:
    return UserProfile(
        profile_id="profile_1",
        name="Test User",
        background_summary="Strategy analyst interested in AI.",
    )


class UserPreferencesPrivacyTests(unittest.TestCase):
    def test_missing_user_preferences_final_json_fails_fast(self):
        main_module = importlib.import_module("src.main")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_profile = root / "user_profile.json"
            user_preferences = root / "user_preferences_final.json"
            search_scope = root / "search_scope.json"
            user_profile.write_text("{}", encoding="utf-8")
            search_scope.write_text("{}", encoding="utf-8")

            with patch.object(main_module, "USER_PROFILE_FILE", user_profile), \
                patch.object(main_module, "USER_PREFERENCES_FILE", user_preferences), \
                patch.object(main_module, "SEARCH_SCOPE_FILE", search_scope):
                with self.assertRaises(FileNotFoundError) as context:
                    main_module.validate_required_planning_inputs()

        message = str(context.exception)
        self.assertIn(str(user_preferences), message)
        self.assertIn("Required planning input", message)

    def test_existing_user_preferences_final_json_allows_startup_validation(self):
        main_module = importlib.import_module("src.main")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_profile = root / "user_profile.json"
            user_preferences = root / "user_preferences_final.json"
            search_scope = root / "search_scope.json"

            for path in [user_profile, user_preferences, search_scope]:
                path.write_text("{}", encoding="utf-8")

            with patch.object(main_module, "USER_PROFILE_FILE", user_profile), \
                patch.object(main_module, "USER_PREFERENCES_FILE", user_preferences), \
                patch.object(main_module, "SEARCH_SCOPE_FILE", search_scope):
                main_module.validate_required_planning_inputs()

    def test_changing_user_preferences_changes_target_career_path_cache_key(self):
        profile = make_profile()

        first_key = _build_target_career_path_cache_key(
            user_profile=profile,
            user_preferences={"preferred_industries": ["technology"]},
            model="model-a",
        )
        second_key = _build_target_career_path_cache_key(
            user_profile=profile,
            user_preferences={"preferred_industries": ["healthcare"]},
            model="model-a",
        )

        self.assertNotEqual(first_key, second_key)

    def test_main_validates_required_inputs_before_pipeline_runs(self):
        main_module = importlib.import_module("src.main")

        with patch.object(main_module, "validate_required_planning_inputs") as validate_mock, \
            patch.object(main_module, "get_database_path", return_value=Path("test.db")), \
            patch.object(main_module, "initialize_database"), \
            patch.object(main_module, "SourceItemRepository"), \
            patch.object(main_module, "CareerSignalRepository"), \
            patch.object(main_module, "BraveSearchClient"), \
            patch.object(main_module, "RSSClient"), \
            patch.object(main_module, "SelectedWebsiteClient"), \
            patch.object(main_module, "AIFilterClient"), \
            patch.object(main_module, "load_user_preferences_from_json", return_value={"ok": True}), \
            patch.object(main_module, "MockPipeline") as pipeline_mock, \
            patch.object(main_module, "ensure_project_directories"), \
            patch.object(main_module, "save_json", return_value=Path("out.json")), \
            redirect_stdout(io.StringIO()):
            pipeline_mock.return_value.run.return_value = _fake_pipeline_output()
            main_module.main()

        validate_mock.assert_called_once_with()
        pipeline_mock.return_value.run.assert_called_once_with()


def _fake_pipeline_output():
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


if __name__ == "__main__":
    unittest.main()
