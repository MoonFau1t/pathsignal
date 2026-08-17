import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.career_signal_scoring import (
    COMPONENT_AI_CONFIDENCE,
    COMPONENT_PATH_ALIGNMENT,
    COMPONENT_RECENCY,
    COMPONENT_SOURCE_PROVENANCE,
    PRIORITY_SCORING_POLICY_VERSION,
    PRIORITY_SCORING_POLICY_V1,
    PriorityScoringError,
    PriorityTier,
    SourceProvenanceInput,
    priority_tier,
    score_ai_confidence_component,
    score_career_signal,
    score_path_alignment_component,
    score_recency_component,
    score_source_provenance_component,
)
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


def make_signal(
    *,
    signal_id="signal-1",
    published_at="2026-08-10T00:00:00+00:00",
    relevance_score=92.0,
    source_type=SourceType.SEARCH_API,
):
    return CareerSignal(
        signal_id=signal_id,
        category=SignalCategory.JOB,
        title="Synthetic Signal",
        organization="Example Co",
        url="https://example.test/signal",
        published_at=published_at,
        summary="Synthetic signal summary.",
        source_type=source_type,
        relevance_score=relevance_score,
    )


def make_path(path_id, fit_score=90.0, path_type="core_match"):
    return TargetCareerPath(
        path_id=path_id,
        title=f"Path {path_id}",
        category=CareerPathCategory.AI_STRATEGY,
        description="Synthetic target path.",
        fit_score=fit_score,
        metadata={"path_type": path_type},
    )


def semantic_component(score, name="evidence"):
    return SemanticComponentResult(
        status=ComponentStatus.AVAILABLE,
        score=score,
        reason=f"{name} reason",
        evidence=(f"{name} evidence",),
    )


def unavailable_component(name="missing"):
    return SemanticComponentResult(
        status=ComponentStatus.UNAVAILABLE,
        score=None,
        reason=f"{name} unavailable",
        evidence=(),
    )


def opportunity_assessment(
    *,
    user_policy_fit=0.75,
    opportunity_feasibility=0.75,
    signal_id="signal-1",
):
    return PriorityAssessmentResult(
        schema_version="priority_assessment_v1",
        signal_id=signal_id,
        assessment_profile=AssessmentProfile.OPPORTUNITY,
        components={
            "user_policy_fit": (
                user_policy_fit
                if isinstance(user_policy_fit, SemanticComponentResult)
                else semantic_component(user_policy_fit, "user_policy_fit")
            ),
            "opportunity_feasibility": (
                opportunity_feasibility
                if isinstance(opportunity_feasibility, SemanticComponentResult)
                else semantic_component(opportunity_feasibility, "opportunity_feasibility")
            ),
        },
        warnings=(),
    )


def intelligence_assessment(
    *,
    career_relevance_strength=1.0,
    signal_significance=0.75,
    signal_id="signal-1",
):
    return PriorityAssessmentResult(
        schema_version="priority_assessment_v1",
        signal_id=signal_id,
        assessment_profile=AssessmentProfile.INTELLIGENCE,
        components={
            "career_relevance_strength": (
                career_relevance_strength
                if isinstance(career_relevance_strength, SemanticComponentResult)
                else semantic_component(career_relevance_strength, "career_relevance_strength")
            ),
            "signal_significance": (
                signal_significance
                if isinstance(signal_significance, SemanticComponentResult)
                else semantic_component(signal_significance, "signal_significance")
            ),
        },
        warnings=(),
    )


def score_opportunity(
    *,
    assessment=None,
    signal=None,
    paths=None,
    matched_ids=("core",),
    confidence=0.9,
    provenance=None,
    as_of="2026-08-11T00:00:00+00:00",
):
    return score_career_signal(
        assessment_result=assessment or opportunity_assessment(),
        career_signal=signal or make_signal(),
        matched_career_path_ids=matched_ids,
        target_career_paths=paths or (make_path("core", 90, "core_match"),),
        as_of=as_of,
        upstream_ai_confidence=confidence,
        source_provenance=provenance,
    )


class ScoringPolicyTests(unittest.TestCase):
    def test_opportunity_profile_weights_are_exact(self):
        self.assertEqual(
            {
                item.name: item.configured_weight
                for item in PRIORITY_SCORING_POLICY_V1.opportunity_components
            },
            {
                "path_alignment": 30.0,
                "user_policy_fit": 25.0,
                "opportunity_feasibility": 20.0,
                "recency": 15.0,
                "source_provenance": 5.0,
                "ai_confidence": 5.0,
            },
        )

    def test_intelligence_profile_weights_are_exact(self):
        self.assertEqual(
            {
                item.name: item.configured_weight
                for item in PRIORITY_SCORING_POLICY_V1.intelligence_components
            },
            {
                "career_relevance_strength": 25.0,
                "signal_significance": 25.0,
                "path_alignment": 20.0,
                "recency": 15.0,
                "source_provenance": 10.0,
                "ai_confidence": 5.0,
            },
        )

    def test_policy_version_is_v1(self):
        self.assertEqual(PRIORITY_SCORING_POLICY_VERSION, "career_signal_priority_v1")


class PathAlignmentTests(unittest.TestCase):
    def test_valid_single_matched_path_uses_normalized_fit_score(self):
        component, resolved, warnings = score_path_alignment_component(
            matched_career_path_ids=("path-1",),
            target_career_paths=(make_path("path-1", 91, "core_match"),),
            configured_weight=30,
        )

        self.assertEqual(component.status, ComponentStatus.AVAILABLE)
        self.assertAlmostEqual(component.normalized_score, 0.91)
        self.assertEqual(resolved, ("path-1",))
        self.assertEqual(warnings, ())

    def test_multiple_paths_uses_strongest_valid_modified_score(self):
        component, resolved, _ = score_path_alignment_component(
            matched_career_path_ids=("core", "bridge", "stretch"),
            target_career_paths=(
                make_path("core", 80, "core_match"),
                make_path("bridge", 100, "bridge_role"),
                make_path("stretch", 100, "stretch_opportunity"),
            ),
            configured_weight=30,
        )

        self.assertAlmostEqual(component.normalized_score, 0.9)
        self.assertEqual(component.references, ("bridge",))
        self.assertEqual(resolved, ("core", "bridge", "stretch"))

    def test_path_type_modifiers(self):
        expected = {
            "core": 1.0,
            "core_match": 1.0,
            "bridge_role": 0.9,
            "stretch_opportunity": 0.75,
            "exploratory_opportunity": 0.65,
        }
        for path_type, score in expected.items():
            with self.subTest(path_type=path_type):
                component, _, _ = score_path_alignment_component(
                    matched_career_path_ids=("path",),
                    target_career_paths=(make_path("path", 100, path_type),),
                    configured_weight=30,
                )
                self.assertAlmostEqual(component.normalized_score, score)

    def test_unresolved_matched_id_warns_and_uses_valid_match(self):
        component, resolved, warnings = score_path_alignment_component(
            matched_career_path_ids=("missing", "path"),
            target_career_paths=(make_path("path", 80, "core_match"),),
            configured_weight=30,
        )

        self.assertEqual(component.status, ComponentStatus.AVAILABLE)
        self.assertEqual(resolved, ("path",))
        self.assertIn("unresolved_matched_career_path_id:missing", warnings)

    def test_all_matched_ids_unresolved_is_unavailable(self):
        component, resolved, warnings = score_path_alignment_component(
            matched_career_path_ids=("missing",),
            target_career_paths=(make_path("path", 80, "core_match"),),
            configured_weight=30,
        )

        self.assertEqual(component.status, ComponentStatus.UNAVAILABLE)
        self.assertIsNone(component.normalized_score)
        self.assertEqual(resolved, ())
        self.assertIn("unresolved_matched_career_path_id:missing", warnings)

    def test_invalid_fit_score_is_not_clipped(self):
        component, _, warnings = score_path_alignment_component(
            matched_career_path_ids=("path",),
            target_career_paths=(make_path("path", 120, "core_match"),),
            configured_weight=30,
        )

        self.assertEqual(component.status, ComponentStatus.UNAVAILABLE)
        self.assertIn("invalid_fit_score:path", warnings)

    def test_unknown_path_type_is_not_inferred(self):
        component, _, warnings = score_path_alignment_component(
            matched_career_path_ids=("path",),
            target_career_paths=(make_path("path", 100, "primary"),),
            configured_weight=30,
        )

        self.assertEqual(component.status, ComponentStatus.UNAVAILABLE)
        self.assertIn("unsupported_path_type:path:primary", warnings)


class RecencyTests(unittest.TestCase):
    def test_common_rfc_rss_timestamps_use_existing_recency_buckets(self):
        cases = (
            (
                "Tue, 11 Aug 2026 10:15:00 +0000",
                "2026-08-12T10:15:00+00:00",
                1.0,
                "age_days=1.0",
            ),
            (
                "Fri, 07 Aug 2026 11:15:00 +0000",
                "2026-08-12T11:15:00+00:00",
                0.9,
                "age_days=5.0",
            ),
        )
        for published_at, as_of, expected_score, expected_age in cases:
            with self.subTest(published_at=published_at):
                component, warnings = score_recency_component(
                    published_at=published_at,
                    as_of=as_of,
                    configured_weight=15,
                )

                self.assertEqual(component.status, ComponentStatus.AVAILABLE)
                self.assertAlmostEqual(component.normalized_score, expected_score)
                self.assertIn(expected_age, component.evidence)
                self.assertEqual(warnings, ())

    def test_rfc_timezone_offset_is_respected(self):
        component, warnings = score_recency_component(
            published_at="Tue, 11 Aug 2026 12:15:00 +0200",
            as_of="2026-08-11T10:15:00+00:00",
            configured_weight=15,
        )

        self.assertEqual(component.status, ComponentStatus.AVAILABLE)
        self.assertAlmostEqual(component.normalized_score, 1.0)
        self.assertIn("age_days=0.0", component.evidence)
        self.assertEqual(warnings, ())

    def test_existing_iso_timestamp_behavior_is_unchanged(self):
        component, warnings = score_recency_component(
            published_at="2026-08-07T11:15:00+00:00",
            as_of="2026-08-12T11:15:00+00:00",
            configured_weight=15,
        )

        self.assertEqual(component.status, ComponentStatus.AVAILABLE)
        self.assertAlmostEqual(component.normalized_score, 0.9)
        self.assertIn("age_days=5.0", component.evidence)
        self.assertEqual(warnings, ())

    def test_recency_bucket_boundaries(self):
        cases = (
            (3, 1.0),
            (4, 0.9),
            (7, 0.9),
            (8, 0.75),
            (14, 0.75),
            (15, 0.55),
            (30, 0.55),
            (31, 0.35),
            (60, 0.35),
            (61, 0.2),
        )
        for days_old, expected in cases:
            with self.subTest(days_old=days_old):
                published = datetime(2026, 8, 11, tzinfo=timezone.utc) - timedelta(
                    days=days_old
                )
                component, warnings = score_recency_component(
                    published_at=published.isoformat(),
                    as_of="2026-08-11T00:00:00+00:00",
                    configured_weight=15,
                )
                self.assertEqual(warnings, ())
                self.assertAlmostEqual(component.normalized_score, expected)

    def test_missing_or_malformed_timestamp_is_unavailable(self):
        for published_at in (None, "", "not-a-date"):
            with self.subTest(published_at=published_at):
                component, _ = score_recency_component(
                    published_at=published_at,
                    as_of="2026-08-11T00:00:00+00:00",
                    configured_weight=15,
                )
                self.assertEqual(component.status, ComponentStatus.UNAVAILABLE)

    def test_stage4d_rfc_timestamp_is_available_in_full_scoring(self):
        result = score_opportunity(
            signal=make_signal(
                published_at="Tue, 11 Aug 2026 10:15:00 +0000",
                source_type=SourceType.RSS,
            ),
            as_of="2026-08-12T10:15:00+00:00",
        )

        recency = result.components[COMPONENT_RECENCY]
        self.assertEqual(recency.status, ComponentStatus.AVAILABLE)
        self.assertAlmostEqual(recency.normalized_score, 1.0)
        self.assertIn("age_days=1.0", recency.evidence)

    def test_explicit_as_of_controls_result(self):
        fresh, _ = score_recency_component(
            published_at="2026-08-10",
            as_of="2026-08-11",
            configured_weight=15,
        )
        older, _ = score_recency_component(
            published_at="2026-08-10",
            as_of="2026-09-10",
            configured_weight=15,
        )

        self.assertGreater(fresh.normalized_score, older.normalized_score)

    def test_future_timestamp_is_age_zero_with_warning(self):
        component, warnings = score_recency_component(
            published_at="2026-08-12T00:00:00+00:00",
            as_of="2026-08-11T00:00:00+00:00",
            configured_weight=15,
        )

        self.assertEqual(component.normalized_score, 1.0)
        self.assertIn("future_published_at_treated_as_age_zero", warnings)


class AIConfidenceTests(unittest.TestCase):
    def test_explicit_confidence_is_used_when_valid(self):
        component, warnings = score_ai_confidence_component(
            upstream_ai_confidence=0.83,
            career_signal_relevance_score=99,
            configured_weight=5,
        )

        self.assertEqual(warnings, ())
        self.assertAlmostEqual(component.normalized_score, 0.83)

    def test_relevance_score_fallback_uses_normalizer_contract(self):
        component, _ = score_ai_confidence_component(
            upstream_ai_confidence=None,
            career_signal_relevance_score=91.5,
            configured_weight=5,
        )

        self.assertAlmostEqual(component.normalized_score, 0.915)

    def test_missing_both_is_unavailable(self):
        component, _ = score_ai_confidence_component(
            upstream_ai_confidence=None,
            career_signal_relevance_score=None,
            configured_weight=5,
        )

        self.assertEqual(component.status, ComponentStatus.UNAVAILABLE)

    def test_invalid_values_are_not_clipped(self):
        component, warnings = score_ai_confidence_component(
            upstream_ai_confidence=1.2,
            career_signal_relevance_score=90,
            configured_weight=5,
        )

        self.assertEqual(component.status, ComponentStatus.UNAVAILABLE)
        self.assertIn("invalid_upstream_ai_confidence", warnings)


class SourceProvenanceTests(unittest.TestCase):
    def test_source_type_alone_does_not_create_quality_score(self):
        for source_type in (
            SourceType.SEARCH_API,
            SourceType.RSS,
            SourceType.SELECTED_WEBSITE,
        ):
            with self.subTest(source_type=source_type):
                result = score_opportunity(
                    signal=make_signal(source_type=source_type),
                    provenance=None,
                )
                component = result.components[COMPONENT_SOURCE_PROVENANCE]
                self.assertEqual(component.status, ComponentStatus.UNAVAILABLE)

    def test_explicit_structured_provenance_score_can_be_used(self):
        component, warnings = score_source_provenance_component(
            source_provenance=SourceProvenanceInput(
                normalized_score=0.8,
                reason="Existing upstream provenance-quality score supplied.",
                evidence=("final_source_evaluation_id=final-1",),
                reference_id="final-1",
            ),
            configured_weight=5,
        )

        self.assertEqual(warnings, ())
        self.assertEqual(component.status, ComponentStatus.AVAILABLE)
        self.assertAlmostEqual(component.normalized_score, 0.8)
        self.assertEqual(component.references, ("final-1",))

    def test_malformed_provenance_context_is_unavailable(self):
        component, warnings = score_source_provenance_component(
            source_provenance={"normalized_score": 1.5, "reason": "bad"},
            configured_weight=5,
        )

        self.assertEqual(component.status, ComponentStatus.UNAVAILABLE)
        self.assertIn("malformed_source_provenance_context", warnings)


class SemanticConsumptionAndAggregationTests(unittest.TestCase):
    def test_opportunity_semantic_components_are_consumed_without_reinterpretation(self):
        result = score_opportunity(
            assessment=opportunity_assessment(
                user_policy_fit=0.0,
                opportunity_feasibility=unavailable_component("feasibility"),
            )
        )

        self.assertEqual(result.components["user_policy_fit"].status, ComponentStatus.AVAILABLE)
        self.assertEqual(result.components["user_policy_fit"].normalized_score, 0.0)
        self.assertEqual(
            result.components["opportunity_feasibility"].status,
            ComponentStatus.UNAVAILABLE,
        )

    def test_intelligence_semantic_components_are_consumed(self):
        result = score_career_signal(
            assessment_result=intelligence_assessment(
                career_relevance_strength=1.0,
                signal_significance=0.75,
            ),
            career_signal=make_signal(),
            matched_career_path_ids=("core",),
            target_career_paths=(make_path("core", 90, "core_match"),),
            as_of="2026-08-11T00:00:00+00:00",
            upstream_ai_confidence=0.9,
        )

        self.assertEqual(result.profile, AssessmentProfile.INTELLIGENCE)
        self.assertEqual(result.components["career_relevance_strength"].normalized_score, 1.0)
        self.assertEqual(result.components["signal_significance"].normalized_score, 0.75)

    def test_all_components_available_contributions_sum_to_priority(self):
        result = score_opportunity(
            provenance=SourceProvenanceInput(0.8, "quality", ("evidence",)),
        )

        self.assertEqual(result.renormalization_denominator, 100.0)
        self.assertAlmostEqual(
            sum(component.weighted_contribution for component in result.components.values()),
            result.priority_score,
        )

    def test_missing_component_is_renormalized_not_zero_penalized(self):
        missing = score_opportunity(provenance=None)
        available = score_opportunity(
            provenance=SourceProvenanceInput(0.0, "explicit low", ("quality",)),
        )

        self.assertEqual(missing.components[COMPONENT_SOURCE_PROVENANCE].status, ComponentStatus.UNAVAILABLE)
        self.assertEqual(missing.renormalization_denominator, 95.0)
        self.assertEqual(available.renormalization_denominator, 100.0)
        self.assertGreater(missing.priority_score, available.priority_score)

    def test_multiple_components_unavailable_renormalizes_denominator(self):
        result = score_opportunity(
            assessment=opportunity_assessment(
                opportunity_feasibility=unavailable_component("feasibility"),
            ),
            signal=make_signal(published_at=None, relevance_score=None),
            confidence=None,
            provenance=None,
        )

        self.assertEqual(result.renormalization_denominator, 55.0)
        self.assertEqual(result.components[COMPONENT_RECENCY].status, ComponentStatus.UNAVAILABLE)
        self.assertEqual(result.components[COMPONENT_AI_CONFIDENCE].status, ComponentStatus.UNAVAILABLE)

    def test_high_weight_component_unavailable_changes_denominator(self):
        result = score_opportunity(matched_ids=("missing",), paths=(make_path("core"),))

        self.assertEqual(result.components[COMPONENT_PATH_ALIGNMENT].status, ComponentStatus.UNAVAILABLE)
        self.assertEqual(result.renormalization_denominator, 65.0)

    def test_zero_denominator_raises(self):
        with self.assertRaises(PriorityScoringError):
            score_opportunity(
                assessment=opportunity_assessment(
                    user_policy_fit=unavailable_component("fit"),
                    opportunity_feasibility=unavailable_component("feasibility"),
                ),
                signal=make_signal(published_at=None, relevance_score=None),
                matched_ids=("missing",),
                paths=(),
                confidence=None,
                provenance=None,
            )

    def test_determinism_same_inputs_same_result(self):
        kwargs = {
            "assessment": opportunity_assessment(),
            "signal": make_signal(),
            "paths": (make_path("core", 90, "core_match"),),
            "matched_ids": ("core",),
            "confidence": 0.9,
            "provenance": SourceProvenanceInput(0.8, "quality", ("evidence",)),
        }

        first = score_opportunity(**kwargs)
        second = score_opportunity(**kwargs)

        self.assertEqual(first.to_dict(), second.to_dict())

    def test_zero_user_policy_fit_has_no_hidden_cap(self):
        result = score_opportunity(
            assessment=opportunity_assessment(user_policy_fit=0.0),
            provenance=SourceProvenanceInput(1.0, "quality", ("evidence",)),
        )

        expected = (
            100
            * (
                (30 * 0.9)
                + (25 * 0.0)
                + (20 * 0.75)
                + (15 * 1.0)
                + (5 * 1.0)
                + (5 * 0.9)
            )
            / 100
        )
        self.assertAlmostEqual(result.priority_score, expected)
        self.assertGreater(result.priority_score, 40)


class TierTests(unittest.TestCase):
    def test_priority_tier_boundaries(self):
        cases = (
            (0, PriorityTier.LOW),
            (49.999, PriorityTier.LOW),
            (50, PriorityTier.MEDIUM),
            (69.999, PriorityTier.MEDIUM),
            (70, PriorityTier.MEDIUM_HIGH),
            (84.999, PriorityTier.MEDIUM_HIGH),
            (85, PriorityTier.HIGH),
            (100, PriorityTier.HIGH),
        )
        for score, expected in cases:
            with self.subTest(score=score):
                self.assertEqual(priority_tier(score), expected)


class SyntheticDemonstrationTests(unittest.TestCase):
    def test_high_priority_opportunity_demo(self):
        result = score_opportunity(
            assessment=opportunity_assessment(
                user_policy_fit=0.75,
                opportunity_feasibility=1.0,
            ),
            paths=(make_path("core", 95, "core_match"),),
            confidence=0.95,
            provenance=SourceProvenanceInput(0.8, "quality", ("source evaluation",)),
        )

        self.assertEqual(result.tier, PriorityTier.HIGH)
        self.assertAlmostEqual(result.renormalization_denominator, 100.0)
        self.assertAlmostEqual(
            result.priority_score,
            sum(component.weighted_contribution for component in result.components.values()),
        )

    def test_stretch_opportunity_demo_scores_lower_than_high_demo(self):
        high = score_opportunity(
            assessment=opportunity_assessment(
                user_policy_fit=0.75,
                opportunity_feasibility=1.0,
            ),
            paths=(make_path("core", 95, "core_match"),),
            confidence=0.95,
            provenance=SourceProvenanceInput(0.8, "quality", ("source evaluation",)),
        )
        stretch = score_opportunity(
            assessment=opportunity_assessment(
                user_policy_fit=0.75,
                opportunity_feasibility=0.5,
            ),
            signal=make_signal(published_at="2026-07-27T00:00:00+00:00"),
            paths=(make_path("stretch", 80, "stretch_opportunity"),),
            matched_ids=("stretch",),
            confidence=0.8,
            provenance=None,
        )

        self.assertLess(stretch.priority_score, high.priority_score)
        self.assertEqual(stretch.components["opportunity_feasibility"].normalized_score, 0.5)

    def test_intelligence_signal_demo(self):
        result = score_career_signal(
            assessment_result=intelligence_assessment(
                career_relevance_strength=1.0,
                signal_significance=0.75,
            ),
            career_signal=make_signal(
                published_at="2026-08-10T00:00:00+00:00",
                relevance_score=90,
            ),
            matched_career_path_ids=("core",),
            target_career_paths=(make_path("core", 90, "core_match"),),
            as_of="2026-08-11T00:00:00+00:00",
            upstream_ai_confidence=0.9,
            source_provenance=SourceProvenanceInput(0.8, "quality", ("source evaluation",)),
        )

        self.assertEqual(result.profile, AssessmentProfile.INTELLIGENCE)
        self.assertEqual(result.tier, PriorityTier.HIGH)

    def test_missing_provenance_demo_renormalizes(self):
        result = score_opportunity(provenance=None)

        self.assertEqual(result.components[COMPONENT_SOURCE_PROVENANCE].status, ComponentStatus.UNAVAILABLE)
        self.assertEqual(result.renormalization_denominator, 95.0)


class BoundaryTests(unittest.TestCase):
    def test_no_llm_or_network_imports_in_scoring_module(self):
        source = Path("src/career_signal_scoring.py").read_text(encoding="utf-8")
        for forbidden in ("OpenAI", "DeepSeek", "requests", "httpx", "urllib", "socket"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
