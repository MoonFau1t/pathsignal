from dataclasses import FrozenInstanceError
import unittest

from src.database.planning_identity import canonical_json
from src.models import CareerPathCategory, TargetCareerPath
from src.source_monitoring.identity import (
    build_information_need_id,
    build_information_need_input_fingerprint,
    build_information_need_output_hash,
)
from src.source_monitoring.models import (
    INFORMATION_NEED_SCHEMA_VERSION,
    InformationNeed,
    InformationNeedPriority,
    LLMExecutionMetadata,
    MonitoringObjectiveCode,
)
from src.source_monitoring.monitoring_objectives import (
    get_monitoring_objectives,
    monitoring_objective_codes,
    validate_monitoring_objectives,
)
from src.source_monitoring.validators import (
    parse_information_need_suggestions,
    validate_normalize_and_deduplicate_information_needs,
)


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


def make_metadata(input_fingerprint: str = "fingerprint") -> LLMExecutionMetadata:
    return LLMExecutionMetadata(
        provider="deepseek",
        model="test-model",
        prompt_version="information_need_prompt_v1",
        input_fingerprint=input_fingerprint,
    )


def make_suggestion(**overrides):
    payload = {
        "need_key": "junior_vc_hiring_requirements",
        "objective_code": "career_path",
        "title": "Junior VC hiring requirements",
        "description": (
            "Monitor generic requirements, candidate backgrounds, analytical "
            "skills, and entry routes for junior venture roles."
        ),
        "related_target_career_path_ids": ["venture_capital"],
        "signal_examples": [
            "Role descriptions mentioning entry-level investment analysis",
            "Public descriptions of junior investor responsibilities",
        ],
        "rationale": "This helps evaluate fit and preparation for the path.",
        "priority": "high",
        "confidence": 0.87,
    }
    payload.update(overrides)
    return payload


class MonitoringObjectiveTaxonomyTests(unittest.TestCase):
    def test_exact_four_objective_codes_exist(self):
        self.assertEqual(
            monitoring_objective_codes(),
            ("opportunity", "organization", "industry", "career_path"),
        )

    def test_enum_codes_are_stable(self):
        self.assertEqual(
            [code.value for code in MonitoringObjectiveCode],
            ["opportunity", "organization", "industry", "career_path"],
        )

    def test_definitions_are_non_empty_and_versioned(self):
        for objective in get_monitoring_objectives():
            self.assertTrue(objective.label)
            self.assertTrue(objective.description)
            self.assertGreaterEqual(len(objective.supported_signal_examples), 2)
            self.assertEqual(objective.schema_version, "monitoring_objective_v1")

    def test_definitions_are_frozen(self):
        objective = get_monitoring_objectives()[0]

        with self.assertRaises(FrozenInstanceError):
            objective.label = "Changed"

    def test_taxonomy_validator_rejects_added_objective(self):
        objectives = list(get_monitoring_objectives())
        objectives.append(objectives[0])

        with self.assertRaisesRegex(ValueError, "exactly"):
            validate_monitoring_objectives(objectives)

    def test_llm_output_cannot_add_fifth_objective(self):
        needs, rejected, diagnostics = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(objective_code="compensation")],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertEqual(needs, ())
        self.assertEqual(len(rejected), 1)
        self.assertIn("objective_code", rejected[0].reason)
        self.assertEqual(diagnostics, ())

    def test_aliases_do_not_silently_create_objective_codes(self):
        needs, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(objective_code="jobs")],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertEqual(needs, ())
        self.assertEqual(len(rejected), 1)


class InformationNeedModelValidationTests(unittest.TestCase):
    def test_valid_information_need_is_normalized(self):
        needs, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(need_key="Junior VC Hiring Requirements")],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertEqual(rejected, ())
        self.assertEqual(needs[0].need_key, "junior_vc_hiring_requirements")
        self.assertEqual(needs[0].objective_code, MonitoringObjectiveCode.CAREER_PATH)
        self.assertEqual(needs[0].priority, InformationNeedPriority.HIGH)
        self.assertEqual(needs[0].confidence, 0.87)

    def test_missing_path_ids_rejected(self):
        _, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(related_target_career_path_ids=[])],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertIn("at least one", rejected[0].reason)

    def test_invalid_objective_rejected(self):
        _, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(objective_code="source")],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertIn("objective_code", rejected[0].reason)

    def test_invalid_priority_rejected(self):
        _, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(priority="urgent")],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertIn("priority", rejected[0].reason)

    def test_invalid_confidence_rejected(self):
        _, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(confidence=1.5)],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertIn("confidence", rejected[0].reason)

    def test_invalid_need_key_rejected(self):
        _, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(need_key="??")],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertIn("need_key", rejected[0].reason)

    def test_empty_title_rejected(self):
        _, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(title=" ")],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertIn("title", rejected[0].reason)

    def test_empty_description_rejected(self):
        _, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(description="")],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertIn("description", rejected[0].reason)

    def test_stable_serialization_uses_enum_values(self):
        needs, _, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion()],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        payload = needs[0].to_dict()
        self.assertEqual(payload["objective_code"], "career_path")
        self.assertEqual(payload["priority"], "high")
        self.assertIn("career_path", canonical_json(payload))


class InformationNeedParsingTests(unittest.TestCase):
    def test_valid_structured_json_extracts_suggestions(self):
        suggestions, diagnostics = parse_information_need_suggestions(
            {"information_needs": [make_suggestion()]}
        )

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(diagnostics, [])

    def test_non_object_top_level_response_is_rejected(self):
        suggestions, diagnostics = parse_information_need_suggestions([])

        self.assertEqual(suggestions, [])
        self.assertIn("JSON object", diagnostics[0])

    def test_missing_required_list_is_diagnosed(self):
        suggestions, diagnostics = parse_information_need_suggestions({})

        self.assertEqual(suggestions, [])
        self.assertIn("information_needs list", diagnostics[-1])

    def test_unsupported_top_level_fields_are_diagnosed(self):
        suggestions, diagnostics = parse_information_need_suggestions(
            {"information_needs": [], "monitoring_objectives": []}
        )

        self.assertEqual(suggestions, [])
        self.assertIn("Unexpected top-level field", diagnostics[0])

    def test_unsupported_item_extra_fields_are_rejected(self):
        _, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(information_need_id="llm_must_not_set")],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertIn("unsupported fields", rejected[0].reason)


class ReferenceValidationTests(unittest.TestCase):
    def test_unknown_target_career_path_id_rejected(self):
        _, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[
                make_suggestion(related_target_career_path_ids=["missing_path"])
            ],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertIn("unknown TargetCareerPath", rejected[0].reason)

    def test_repeated_target_career_path_ids_are_deduplicated(self):
        needs, _, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[
                make_suggestion(
                    related_target_career_path_ids=[
                        "venture_capital",
                        "venture_capital",
                    ]
                )
            ],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertEqual(needs[0].related_target_career_path_ids, ("venture_capital",))

    def test_one_need_can_support_several_paths(self):
        needs, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[
                make_suggestion(
                    related_target_career_path_ids=[
                        "venture_capital",
                        "ai_strategy",
                    ]
                )
            ],
            target_career_paths=[make_path(), make_path("ai_strategy")],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertEqual(rejected, ())
        self.assertEqual(
            needs[0].related_target_career_path_ids,
            ("venture_capital", "ai_strategy"),
        )

    def test_all_supplied_paths_can_receive_coverage(self):
        needs, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[
                make_suggestion(
                    need_key="junior_vc_hiring_requirements",
                    related_target_career_path_ids=["venture_capital"],
                ),
                make_suggestion(
                    need_key="ai_strategy_team_requirements",
                    related_target_career_path_ids=["ai_strategy"],
                ),
            ],
            target_career_paths=[make_path(), make_path("ai_strategy")],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        covered = {
            path_id
            for need in needs
            for path_id in need.related_target_career_path_ids
        }
        self.assertEqual(rejected, ())
        self.assertEqual(covered, {"venture_capital", "ai_strategy"})

    def test_validation_does_not_mutate_target_career_paths(self):
        path = make_path()
        before = path.to_dict()

        validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion()],
            target_career_paths=[path],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertEqual(path.to_dict(), before)


class ResponsibilityBoundaryTests(unittest.TestCase):
    def assert_rejected_for_boundary(self, **overrides):
        _, rejected, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[make_suggestion(**overrides)],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )
        self.assertEqual(len(rejected), 1)

    def test_concrete_company_name_as_need_is_rejected(self):
        self.assert_rejected_for_boundary(
            description="Monitor entry-level hiring at OpenAI."
        )

    def test_website_url_is_rejected(self):
        self.assert_rejected_for_boundary(description="Monitor https://example.com")

    def test_domain_is_rejected(self):
        self.assert_rejected_for_boundary(description="Monitor careers.example.com")

    def test_rss_feed_reference_is_rejected(self):
        self.assert_rejected_for_boundary(description="Monitor the RSS feed URL")

    def test_site_search_query_is_rejected(self):
        self.assert_rejected_for_boundary(description="Use site:example.com hiring")

    def test_search_plan_reference_is_rejected(self):
        self.assert_rejected_for_boundary(description="Create a search plan for VC")

    def test_concrete_job_posting_is_rejected(self):
        self.assert_rejected_for_boundary(description="Track this job posting")

    def test_entity_type_list_is_rejected(self):
        self.assert_rejected_for_boundary(description="Generate entity types")

    def test_article_title_as_need_is_rejected(self):
        self.assert_rejected_for_boundary(description="Headline: fund launches today")


class DeduplicationAndIdentityTests(unittest.TestCase):
    def test_duplicate_need_keys_merge(self):
        needs, rejected, diagnostics = validate_normalize_and_deduplicate_information_needs(
            suggestions=[
                make_suggestion(confidence=0.8, priority="medium"),
                make_suggestion(
                    related_target_career_path_ids=["ai_strategy"],
                    signal_examples=["Team pages describing junior investor work"],
                    rationale="Second rationale",
                    confidence=1.0,
                    priority="high",
                ),
            ],
            target_career_paths=[make_path(), make_path("ai_strategy")],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertEqual(rejected, ())
        self.assertEqual(len(needs), 1)
        self.assertEqual(
            set(needs[0].related_target_career_path_ids),
            {"venture_capital", "ai_strategy"},
        )
        self.assertIn("Team pages", " ".join(needs[0].signal_examples))
        self.assertEqual(needs[0].priority, InformationNeedPriority.HIGH)
        self.assertEqual(needs[0].confidence, 0.9)
        self.assertTrue(any("Merged duplicate" in item for item in diagnostics))

    def test_ordering_is_stable(self):
        first, _, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[
                make_suggestion(need_key="low_need", priority="low"),
                make_suggestion(need_key="high_need", priority="high"),
                make_suggestion(need_key="medium_need", priority="medium"),
            ],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )
        second, _, _ = validate_normalize_and_deduplicate_information_needs(
            suggestions=[
                make_suggestion(need_key="medium_need", priority="medium"),
                make_suggestion(need_key="low_need", priority="low"),
                make_suggestion(need_key="high_need", priority="high"),
            ],
            target_career_paths=[make_path()],
            llm_metadata=make_metadata(),
            input_fingerprint="fingerprint",
            max_total=10,
            max_signal_examples=5,
            max_per_path_objective=4,
        )

        self.assertEqual(
            [need.need_key for need in first],
            [need.need_key for need in second],
        )

    def test_equivalent_normalized_inputs_produce_same_information_need_id(self):
        self.assertEqual(
            build_information_need_id(
                objective_code="career_path",
                need_key="junior_vc_hiring_requirements",
            ),
            build_information_need_id(
                objective_code="career_path",
                need_key="junior_vc_hiring_requirements",
            ),
        )

    def test_output_hash_changes_when_normalized_output_changes(self):
        need = InformationNeed(
            information_need_id="need_a",
            need_key="need_a",
            objective_code=MonitoringObjectiveCode.OPPORTUNITY,
            title="A",
            description="A description",
            related_target_career_path_ids=("venture_capital",),
            signal_examples=("Example",),
            rationale="Rationale",
            priority=InformationNeedPriority.HIGH,
            confidence=0.9,
        )
        changed = InformationNeed(
            information_need_id="need_b",
            need_key="need_b",
            objective_code=MonitoringObjectiveCode.OPPORTUNITY,
            title="B",
            description="B description",
            related_target_career_path_ids=("venture_capital",),
            signal_examples=("Example",),
            rationale="Rationale",
            priority=InformationNeedPriority.HIGH,
            confidence=0.9,
        )

        self.assertNotEqual(
            build_information_need_output_hash((need,)),
            build_information_need_output_hash((changed,)),
        )

    def test_equivalent_generation_inputs_produce_same_fingerprint(self):
        metadata = make_metadata()
        first = build_information_need_input_fingerprint(
            target_career_paths=[make_path()],
            user_preferences={"preferred_industries": ["AI"]},
            monitoring_objectives=get_monitoring_objectives(),
            llm_metadata=metadata,
            generation_limits={"max_total": 120},
            temperature=0.2,
        )
        second = build_information_need_input_fingerprint(
            target_career_paths=[make_path()],
            user_preferences={"preferred_industries": ["AI"]},
            monitoring_objectives=get_monitoring_objectives(),
            llm_metadata=metadata,
            generation_limits={"max_total": 120},
            temperature=0.2,
        )

        self.assertEqual(first, second)

    def test_meaningful_generation_input_change_changes_fingerprint(self):
        metadata = make_metadata()
        first = build_information_need_input_fingerprint(
            target_career_paths=[make_path()],
            user_preferences={"preferred_industries": ["AI"]},
            monitoring_objectives=get_monitoring_objectives(),
            llm_metadata=metadata,
            generation_limits={"max_total": 120},
            temperature=0.2,
        )
        second = build_information_need_input_fingerprint(
            target_career_paths=[make_path()],
            user_preferences={"preferred_industries": ["Healthcare"]},
            monitoring_objectives=get_monitoring_objectives(),
            llm_metadata=metadata,
            generation_limits={"max_total": 120},
            temperature=0.2,
        )

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
