import json
from dataclasses import replace
from pathlib import Path
import unittest

from src.career_intelligence_brief import (
    CAREER_INTELLIGENCE_BRIEF_SCHEMA_VERSION,
    CareerIntelligenceBriefError,
    build_career_intelligence_brief,
)
from src.career_intelligence_interpretation import (
    CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
    CareerImplicationInterpretation,
    CareerIntelligenceInterpretationResult,
    InterpretationConfidence,
    KeyDevelopmentInterpretation,
    ThemeInterpretation,
)
from src.career_signal_priority import ScoredCareerSignal
from src.career_signal_routing import CareerSignalRoutingResult
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


GENERATED_AT = "2026-08-12T12:00:00+08:00"


def make_path(path_id="path-ai", title="AI Strategy"):
    return TargetCareerPath(
        path_id=path_id,
        title=title,
        category=CareerPathCategory.AI_STRATEGY,
        description="Synthetic path.",
        fit_score=0.9,
    )


def make_component(
    reason,
    *,
    status=ComponentStatus.AVAILABLE,
):
    return SemanticComponentResult(
        status=status,
        score=0.75 if status == ComponentStatus.AVAILABLE else None,
        reason=reason,
        evidence=("Synthetic evidence.",) if status == ComponentStatus.AVAILABLE else (),
    )


def make_scored(
    signal_id,
    *,
    category=SignalCategory.JOB,
    score=80.0,
    tier=PriorityTier.MEDIUM_HIGH,
    matched_path_ids=("path-ai",),
    organization="Example Organization",
    summary="Synthetic signal summary.",
    url="https://example.com/signal",
    published_at="2026-08-11T00:00:00+00:00",
    policy_status=ComponentStatus.AVAILABLE,
    feasibility_status=ComponentStatus.AVAILABLE,
):
    profile = (
        AssessmentProfile.OPPORTUNITY
        if category == SignalCategory.JOB
        else AssessmentProfile.INTELLIGENCE
    )
    components = (
        {
            "user_policy_fit": make_component(
                "Policy reason preserved exactly.  ",
                status=policy_status,
            ),
            "opportunity_feasibility": make_component(
                "Feasibility reason preserved exactly.",
                status=feasibility_status,
            ),
        }
        if profile == AssessmentProfile.OPPORTUNITY
        else {}
    )
    signal = CareerSignal(
        signal_id=signal_id,
        category=category,
        title=f"Title {signal_id}",
        organization=organization,
        url=url,
        published_at=published_at,
        summary=summary,
        source_type=SourceType.SEARCH_API,
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
            priority_score=score,
            tier=tier,
            profile=profile,
            components={},
            matched_path_ids=matched_path_ids,
            policy_version="career_signal_priority_v1",
            renormalization_denominator=95.0,
            warnings=(),
        ),
        assessment_profile=profile,
    )


def make_interpretation(signal_ids=("intel-a", "intel-b"), *, populated=True):
    signal_ids = tuple(signal_ids)
    return CareerIntelligenceInterpretationResult(
        schema_version=CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
        input_signal_ids=signal_ids,
        themes=(
            ThemeInterpretation(
                title="Synthetic theme",
                summary="Two synthetic signals support this theme.",
                supporting_signal_ids=signal_ids,
                relevant_career_path_ids=("path-ai",),
                confidence=InterpretationConfidence.MEDIUM,
            ),
        ) if populated else (),
        key_developments=(
            KeyDevelopmentInterpretation(
                title="Synthetic development",
                summary="A synthetic development occurred.",
                why_it_matters="It matters to the supplied path.",
                supporting_signal_ids=(signal_ids[0],),
                confidence=InterpretationConfidence.MEDIUM,
            ),
        ) if populated else (),
        career_implications=(
            CareerImplicationInterpretation(
                summary="The development may affect the supplied path.",
                relevant_career_path_ids=("path-ai",),
                supporting_signal_ids=(signal_ids[0],),
                confidence=InterpretationConfidence.MEDIUM,
            ),
        ) if populated else (),
        warnings=("Synthetic source-concentration warning.",) if populated else (),
    )


def build_mixed_brief():
    opportunities = (
        make_scored("signal-b", score=91.125, tier=PriorityTier.HIGH),
        make_scored("signal-a", score=80.375),
    )
    intelligence = (
        make_scored("intel-a", category=SignalCategory.NEWS),
        make_scored("intel-b", category=SignalCategory.FUNDING),
    )
    routing = CareerSignalRoutingResult(
        opportunities=opportunities,
        intelligence=intelligence,
    )
    interpretation = make_interpretation()
    brief = build_career_intelligence_brief(
        routing_result=routing,
        interpretation=interpretation,
        target_career_paths=(make_path(),),
        generated_at=GENERATED_AT,
    )
    return brief, routing, interpretation


class CareerIntelligenceBriefBuilderTests(unittest.TestCase):
    def test_builder_has_no_llm_network_database_or_clock_dependency(self):
        source = (
            Path(__file__).parents[1]
            / "src"
            / "career_intelligence_brief.py"
        ).read_text(encoding="utf-8").lower()

        for prohibited in (
            "openai",
            "deepseek",
            "requests",
            "httpx",
            "urllib",
            "sqlite",
            "repository",
            "datetime.now",
        ):
            self.assertNotIn(prohibited, source)

    def test_mixed_happy_path_preserves_all_sections_and_order(self):
        brief, routing, interpretation = build_mixed_brief()

        self.assertEqual(
            tuple(item.signal_id for item in brief.opportunities),
            ("signal-b", "signal-a"),
        )
        self.assertEqual(brief.key_developments, interpretation.key_developments)
        self.assertEqual(brief.themes, interpretation.themes)
        self.assertEqual(brief.career_implications, interpretation.career_implications)
        self.assertEqual(brief.warnings, interpretation.warnings)
        self.assertIs(brief.key_developments[0], interpretation.key_developments[0])
        self.assertEqual(len(routing.intelligence), 2)

    def test_score_tier_reasons_and_path_titles_are_copied_exactly(self):
        brief, _, _ = build_mixed_brief()
        opportunity = brief.opportunities[0]

        self.assertEqual(opportunity.priority_score, 91.125)
        self.assertEqual(opportunity.priority_tier, PriorityTier.HIGH)
        self.assertEqual(
            opportunity.user_policy_fit_reason,
            "Policy reason preserved exactly.  ",
        )
        self.assertEqual(
            opportunity.opportunity_feasibility_reason,
            "Feasibility reason preserved exactly.",
        )
        self.assertEqual(
            opportunity.matched_career_paths[0].to_dict(),
            {"path_id": "path-ai", "title": "AI Strategy"},
        )

    def test_nullable_strings_and_unavailable_reasons_become_null(self):
        opportunity = make_scored(
            "nullable",
            organization=None,
            summary="",
            url="   ",
            published_at=None,
            policy_status=ComponentStatus.UNAVAILABLE,
            feasibility_status=ComponentStatus.UNAVAILABLE,
        )
        brief = build_career_intelligence_brief(
            routing_result=CareerSignalRoutingResult(opportunities=(opportunity,)),
            interpretation=make_interpretation((), populated=False),
            target_career_paths=(make_path(),),
            generated_at=GENERATED_AT,
        )

        payload = brief.to_dict()["opportunities"][0]
        for field_name in (
            "organization",
            "summary",
            "url",
            "published_at",
            "user_policy_fit_reason",
            "opportunity_feasibility_reason",
        ):
            self.assertIsNone(payload[field_name])

    def test_unresolved_path_preserves_id_with_null_title(self):
        opportunity = make_scored(
            "unresolved",
            matched_path_ids=("path-ai", "missing-path"),
        )
        brief = build_career_intelligence_brief(
            routing_result=CareerSignalRoutingResult(opportunities=(opportunity,)),
            interpretation=make_interpretation((), populated=False),
            target_career_paths=(make_path(),),
            generated_at=GENERATED_AT,
        )

        self.assertEqual(
            [item.to_dict() for item in brief.opportunities[0].matched_career_paths],
            [
                {"path_id": "path-ai", "title": "AI Strategy"},
                {"path_id": "missing-path", "title": None},
            ],
        )

    def test_duplicate_target_path_ids_fail_explicitly(self):
        with self.assertRaisesRegex(
            CareerIntelligenceBriefError,
            "TargetCareerPath IDs must be unique",
        ):
            build_career_intelligence_brief(
                routing_result=CareerSignalRoutingResult(),
                interpretation=make_interpretation((), populated=False),
                target_career_paths=(make_path(title="One"), make_path(title="Two")),
                generated_at=GENERATED_AT,
            )

    def test_generated_at_is_preserved_and_naive_value_is_rejected(self):
        brief = build_career_intelligence_brief(
            routing_result=CareerSignalRoutingResult(),
            interpretation=make_interpretation((), populated=False),
            generated_at=GENERATED_AT,
        )
        self.assertEqual(brief.generated_at, GENERATED_AT)

        with self.assertRaisesRegex(CareerIntelligenceBriefError, "timezone"):
            build_career_intelligence_brief(
                routing_result=CareerSignalRoutingResult(),
                interpretation=make_interpretation((), populated=False),
                generated_at="2026-08-12T12:00:00",
            )

    def test_empty_opportunity_only_and_intelligence_only_cases(self):
        empty = build_career_intelligence_brief(
            routing_result=CareerSignalRoutingResult(),
            interpretation=make_interpretation((), populated=False),
            generated_at=GENERATED_AT,
        )
        self.assertEqual(empty.opportunities, ())
        self.assertEqual(empty.key_developments, ())

        opportunity_only = build_career_intelligence_brief(
            routing_result=CareerSignalRoutingResult(
                opportunities=(make_scored("opportunity-only"),)
            ),
            interpretation=make_interpretation((), populated=False),
            generated_at=GENERATED_AT,
        )
        self.assertEqual(len(opportunity_only.opportunities), 1)
        self.assertEqual(opportunity_only.themes, ())

        intelligence = (
            make_scored("intel-a", category=SignalCategory.NEWS),
            make_scored("intel-b", category=SignalCategory.COMPANY),
        )
        intelligence_only = build_career_intelligence_brief(
            routing_result=CareerSignalRoutingResult(intelligence=intelligence),
            interpretation=make_interpretation(),
            target_career_paths=(make_path(),),
            generated_at=GENERATED_AT,
        )
        self.assertEqual(intelligence_only.opportunities, ())
        self.assertEqual(len(intelligence_only.key_developments), 1)

    def test_serialization_is_exact_json_compatible_contract_shape(self):
        brief, _, _ = build_mixed_brief()
        payload = brief.to_dict()

        self.assertEqual(
            tuple(payload),
            (
                "schema_version",
                "generated_at",
                "opportunities",
                "key_developments",
                "themes",
                "career_implications",
                "warnings",
            ),
        )
        self.assertEqual(
            payload["schema_version"],
            CAREER_INTELLIGENCE_BRIEF_SCHEMA_VERSION,
        )
        self.assertEqual(
            tuple(payload["opportunities"][0]),
            (
                "signal_id",
                "title",
                "organization",
                "summary",
                "url",
                "published_at",
                "priority_score",
                "priority_tier",
                "matched_career_paths",
                "user_policy_fit_reason",
                "opportunity_feasibility_reason",
            ),
        )
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_builder_does_not_mutate_inputs(self):
        _, routing, interpretation = build_mixed_brief()
        paths = (make_path(),)
        routing_before = routing.to_dict()
        interpretation_before = interpretation.to_dict()
        paths_before = [path.to_dict() for path in paths]

        build_career_intelligence_brief(
            routing_result=routing,
            interpretation=interpretation,
            target_career_paths=paths,
            generated_at=GENERATED_AT,
        )

        self.assertEqual(routing.to_dict(), routing_before)
        self.assertEqual(interpretation.to_dict(), interpretation_before)
        self.assertEqual([path.to_dict() for path in paths], paths_before)

    def test_duplicate_opportunity_and_identity_mismatch_fail_explicitly(self):
        opportunity = make_scored("duplicate")
        with self.assertRaisesRegex(CareerIntelligenceBriefError, "unique signal IDs"):
            build_career_intelligence_brief(
                routing_result=CareerSignalRoutingResult(
                    opportunities=(opportunity, opportunity)
                ),
                interpretation=make_interpretation((), populated=False),
                generated_at=GENERATED_AT,
            )

        mismatched = replace(
            opportunity,
            priority_score=replace(opportunity.priority_score, signal_id="other"),
        )
        with self.assertRaisesRegex(CareerIntelligenceBriefError, "do not match"):
            build_career_intelligence_brief(
                routing_result=CareerSignalRoutingResult(opportunities=(mismatched,)),
                interpretation=make_interpretation((), populated=False),
                generated_at=GENERATED_AT,
            )

    def test_non_opportunity_and_missing_components_fail_explicitly(self):
        intelligence = make_scored("intel", category=SignalCategory.NEWS)
        with self.assertRaisesRegex(CareerIntelligenceBriefError, "only job"):
            build_career_intelligence_brief(
                routing_result=CareerSignalRoutingResult(
                    opportunities=(intelligence,),
                    intelligence=(intelligence,),
                ),
                interpretation=make_interpretation(("intel",), populated=False),
                generated_at=GENERATED_AT,
            )

        opportunity = make_scored("missing-components")
        malformed = replace(
            opportunity,
            priority_assessment=replace(
                opportunity.priority_assessment,
                components={},
            ),
        )
        with self.assertRaisesRegex(CareerIntelligenceBriefError, "components"):
            build_career_intelligence_brief(
                routing_result=CareerSignalRoutingResult(opportunities=(malformed,)),
                interpretation=make_interpretation((), populated=False),
                generated_at=GENERATED_AT,
            )

    def test_malformed_extreme_numeric_value_raises_dedicated_error(self):
        opportunity = make_scored("extreme-score")
        malformed = replace(
            opportunity,
            priority_score=replace(
                opportunity.priority_score,
                priority_score=10 ** 1000,
            ),
        )

        with self.assertRaises(CareerIntelligenceBriefError):
            build_career_intelligence_brief(
                routing_result=CareerSignalRoutingResult(
                    opportunities=(malformed,)
                ),
                interpretation=make_interpretation((), populated=False),
                generated_at=GENERATED_AT,
            )

    def test_interpretation_must_match_routed_intelligence(self):
        routing = CareerSignalRoutingResult(
            intelligence=(make_scored("intel-a", category=SignalCategory.NEWS),)
        )
        with self.assertRaisesRegex(CareerIntelligenceBriefError, "do not match"):
            build_career_intelligence_brief(
                routing_result=routing,
                interpretation=make_interpretation(("intel-b",), populated=False),
                generated_at=GENERATED_AT,
            )


if __name__ == "__main__":
    unittest.main()
