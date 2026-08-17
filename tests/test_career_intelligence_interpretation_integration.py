import hashlib
import unittest

from src.career_intelligence_interpretation import (
    CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
    EMPTY_INPUT_WARNING,
    CareerIntelligenceInterpretationError,
    CareerIntelligenceInterpretationResult,
    InterpretationConfidence,
    KeyDevelopmentInterpretation,
)
from src.career_intelligence_interpretation_runtime import (
    interpret_routed_intelligence,
)
from src.career_signal_priority import (
    PriorityIntegrationBatchResult,
    ScoredCareerSignal,
)
from src.career_signal_routing import route_scored_career_signals
from src.career_signal_scoring import PriorityScoreResult, PriorityTier
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
from src.normalizer import normalize_raw_items_to_career_signals
from src.pipeline import MockPipeline
from src.priority_assessment import (
    AssessmentProfile,
    ComponentStatus,
    PriorityAssessmentResult,
    SemanticComponentResult,
)


def make_path(path_id="path-stage4c"):
    return TargetCareerPath(
        path_id=path_id,
        title="AI Strategy",
        category=CareerPathCategory.AI_STRATEGY,
        description="Synthetic career path.",
        fit_score=0.9,
        keywords=["AI", "strategy"],
    )


def make_profile():
    return UserProfile(
        profile_id="profile-stage4c",
        name="Synthetic User",
        background_summary="Synthetic strategy background.",
        skills=["strategy"],
    )


def make_signal(signal_id, category):
    return CareerSignal(
        signal_id=signal_id,
        category=category,
        title=f"Synthetic {signal_id}",
        organization="Example Organization",
        url=f"https://example.com/{signal_id}",
        published_at="2026-08-12T00:00:00+00:00",
        summary=f"Synthetic evidence for {signal_id}.",
        source_type=SourceType.SEARCH_API,
        metadata={"matched_career_path_ids": ["path-stage4c"]},
    )


def make_scored_signal(signal_id, category):
    profile = (
        AssessmentProfile.OPPORTUNITY
        if category == SignalCategory.JOB
        else AssessmentProfile.INTELLIGENCE
    )
    signal = make_signal(signal_id, category)
    components = (
        {
            "user_policy_fit": SemanticComponentResult(
                status=ComponentStatus.AVAILABLE,
                score=0.75,
                reason="Synthetic policy fit.",
                evidence=("Synthetic policy evidence.",),
            ),
            "opportunity_feasibility": SemanticComponentResult(
                status=ComponentStatus.AVAILABLE,
                score=0.75,
                reason="Synthetic feasibility.",
                evidence=("Synthetic feasibility evidence.",),
            ),
        }
        if profile == AssessmentProfile.OPPORTUNITY
        else {}
    )
    return ScoredCareerSignal(
        career_signal=signal,
        priority_assessment=PriorityAssessmentResult(
            schema_version="priority_assessment_v1",
            signal_id=signal_id,
            assessment_profile=profile,
            components=components,
            warnings=(),
        ),
        priority_score=PriorityScoreResult(
            signal_id=signal_id,
            priority_score=82.0,
            tier=PriorityTier.MEDIUM_HIGH,
            profile=profile,
            components={},
            matched_path_ids=("path-stage4c",),
            policy_version="synthetic_policy",
            renormalization_denominator=1.0,
            warnings=(),
        ),
        assessment_profile=profile,
    )


def make_interpretation_result(signal_ids):
    signal_ids = tuple(signal_ids)
    return CareerIntelligenceInterpretationResult(
        schema_version=CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
        input_signal_ids=signal_ids,
        themes=(),
        key_developments=(
            KeyDevelopmentInterpretation(
                title="Synthetic development",
                summary="A synthetic development grounded in the batch.",
                why_it_matters="It is relevant to the supplied career path.",
                supporting_signal_ids=(signal_ids[0],),
                confidence=InterpretationConfidence.MEDIUM,
            ),
        ),
        career_implications=(),
        warnings=(),
    )


class RecordingInterpretationExecutor:
    def __init__(self, error=None):
        self.contexts = []
        self.error = error

    def __call__(self, context):
        self.contexts.append(context)
        if self.error is not None:
            raise self.error
        signal_ids = tuple(
            item.career_signal.signal_id
            for item in context.intelligence_signals
        )
        return make_interpretation_result(signal_ids)


def _filter_fingerprint(raw_item):
    value = (
        f"{raw_item.source_type.value}|{raw_item.title}|{raw_item.url}|"
        f"{raw_item.raw_text[:200]}"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def make_pipeline(
    categories,
    *,
    interpretation_executor,
    user_preferences=None,
    target_career_paths=None,
):
    preferences = user_preferences if user_preferences is not None else {}
    paths = target_career_paths if target_career_paths is not None else [make_path()]
    raw_items = [
        RawItem(
            source_type=SourceType.SEARCH_API,
            title=f"Synthetic item {index}",
            organization="Example Organization",
            url=f"https://example.com/item-{index}",
            published_at="2026-08-12T00:00:00+00:00",
            raw_text=f"Synthetic source evidence {index}.",
            metadata={"source_excerpt": f"Synthetic evidence {index}."},
        )
        for index, _ in enumerate(categories)
    ]
    filter_results = [
        AIFilterResult(
            raw_item_fingerprint=_filter_fingerprint(raw_item),
            title=raw_item.title,
            url=raw_item.url,
            is_relevant=True,
            confidence=0.9,
            reason="Synthetic accepted result.",
            suggested_category=category,
            matched_career_path_ids=[paths[0].path_id],
            action="keep",
        )
        for raw_item, category in zip(raw_items, categories)
    ]
    statuses = [
        RawItemFilterStatus(
            raw_item_fingerprint=result.raw_item_fingerprint,
            raw_item_index=index,
            source_type=raw_item.source_type,
            title=raw_item.title,
            url=raw_item.url,
            status="processed_accepted",
            reason="accepted",
            is_relevant=True,
        )
        for index, (raw_item, result) in enumerate(
            zip(raw_items, filter_results)
        )
    ]

    def priority_assessor(**kwargs):
        return PriorityIntegrationBatchResult(
            scored_career_signals=tuple(
                make_scored_signal(signal.signal_id, signal.category)
                for signal in kwargs["career_signals"]
            )
        )

    pipeline = MockPipeline(
        raw_item_loader=lambda: [],
        user_profile_loader=make_profile,
        search_scope_loader=lambda: SearchScope(
            scope_id="scope-stage4c",
            name="Stage 4C Scope",
        ),
        career_path_generator=lambda profile: paths,
        search_query_generator=lambda current_paths: [],
        search_plan_builder=lambda queries, scope: [],
        search_api_executor=lambda plans: SearchAPIExecutionReport(
            raw_items=raw_items,
            executed_plan_count=0,
        ),
        rss_executor=lambda scope, plans: ([], 0),
        selected_website_executor=lambda scope, plans: ([], 0),
        ai_filter_executor=lambda items, profile, current_paths: AIFilterExecutionReport(
            filtered_raw_items=raw_items,
            ai_filter_results=filter_results,
            raw_item_statuses=statuses,
            executed_count=len(raw_items),
        ),
        normalizer=normalize_raw_items_to_career_signals,
        user_preferences_loader=lambda: preferences,
        priority_assessor=priority_assessor,
        interpretation_executor=interpretation_executor,
    )
    return pipeline, preferences, paths


class SharedInterpretationBoundaryTests(unittest.TestCase):
    def test_call_count_is_batch_based_for_zero_one_and_three_signals(self):
        for count, expected_calls in ((0, 0), (1, 1), (3, 1)):
            with self.subTest(count=count):
                executor = RecordingInterpretationExecutor()
                routing = route_scored_career_signals(
                    tuple(
                        make_scored_signal(
                            f"signal-{index}",
                            SignalCategory.NEWS,
                        )
                        for index in range(count)
                    )
                )
                result = interpret_routed_intelligence(
                    routing_result=routing,
                    target_career_paths=(make_path(),),
                    user_preferences={},
                    interpretation_executor=executor,
                )
                self.assertEqual(len(executor.contexts), expected_calls)
                self.assertEqual(len(result.input_signal_ids), count)
                if count == 0:
                    self.assertEqual(result.warnings, (EMPTY_INPUT_WARNING,))

    def test_only_intelligence_and_current_context_reach_executor(self):
        opportunity = make_scored_signal("opportunity-1", SignalCategory.JOB)
        intelligence = (
            make_scored_signal("intelligence-1", SignalCategory.NEWS),
            make_scored_signal("intelligence-2", SignalCategory.FUNDING),
        )
        preferences = {"role_preferences": {"preferred": ["AI Strategy"]}}
        paths = [make_path()]
        executor = RecordingInterpretationExecutor()
        routing = route_scored_career_signals((opportunity, *intelligence))

        result = interpret_routed_intelligence(
            routing_result=routing,
            target_career_paths=paths,
            user_preferences=preferences,
            interpretation_executor=executor,
        )

        self.assertEqual(routing.opportunities, (opportunity,))
        self.assertEqual(executor.contexts[0].intelligence_signals, intelligence)
        self.assertIs(executor.contexts[0].user_preferences, preferences)
        self.assertIs(executor.contexts[0].target_career_paths[0], paths[0])
        self.assertFalse(hasattr(executor.contexts[0], "user_profile"))
        self.assertEqual(
            result.input_signal_ids,
            ("intelligence-1", "intelligence-2"),
        )

    def test_nonempty_batch_requires_configured_executor(self):
        routing = route_scored_career_signals(
            (make_scored_signal("intelligence-1", SignalCategory.NEWS),)
        )
        with self.assertRaises(CareerIntelligenceInterpretationError):
            interpret_routed_intelligence(
                routing_result=routing,
                target_career_paths=(make_path(),),
                user_preferences={},
                interpretation_executor=None,
            )

    def test_executor_error_is_not_replaced_with_empty_success(self):
        error = CareerIntelligenceInterpretationError("Malformed live response.")
        executor = RecordingInterpretationExecutor(error=error)
        routing = route_scored_career_signals(
            (make_scored_signal("intelligence-1", SignalCategory.NEWS),)
        )
        with self.assertRaises(CareerIntelligenceInterpretationError) as context:
            interpret_routed_intelligence(
                routing_result=routing,
                target_career_paths=(make_path(),),
                user_preferences={},
                interpretation_executor=executor,
            )
        self.assertIs(context.exception, error)


class MainPipelineInterpretationIntegrationTests(unittest.TestCase):
    def test_mixed_batch_interprets_intelligence_once_and_exposes_result(self):
        preferences = {"soft_preferences": ["mission-driven work"]}
        paths = [make_path()]
        executor = RecordingInterpretationExecutor()
        pipeline, _, _ = make_pipeline(
            (
                SignalCategory.JOB,
                SignalCategory.NEWS,
                SignalCategory.MARKET_TREND,
            ),
            interpretation_executor=executor,
            user_preferences=preferences,
            target_career_paths=paths,
        )

        output = pipeline.run()

        self.assertEqual(len(output.career_signal_routing.opportunities), 1)
        self.assertEqual(len(output.career_signal_routing.intelligence), 2)
        self.assertEqual(len(executor.contexts), 1)
        context = executor.contexts[0]
        self.assertEqual(
            tuple(item.career_signal.signal_id for item in context.intelligence_signals),
            tuple(
                item.career_signal.signal_id
                for item in output.career_signal_routing.intelligence
            ),
        )
        self.assertIs(context.user_preferences, preferences)
        self.assertIs(context.target_career_paths[0], paths[0])
        self.assertFalse(hasattr(context, "user_profile"))
        self.assertEqual(
            output.career_intelligence_interpretation,
            make_interpretation_result(
                item.career_signal.signal_id
                for item in output.career_signal_routing.intelligence
            ),
        )
        self.assertEqual(len(output.scored_career_signals), 3)

    def test_opportunity_only_bypasses_interpretation(self):
        executor = RecordingInterpretationExecutor()
        pipeline, _, _ = make_pipeline(
            (SignalCategory.JOB, SignalCategory.JOB),
            interpretation_executor=executor,
        )
        output = pipeline.run()
        self.assertEqual(len(output.career_signal_routing.opportunities), 2)
        self.assertEqual(output.career_signal_routing.intelligence, ())
        self.assertEqual(executor.contexts, [])
        self.assertEqual(
            output.career_intelligence_interpretation.warnings,
            (EMPTY_INPUT_WARNING,),
        )

    def test_empty_run_returns_deterministic_empty_interpretation(self):
        executor = RecordingInterpretationExecutor()
        pipeline, _, _ = make_pipeline((), interpretation_executor=executor)
        output = pipeline.run()
        self.assertEqual(output.career_signal_routing.intelligence, ())
        self.assertEqual(executor.contexts, [])
        self.assertEqual(
            output.career_intelligence_interpretation.input_signal_ids,
            (),
        )

    def test_interpretation_failure_propagates_at_explicit_pipeline_stage(self):
        error = CareerIntelligenceInterpretationError("Malformed live response.")
        executor = RecordingInterpretationExecutor(error=error)
        pipeline, _, _ = make_pipeline(
            (SignalCategory.NEWS,),
            interpretation_executor=executor,
        )
        with self.assertRaises(CareerIntelligenceInterpretationError) as context:
            pipeline.run()
        self.assertIs(context.exception, error)
        self.assertEqual(
            pipeline.pipeline_run_stage,
            "career_intelligence_interpretation",
        )


if __name__ == "__main__":
    unittest.main()
