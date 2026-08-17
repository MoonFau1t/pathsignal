import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from src.models import CareerPathCategory, SearchScope, SourceType, TargetCareerPath
from src.search_plan_builder import build_search_plans
from src.search_query_generator import generate_search_queries
from src.source_monitoring.cache import (
    load_cached_entity_type_expansion_result,
)
from src.source_monitoring.entity_type_expander import (
    EntityTypeExpansionClient,
    EntityTypeExpansionError,
    expand_entity_types,
)
from src.source_monitoring.entity_type_ontology import (
    ENTITY_TYPE_ONTOLOGY_VERSION,
    get_entity_type_ontology,
)
from src.source_monitoring.identity import (
    build_entity_type_candidate_id,
    build_entity_type_expansion_input_fingerprint,
    build_entity_type_expansion_output_hash,
)
from src.source_monitoring.models import (
    ENTITY_TYPE_CANDIDATE_SCHEMA_VERSION,
    EntityTypeCandidate,
    InformationNeed,
    InformationNeedPriority,
    LLMExecutionMetadata,
    MonitoringObjectiveCode,
    ProposedEntityType,
)
from src.source_monitoring.monitoring_objectives import get_monitoring_objectives
from src.source_monitoring.validators import (
    parse_entity_type_expansion_suggestions,
    validate_normalize_and_deduplicate_entity_type_expansion,
)
from src.storage import save_json


def make_path(path_id: str = "venture_capital_analyst") -> TargetCareerPath:
    return TargetCareerPath(
        path_id=path_id,
        title=path_id.replace("_", " ").title(),
        category=CareerPathCategory.VENTURE_CAPITAL,
        description="Relevant target career path.",
        fit_score=88.0,
        rationale=["Good fit."],
        keywords=["AI", "strategy"],
        suggested_roles=["Analyst"],
        search_seed_terms=["analyst"],
    )


def make_need(
    need_id: str = "need_ai_investment",
    *,
    objective_code: MonitoringObjectiveCode = MonitoringObjectiveCode.INDUSTRY,
    path_ids: tuple[str, ...] = ("venture_capital_analyst",),
) -> InformationNeed:
    return InformationNeed(
        information_need_id=need_id,
        need_key=need_id.removeprefix("need_"),
        objective_code=objective_code,
        title="AI investment and funding trends",
        description="Monitor generic capital flows into AI technologies and markets.",
        related_target_career_path_ids=path_ids,
        signal_examples=("Funding trend reports", "Investor thesis updates"),
        rationale="This helps evaluate market demand and role preparation.",
        priority=InformationNeedPriority.HIGH,
        confidence=0.9,
    )


def valid_candidate(**overrides):
    payload = {
        "entity_type_code": "venture_capital_firm",
        "related_information_need_ids": ["need_ai_investment"],
        "rationale": (
            "Venture capital firms publish investment theses, fundraising "
            "signals, and portfolio-market analysis."
        ),
        "discovery_terms": [
            "early-stage AI investors",
            "APAC technology venture funds",
        ],
        "confidence": 0.91,
    }
    payload.update(overrides)
    return payload


def valid_proposed(**overrides):
    payload = {
        "proposed_code": "university_venture_center",
        "display_name": "University Venture Center",
        "definition": "A university-linked organization supporting venture creation.",
        "broader_group": "knowledge_institution",
        "supporting_information_need_ids": ["need_ai_investment"],
        "closest_canonical_type_codes": ["startup_accelerator"],
        "why_canonical_types_are_insufficient": (
            "It is educational and venture-linked but not always an accelerator."
        ),
        "rationale": "It may publish fellowship and startup ecosystem signals.",
        "confidence": 0.72,
    }
    payload.update(overrides)
    return payload


class FakeEntityTypeExpansionClient:
    def __init__(self, payload=None, error=None):
        self.provider = "fake-provider"
        self.model = "fake-model"
        self.payload = payload or {
            "entity_type_candidates": [valid_candidate()],
            "proposed_new_types": [],
        }
        self.error = error
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs

        if self.error is not None:
            raise self.error

        return self.payload


def run_validation(candidate_suggestions, proposed_type_suggestions=()):
    return validate_normalize_and_deduplicate_entity_type_expansion(
        candidate_suggestions=list(candidate_suggestions),
        proposed_type_suggestions=list(proposed_type_suggestions),
        information_needs=(make_need(),),
        target_career_paths=[make_path()],
        ontology=get_entity_type_ontology(),
        llm_metadata=LLMExecutionMetadata(
            provider="fake",
            model="fake-model",
            prompt_version="entity_type_expansion_prompt_v1",
            schema_version=ENTITY_TYPE_CANDIDATE_SCHEMA_VERSION,
            input_fingerprint="fingerprint",
        ),
        input_fingerprint="fingerprint",
        max_canonical_candidates=25,
        max_proposed_types=5,
        max_types_per_need=5,
        max_discovery_terms=8,
    )


class EntityTypeExpansionFlowTests(unittest.TestCase):
    def test_valid_expansion_generates_result(self):
        client = FakeEntityTypeExpansionClient()
        result = expand_entity_types(
            information_needs=(make_need(),),
            target_career_paths=[make_path()],
            user_preferences={"preferred_industries": ["AI"]},
            client=client,
            cache_enabled=False,
        )

        self.assertEqual(client.calls, 1)
        self.assertEqual(result.generation_mode, "generated")
        self.assertEqual(len(result.canonical_entity_types), 21)
        self.assertEqual(len(result.canonical_candidates), 1)
        self.assertEqual(result.proposed_new_types, ())
        self.assertEqual(result.rejected_suggestions, ())
        self.assertEqual(result.uncovered_information_need_ids, ())
        self.assertTrue(result.input_fingerprint)
        self.assertTrue(result.output_hash)

    def test_provider_timeout_returns_unavailable_result_without_fake_success(self):
        result = expand_entity_types(
            information_needs=(make_need(),),
            target_career_paths=[make_path()],
            user_preferences={},
            client=FakeEntityTypeExpansionClient(error=TimeoutError("timed out")),
            cache_enabled=False,
        )

        self.assertEqual(result.generation_mode, "unavailable")
        self.assertEqual(result.canonical_candidates, ())
        self.assertEqual(result.uncovered_information_need_ids, ("need_ai_investment",))
        self.assertIn("TimeoutError", result.diagnostics[0])

    def test_generation_limits_are_passed_to_client(self):
        client = FakeEntityTypeExpansionClient()

        expand_entity_types(
            information_needs=(make_need(),),
            target_career_paths=[make_path()],
            user_preferences={},
            client=client,
            cache_enabled=False,
            max_canonical_candidates=7,
            max_proposed_types=2,
            max_types_per_need=3,
            max_discovery_terms=4,
        )

        self.assertEqual(client.last_kwargs["max_canonical_candidates"], 7)
        self.assertEqual(client.last_kwargs["max_proposed_types"], 2)
        self.assertEqual(client.last_kwargs["max_types_per_need"], 3)
        self.assertEqual(client.last_kwargs["max_discovery_terms"], 4)

    def test_output_containing_only_proposed_types_is_valid(self):
        result = expand_entity_types(
            information_needs=(make_need(),),
            target_career_paths=[make_path()],
            user_preferences={},
            client=FakeEntityTypeExpansionClient(
                {"entity_type_candidates": [], "proposed_new_types": [valid_proposed()]}
            ),
            cache_enabled=False,
        )

        self.assertEqual(result.canonical_candidates, ())
        self.assertEqual(len(result.proposed_new_types), 1)
        self.assertEqual(result.uncovered_information_need_ids, ())


class EntityTypeExpansionClientParsingTests(unittest.TestCase):
    def make_response(self, content):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content))
            ]
        )

    def test_live_client_uses_openai_compatible_json_object_output(self):
        with patch(
            "src.source_monitoring.entity_type_expander.OpenAI"
        ) as openai_factory:
            create = openai_factory.return_value.chat.completions.create
            create.return_value = self.make_response(
                json.dumps({"entity_type_candidates": [valid_candidate()]})
            )
            client = EntityTypeExpansionClient(
                provider="deepseek",
                api_key="real-enough-for-test",
                base_url="https://api.deepseek.com",
                model="model-a",
                temperature=0.2,
            )

            parsed = client.generate(
                monitoring_objectives=get_monitoring_objectives(),
                information_needs=(make_need(),),
                target_career_paths=[make_path()],
                user_preferences={},
                entity_type_ontology=get_entity_type_ontology(),
                max_canonical_candidates=25,
                max_proposed_types=5,
                max_types_per_need=5,
                max_discovery_terms=8,
            )

        self.assertIn("entity_type_candidates", parsed)
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(kwargs["temperature"], 0.2)

    def test_malformed_json_raises_generation_error(self):
        with patch(
            "src.source_monitoring.entity_type_expander.OpenAI"
        ) as openai_factory:
            openai_factory.return_value.chat.completions.create.return_value = (
                self.make_response("{not-json")
            )
            client = EntityTypeExpansionClient(
                provider="deepseek",
                api_key="real-enough-for-test",
                base_url="https://api.deepseek.com",
                model="model-a",
            )

            with self.assertRaises(EntityTypeExpansionError):
                client.generate(
                    monitoring_objectives=get_monitoring_objectives(),
                    information_needs=(make_need(),),
                    target_career_paths=[make_path()],
                    user_preferences={},
                    entity_type_ontology=get_entity_type_ontology(),
                    max_canonical_candidates=25,
                    max_proposed_types=5,
                    max_types_per_need=5,
                    max_discovery_terms=8,
                )

    def test_empty_response_raises_generation_error(self):
        with patch(
            "src.source_monitoring.entity_type_expander.OpenAI"
        ) as openai_factory:
            openai_factory.return_value.chat.completions.create.return_value = (
                self.make_response("")
            )
            client = EntityTypeExpansionClient(
                provider="deepseek",
                api_key="real-enough-for-test",
                base_url="https://api.deepseek.com",
                model="model-a",
            )

            with self.assertRaises(EntityTypeExpansionError):
                client.generate(
                    monitoring_objectives=get_monitoring_objectives(),
                    information_needs=(make_need(),),
                    target_career_paths=[make_path()],
                    user_preferences={},
                    entity_type_ontology=get_entity_type_ontology(),
                    max_canonical_candidates=25,
                    max_proposed_types=5,
                    max_types_per_need=5,
                    max_discovery_terms=8,
                )

    def test_non_object_response_raises_generation_error(self):
        with patch(
            "src.source_monitoring.entity_type_expander.OpenAI"
        ) as openai_factory:
            openai_factory.return_value.chat.completions.create.return_value = (
                self.make_response("[1, 2]")
            )
            client = EntityTypeExpansionClient(
                provider="deepseek",
                api_key="real-enough-for-test",
                base_url="https://api.deepseek.com",
                model="model-a",
            )

            with self.assertRaises(EntityTypeExpansionError):
                client.generate(
                    monitoring_objectives=get_monitoring_objectives(),
                    information_needs=(make_need(),),
                    target_career_paths=[make_path()],
                    user_preferences={},
                    entity_type_ontology=get_entity_type_ontology(),
                    max_canonical_candidates=25,
                    max_proposed_types=5,
                    max_types_per_need=5,
                    max_discovery_terms=8,
                )


class EntityTypeParsingTests(unittest.TestCase):
    def test_valid_strict_json_extracts_suggestions(self):
        candidates, proposed, diagnostics = parse_entity_type_expansion_suggestions(
            {
                "entity_type_candidates": [valid_candidate()],
                "proposed_new_types": [valid_proposed()],
            }
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(proposed), 1)
        self.assertEqual(diagnostics, [])

    def test_non_object_top_level_response_is_rejected(self):
        candidates, proposed, diagnostics = parse_entity_type_expansion_suggestions([])

        self.assertEqual(candidates, [])
        self.assertEqual(proposed, [])
        self.assertIn("JSON object", diagnostics[0])

    def test_missing_lists_are_treated_as_empty_output(self):
        candidates, proposed, diagnostics = parse_entity_type_expansion_suggestions({})

        self.assertEqual(candidates, [])
        self.assertEqual(proposed, [])
        self.assertEqual(diagnostics, [])

    def test_unsupported_top_level_fields_are_diagnosed(self):
        _, _, diagnostics = parse_entity_type_expansion_suggestions(
            {"entity_type_candidates": [], "entities": []}
        )

        self.assertIn("Unexpected top-level field", diagnostics[0])


class EntityTypeValidationAndResolutionTests(unittest.TestCase):
    def test_cvc_vc_and_fa_aliases_resolve_to_canonical_candidates(self):
        candidates, proposed, rejected, uncovered, _ = run_validation(
            [
                valid_candidate(entity_type_code="CVC"),
                valid_candidate(entity_type_code="VC firm"),
                valid_candidate(entity_type_code="FA"),
            ]
        )

        self.assertEqual(proposed, ())
        self.assertEqual(rejected, ())
        self.assertEqual(uncovered, ())
        self.assertEqual(
            [candidate.entity_type_code for candidate in candidates],
            [
                "boutique_investment_bank_or_fa",
                "corporate_venture_capital",
                "venture_capital_firm",
            ],
        )

    def test_chinese_alias_candidate_resolves_to_canonical_candidate(self):
        candidates, proposed, rejected, uncovered, _ = run_validation(
            [
                valid_candidate(
                    entity_type_code="风险投资机构",
                    discovery_terms=["venture capital firms", "风险投资机构"],
                )
            ]
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].entity_type_code, "venture_capital_firm")
        self.assertEqual(
            candidates[0].discovery_terms,
            ("venture capital firms", "风险投资机构"),
        )
        self.assertEqual(proposed, ())
        self.assertEqual(rejected, ())
        self.assertEqual(uncovered, ())

    def test_unknown_chinese_phrase_is_not_promoted_to_proposed_type(self):
        candidates, proposed, rejected, uncovered, _ = run_validation(
            [valid_candidate(entity_type_code="投资机构")]
        )

        self.assertEqual(candidates, ())
        self.assertEqual(proposed, ())
        self.assertEqual(len(rejected), 1)
        self.assertIn("entity_type_code", rejected[0].reason)
        self.assertEqual(uncovered, ("need_ai_investment",))

    def test_missing_information_need_id_is_rejected(self):
        _, _, rejected, uncovered, _ = run_validation(
            [valid_candidate(related_information_need_ids=["missing"])]
        )

        self.assertEqual(len(rejected), 1)
        self.assertIn("unknown InformationNeed", rejected[0].reason)
        self.assertEqual(uncovered, ("need_ai_investment",))

    def test_invalid_confidence_is_rejected(self):
        _, _, rejected, _, _ = run_validation([valid_candidate(confidence=1.5)])

        self.assertEqual(len(rejected), 1)
        self.assertIn("confidence", rejected[0].reason)

    def test_invalid_type_code_is_rejected(self):
        _, _, rejected, _, _ = run_validation([valid_candidate(entity_type_code="??")])

        self.assertEqual(len(rejected), 1)
        self.assertIn("entity_type_code", rejected[0].reason)

    def test_unsupported_extra_fields_are_rejected(self):
        _, _, rejected, _, _ = run_validation([valid_candidate(url="https://example.com")])

        self.assertEqual(len(rejected), 1)
        self.assertIn("unsupported fields", rejected[0].reason)

    def test_unknown_valid_type_becomes_proposed_not_canonical(self):
        candidates, proposed, rejected, uncovered, _ = run_validation(
            [valid_candidate(entity_type_code="university_venture_center")]
        )

        self.assertEqual(candidates, ())
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0].proposed_code, "university_venture_center")
        self.assertEqual(rejected, ())
        self.assertEqual(uncovered, ())

    def test_duplicate_canonical_candidates_merge(self):
        candidates, _, _, _, _ = run_validation(
            [
                valid_candidate(discovery_terms=["early-stage AI investors"], confidence=0.8),
                valid_candidate(
                    entity_type_code="venture fund",
                    discovery_terms=["APAC technology venture funds"],
                    confidence=1.0,
                ),
            ]
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].entity_type_code, "venture_capital_firm")
        self.assertEqual(
            candidates[0].discovery_terms,
            ("APAC technology venture funds", "early-stage AI investors"),
        )
        self.assertEqual(candidates[0].confidence, 0.9)

    def test_relationships_are_derived_from_information_needs(self):
        needs = (
            make_need(path_ids=("venture_capital_analyst", "ai_strategy_analyst")),
            make_need(
                "need_role_skills",
                objective_code=MonitoringObjectiveCode.CAREER_PATH,
                path_ids=("ai_strategy_analyst",),
            ),
        )
        candidates, _, _, _, _ = validate_normalize_and_deduplicate_entity_type_expansion(
            candidate_suggestions=[
                valid_candidate(
                    related_information_need_ids=[
                        "need_ai_investment",
                        "need_role_skills",
                    ]
                )
            ],
            proposed_type_suggestions=[],
            information_needs=needs,
            target_career_paths=[make_path(), make_path("ai_strategy_analyst")],
            ontology=get_entity_type_ontology(),
            llm_metadata=LLMExecutionMetadata(provider="fake", model="fake"),
            input_fingerprint="fingerprint",
            max_canonical_candidates=25,
            max_proposed_types=5,
            max_types_per_need=5,
            max_discovery_terms=8,
        )

        self.assertEqual(
            candidates[0].related_target_career_path_ids,
            ("venture_capital_analyst", "ai_strategy_analyst"),
        )
        self.assertEqual(
            candidates[0].supported_monitoring_objectives,
            (MonitoringObjectiveCode.INDUSTRY, MonitoringObjectiveCode.CAREER_PATH),
        )

    def test_input_information_needs_are_not_mutated(self):
        need = make_need()
        before = need.to_dict()

        run_validation([valid_candidate()])

        self.assertEqual(need.to_dict(), before)


class EntityTypeCoverageAndBoundaryTests(unittest.TestCase):
    def test_uncovered_information_need_is_reported(self):
        needs = (
            make_need(),
            make_need("need_role_skills", objective_code=MonitoringObjectiveCode.CAREER_PATH),
        )
        _, _, _, uncovered, diagnostics = validate_normalize_and_deduplicate_entity_type_expansion(
            candidate_suggestions=[valid_candidate()],
            proposed_type_suggestions=[],
            information_needs=needs,
            target_career_paths=[make_path()],
            ontology=get_entity_type_ontology(),
            llm_metadata=LLMExecutionMetadata(provider="fake", model="fake"),
            input_fingerprint="fingerprint",
            max_canonical_candidates=25,
            max_proposed_types=5,
            max_types_per_need=5,
            max_discovery_terms=8,
        )

        self.assertEqual(uncovered, ("need_role_skills",))
        self.assertTrue(any("not covered" in item for item in diagnostics))

    def test_high_priority_need_truncated_by_limit_is_reported(self):
        candidates, _, rejected, uncovered, diagnostics = validate_normalize_and_deduplicate_entity_type_expansion(
            candidate_suggestions=[
                valid_candidate(entity_type_code="venture_capital_firm"),
                valid_candidate(entity_type_code="corporate_venture_capital"),
            ],
            proposed_type_suggestions=[],
            information_needs=(make_need(),),
            target_career_paths=[make_path()],
            ontology=get_entity_type_ontology(),
            llm_metadata=LLMExecutionMetadata(provider="fake", model="fake"),
            input_fingerprint="fingerprint",
            max_canonical_candidates=1,
            max_proposed_types=5,
            max_types_per_need=5,
            max_discovery_terms=8,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(rejected), 1)
        self.assertIn("max_canonical_candidates", rejected[0].reason)
        self.assertEqual(uncovered, ())
        self.assertTrue(any("truncated" in item.lower() for item in diagnostics))

    def test_responsibility_boundaries_reject_invalid_output(self):
        invalid_cases = [
            valid_candidate(rationale="Track OpenAI announcements"),
            valid_candidate(rationale="跟踪腾讯的AI产品发布"),
            valid_candidate(entity_type_code="Sequoia Capital"),
            valid_candidate(discovery_terms=["https://example.com"]),
            valid_candidate(discovery_terms=["example.com"]),
            valid_candidate(discovery_terms=["例子.cn"]),
            valid_candidate(discovery_terms=["RSS feed URL"]),
            valid_candidate(discovery_terms=["site:example.com AI investors"]),
            valid_candidate(discovery_terms=['"AI investors" AND "APAC"']),
            valid_candidate(rationale="Summarize article title"),
            valid_candidate(rationale="Track this job posting"),
            valid_candidate(entity_type_code="rss"),
        ]

        for payload in invalid_cases:
            with self.subTest(payload=payload):
                _, _, rejected, _, _ = run_validation([payload])
                self.assertEqual(len(rejected), 1)

    def test_valid_generic_phrases_are_allowed(self):
        candidates, _, rejected, uncovered, _ = run_validation(
            [
                valid_candidate(
                    entity_type_code="technology_consulting_firm",
                    rationale=(
                        "Technology consulting firms and corporate strategy "
                        "teams can discuss AI implementation demand."
                    ),
                    discovery_terms=[
                        "AI-native companies",
                        "AI原生公司",
                        "venture capital firms",
                        "风险投资机构",
                        "portfolio companies",
                        "technology consulting firms",
                        "policy research institutions",
                        "corporate strategy teams",
                    ],
                )
            ]
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(rejected, ())
        self.assertEqual(uncovered, ())


class EntityTypeIdentityAndCacheTests(unittest.TestCase):
    def test_candidate_id_is_stable(self):
        self.assertEqual(
            build_entity_type_candidate_id(
                entity_type_code="venture_capital_firm",
                ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
            ),
            build_entity_type_candidate_id(
                entity_type_code="venture_capital_firm",
                ontology_version=ENTITY_TYPE_ONTOLOGY_VERSION,
            ),
        )

    def test_equivalent_inputs_produce_same_fingerprint(self):
        metadata = LLMExecutionMetadata(
            provider="fake",
            model="fake-model",
            prompt_version="entity_type_expansion_prompt_v1",
            schema_version=ENTITY_TYPE_CANDIDATE_SCHEMA_VERSION,
        )
        first = build_entity_type_expansion_input_fingerprint(
            information_needs=(make_need("need_b"), make_need("need_a")),
            phase0_output_hash="phase0",
            target_career_paths=[make_path("b"), make_path("a")],
            user_preferences={"b": 2, "a": 1},
            monitoring_objectives=get_monitoring_objectives(),
            ontology=get_entity_type_ontology(),
            llm_metadata=metadata,
            generation_limits={"max": 1},
            temperature=0.2,
        )
        second = build_entity_type_expansion_input_fingerprint(
            information_needs=(make_need("need_a"), make_need("need_b")),
            phase0_output_hash="phase0",
            target_career_paths=[make_path("a"), make_path("b")],
            user_preferences={"a": 1, "b": 2},
            monitoring_objectives=get_monitoring_objectives(),
            ontology=get_entity_type_ontology(),
            llm_metadata=metadata,
            generation_limits={"max": 1},
            temperature=0.2,
        )

        self.assertEqual(first, second)

    def test_meaningful_information_need_change_changes_fingerprint(self):
        metadata = LLMExecutionMetadata(provider="fake", model="fake")
        first = build_entity_type_expansion_input_fingerprint(
            information_needs=(make_need(),),
            phase0_output_hash="phase0-a",
            target_career_paths=[make_path()],
            user_preferences={},
            monitoring_objectives=get_monitoring_objectives(),
            ontology=get_entity_type_ontology(),
            llm_metadata=metadata,
            generation_limits={},
            temperature=0.2,
        )
        second = build_entity_type_expansion_input_fingerprint(
            information_needs=(make_need("need_changed"),),
            phase0_output_hash="phase0-b",
            target_career_paths=[make_path()],
            user_preferences={},
            monitoring_objectives=get_monitoring_objectives(),
            ontology=get_entity_type_ontology(),
            llm_metadata=metadata,
            generation_limits={},
            temperature=0.2,
        )

        self.assertNotEqual(first, second)

    def test_output_hash_is_deterministic(self):
        candidates, proposed, rejected, uncovered, _ = run_validation([valid_candidate()])
        self.assertEqual(
            build_entity_type_expansion_output_hash(
                canonical_candidates=candidates,
                proposed_new_types=proposed,
                rejected_suggestions=rejected,
                uncovered_information_need_ids=uncovered,
            ),
            build_entity_type_expansion_output_hash(
                canonical_candidates=candidates,
                proposed_new_types=proposed,
                rejected_suggestions=rejected,
                uncovered_information_need_ids=uncovered,
            ),
        )

    def test_cache_hit_avoids_llm_call_and_round_trips_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_type_candidates.json"
            client = FakeEntityTypeExpansionClient()
            first = expand_entity_types(
                information_needs=(make_need(),),
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )
            second = expand_entity_types(
                information_needs=(make_need(),),
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )

        self.assertEqual(client.calls, 1)
        self.assertEqual(second.generation_mode, "loaded_from_cache")
        self.assertEqual(first.input_fingerprint, second.input_fingerprint)
        self.assertEqual(first.output_hash, second.output_hash)
        self.assertEqual(
            [candidate.to_dict() for candidate in first.canonical_candidates],
            [candidate.to_dict() for candidate in second.canonical_candidates],
        )
        self.assertTrue(second.canonical_candidates[0].provenance)

    def test_force_refresh_bypasses_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_type_candidates.json"
            client = FakeEntityTypeExpansionClient()

            expand_entity_types(
                information_needs=(make_need(),),
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )
            expand_entity_types(
                information_needs=(make_need(),),
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
                force_refresh=True,
            )

        self.assertEqual(client.calls, 2)

    def test_model_and_prompt_changes_miss_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_type_candidates.json"
            client = FakeEntityTypeExpansionClient()

            expand_entity_types(
                information_needs=(make_need(),),
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
                model="model-a",
            )
            expand_entity_types(
                information_needs=(make_need(),),
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
                model="model-b",
            )
            with patch(
                "src.source_monitoring.entity_type_expander.ENTITY_TYPE_EXPANSION_PROMPT_VERSION",
                "prompt-b",
            ):
                expand_entity_types(
                    information_needs=(make_need(),),
                    target_career_paths=[make_path()],
                    user_preferences={},
                    client=client,
                    cache_file=cache_file,
                    model="model-b",
                )

        self.assertEqual(client.calls, 3)

    def test_ontology_version_change_misses_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_type_candidates.json"
            client = FakeEntityTypeExpansionClient()
            result = expand_entity_types(
                information_needs=(make_need(),),
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )

            loaded, diagnostics = load_cached_entity_type_expansion_result(
                cache_file,
                result.input_fingerprint,
                "entity_type_ontology_v2",
            )

        self.assertIsNone(loaded)
        self.assertTrue(any("ontology version" in item for item in diagnostics))

    def test_old_phase1_ontology_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_type_candidates.json"
            client = FakeEntityTypeExpansionClient()
            result = expand_entity_types(
                information_needs=(make_need(),),
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )
            payload = result.to_dict()
            for entity_type in payload["canonical_entity_types"]:
                entity_type["ontology_version"] = "entity_type_ontology_v1"
            save_json(payload, cache_file)

            loaded, diagnostics = load_cached_entity_type_expansion_result(
                cache_file,
                result.input_fingerprint,
                ENTITY_TYPE_ONTOLOGY_VERSION,
            )

        self.assertIsNone(loaded)
        self.assertTrue(any("ontology version" in item for item in diagnostics))

    def test_malformed_cache_is_diagnosed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_type_candidates.json"
            client = FakeEntityTypeExpansionClient()
            first = expand_entity_types(
                information_needs=(make_need(),),
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )
            save_json(
                {
                    "input_fingerprint": first.input_fingerprint,
                    "schema_version": "entity_type_expansion_v1",
                    "output_hash": first.output_hash,
                    "canonical_entity_types": [
                        {"ontology_version": ENTITY_TYPE_ONTOLOGY_VERSION}
                    ],
                    "canonical_candidates": ["bad"],
                },
                cache_file,
            )
            second = expand_entity_types(
                information_needs=(make_need(),),
                target_career_paths=[make_path()],
                user_preferences={},
                client=client,
                cache_file=cache_file,
            )

        self.assertEqual(client.calls, 2)
        self.assertTrue(any("cache" in item.lower() for item in second.diagnostics))


class EntityTypeRegressionTests(unittest.TestCase):
    def test_phase1_does_not_change_search_query_or_plan_generation(self):
        path = make_path()
        scope = SearchScope(
            scope_id="scope",
            name="Scope",
            source_types=[SourceType.SEARCH_API],
            max_results_per_query=3,
        )
        queries_before = generate_search_queries([path])
        plans_before = build_search_plans(queries_before, scope)

        expand_entity_types(
            information_needs=(make_need(),),
            target_career_paths=[path],
            user_preferences={},
            client=FakeEntityTypeExpansionClient(),
            cache_enabled=False,
        )

        self.assertEqual(
            [query.to_dict() for query in generate_search_queries([path])],
            [query.to_dict() for query in queries_before],
        )
        self.assertEqual(
            [plan.to_dict() for plan in build_search_plans(queries_before, scope)],
            [plan.to_dict() for plan in plans_before],
        )


class DeterministicExampleTests(unittest.TestCase):
    def test_ai_investment_and_funding_trends_example_mapping(self):
        result = expand_entity_types(
            information_needs=(make_need(),),
            target_career_paths=[make_path()],
            user_preferences={},
            client=FakeEntityTypeExpansionClient(
                {
                    "entity_type_candidates": [
                        valid_candidate(entity_type_code="venture_capital_firm"),
                        valid_candidate(entity_type_code="corporate_venture_capital"),
                        valid_candidate(entity_type_code="investment_data_provider"),
                        valid_candidate(entity_type_code="research_institute"),
                        valid_candidate(entity_type_code="professional_media"),
                    ],
                    "proposed_new_types": [],
                }
            ),
            cache_enabled=False,
        )

        self.assertEqual(
            [candidate.entity_type_code for candidate in result.canonical_candidates],
            [
                "corporate_venture_capital",
                "investment_data_provider",
                "professional_media",
                "research_institute",
                "venture_capital_firm",
            ],
        )


if __name__ == "__main__":
    unittest.main()
