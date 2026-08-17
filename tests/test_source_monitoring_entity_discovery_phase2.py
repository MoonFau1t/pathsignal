import tempfile
import unittest
from pathlib import Path

from src.models import CareerPathCategory, RawItem, SourceType, TargetCareerPath
from src.search_api_client import SearchAPIError
from src.source_monitoring.entity_candidate_extractor import (
    validate_entity_candidate_suggestions,
)
from src.source_monitoring.entity_classifier import classify_entity_type_codes
from src.source_monitoring.entity_discovery_executor import (
    execute_entity_discovery_plans,
)
from src.source_monitoring.entity_discovery_models import (
    ENTITY_UNIVERSE_RESULT_SCHEMA_VERSION,
    EntityDiscoveryEvidence,
    EntityDiscoveryPlan,
    EntityDiscoveryQuery,
    OfficialDomainCandidate,
    OfficialDomainVerificationStatus,
    PrimaryEntityKind,
)
from src.source_monitoring.entity_discovery_planner import (
    EntityDiscoveryPlanningError,
    plan_entity_discovery,
    validate_and_build_entity_discovery_plans,
)
from src.source_monitoring.entity_identity import (
    build_entity_candidate_id,
    normalize_domain,
    normalize_evidence_url,
    normalize_organization_name,
    resolve_entity_identities,
)
from src.source_monitoring.entity_type_ontology import get_entity_type_ontology
from src.source_monitoring.identity import build_entity_type_expansion_output_hash
from src.source_monitoring.models import (
    EntityTypeCandidate,
    EntityTypeExpansionResult,
    InformationNeed,
    InformationNeedPriority,
    LLMExecutionMetadata,
    MonitoringObjectiveCode,
)
from src.source_monitoring.entity_universe import build_entity_universe


class FakeExtractionClient:
    provider = "deepseek"
    model = "fake-extraction"

    def __init__(self):
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        ev = kwargs["entity_discovery_evidence"][0]
        return {
            "entity_candidates": [
                {
                    "canonical_name": "Example AI",
                    "names_by_language": {"en": ["Example AI"]},
                    "primary_entity_kind": "operating_company",
                    "entity_type_codes": ["ai_native_company"],
                    "classification_facets": {
                        "business_focus": ["artificial_intelligence"]
                    },
                    "official_domain_candidates": [
                        {
                            "domain": "example.ai",
                            "evidence_url": ev.url,
                            "confidence": 0.95,
                            "verification_status": "verified_official",
                            "reason": "Official result.",
                        }
                    ],
                    "supporting_evidence_ids": [ev.evidence_id],
                    "geographic_scope": "global",
                    "rationale": "Evidence supports Example AI.",
                    "confidence": 0.95,
                }
            ]
        }


class FailingExtractionClient:
    provider = "deepseek"
    model = "fake-extraction"

    def __init__(self):
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        raise RuntimeError("provider unavailable")


class GuardClient:
    def __init__(self, provider="deepseek", model="guard"):
        self.provider = provider
        self.model = model
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        raise AssertionError("guard client should not be called")


class FakePlanningClient:
    provider = "deepseek"
    model = "fake-planning"

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_kwargs = None

    def generate(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return self.payload


class FakeSearchClient:
    def __init__(self):
        self.calls = []

    def search(self, search_plan):
        self.calls.append(search_plan)
        return [
            RawItem(
                source_type=SourceType.SEARCH_API,
                title="Example AI official website",
                organization="www.example.ai",
                url="https://www.example.ai/?utm_source=test",
                published_at=None,
                raw_text="Example AI is an enterprise AI software company.",
                metadata={
                    "provider": "brave",
                    "retrieved_at": "2026-08-05T00:00:00+00:00",
                    "raw_result": {"rank": 1},
                },
            )
        ]


class PartiallyFailingSearchClient:
    def __init__(self):
        self.calls = []

    def search(self, search_plan):
        self.calls.append(search_plan)
        if len(self.calls) == 1:
            raise SearchAPIError("simulated Brave batch failure")

        return [
            RawItem(
                source_type=SourceType.SEARCH_API,
                title="Example AI official website",
                organization="example.ai",
                url="https://example.ai",
                published_at=None,
                raw_text="Example AI is an enterprise AI software company.",
                metadata={"provider": "brave"},
            )
        ]


def need(need_id="need_1", path_id="path_1"):
    return InformationNeed(
        information_need_id=need_id,
        need_key=f"{need_id}_key",
        objective_code=MonitoringObjectiveCode.ORGANIZATION,
        title="AI organization signals",
        description="Track organizations relevant to AI strategy.",
        related_target_career_path_ids=(path_id,),
        signal_examples=("funding",),
        rationale="Relevant to monitoring.",
        priority=InformationNeedPriority.HIGH,
        confidence=0.9,
    )


def path(path_id="path_1"):
    return TargetCareerPath(
        path_id=path_id,
        title="AI Strategy",
        category=CareerPathCategory.AI_STRATEGY,
        description="AI strategy path",
        fit_score=0.9,
    )


def candidate(code="venture_capital_firm", candidate_id="etc_vc", confidence=0.9):
    return EntityTypeCandidate(
        candidate_id=candidate_id,
        entity_type_code=code,
        display_name=code.replace("_", " ").title(),
        related_information_need_ids=("need_1",),
        related_target_career_path_ids=("path_1",),
        supported_monitoring_objectives=(MonitoringObjectiveCode.ORGANIZATION,),
        rationale="Relevant entity type.",
        discovery_terms=("venture capital firms", "风险投资机构"),
        confidence=confidence,
    )


def query(
    query_id="query_1",
    text="venture capital firms official website",
    language="en",
    code="venture_capital_firm",
    candidate_id="etc_vc",
):
    return EntityDiscoveryQuery(
        query_id=query_id,
        query_text=text,
        language=language,
        region="global" if language == "en" else "china",
        entity_type_code=code,
        related_entity_type_candidate_id=candidate_id,
        related_information_need_ids=("need_1",),
        discovery_intent="concrete_entity_discovery",
    )


def plan_obj(candidate_obj=None, query_obj=None):
    candidate_obj = candidate_obj or candidate()
    query_obj = query_obj or query(
        code=candidate_obj.entity_type_code,
        candidate_id=candidate_obj.candidate_id,
    )
    return EntityDiscoveryPlan(
        plan_id="plan_1",
        entity_type_candidate_id=candidate_obj.candidate_id,
        entity_type_code=candidate_obj.entity_type_code,
        queries=(query_obj,),
        language=query_obj.language,
        region=query_obj.region,
        max_results=5,
        priority=0.9,
        confidence=candidate_obj.confidence,
    )


def evidence(evidence_id="ev_1", plan_id="plan_1", query_id="query_1"):
    return EntityDiscoveryEvidence(
        evidence_id=evidence_id,
        plan_id=plan_id,
        query_id=query_id,
        result_rank=1,
        title="Example AI official website",
        snippet="Example AI builds enterprise AI software.",
        url="https://example.ai",
        displayed_domain="example.ai",
        search_provider="brave",
        retrieved_at="2026-08-05T00:00:00+00:00",
        raw_metadata={"provider": "brave"},
    )


class EntityDiscoveryPlanningTests(unittest.TestCase):
    def test_deepseek_query_content_is_used_and_languages_stay_separate(self):
        payload = {
            "entity_discovery_queries": [
                {
                    "entity_type_code": "venture_capital_firm",
                    "query_text": "AI venture capital firms official website",
                    "language": "en",
                    "region": "global",
                    "discovery_intent": "concrete_entity_discovery",
                    "related_information_need_ids": ["need_1"],
                    "rationale": "Find concrete firms.",
                },
                {
                    "entity_type_code": "venture_capital_firm",
                    "query_text": "人工智能 风险投资机构 官方网站",
                    "language": "zh",
                    "region": "china",
                    "discovery_intent": "concrete_entity_discovery",
                    "related_information_need_ids": ["need_1"],
                    "rationale": "Find Chinese-language concrete firms.",
                },
            ]
        }
        client = FakePlanningClient(payload)

        plans, diagnostics = plan_entity_discovery(
            entity_type_candidates=(candidate(),),
            information_needs=(need(),),
            target_career_paths=[path()],
            user_preferences={"preferred_regions": ["China", "US"]},
            client=client,
            languages=("en", "zh"),
            regions=("global", "china"),
            max_plans=4,
            max_queries_per_type=4,
        )

        self.assertEqual(client.calls, 1)
        self.assertEqual(len(plans), 2)
        self.assertEqual({p.language for p in plans}, {"en", "zh"})
        self.assertEqual(
            {p.queries[0].query_text for p in plans},
            {
                "AI venture capital firms official website",
                "人工智能 风险投资机构 官方网站",
            },
        )
        self.assertNotIn("job", " ".join(p.queries[0].query_text for p in plans))
        self.assertTrue(all(p.plan_id.startswith("entity_plan_") for p in plans))
        self.assertEqual(diagnostics, ())

    def test_invalid_deepseek_output_is_rejected_without_static_fallback(self):
        proposals = [
            {
                "entity_type_code": "venture_capital_firm",
                "query_text": "venture capital jobs apply now",
                "language": "en",
                "region": "global",
                "discovery_intent": "jobs",
                "related_information_need_ids": ["need_1"],
                "rationale": "Bad opportunity intent.",
            },
            {
                "entity_type_code": "venture_capital_firm",
                "query_text": "https://example.com/rss.xml",
                "language": "en",
                "region": "global",
                "discovery_intent": "source",
                "related_information_need_ids": ["need_1"],
                "rationale": "Bad source object.",
            },
        ]

        plans, diagnostics = validate_and_build_entity_discovery_plans(
            proposals=proposals,
            entity_type_candidates=(candidate(),),
            information_needs=(need(),),
            languages=("en", "zh"),
            regions=("global", "china"),
            max_plans=4,
            max_results_per_plan=5,
            max_queries_per_type=4,
        )

        self.assertEqual(plans, ())
        self.assertTrue(any("current jobs" in item for item in diagnostics))
        self.assertTrue(any("URL" in item for item in diagnostics))

    def test_general_subject_query_is_rejected_as_not_concrete_entity_discovery(self):
        proposal = {
            "entity_type_code": "venture_capital_firm",
            "query_text": "AI investment trends market analysis",
            "language": "en",
            "region": "global",
            "discovery_intent": "trend_research",
            "related_information_need_ids": ["need_1"],
            "rationale": "Too broad.",
        }

        plans, diagnostics = validate_and_build_entity_discovery_plans(
            proposals=[proposal],
            entity_type_candidates=(candidate(),),
            information_needs=(need(),),
            languages=("en",),
            regions=("global",),
            max_plans=4,
            max_results_per_plan=5,
            max_queries_per_type=4,
        )

        self.assertEqual(plans, ())
        self.assertTrue(any("concrete organizations" in item for item in diagnostics))

    def test_entity_directories_lists_and_agencies_are_concrete_discovery(self):
        proposals = [
            {
                "entity_type_code": "startup_accelerator",
                "query_text": "startup accelerators list",
                "language": "en",
                "region": "global",
                "discovery_intent": "concrete_entity_discovery",
                "related_information_need_ids": ["need_1"],
                "rationale": "Find accelerator organizations.",
            },
            {
                "entity_type_code": "startup_accelerator",
                "query_text": "创业加速器名录",
                "language": "zh",
                "region": "china",
                "discovery_intent": "concrete_entity_discovery",
                "related_information_need_ids": ["need_1"],
                "rationale": "Find Chinese accelerator organizations.",
            },
        ]
        accelerator = candidate("startup_accelerator", "etc_accelerator")

        plans, diagnostics = validate_and_build_entity_discovery_plans(
            proposals=proposals,
            entity_type_candidates=(accelerator,),
            information_needs=(need(),),
            languages=("en", "zh"),
            regions=("global", "china"),
            max_plans=2,
            max_results_per_plan=5,
            max_queries_per_type=2,
        )

        self.assertEqual({plan.language for plan in plans}, {"en", "zh"})
        self.assertFalse(any("concrete organizations" in item for item in diagnostics))

    def test_missing_configured_language_is_explicitly_diagnosed(self):
        proposal = {
            "entity_type_code": "venture_capital_firm",
            "query_text": "venture capital firms official website",
            "language": "en",
            "region": "global",
            "discovery_intent": "concrete_entity_discovery",
            "related_information_need_ids": ["need_1"],
            "rationale": "Find firms.",
        }

        plans, diagnostics = validate_and_build_entity_discovery_plans(
            proposals=[proposal],
            entity_type_candidates=(candidate(),),
            information_needs=(need(),),
            languages=("en", "zh"),
            regions=("global", "china"),
            max_plans=4,
            max_results_per_plan=5,
            max_queries_per_type=4,
        )

        self.assertEqual(len(plans), 1)
        self.assertTrue(any("missing validated zh" in item for item in diagnostics))

    def test_english_proposals_cannot_consume_chinese_quota(self):
        proposals = [
            {
                "entity_type_code": "venture_capital_firm",
                "query_text": "venture capital firms official website",
                "language": "en",
                "region": "global",
                "discovery_intent": "concrete_entity_discovery",
                "related_information_need_ids": ["need_1"],
                "rationale": "Find firms.",
            },
            {
                "entity_type_code": "venture_capital_firm",
                "query_text": "AI venture capital firms organization directory",
                "language": "en",
                "region": "global",
                "discovery_intent": "concrete_entity_discovery",
                "related_information_need_ids": ["need_1"],
                "rationale": "Second English proposal.",
            },
            {
                "entity_type_code": "venture_capital_firm",
                "query_text": "风险投资机构 官方网站",
                "language": "zh",
                "region": "china",
                "discovery_intent": "concrete_entity_discovery",
                "related_information_need_ids": ["need_1"],
                "rationale": "Find Chinese firms.",
            },
        ]

        plans, diagnostics = validate_and_build_entity_discovery_plans(
            proposals=proposals,
            entity_type_candidates=(candidate(),),
            information_needs=(need(),),
            languages=("en", "zh"),
            regions=("global", "china"),
            max_plans=2,
            max_results_per_plan=5,
            max_queries_per_type=2,
        )

        self.assertEqual({plan.language for plan in plans}, {"en", "zh"})
        self.assertTrue(any("per-type language query limit" in item for item in diagnostics))

    def test_impossible_language_or_plan_capacity_fails_before_planning(self):
        with self.assertRaisesRegex(EntityDiscoveryPlanningError, "max_queries_per_type"):
            validate_and_build_entity_discovery_plans(
                proposals=[],
                entity_type_candidates=(candidate(),),
                information_needs=(need(),),
                languages=("en", "zh"),
                regions=("global", "china"),
                max_plans=2,
                max_results_per_plan=5,
                max_queries_per_type=1,
            )

        with self.assertRaisesRegex(EntityDiscoveryPlanningError, "max_plans"):
            validate_and_build_entity_discovery_plans(
                proposals=[],
                entity_type_candidates=(candidate(), candidate("ai_native_company", "etc_ai")),
                information_needs=(need(),),
                languages=("en", "zh"),
                regions=("global", "china"),
                max_plans=3,
                max_results_per_plan=5,
                max_queries_per_type=2,
            )

    def test_extra_valid_but_unsupported_need_ids_are_trimmed(self):
        vc_candidate = candidate()
        proposals = [
            {
                "entity_type_code": "venture_capital_firm",
                "query_text": "venture capital firms official website",
                "language": "en",
                "region": "global",
                "discovery_intent": "concrete_entity_discovery",
                "related_information_need_ids": ["need_1", "need_extra"],
                "rationale": "Find firms.",
            },
            {
                "entity_type_code": "venture_capital_firm",
                "query_text": "风险投资机构 官方网站",
                "language": "zh",
                "region": "china",
                "discovery_intent": "concrete_entity_discovery",
                "related_information_need_ids": ["need_1"],
                "rationale": "Find Chinese firms.",
            },
        ]

        plans, diagnostics = validate_and_build_entity_discovery_plans(
            proposals=proposals,
            entity_type_candidates=(vc_candidate,),
            information_needs=(need(), need("need_extra")),
            languages=("en", "zh"),
            regions=("global", "china"),
            max_plans=2,
            max_results_per_plan=5,
            max_queries_per_type=2,
        )

        en_query = next(plan.queries[0] for plan in plans if plan.language == "en")
        self.assertEqual(en_query.related_information_need_ids, ("need_1",))
        self.assertTrue(any("unsupported related_information_need_ids removed" in item for item in diagnostics))

    def test_low_confidence_candidate_receives_smaller_budget(self):
        low = candidate("venture_studio", "etc_studio", confidence=0.5)
        proposal = {
            "entity_type_code": "venture_studio",
            "query_text": "venture studio organizations official website",
            "language": "en",
            "region": "global",
            "discovery_intent": "concrete_entity_discovery",
            "related_information_need_ids": ["need_1"],
            "rationale": "Find concrete venture studios.",
        }

        plans, _ = validate_and_build_entity_discovery_plans(
            proposals=[proposal],
            entity_type_candidates=(low,),
            information_needs=(need(),),
            languages=("en",),
            regions=("global",),
            max_plans=4,
            max_results_per_plan=5,
            max_queries_per_type=4,
        )

        self.assertEqual(plans[0].max_results, 2)
        self.assertTrue(plans[0].planning_notes)


class EntityDiscoveryExecutionTests(unittest.TestCase):
    def test_brave_results_retain_plan_and_query_provenance(self):
        search = FakeSearchClient()
        plan = plan_obj()

        evidence_items, diagnostics = execute_entity_discovery_plans(
            plans=(plan,),
            search_client=search,
        )

        self.assertEqual(diagnostics, ())
        self.assertEqual(len(search.calls), 1)
        self.assertEqual(len(evidence_items), 1)
        item = evidence_items[0]
        self.assertEqual(item.plan_id, plan.plan_id)
        self.assertEqual(item.query_id, plan.queries[0].query_id)
        self.assertEqual(item.result_rank, 1)
        self.assertEqual(item.displayed_domain, "example.ai")
        self.assertEqual(item.raw_metadata["entity_discovery_plan_id"], plan.plan_id)


class EntityExtractionIdentityClassificationTests(unittest.TestCase):
    def test_search_results_are_not_automatically_entities(self):
        candidates, rejected, diagnostics = validate_entity_candidate_suggestions(
            suggestions=[],
            entity_discovery_evidence=(evidence(),),
            entity_discovery_plans=(plan_obj(),),
            entity_type_candidates=(candidate("ai_native_company", "etc_ai"),),
            max_entities_per_type=5,
        )

        self.assertEqual(candidates, ())
        self.assertEqual(rejected, ())
        self.assertEqual(diagnostics, ())

    def test_valid_entity_extracted_and_false_positives_rejected(self):
        ai_candidate = candidate("ai_native_company", "etc_ai")
        ev = evidence()
        suggestions = [
            {
                "canonical_name": "智谱AI",
                "names_by_language": {"zh": ["智谱AI"], "en": ["Zhipu AI"]},
                "primary_entity_kind": "operating_company",
                "entity_type_codes": ["ai_native_company"],
                "classification_facets": {"business_focus": ["artificial_intelligence"]},
                "official_domain_candidates": [
                    {
                        "domain": "https://www.zhipuai.cn/path?utm_source=x",
                        "evidence_url": ev.url,
                        "confidence": 0.9,
                        "verification_status": "verified_official",
                        "reason": "Official site result.",
                    }
                ],
                "supporting_evidence_ids": [ev.evidence_id],
                "geographic_scope": "china",
                "rationale": "Evidence names a concrete AI company.",
                "confidence": 0.9,
            },
            {
                "canonical_name": "Top AI companies to watch in 2026",
                "primary_entity_kind": "operating_company",
                "entity_type_codes": ["ai_native_company"],
                "supporting_evidence_ids": [ev.evidence_id],
                "confidence": 0.8,
            },
        ]

        candidates, rejected, _ = validate_entity_candidate_suggestions(
            suggestions=suggestions,
            entity_discovery_evidence=(ev,),
            entity_discovery_plans=(plan_obj(ai_candidate),),
            entity_type_candidates=(ai_candidate,),
            max_entities_per_type=5,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].names_by_language["zh"], ("智谱AI",))
        self.assertEqual(candidates[0].official_domain_candidates[0].domain, "zhipuai.cn")
        self.assertEqual(len(rejected), 1)

    def test_invalid_llm_primary_kind_is_overridden_not_rejected(self):
        ai_candidate = candidate("ai_native_company", "etc_ai")
        ev = evidence()
        suggestions = [
            {
                "canonical_name": "Example AI",
                "names_by_language": {"en": ["Example AI"]},
                "primary_entity_kind": "company",
                "entity_type_codes": ["ai_native_company"],
                "classification_facets": {},
                "official_domain_candidates": [],
                "supporting_evidence_ids": [ev.evidence_id],
                "geographic_scope": "global",
                "rationale": "Evidence names a concrete AI company.",
                "confidence": 0.8,
            }
        ]

        candidates, rejected, diagnostics = validate_entity_candidate_suggestions(
            suggestions=suggestions,
            entity_discovery_evidence=(ev,),
            entity_discovery_plans=(plan_obj(ai_candidate),),
            entity_type_candidates=(ai_candidate,),
            max_entities_per_type=5,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(rejected, ())
        self.assertEqual(candidates[0].primary_entity_kind, PrimaryEntityKind.OPERATING_COMPANY)
        self.assertTrue(any("uncontrolled primary_entity_kind" in item for item in diagnostics))

    def test_domain_normalization_and_chinese_name_normalization(self):
        self.assertEqual(
            normalize_domain("https://www.Example.AI/path?utm_source=x"),
            "example.ai",
        )
        self.assertEqual(
            normalize_evidence_url("https://www.example.ai/path?utm_source=x&a=1"),
            "https://example.ai/path?a=1",
        )
        self.assertEqual(normalize_organization_name(" 智谱AI "), "智谱ai")

    def test_same_verified_domain_merges_but_similar_names_do_not(self):
        one = _entity("Example AI", "example.ai", "entity_a")
        two = _entity("示例智能", "example.ai", "entity_b")
        three = _entity("Example A.I.", "", "entity_c")

        merged, conflicts = resolve_entity_identities((one, two, three))

        self.assertEqual(conflicts, ())
        self.assertEqual(len(merged), 2)
        self.assertTrue(
            any(
                item.official_domain_candidates
                and item.official_domain_candidates[0].domain == "example.ai"
                and "zh" in item.names_by_language
                for item in merged
            )
        )

    def test_conflicting_official_domains_remain_auditable(self):
        one = _entity("Example AI", "example.ai", "entity_a")
        two = _entity("Example AI", "example.cn", "entity_b")

        merged, conflicts = resolve_entity_identities((one, two))

        self.assertEqual(len(merged), 2)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("conflicting", conflicts[0].reason)

    def test_primary_kind_and_facets_are_controlled_and_nonexclusive(self):
        primary, facets = classify_entity_type_codes(
            (
                "ai_native_company",
                "growth_stage_company",
                "portfolio_company",
            )
        )
        self.assertEqual(primary, PrimaryEntityKind.OPERATING_COMPANY)
        self.assertEqual(
            facets["business_focus"],
            ("artificial_intelligence",),
        )
        self.assertEqual(facets["lifecycle_stage"], ("growth_stage",))
        self.assertEqual(facets["capital_relationship"], ("portfolio_backed",))

        primary, facets = classify_entity_type_codes(("corporate_venture_capital",))
        self.assertEqual(primary, PrimaryEntityKind.INVESTMENT_FIRM)
        self.assertEqual(
            facets["investment_model"],
            ("corporate_venture_capital",),
        )


def _entity(name, domain, entity_id):
    official = ()
    verified_domains = ()
    if domain:
        official = (
            OfficialDomainCandidate(
                domain=domain,
                evidence_url=f"https://{domain}",
                confidence=0.9,
                verification_status=OfficialDomainVerificationStatus.VERIFIED_OFFICIAL,
                reason="verified",
            ),
        )
        verified_domains = (domain,)

    language = "zh" if any("\u4e00" <= char <= "\u9fff" for char in name) else "en"
    return EntityDiscoveryCandidateFactory.create(
        entity_id=build_entity_candidate_id(
            canonical_name=name,
            official_domains=verified_domains,
            entity_type_codes=("ai_native_company",),
        )
        if entity_id == "stable"
        else entity_id,
        canonical_name=name,
        names_by_language={language: (name,)},
        official_domain_candidates=official,
    )


class EntityDiscoveryCandidateFactory:
    @staticmethod
    def create(
        *,
        entity_id,
        canonical_name,
        names_by_language,
        official_domain_candidates,
    ):
        from src.source_monitoring.entity_discovery_models import (
            EntityCandidate,
            EntityCandidateVerificationStatus,
        )

        return EntityCandidate(
            entity_id=entity_id,
            canonical_name=canonical_name,
            names_by_language=names_by_language,
            primary_entity_kind=PrimaryEntityKind.OPERATING_COMPANY,
            entity_type_codes=("ai_native_company",),
            classification_facets={"business_focus": ("artificial_intelligence",)},
            related_entity_type_candidate_ids=("etc_ai",),
            related_information_need_ids=("need_1",),
            related_target_career_path_ids=("path_1",),
            official_domain_candidates=official_domain_candidates,
            evidence_ids=("ev_1",),
            evidence_urls=("https://example.ai",),
            geographic_scope="global",
            rationale="test",
            confidence=0.9,
            verification_status=EntityCandidateVerificationStatus.EVIDENCE_SUPPORTED,
        )


class EntityUniverseServiceCacheTests(unittest.TestCase):
    def test_cache_hit_avoids_deepseek_brave_and_extraction_calls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_universe.json"
            planning = FakePlanningClient(_planning_payload("ai_native_company"))
            search = FakeSearchClient()
            extraction = FakeExtractionClient()
            result = build_entity_universe(
                entity_type_expansion_result=_phase1_result(
                    (candidate("ai_native_company", "etc_ai"),)
                ),
                information_needs=(need(),),
                target_career_paths=[path()],
                user_preferences={},
                planning_client=planning,
                search_client=search,
                extraction_client=extraction,
                cache_file=cache_file,
                languages=("en",),
                regions=("global",),
                max_plans=2,
            )

            self.assertEqual(result.generation_mode, "generated")
            self.assertEqual(planning.calls, 1)
            self.assertEqual(len(search.calls), 1)
            self.assertEqual(extraction.calls, 1)

            guard_planning = GuardClient(model="fake-planning")
            guard_extraction = GuardClient(model="fake-extraction")
            cached = build_entity_universe(
                entity_type_expansion_result=_phase1_result(
                    (candidate("ai_native_company", "etc_ai"),)
                ),
                information_needs=(need(),),
                target_career_paths=[path()],
                user_preferences={},
                planning_client=guard_planning,
                search_client=FakeSearchClient(),
                extraction_client=guard_extraction,
                cache_file=cache_file,
                languages=("en",),
                regions=("global",),
                max_plans=2,
            )

            self.assertEqual(cached.generation_mode, "loaded_from_cache")
            self.assertEqual(guard_planning.calls, 0)
            self.assertEqual(guard_extraction.calls, 0)
            self.assertEqual(cached.output_hash, result.output_hash)

    def test_force_refresh_bypasses_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_universe.json"
            planning = FakePlanningClient(_planning_payload("ai_native_company"))
            build_entity_universe(
                entity_type_expansion_result=_phase1_result(
                    (candidate("ai_native_company", "etc_ai"),)
                ),
                information_needs=(need(),),
                target_career_paths=[path()],
                user_preferences={},
                planning_client=planning,
                search_client=FakeSearchClient(),
                extraction_client=FakeExtractionClient(),
                cache_file=cache_file,
                languages=("en",),
                regions=("global",),
                max_plans=2,
            )
            build_entity_universe(
                entity_type_expansion_result=_phase1_result(
                    (candidate("ai_native_company", "etc_ai"),)
                ),
                information_needs=(need(),),
                target_career_paths=[path()],
                user_preferences={},
                force_refresh=True,
                planning_client=planning,
                search_client=FakeSearchClient(),
                extraction_client=FakeExtractionClient(),
                cache_file=cache_file,
                languages=("en",),
                regions=("global",),
                max_plans=2,
            )

            self.assertEqual(planning.calls, 2)

    def test_extraction_failure_is_not_saved_as_generated_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_universe.json"
            result = build_entity_universe(
                entity_type_expansion_result=_phase1_result(
                    (candidate("ai_native_company", "etc_ai"),)
                ),
                information_needs=(need(),),
                target_career_paths=[path()],
                user_preferences={},
                planning_client=FakePlanningClient(_planning_payload("ai_native_company")),
                search_client=FakeSearchClient(),
                extraction_client=FailingExtractionClient(),
                cache_file=cache_file,
                languages=("en",),
                regions=("global",),
                max_plans=2,
            )

            self.assertEqual(result.generation_mode, "unavailable")
            self.assertFalse(cache_file.exists())

    def test_partial_planning_result_is_not_saved_as_generated_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_universe.json"
            result = build_entity_universe(
                entity_type_expansion_result=_phase1_result(
                    (candidate("ai_native_company", "etc_ai"),)
                ),
                information_needs=(need(),),
                target_career_paths=[path()],
                user_preferences={},
                planning_client=FakePlanningClient(_planning_payload("ai_native_company")),
                search_client=FakeSearchClient(),
                extraction_client=FakeExtractionClient(),
                cache_file=cache_file,
                languages=("en", "zh"),
                regions=("global", "china"),
                max_queries_per_type=2,
                max_plans=2,
            )

            self.assertEqual(result.generation_mode, "partial")
            self.assertFalse(cache_file.exists())

    def test_partial_brave_execution_is_not_saved_as_generated_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_universe.json"
            result = build_entity_universe(
                entity_type_expansion_result=_phase1_result(
                    (candidate("ai_native_company", "etc_ai"),)
                ),
                information_needs=(need(),),
                target_career_paths=[path()],
                user_preferences={},
                planning_client=FakePlanningClient(
                    {
                        "entity_discovery_queries": [
                            {
                                "entity_type_code": "ai_native_company",
                                "query_text": "AI companies official website",
                                "language": "en",
                                "region": "global",
                                "discovery_intent": "concrete_entity_discovery",
                                "related_information_need_ids": ["need_1"],
                                "rationale": "Find companies.",
                            },
                            {
                                "entity_type_code": "ai_native_company",
                                "query_text": "人工智能公司 官方网站",
                                "language": "zh",
                                "region": "china",
                                "discovery_intent": "concrete_entity_discovery",
                                "related_information_need_ids": ["need_1"],
                                "rationale": "Find Chinese companies.",
                            },
                        ]
                    }
                ),
                search_client=PartiallyFailingSearchClient(),
                extraction_client=FakeExtractionClient(),
                cache_file=cache_file,
                languages=("en", "zh"),
                regions=("global", "china"),
                max_queries_per_type=2,
                max_plans=2,
            )

            self.assertEqual(result.generation_mode, "partial")
            self.assertFalse(cache_file.exists())

    def test_malformed_cache_is_diagnosed_and_every_type_is_accounted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "entity_universe.json"
            phase1 = _phase1_result(
                (
                    candidate("ai_native_company", "etc_ai"),
                    candidate("venture_capital_firm", "etc_vc"),
                )
            )
            planning = FakePlanningClient(_planning_payload("ai_native_company"))
            first = build_entity_universe(
                entity_type_expansion_result=phase1,
                information_needs=(need(),),
                target_career_paths=[path()],
                user_preferences={},
                planning_client=planning,
                search_client=FakeSearchClient(),
                extraction_client=FakeExtractionClient(),
                cache_file=cache_file,
                languages=("en",),
                regions=("global",),
                max_plans=2,
            )
            cache_file.write_text(
                "{"
                f"\"schema_version\":\"{ENTITY_UNIVERSE_RESULT_SCHEMA_VERSION}\","
                f"\"input_fingerprint\":\"{first.input_fingerprint}\","
                "\"entity_candidates\":\"bad\""
                "}",
                encoding="utf-8",
            )
            second = build_entity_universe(
                entity_type_expansion_result=phase1,
                information_needs=(need(),),
                target_career_paths=[path()],
                user_preferences={},
                planning_client=planning,
                search_client=FakeSearchClient(),
                extraction_client=FakeExtractionClient(),
                cache_file=cache_file,
                languages=("en",),
                regions=("global",),
                max_plans=2,
            )

            self.assertTrue(any("malformed" in item for item in second.diagnostics))
            self.assertIn("etc_vc", second.uncovered_entity_type_candidate_ids)
            self.assertTrue(second.entity_candidates)
            self.assertTrue(second.output_hash)

    def test_no_database_migration_is_added_for_phase2(self):
        migration_files = sorted(Path("src/database/sql").glob("*.sql"))
        self.assertTrue(migration_files)
        self.assertFalse(
            any("entity_universe" in path.name for path in migration_files)
        )


def _planning_payload(code):
    return {
        "entity_discovery_queries": [
            {
                "entity_type_code": code,
                "query_text": "AI companies official website",
                "language": "en",
                "region": "global",
                "discovery_intent": "concrete_entity_discovery",
                "related_information_need_ids": ["need_1"],
                "rationale": "Find concrete companies.",
            }
        ]
    }


def _phase1_result(candidates):
    output_hash = build_entity_type_expansion_output_hash(
        canonical_candidates=tuple(candidates),
        proposed_new_types=(),
        rejected_suggestions=(),
        uncovered_information_need_ids=(),
    )
    return EntityTypeExpansionResult(
        canonical_entity_types=get_entity_type_ontology(),
        canonical_candidates=tuple(candidates),
        proposed_new_types=(),
        rejected_suggestions=(),
        uncovered_information_need_ids=(),
        diagnostics=(),
        llm_execution_metadata=LLMExecutionMetadata(
            provider="deepseek",
            model="fake-phase1",
            prompt_version="phase1",
        ),
        input_fingerprint="phase1_input",
        output_hash=output_hash,
    )


if __name__ == "__main__":
    unittest.main()
