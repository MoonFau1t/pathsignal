import json
from pathlib import Path
import unittest

from src.source_monitoring.entity_discovery_models import PrimaryEntityKind
from src.source_monitoring.source_discovery_models import (
    CandidateOfficialityStatus,
    CandidateSource,
    CandidateSourceStatus,
    SourceDiscoveryResult,
    SourceFormatHint,
    SourceRole,
)
from src.source_monitoring.source_evaluation_identity import (
    build_final_source_evaluation_id,
    build_initial_source_evaluation_id,
    build_observed_source_evidence_id,
    build_semantic_text_window_id,
    build_source_evaluation_plan_id,
    build_source_evaluation_result_hash,
    build_source_fetch_execution_id,
    build_source_fetch_request_fingerprint,
    build_source_inspection_id,
    build_source_observation_plan_id,
    build_source_semantic_bundle_fingerprint,
    build_source_semantic_evidence_bundle_id,
)
from src.source_monitoring.source_evaluation_models import (
    DEFAULT_SEMANTIC_TEXT_WINDOW_MAX_CHARS,
    SOURCE_EVALUATION_RESULT_SCHEMA_VERSION,
    UNTRUSTED_WEBPAGE_EVIDENCE_MARKER,
    AssessmentMethod,
    EntityMatchAssessment,
    EntityMatchStatus,
    EvaluationConfidence,
    EvaluationScope,
    FeedLinkHint,
    FetchMethod,
    FetchStatus,
    FetchedPage,
    FinalEvaluationDecision,
    FinalSourceEvaluation,
    InformationNeedRelevanceAssessment,
    InitialEvaluationDecision,
    InitialSourceEvaluation,
    ObservedSignalPotential,
    ObservedSignalPotentialLevel,
    ObservedSourceEvidence,
    ObservationSamplingStrategy,
    ObservationStatus,
    OfficialityAssessment,
    OfficialityStatus,
    PageType,
    PageTypeAssessment,
    RawPageArtifactRef,
    RedirectHop,
    RelevanceLevel,
    SemanticTextWindow,
    SemanticTextWindowType,
    SourceEvaluationPlan,
    SourceEvaluationResult,
    SourceFetchExecution,
    SourceFetchRequest,
    SourceInspection,
    SourceObservationPlan,
    SourceObservationResult,
    SourceRoleAssessment,
    SourceRoleMatchStatus,
    SourceSemanticEvidenceBundle,
    SourceValueLevel,
    SurfaceDurabilityAssessment,
    SurfaceDurabilityStatus,
    eligible_phase5_candidate_sources,
    validate_source_evaluation_plans,
    validate_source_evaluation_result_references,
)


class Phase5ASourceEvaluationContractTests(unittest.TestCase):
    def test_contract_round_trip_preserves_semantic_equality_and_hashes(self):
        objects = make_contract_graph()
        for model in objects["round_trip_models"]:
            parsed = type(model).from_dict(json.loads(json.dumps(model.to_dict())))
            self.assertEqual(parsed, model)

        result = objects["result"]
        parsed_result = SourceEvaluationResult.from_dict(
            json.loads(json.dumps(result.to_dict()))
        )
        self.assertEqual(parsed_result, result)
        self.assertEqual(parsed_result.output_hash, result.output_hash)
        self.assertEqual(validate_source_evaluation_result_references(result), ())

    def test_identity_helpers_are_deterministic_and_exclude_fetch_occurrence_time(self):
        first = build_source_fetch_request_fingerprint(
            requested_url="https://www.Example.com/news?utm_source=x",
            method=FetchMethod.GET,
            timeout_seconds=10,
            max_response_bytes=1000,
            max_redirects=2,
            accepted_content_types=("text/html",),
            user_agent_policy_version="ua",
            fetch_policy_version="fetch",
        )
        second = build_source_fetch_request_fingerprint(
            requested_url="https://example.com/news",
            method=FetchMethod.GET,
            timeout_seconds=10,
            max_response_bytes=1000,
            max_redirects=2,
            accepted_content_types=("text/html",),
            user_agent_policy_version="ua",
            fetch_policy_version="fetch",
        )
        self.assertEqual(first, second)

        exec_one = build_source_fetch_execution_id(
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
            request_fingerprint=first,
            final_url="https://example.com/news",
            fetch_status=FetchStatus.COMPLETED_HTML.value,
            raw_body_sha256="a" * 64,
        )
        exec_two = build_source_fetch_execution_id(
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
            request_fingerprint=first,
            final_url="https://example.com/news",
            fetch_status=FetchStatus.COMPLETED_HTML.value,
            raw_body_sha256="a" * 64,
        )
        self.assertEqual(exec_one, exec_two)

    def test_fetch_contract_keeps_network_facts_separate_from_semantic_rejection(self):
        request = SourceFetchRequest(
            requested_url="https://example.com/news",
            method=FetchMethod.GET,
            timeout_seconds=10,
            max_response_bytes=1024,
            max_redirects=3,
            accepted_content_types=("text/html",),
            user_agent_policy_version="ua_v1",
            fetch_policy_version="fetch_v1",
            request_fingerprint="f" * 64,
        )
        self.assertEqual(request.method, FetchMethod.GET)
        with self.assertRaises(ValueError):
            SourceFetchRequest.from_dict({**request.to_dict(), "method": "POST"})
        with self.assertRaises(ValueError):
            FetchStatus("semantic_rejected")

    def test_raw_artifact_ref_allows_html_reference_but_models_reject_raw_html(self):
        artifact = make_artifact(content_type="text/html; charset=utf-8")
        self.assertEqual(artifact.content_type.split(";", 1)[0], "text/html")

        window = make_window(text="News and research updates.")
        inspection = make_inspection(artifact=artifact, windows=(window,))
        self.assertNotIn("raw_html", inspection.to_dict())
        self.assertNotIn("raw_body", inspection.to_dict())

        with self.assertRaises(ValueError):
            SourceSemanticEvidenceBundle.from_dict(
                {**make_bundle(windows=(window,)).to_dict(), "raw_html": "<html></html>"}
            )
        with self.assertRaises(ValueError):
            SemanticTextWindow(
                window_id="bad",
                window_type=SemanticTextWindowType.MAIN_CONTENT_EXCERPT,
                source_location="main",
                text="<script>alert(1)</script>",
                character_count=len("<script>alert(1)</script>"),
                structural_context=None,
                evidence_provenance={},
            )

        page = FetchedPage(
            fetch_execution_id="fetch",
            response_metadata={"content_type": "text/html"},
            raw_bytes=b"<html>secret</html>",
            decoded_text="<html>secret</html>",
            raw_artifact_ref=artifact,
        )
        serialized = json.dumps(page.to_dict())
        self.assertNotIn("secret", serialized)
        self.assertIn("runtime_payload_omitted", serialized)

    def test_semantic_windows_are_bounded(self):
        with self.assertRaises(ValueError):
            make_window(text="x" * (DEFAULT_SEMANTIC_TEXT_WINDOW_MAX_CHARS + 1))

    def test_llm_boundary_cannot_invent_roles_needs_or_initial_approval(self):
        with self.assertRaises(ValueError):
            SourceRoleAssessment.from_dict(
                {
                    "planned_source_role": "newsroom",
                    "observed_source_role": "rss_candidate",
                    "source_role_match_status": "match",
                    "confidence": "high",
                    "rationale": "",
                    "evidence_refs": [],
                    "assessment_method": "llm",
                }
            )

        with self.assertRaises(ValueError):
            InformationNeedRelevanceAssessment(
                allowed_information_need_ids=("need_a",),
                supported_information_need_ids=("need_b",),
                relevance_level=RelevanceLevel.HIGH,
                confidence=EvaluationConfidence.HIGH,
                rationale="",
                evidence_refs=(),
                assessment_method=AssessmentMethod.LLM,
            )

        initial = make_initial_evaluation()
        with self.assertRaises(ValueError):
            InitialSourceEvaluation.from_dict(
                {**initial.to_dict(), "decision": "approved_for_acquisition"}
            )

        with self.assertRaises(ValueError):
            FeedLinkHint(
                href="https://example.com/feed.xml",
                rel="alternate",
                mime_type="application/rss+xml",
                verification_status="verified",
            )

        with self.assertRaises(ValueError):
            ObservedSignalPotential(
                observed_signal_potential_id="signal",
                source_observation_result_id="obs",
                level=ObservedSignalPotentialLevel.HIGH,
                sampled_item_count=2,
                relevant_item_count=1,
                information_need_hit_count={"need_a": 1},
                supporting_observed_evidence_ids=("evidence",),
                rationale="bounded sample only",
                limitations=("not long-term history",),
                supporting_metrics={"weekly_cadence": "claimed"},
            )

    def test_observation_and_final_decision_contracts_are_bounded(self):
        with self.assertRaises(ValueError):
            SourceObservationPlan(
                source_observation_plan_id="obs_plan",
                candidate_source_id="candidate",
                initial_source_evaluation_id="initial",
                sampling_strategy=ObservationSamplingStrategy.BOUNDED_SOURCE_SAMPLE,
                max_item_count=1000,
                lookback_window_days=None,
                observation_policy_version="obs_v1",
                input_fingerprint="fp",
            )

        final = make_final_evaluation()
        self.assertEqual(
            final.final_decision,
            FinalEvaluationDecision.APPROVED_FOR_ACQUISITION,
        )
        with self.assertRaises(ValueError):
            FinalSourceEvaluation.from_dict(
                {**final.to_dict(), "final_decision": "approved_for_monitoring"}
            )

    def test_source_evaluation_plan_reuses_phase4_source_role_and_format_hint(self):
        candidate = make_candidate()
        plan = make_plan(candidate=candidate)
        self.assertEqual(plan.planned_source_role, SourceRole.NEWSROOM)
        self.assertEqual(plan.planned_source_format_hint, SourceFormatHint.HTML_PAGE)
        self.assertNotIn(
            SourceFormatHint.RSS_CANDIDATE.value,
            {role.value for role in SourceRole},
        )

        with self.assertRaises(ValueError):
            SourceEvaluationPlan.from_candidate_source(
                candidate=candidate,
                phase4_candidate_status=CandidateSourceStatus.REJECTED,
                allowed_information_need_ids=("need_a",),
                source_evaluation_plan_id="plan",
                input_fingerprint="fp",
                phase4_input_fingerprint="phase4_fp",
                phase4_output_hash="phase4_hash",
                source_role_ontology_version="source_role_ontology_v1",
            )

    def test_cross_model_validation_rejects_unknown_references(self):
        candidate = make_candidate()
        plan = make_plan(candidate=candidate)
        self.assertEqual(
            validate_source_evaluation_plans(
                plans=(plan,),
                candidate_sources=(candidate,),
                allowed_information_need_ids=("need_a",),
                source_discovery_evidence_ids=("evidence_a",),
            ),
            (),
        )
        bad_plan = SourceEvaluationPlan.from_dict(
            {
                **plan.to_dict(),
                "allowed_information_need_ids": ["need_unknown"],
                "source_evaluation_plan_id": "bad_plan",
            }
        )
        errors = validate_source_evaluation_plans(
            plans=(bad_plan,),
            candidate_sources=(candidate,),
            allowed_information_need_ids=("need_a",),
            source_discovery_evidence_ids=("evidence_a",),
        )
        self.assertTrue(any("unknown InformationNeed" in item for item in errors))

    def test_phase4_canonical_cache_has_485_eligible_inputs_and_excludes_rejected(self):
        payload = json.loads(
            Path("outputs/planning/source_monitoring/candidate_sources.json").read_text(
                encoding="utf-8"
            )
        )
        result = SourceDiscoveryResult.from_dict(payload)
        eligible = eligible_phase5_candidate_sources(
            accepted_candidates=result.candidate_sources,
            needs_review_candidates=result.needs_review_candidates,
        )
        self.assertEqual(len(result.candidate_sources), 220)
        self.assertEqual(len(result.needs_review_candidates), 265)
        self.assertEqual(len(eligible), 485)
        self.assertEqual(len(result.rejected_candidates), 104)

        eligible_ids = {candidate.candidate_source_id for candidate, _ in eligible}
        rejected_ids = {
            item.provisional_candidate_id
            for item in result.rejected_candidates
            if item.provisional_candidate_id
        }
        self.assertTrue(eligible_ids.isdisjoint(rejected_ids))


def make_candidate() -> CandidateSource:
    return CandidateSource(
        candidate_source_id="candidate_source_a",
        entity_id="entity_a",
        canonical_url="https://example.com/news",
        normalized_url="https://example.com/news",
        root_domain="example.com",
        source_role=SourceRole.NEWSROOM,
        source_format_hint=SourceFormatHint.HTML_PAGE,
        language="en",
        candidate_officiality_status=CandidateOfficialityStatus.OFFICIAL_DOMAIN_MATCH,
        discovery_methods=("domain_first",),
        supporting_evidence_ids=("evidence_a",),
        confidence=0.9,
        rationale="Phase 4 candidate.",
        review_flags=(),
        provenance={"phase": "4"},
    )


def make_plan(candidate: CandidateSource | None = None) -> SourceEvaluationPlan:
    candidate = candidate or make_candidate()
    plan_input = "plan_input"
    plan_id = build_source_evaluation_plan_id(
        candidate_source_id=candidate.candidate_source_id,
        entity_id=candidate.entity_id,
        candidate_url=candidate.normalized_url,
        planned_source_role=candidate.source_role,
        phase4_candidate_status=CandidateSourceStatus.ACCEPTED.value,
        input_fingerprint=plan_input,
    )
    return SourceEvaluationPlan.from_candidate_source(
        candidate=candidate,
        phase4_candidate_status=CandidateSourceStatus.ACCEPTED,
        allowed_information_need_ids=("need_a",),
        source_evaluation_plan_id=plan_id,
        input_fingerprint=plan_input,
        phase4_input_fingerprint="phase4_fp",
        phase4_output_hash="phase4_hash",
        source_role_ontology_version="source_role_ontology_v1",
        candidate_priority_rank=1,
        evaluation_scope=EvaluationScope.SOURCE_SURFACE,
    )


def make_artifact(content_type: str = "text/html") -> RawPageArtifactRef:
    return RawPageArtifactRef(
        artifact_path="outputs/diagnostics/source_monitoring/raw/example.html",
        sha256="a" * 64,
        byte_size=128,
        content_type=content_type,
        encoding="utf-8",
        retrieved_at="2026-08-08T00:00:00+00:00",
    )


def make_window(text: str = "Example newsroom updates.") -> SemanticTextWindow:
    return SemanticTextWindow(
        window_id=build_semantic_text_window_id(
            source_inspection_id="inspection",
            window_type=SemanticTextWindowType.MAIN_CONTENT_EXCERPT.value,
            source_location="main",
            text=text,
        ),
        window_type=SemanticTextWindowType.MAIN_CONTENT_EXCERPT,
        source_location="main",
        text=text,
        character_count=len(text),
        structural_context="main",
        evidence_provenance={"inspection_id": "inspection"},
    )


def make_fetch_execution(artifact: RawPageArtifactRef | None = None) -> SourceFetchExecution:
    artifact = artifact or make_artifact()
    request_fp = build_source_fetch_request_fingerprint(
        requested_url="https://example.com/news",
        method=FetchMethod.GET,
        timeout_seconds=10,
        max_response_bytes=1024,
        max_redirects=1,
        accepted_content_types=("text/html",),
        user_agent_policy_version="ua_v1",
        fetch_policy_version="fetch_v1",
    )
    fetch_id = build_source_fetch_execution_id(
        source_evaluation_plan_id=make_plan().source_evaluation_plan_id,
        candidate_source_id="candidate_source_a",
        request_fingerprint=request_fp,
        final_url="https://example.com/news",
        fetch_status=FetchStatus.COMPLETED_HTML.value,
        raw_body_sha256=artifact.sha256,
    )
    return SourceFetchExecution(
        source_fetch_execution_id=fetch_id,
        source_evaluation_plan_id=make_plan().source_evaluation_plan_id,
        candidate_source_id="candidate_source_a",
        request_fingerprint=request_fp,
        requested_url="https://example.com/news",
        final_url="https://example.com/news",
        fetch_status=FetchStatus.COMPLETED_HTML,
        http_status=200,
        redirect_chain=(
            RedirectHop(
                source_url="http://example.com/news",
                destination_url="https://example.com/news",
                status_code=301,
                hop_order=0,
            ),
        ),
        content_type="text/html",
        content_length_reported=128,
        declared_encoding="utf-8",
        detected_encoding="utf-8",
        content_language="en",
        response_size_bytes=128,
        etag='"abc"',
        last_modified="Sat, 08 Aug 2026 00:00:00 GMT",
        retrieved_at="2026-08-08T00:00:00+00:00",
        elapsed_ms=25,
        raw_body_sha256=artifact.sha256,
        raw_artifact_ref=artifact,
        error_type=None,
        error_message=None,
        fetch_policy_version="fetch_v1",
    )


def make_inspection(
    artifact: RawPageArtifactRef | None = None,
    windows: tuple[SemanticTextWindow, ...] | None = None,
) -> SourceInspection:
    artifact = artifact or make_artifact()
    windows = windows or (make_window(),)
    fetch = make_fetch_execution(artifact)
    inspection_input = "inspection_fp"
    return SourceInspection(
        inspection_id=build_source_inspection_id(
            fetch_execution_id=fetch.source_fetch_execution_id,
            candidate_source_id="candidate_source_a",
            raw_body_sha256=artifact.sha256,
            inspection_input_fingerprint=inspection_input,
        ),
        fetch_execution_id=fetch.source_fetch_execution_id,
        candidate_source_id="candidate_source_a",
        requested_url="https://example.com/news",
        final_url="https://example.com/news",
        canonical_url="https://example.com/news",
        root_domain="example.com",
        canonical_root_domain="example.com",
        page_title="Example Newsroom",
        meta_description="Latest updates.",
        html_language="en",
        content_language="en",
        open_graph_title="Example Newsroom",
        open_graph_description="Latest updates.",
        structured_data_types=("Organization",),
        structured_data_organization_names=("Example",),
        heading_summary=("Newsroom", "Latest"),
        navigation_labels=("News", "Careers"),
        internal_link_count=10,
        external_link_count=2,
        same_domain_link_count=10,
        has_pagination_hints=True,
        has_article_link_hints=True,
        has_job_link_hints=False,
        has_report_link_hints=False,
        has_event_link_hints=False,
        has_section_hub_hints=True,
        has_detail_page_hints=False,
        feed_link_hints=(
            FeedLinkHint(
                href="https://example.com/feed.xml",
                rel="alternate",
                mime_type="application/rss+xml",
                title="RSS",
            ),
        ),
        source_format_hints=(SourceFormatHint.HTML_PAGE, SourceFormatHint.RSS_CANDIDATE),
        visible_text_length=500,
        semantic_text_windows=windows,
        semantic_content_truncated=False,
        client_rendering_required_hint=False,
        inspector_version="source_inspector_contract_v1",
        raw_body_sha256=artifact.sha256,
        raw_artifact_ref=artifact,
        inspection_input_fingerprint=inspection_input,
        inspection_output_hash="b" * 64,
    )


def make_bundle(
    windows: tuple[SemanticTextWindow, ...] | None = None,
) -> SourceSemanticEvidenceBundle:
    windows = windows or (make_window(),)
    fingerprint = build_source_semantic_bundle_fingerprint(
        source_inspection_id="inspection",
        candidate_source_id="candidate_source_a",
        entity_id="entity_a",
        windows=windows,
        allowed_information_need_ids=("need_a",),
    )
    return SourceSemanticEvidenceBundle(
        semantic_evidence_bundle_id=build_source_semantic_evidence_bundle_id(
            source_inspection_id="inspection",
            candidate_source_id="candidate_source_a",
            entity_id="entity_a",
            bundle_fingerprint=fingerprint,
        ),
        entity_id="entity_a",
        canonical_name="Example",
        aliases=("Example Inc.",),
        known_domain_evidence=("example.com",),
        primary_entity_kind=PrimaryEntityKind.OPERATING_COMPANY,
        candidate_source_id="candidate_source_a",
        candidate_url="https://example.com/news",
        planned_source_role=SourceRole.NEWSROOM,
        phase4_officiality_status=CandidateOfficialityStatus.OFFICIAL_DOMAIN_MATCH,
        supporting_source_discovery_evidence_ids=("evidence_a",),
        source_inspection_id="inspection",
        requested_url="https://example.com/news",
        final_url="https://example.com/news",
        root_domain="example.com",
        canonical_url="https://example.com/news",
        page_title="Example Newsroom",
        meta_description="Latest updates.",
        structural_hints=("section_hub_hint", "article_link_hint"),
        feed_link_hints=(
            FeedLinkHint(
                href="https://example.com/feed.xml",
                rel="alternate",
                mime_type="application/rss+xml",
                title="RSS",
            ),
        ),
        semantic_text_windows=windows,
        allowed_source_roles=(SourceRole.NEWSROOM, SourceRole.BLOG),
        allowed_information_need_ids=("need_a",),
        untrusted_content_marker=UNTRUSTED_WEBPAGE_EVIDENCE_MARKER,
        bundle_size_bytes=512,
        semantic_content_truncated=False,
        bundle_fingerprint=fingerprint,
    )


def make_entity_assessment() -> EntityMatchAssessment:
    return EntityMatchAssessment(
        status=EntityMatchStatus.CONFIRMED,
        confidence=EvaluationConfidence.HIGH,
        rationale="Domain and title match.",
        evidence_refs=("window",),
        assessment_method=AssessmentMethod.HYBRID,
    )


def make_initial_evaluation() -> InitialSourceEvaluation:
    return InitialSourceEvaluation(
        initial_source_evaluation_id=build_initial_source_evaluation_id(
            source_evaluation_plan_id=make_plan().source_evaluation_plan_id,
            source_inspection_id=make_inspection().inspection_id,
            semantic_evidence_bundle_id=make_bundle().semantic_evidence_bundle_id,
            evaluator_policy_version="eval_v1",
        ),
        source_evaluation_plan_id=make_plan().source_evaluation_plan_id,
        source_inspection_id=make_inspection().inspection_id,
        semantic_evidence_bundle_id=make_bundle().semantic_evidence_bundle_id,
        candidate_source_id="candidate_source_a",
        entity_id="entity_a",
        entity_match_assessment=make_entity_assessment(),
        officiality_assessment=OfficialityAssessment(
            status=OfficialityStatus.OFFICIAL,
            confidence=EvaluationConfidence.HIGH,
            rationale="Official domain.",
            evidence_refs=("inspection",),
            assessment_method=AssessmentMethod.HYBRID,
        ),
        page_type_assessment=PageTypeAssessment(
            page_type=PageType.SECTION_HUB,
            confidence=EvaluationConfidence.MEDIUM,
            rationale="Structural hints suggest hub.",
            evidence_refs=("inspection",),
            assessment_method=AssessmentMethod.HYBRID,
        ),
        surface_durability_assessment=SurfaceDurabilityAssessment(
            status=SurfaceDurabilityStatus.DURABLE_SURFACE,
            confidence=EvaluationConfidence.MEDIUM,
            rationale="Durable newsroom surface.",
            evidence_refs=("inspection",),
            assessment_method=AssessmentMethod.HYBRID,
        ),
        source_role_assessment=SourceRoleAssessment(
            planned_source_role=SourceRole.NEWSROOM,
            observed_source_role=SourceRole.NEWSROOM,
            source_role_match_status=SourceRoleMatchStatus.MATCH,
            confidence=EvaluationConfidence.HIGH,
            rationale="Newsroom language appears.",
            evidence_refs=("window",),
            assessment_method=AssessmentMethod.HYBRID,
        ),
        information_need_relevance_assessment=InformationNeedRelevanceAssessment(
            allowed_information_need_ids=("need_a",),
            supported_information_need_ids=("need_a",),
            relevance_level=RelevanceLevel.HIGH,
            confidence=EvaluationConfidence.MEDIUM,
            rationale="Relevant to the allowed need.",
            evidence_refs=("window",),
            assessment_method=AssessmentMethod.LLM,
        ),
        initial_monitoring_suitability=RelevanceLevel.HIGH,
        source_value=SourceValueLevel.HIGH,
        evaluation_confidence=EvaluationConfidence.MEDIUM,
        rationale="Proceed to bounded observation.",
        review_flags=(),
        decision=InitialEvaluationDecision.PROCEED_TO_OBSERVATION,
        evaluator_policy_version="eval_v1",
    )


def make_observation_plan() -> SourceObservationPlan:
    return SourceObservationPlan(
        source_observation_plan_id=build_source_observation_plan_id(
            candidate_source_id="candidate_source_a",
            initial_source_evaluation_id=make_initial_evaluation().initial_source_evaluation_id,
            sampling_strategy=ObservationSamplingStrategy.BOUNDED_SOURCE_SAMPLE.value,
            max_item_count=5,
            input_fingerprint="obs_fp",
        ),
        candidate_source_id="candidate_source_a",
        initial_source_evaluation_id=make_initial_evaluation().initial_source_evaluation_id,
        sampling_strategy=ObservationSamplingStrategy.BOUNDED_SOURCE_SAMPLE,
        max_item_count=5,
        lookback_window_days=90,
        observation_policy_version="obs_v1",
        input_fingerprint="obs_fp",
    )


def make_observed_evidence() -> ObservedSourceEvidence:
    plan = make_observation_plan()
    return ObservedSourceEvidence(
        observed_evidence_id=build_observed_source_evidence_id(
            candidate_source_id="candidate_source_a",
            item_url="https://example.com/news/item",
            item_title="AI update",
            observation_plan_id=plan.source_observation_plan_id,
        ),
        observation_plan_id=plan.source_observation_plan_id,
        candidate_source_id="candidate_source_a",
        item_url="https://example.com/news/item",
        item_title="AI update",
        publication_date_hint="2026-08-01",
        content_type_hint="article",
        relevant_information_need_ids=("need_a",),
        signal_relevance=RelevanceLevel.HIGH,
        observation_provenance={"bounded_sample": True},
    )


def make_observation_result() -> SourceObservationResult:
    plan = make_observation_plan()
    evidence = make_observed_evidence()
    return SourceObservationResult(
        source_observation_result_id="observation_result_a",
        source_observation_plan_id=plan.source_observation_plan_id,
        observation_status=ObservationStatus.COMPLETED,
        sampled_item_count=1,
        recent_item_count=1,
        relevant_item_count=1,
        information_need_hit_count={"need_a": 1},
        observed_date_span_start="2026-08-01",
        observed_date_span_end="2026-08-01",
        observed_evidence_ids=(evidence.observed_evidence_id,),
        failures=(),
        diagnostics=("bounded sample only",),
        observation_policy_version="obs_v1",
    )


def make_signal_potential() -> ObservedSignalPotential:
    result = make_observation_result()
    evidence = make_observed_evidence()
    return ObservedSignalPotential(
        observed_signal_potential_id="signal_potential_a",
        source_observation_result_id=result.source_observation_result_id,
        level=ObservedSignalPotentialLevel.HIGH,
        sampled_item_count=1,
        relevant_item_count=1,
        information_need_hit_count={"need_a": 1},
        supporting_observed_evidence_ids=(evidence.observed_evidence_id,),
        rationale="Bounded sample contained a relevant item.",
        limitations=("bounded observation is not long-term monitoring history",),
        supporting_metrics={"sampled_item_count": 1},
    )


def make_final_evaluation() -> FinalSourceEvaluation:
    initial = make_initial_evaluation()
    observation = make_observation_result()
    return FinalSourceEvaluation(
        final_source_evaluation_id=build_final_source_evaluation_id(
            initial_source_evaluation_id=initial.initial_source_evaluation_id,
            candidate_source_id="candidate_source_a",
            observation_result_id=observation.source_observation_result_id,
            input_fingerprint="final_fp",
        ),
        initial_source_evaluation_id=initial.initial_source_evaluation_id,
        observation_result_id=observation.source_observation_result_id,
        candidate_source_id="candidate_source_a",
        entity_id="entity_a",
        source_value=SourceValueLevel.HIGH,
        evaluation_confidence=EvaluationConfidence.MEDIUM,
        observed_signal_potential=make_signal_potential(),
        final_rationale="Approved for acquisition-method planning.",
        review_flags=(),
        final_decision=FinalEvaluationDecision.APPROVED_FOR_ACQUISITION,
        policy_version="final_eval_v1",
        input_fingerprint="final_fp",
    )


def make_contract_graph() -> dict[str, object]:
    candidate = make_candidate()
    plan = make_plan(candidate)
    artifact = make_artifact()
    fetch = make_fetch_execution(artifact)
    inspection = make_inspection(artifact)
    bundle = make_bundle()
    initial = make_initial_evaluation()
    observation_plan = make_observation_plan()
    observed_evidence = make_observed_evidence()
    observation_result = make_observation_result()
    signal = make_signal_potential()
    final = make_final_evaluation()

    result_payload = {
        "evaluation_plans": (plan,),
        "fetch_executions": (fetch,),
        "inspections": (inspection,),
        "initial_evaluations": (initial,),
        "observation_plans": (observation_plan,),
        "observation_results": (observation_result,),
        "final_evaluations": (final,),
        "schema_version": SOURCE_EVALUATION_RESULT_SCHEMA_VERSION,
    }
    result_hash = build_source_evaluation_result_hash(**result_payload)
    result = SourceEvaluationResult(
        upstream_phase4_input_fingerprint="phase4_fp",
        upstream_phase4_output_hash="phase4_hash",
        evaluation_plans=(plan,),
        fetch_executions=(fetch,),
        inspections=(inspection,),
        initial_evaluations=(initial,),
        observation_plans=(observation_plan,),
        observation_results=(observation_result,),
        final_evaluations=(final,),
        rejected_evaluation_records=(),
        diagnostics=(),
        input_fingerprint="result_fp",
        output_hash=result_hash,
        generation_mode="contract_only",
    )
    return {
        "round_trip_models": (
            plan,
            artifact,
            fetch,
            inspection,
            bundle,
            initial,
            observation_plan,
            observed_evidence,
            observation_result,
            signal,
            final,
        ),
        "result": result,
    }


if __name__ == "__main__":
    unittest.main()
