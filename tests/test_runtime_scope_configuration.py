import importlib
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


CONFIG_MODULES = (
    "scripts.run_phase5c_source_inspection_validation",
    "scripts.run_phase5d_initial_evaluation_validation",
    "src.main",
    "src.config",
)


def reload_runtime_modules(*, env_values, skip_dotenv=True):
    for module_name in CONFIG_MODULES:
        sys.modules.pop(module_name, None)
    with patch.dict(os.environ, env_values, clear=True):
        if skip_dotenv:
            with patch("dotenv.load_dotenv", return_value=True):
                config = importlib.import_module("src.config")
                main = importlib.import_module("src.main")
        else:
            config = importlib.import_module("src.config")
            main = importlib.import_module("src.main")
    return config, main


class RuntimeScopeConfigurationTests(unittest.TestCase):
    def tearDown(self):
        for module_name in CONFIG_MODULES:
            sys.modules.pop(module_name, None)

    def test_search_broad_run_values_are_resolved_by_src_main(self):
        config, main = reload_runtime_modules(
            env_values={
                "SEARCH_API_MAX_PLANS": "8",
                "SEARCH_API_PLAN_OFFSET": "8",
            }
        )

        self.assertEqual(config.SEARCH_API_MAX_PLANS, 8)
        self.assertEqual(config.SEARCH_API_PLAN_OFFSET, 8)
        self.assertEqual(main.SEARCH_API_MAX_PLANS, 8)
        self.assertEqual(main.SEARCH_API_PLAN_OFFSET, 8)
        self.assertFalse(hasattr(config, "AI_FILTER_MAX_ITEMS"))
        self.assertFalse(hasattr(main, "AI_FILTER_MAX_ITEMS"))

    def test_search_defaults_remain_intact_when_overrides_are_omitted(self):
        config, main = reload_runtime_modules(env_values={})

        self.assertEqual(config.SEARCH_API_MAX_PLANS, 5)
        self.assertEqual(config.SEARCH_API_PLAN_OFFSET, 0)
        self.assertEqual(main.SEARCH_API_MAX_PLANS, 5)
        self.assertEqual(main.SEARCH_API_PLAN_OFFSET, 0)
        self.assertFalse(hasattr(config, "AI_FILTER_MAX_ITEMS"))
        self.assertFalse(hasattr(main, "AI_FILTER_MAX_ITEMS"))

    def test_source_inspection_broad_run_corpus_value_is_configurable(self):
        config, _ = reload_runtime_modules(env_values={"SOURCE_INSPECTION_MAX_CANDIDATES": "100"})
        phase5c = importlib.import_module("scripts.run_phase5c_source_inspection_validation")
        inspections = tuple(
            (SimpleNamespace(candidate_source_id=f"candidate_{index:03d}"), SimpleNamespace(value="accepted"))
            for index in range(105)
        )

        self.assertEqual(config.SOURCE_INSPECTION_MAX_CANDIDATES, 100)
        self.assertEqual(
            len(phase5c.select_source_inspection_corpus(inspections)),
            100,
        )

    def test_source_inspection_default_corpus_value_remains_twenty(self):
        config, _ = reload_runtime_modules(env_values={})
        phase5c = importlib.import_module("scripts.run_phase5c_source_inspection_validation")
        inspections = tuple(
            (SimpleNamespace(candidate_source_id=f"candidate_{index:03d}"), SimpleNamespace(value="accepted"))
            for index in range(25)
        )

        self.assertEqual(config.SOURCE_INSPECTION_MAX_CANDIDATES, 20)
        self.assertEqual(
            len(phase5c.select_source_inspection_corpus(inspections)),
            20,
        )

    def test_source_inspection_fetch_corpus_processes_all_selected_items_in_existing_batches(self):
        _config, _ = reload_runtime_modules(env_values={})
        phase5c = importlib.import_module("scripts.run_phase5c_source_inspection_validation")
        fetcher = FakeBatchFetcher(batch_size=6)

        outcomes = phase5c.execute_source_fetch_corpus(
            requests_to_execute=tuple(range(14)),
            fetcher=fetcher,
        )

        self.assertEqual(fetcher.batch_sizes, [6, 6, 2])
        self.assertEqual(len(outcomes), 14)


class FakeBatchFetcher:
    def __init__(self, *, batch_size):
        self.policy = SimpleNamespace(batch_size=batch_size)
        self.batch_sizes = []

    def fetch_many(self, items):
        self.batch_sizes.append(len(items))
        return tuple(SimpleNamespace(execution=item) for item in items)


if __name__ == "__main__":
    unittest.main()
