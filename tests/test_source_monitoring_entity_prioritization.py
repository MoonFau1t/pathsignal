import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.models import CareerPathCategory, TargetCareerPath
from src.source_monitoring.entity_discovery_models import (
    EntityCandidate,
    EntityCandidateVerificationStatus,
    EntityDiscoveryEvidence,
    EntityDiscoveryPlan,
    EntityDiscoveryQuery,
    EntityUniverseExecutionMetadata,
    EntityUniverseResult,
    OfficialDomainCandidate,
    OfficialDomainVerificationStatus,
    PrimaryEntityKind,
    UnresolvedIdentityConflict,
)
from src.source_monitoring.entity_priority_context import (
    DUPLICATE_INPUT_CONSOLIDATION_DIAGNOSTIC,
    build_compact_entity_contexts,
    consolidate_entity_universe_for_prioritization,
)
from src.source_monitoring.entity_priority_policy import (
    calculate_entity_priority_score,
    calculate_evidence_readiness_assessment,
    calculate_geography_assessment,
)
from src.source_monitoring.entity_prioritization_models import (
    EntityPrioritizationResult,
    PriorityTier,
    SemanticAssessmentStatus,
    SemanticDimensionAssessment,
)
from src.source_monitoring.entity_prioritizer import (
    prioritize_entities,
    validate_entity_semantic_assessment_suggestions,
)
from src.source_monitoring.prompts import build_entity_prioritization_prompt
from src.source_monitoring.models import (
    InformationNeed,
    InformationNeedPriority,
    MonitoringObjectiveCode,
)


class FakePriorityClient:
    provider = "deepseek"
    model = "fake-priority"

    def __init__(self, overrides=None):
        self.calls = 0
        self.overrides = overrides or {}
        self.last_contexts = None

    def generate(self, **kwargs):
        self.calls += 1
        self.last_contexts = kwargs["compact_entity_contexts"]
        assessments = []
        for index, context in enumerate(kwargs["compact_entity_contexts"]):
            scores = self.overrides.get(context["entity_id"], {})
            assessments.append(
                semantic_payload(
                    context["entity_id"],
                    index=index,
                    path_score=scores.get("path", 4),
                    stage_score=scores.get("stage", 4),
                    stage_status=scores.get("stage_status", "applicable"),
                    expected_score=scores.get("expected", 4),
                    strategic_score=scores.get("strategic", 4),
                )
            )
        return {"entity_semantic_assessments": assessments}


class GuardPriorityClient:
    provider = "deepseek"
    model = "fake-priority"

    def __init__(self):
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        raise AssertionError("guard client should not be called")


def target_path(path_id="path_1"):
    return TargetCareerPath(
        path_id=path_id,
        title="AI Strategy Analyst",
        category=CareerPathCategory.AI_STRATEGY,
        description="AI strategy career path",
        fit_score=0.9,
    )


def need(need_id="need_1", path_id="path_1"):
    return InformationNeed(
        information_need_id=need_id,
        need_key=f"{need_id}_key",
        objective_code=MonitoringObjectiveCode.ORGANIZATION,
        title="AI organization signals",
        description="Track AI organizations.",
        related_target_career_path_ids=(path_id,),
        signal_examples=("funding", "partnerships"),
        rationale="Relevant to monitoring.",
        priority=InformationNeedPriority.HIGH,
        confidence=0.9,
    )


def query(query_id="query_1", plan_id="plan_1"):
    return EntityDiscoveryQuery(
        query_id=query_id,
        query_text="AI companies official website",
        language="en",
        region="global",
        entity_type_code="ai_native_company",
        related_entity_type_candidate_id="etc_1",
        related_information_need_ids=("need_1",),
        discovery_intent="concrete_entity_discovery",
    )


def plan(plan_id="plan_1", language="en"):
    q = query(query_id=f"{plan_id}_query")
    return EntityDiscoveryPlan(
        plan_id=plan_id,
        entity_type_candidate_id="etc_1",
        entity_type_code="ai_native_company",
        queries=(q,),
        language=language,
        region="global" if language == "en" else "china",
        max_results=5,
        priority=0.9,
        confidence=0.9,
    )


def evidence(evidence_id="ev_1", plan_id="plan_1", url="https://example.ai"):
    return EntityDiscoveryEvidence(
        evidence_id=evidence_id,
        plan_id=plan_id,
        query_id=f"{plan_id}_query",
        result_rank=1,
        title="Example AI official website",
        snippet="Example AI builds enterprise AI software.",
        url=url,
        displayed_domain=url.replace("https://", "").split("/", 1)[0],
        search_provider="brave",
        retrieved_at="2026-08-05T00:00:00+00:00",
        raw_metadata={"raw_result": {"large": "payload"}},
    )


def entity(
    entity_id="entity_a",
    name="Example AI",
    evidence_ids=("ev_1",),
    evidence_urls=("https://example.ai",),
    domains=(),
    confidence=0.9,
    names_by_language=None,
    geographic_scope="global",
):
    return EntityCandidate(
        entity_id=entity_id,
        canonical_name=name,
        names_by_language=names_by_language or {"en": (name,)},
        primary_entity_kind=PrimaryEntityKind.OPERATING_COMPANY,
        entity_type_codes=("ai_native_company",),
        classification_facets={"business_focus": ("artificial_intelligence",)},
        related_entity_type_candidate_ids=("etc_1",),
        related_information_need_ids=("need_1",),
        related_target_career_path_ids=("path_1",),
        official_domain_candidates=domains,
        evidence_ids=evidence_ids,
        evidence_urls=evidence_urls,
        geographic_scope=geographic_scope,
        rationale="Phase 2 evidence supports the entity.",
        confidence=confidence,
        verification_status=EntityCandidateVerificationStatus.EVIDENCE_SUPPORTED,
        provenance={"source": "structured_extraction"},
    )


def universe(entities=None, evidence_items=None, conflicts=()):
    entities = tuple(entities or (entity(),))
    evidence_items = tuple(evidence_items or (evidence(),))
    plans = (plan("plan_1", "en"), plan("plan_zh", "zh"))
    return EntityUniverseResult(
        entity_discovery_plans=plans,
        entity_discovery_evidence=evidence_items,
        entity_candidates=entities,
        rejected_candidates=(),
        unresolved_identity_conflicts=tuple(conflicts),
        uncovered_entity_type_candidate_ids=(),
        diagnostics=(),
        execution_metadata=EntityUniverseExecutionMetadata(input_fingerprint="fp"),
        input_fingerprint="phase2_fp",
        output_hash="phase2_hash",
    )


def semantic_payload(
    entity_id,
    *,
    index=0,
    path_score=4,
    stage_score=4,
    stage_status="applicable",
    expected_score=4,
    strategic_score=4,
    need_ids=None,
    rationale_suffix="",
):
    need_ids = ["need_1"] if need_ids is None else need_ids
    return {
        "entity_id": entity_id,
        "path_relevance": dimension(path_score, "assessed", need_ids, rationale_suffix),
        "stage_relevance": dimension(
            stage_score,
            stage_status,
            need_ids,
            rationale_suffix,
        ),
        "expected_signal_potential": dimension(
            expected_score,
            "assessed",
            need_ids,
            rationale_suffix,
        ),
        "strategic_importance": dimension(
            strategic_score,
            "assessed",
            need_ids,
            rationale_suffix,
        ),
        "short_overall_rationale": "Relevant entity for future Source Discovery.",
    }


def dimension(score, status, need_ids, rationale_suffix=""):
    return {
        "score": score,
        "status": status,
        "rationale": f"Semantic assessment from injected fake client. {rationale_suffix}",
        "supporting_information_need_ids": need_ids,
        "limiting_factors": [],
        "review_flags": [],
    }


class EntityPriorityContextTests(unittest.TestCase):
    def test_duplicate_entity_records_are_consolidated_for_phase3_contexts(self):
        unresolved = OfficialDomainCandidate(
            domain="https://oracle.com/",
            evidence_url="https://oracle.com/about",
            confidence=0.5,
            verification_status=OfficialDomainVerificationStatus.UNRESOLVED,
            reason="Unresolved domain.",
        )
        probable = OfficialDomainCandidate(
            domain="www.oracle.com",
            evidence_url="https://www.oracle.com/",
            confidence=0.9,
            verification_status=OfficialDomainVerificationStatus.PROBABLE_OFFICIAL,
            reason="Probable official domain.",
        )
        oracle_a = entity(
            "entity_oracle",
            "Oracle",
            evidence_ids=("ev_oracle_a",),
            evidence_urls=("https://oracle.com/a",),
            domains=(unresolved,),
        )
        oracle_b = entity(
            "entity_oracle",
            "Oracle",
            evidence_ids=("ev_oracle_b",),
            evidence_urls=("https://oracle.com/b",),
            domains=(probable,),
            confidence=0.8,
        )
        others = tuple(
            entity(f"entity_{index:02d}", f"Company {index}")
            for index in range(61)
        )

        consolidated, diagnostics = consolidate_entity_universe_for_prioritization(
            entity_universe_result=universe(entities=(oracle_a, oracle_b) + others),
        )
        contexts = build_compact_entity_contexts(
            entity_universe_result=consolidated,
            max_evidence_per_entity=2,
        )
        oracle = next(
            item for item in consolidated.entity_candidates
            if item.entity_id == "entity_oracle"
        )

        self.assertEqual(len(consolidated.entity_candidates), 62)
        self.assertEqual(
            sum(1 for item in contexts if item["entity_id"] == "entity_oracle"),
            1,
        )
        self.assertEqual(oracle.evidence_ids, ("ev_oracle_a", "ev_oracle_b"))
        self.assertEqual(
            oracle.evidence_urls,
            ("https://oracle.com/a", "https://oracle.com/b"),
        )
        self.assertEqual(len(oracle.official_domain_candidates), 1)
        self.assertEqual(oracle.official_domain_candidates[0].domain, "oracle.com")
        self.assertEqual(
            oracle.official_domain_candidates[0].verification_status,
            OfficialDomainVerificationStatus.PROBABLE_OFFICIAL,
        )
        self.assertTrue(
            any(DUPLICATE_INPUT_CONSOLIDATION_DIAGNOSTIC in item for item in diagnostics)
        )

    def test_consolidation_does_not_merge_different_entity_ids(self):
        first = entity("entity_a", "Sequoia China")
        second = entity(
            "entity_b",
            "\u7ea2\u6749\u4e2d\u56fd",
            names_by_language={
                "en": ("Sequoia China",),
                "zh": ("\u7ea2\u6749\u4e2d\u56fd",),
            },
        )

        consolidated, diagnostics = consolidate_entity_universe_for_prioritization(
            entity_universe_result=universe(entities=(first, second)),
        )
        first_contexts = build_compact_entity_contexts(
            entity_universe_result=consolidated,
            max_evidence_per_entity=1,
        )
        second_contexts = build_compact_entity_contexts(
            entity_universe_result=consolidated,
            max_evidence_per_entity=1,
        )

        self.assertEqual(len(consolidated.entity_candidates), 2)
        self.assertEqual(diagnostics, ())
        self.assertEqual(first_contexts, second_contexts)

    def test_compact_context_excludes_raw_metadata_and_preserves_chinese_names(self):
        zh_entity = entity(
            names_by_language={"en": ("Example AI",), "zh": ("示例智能",)},
            evidence_ids=("ev_1", "ev_zh"),
            evidence_urls=("https://example.ai", "https://example.cn/report"),
        )
        contexts = build_compact_entity_contexts(
            entity_universe_result=universe(
                entities=(zh_entity,),
                evidence_items=(
                    evidence("ev_1"),
                    evidence("ev_zh", plan_id="plan_zh", url="https://example.cn/report"),
                ),
            ),
            max_evidence_per_entity=2,
        )

        self.assertEqual(contexts[0]["names_by_language"]["zh"], ["示例智能"])
        self.assertNotIn("raw_metadata", str(contexts))
        self.assertEqual(len(contexts[0]["representative_evidence"]), 2)
        self.assertTrue(
            any(item["language"] == "zh" for item in contexts[0]["representative_evidence"])
        )

    def test_representative_evidence_selection_is_deterministic(self):
        item = entity(evidence_ids=("ev_b", "ev_a"))
        evidence_items = (
            evidence("ev_b", url="https://b.example/item"),
            evidence("ev_a", url="https://a.example/item"),
        )
        first = build_compact_entity_contexts(
            entity_universe_result=universe(entities=(item,), evidence_items=evidence_items),
            max_evidence_per_entity=2,
        )
        second = build_compact_entity_contexts(
            entity_universe_result=universe(entities=(item,), evidence_items=tuple(reversed(evidence_items))),
            max_evidence_per_entity=2,
        )
        self.assertEqual(first, second)

    def test_real_chinese_name_is_preserved_with_ascii_source_literal(self):
        item = entity(
            names_by_language={
                "en": ("Example AI",),
                "zh": ("\u793a\u4f8b\u667a\u80fd",),
            }
        )
        contexts = build_compact_entity_contexts(
            entity_universe_result=universe(entities=(item,)),
            max_evidence_per_entity=1,
        )

        self.assertEqual(contexts[0]["names_by_language"]["zh"], ["\u793a\u4f8b\u667a\u80fd"])


class EntityPriorityPolicyTests(unittest.TestCase):
    def test_stage_not_applicable_renormalizes_remaining_weights(self):
        score, weights = calculate_entity_priority_score(
            path_relevance=SemanticDimensionAssessment(5, SemanticAssessmentStatus.ASSESSED, "x"),
            geography_relevance=calculate_geography_assessment(
                entity=entity(), user_preferences={"locations": ["China", "global"]}
            ),
            stage_relevance=SemanticDimensionAssessment(
                None,
                SemanticAssessmentStatus.NOT_APPLICABLE,
                "Not meaningful for this publisher.",
            ),
            expected_signal_potential=SemanticDimensionAssessment(
                5,
                SemanticAssessmentStatus.ASSESSED,
                "x",
            ),
            strategic_importance=SemanticDimensionAssessment(
                5,
                SemanticAssessmentStatus.ASSESSED,
                "x",
            ),
        )

        self.assertNotIn("stage_relevance", weights)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=5)
        self.assertGreaterEqual(score, 95)

    def test_geography_scoring_is_deterministic(self):
        assessment = calculate_geography_assessment(
            entity=entity(geographic_scope="china"),
            user_preferences={"preferred_locations": ["China", "APAC"]},
        )

        self.assertEqual(assessment.score, 90)
        self.assertEqual(assessment.conflicts, ())

    def test_evidence_readiness_is_deterministic_and_conflict_reduces_score(self):
        domain = OfficialDomainCandidate(
            domain="example.ai",
            evidence_url="https://example.ai",
            confidence=0.95,
            verification_status=OfficialDomainVerificationStatus.VERIFIED_OFFICIAL,
            reason="Official domain.",
        )
        clean = entity(domains=(domain,))
        conflicted = UnresolvedIdentityConflict(
            conflict_id="conflict_1",
            candidate_entity_ids=(clean.entity_id,),
            reason="conflicting verified official domains",
            evidence_ids=("ev_1",),
        )

        ready = calculate_evidence_readiness_assessment(
            entity=clean,
            entity_universe_result=universe(entities=(clean,)),
        )
        reduced = calculate_evidence_readiness_assessment(
            entity=clean,
            entity_universe_result=universe(entities=(clean,), conflicts=(conflicted,)),
        )

        self.assertGreater(ready.score, reduced.score)
        self.assertEqual(reduced.identity_conflict_status, "unresolved_conflict")


class EntityPrioritizerValidationTests(unittest.TestCase):
    def test_prioritization_prompt_closes_live_validation_schema_gaps(self):
        contexts = build_compact_entity_contexts(
            entity_universe_result=universe(),
            max_evidence_per_entity=1,
        )
        prompt = build_entity_prioritization_prompt(
            compact_entity_contexts=contexts,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
        )

        self.assertIn("use only IDs listed on that same compact", prompt)
        self.assertIn("related_information_need_ids", prompt)
        self.assertIn("must be null, not 0", prompt)
        self.assertIn('"score": null', prompt)
        self.assertIn("high publication frequency", prompt)
        self.assertIn("continuous signal generation", prompt)
        self.assertIn("Source Evaluation phases", prompt)
        self.assertIn("status is not_applicable, supporting_information_need_ids", prompt)
        self.assertIn('"supporting_information_need_ids": []', prompt)

    def test_semantic_assessments_come_from_injected_client(self):
        high = entity("entity_high", "High Priority AI", domains=())
        moderate_domain = OfficialDomainCandidate(
            domain="moderate.ai",
            evidence_url="https://moderate.ai",
            confidence=0.95,
            verification_status=OfficialDomainVerificationStatus.VERIFIED_OFFICIAL,
            reason="Official domain.",
        )
        moderate = entity(
            "entity_moderate",
            "Moderate Ready AI",
            domains=(moderate_domain,),
        )
        client = FakePriorityClient(
            overrides={
                "entity_high": {"path": 5, "stage": 5, "expected": 5, "strategic": 5},
                "entity_moderate": {"path": 3, "stage": 3, "expected": 3, "strategic": 3},
            }
        )
        result = prioritize_entities(
            entity_universe_result=universe(entities=(moderate, high)),
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={"preferred_locations": ["global"]},
            client=client,
            cache_enabled=False,
        )

        self.assertEqual(client.calls, 1)
        self.assertEqual(result.priority_assessments[0].entity_id, "entity_high")
        self.assertGreater(
            result.priority_assessments[0].entity_priority_score,
            result.priority_assessments[1].entity_priority_score,
        )
        self.assertLess(
            result.priority_assessments[0].evidence_readiness_score,
            result.priority_assessments[1].evidence_readiness_score,
        )

    def test_duplicate_unknown_invalid_score_and_hallucinated_need_are_rejected(self):
        contexts = build_compact_entity_contexts(
            entity_universe_result=universe(),
            max_evidence_per_entity=1,
        )
        suggestions = [
            semantic_payload("entity_a", path_score=6),
            semantic_payload("unknown_entity"),
            semantic_payload("entity_a"),
            semantic_payload("entity_a", need_ids=["hallucinated_need"]),
        ]

        accepted, rejected, _ = validate_entity_semantic_assessment_suggestions(
            suggestions=suggestions,
            entity_contexts=contexts,
            information_needs=(need(),),
            valid_entity_ids=("entity_a",),
        )

        self.assertEqual(accepted, ())
        self.assertEqual(len(rejected), 4)
        self.assertTrue(any("unknown entity_id" in item.rejection_reason for item in rejected))
        self.assertTrue(any("score must be between 0 and 5" in item.rejection_reason for item in rejected))
        self.assertTrue(any("unknown InformationNeed" in item.rejection_reason for item in rejected))

    def test_stage_statuses_and_source_level_claims_are_validated(self):
        contexts = build_compact_entity_contexts(
            entity_universe_result=universe(),
            max_evidence_per_entity=1,
        )
        valid_na = semantic_payload(
            "entity_a",
            stage_score=None,
            stage_status="not_applicable",
        )
        invalid_rss = semantic_payload(
            "entity_a",
            rationale_suffix="RSS available and publication frequency is daily.",
        )

        accepted, _, _ = validate_entity_semantic_assessment_suggestions(
            suggestions=[valid_na],
            entity_contexts=contexts,
            information_needs=(need(),),
            valid_entity_ids=("entity_a",),
        )
        _, rejected, _ = validate_entity_semantic_assessment_suggestions(
            suggestions=[invalid_rss],
            entity_contexts=contexts,
            information_needs=(need(),),
            valid_entity_ids=("entity_a",),
        )

        self.assertEqual(
            accepted[0].stage_relevance.status,
            SemanticAssessmentStatus.NOT_APPLICABLE,
        )
        self.assertEqual(len(rejected), 1)
        self.assertIn("observed-signal", rejected[0].rejection_reason)


class EntityPrioritizerCacheTests(unittest.TestCase):
    def test_cache_hit_avoids_deepseek_calls_and_round_trips_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_priorities.json"
            client = FakePriorityClient()
            first = prioritize_entities(
                entity_universe_result=universe(),
                information_needs=(need(),),
                target_career_paths=[target_path()],
                user_preferences={"preferred_locations": ["global"]},
                client=client,
                cache_file=cache_file,
            )
            guard = GuardPriorityClient()
            second = prioritize_entities(
                entity_universe_result=universe(),
                information_needs=(need(),),
                target_career_paths=[target_path()],
                user_preferences={"preferred_locations": ["global"]},
                client=guard,
                cache_file=cache_file,
            )

            self.assertEqual(first.generation_mode, "generated")
            self.assertEqual(second.generation_mode, "loaded_from_cache")
            self.assertEqual(guard.calls, 0)
            self.assertEqual(second.output_hash, first.output_hash)
            self.assertIsInstance(second, EntityPrioritizationResult)

    def test_batch_checkpoint_persists_raw_semantic_batches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_dir = Path(temp_dir) / "priority_batches"
            client = FakePriorityClient()
            prioritize_entities(
                entity_universe_result=universe(),
                information_needs=(need(),),
                target_career_paths=[target_path()],
                user_preferences={},
                client=client,
                cache_enabled=False,
                batch_checkpoint_dir=checkpoint_dir,
            )

            checkpoint = checkpoint_dir / "entity_priority_batch_1.json"
            self.assertTrue(checkpoint.exists())
            self.assertIn("entity_semantic_assessments", checkpoint.read_text())

    def test_force_refresh_bypasses_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_priorities.json"
            first_client = FakePriorityClient()
            prioritize_entities(
                entity_universe_result=universe(),
                information_needs=(need(),),
                target_career_paths=[target_path()],
                user_preferences={},
                client=first_client,
                cache_file=cache_file,
            )
            second_client = FakePriorityClient()
            prioritize_entities(
                entity_universe_result=universe(),
                information_needs=(need(),),
                target_career_paths=[target_path()],
                user_preferences={},
                client=second_client,
                cache_file=cache_file,
                force_refresh=True,
            )

            self.assertEqual(second_client.calls, 1)

    def test_scoring_policy_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_priorities.json"
            prioritize_entities(
                entity_universe_result=universe(),
                information_needs=(need(),),
                target_career_paths=[target_path()],
                user_preferences={},
                client=FakePriorityClient(),
                cache_file=cache_file,
            )
            second_client = FakePriorityClient()
            with patch(
                "src.source_monitoring.entity_prioritizer.scoring_policy_snapshot",
                return_value={"scoring_policy_version": "changed"},
            ):
                result = prioritize_entities(
                    entity_universe_result=universe(),
                    information_needs=(need(),),
                    target_career_paths=[target_path()],
                    user_preferences={},
                    client=second_client,
                    cache_file=cache_file,
                )

            self.assertEqual(second_client.calls, 1)
            self.assertEqual(result.generation_mode, "generated")

    def test_malformed_cache_is_diagnosed_and_incomplete_generation_not_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_priorities.json"
            cache_file.write_text("{malformed", encoding="utf-8")
            result = prioritize_entities(
                entity_universe_result=universe(),
                information_needs=(need(),),
                target_career_paths=[target_path()],
                user_preferences={},
                client=FakePriorityClient(),
                cache_file=cache_file,
            )

            self.assertTrue(any("malformed" in item for item in result.diagnostics))

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_priorities.json"
            first = entity("entity_a")
            second = entity("entity_b", "Second AI")
            partial_client = FakePriorityClient()

            def one_only(**kwargs):
                partial_client.calls += 1
                return {
                    "entity_semantic_assessments": [
                        semantic_payload(kwargs["compact_entity_contexts"][0]["entity_id"])
                    ]
                }

            partial_client.generate = one_only
            result = prioritize_entities(
                entity_universe_result=universe(entities=(first, second)),
                information_needs=(need(),),
                target_career_paths=[target_path()],
                user_preferences={},
                client=partial_client,
                cache_file=cache_file,
            )

            self.assertEqual(result.generation_mode, "partial")
            self.assertFalse(cache_file.exists())
            self.assertEqual(result.unassessed_entity_ids, ("entity_b",))

    def test_phase3_has_no_brave_path_and_no_database_migration(self):
        self.assertNotIn("search_client", prioritize_entities.__annotations__)
        migrations = list(Path("src/database/sql").glob("*.sql"))
        self.assertEqual(len(migrations), 7)

    def test_controlled_tier_values_only(self):
        result = prioritize_entities(
            entity_universe_result=universe(),
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            client=FakePriorityClient(),
            cache_enabled=False,
        )

        self.assertIsInstance(result.priority_assessments[0].priority_tier, PriorityTier)


if __name__ == "__main__":
    unittest.main()
