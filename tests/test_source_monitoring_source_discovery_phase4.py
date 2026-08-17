import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.models import RawItem, SourceType
from src.source_monitoring.cache import (
    load_cached_source_discovery_planning_result,
    load_cached_source_discovery_result,
)
from src.source_monitoring.entity_discovery_models import (
    OfficialDomainCandidate,
    OfficialDomainVerificationStatus,
    PrimaryEntityKind,
)
from src.source_monitoring.source_discovery import (
    _build_classifier_prompt,
    assemble_candidate_sources,
    build_source_discovery_plans,
    discover_candidate_sources,
    preclassify_source_evidence,
    rank_and_truncate_candidate_plans,
)
from src.source_monitoring.entity_prioritization_models import PriorityTier
from src.source_monitoring.source_discovery_identity import normalize_source_url
from src.source_monitoring.source_discovery_models import (
    SOURCE_DISCOVERY_PLAN_RANKING_POLICY_VERSION,
    CandidateOfficialityStatus,
    DiscoveryPlanStatus,
    DiscoveryStrategy,
    SourceDiscoveryBudget,
    SourceDiscoveryEvidence,
    SourceFormatHint,
    SourceRole,
)
from src.source_monitoring.source_role_ontology import (
    applicable_source_roles,
    get_source_role_ontology,
)
from src.storage import save_json
from tests.test_source_monitoring_entity_prioritization import (
    FakePriorityClient,
    entity,
    need,
    prioritize_entities,
    target_path,
    universe,
)


class FakeSearchClient:
    dry_run = True

    def __init__(self, results_by_query=None):
        self.calls = []
        self.results_by_query = results_by_query or {}

    def search(self, search_plan):
        self.calls.append(search_plan)
        return self.results_by_query.get(
            search_plan.query_text,
            [
                RawItem(
                    source_type=SourceType.SEARCH_API,
                    title="Example AI Newsroom",
                    organization="example.ai",
                    url="https://www.example.ai/newsroom/?utm_source=test#top",
                    published_at=None,
                    raw_text="Official newsroom for Example AI.",
                    metadata={"provider": "brave", "language": "en"},
                )
            ],
        )


class GuardSearchClient:
    dry_run = True

    def __init__(self):
        self.calls = 0

    def search(self, search_plan):
        self.calls += 1
        raise AssertionError("search client should not be called")


class FakeClassifierClient:
    provider = "deepseek"
    model = "fake-classifier"

    def __init__(self, classifications):
        self.classifications = classifications
        self.calls = []

    def classify(self, **kwargs):
        self.calls.append(kwargs["evidence_items"])
        return {"classifications": list(self.classifications)}


class EntityScopedClassifierClient:
    provider = "deepseek"
    model = "fake-entity-scoped-classifier"

    def __init__(self):
        self.calls = []

    def classify(self, **kwargs):
        evidence_items = kwargs["evidence_items"]
        entity_ids = {item.entity_id for item in evidence_items}
        if len(entity_ids) != 1:
            raise AssertionError("ambiguous classifier batches must not mix entities")
        entity_id = next(iter(entity_ids))
        self.calls.append(
            {
                "entity_id": entity_id,
                "context_entity_id": kwargs["entity_context"]["entity_id"],
                "evidence_count": len(evidence_items),
            }
        )
        return {
            "classifications": [
                {
                    "evidence_id": item.evidence_id,
                    "url": item.normalized_url,
                    "source_role": "official_homepage",
                    "officiality_status": "unresolved",
                    "decision": "needs_review",
                    "confidence": 0.61,
                    "rationale": "Ambiguous homepage-like result needs review.",
                    "review_flags": ["needs_domain_verification"],
                }
                for item in evidence_items
            ]
        }


class SourceDiscoveryPhase4Tests(unittest.TestCase):
    def test_role_ontology_is_controlled_versioned_and_bilingual(self):
        roles = get_source_role_ontology()
        codes = {item.source_role.value for item in roles}

        self.assertIn(SourceRole.NEWSROOM.value, codes)
        self.assertIn(SourceRole.CAREERS.value, codes)
        self.assertNotIn("explicit_feed_candidate", codes)
        self.assertTrue(all(item.ontology_version == "source_role_ontology_v1" for item in roles))
        self.assertTrue(any("官网" in item.chinese_aliases for item in roles))
        self.assertIn(
            SourceRole.PORTFOLIO,
            applicable_source_roles(PrimaryEntityKind.INVESTMENT_FIRM),
        )
        self.assertNotIn(
            SourceRole.PORTFOLIO,
            applicable_source_roles(PrimaryEntityKind.PUBLIC_SECTOR_BODY),
        )

    def test_source_format_hint_is_independent_from_source_role(self):
        self.assertEqual(SourceFormatHint.HTML_PAGE.value, "html_page")
        self.assertEqual(SourceFormatHint.RSS_CANDIDATE.value, "rss_candidate")
        self.assertEqual(SourceFormatHint.ATOM_CANDIDATE.value, "atom_candidate")
        self.assertEqual(SourceFormatHint.UNKNOWN.value, "unknown")
        self.assertIn(SourceRole.NEWSROOM.value, {role.value for role in SourceRole})
        self.assertNotIn("rss_candidate", {role.value for role in SourceRole})

    def test_budget_policy_and_planning_keep_all_entities_auditable(self):
        phase2 = universe(
            entities=tuple(
                entity(
                    f"entity_{index:02d}",
                    f"Company {index}",
                    domains=(
                        OfficialDomainCandidate(
                            domain=f"company{index}.ai",
                            evidence_url=f"https://company{index}.ai",
                            confidence=0.95,
                            verification_status=OfficialDomainVerificationStatus.PROBABLE_OFFICIAL,
                            reason="Probable official domain.",
                        ),
                    ) if index % 2 == 0 else (),
                )
                for index in range(62)
            )
        )
        phase3 = prioritize_entities(
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            client=FakePriorityClient(),
            cache_enabled=False,
        )

        planning = build_source_discovery_plans(
            entity_prioritization_result=phase3,
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            cache_enabled=False,
        )

        self.assertEqual(len(planning.budgets), 62)
        self.assertEqual({budget.entity_id for budget in planning.budgets}, {a.entity_id for a in phase3.priority_assessments})
        self.assertTrue(all(budget.allocated_plan_count <= budget.maximum_plan_count for budget in planning.budgets))
        self.assertTrue(any(budget.allocated_plan_count < budget.maximum_plan_count for budget in planning.budgets))
        self.assertTrue(all(plan.entity_id for plan in planning.plans))
        self.assertTrue(all(isinstance(plan.source_role, SourceRole) for plan in planning.plans))
        self.assertTrue(all(plan.query_language in {"en", "zh"} for plan in planning.plans))
        self.assertTrue(all(isinstance(plan.strategy, DiscoveryStrategy) for plan in planning.plans))
        self.assertTrue(all(plan.query for plan in planning.plans))
        self.assertFalse(any(hasattr(plan, "source_roles") for plan in planning.plans))

    def test_domain_first_name_first_bilingual_dedup_and_deferred_plans(self):
        domain = OfficialDomainCandidate(
            domain="https://www.example.ai/",
            evidence_url="https://example.ai",
            confidence=0.95,
            verification_status=OfficialDomainVerificationStatus.PROBABLE_OFFICIAL,
            reason="Probable official domain.",
        )
        phase2 = universe(
            entities=(
                entity(
                    names_by_language={
                        "en": ("Example AI",),
                        "zh": ("示例智能",),
                    },
                    domains=(domain,),
                ),
            )
        )
        phase3 = prioritize_entities(
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            client=FakePriorityClient(),
            cache_enabled=False,
        )
        planning = build_source_discovery_plans(
            entity_prioritization_result=phase3,
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            cache_enabled=False,
        )

        executable = [p for p in planning.plans if p.status == DiscoveryPlanStatus.EXECUTABLE]
        self.assertTrue(all("site:example.ai" in plan.query for plan in executable))
        self.assertTrue({plan.query_language for plan in planning.plans}.issuperset({"en", "zh"}))
        self.assertTrue(planning.deferred_plan_ids)
        self.assertEqual(
            [plan.candidate_plan_rank for plan in planning.plans],
            sorted(plan.candidate_plan_rank for plan in planning.plans),
        )

        no_domain = entity("entity_no_domain", "No Domain AI", domains=())
        phase2_no_domain = universe(entities=(no_domain,))
        phase3_no_domain = prioritize_entities(
            entity_universe_result=phase2_no_domain,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            client=FakePriorityClient(),
            cache_enabled=False,
        )
        name_first = build_source_discovery_plans(
            entity_prioritization_result=phase3_no_domain,
            entity_universe_result=phase2_no_domain,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            cache_enabled=False,
        )
        self.assertTrue(any(plan.strategy == DiscoveryStrategy.IDENTITY_RESOLUTION for plan in name_first.plans))
        self.assertTrue(any('"No Domain AI" official website' in plan.query for plan in name_first.plans))
        name_first_executable = [
            plan
            for plan in name_first.plans
            if plan.status == DiscoveryPlanStatus.EXECUTABLE
        ]
        self.assertTrue(name_first_executable)
        self.assertTrue(
            all(plan.source_role == SourceRole.OFFICIAL_HOMEPAGE for plan in name_first_executable)
        )

        identity_plan = next(
            plan
            for plan in name_first.plans
            if plan.strategy == DiscoveryStrategy.IDENTITY_RESOLUTION
        )
        role_plan = replace(
            identity_plan,
            plan_id="role_first_plan",
            source_role=SourceRole.NEWSROOM,
            strategy=DiscoveryStrategy.NAME_FIRST,
            query='"No Domain AI" newsroom',
            ranking_score=identity_plan.ranking_score - 20,
            status=DiscoveryPlanStatus.DEFERRED_BUDGET_LIMIT,
            deferral_reason="budget_limit",
        )
        identity_first = rank_and_truncate_candidate_plans(
            candidate_plans=(role_plan, identity_plan),
            budget=SourceDiscoveryBudget(
                entity_id="entity_no_domain",
                priority_tier=PriorityTier.TIER_A_IMMEDIATE,
                maximum_plan_count=4,
                allocated_plan_count=0,
                readiness_score=80,
                needs_domain_verification=True,
                low_evidence_readiness=False,
                probable_official_domain=None,
                rationale="Test identity-first rank policy.",
            ),
        )
        deferred_role_plan = next(plan for plan in identity_first if plan.plan_id == "role_first_plan")
        self.assertEqual(deferred_role_plan.status, DiscoveryPlanStatus.DEFERRED_UNRESOLVED_DOMAIN)
        self.assertEqual(
            deferred_role_plan.deferral_reason,
            "identity_resolution_required_before_role_discovery",
        )

        fallback = rank_and_truncate_candidate_plans(
            candidate_plans=(role_plan,),
            budget=SourceDiscoveryBudget(
                entity_id="entity_no_domain",
                priority_tier=PriorityTier.TIER_A_IMMEDIATE,
                maximum_plan_count=1,
                allocated_plan_count=0,
                readiness_score=80,
                needs_domain_verification=True,
                low_evidence_readiness=False,
                probable_official_domain=None,
                rationale="Test fallback when no identity plan exists.",
            ),
        )
        self.assertEqual(fallback[0].status, DiscoveryPlanStatus.EXECUTABLE)

    def test_url_normalization_preserves_paths_and_removes_tracking(self):
        first = normalize_source_url("HTTPS://WWW.Example.AI:443/news/?utm_source=x&a=1#frag")
        second = normalize_source_url("https://example.ai/news?a=1")
        different = normalize_source_url("https://example.ai/research?a=1")
        chinese = normalize_source_url("https://例子.cn/新闻/?utm_campaign=x")

        self.assertEqual(first, second)
        self.assertNotEqual(first, different)
        self.assertIn("%E6%96%B0%E9%97%BB", chinese)

    def test_execution_is_fake_bounded_checkpointed_and_cacheable(self):
        phase2 = universe(entities=(entity(domains=(OfficialDomainCandidate(
            domain="example.ai",
            evidence_url="https://example.ai",
            confidence=0.95,
            verification_status=OfficialDomainVerificationStatus.PROBABLE_OFFICIAL,
            reason="Probable official domain.",
        ),)),))
        phase3 = prioritize_entities(
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            client=FakePriorityClient(),
            cache_enabled=False,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            planning = build_source_discovery_plans(
                entity_prioritization_result=phase3,
                entity_universe_result=phase2,
                information_needs=(need(),),
                target_career_paths=[target_path()],
                user_preferences={},
                cache_file=root / "source_discovery_plans.json",
            )
            fake = FakeSearchClient()
            result = discover_candidate_sources(
                planning_result=planning,
                entity_prioritization_result=phase3,
                entity_universe_result=phase2,
                information_needs=(need(),),
                search_client=fake,
                classifier_client=FakeClassifierClient(()),
                max_plans=1,
                cache_file=root / "candidate_sources.json",
                checkpoint_dir=root / "checkpoints",
            )

            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(len(result.executions), 1)
            self.assertTrue(list((root / "checkpoints").glob("source_discovery_batch_*.json")))
            self.assertEqual(result.generation_mode, "partial")
            self.assertFalse((root / "candidate_sources.json").exists())

            complete = discover_candidate_sources(
                planning_result=planning,
                entity_prioritization_result=phase3,
                entity_universe_result=phase2,
                information_needs=(need(),),
                search_client=fake,
                classifier_client=FakeClassifierClient(()),
                cache_file=root / "candidate_sources.json",
                checkpoint_dir=root / "checkpoints2",
            )
            self.assertEqual(complete.generation_mode, "complete")
            guard = GuardSearchClient()
            cached = discover_candidate_sources(
                planning_result=planning,
                entity_prioritization_result=phase3,
                entity_universe_result=phase2,
                information_needs=(need(),),
                search_client=guard,
                classifier_client=FakeClassifierClient(()),
                cache_file=root / "candidate_sources.json",
            )
            self.assertEqual(cached.generation_mode, "loaded_from_cache")
            self.assertEqual(guard.calls, 0)

    def test_preclassification_rejects_obvious_bad_pages_and_hints_feeds(self):
        domain = OfficialDomainCandidate(
            domain="example.ai",
            evidence_url="https://example.ai",
            confidence=0.95,
            verification_status=OfficialDomainVerificationStatus.PROBABLE_OFFICIAL,
            reason="Probable official domain.",
        )
        phase2 = universe(entities=(entity(domains=(domain,)),))
        phase3 = prioritize_entities(
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            client=FakePriorityClient(),
            cache_enabled=False,
        )
        planning = build_source_discovery_plans(
            entity_prioritization_result=phase3,
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            cache_enabled=False,
        )
        plan = next(p for p in planning.plans if p.status == DiscoveryPlanStatus.EXECUTABLE)

        def evidence_for(url):
            return discover_candidate_sources(
                planning_result=planning,
                entity_prioritization_result=phase3,
                entity_universe_result=phase2,
                information_needs=(need(),),
                search_client=FakeSearchClient({plan.query: [RawItem(SourceType.SEARCH_API, "x", "x", url, None, "x", {"provider": "brave"})]}),
                classifier_client=FakeClassifierClient(()),
                max_plans=1,
                cache_enabled=False,
            ).evidence[0]

        job = preclassify_source_evidence(evidence_for("https://example.ai/jobs/12345678"), plan, phase2.entity_candidates[0])
        social = preclassify_source_evidence(evidence_for("https://linkedin.com/company/example-ai"), plan, phase2.entity_candidates[0])
        feed = preclassify_source_evidence(evidence_for("https://example.ai/newsroom/rss.xml"), plan, phase2.entity_candidates[0])

        self.assertEqual(job["reason"], "individual_job_detail_page")
        self.assertEqual(social["reason"], "unsupported_social_media_profile")
        self.assertEqual(feed["decision"], "accept")
        self.assertEqual(SourceFormatHint.RSS_CANDIDATE, SourceFormatHint("rss_candidate"))

    def test_ambiguous_classifier_is_batched_and_validated(self):
        phase2 = universe(entities=(entity(domains=()),))
        phase3 = prioritize_entities(
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            client=FakePriorityClient(),
            cache_enabled=False,
        )
        planning = build_source_discovery_plans(
            entity_prioritization_result=phase3,
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            cache_enabled=False,
        )
        plan = next(p for p in planning.plans if p.status == DiscoveryPlanStatus.EXECUTABLE)
        raw_items = [
            RawItem(SourceType.SEARCH_API, "About Lab", "unknown.ai", "https://unknown.ai/about", None, "Maybe official.", {"provider": "brave"}),
            RawItem(SourceType.SEARCH_API, "Research Lab", "unknown.ai", "https://unknown.ai/resources", None, "Maybe official.", {"provider": "brave"}),
        ]
        temp_classifier = FakeClassifierClient(())
        with tempfile.TemporaryDirectory() as temp_dir:
            preliminary = discover_candidate_sources(
                planning_result=planning,
                entity_prioritization_result=phase3,
                entity_universe_result=phase2,
                information_needs=(need(),),
                search_client=FakeSearchClient({plan.query: raw_items}),
                classifier_client=temp_classifier,
                max_plans=1,
                cache_enabled=False,
                checkpoint_dir=Path(temp_dir),
            )
            evidence = preliminary.evidence
            classifier = FakeClassifierClient(
                (
                    {
                        "evidence_id": evidence[0].evidence_id,
                        "url": evidence[0].normalized_url,
                        "source_role": "other_official_section",
                        "officiality_status": "unresolved",
                        "decision": "needs_review",
                        "confidence": 0.62,
                        "rationale": "Ambiguous but plausible durable section.",
                        "review_flags": ["needs_domain_verification"],
                    },
                    {
                        "evidence_id": evidence[1].evidence_id,
                        "url": "https://invented.example/not-supplied",
                        "source_role": "invented_role",
                        "decision": "accept",
                        "confidence": 0.9,
                        "rationale": "publishes daily and has valid RSS",
                    },
                )
            )
            accepted, rejected, review, _ = assemble_candidate_sources(
                evidence=evidence,
                plans=planning.plans,
                entity_universe_result=phase2,
                entity_prioritization_result=phase3,
                information_needs=(need(),),
                classifier_client=classifier,
                classifier_batch_size=20,
                checkpoint_dir=Path(temp_dir),
                classifier_model="fake",
            )

        self.assertEqual(len(classifier.calls), 1)
        self.assertEqual(accepted, ())
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0].candidate_officiality_status, CandidateOfficialityStatus.UNRESOLVED)
        self.assertEqual(len(rejected), 1)
        self.assertIn("invented_url", rejected[0].rejection_reason)

    def test_ambiguous_classifier_prompt_is_json_mode_compatible(self):
        phase2 = universe(entities=(entity(domains=()),))
        phase3 = prioritize_entities(
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            client=FakePriorityClient(),
            cache_enabled=False,
        )
        planning = build_source_discovery_plans(
            entity_prioritization_result=phase3,
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            cache_enabled=False,
        )
        plan = next(p for p in planning.plans if p.status == DiscoveryPlanStatus.EXECUTABLE)
        preliminary = discover_candidate_sources(
            planning_result=planning,
            entity_prioritization_result=phase3,
            entity_universe_result=phase2,
            information_needs=(need(),),
            search_client=FakeSearchClient(
                {
                    plan.query: [
                        RawItem(
                            SourceType.SEARCH_API,
                            "About Lab",
                            "unknown.ai",
                            "https://unknown.ai/about",
                            None,
                            "Maybe official.",
                            {"provider": "brave"},
                        )
                    ]
                }
            ),
            classifier_client=FakeClassifierClient(()),
            max_plans=1,
            cache_enabled=False,
        )

        prompt = _build_classifier_prompt(
            entity_context={"entity_id": phase2.entity_candidates[0].entity_id},
            priority_context={"entity_id": phase3.priority_assessments[0].entity_id},
            information_needs=(need(),),
            evidence_items=preliminary.evidence,
            controlled_roles=tuple(applicable_source_roles(phase2.entity_candidates[0].primary_entity_kind)),
        )

        self.assertIn("valid JSON", prompt)
        self.assertIn("classifications", prompt)

    def test_ambiguous_classifier_batches_are_entity_scoped(self):
        phase2 = universe(
            entities=(
                entity("entity_alpha", "Alpha AI", domains=()),
                entity("entity_beta", "Beta AI", domains=()),
            )
        )
        phase3 = prioritize_entities(
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            client=FakePriorityClient(),
            cache_enabled=False,
        )
        planning = build_source_discovery_plans(
            entity_prioritization_result=phase3,
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            cache_enabled=False,
        )
        plan_by_entity = {
            entity_id: next(
                plan
                for plan in planning.plans
                if plan.entity_id == entity_id
                and plan.status == DiscoveryPlanStatus.EXECUTABLE
            )
            for entity_id in ("entity_alpha", "entity_beta")
        }
        evidence = tuple(
            SourceDiscoveryEvidence(
                evidence_id=f"evidence_{entity_id}",
                execution_id=f"execution_{entity_id}",
                plan_id=plan.plan_id,
                entity_id=entity_id,
                result_rank=1,
                title="About Lab",
                url=f"https://{entity_id}.example/about",
                normalized_url=f"https://{entity_id}.example/about",
                root_domain=f"{entity_id}.example",
                snippet="Maybe official.",
                language="en",
                provider="brave",
                raw_metadata={},
                retrieved_at="2026-08-07T00:00:00+00:00",
            )
            for entity_id, plan in plan_by_entity.items()
        )
        classifier = EntityScopedClassifierClient()

        with tempfile.TemporaryDirectory() as temp_dir:
            accepted, rejected, review, diagnostics = assemble_candidate_sources(
                evidence=evidence,
                plans=planning.plans,
                entity_universe_result=phase2,
                entity_prioritization_result=phase3,
                information_needs=(need(),),
                classifier_client=classifier,
                classifier_batch_size=20,
                checkpoint_dir=Path(temp_dir),
                classifier_model="fake",
            )

        self.assertEqual(accepted, ())
        self.assertEqual(rejected, ())
        self.assertEqual(diagnostics, ())
        self.assertEqual(len(review), 2)
        self.assertEqual(len(classifier.calls), 2)
        self.assertEqual(
            {call["entity_id"] for call in classifier.calls},
            {"entity_alpha", "entity_beta"},
        )
        self.assertTrue(
            all(
                call["entity_id"] == call["context_entity_id"]
                for call in classifier.calls
            )
        )

    def test_ambiguous_classifier_accepts_role_aliases_and_plain_rejects(self):
        phase2 = universe(entities=(entity(domains=()),))
        phase3 = prioritize_entities(
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            client=FakePriorityClient(),
            cache_enabled=False,
        )
        planning = build_source_discovery_plans(
            entity_prioritization_result=phase3,
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            cache_enabled=False,
        )
        plan = next(p for p in planning.plans if p.status == DiscoveryPlanStatus.EXECUTABLE)
        first = SourceDiscoveryEvidence(
            evidence_id="evidence_alias",
            execution_id="execution_alias",
            plan_id=plan.plan_id,
            entity_id=plan.entity_id,
            result_rank=1,
            title="About Lab",
            url="https://unknown.example/about",
            normalized_url="https://unknown.example/about",
            root_domain="unknown.example",
            snippet="Maybe official.",
            language="en",
            provider="brave",
            raw_metadata={},
            retrieved_at="2026-08-07T00:00:00+00:00",
        )
        second = SourceDiscoveryEvidence(
            evidence_id="evidence_reject",
            execution_id="execution_reject",
            plan_id=plan.plan_id,
            entity_id=plan.entity_id,
            result_rank=2,
            title="Directory Result",
            url="https://directory.example/company",
            normalized_url="https://directory.example/company",
            root_domain="directory.example",
            snippet="Not official.",
            language="en",
            provider="brave",
            raw_metadata={},
            retrieved_at="2026-08-07T00:00:00+00:00",
        )
        classifier = FakeClassifierClient(
            (
                {
                    "evidence_id": first.evidence_id,
                    "url": first.normalized_url,
                    "role": "official_homepage",
                    "officiality_status": "unresolved",
                    "decision": "needs_review",
                    "confidence": 0.61,
                    "reason": "Alias role key accepted.",
                    "review_flags": ["needs_domain_verification"],
                },
                {
                    "evidence_id": second.evidence_id,
                    "url": second.normalized_url,
                    "decision": "reject",
                },
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            accepted, rejected, review, diagnostics = assemble_candidate_sources(
                evidence=(first, second),
                plans=planning.plans,
                entity_universe_result=phase2,
                entity_prioritization_result=phase3,
                information_needs=(need(),),
                classifier_client=classifier,
                classifier_batch_size=20,
                checkpoint_dir=Path(temp_dir),
                classifier_model="fake",
            )

        self.assertEqual(accepted, ())
        self.assertEqual(diagnostics, ())
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0].source_role, SourceRole.OFFICIAL_HOMEPAGE)
        self.assertEqual(review[0].rationale, "Alias role key accepted.")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].rejection_reason, "classifier_rejected")

    def test_planning_and_execution_cache_diagnostics(self):
        phase2 = universe()
        phase3 = prioritize_entities(
            entity_universe_result=phase2,
            information_needs=(need(),),
            target_career_paths=[target_path()],
            user_preferences={},
            client=FakePriorityClient(),
            cache_enabled=False,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            planning_file = root / "source_discovery_plans.json"
            planning = build_source_discovery_plans(
                entity_prioritization_result=phase3,
                entity_universe_result=phase2,
                information_needs=(need(),),
                target_career_paths=[target_path()],
                user_preferences={},
                cache_file=planning_file,
            )
            loaded, diagnostics = load_cached_source_discovery_planning_result(
                planning_file,
                planning.input_fingerprint,
            )
            self.assertIsNotNone(loaded)
            self.assertEqual(diagnostics, ())

            payload = json.loads(planning_file.read_text(encoding="utf-8"))
            payload["plan_ranking_policy_version"] = "changed"
            payload["input_fingerprint"] = "changed"
            save_json(payload, planning_file)
            missed, _ = load_cached_source_discovery_planning_result(
                planning_file,
                planning.input_fingerprint,
            )
            self.assertIsNone(missed)

            malformed = root / "candidate_sources.json"
            malformed.write_text("{bad", encoding="utf-8")
            execution, execution_diagnostics = load_cached_source_discovery_result(
                malformed,
                "fingerprint",
            )
            self.assertIsNone(execution)
            self.assertTrue(any("could not be read" in item for item in execution_diagnostics))
            self.assertEqual(SOURCE_DISCOVERY_PLAN_RANKING_POLICY_VERSION, "source_discovery_plan_ranking_policy_v1_1")


if __name__ == "__main__":
    unittest.main()
