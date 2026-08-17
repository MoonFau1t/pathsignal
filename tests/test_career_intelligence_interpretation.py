import json
import unittest
from dataclasses import FrozenInstanceError

from src.career_intelligence_interpretation import (
    CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
    CAREER_INTELLIGENCE_INTERPRETATION_SYSTEM_PROMPT,
    EMPTY_INPUT_WARNING,
    INTERPRETATION_USER_PREFERENCE_KEYS,
    CareerImplicationInterpretation,
    CareerIntelligenceInterpretationClient,
    CareerIntelligenceInterpretationError,
    CareerIntelligenceInterpretationResult,
    InterpretationConfidence,
    InterpretationRequestContext,
    KeyDevelopmentInterpretation,
    ThemeInterpretation,
    career_intelligence_interpretation_result_from_dict,
    parse_career_intelligence_interpretation_response,
    render_career_intelligence_interpretation_request,
)
from src.career_signal_priority import ScoredCareerSignal
from src.career_signal_scoring import PriorityScoreResult, PriorityTier
from src.models import (
    CareerPathCategory,
    CareerSignal,
    SignalCategory,
    SourceType,
    TargetCareerPath,
)
from src.priority_assessment import (
    AssessmentProfile,
    ComponentStatus,
    PriorityAssessmentResult,
    SemanticComponentResult,
)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.content)


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content):
        self.chat = _FakeChat(content)


class _MalformedResponseCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return object()


class _MalformedResponseClient:
    def __init__(self):
        self.chat = type("FakeChat", (), {})()
        self.chat.completions = _MalformedResponseCompletions()


class _FailingCompletions:
    def create(self, **kwargs):
        raise RuntimeError("synthetic transport secret")


class _FailingClient:
    def __init__(self):
        self.chat = type("FakeChat", (), {})()
        self.chat.completions = _FailingCompletions()


def _target_paths():
    return (
        TargetCareerPath(
            path_id="path-ai-strategy",
            title="AI Strategy",
            category=CareerPathCategory.AI_STRATEGY,
            description="Strategy roles connecting AI capability and adoption.",
            fit_score=91.0,
            rationale=["Synthetic rationale that should not be rendered."],
            suggested_roles=["AI Strategy Associate"],
            search_seed_terms=["synthetic search term"],
            metadata={"path_type": "core"},
        ),
        TargetCareerPath(
            path_id="path-tech-consulting",
            title="Technology Consulting",
            category=CareerPathCategory.TECH_CONSULTING,
            description="Consulting roles focused on technology transformation.",
            fit_score=84.0,
            suggested_roles=["Technology Transformation Consultant"],
        ),
    )


def _semantic_component(name):
    return SemanticComponentResult(
        status=ComponentStatus.AVAILABLE,
        score=0.75,
        reason=f"Synthetic {name} reason.",
        evidence=(f"Synthetic {name} evidence.",),
    )


def _scored_signal(
    signal_id,
    *,
    category=SignalCategory.NEWS,
    source_type=SourceType.SEARCH_API,
    priority_score=82.0,
    matched_path_ids=("path-ai-strategy",),
):
    signal = CareerSignal(
        signal_id=signal_id,
        category=category,
        title=f"Synthetic development {signal_id}",
        organization="Synthetic Example Organization",
        url=f"https://example.test/{signal_id}",
        published_at="2026-08-12T00:00:00+00:00",
        summary="Synthetic evidence about enterprise AI implementation.",
        source_type=source_type,
        relevance_score=93.0,
        metadata={
            "event_type": "practice_expansion",
            "source_excerpt": "Synthetic supplied source evidence.",
            "unrelated_metadata": "must not render",
        },
    )
    assessment = PriorityAssessmentResult(
        schema_version="priority_assessment_v1",
        signal_id=signal_id,
        assessment_profile=AssessmentProfile.INTELLIGENCE,
        components={
            "career_relevance_strength": _semantic_component(
                "career relevance"
            ),
            "signal_significance": _semantic_component(
                "signal significance"
            ),
        },
        warnings=(),
    )
    score = PriorityScoreResult(
        signal_id=signal_id,
        priority_score=priority_score,
        tier=PriorityTier.MEDIUM_HIGH,
        profile=AssessmentProfile.INTELLIGENCE,
        components={},
        matched_path_ids=matched_path_ids,
        policy_version="career_signal_priority_v1",
        renormalization_denominator=100.0,
        warnings=(),
    )
    return ScoredCareerSignal(
        career_signal=signal,
        priority_assessment=assessment,
        priority_score=score,
        assessment_profile=AssessmentProfile.INTELLIGENCE,
    )


def _context(*, signals=None, paths=None, preferences=None):
    return InterpretationRequestContext(
        intelligence_signals=(
            signals
            if signals is not None
            else (
                _scored_signal("signal-001", priority_score=91.0),
                _scored_signal(
                    "signal-002",
                    category=SignalCategory.COMPANY,
                    priority_score=77.0,
                    matched_path_ids=("path-tech-consulting",),
                ),
            )
        ),
        target_career_paths=paths if paths is not None else _target_paths(),
        user_preferences=(
            preferences
            if preferences is not None
            else {
                "career_objectives": {"ordered": ["AI strategy"]},
                "role_preferences": {"primary": ["strategy"]},
                "industry_preferences": {"preferred": ["technology"]},
                "work_content_preferences": {
                    "preferred": ["implementation strategy"]
                },
                "hard_constraints": ["synthetic constraint"],
                "user_profile": {
                    "raw_resume_text": "private profile text must not render"
                },
                "work_authorization": {
                    "nationality": "private authorization must not render"
                },
                "compensation_preferences": {
                    "absolute_floor": "private compensation must not render"
                },
                "recommendation_explanation_preferences": {
                    "show_application_advice": True
                },
                "revision_log": ["private revision history must not render"],
            }
        ),
    )


def _valid_payload():
    return {
        "schema_version": CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
        "input_signal_ids": ["signal-001", "signal-002"],
        "themes": [
            {
                "title": "Enterprise AI moves toward implementation",
                "summary": "Two supplied signals indicate an implementation shift.",
                "supporting_signal_ids": ["signal-001", "signal-002"],
                "relevant_career_path_ids": ["path-ai-strategy"],
                "confidence": "high",
            }
        ],
        "key_developments": [
            {
                "title": "AI enters an operational workflow",
                "summary": "A supplied signal describes operational deployment.",
                "why_it_matters": "It is concrete evidence of implementation activity.",
                "supporting_signal_ids": ["signal-001"],
                "confidence": "medium",
            }
        ],
        "career_implications": [
            {
                "summary": "Implementation experience may matter more for AI Strategy roles.",
                "relevant_career_path_ids": ["path-ai-strategy"],
                "supporting_signal_ids": ["signal-001", "signal-002"],
                "confidence": "medium",
            }
        ],
        "warnings": ["Evidence is limited to two synthetic signals."],
    }


def _parse(payload):
    return career_intelligence_interpretation_result_from_dict(
        payload,
        expected_input_signal_ids=("signal-001", "signal-002"),
        expected_career_path_ids=(
            "path-ai-strategy",
            "path-tech-consulting",
        ),
    )


class CareerIntelligencePromptTests(unittest.TestCase):
    def test_system_prompt_freezes_semantics_and_boundaries(self):
        prompt = CAREER_INTELLIGENCE_INTERPRETATION_SYSTEM_PROMPT
        required = (
            "multi-signal Career Intelligence Interpretation",
            "Interpret only the supplied Intelligence CareerSignals",
            "Use only information supplied in this request",
            "Do not reinterpret individual job Opportunities",
            "Do not recalculate, validate, change, or replace Priority Score",
            "Do not redo CareerPath matching",
            "What broader user-relevant pattern is emerging across multiple supplied Intelligence signals?",
            "at least TWO DISTINCT supporting signals",
            "What concrete recent event, change, focal development, or evidence-backed potential technological breakthrough deserves attention?",
            "at least ONE supporting signal",
            "What could the supplied developments mean for the user's current TargetCareerPaths?",
            "at least ONE supplied TargetCareerPath",
            "HIGH means",
            "MEDIUM means",
            "LOW means",
            "zero and 5 Themes",
            "zero and 8 Key Developments",
            "zero and 5 Career Implications",
            "Empty arrays are valid",
            "create an action plan",
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, prompt)

    def test_rendered_user_prompt_contains_exact_contract(self):
        rendered = render_career_intelligence_interpretation_request(_context())
        prompt = rendered.user_prompt
        required = (
            '"schema_version": "career_intelligence_interpretation_v1"',
            "exactly these top-level keys",
            "at least TWO DISTINCT supplied input signal IDs",
            "at least ONE supplied input signal ID",
            "at least ONE supplied TargetCareerPath ID",
            "no more than 5 Themes",
            "no more than 8 Key Developments",
            "no more than 5 Career Implications",
            "exactly high, medium, or low",
            "not be treated as proof that an external development occurred",
            "do not create recommendations or actions",
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, prompt)

    def test_rendered_prompt_distinguishes_career_relevance_from_external_facts(self):
        rendered = render_career_intelligence_interpretation_request(_context())
        prompt = "\n\n".join(message["content"] for message in rendered.messages)
        required = (
            "Career Implication may infer how a supplied external development "
            "is relevant to a current CareerPath",
            "must NOT introduce a new external-world factual claim",
            "hiring demand is increasing",
            "a market is growing",
            "startup or investment opportunities are increasing",
            "analyst demand is rising",
            "These cases make AI implementation experience more relevant to AI "
            "Strategy and Digital Transformation career paths.",
            "These cases prove hiring demand for AI Strategy roles is increasing.",
            "These signals are relevant to VC analysts assessing enterprise AI "
            "adoption.",
            "These signals show growing investment opportunities in enterprise "
            "AI startups.",
            "Cross-sector AI implementation is relevant to Industry Research "
            "roles tracking enterprise adoption.",
            "These signals prove demand for industry research analysts is rising.",
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, prompt)

    def test_prompt_example_is_concrete_json_accepted_by_same_parser(self):
        rendered = render_career_intelligence_interpretation_request(_context())
        example = rendered.canonical_output_example
        serialized = json.dumps(example)
        example_signal_ids = tuple(
            rendered.canonical_example_input["input_signal_ids"]
        )
        example_path_ids = tuple(
            rendered.canonical_example_input["target_career_path_ids"]
        )

        result = parse_career_intelligence_interpretation_response(
            serialized,
            expected_input_signal_ids=example_signal_ids,
            expected_career_path_ids=example_path_ids,
        )

        self.assertEqual(result.input_signal_ids, ("signal-001", "signal-002"))
        self.assertEqual(len(result.themes), 1)
        self.assertEqual(len(result.key_developments), 1)
        self.assertEqual(len(result.career_implications), 1)
        self.assertIn(
            json.dumps(example, ensure_ascii=True, indent=2),
            rendered.user_prompt,
        )
        self.assertNotIn("high | medium | low", serialized)
        self.assertNotIn("<signal_id>", serialized)
        self.assertNotIn("<path_id>", serialized)

    def test_prompt_example_ids_are_grounded_in_synthetic_example_input(self):
        rendered = render_career_intelligence_interpretation_request(_context())
        example = rendered.canonical_output_example
        input_signal_ids = set(
            rendered.canonical_example_input["input_signal_ids"]
        )
        target_career_path_ids = set(
            rendered.canonical_example_input["target_career_path_ids"]
        )

        self.assertEqual(set(example["input_signal_ids"]), input_signal_ids)
        for theme in example["themes"]:
            self.assertTrue(set(theme["supporting_signal_ids"]) <= input_signal_ids)
            self.assertTrue(
                set(theme["relevant_career_path_ids"])
                <= target_career_path_ids
            )
        for development in example["key_developments"]:
            self.assertTrue(
                set(development["supporting_signal_ids"])
                <= input_signal_ids
            )
        for implication in example["career_implications"]:
            self.assertTrue(
                set(implication["supporting_signal_ids"])
                <= input_signal_ids
            )
            self.assertTrue(
                set(implication["relevant_career_path_ids"])
                <= target_career_path_ids
            )

        serialized = json.dumps(example)
        for forbidden in (
            "<signal_id>",
            "<path_id>",
            "high | medium | low",
            "A | B",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

    def test_rendering_bounds_user_preferences_and_omits_user_profile(self):
        rendered = render_career_intelligence_interpretation_request(_context())
        preferences = rendered.payload["user_preferences"]

        self.assertEqual(
            set(preferences),
            {
                "career_objectives",
                "role_preferences",
                "industry_preferences",
                "work_content_preferences",
                "hard_constraints",
            },
        )
        self.assertTrue(set(preferences).issubset(INTERPRETATION_USER_PREFERENCE_KEYS))
        self.assertNotIn("user_profile", rendered.payload)
        self.assertNotIn("private profile text", rendered.user_prompt)
        self.assertNotIn("private authorization", rendered.user_prompt)
        self.assertNotIn("private compensation", rendered.user_prompt)
        self.assertNotIn("private revision history", rendered.user_prompt)

    def test_target_paths_and_signal_context_are_bounded(self):
        rendered = render_career_intelligence_interpretation_request(_context())
        path = rendered.payload["target_career_paths"][0]
        signal = rendered.payload["intelligence_signals"][0]

        self.assertEqual(
            set(path),
            {"path_id", "title", "category", "description", "suggested_roles"},
        )
        self.assertEqual(path["path_id"], "path-ai-strategy")
        self.assertNotIn("fit_score", path)
        self.assertNotIn("rationale", path)
        self.assertEqual(
            set(signal),
            {
                "signal_id",
                "category",
                "title",
                "organization",
                "published_at",
                "summary",
                "matched_career_path_ids",
                "intelligence_assessment",
                "priority_context",
                "source_evidence",
            },
        )
        self.assertEqual(signal["priority_context"]["priority_score"], 91.0)
        self.assertEqual(signal["priority_context"]["tier"], "medium_high")
        self.assertIn("career_relevance_strength", signal["intelligence_assessment"])
        self.assertNotIn("relevance_score", signal)
        self.assertNotIn("unrelated_metadata", rendered.user_prompt)

    def test_rendering_preserves_supplied_stage3_order_without_reranking(self):
        context = _context(
            signals=(
                _scored_signal("signal-low", priority_score=51.0),
                _scored_signal("signal-high", priority_score=99.0),
            )
        )
        rendered = render_career_intelligence_interpretation_request(context)
        self.assertEqual(
            rendered.payload["input_signal_ids"],
            ["signal-low", "signal-high"],
        )

    def test_job_and_non_intelligence_categories_are_rejected(self):
        for category in (SignalCategory.JOB, SignalCategory.UNKNOWN, "other"):
            with self.subTest(category=category):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    render_career_intelligence_interpretation_request(
                        _context(signals=(_scored_signal("signal-x", category=category),))
                    )

    def test_all_intelligence_categories_are_accepted(self):
        for category in (
            SignalCategory.NEWS,
            SignalCategory.COMPANY,
            SignalCategory.FUNDING,
            SignalCategory.MARKET_TREND,
        ):
            with self.subTest(category=category):
                rendered = render_career_intelligence_interpretation_request(
                    _context(
                        signals=(
                            _scored_signal(
                                f"signal-{category.value}",
                                category=category,
                            ),
                        )
                    )
                )
                self.assertEqual(
                    rendered.payload["intelligence_signals"][0]["category"],
                    category.value,
                )

    def test_source_type_does_not_determine_intelligence_eligibility(self):
        rendered = render_career_intelligence_interpretation_request(
            _context(
                signals=(
                    _scored_signal(
                        "signal-news",
                        category=SignalCategory.NEWS,
                        source_type=SourceType.MOCK_JOB,
                    ),
                )
            )
        )
        self.assertEqual(rendered.payload["input_signal_ids"], ["signal-news"])

    def test_duplicate_signal_path_and_unknown_matched_path_ids_are_rejected(self):
        duplicate = _scored_signal("signal-duplicate")
        invalid_contexts = (
            _context(signals=(duplicate, duplicate)),
            _context(paths=(_target_paths()[0], _target_paths()[0])),
            _context(
                signals=(
                    _scored_signal(
                        "signal-unknown-path",
                        matched_path_ids=("path-missing",),
                    ),
                )
            ),
        )
        for context in invalid_contexts:
            with self.subTest(context=context):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    render_career_intelligence_interpretation_request(context)

    def test_non_mapping_preferences_are_rejected(self):
        with self.assertRaises(CareerIntelligenceInterpretationError):
            render_career_intelligence_interpretation_request(
                _context(preferences=["not", "an", "object"])
            )


class CareerIntelligenceParserTests(unittest.TestCase):
    def test_valid_response_returns_exact_frozen_result_models(self):
        result = _parse(_valid_payload())

        self.assertEqual(
            result,
            CareerIntelligenceInterpretationResult(
                schema_version=CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
                input_signal_ids=("signal-001", "signal-002"),
                themes=(
                    ThemeInterpretation(
                        title="Enterprise AI moves toward implementation",
                        summary="Two supplied signals indicate an implementation shift.",
                        supporting_signal_ids=("signal-001", "signal-002"),
                        relevant_career_path_ids=("path-ai-strategy",),
                        confidence=InterpretationConfidence.HIGH,
                    ),
                ),
                key_developments=(
                    KeyDevelopmentInterpretation(
                        title="AI enters an operational workflow",
                        summary="A supplied signal describes operational deployment.",
                        why_it_matters="It is concrete evidence of implementation activity.",
                        supporting_signal_ids=("signal-001",),
                        confidence=InterpretationConfidence.MEDIUM,
                    ),
                ),
                career_implications=(
                    CareerImplicationInterpretation(
                        summary="Implementation experience may matter more for AI Strategy roles.",
                        relevant_career_path_ids=("path-ai-strategy",),
                        supporting_signal_ids=("signal-001", "signal-002"),
                        confidence=InterpretationConfidence.MEDIUM,
                    ),
                ),
                warnings=("Evidence is limited to two synthetic signals.",),
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            result.schema_version = "changed"
        self.assertNotIn("priority_score", result.to_dict())
        self.assertNotIn("overall_score", result.to_dict())

    def test_empty_interpretation_arrays_and_warnings_are_valid(self):
        payload = _valid_payload()
        payload["themes"] = []
        payload["key_developments"] = []
        payload["career_implications"] = []
        payload["warnings"] = []

        result = _parse(payload)

        self.assertEqual(result.themes, ())
        self.assertEqual(result.key_developments, ())
        self.assertEqual(result.career_implications, ())
        self.assertEqual(result.warnings, ())

    def test_json_decoding_and_top_level_object_are_strict(self):
        invalid_responses = (
            "not-json",
            "[]",
            "null",
            "\"text\"",
            '{"schema_version": NaN}',
        )
        for response in invalid_responses:
            with self.subTest(response=response):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    parse_career_intelligence_interpretation_response(
                        response,
                        expected_input_signal_ids=("signal-001", "signal-002"),
                        expected_career_path_ids=("path-ai-strategy",),
                    )

    def test_schema_version_must_be_exact(self):
        for version in (
            "hybrid_priority_assessment_v1",
            "career_intelligence_v1",
            "career_intelligence_interpretation_v2",
            None,
        ):
            payload = _valid_payload()
            payload["schema_version"] = version
            with self.subTest(version=version):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    _parse(payload)

    def test_missing_extra_and_prohibited_top_level_fields_are_rejected(self):
        for field in tuple(_valid_payload()):
            payload = _valid_payload()
            del payload[field]
            with self.subTest(missing=field):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    _parse(payload)

        for field in (
            "extra",
            "priority_score",
            "overall_score",
            "recommendation",
            "action_plan",
        ):
            payload = _valid_payload()
            payload[field] = "forbidden"
            with self.subTest(extra=field):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    _parse(payload)

    def test_wrong_top_level_types_are_rejected_without_coercion(self):
        replacements = {
            "input_signal_ids": "signal-001",
            "themes": {},
            "key_developments": None,
            "career_implications": "none",
            "warnings": {},
        }
        for field, value in replacements.items():
            payload = _valid_payload()
            payload[field] = value
            with self.subTest(field=field):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    _parse(payload)

    def test_input_signal_ids_require_exact_set_equality_and_uniqueness(self):
        invalid_ids = (
            ["signal-001"],
            ["signal-001", "signal-002", "signal-unknown"],
            ["signal-001", "signal-001"],
            ["signal-001", 2],
            ["signal-001", " "],
        )
        for ids in invalid_ids:
            payload = _valid_payload()
            payload["input_signal_ids"] = ids
            with self.subTest(ids=ids):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    _parse(payload)

        payload = _valid_payload()
        payload["input_signal_ids"] = ["signal-002", "signal-001"]
        result = _parse(payload)
        self.assertEqual(result.input_signal_ids, ("signal-002", "signal-001"))

    def test_unknown_supporting_signal_is_rejected_for_every_object_type(self):
        for section in ("themes", "key_developments", "career_implications"):
            payload = _valid_payload()
            payload[section][0]["supporting_signal_ids"] = ["signal-unknown"]
            if section == "themes":
                payload[section][0]["supporting_signal_ids"].append("signal-002")
            with self.subTest(section=section):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    _parse(payload)

    def test_theme_validation_is_strict(self):
        mutations = (
            ("one_signal", lambda item: item.update(supporting_signal_ids=["signal-001"])),
            ("duplicate_signal", lambda item: item.update(supporting_signal_ids=["signal-001", "signal-001"])),
            ("empty_title", lambda item: item.update(title=" ")),
            ("empty_summary", lambda item: item.update(summary="")),
            ("invalid_confidence", lambda item: item.update(confidence="very_high")),
            ("numeric_confidence", lambda item: item.update(confidence=1)),
            ("scalar_signals", lambda item: item.update(supporting_signal_ids="signal-001")),
            ("extra_field", lambda item: item.update(recommendation="forbidden")),
        )
        for name, mutate in mutations:
            payload = _valid_payload()
            mutate(payload["themes"][0])
            with self.subTest(name=name):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    _parse(payload)

    def test_theme_empty_path_annotation_is_valid_but_unknown_path_is_rejected(self):
        payload = _valid_payload()
        payload["themes"][0]["relevant_career_path_ids"] = []
        result = _parse(payload)
        self.assertEqual(result.themes[0].relevant_career_path_ids, ())

        payload = _valid_payload()
        payload["themes"][0]["relevant_career_path_ids"] = ["path-unknown"]
        with self.assertRaises(CareerIntelligenceInterpretationError):
            _parse(payload)

    def test_key_development_validation_is_strict(self):
        mutations = (
            ("zero_signals", lambda item: item.update(supporting_signal_ids=[])),
            ("duplicate_signals", lambda item: item.update(supporting_signal_ids=["signal-001", "signal-001"])),
            ("empty_title", lambda item: item.update(title="")),
            ("empty_summary", lambda item: item.update(summary=" ")),
            ("empty_why", lambda item: item.update(why_it_matters="")),
            ("invalid_confidence", lambda item: item.update(confidence="unknown")),
            ("extra_field", lambda item: item.update(next_steps=[])),
        )
        for name, mutate in mutations:
            payload = _valid_payload()
            mutate(payload["key_developments"][0])
            with self.subTest(name=name):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    _parse(payload)

    def test_career_implication_validation_is_strict(self):
        mutations = (
            ("zero_signals", lambda item: item.update(supporting_signal_ids=[])),
            ("zero_paths", lambda item: item.update(relevant_career_path_ids=[])),
            ("unknown_path", lambda item: item.update(relevant_career_path_ids=["path-unknown"])),
            ("duplicate_paths", lambda item: item.update(relevant_career_path_ids=["path-ai-strategy", "path-ai-strategy"])),
            ("empty_summary", lambda item: item.update(summary="")),
            ("invalid_confidence", lambda item: item.update(confidence="uncertain")),
            ("extra_field", lambda item: item.update(action_plan=[])),
        )
        for name, mutate in mutations:
            payload = _valid_payload()
            mutate(payload["career_implications"][0])
            with self.subTest(name=name):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    _parse(payload)

    def test_maximum_counts_are_enforced_without_truncation(self):
        cases = (
            ("themes", 6),
            ("key_developments", 9),
            ("career_implications", 6),
        )
        for section, count in cases:
            payload = _valid_payload()
            payload[section] = [dict(payload[section][0]) for _ in range(count)]
            with self.subTest(section=section):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    _parse(payload)

    def test_warnings_must_be_non_empty_strings(self):
        for warnings in ([""], [" "], [1], None, "warning"):
            payload = _valid_payload()
            payload["warnings"] = warnings
            with self.subTest(warnings=warnings):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    _parse(payload)

    def test_expected_validation_context_must_use_unique_non_empty_ids(self):
        payload = _valid_payload()
        invalid_contexts = (
            (("signal-001", "signal-001"), ("path-ai-strategy",)),
            (("", "signal-002"), ("path-ai-strategy",)),
            (("signal-001", "signal-002"), ("path-ai-strategy", "path-ai-strategy")),
        )
        for signal_ids, path_ids in invalid_contexts:
            with self.subTest(signal_ids=signal_ids, path_ids=path_ids):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    career_intelligence_interpretation_result_from_dict(
                        payload,
                        expected_input_signal_ids=signal_ids,
                        expected_career_path_ids=path_ids,
                    )


class CareerIntelligenceClientTests(unittest.TestCase):
    def test_one_request_makes_one_json_mode_call_with_rendered_messages(self):
        content = json.dumps(_valid_payload())
        fake_client = _FakeClient(content)
        client = CareerIntelligenceInterpretationClient(
            provider="synthetic",
            api_key="unused-with-injected-client",
            base_url="https://llm.example.test",
            model="synthetic-model",
            client=fake_client,
        )
        context = _context()
        expected_rendered = render_career_intelligence_interpretation_request(context)

        result = client.interpret(context)

        self.assertEqual(result.schema_version, CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION)
        self.assertEqual(len(fake_client.chat.completions.calls), 1)
        call = fake_client.chat.completions.calls[0]
        self.assertEqual(call["model"], "synthetic-model")
        self.assertEqual(call["messages"], expected_rendered.messages)
        self.assertEqual(call["response_format"], {"type": "json_object"})
        self.assertFalse(call["stream"])

    def test_malformed_content_and_transport_shape_raise_dedicated_error(self):
        for content in ("not-json", "", None):
            fake_client = _FakeClient(content)
            client = CareerIntelligenceInterpretationClient(
                provider="synthetic",
                api_key="unused",
                base_url="https://llm.example.test",
                model="synthetic-model",
                client=fake_client,
            )
            with self.subTest(content=content):
                with self.assertRaises(CareerIntelligenceInterpretationError):
                    client.interpret(_context())

        malformed_client = CareerIntelligenceInterpretationClient(
            provider="synthetic",
            api_key="unused",
            base_url="https://llm.example.test",
            model="synthetic-model",
            client=_MalformedResponseClient(),
        )
        with self.assertRaises(CareerIntelligenceInterpretationError):
            malformed_client.interpret(_context())

    def test_transport_failure_is_wrapped_without_exposing_provider_message(self):
        client = CareerIntelligenceInterpretationClient(
            provider="synthetic",
            api_key="unused",
            base_url="https://llm.example.test",
            model="synthetic-model",
            client=_FailingClient(),
        )

        with self.assertRaises(CareerIntelligenceInterpretationError) as context:
            client.interpret(_context())

        self.assertNotIn("synthetic transport secret", str(context.exception))

    def test_empty_input_returns_deterministically_without_client_call(self):
        fake_client = _FakeClient("must not be used")
        client = CareerIntelligenceInterpretationClient(
            provider="synthetic",
            api_key="unused",
            base_url="https://llm.example.test",
            model="synthetic-model",
            client=fake_client,
        )

        result = client.interpret(_context(signals=()))

        self.assertEqual(fake_client.chat.completions.calls, [])
        self.assertEqual(
            result,
            CareerIntelligenceInterpretationResult(
                schema_version=CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
                input_signal_ids=(),
                themes=(),
                key_developments=(),
                career_implications=(),
                warnings=(EMPTY_INPUT_WARNING,),
            ),
        )

    def test_missing_live_configuration_fails_before_network(self):
        with self.assertRaises(CareerIntelligenceInterpretationError):
            CareerIntelligenceInterpretationClient(
                provider="synthetic",
                api_key="",
                base_url="https://llm.example.test",
                model="synthetic-model",
            )


if __name__ == "__main__":
    unittest.main()
