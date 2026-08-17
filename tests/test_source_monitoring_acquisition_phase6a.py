import unittest

from src.source_monitoring.acquisition_models import (
    AcquisitionMethod,
    AcquisitionResolution,
    AcquisitionResolutionStatus,
    FeedFormat,
    FeedParseStatus,
    FeedVerificationResult,
    FeedVerificationStatus,
    Phase7MonitoringHandoff,
    SelectedWebsiteResolutionResult,
    SelectedWebsiteResolutionStatus,
)
from src.source_monitoring.acquisition_planner import (
    ACQUISITION_PLANNING_POLICY_VERSION,
    AcquisitionPlanningError,
    plan_acquisition_resolution,
)
from src.source_monitoring.source_discovery_models import (
    CandidateOfficialityStatus,
    CandidateSource,
    SourceFormatHint,
    SourceRole,
)
from src.source_monitoring.source_evaluation_models import (
    EvaluationConfidence,
    FeedLinkHint,
    FinalEvaluationDecision,
    FinalSourceEvaluation,
    ObservedSignalPotential,
    ObservedSignalPotentialLevel,
    SourceInspection,
    SourceValueLevel,
)


class Phase6AAcquisitionPlanningTests(unittest.TestCase):
    def test_01_approved_phase5_source_is_accepted(self):
        result = self.plan()
        self.assertEqual(len(result.acquisition_resolution_plans), 1)

    def test_02_needs_review_phase5_source_rejected_from_planning(self):
        with self.assertRaises(AcquisitionPlanningError):
            self.plan(final_decision=FinalEvaluationDecision.NEEDS_REVIEW)

    def test_03_rejected_phase5_source_rejected_from_planning(self):
        with self.assertRaises(AcquisitionPlanningError):
            self.plan(final_decision=FinalEvaluationDecision.REJECTED)

    def test_04_stable_acquisition_resolution_plan_id(self):
        first = self.plan()
        second = self.plan()
        self.assertEqual(first.acquisition_resolution_plans[0].acquisition_resolution_plan_id, second.acquisition_resolution_plans[0].acquisition_resolution_plan_id)

    def test_05_changed_final_source_evaluation_hash_invalidates_plan_fingerprint(self):
        first = self.plan(final_rationale="approved: first")
        second = self.plan(final_rationale="approved: changed")
        self.assertNotEqual(first.acquisition_resolution_plans[0].input_fingerprint, second.acquisition_resolution_plans[0].input_fingerprint)

    def test_06_changed_source_url_invalidates_plan_fingerprint(self):
        first = self.plan(source_url="https://example.com/news")
        second = self.plan(source_url="https://example.com/research")
        self.assertNotEqual(first.acquisition_resolution_plans[0].input_fingerprint, second.acquisition_resolution_plans[0].input_fingerprint)

    def test_07_timestamp_like_handoff_metadata_does_not_affect_plan_id(self):
        first = self.plan(handoff_extra={"generated_at": "2026-01-01T00:00:00Z"})
        second = self.plan(handoff_extra={"generated_at": "2026-12-31T00:00:00Z"})
        self.assertEqual(first.acquisition_resolution_plans[0].acquisition_resolution_plan_id, second.acquisition_resolution_plans[0].acquisition_resolution_plan_id)

    def test_08_zero_feed_hints_supported(self):
        result = self.plan(feed_hints=())
        self.assertEqual(len(result.feed_verification_plans), 0)
        self.assertEqual(len(result.acquisition_resolution_plans), 1)

    def test_09_one_feed_hint_creates_one_feed_verification_plan(self):
        result = self.plan(feed_hints=(FeedLinkHint(href="https://example.com/feed", rel="alternate", mime_type="application/rss+xml"),))
        self.assertEqual(len(result.feed_verification_plans), 1)

    def test_10_duplicate_normalized_feed_hints_deduplicate(self):
        result = self.plan(feed_hints=(
            FeedLinkHint(href="https://www.example.com/feed?utm_source=x", rel="alternate", mime_type="application/rss+xml"),
            FeedLinkHint(href="https://example.com/feed", rel="alternate", mime_type="application/rss+xml"),
        ))
        self.assertEqual(len(result.feed_verification_plans), 1)

    def test_11_multiple_evidence_refs_preserved_after_dedup(self):
        result = self.plan(feed_hints=(
            FeedLinkHint(href="https://www.example.com/feed?utm_source=x", rel="alternate", mime_type="application/rss+xml"),
            FeedLinkHint(href="https://example.com/feed", rel="alternate", mime_type="application/rss+xml"),
        ))
        self.assertEqual(len(result.feed_verification_plans[0].feed_hint_evidence_refs), 2)

    def test_12_feed_candidate_ordering_deterministic(self):
        result = self.plan(feed_hints=(
            FeedLinkHint(href="https://example.com/z-feed", rel="alternate", mime_type="application/rss+xml"),
            FeedLinkHint(href="https://example.com/a-feed", rel="alternate", mime_type="application/rss+xml"),
        ))
        self.assertEqual([p.feed_candidate_url for p in result.feed_verification_plans], ["https://example.com/a-feed", "https://example.com/z-feed"])

    def test_13_feed_plan_budget_bounded(self):
        result = self.plan(feed_hints=self.many_feed_hints(), max_feed_candidates_per_source=2)
        self.assertEqual(len(result.feed_verification_plans), 2)

    def test_14_overflow_feed_candidates_preserved_as_deferred(self):
        result = self.plan(feed_hints=self.many_feed_hints(), max_feed_candidates_per_source=2)
        self.assertEqual(len(result.deferred_feed_candidates), 2)

    def test_15_feed_hint_remains_unverified(self):
        result = self.plan()
        self.assertEqual(result.feed_verification_plans[0].feed_hint_evidence_refs[0].verification_status, "unverified")

    def test_16_relative_feed_hint_is_resolved_without_fetching(self):
        result = self.plan(feed_hints=(FeedLinkHint(href="/feed", rel="alternate", mime_type="application/rss+xml"),))
        self.assertEqual(result.feed_verification_plans[0].feed_candidate_url, "https://example.com/feed")

    def test_17_invalid_feed_candidate_url_does_not_produce_plan(self):
        result = self.plan(feed_hints=(FeedLinkHint(href="mailto:news@example.com", rel="alternate", mime_type="application/rss+xml"),))
        self.assertEqual(len(result.feed_verification_plans), 0)

    def test_18_invalid_feed_candidate_preserves_diagnostic(self):
        result = self.plan(feed_hints=(FeedLinkHint(href="mailto:news@example.com", rel="alternate", mime_type="application/rss+xml"),))
        self.assertTrue(any(item.startswith("invalid_feed_candidate_url") for item in result.diagnostics))

    def test_19_selected_website_fallback_plan_exists(self):
        result = self.plan()
        self.assertEqual(len(result.selected_website_resolution_plans), 1)

    def test_20_feed_candidate_does_not_remove_website_fallback(self):
        result = self.plan()
        self.assertEqual(len(result.feed_verification_plans), 1)
        self.assertEqual(len(result.selected_website_resolution_plans), 1)

    def test_21_website_fallback_dependency_encoded_after_feed_verification(self):
        result = self.plan()
        dependency = result.selected_website_resolution_plans[0].execution_dependency
        self.assertEqual(dependency["condition"], "execute_if_no_verified_usable_feed")

    def test_22_no_final_acquisition_method_assigned(self):
        result = self.plan()
        self.assertNotIn("acquisition_method", result.acquisition_resolution_plans[0].to_dict())

    def test_23_no_final_acquisition_resolution_fabricated(self):
        result = self.plan()
        self.assertFalse(hasattr(result, "acquisition_resolutions"))

    def test_24_acquisition_method_supports_rss(self):
        self.assertEqual(AcquisitionMethod("rss"), AcquisitionMethod.RSS)

    def test_25_acquisition_method_supports_atom(self):
        self.assertEqual(AcquisitionMethod("atom"), AcquisitionMethod.ATOM)

    def test_26_acquisition_method_supports_selected_website(self):
        self.assertEqual(AcquisitionMethod("selected_website"), AcquisitionMethod.SELECTED_WEBSITE)

    def test_27_unsupported_is_not_acquisition_method(self):
        with self.assertRaises(ValueError):
            AcquisitionMethod("unsupported")

    def test_28_needs_review_is_not_acquisition_method(self):
        with self.assertRaises(ValueError):
            AcquisitionMethod("needs_review")

    def test_29_resolution_status_supports_resolved(self):
        self.assertEqual(AcquisitionResolutionStatus("resolved"), AcquisitionResolutionStatus.RESOLVED)

    def test_30_resolution_status_supports_needs_review(self):
        self.assertEqual(AcquisitionResolutionStatus("needs_review"), AcquisitionResolutionStatus.NEEDS_REVIEW)

    def test_31_resolution_status_supports_unsupported(self):
        self.assertEqual(AcquisitionResolutionStatus("unsupported"), AcquisitionResolutionStatus.UNSUPPORTED)

    def test_32_resolved_final_contract_requires_method(self):
        with self.assertRaises(ValueError):
            self.resolution(status=AcquisitionResolutionStatus.RESOLVED, method=None)

    def test_33_unsupported_final_contract_requires_null_method(self):
        with self.assertRaises(ValueError):
            self.resolution(status=AcquisitionResolutionStatus.UNSUPPORTED, method=AcquisitionMethod.RSS, verified_feed_format=FeedFormat.RSS)

    def test_34_review_final_contract_normally_requires_null_method(self):
        with self.assertRaises(ValueError):
            self.resolution(status=AcquisitionResolutionStatus.NEEDS_REVIEW, method=AcquisitionMethod.SELECTED_WEBSITE, config_ref="config_1")

    def test_35_rss_resolution_requires_compatible_verified_feed_evidence(self):
        with self.assertRaises(ValueError):
            self.resolution(method=AcquisitionMethod.RSS, verified_feed_format=FeedFormat.ATOM)

    def test_36_atom_resolution_requires_compatible_verified_feed_evidence(self):
        with self.assertRaises(ValueError):
            self.resolution(method=AcquisitionMethod.ATOM, verified_feed_format=FeedFormat.RSS)

    def test_37_selected_website_resolution_requires_feasible_evidence_config(self):
        with self.assertRaises(ValueError):
            self.resolution(method=AcquisitionMethod.SELECTED_WEBSITE, selected_result_id=None, config_ref=None)

    def test_38_phase7_handoff_accepts_only_resolved_acquisition(self):
        handoff = Phase7MonitoringHandoff.from_resolution(
            phase7_monitoring_handoff_id="phase7_1",
            resolution=self.resolution(method=AcquisitionMethod.RSS, verified_feed_format=FeedFormat.RSS),
            supported_information_need_ids=("need_1",),
            source_role=SourceRole.NEWSROOM,
            provenance={},
        )
        self.assertEqual(handoff.acquisition_method, AcquisitionMethod.RSS)

    def test_39_phase7_handoff_rejects_needs_review_acquisition(self):
        with self.assertRaises(ValueError):
            Phase7MonitoringHandoff.from_resolution(
                phase7_monitoring_handoff_id="phase7_1",
                resolution=self.resolution(status=AcquisitionResolutionStatus.NEEDS_REVIEW, method=None),
                supported_information_need_ids=("need_1",),
                source_role=SourceRole.NEWSROOM,
                provenance={},
            )

    def test_40_phase7_handoff_rejects_unsupported_acquisition(self):
        with self.assertRaises(ValueError):
            Phase7MonitoringHandoff.from_resolution(
                phase7_monitoring_handoff_id="phase7_1",
                resolution=self.resolution(status=AcquisitionResolutionStatus.UNSUPPORTED, method=None),
                supported_information_need_ids=("need_1",),
                source_role=SourceRole.NEWSROOM,
                provenance={},
            )

    def test_41_phase5_approved_state_independent_from_phase6_unsupported_state(self):
        resolution = self.resolution(status=AcquisitionResolutionStatus.UNSUPPORTED, method=None)
        self.assertEqual(resolution.resolution_status, AcquisitionResolutionStatus.UNSUPPORTED)

    def test_42_feed_result_distinguishes_syntax_validity_from_usability(self):
        result = self.feed_result(status=FeedVerificationStatus.VERIFIED_BUT_LIMITED, syntax_valid=True, usable=False)
        self.assertTrue(result.syntax_valid)
        self.assertFalse(result.usable_for_monitoring)

    def test_43_unreachable_feed_does_not_reject_phase5_source(self):
        result = self.feed_result(status=FeedVerificationStatus.UNREACHABLE, syntax_valid=False, usable=False)
        self.assertEqual(result.verification_status, FeedVerificationStatus.UNREACHABLE)

    def test_44_selected_website_result_distinguishes_needs_review_from_unsupported(self):
        review = self.website_result(SelectedWebsiteResolutionStatus.NEEDS_REVIEW)
        unsupported = self.website_result(SelectedWebsiteResolutionStatus.UNSUPPORTED)
        self.assertNotEqual(review.feasibility_status, unsupported.feasibility_status)

    def test_45_no_rss_is_not_semantic_source_rejection(self):
        result = self.plan(feed_hints=())
        self.assertEqual(result.acquisition_resolution_plans[0].known_technical_limitation_flags, ("no_explicit_feed_hint_observed",))

    def test_46_technical_reason_codes_use_acquisition_vocabulary_only(self):
        resolution = self.resolution(status=AcquisitionResolutionStatus.UNSUPPORTED, method=None, reason_codes=("no_supported_v1_acquisition_method",))
        self.assertFalse(any("semantic" in code for code in resolution.resolution_reason_codes))

    def test_47_plan_dependencies_stable(self):
        first = self.plan().acquisition_resolution_plans[0].dependency_model
        second = self.plan().acquisition_resolution_plans[0].dependency_model
        self.assertEqual(first, second)

    def test_48_no_raw_html_enters_phase6a_planning_output(self):
        text = str(self.plan().to_dict()).casefold()
        self.assertNotIn("<html", text)

    def test_49_no_raw_deepseek_responses_enter_phase6a(self):
        text = str(self.plan().to_dict()).casefold()
        self.assertNotIn("deepseek_response", text)

    def test_50_supported_information_need_ids_preserved_exactly(self):
        result = self.plan(needs=("need_a", "need_b"))
        self.assertEqual(result.acquisition_resolution_plans[0].supported_information_need_ids, ("need_a", "need_b"))

    def test_51_source_role_preserved(self):
        result = self.plan(role=SourceRole.REPORTS_OR_DATA)
        self.assertEqual(result.acquisition_resolution_plans[0].observed_source_role, SourceRole.REPORTS_OR_DATA)

    def test_52_upstream_provenance_preserved(self):
        plan = self.plan().acquisition_resolution_plans[0]
        self.assertEqual(plan.source_inspection_id, "inspection_1")
        self.assertEqual(plan.final_source_evaluation_id, "final_1")

    def test_53_same_real_like_input_produces_deterministic_serialized_output(self):
        first = self.plan().to_dict()
        second = self.plan().to_dict()
        first["generation"] = {}
        second["generation"] = {}
        self.assertEqual(first, second)

    def test_54_changed_planning_policy_version_invalidates_plan_fingerprint(self):
        first = self.plan(planning_policy_version=ACQUISITION_PLANNING_POLICY_VERSION)
        second = self.plan(planning_policy_version="acquisition_planning_policy_v2")
        self.assertNotEqual(first.acquisition_resolution_plans[0].input_fingerprint, second.acquisition_resolution_plans[0].input_fingerprint)

    def test_55_no_external_client_is_invoked(self):
        result = self.plan()
        self.assertEqual(result.generation["http_calls"], 0)
        self.assertEqual(result.generation["brave_calls"], 0)
        self.assertEqual(result.generation["deepseek_calls"], 0)
        self.assertEqual(result.generation["browser_calls"], 0)

    def plan(
        self,
        *,
        final_decision=FinalEvaluationDecision.APPROVED_FOR_ACQUISITION,
        final_rationale="approved",
        source_url="https://example.com/news",
        feed_hints=None,
        role=SourceRole.NEWSROOM,
        needs=("need_1",),
        handoff_extra=None,
        max_feed_candidates_per_source=3,
        planning_policy_version=ACQUISITION_PLANNING_POLICY_VERSION,
    ):
        candidate = self.candidate(source_url=source_url, role=role)
        final = self.final(decision=final_decision, rationale=final_rationale, role=role)
        inspection = self.inspection(source_url=source_url, feed_hints=feed_hints if feed_hints is not None else self.default_feed_hints())
        handoff = {
            "candidate_source_id": "candidate_1",
            "entity_id": "entity_1",
            "final_source_evaluation_id": "final_1",
            "observed_source_role": role.value,
            "supported_information_need_ids": list(needs),
            "source_value": "medium",
            "evaluation_confidence": "medium",
            "reason_codes": ["foundational_evidence_sufficient"],
        }
        if handoff_extra:
            handoff.update(handoff_extra)
        return plan_acquisition_resolution(
            phase5_canonical={"final_evaluations": [final.to_dict()]},
            phase6_handoff={"approved_sources": [handoff]},
            candidates=(candidate,),
            inspections=(inspection,),
            source_observation_results=(),
            max_feed_candidates_per_source=max_feed_candidates_per_source,
            planning_policy_version=planning_policy_version,
        )

    def candidate(self, *, source_url, role):
        return CandidateSource(
            candidate_source_id="candidate_1",
            entity_id="entity_1",
            canonical_url=source_url,
            normalized_url=source_url,
            root_domain="example.com",
            source_role=role,
            source_format_hint=SourceFormatHint.HTML_PAGE,
            language="en",
            candidate_officiality_status=CandidateOfficialityStatus.OFFICIAL_DOMAIN_MATCH,
            discovery_methods=("test",),
            supporting_evidence_ids=("evidence_1",),
            confidence=0.9,
            rationale="test",
            review_flags=(),
            provenance={},
        )

    def final(self, *, decision, rationale, role):
        return FinalSourceEvaluation(
            final_source_evaluation_id="final_1",
            initial_source_evaluation_id="initial_1",
            observation_result_id=None,
            candidate_source_id="candidate_1",
            entity_id="entity_1",
            source_value=SourceValueLevel.MEDIUM,
            evaluation_confidence=EvaluationConfidence.MEDIUM,
            observed_signal_potential=ObservedSignalPotential(
                observed_signal_potential_id="potential_1",
                source_observation_result_id="observation_1",
                level=ObservedSignalPotentialLevel.MEDIUM,
                sampled_item_count=5,
                relevant_item_count=2,
                information_need_hit_count={"need_1": 2},
                supporting_observed_evidence_ids=("obs_evidence_1",),
                rationale="test",
                limitations=(),
            ),
            final_rationale=rationale,
            review_flags=(),
            final_decision=decision,
            policy_version="final_source_evaluation_policy_v1",
            input_fingerprint=f"final_fp_{rationale}_{role.value}_{decision.value}",
        )

    def inspection(self, *, source_url, feed_hints):
        return SourceInspection(
            inspection_id="inspection_1",
            fetch_execution_id="fetch_1",
            candidate_source_id="candidate_1",
            requested_url=source_url,
            final_url=source_url,
            canonical_url=source_url,
            root_domain="example.com",
            canonical_root_domain="example.com",
            page_title="News",
            meta_description="Latest news",
            html_language="en",
            content_language=None,
            open_graph_title=None,
            open_graph_description=None,
            structured_data_types=(),
            structured_data_organization_names=(),
            heading_summary=("News",),
            navigation_labels=("News",),
            internal_link_count=5,
            external_link_count=0,
            same_domain_link_count=5,
            has_pagination_hints=False,
            has_article_link_hints=True,
            has_job_link_hints=False,
            has_report_link_hints=False,
            has_event_link_hints=False,
            has_section_hub_hints=True,
            has_detail_page_hints=False,
            feed_link_hints=tuple(feed_hints),
            source_format_hints=(SourceFormatHint.HTML_PAGE,),
            visible_text_length=1000,
            semantic_text_windows=(),
            semantic_content_truncated=False,
            client_rendering_required_hint=False,
            inspector_version="test",
            raw_body_sha256="a" * 64,
            raw_artifact_ref=None,
            inspection_input_fingerprint="inspection_input",
            inspection_output_hash="inspection_hash_1",
        )

    def default_feed_hints(self):
        return (
            FeedLinkHint(
                href="https://example.com/feed",
                rel="alternate",
                mime_type="application/rss+xml",
                title="Feed",
            ),
        )

    def many_feed_hints(self):
        return tuple(
            FeedLinkHint(
                href=f"https://example.com/feed-{index}",
                rel="alternate",
                mime_type="application/rss+xml",
            )
            for index in range(4)
        )

    def resolution(
        self,
        *,
        status=AcquisitionResolutionStatus.RESOLVED,
        method=AcquisitionMethod.RSS,
        verified_feed_format=FeedFormat.RSS,
        selected_result_id="website_result_1",
        config_ref="config_1",
        reason_codes=("resolved_feed",),
    ):
        feed_ids = ("feed_result_1",) if method in {AcquisitionMethod.RSS, AcquisitionMethod.ATOM} else ()
        return AcquisitionResolution(
            acquisition_resolution_id="resolution_1",
            acquisition_resolution_plan_id="plan_1",
            candidate_source_id="candidate_1",
            entity_id="entity_1",
            final_source_evaluation_id="final_1",
            source_url="https://example.com/news",
            resolution_status=status,
            acquisition_method=method,
            feed_verification_result_ids=feed_ids,
            selected_website_resolution_result_id=selected_result_id if method == AcquisitionMethod.SELECTED_WEBSITE else None,
            selected_acquisition_config_ref=config_ref if method == AcquisitionMethod.SELECTED_WEBSITE else None,
            verified_feed_format=verified_feed_format if method in {AcquisitionMethod.RSS, AcquisitionMethod.ATOM} else None,
            technical_limitation_flags=(),
            resolution_reason_codes=reason_codes,
            evidence_quality="medium",
            resolution_policy_version="resolution_policy_v1",
            input_fingerprint="resolution_input",
        )

    def feed_result(self, *, status, syntax_valid, usable):
        return FeedVerificationResult(
            feed_verification_result_id="feed_result_1",
            feed_verification_plan_id="feed_plan_1",
            candidate_source_id="candidate_1",
            feed_candidate_url="https://example.com/feed",
            final_url=None,
            fetch_execution_id=None,
            fetch_status=None,
            http_status=None,
            content_type=None,
            redirect_chain=(),
            parse_status=FeedParseStatus.PARSED_VALID if syntax_valid else FeedParseStatus.NOT_PARSED,
            verified_feed_format=FeedFormat.RSS if syntax_valid else FeedFormat.UNKNOWN,
            feed_title="Feed" if syntax_valid else None,
            feed_home_link="https://example.com" if syntax_valid else None,
            sampled_entry_count=1 if usable else 0,
            valid_entry_url_count=1 if usable else 0,
            title_support=usable,
            publication_date_support=False,
            stable_item_identity_support=usable,
            syntax_valid=syntax_valid,
            usable_for_monitoring=usable,
            verification_status=status,
            failure_reason=None if syntax_valid else "network_unreachable",
            diagnostics=(),
            verification_policy_version="feed_verification_policy_v1",
            input_fingerprint="feed_input",
        )

    def website_result(self, status):
        return SelectedWebsiteResolutionResult(
            selected_website_resolution_result_id="website_result_1",
            selected_website_resolution_plan_id="website_plan_1",
            candidate_source_id="candidate_1",
            final_source_evaluation_id="final_1",
            source_url="https://example.com/news",
            feasibility_status=status,
            candidate_item_link_discoverability="unknown",
            normalized_item_url_support=False,
            item_title_support=False,
            date_hint_support=False,
            item_type_role_support=False,
            bounded_extraction_consistency="unknown",
            technical_limitations=(),
            selected_website_acquisition_config=None,
            reason_codes=(status.value,),
            resolution_policy_version="selected_website_resolution_policy_v1",
            input_fingerprint="website_input",
        )


if __name__ == "__main__":
    unittest.main()
