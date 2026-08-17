import unittest
from unittest.mock import patch

from src.career_signal_priority import ScoredCareerSignal
from src.career_signal_routing import (
    UNSUPPORTED_OR_UNKNOWN_CATEGORY_REASON,
    CareerSignalRoutingResult,
    route_scored_career_signals,
)
from src.career_signal_scoring import PriorityScoreResult, PriorityTier
from src.models import CareerSignal, SignalCategory, SourceType
from src.priority_assessment import (
    AssessmentProfile,
    ComponentStatus,
    PriorityAssessmentResult,
    SemanticComponentResult,
)


def make_signal(
    signal_id,
    category,
    *,
    source_type=SourceType.SEARCH_API,
):
    return CareerSignal(
        signal_id=signal_id,
        category=category,
        title=f"Signal {signal_id}",
        organization="Example Co",
        url=f"https://example.test/{signal_id}",
        published_at="2026-08-12T00:00:00+00:00",
        summary="Synthetic scored signal.",
        source_type=source_type,
        relevance_score=0.9,
    )


def make_assessment(signal_id, profile):
    component_name = (
        "user_policy_fit"
        if profile == AssessmentProfile.OPPORTUNITY
        else "career_relevance_strength"
    )
    return PriorityAssessmentResult(
        schema_version="priority_assessment_v1",
        signal_id=signal_id,
        assessment_profile=profile,
        components={
            component_name: SemanticComponentResult(
                status=ComponentStatus.AVAILABLE,
                score=0.75,
                reason="synthetic reason",
                evidence=("synthetic evidence",),
            )
        },
        warnings=(),
    )


def make_priority_score(signal_id, score, profile):
    return PriorityScoreResult(
        signal_id=signal_id,
        priority_score=score,
        tier=PriorityTier.MEDIUM_HIGH,
        profile=profile,
        components={},
        matched_path_ids=(),
        policy_version="test_policy",
        renormalization_denominator=1.0,
        warnings=(),
    )


def make_scored(
    signal_id,
    category,
    score,
    *,
    source_type=SourceType.SEARCH_API,
):
    profile = (
        AssessmentProfile.OPPORTUNITY
        if category == SignalCategory.JOB
        else AssessmentProfile.INTELLIGENCE
    )
    return ScoredCareerSignal(
        career_signal=make_signal(
            signal_id,
            category,
            source_type=source_type,
        ),
        priority_assessment=make_assessment(signal_id, profile),
        priority_score=make_priority_score(signal_id, score, profile),
        assessment_profile=profile,
    )


class CareerSignalRoutingTests(unittest.TestCase):
    def test_job_routes_to_opportunities(self):
        result = route_scored_career_signals(
            [make_scored("signal-job", SignalCategory.JOB, 82)]
        )
        self.assertEqual(
            [item.career_signal.signal_id for item in result.opportunities],
            ["signal-job"],
        )
        self.assertEqual(result.intelligence, ())
        self.assertEqual(result.unrouted, ())

    def test_news_routes_to_intelligence(self):
        result = route_scored_career_signals(
            [make_scored("signal-news", SignalCategory.NEWS, 65)]
        )
        self.assertEqual(
            [item.career_signal.signal_id for item in result.intelligence],
            ["signal-news"],
        )

    def test_company_routes_to_intelligence(self):
        result = route_scored_career_signals(
            [make_scored("signal-company", SignalCategory.COMPANY, 65)]
        )
        self.assertEqual(
            [item.career_signal.signal_id for item in result.intelligence],
            ["signal-company"],
        )

    def test_funding_routes_to_intelligence(self):
        result = route_scored_career_signals(
            [make_scored("signal-funding", SignalCategory.FUNDING, 65)]
        )
        self.assertEqual(
            [item.career_signal.signal_id for item in result.intelligence],
            ["signal-funding"],
        )

    def test_market_trend_routes_to_intelligence(self):
        result = route_scored_career_signals(
            [make_scored("signal-market", SignalCategory.MARKET_TREND, 65)]
        )
        self.assertEqual(
            [item.career_signal.signal_id for item in result.intelligence],
            ["signal-market"],
        )

    def test_unknown_routes_to_unrouted_review(self):
        result = route_scored_career_signals(
            [make_scored("signal-unknown", SignalCategory.UNKNOWN, 50)]
        )
        self.assertEqual(result.opportunities, ())
        self.assertEqual(result.intelligence, ())
        self.assertEqual(
            result.unrouted[0].scored_career_signal.career_signal.signal_id,
            "signal-unknown",
        )
        self.assertEqual(
            result.unrouted[0].reason,
            UNSUPPORTED_OR_UNKNOWN_CATEGORY_REASON,
        )

    def test_unsupported_string_category_routes_to_unrouted_review(self):
        result = route_scored_career_signals(
            [make_scored("signal-other", "other", 50)]
        )
        self.assertEqual(
            result.unrouted[0].reason,
            UNSUPPORTED_OR_UNKNOWN_CATEGORY_REASON,
        )

    def test_source_type_does_not_affect_routing(self):
        result = route_scored_career_signals(
            [
                make_scored(
                    "signal-market",
                    SignalCategory.MARKET_TREND,
                    91,
                    source_type=SourceType.SEARCH_API,
                ),
                make_scored(
                    "signal-job",
                    SignalCategory.JOB,
                    82,
                    source_type=SourceType.RSS,
                ),
            ]
        )
        self.assertEqual(
            [item.career_signal.signal_id for item in result.intelligence],
            ["signal-market"],
        )
        self.assertEqual(
            [item.career_signal.signal_id for item in result.opportunities],
            ["signal-job"],
        )

    def test_opportunities_sort_by_priority_descending(self):
        result = route_scored_career_signals(
            [
                make_scored("signal-low", SignalCategory.JOB, 74),
                make_scored("signal-high", SignalCategory.JOB, 82),
            ]
        )
        self.assertEqual(
            [item.career_signal.signal_id for item in result.opportunities],
            ["signal-high", "signal-low"],
        )

    def test_intelligence_sorts_by_priority_descending(self):
        result = route_scored_career_signals(
            [
                make_scored("signal-news", SignalCategory.NEWS, 65),
                make_scored("signal-market", SignalCategory.MARKET_TREND, 91),
            ]
        )
        self.assertEqual(
            [item.career_signal.signal_id for item in result.intelligence],
            ["signal-market", "signal-news"],
        )

    def test_equal_scores_sort_by_signal_id(self):
        result = route_scored_career_signals(
            [
                make_scored("signal-b", SignalCategory.JOB, 82),
                make_scored("signal-a", SignalCategory.JOB, 82),
            ]
        )
        self.assertEqual(
            [item.career_signal.signal_id for item in result.opportunities],
            ["signal-a", "signal-b"],
        )

    def test_no_scored_signals_returns_empty_collections(self):
        result = route_scored_career_signals(())
        self.assertEqual(result, CareerSignalRoutingResult())

    def test_routing_preserves_scored_runtime_objects(self):
        scored = make_scored("signal-job", SignalCategory.JOB, 82)
        result = route_scored_career_signals([scored])
        self.assertIs(result.opportunities[0], scored)
        self.assertIs(result.opportunities[0].career_signal, scored.career_signal)
        self.assertIs(
            result.opportunities[0].priority_assessment,
            scored.priority_assessment,
        )
        self.assertIs(result.opportunities[0].priority_score, scored.priority_score)

    def test_no_llm_or_network_call_occurs(self):
        with patch(
            "src.priority_assessment.PriorityAssessmentClient.assess",
            side_effect=AssertionError("router must not call LLM"),
        ):
            result = route_scored_career_signals(
                [make_scored("signal-job", SignalCategory.JOB, 82)]
            )
        self.assertEqual(len(result.opportunities), 1)

    def test_mixed_scored_inputs_route_and_sort_without_recalculation(self):
        inputs = [
            make_scored("job-82", SignalCategory.JOB, 82),
            make_scored("market-91", SignalCategory.MARKET_TREND, 91),
            make_scored("news-65", SignalCategory.NEWS, 65),
            make_scored("job-74", SignalCategory.JOB, 74),
            make_scored("unknown-50", SignalCategory.UNKNOWN, 50),
        ]
        result = route_scored_career_signals(inputs)

        self.assertEqual(
            [
                (
                    item.career_signal.category,
                    item.priority_score.priority_score,
                )
                for item in result.opportunities
            ],
            [
                (SignalCategory.JOB, 82),
                (SignalCategory.JOB, 74),
            ],
        )
        self.assertEqual(
            [
                (
                    item.career_signal.category,
                    item.priority_score.priority_score,
                )
                for item in result.intelligence
            ],
            [
                (SignalCategory.MARKET_TREND, 91),
                (SignalCategory.NEWS, 65),
            ],
        )
        self.assertEqual(
            [
                item.scored_career_signal.career_signal.category
                for item in result.unrouted
            ],
            [SignalCategory.UNKNOWN],
        )
        self.assertIs(result.opportunities[0], inputs[0])
        self.assertIs(result.intelligence[0], inputs[1])
