import json
import unittest
from pathlib import Path

import src.config as config_module
import src.main as main_module
from src.career_signal_priority import (
    PriorityIntegrationBatchResult,
    _ai_filter_raw_item_fingerprint,
    assess_and_score_career_signal_batch,
    assessment_profile_for_signal,
)
from src.career_signal_scoring import SourceProvenanceInput
from src.models import (
    AIFilterExecutionReport,
    AIFilterResult,
    CareerPathCategory,
    CareerSignal,
    RawItem,
    RawItemFilterStatus,
    SearchAPIExecutionReport,
    SearchScope,
    SignalCategory,
    SourceType,
    TargetCareerPath,
    UserProfile,
)
from src.monitoring_runtime import MonitoringCandidateOutcome, _new_filter_candidates
from src.normalizer import normalize_raw_items_to_career_signals
from src.pipeline import MockPipeline
from src.priority_assessment import (
    AssessmentProfile,
    ComponentStatus,
    PriorityAssessmentResult,
    SemanticComponentResult,
    render_priority_assessment_request,
)
from src.profile_loader import load_user_preferences_from_json


AS_OF = "2026-08-11T00:00:00+00:00"


class RecordingAssessmentClient:
    def __init__(self):
        self.inputs = []

    def assess(self, assessment_input):
        self.inputs.append(assessment_input)
        source_text = ""
        evidence = assessment_input.supporting_source_evidence
        if isinstance(evidence, RawItem):
            source_text = evidence.raw_text
        preferences = assessment_input.user_preferences

        if assessment_input.assessment_profile == AssessmentProfile.OPPORTUNITY:
            conflict = (
                "ad-supported" in source_text
                and "ad-supported" in json.dumps(preferences)
            )
            user_policy_score = 0.0 if conflict else 0.75
            return PriorityAssessmentResult(
                schema_version="priority_assessment_v1",
                signal_id=assessment_input.signal_id,
                assessment_profile=AssessmentProfile.OPPORTUNITY,
                components={
                    "user_policy_fit": SemanticComponentResult(
                        status=ComponentStatus.AVAILABLE,
                        score=user_policy_score,
                        reason="Synthetic policy fit.",
                        evidence=("Synthetic preference evidence.",),
                    ),
                    "opportunity_feasibility": SemanticComponentResult(
                        status=ComponentStatus.AVAILABLE,
                        score=0.75,
                        reason="Synthetic feasibility.",
                        evidence=("Synthetic feasibility evidence.",),
                    ),
                },
                warnings=(),
            )

        return PriorityAssessmentResult(
            schema_version="priority_assessment_v1",
            signal_id=assessment_input.signal_id,
            assessment_profile=AssessmentProfile.INTELLIGENCE,
            components={
                "career_relevance_strength": SemanticComponentResult(
                    status=ComponentStatus.AVAILABLE,
                    score=1.0,
                    reason="Synthetic relevance.",
                    evidence=("Synthetic relevance evidence.",),
                ),
                "signal_significance": SemanticComponentResult(
                    status=ComponentStatus.AVAILABLE,
                    score=0.75,
                    reason="Synthetic significance.",
                    evidence=("Synthetic significance evidence.",),
                ),
            },
            warnings=(),
        )


def make_profile():
    return UserProfile(
        profile_id="profile-1",
        name="Synthetic User",
        background_summary="Synthetic strategy and AI background.",
        skills=["strategy", "AI"],
        preferred_roles=["AI Strategy"],
        preferred_locations=["Remote"],
    )


def make_path(path_id="path-1", fit_score=90.0, path_type="core_match"):
    return TargetCareerPath(
        path_id=path_id,
        title="AI Strategy",
        category=CareerPathCategory.AI_STRATEGY,
        description="Synthetic path.",
        fit_score=fit_score,
        metadata={"path_type": path_type},
    )


def make_raw_item(
    *,
    source_type=SourceType.SEARCH_API,
    title="AI Strategy Role",
    raw_text="Hiring for an AI Strategy role.",
    published_at="2026-08-10T00:00:00+00:00",
):
    return RawItem(
        source_type=source_type,
        title=title,
        organization="Example Co",
        url=f"https://example.com/{title.lower().replace(' ', '-')}",
        published_at=published_at,
        raw_text=raw_text,
        metadata={"source_excerpt": raw_text},
    )


def make_filter_result(
    raw_item,
    *,
    category=SignalCategory.JOB,
    confidence=0.91,
    matched_path_ids=None,
):
    return AIFilterResult(
        raw_item_fingerprint=_ai_filter_raw_item_fingerprint(raw_item),
        title=raw_item.title,
        url=raw_item.url,
        is_relevant=True,
        confidence=confidence,
        reason="Upstream filter reason must not enter semantic prompt.",
        suggested_category=category,
        matched_career_path_ids=matched_path_ids or ["path-1"],
        action="review",
    )


def normalized_signal(raw_item, filter_result):
    return normalize_raw_items_to_career_signals([raw_item], [filter_result])[0]


def score_batch(
    *,
    raw_item=None,
    filter_result=None,
    preferences=None,
    path=None,
    client=None,
    provenance=None,
):
    raw_item = raw_item or make_raw_item()
    filter_result = filter_result or make_filter_result(raw_item)
    signal = normalized_signal(raw_item, filter_result)
    return assess_and_score_career_signal_batch(
        career_signals=(signal,),
        filtered_raw_items=(raw_item,),
        ai_filter_results=(filter_result,),
        user_profile=make_profile(),
        user_preferences=preferences or {
            "business_model_exclusions": ["gambling"],
            "role_preferences": {"preferred": ["AI Strategy"]},
        },
        target_career_paths=(path or make_path(),),
        as_of=AS_OF,
        priority_assessment_client=client or RecordingAssessmentClient(),
        provenance_quality_by_signal_id=(
            {signal.signal_id: provenance}
            if provenance is not None
            else None
        ),
    )


class UserPreferencesRuntimeAuditTests(unittest.TestCase):
    def test_authoritative_runtime_preferences_path_and_loader(self):
        self.assertEqual(
            config_module.USER_PREFERENCES_FILE,
            config_module.INPUT_DIR / "user_preferences_final.json",
        )
        self.assertIs(main_module.USER_PREFERENCES_FILE, config_module.USER_PREFERENCES_FILE)
        self.assertIs(
            main_module.load_user_preferences_from_json,
            load_user_preferences_from_json,
        )

    def test_loader_rejects_missing_file_instead_of_example_fallback(self):
        with self.assertRaises(FileNotFoundError) as context:
            load_user_preferences_from_json(
                Path("missing_user_preferences_final.json")
            )
        self.assertIn("missing_user_preferences_final.json", str(context.exception))

    def test_priority_integration_receives_current_preferences_object(self):
        preferences = {"business_model_exclusions": ["ad-supported"]}
        client = RecordingAssessmentClient()
        score_batch(preferences=preferences, client=client)
        self.assertIs(client.inputs[0].user_preferences, preferences)

    def test_opportunity_payload_preserves_detailed_preference(self):
        preferences = {
            "business_model_exclusions": ["ad-supported"],
            "unrelated_execution_only": "not rendered",
        }
        client = RecordingAssessmentClient()
        score_batch(preferences=preferences, client=client)
        rendered = render_priority_assessment_request(client.inputs[0])
        self.assertIn("business_model_exclusions", rendered.payload["user_preferences"])
        self.assertNotIn("unrelated_execution_only", rendered.payload["user_preferences"])
        self.assertIn("USER PROFILE", rendered.user_prompt)

    def test_intelligence_payload_omits_user_profile(self):
        raw_item = make_raw_item(title="Funding News")
        filter_result = make_filter_result(raw_item, category=SignalCategory.FUNDING)
        client = RecordingAssessmentClient()
        score_batch(raw_item=raw_item, filter_result=filter_result, client=client)
        rendered = render_priority_assessment_request(client.inputs[0])
        self.assertEqual(client.inputs[0].assessment_profile, AssessmentProfile.INTELLIGENCE)
        self.assertNotIn("user_profile", rendered.payload)
        self.assertNotIn("USER PROFILE", rendered.user_prompt)

    def test_preference_change_can_change_final_priority_via_mocked_semantics(self):
        raw_item = make_raw_item(raw_text="Role at an ad-supported AI product.")
        filter_result = make_filter_result(raw_item)
        client_a = RecordingAssessmentClient()
        client_b = RecordingAssessmentClient()
        allowed = score_batch(
            raw_item=raw_item,
            filter_result=filter_result,
            preferences={"business_model_exclusions": ["gambling"]},
            client=client_a,
        )
        excluded = score_batch(
            raw_item=raw_item,
            filter_result=filter_result,
            preferences={"business_model_exclusions": ["ad-supported"]},
            client=client_b,
        )
        self.assertGreater(
            allowed.scored_career_signals[0].priority_score.priority_score,
            excluded.scored_career_signals[0].priority_score.priority_score,
        )


class PriorityIntegrationFlowTests(unittest.TestCase):
    def test_opportunity_flow_scores_canonical_career_signal(self):
        result = score_batch()
        scored = result.scored_career_signals[0]
        self.assertEqual(scored.assessment_profile, AssessmentProfile.OPPORTUNITY)
        self.assertEqual(scored.priority_score.tier.value, "medium_high")
        self.assertEqual(scored.priority_score.matched_path_ids, ("path-1",))

    def test_intelligence_flow_scores_canonical_career_signal(self):
        raw_item = make_raw_item(title="Company Expansion")
        filter_result = make_filter_result(raw_item, category=SignalCategory.COMPANY)
        result = score_batch(raw_item=raw_item, filter_result=filter_result)
        scored = result.scored_career_signals[0]
        self.assertEqual(scored.assessment_profile, AssessmentProfile.INTELLIGENCE)
        self.assertIn("career_relevance_strength", scored.priority_score.components)

    def test_profile_selection_uses_structured_category_not_source_type(self):
        signal = CareerSignal(
            signal_id="signal-1",
            category=SignalCategory.JOB,
            title="Role",
            organization="Example",
            url="https://example.com",
            published_at="2026-08-10T00:00:00+00:00",
            summary="Role",
            source_type=SourceType.RSS,
        )
        self.assertEqual(
            assessment_profile_for_signal(signal),
            AssessmentProfile.OPPORTUNITY,
        )
        signal.category = SignalCategory.UNKNOWN
        self.assertIsNone(assessment_profile_for_signal(signal))

    def test_unknown_category_is_diagnostic_not_guessed(self):
        raw_item = make_raw_item(
            title="Ambiguous Item",
            raw_text="Unclear supplied item.",
        )
        filter_result = make_filter_result(raw_item, category=SignalCategory.UNKNOWN)
        signal = normalized_signal(raw_item, filter_result)
        client = RecordingAssessmentClient()
        result = assess_and_score_career_signal_batch(
            career_signals=(signal,),
            filtered_raw_items=(raw_item,),
            ai_filter_results=(filter_result,),
            user_profile=make_profile(),
            user_preferences={},
            target_career_paths=(make_path(),),
            as_of=AS_OF,
            priority_assessment_client=client,
        )
        self.assertEqual(result.scored_career_signals, ())
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(client.inputs, [])

    def test_filter_confidence_reaches_scoring_not_semantic_prompt(self):
        raw_item = make_raw_item()
        filter_result = make_filter_result(raw_item, confidence=0.91)
        client = RecordingAssessmentClient()
        result = score_batch(raw_item=raw_item, filter_result=filter_result, client=client)
        prompt = render_priority_assessment_request(client.inputs[0]).user_prompt
        self.assertNotIn("0.91", prompt)
        self.assertNotIn(filter_result.reason, prompt)
        component = result.scored_career_signals[0].priority_score.components["ai_confidence"]
        self.assertEqual(component.normalized_score, 0.91)

    def test_source_evidence_and_explicit_as_of_reach_expected_layers(self):
        raw_item = make_raw_item(raw_text="Explicit source evidence.")
        filter_result = make_filter_result(raw_item)
        client = RecordingAssessmentClient()
        result = score_batch(raw_item=raw_item, filter_result=filter_result, client=client)
        self.assertIs(client.inputs[0].supporting_source_evidence, raw_item)
        recency = result.scored_career_signals[0].priority_score.components["recency"]
        self.assertIn(f"as_of={AS_OF}", recency.evidence)

    def test_missing_provenance_remains_unavailable_and_renormalizes(self):
        result = score_batch()
        score = result.scored_career_signals[0].priority_score
        self.assertEqual(score.renormalization_denominator, 95.0)
        self.assertIsNone(
            score.components["source_provenance"].weighted_contribution
        )

    def test_explicit_provenance_can_be_handed_to_scorer(self):
        provenance = SourceProvenanceInput(
            normalized_score=0.8,
            reason="Synthetic source quality.",
            evidence=("Synthetic provenance evidence.",),
        )
        result = score_batch(provenance=provenance)
        source = result.scored_career_signals[0].priority_score.components[
            "source_provenance"
        ]
        self.assertEqual(source.normalized_score, 0.8)

    def test_search_api_and_monitoring_sources_use_same_batch_orchestrator(self):
        search_raw = make_raw_item(source_type=SourceType.SEARCH_API)
        rss_raw = make_raw_item(
            source_type=SourceType.RSS,
            title="Market Trend",
            raw_text="Market trend signal.",
        )
        search_filter = make_filter_result(search_raw, category=SignalCategory.JOB)
        rss_filter = make_filter_result(rss_raw, category=SignalCategory.MARKET_TREND)
        signals = (
            normalized_signal(search_raw, search_filter),
            normalized_signal(rss_raw, rss_filter),
        )
        client = RecordingAssessmentClient()
        result = assess_and_score_career_signal_batch(
            career_signals=signals,
            filtered_raw_items=(search_raw, rss_raw),
            ai_filter_results=(search_filter, rss_filter),
            user_profile=make_profile(),
            user_preferences={},
            target_career_paths=(make_path(),),
            as_of=AS_OF,
            priority_assessment_client=client,
        )
        self.assertEqual(len(result.scored_career_signals), 2)
        self.assertEqual(len(client.inputs), 2)


class RuntimeHookTests(unittest.TestCase):
    def test_mock_pipeline_invokes_priority_assessor_after_normalization(self):
        raw_item = make_raw_item()
        filter_result = make_filter_result(raw_item)
        client = RecordingAssessmentClient()
        captured = {}

        def priority_assessor(**kwargs):
            captured.update(kwargs)
            return assess_and_score_career_signal_batch(
                priority_assessment_client=client,
                **kwargs,
            )

        pipeline = MockPipeline(
            raw_item_loader=lambda: [],
            user_profile_loader=make_profile,
            search_scope_loader=lambda: SearchScope(scope_id="scope", name="Scope"),
            career_path_generator=lambda profile: [make_path()],
            search_query_generator=lambda paths: [],
            search_plan_builder=lambda queries, scope: [],
            search_api_executor=lambda plans: SearchAPIExecutionReport(
                raw_items=[raw_item],
                executed_plan_count=0,
            ),
            rss_executor=lambda scope, plans: ([], 0),
            selected_website_executor=lambda scope, plans: ([], 0),
            ai_filter_executor=lambda raw_items, profile, paths: AIFilterExecutionReport(
                filtered_raw_items=[raw_item],
                ai_filter_results=[filter_result],
                raw_item_statuses=[
                    RawItemFilterStatus(
                        raw_item_fingerprint=filter_result.raw_item_fingerprint,
                        raw_item_index=0,
                        source_type=raw_item.source_type,
                        title=raw_item.title,
                        url=raw_item.url,
                        status="processed_accepted",
                        reason="accepted",
                        is_relevant=True,
                    )
                ],
                executed_count=1,
            ),
            normalizer=normalize_raw_items_to_career_signals,
            user_preferences_loader=lambda: {
                "business_model_exclusions": ["gambling"]
            },
            priority_assessor=priority_assessor,
            priority_as_of_loader=lambda: AS_OF,
        )
        output = pipeline.run()
        self.assertEqual(len(output.career_signals), 1)
        self.assertEqual(len(output.scored_career_signals), 1)
        self.assertEqual(
            len(output.career_signal_routing.opportunities),
            1,
        )
        self.assertEqual(output.career_signal_routing.intelligence, ())
        self.assertEqual(output.career_signal_routing.unrouted, ())
        self.assertEqual(captured["user_preferences"], {"business_model_exclusions": ["gambling"]})
        self.assertEqual(captured["as_of"], AS_OF)

    def test_historical_duplicate_path_produces_no_new_filter_candidate(self):
        raw_item = make_raw_item(source_type=SourceType.RSS)
        outcome = MonitoringCandidateOutcome(
            source_item_id=1,
            fingerprint="fingerprint",
            result_position=0,
            created_new=False,
            historical_duplicate=True,
            filter_eligible=False,
            raw_item=raw_item,
        )
        self.assertEqual(_new_filter_candidates((outcome,)), [])

    def test_batch_with_no_career_signals_makes_no_semantic_call(self):
        client = RecordingAssessmentClient()
        result = assess_and_score_career_signal_batch(
            career_signals=(),
            filtered_raw_items=(),
            ai_filter_results=(),
            user_profile=make_profile(),
            user_preferences={},
            target_career_paths=(make_path(),),
            as_of=AS_OF,
            priority_assessment_client=client,
        )
        self.assertEqual(result, PriorityIntegrationBatchResult(scored_career_signals=()))
        self.assertEqual(client.inputs, [])

    def test_no_priority_score_is_hidden_in_career_signal_metadata(self):
        result = score_batch()
        signal = result.scored_career_signals[0].career_signal
        self.assertNotIn("priority_score", signal.metadata)

    def test_no_llm_or_network_client_is_constructed_by_orchestrator(self):
        text = Path("src/career_signal_priority.py").read_text(encoding="utf-8")
        for forbidden in ("OpenAI(", "requests.", "httpx.", "socket."):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
