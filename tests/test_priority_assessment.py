import json
import unittest

from src.models import (
    CareerPathCategory,
    CareerSignal,
    RawItem,
    SignalCategory,
    SourceType,
    TargetCareerPath,
    UserProfile,
)
from src.priority_assessment import (
    AssessmentProfile,
    ComponentStatus,
    OPPORTUNITY_SYSTEM_PROMPT,
    PriorityAssessmentClient,
    PriorityAssessmentError,
    PriorityAssessmentInput,
    parse_priority_assessment_response,
    render_priority_assessment_request,
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
        self.contents = (
            list(content)
            if isinstance(content, (list, tuple))
            else [content]
        )
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        call_index = len(self.calls) - 1
        if call_index >= len(self.contents):
            raise AssertionError("Unexpected extra fake LLM call.")
        outcome = self.contents[call_index]
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeResponse(outcome)


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content):
        self.chat = _FakeChat(content)


def _career_signal():
    return CareerSignal(
        signal_id="signal-1",
        category=SignalCategory.JOB,
        title="AI Strategy Associate",
        organization="Example Co",
        url="https://example.test/jobs/1",
        published_at="2026-08-10",
        summary="Role focused on AI strategy work for enterprise customers.",
        source_type=SourceType.SEARCH_API,
        relevance_score=0.97,
        metadata={
            "location": "New York",
            "experience_requirements": "2+ years strategy experience",
            "filter_confidence": 0.99,
            "ai_filter_reason": "Looks highly relevant.",
            "action": "accept",
            "unrelated": "do not include",
        },
    )


def _intelligence_signal():
    return CareerSignal(
        signal_id="signal-1",
        category=SignalCategory.COMPANY,
        title="Example Co expands AI advisory practice",
        organization="Example Co",
        url="https://example.test/news/ai-practice",
        published_at="2026-08-10",
        summary=(
            "Example Co announced a new AI advisory practice and plans to "
            "hire strategy and implementation teams."
        ),
        source_type=SourceType.SEARCH_API,
        relevance_score=0.93,
        metadata={
            "event_type": "practice_expansion",
            "signal_details": "New AI advisory practice with planned hiring.",
            "filter_confidence": 0.99,
            "ai_filter_reason": "Looks highly relevant.",
            "action": "accept",
        },
    )


def _user_profile():
    return UserProfile(
        profile_id="profile-1",
        name="Synthetic User",
        background_summary="Strategy analyst with applied AI project work.",
        education=[{"degree": "MBA"}],
        work_experience=[{"role": "Strategy Analyst", "years": 3}],
        skills=["strategy", "AI", "market research"],
        preferred_locations=["New York", "Remote"],
        preferred_roles=["AI Strategy Associate"],
        constraints=["No relocation"],
        raw_resume_text="private resume text should not be rendered",
    )


def _target_paths():
    return (
        TargetCareerPath(
            path_id="path-1",
            title="AI Strategy",
            category=CareerPathCategory.AI_STRATEGY,
            description="Strategy roles involving applied AI adoption.",
            fit_score=0.91,
            metadata={
                "path_type": "primary",
                "risk_flags": ["travel-heavy roles"],
            },
        ),
        TargetCareerPath(
            path_id="path-2",
            title="Venture Capital",
            category=CareerPathCategory.VENTURE_CAPITAL,
            description="Investment roles around technology companies.",
            fit_score=0.67,
        ),
    )


def _preferences():
    return {
        "hard_constraints": ["No relocation"],
        "location_preferences": ["New York", "Remote"],
        "seniority_preferences": ["associate"],
        "industry_preferences": ["AI software"],
        "business_model_exclusions": ["crypto"],
        "work_authorization": {"requires_sponsorship": False},
        "work_style_preferences": {"research_preference": "high"},
        "private_notes": "do not include",
        "recommendation_explanation_preferences": {
            "show_recommendation_reason": True,
        },
    }


def _source_evidence():
    return RawItem(
        source_type=SourceType.SEARCH_API,
        title="AI Strategy Associate",
        organization="Example Co",
        url="https://example.test/jobs/1",
        published_at="2026-08-10",
        raw_text="Hiring page says New York or remote and 2+ years required.",
        metadata={
            "location": "New York or Remote",
            "compensation": "$100k-$130k",
            "ranking_score": 999,
        },
    )


def _intelligence_source_evidence():
    return RawItem(
        source_type=SourceType.SEARCH_API,
        title="Example Co expands AI advisory practice",
        organization="Example Co",
        url="https://example.test/news/ai-practice",
        published_at="2026-08-10",
        raw_text=(
            "Example Co announced an AI advisory practice with planned "
            "strategy and implementation hiring."
        ),
        metadata={
            "event_type": "practice_expansion",
            "signal_details": "Specific expansion and hiring-capability event.",
            "ranking_score": 999,
        },
    )


def _assessment_input(profile=AssessmentProfile.OPPORTUNITY, as_of="2026-08-11"):
    career_signal = (
        _intelligence_signal()
        if profile == AssessmentProfile.INTELLIGENCE
        else _career_signal()
    )
    source_evidence = (
        _intelligence_source_evidence()
        if profile == AssessmentProfile.INTELLIGENCE
        else _source_evidence()
    )
    return PriorityAssessmentInput(
        assessment_profile=profile,
        signal_id="signal-1",
        as_of=as_of,
        career_signal=career_signal,
        matched_career_path_ids=("path-1",),
        target_career_paths=_target_paths(),
        user_preferences=_preferences(),
        user_profile=_user_profile(),
        supporting_source_evidence=source_evidence,
    )


def _component(status="available", score=0.75, evidence=None):
    return {
        "status": status,
        "score": score,
        "reason": "Supported by supplied synthetic evidence.",
        "evidence": evidence or ["Synthetic evidence line"],
    }


def _response(profile, components, warnings=None, signal_id="signal-1"):
    return json.dumps(
        {
            "schema_version": "priority_assessment_v1",
            "signal_id": signal_id,
            "assessment_profile": profile,
            "components": components,
            "warnings": warnings or [],
        }
    )


def _opportunity_response(**overrides):
    components = {
        "user_policy_fit": _component(score=0.75),
        "opportunity_feasibility": _component(score=0.5),
    }
    components.update(overrides)
    return _response("opportunity", components)


def _intelligence_response(**overrides):
    components = {
        "career_relevance_strength": _component(score=1.0),
        "signal_significance": _component(score=0.75),
    }
    components.update(overrides)
    return _response("intelligence", components)


class PriorityAssessmentValidationTests(unittest.TestCase):
    def test_accepts_only_allowed_discrete_scores(self):
        for score in (0, 0.25, 0.5, 0.75, 1):
            with self.subTest(score=score):
                result = parse_priority_assessment_response(
                    _opportunity_response(user_policy_fit=_component(score=score)),
                    expected_signal_id="signal-1",
                    expected_profile=AssessmentProfile.OPPORTUNITY,
                )
                self.assertEqual(
                    result.components["user_policy_fit"].score,
                    float(score),
                )

    def test_rejects_intermediate_and_out_of_range_scores(self):
        for score in (0.8, -0.25, 1.25, True):
            with self.subTest(score=score):
                with self.assertRaises(PriorityAssessmentError):
                    parse_priority_assessment_response(
                        _opportunity_response(
                            user_policy_fit=_component(score=score)
                        ),
                        expected_signal_id="signal-1",
                        expected_profile=AssessmentProfile.OPPORTUNITY,
                    )

    def test_rejects_string_scores(self):
        for score in ("0", "0.25", "0.50", "0.75", "1", "1.00"):
            with self.subTest(score=score):
                with self.assertRaises(PriorityAssessmentError):
                    parse_priority_assessment_response(
                        _opportunity_response(
                            user_policy_fit=_component(score=score)
                        ),
                        expected_signal_id="signal-1",
                        expected_profile=AssessmentProfile.OPPORTUNITY,
                    )

    def test_available_component_requires_score_reason_and_evidence(self):
        invalid_components = (
            {"status": "available", "score": None, "reason": "x", "evidence": ["x"]},
            {"status": "available", "score": 0.5, "reason": "", "evidence": ["x"]},
            {"status": "available", "score": 0.5, "reason": "x", "evidence": []},
        )
        for component in invalid_components:
            with self.subTest(component=component):
                with self.assertRaises(PriorityAssessmentError):
                    parse_priority_assessment_response(
                        _opportunity_response(user_policy_fit=component),
                        expected_signal_id="signal-1",
                        expected_profile="opportunity",
                    )

    def test_unavailable_component_requires_null_score_and_empty_evidence(self):
        response = _opportunity_response(
            user_policy_fit={
                "status": "unavailable",
                "score": None,
                "reason": "Insufficient supplied evidence.",
                "evidence": [],
            }
        )
        result = parse_priority_assessment_response(
            response,
            expected_signal_id="signal-1",
            expected_profile="opportunity",
        )

        component = result.components["user_policy_fit"]
        self.assertEqual(component.status, ComponentStatus.UNAVAILABLE)
        self.assertIsNone(component.score)
        self.assertEqual(component.evidence, ())

        for component in (
            {
                "status": "unavailable",
                "score": 0,
                "reason": "Insufficient supplied evidence.",
                "evidence": [],
            },
            {
                "status": "unavailable",
                "score": None,
                "reason": "Insufficient supplied evidence.",
                "evidence": ["should be empty"],
            },
        ):
            with self.subTest(component=component):
                with self.assertRaises(PriorityAssessmentError):
                    parse_priority_assessment_response(
                        _opportunity_response(user_policy_fit=component),
                        expected_signal_id="signal-1",
                        expected_profile="opportunity",
                    )

    def test_parser_rejects_unavailable_component_with_evidence(self):
        malformed = {
            "status": "unavailable",
            "score": None,
            "reason": "Insufficient supplied evidence.",
            "evidence": ["something"],
        }

        with self.assertRaises(PriorityAssessmentError):
            parse_priority_assessment_response(
                _opportunity_response(user_policy_fit=malformed),
                expected_signal_id="signal-1",
                expected_profile="opportunity",
            )

    def test_profile_components_must_match_opportunity_exactly(self):
        parse_priority_assessment_response(
            _opportunity_response(),
            expected_signal_id="signal-1",
            expected_profile="opportunity",
        )

        with self.assertRaises(PriorityAssessmentError):
            parse_priority_assessment_response(
                _opportunity_response(career_relevance_strength=_component()),
                expected_signal_id="signal-1",
                expected_profile="opportunity",
            )

    def test_profile_components_must_match_intelligence_exactly(self):
        parse_priority_assessment_response(
            _intelligence_response(),
            expected_signal_id="signal-1",
            expected_profile="intelligence",
        )

        with self.assertRaises(PriorityAssessmentError):
            parse_priority_assessment_response(
                _intelligence_response(user_policy_fit=_component()),
                expected_signal_id="signal-1",
                expected_profile="intelligence",
            )

    def test_rejects_malformed_json_missing_fields_mismatches_and_priority_score(self):
        invalid_payloads = (
            "{not-json",
            json.dumps([]),
            json.dumps({"schema_version": "priority_assessment_v1"}),
            _response("opportunity", {"user_policy_fit": _component()}),
            _response(
                "opportunity",
                {
                    "user_policy_fit": _component(),
                    "opportunity_feasibility": _component(),
                },
                signal_id="other-signal",
            ),
            json.dumps(
                {
                    "schema_version": "priority_assessment_v1",
                    "signal_id": "signal-1",
                    "assessment_profile": "opportunity",
                    "components": {
                        "user_policy_fit": _component(),
                        "opportunity_feasibility": _component(),
                    },
                    "warnings": [],
                    "priority_score": 0.9,
                }
            ),
            _opportunity_response(
                user_policy_fit={**_component(), "recommendation": "keep"}
            ),
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(PriorityAssessmentError):
                    parse_priority_assessment_response(
                        payload,
                        expected_signal_id="signal-1",
                        expected_profile="opportunity",
                    )

    def test_rejects_schema_version_aliases(self):
        for schema_version in (
            "hybrid_priority_assessment_v1",
            "career_signal_priority_v1",
            "priority_assessment_v2",
            "",
        ):
            with self.subTest(schema_version=schema_version):
                payload = json.loads(_opportunity_response())
                payload["schema_version"] = schema_version

                with self.assertRaises(PriorityAssessmentError):
                    parse_priority_assessment_response(
                        json.dumps(payload),
                        expected_signal_id="signal-1",
                        expected_profile="opportunity",
                    )


class PriorityAssessmentRenderingTests(unittest.TestCase):
    def test_opportunity_prompt_includes_required_context_only(self):
        rendered = render_priority_assessment_request(_assessment_input())
        prompt = rendered.user_prompt

        self.assertIn("USER PROFILE", prompt)
        self.assertIn("USER PREFERENCES", prompt)
        self.assertIn("MATCHED TARGET CAREER PATHS", prompt)
        self.assertIn("CAREER SIGNAL", prompt)
        self.assertIn("SUPPORTING SOURCE EVIDENCE", prompt)
        self.assertIn("AI Strategy", prompt)
        self.assertNotIn("Venture Capital", prompt)
        self.assertIn("Hiring page says New York or remote", prompt)
        self.assertNotIn("raw_resume_text", prompt)
        self.assertNotIn("private resume text", prompt)
        self.assertNotIn("private_notes", prompt)
        self.assertIn("business_model_exclusions", prompt)
        self.assertIn("work_authorization", prompt)
        self.assertIn("work_style_preferences", prompt)
        self.assertNotIn("recommendation_explanation_preferences", prompt)

    def test_intelligence_prompt_omits_user_profile_by_default(self):
        rendered = render_priority_assessment_request(
            _assessment_input(profile=AssessmentProfile.INTELLIGENCE)
        )

        self.assertNotIn("USER PROFILE", rendered.user_prompt)
        self.assertNotIn("background_summary", rendered.user_prompt)
        self.assertIn("USER PREFERENCES", rendered.user_prompt)
        self.assertIn("MATCHED TARGET CAREER PATHS", rendered.user_prompt)
        self.assertIn("CAREER SIGNAL", rendered.user_prompt)
        self.assertIn("SUPPORTING SOURCE EVIDENCE", rendered.user_prompt)
        self.assertIn("Example Co expands AI advisory practice", rendered.user_prompt)
        self.assertIn("practice_expansion", rendered.user_prompt)
        self.assertIn("business_model_exclusions", rendered.user_prompt)
        self.assertNotIn("USER PROFILE", rendered.user_prompt)
        self.assertNotIn("work_authorization", rendered.user_prompt)
        self.assertNotIn("work_style_preferences", rendered.user_prompt)

    def test_prompt_omits_upstream_filter_and_relevance_anchoring(self):
        rendered_requests = (
            render_priority_assessment_request(_assessment_input()),
            render_priority_assessment_request(
                _assessment_input(profile=AssessmentProfile.INTELLIGENCE)
            ),
        )

        for forbidden in (
            "relevance_score",
            "filter_confidence",
            "ai_filter_reason",
            "Looks highly relevant",
            '"action"',
            "ranking_score",
        ):
            for rendered in rendered_requests:
                with self.subTest(forbidden=forbidden):
                    serialized = json.dumps(rendered.payload, sort_keys=True)
                    self.assertNotIn(forbidden, serialized)
                    self.assertNotIn(forbidden, rendered.user_prompt)

    def test_prompts_state_scoring_boundaries_and_stage_boundaries(self):
        opportunity = render_priority_assessment_request(_assessment_input())
        intelligence = render_priority_assessment_request(
            _assessment_input(profile=AssessmentProfile.INTELLIGENCE)
        )

        self.assertIn("0.00\n0.25\n0.50\n0.75\n1.00", OPPORTUNITY_SYSTEM_PROMPT)
        self.assertIn("return that dimension as unavailable", opportunity.system_prompt)
        self.assertIn("NOT to calculate the final Priority Score", opportunity.system_prompt)
        self.assertIn("NOT to redo CareerPath matching", opportunity.system_prompt)
        self.assertIn("Do not calculate priority_score", opportunity.user_prompt)
        self.assertIn("You are evaluating ONE CareerSignal only", intelligence.system_prompt)
        self.assertIn("Do not synthesize across signals", intelligence.user_prompt)
        self.assertIn("Do not produce an overall score", intelligence.system_prompt)

    def test_opportunity_prompt_requires_exact_schema_version_literal(self):
        rendered = render_priority_assessment_request(_assessment_input())

        self.assertIn('"schema_version": "priority_assessment_v1"', rendered.user_prompt)
        self.assertIn('"assessment_profile": "opportunity"', rendered.user_prompt)
        self.assertIn('"user_policy_fit"', rendered.user_prompt)
        self.assertIn('"opportunity_feasibility"', rendered.user_prompt)

    def test_intelligence_prompt_requires_exact_schema_version_literal(self):
        rendered = render_priority_assessment_request(
            _assessment_input(profile=AssessmentProfile.INTELLIGENCE)
        )

        self.assertIn('"schema_version": "priority_assessment_v1"', rendered.user_prompt)
        self.assertIn('"assessment_profile": "intelligence"', rendered.user_prompt)
        self.assertIn('"career_relevance_strength"', rendered.user_prompt)
        self.assertIn('"signal_significance"', rendered.user_prompt)

    def test_opportunity_prompt_explicitly_distinguishes_component_forms(self):
        rendered = render_priority_assessment_request(_assessment_input())
        prompt = rendered.user_prompt

        self.assertIn("VALID COMPONENT FORM WHEN AVAILABLE", prompt)
        self.assertIn('"status": "available"', prompt)
        self.assertIn('"score": 0.75', prompt)
        self.assertIn("Allowed score values are exactly: 0, 0.25, 0.5, 0.75, 1.", prompt)
        self.assertIn("The score MUST be a JSON NUMBER.", prompt)
        self.assertIn('Never return string score values such as "0"', prompt)
        self.assertNotIn('"score": "0 | 0.25 | 0.5 | 0.75 | 1"', prompt)
        self.assertIn('"evidence": [\n    "specific supplied evidence"\n  ]', prompt)
        self.assertIn("VALID COMPONENT FORM WHEN UNAVAILABLE", prompt)
        self.assertIn('"status": "unavailable"', prompt)
        self.assertIn('"score": null', prompt)
        self.assertIn('"evidence": []', prompt)

    def test_intelligence_prompt_explicitly_distinguishes_component_forms(self):
        rendered = render_priority_assessment_request(
            _assessment_input(profile=AssessmentProfile.INTELLIGENCE)
        )
        prompt = rendered.user_prompt

        self.assertIn("VALID COMPONENT FORM WHEN AVAILABLE", prompt)
        self.assertIn('"status": "available"', prompt)
        self.assertIn('"score": 0.75', prompt)
        self.assertIn("Allowed score values are exactly: 0, 0.25, 0.5, 0.75, 1.", prompt)
        self.assertIn("The score MUST be a JSON NUMBER.", prompt)
        self.assertIn('Never return string score values such as "0"', prompt)
        self.assertNotIn('"score": "0 | 0.25 | 0.5 | 0.75 | 1"', prompt)
        self.assertIn('"evidence": [\n    "specific supplied evidence"\n  ]', prompt)
        self.assertIn("VALID COMPONENT FORM WHEN UNAVAILABLE", prompt)
        self.assertIn('"status": "unavailable"', prompt)
        self.assertIn('"score": null', prompt)
        self.assertIn('"evidence": []', prompt)

    def test_opportunity_prompt_preserves_complete_contract_boundaries(self):
        rendered = render_priority_assessment_request(_assessment_input())
        prompt = rendered.user_prompt

        for expected in (
            "This dimension may consider supplied evidence related to:",
            "- business model;",
            "This dimension does NOT evaluate:",
            "- whether the user is qualified;",
            "- CareerPath priority;",
            "- recency;",
            "- source quality;",
            "- company prestige based on outside knowledge.",
            "This score affects the normal weighted scoring calculation.",
            "It does NOT trigger a separate final-score override or cap.",
            "Possible evidence may include:",
            "- work authorization;",
            "This dimension evaluates reasonable application feasibility.",
            "It does NOT estimate:",
            "- probability of receiving an offer;",
            "- user preference;",
            "- career-path importance;",
            "- long-term career upside.",
            "Job title alone is not sufficient evidence for qualification requirements.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, prompt)

    def test_intelligence_prompt_preserves_complete_contract_boundaries(self):
        rendered = render_priority_assessment_request(
            _assessment_input(profile=AssessmentProfile.INTELLIGENCE)
        )
        prompt = rendered.user_prompt

        for expected in (
            "This differs from Path Alignment.",
            "Path Alignment answers:",
            '"Which TargetCareerPath does this signal relate to, and how strong is that path for the user?"',
            "Career Relevance Strength answers:",
            '"How much does this specific event actually matter for that career direction?"',
            "Relevant evidence may concern:",
            "- capability expansion;",
            "- organizational strategy;",
            "- investment direction;",
            "- talent structure;",
            "This dimension evaluates the signal itself.",
            "It does NOT evaluate whether the user personally cares about it.",
            "A clear and substantial event or change, such as:",
            "- major new capability/practice.",
            "Important boundary:",
            "It must NOT infer broader trends from multiple signals.",
            "Cross-signal synthesis belongs to downstream LLM Interpretation.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, prompt)

    def test_rendering_is_deterministic_and_does_not_include_as_of(self):
        first = render_priority_assessment_request(
            _assessment_input(as_of="2026-08-11")
        )
        second = render_priority_assessment_request(
            _assessment_input(as_of="2027-01-01")
        )

        self.assertEqual(first.user_prompt, second.user_prompt)
        self.assertEqual(first.payload, second.payload)
        self.assertNotIn("2026-08-11", first.user_prompt)
        self.assertNotIn("2027-01-01", second.user_prompt)

    def test_unresolved_matched_path_ids_become_warnings_not_extra_payload(self):
        assessment_input = PriorityAssessmentInput(
            assessment_profile=AssessmentProfile.INTELLIGENCE,
            signal_id="signal-1",
            as_of="2026-08-11",
            career_signal=_career_signal(),
            matched_career_path_ids=("path-1", "missing-path"),
            target_career_paths=_target_paths(),
            user_preferences=_preferences(),
            supporting_source_evidence=_source_evidence(),
        )

        rendered = render_priority_assessment_request(assessment_input)

        self.assertEqual(
            rendered.warnings,
            ("unresolved_matched_career_path_id:missing-path",),
        )
        self.assertEqual(
            [path["path_id"] for path in rendered.payload["matched_target_career_paths"]],
            ["path-1"],
        )


class PriorityAssessmentClientTests(unittest.TestCase):
    def test_client_invokes_openai_compatible_json_mode_once(self):
        fake_client = _FakeClient(_opportunity_response())
        client = PriorityAssessmentClient(
            provider="deepseek",
            api_key="unused-with-injected-client",
            base_url="https://llm.example.test",
            model="deepseek-chat",
            client=fake_client,
        )

        result = client.assess(_assessment_input())

        self.assertEqual(result.signal_id, "signal-1")
        self.assertEqual(len(fake_client.chat.completions.calls), 1)
        call = fake_client.chat.completions.calls[0]
        self.assertEqual(call["model"], "deepseek-chat")
        self.assertEqual(call["response_format"], {"type": "json_object"})
        self.assertFalse(call["stream"])
        self.assertEqual(len(call["messages"]), 2)
        self.assertEqual(call["messages"][0]["role"], "system")
        self.assertEqual(call["messages"][1]["role"], "user")

    def test_client_retries_live_unavailable_evidence_shape_once(self):
        invalid_response = _opportunity_response(
            user_policy_fit={
                "status": "unavailable",
                "score": None,
                "reason": "Insufficient evidence.",
                "evidence": ["invalid evidence"],
            }
        )
        valid_response = _opportunity_response(
            user_policy_fit={
                "status": "unavailable",
                "score": None,
                "reason": "Insufficient evidence.",
                "evidence": [],
            }
        )
        fake_client = _FakeClient([invalid_response, valid_response])
        client = PriorityAssessmentClient(
            provider="deepseek",
            api_key="unused-with-injected-client",
            base_url="https://llm.example.test",
            model="deepseek-chat",
            client=fake_client,
        )

        result = client.assess(_assessment_input())

        self.assertEqual(len(fake_client.chat.completions.calls), 2)
        component = result.components["user_policy_fit"]
        self.assertEqual(component.status, ComponentStatus.UNAVAILABLE)
        self.assertIsNone(component.score)
        self.assertEqual(component.evidence, ())

        first_messages = fake_client.chat.completions.calls[0]["messages"]
        retry_messages = fake_client.chat.completions.calls[1]["messages"]
        self.assertEqual(retry_messages[0], first_messages[0])
        self.assertTrue(
            retry_messages[1]["content"].startswith(
                first_messages[1]["content"]
            )
        )
        retry_instruction = retry_messages[1]["content"][
            len(first_messages[1]["content"]):
        ]
        self.assertIn(
            "previous response failed strict Priority Assessment output validation",
            retry_instruction,
        )
        self.assertIn(
            "Component 'user_policy_fit' must not include evidence when unavailable.",
            retry_instruction,
        )
        self.assertIn(
            "Regenerate the COMPLETE response from the original supplied context.",
            retry_instruction,
        )
        self.assertIn(
            'score = one JSON number from exactly {0, 0.25, 0.5, 0.75, 1}',
            retry_instruction,
        )
        self.assertIn("score = null", retry_instruction)
        self.assertIn("evidence = []", retry_instruction)
        self.assertIn("Return JSON only.", retry_instruction)
        self.assertIn("AI Strategy Associate", retry_messages[1]["content"])
        self.assertIn("USER PROFILE", retry_messages[1]["content"])
        self.assertIn("USER PREFERENCES", retry_messages[1]["content"])

    def test_client_raises_after_exactly_one_failed_corrective_retry(self):
        invalid_response = _opportunity_response(
            user_policy_fit={
                "status": "unavailable",
                "score": None,
                "reason": "Insufficient evidence.",
                "evidence": ["invalid evidence"],
            }
        )
        fake_client = _FakeClient([invalid_response, invalid_response])
        client = PriorityAssessmentClient(
            provider="deepseek",
            api_key="unused-with-injected-client",
            base_url="https://llm.example.test",
            model="deepseek-chat",
            client=fake_client,
        )

        with self.assertRaisesRegex(
            PriorityAssessmentError,
            "must not include evidence when unavailable",
        ):
            client.assess(_assessment_input())

        self.assertEqual(len(fake_client.chat.completions.calls), 2)

    def test_client_does_not_retry_transport_failure(self):
        fake_client = _FakeClient(RuntimeError("provider unavailable"))
        client = PriorityAssessmentClient(
            provider="deepseek",
            api_key="unused-with-injected-client",
            base_url="https://llm.example.test",
            model="deepseek-chat",
            client=fake_client,
        )

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            client.assess(_assessment_input())

        self.assertEqual(len(fake_client.chat.completions.calls), 1)

    def test_client_merges_rendering_warnings_into_result(self):
        fake_client = _FakeClient(_intelligence_response())
        client = PriorityAssessmentClient(
            provider="mock",
            api_key="unused",
            base_url="https://llm.example.test",
            model="mock-model",
            client=fake_client,
        )
        assessment_input = PriorityAssessmentInput(
            assessment_profile=AssessmentProfile.INTELLIGENCE,
            signal_id="signal-1",
            as_of="2026-08-11",
            career_signal=_career_signal(),
            matched_career_path_ids=("missing-path",),
            target_career_paths=_target_paths(),
            user_preferences=_preferences(),
            supporting_source_evidence=_source_evidence(),
        )

        result = client.assess(assessment_input)

        self.assertEqual(
            result.warnings,
            ("unresolved_matched_career_path_id:missing-path",),
        )


if __name__ == "__main__":
    unittest.main()
