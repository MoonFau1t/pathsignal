import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from src.models import CareerPathCategory, TargetCareerPath
from src.search_api_client import BraveSearchClient
from src.search_plan_builder import build_search_plans
from src.search_query_generator import generate_search_queries
from src.source_monitoring.information_need_generator import (
    InformationNeedClient,
    InformationNeedGenerationError,
    generate_information_needs,
)
from src.source_monitoring.monitoring_objectives import get_monitoring_objectives
from src.source_monitoring.models import MonitoringObjectiveDefinition
from src.storage import save_json


def make_path(path_id: str = "venture_capital") -> TargetCareerPath:
    return TargetCareerPath(
        path_id=path_id,
        title="Venture Capital Analyst",
        category=CareerPathCategory.VENTURE_CAPITAL,
        description="Early-career venture capital research and investing path.",
        fit_score=86.0,
        rationale=["Strong interest in startups and investment analysis."],
        keywords=["venture capital", "startup", "AI"],
        suggested_roles=["VC analyst"],
        search_seed_terms=["venture capital analyst"],
        metadata={"path_type": "core_match"},
    )


def valid_payload(**overrides):
    need = {
        "need_key": "junior_vc_hiring_requirements",
        "objective_code": "career_path",
        "title": "Junior VC hiring requirements",
        "description": (
            "Monitor requirements, candidate backgrounds, skills, seniority "
            "patterns, and entry routes for junior venture roles."
        ),
        "related_target_career_path_ids": ["venture_capital"],
        "signal_examples": [
            "Role descriptions mentioning investment analysis",
            "Public descriptions of junior investor responsibilities",
        ],
        "rationale": "This helps assess preparation for early-career VC paths.",
        "priority": "high",
        "confidence": 0.88,
    }
    need.update(overrides)
    return {"information_needs": [need]}


class FakeInformationNeedClient:
    def __init__(self, payload=None, error=None):
        self.provider = "fake-provider"
        self.model = "fake-model"
        self.payload = payload or valid_payload()
        self.error = error
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs

        if self.error is not None:
            raise self.error

        return self.payload


class InformationNeedGenerationFlowTests(unittest.TestCase):
    def test_valid_llm_expansion_generates_result(self):
        client = FakeInformationNeedClient()
        result = generate_information_needs(
            target_career_paths=[make_path()],
            user_preferences={"preferred_industries": ["AI"]},
            client=client,
            cache_enabled=False,
        )

        self.assertEqual(client.calls, 1)
        self.assertEqual(result.generation_mode, "generated")
        self.assertEqual(len(result.monitoring_objectives), 4)
        self.assertEqual(len(result.information_needs), 1)
        self.assertEqual(result.rejected_suggestions, ())
        self.assertTrue(result.input_fingerprint)
        self.assertTrue(result.output_hash)

    def test_empty_output_is_diagnosed(self):
        result = generate_information_needs(
            target_career_paths=[make_path()],
            user_preferences={},
            client=FakeInformationNeedClient({"information_needs": []}),
            cache_enabled=False,
        )

        self.assertEqual(result.information_needs, ())
        self.assertEqual(result.generation_mode, "generated")

    def test_provider_timeout_returns_unavailable_result(self):
        result = generate_information_needs(
            target_career_paths=[make_path()],
            user_preferences={},
            client=FakeInformationNeedClient(error=TimeoutError("timed out")),
            cache_enabled=False,
        )

        self.assertEqual(result.generation_mode, "unavailable")
        self.assertIn("TimeoutError", result.diagnostics[0])

    def test_provider_error_returns_unavailable_result(self):
        result = generate_information_needs(
            target_career_paths=[make_path()],
            user_preferences={},
            client=FakeInformationNeedClient(error=RuntimeError("provider down")),
            cache_enabled=False,
        )

        self.assertEqual(result.generation_mode, "unavailable")
        self.assertIn("provider down", result.diagnostics[0])

    def test_excessive_result_count_is_diagnosed_and_limited(self):
        payload = {
            "information_needs": [
                valid_payload(need_key=f"need_{index:02d}")["information_needs"][0]
                for index in range(5)
            ]
        }
        result = generate_information_needs(
            target_career_paths=[make_path()],
            user_preferences={},
            client=FakeInformationNeedClient(payload),
            cache_enabled=False,
            max_total=2,
            max_per_path_objective=10,
        )

        self.assertEqual(len(result.information_needs), 2)
        self.assertEqual(len(result.rejected_suggestions), 3)
        self.assertTrue(any("max_total" in item for item in result.diagnostics))

    def test_generation_limits_are_passed_to_client(self):
        client = FakeInformationNeedClient()

        generate_information_needs(
            target_career_paths=[make_path()],
            user_preferences={},
            client=client,
            cache_enabled=False,
            max_total=7,
            max_per_path_objective=3,
            max_signal_examples=2,
        )

        self.assertEqual(client.last_kwargs["max_total"], 7)
        self.assertEqual(client.last_kwargs["max_per_path_objective"], 3)
        self.assertEqual(client.last_kwargs["max_signal_examples"], 2)

    def test_prompt_receives_user_preferences_context(self):
        client = FakeInformationNeedClient()
        preferences = {
            "career_status": {"target_experience_years": "0-3"},
            "stretch_paths": {"three_to_five_years": "keep_if_highly_matched"},
            "excluded_industries": [
                "low-value process outsourcing",
                "acquisition-focused cross-border e-commerce",
            ],
            "preferred_themes": [
                "AI with business, industry, and organizational implementation"
            ],
        }

        result = generate_information_needs(
            target_career_paths=[make_path()],
            user_preferences=preferences,
            client=client,
            cache_enabled=False,
        )

        self.assertEqual(len(result.information_needs), 1)
        self.assertEqual(client.last_kwargs["user_preferences"], preferences)


class InformationNeedClientParsingTests(unittest.TestCase):
    def make_response(self, content):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content))
            ]
        )

    def test_live_client_uses_openai_compatible_json_object_output(self):
        with patch(
            "src.source_monitoring.information_need_generator.OpenAI"
        ) as openai_factory:
            create = openai_factory.return_value.chat.completions.create
            create.return_value = self.make_response(json.dumps(valid_payload()))
            client = InformationNeedClient(
                provider="deepseek",
                api_key="real-enough-for-test",
                base_url="https://api.deepseek.com",
                model="model-a",
                temperature=0.2,
            )

            parsed = client.generate(
                target_career_paths=[make_path()],
                user_preferences={},
                monitoring_objectives=get_monitoring_objectives(),
                max_per_path_objective=4,
                max_total=120,
                max_signal_examples=5,
            )

        self.assertIn("information_needs", parsed)
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(kwargs["temperature"], 0.2)

    def test_malformed_json_raises_generation_error(self):
        with patch(
            "src.source_monitoring.information_need_generator.OpenAI"
        ) as openai_factory:
            openai_factory.return_value.chat.completions.create.return_value = (
                self.make_response("{not-json")
            )
            client = InformationNeedClient(
                provider="deepseek",
                api_key="real-enough-for-test",
                base_url="https://api.deepseek.com",
                model="model-a",
            )

            with self.assertRaises(InformationNeedGenerationError):
                client.generate(
                    target_career_paths=[make_path()],
                    user_preferences={},
                    monitoring_objectives=get_monitoring_objectives(),
                    max_per_path_objective=4,
                    max_total=120,
                    max_signal_examples=5,
                )

    def test_empty_llm_response_raises_generation_error(self):
        with patch(
            "src.source_monitoring.information_need_generator.OpenAI"
        ) as openai_factory:
            openai_factory.return_value.chat.completions.create.return_value = (
                self.make_response("")
            )
            client = InformationNeedClient(
                provider="deepseek",
                api_key="real-enough-for-test",
                base_url="https://api.deepseek.com",
                model="model-a",
            )

            with self.assertRaises(InformationNeedGenerationError):
                client.generate(
                    target_career_paths=[make_path()],
                    user_preferences={},
                    monitoring_objectives=get_monitoring_objectives(),
                    max_per_path_objective=4,
                    max_total=120,
                    max_signal_examples=5,
                )

    def test_non_object_llm_response_raises_generation_error(self):
        with patch(
            "src.source_monitoring.information_need_generator.OpenAI"
        ) as openai_factory:
            openai_factory.return_value.chat.completions.create.return_value = (
                self.make_response("[1, 2]")
            )
            client = InformationNeedClient(
                provider="deepseek",
                api_key="real-enough-for-test",
                base_url="https://api.deepseek.com",
                model="model-a",
            )

            with self.assertRaises(InformationNeedGenerationError):
                client.generate(
                    target_career_paths=[make_path()],
                    user_preferences={},
                    monitoring_objectives=get_monitoring_objectives(),
                    max_per_path_objective=4,
                    max_total=120,
                    max_signal_examples=5,
                )


class CacheAndReuseTests(unittest.TestCase):
    def test_cache_hit_avoids_second_llm_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "information_needs.json"
            client = FakeInformationNeedClient()

            first = generate_information_needs(
                target_career_paths=[make_path()],
                user_preferences={"preferred_industries": ["AI"]},
                client=client,
                cache_file=cache_file,
            )
            second = generate_information_needs(
                target_career_paths=[make_path()],
                user_preferences={"preferred_industries": ["AI"]},
                client=client,
                cache_file=cache_file,
            )

        self.assertEqual(client.calls, 1)
        self.assertEqual(first.input_fingerprint, second.input_fingerprint)
        self.assertEqual(second.generation_mode, "loaded_from_cache")

    def test_force_refresh_bypasses_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "information_needs.json"
            client = FakeInformationNeedClient()

            generate_information_needs(
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )
            generate_information_needs(
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
                force_refresh=True,
            )

        self.assertEqual(client.calls, 2)

    def test_changed_target_career_paths_miss_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "information_needs.json"
            client = FakeInformationNeedClient()

            first = generate_information_needs(
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )
            second = generate_information_needs(
                target_career_paths=[make_path("ai_strategy")],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )

        self.assertEqual(client.calls, 2)
        self.assertNotEqual(first.input_fingerprint, second.input_fingerprint)

    def test_changed_user_preferences_miss_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "information_needs.json"
            client = FakeInformationNeedClient()

            generate_information_needs(
                target_career_paths=[make_path()],
                user_preferences={"preferred": ["AI"]},
                client=client,
                cache_file=cache_file,
            )
            generate_information_needs(
                target_career_paths=[make_path()],
                user_preferences={"preferred": ["Healthcare"]},
                client=client,
                cache_file=cache_file,
            )

        self.assertEqual(client.calls, 2)

    def test_changed_prompt_version_misses_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "information_needs.json"
            client = FakeInformationNeedClient()

            with patch(
                "src.source_monitoring.information_need_generator.INFORMATION_NEED_PROMPT_VERSION",
                "prompt_a",
            ):
                generate_information_needs(
                    target_career_paths=[make_path()],
                    user_preferences={},
                    client=client,
                    cache_file=cache_file,
                )
            with patch(
                "src.source_monitoring.information_need_generator.INFORMATION_NEED_PROMPT_VERSION",
                "prompt_b",
            ):
                generate_information_needs(
                    target_career_paths=[make_path()],
                    user_preferences={},
                    client=client,
                    cache_file=cache_file,
                )

        self.assertEqual(client.calls, 2)

    def test_changed_ontology_version_misses_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "information_needs.json"
            client = FakeInformationNeedClient()
            objectives = get_monitoring_objectives()
            changed_objectives = tuple(
                MonitoringObjectiveDefinition(
                    code=objective.code,
                    label=objective.label,
                    description=objective.description,
                    supported_signal_examples=objective.supported_signal_examples,
                    schema_version="monitoring_objective_v2",
                )
                for objective in objectives
            )

            generate_information_needs(
                target_career_paths=[make_path()],
                user_preferences={},
                monitoring_objectives=objectives,
                client=client,
                cache_file=cache_file,
            )
            generate_information_needs(
                target_career_paths=[make_path()],
                user_preferences={},
                monitoring_objectives=changed_objectives,
                client=client,
                cache_file=cache_file,
            )

        self.assertEqual(client.calls, 2)

    def test_malformed_cache_data_is_diagnosed_and_not_trusted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "information_needs.json"
            client = FakeInformationNeedClient()
            first = generate_information_needs(
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )
            save_json(
                {
                    "input_fingerprint": first.input_fingerprint,
                    "output_hash": first.output_hash,
                    "information_needs": ["bad"],
                },
                cache_file,
            )
            second = generate_information_needs(
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )

        self.assertEqual(client.calls, 2)
        self.assertEqual(second.generation_mode, "generated")
        self.assertTrue(any("cache" in item.lower() for item in second.diagnostics))


class RegressionBoundaryTests(unittest.TestCase):
    def test_phase0_does_not_change_search_query_or_plan_generation(self):
        path = make_path()
        queries_before = generate_search_queries([path])
        plans_before = build_search_plans(
            queries_before,
            _search_scope(),
        )

        generate_information_needs(
            target_career_paths=[path],
            user_preferences={},
            client=FakeInformationNeedClient(),
            cache_enabled=False,
        )

        self.assertEqual(
            [query.to_dict() for query in generate_search_queries([path])],
            [query.to_dict() for query in queries_before],
        )
        self.assertEqual(
            [plan.to_dict() for plan in build_search_plans(queries_before, _search_scope())],
            [plan.to_dict() for plan in plans_before],
        )

    def test_phase0_does_not_invoke_brave_search(self):
        client = BraveSearchClient(api_key="", dry_run=True)

        generate_information_needs(
            target_career_paths=[make_path()],
            user_preferences={},
            client=FakeInformationNeedClient(),
            cache_enabled=False,
        )

        self.assertEqual(client.last_result_diagnostics, [])

    def test_phase0_does_not_touch_database_migrations(self):
        migration_dir = Path("src/database/sql")
        migration_names = sorted(path.name for path in migration_dir.glob("*.sql"))

        generate_information_needs(
            target_career_paths=[make_path()],
            user_preferences={},
            client=FakeInformationNeedClient(),
            cache_enabled=False,
        )

        self.assertEqual(
            sorted(path.name for path in migration_dir.glob("*.sql")),
            migration_names,
        )

    def test_phase0_public_service_does_not_require_live_llm_key_with_stub(self):
        result = generate_information_needs(
            target_career_paths=[make_path()],
            user_preferences={},
            client=FakeInformationNeedClient(),
            cache_enabled=False,
        )

        self.assertEqual(result.llm_execution_metadata.provider, "fake-provider")


def _search_scope():
    from src.models import SearchScope, SourceType

    return SearchScope(
        scope_id="scope",
        name="Scope",
        source_types=[SourceType.SEARCH_API],
        max_results_per_query=3,
    )


if __name__ == "__main__":
    unittest.main()
