from __future__ import annotations

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

import requests

from src.source_monitoring.acquisition_models import (
    AcquisitionPlanningResult,
    AcquisitionResolutionPlan,
    PlanStatus,
    SelectedWebsiteResolutionPlan,
    SelectedWebsiteResolutionStatus,
)
from src.source_monitoring.selected_website_resolver import (
    DEFAULT_MAX_ITEMS_PER_RUN,
    SELECTED_WEBSITE_FEASIBILITY_POLICY_VERSION,
    SELECTED_WEBSITE_ITEM_DISCOVERY_POLICY_VERSION,
    SelectedWebsiteDiscoveryEvidence,
    assess_selected_website_resolution,
    build_discovery_evidence,
    build_phase6c_source_fetcher,
    execute_selected_website_resolution_plans,
    persist_selected_website_resolution_results,
    select_phase6c_execution_plans,
)
from src.source_monitoring.source_discovery_models import SourceFormatHint, SourceRole
from src.source_monitoring.source_evaluation_models import (
    FetchStatus,
    SemanticTextWindow,
    SemanticTextWindowType,
    SourceInspection,
)


class SelectedWebsiteResolverPhase6CBase(unittest.TestCase):
    def inspection(
        self,
        *,
        lines: tuple[str, ...] = (),
        source_url: str = "https://example.com/news",
        final_url: str | None = None,
        candidate_source_id: str = "candidate_a",
        client_rendering: bool = False,
        pagination: bool = False,
        detail_hints: bool = True,
    ) -> SourceInspection:
        text = "\n".join(lines)
        window = SemanticTextWindow(
            window_id="window_links",
            window_type=SemanticTextWindowType.REPRESENTATIVE_LINK_CLUSTER,
            source_location="links",
            text=text,
            character_count=len(text),
            structural_context="representative_links",
            evidence_provenance={"test": True},
        )
        normalized_final_url = final_url or source_url
        return SourceInspection(
            inspection_id=f"inspection_{abs(hash((lines, source_url, client_rendering, pagination))) % 999999}",
            fetch_execution_id="fetch_1",
            candidate_source_id=candidate_source_id,
            requested_url=source_url,
            final_url=normalized_final_url,
            canonical_url=None,
            root_domain="example.com",
            canonical_root_domain=None,
            page_title="Example",
            meta_description=None,
            html_language="en",
            content_language=None,
            open_graph_title=None,
            open_graph_description=None,
            structured_data_types=(),
            structured_data_organization_names=(),
            heading_summary=("h1:Example",),
            navigation_labels=(),
            internal_link_count=len(lines),
            external_link_count=0,
            same_domain_link_count=len(lines),
            has_pagination_hints=pagination,
            has_article_link_hints=True,
            has_job_link_hints=False,
            has_report_link_hints=False,
            has_event_link_hints=False,
            has_section_hub_hints=False,
            has_detail_page_hints=detail_hints,
            feed_link_hints=(),
            source_format_hints=(SourceFormatHint.HTML_PAGE,),
            visible_text_length=500,
            semantic_text_windows=(window,),
            semantic_content_truncated=False,
            client_rendering_required_hint=client_rendering,
            inspector_version="test_inspector",
            raw_body_sha256="abc",
            raw_artifact_ref=None,
            inspection_input_fingerprint="inspection_input",
            inspection_output_hash=f"inspection_hash_{abs(hash(lines)) % 999999}",
        )

    def evidence(
        self,
        *,
        lines: tuple[str, ...] = (),
        role: SourceRole = SourceRole.NEWSROOM,
        source_url: str = "https://example.com/news",
        client_rendering: bool = False,
        pagination: bool = False,
        fetch_status: FetchStatus | None = FetchStatus.COMPLETED_HTML,
        inspectable: bool = True,
    ) -> SelectedWebsiteDiscoveryEvidence:
        inspection = None if not inspectable else self.inspection(
            lines=lines,
            source_url=source_url,
            client_rendering=client_rendering,
            pagination=pagination,
        )
        return build_discovery_evidence(
            inspection=inspection,
            source_url=source_url,
            observed_source_role=role,
            fetch_status=fetch_status,
            inspectable=inspectable,
            skipped_reason=None if inspectable else "fetch_status_not_html:unsupported_content",
        )

    def plan(self, *, role: SourceRole = SourceRole.NEWSROOM) -> SelectedWebsiteResolutionPlan:
        return SelectedWebsiteResolutionPlan(
            selected_website_resolution_plan_id="selected_plan_a",
            acquisition_resolution_plan_id="acq_plan_a",
            candidate_source_id="candidate_a",
            final_source_evaluation_id="final_a",
            source_url="https://example.com/news",
            source_inspection_id="inspection_old",
            source_inspection_hash="inspection_hash_old",
            source_observation_result_id="observation_old",
            source_observation_result_hash="observation_hash_old",
            observed_source_role=role,
            evidence_input_refs=("inspection_old", "observation_old"),
            execution_dependency={"condition": "execute_if_no_verified_usable_feed"},
            resolution_policy_version="selected_website_resolution_policy_v1",
            input_fingerprint="plan_input",
        )

    def acquisition_plan(self, *, role: SourceRole = SourceRole.NEWSROOM) -> AcquisitionResolutionPlan:
        return AcquisitionResolutionPlan(
            acquisition_resolution_plan_id="acq_plan_a",
            candidate_source_id="candidate_a",
            entity_id="entity_a",
            final_source_evaluation_id="final_a",
            source_url="https://example.com/news",
            observed_source_role=role,
            supported_information_need_ids=("need_a",),
            phase5_handoff_fingerprint="handoff_hash",
            final_source_evaluation_fingerprint="final_hash",
            source_inspection_id="inspection_old",
            source_inspection_hash="inspection_hash_old",
            source_observation_result_id="observation_old",
            source_observation_result_hash="observation_hash_old",
            known_technical_limitation_flags=(),
            strategy_order=("feed", "selected_website"),
            feed_candidate_count=0,
            executable_feed_verification_plan_count=0,
            deferred_feed_candidate_count=0,
            selected_website_fallback_planned=True,
            dependency_model={"selected_website": "execute_if_no_verified_usable_feed"},
            planning_policy_version="acquisition_planning_policy_v1",
            input_fingerprint="acq_input",
            plan_status=PlanStatus.PLANNED,
        )

    def result(
        self,
        *,
        current: SelectedWebsiteDiscoveryEvidence,
        historical: SelectedWebsiteDiscoveryEvidence | None = None,
        role: SourceRole = SourceRole.NEWSROOM,
    ):
        return assess_selected_website_resolution(
            plan=self.plan(role=role),
            acquisition_plan=self.acquisition_plan(role=role),
            current_evidence=current,
            historical_evidence=historical,
            fetch_execution_id="fetch_current",
            current_final_url="https://example.com/news",
            current_raw_body_sha256="body_hash",
            max_items_per_run=DEFAULT_MAX_ITEMS_PER_RUN,
            item_discovery_policy_version=SELECTED_WEBSITE_ITEM_DISCOVERY_POLICY_VERSION,
            feasibility_policy_version=SELECTED_WEBSITE_FEASIBILITY_POLICY_VERSION,
            config_policy_version="selected_website_acquisition_config_policy_v1",
            routing_source={"routing": "NO_USABLE_VERIFIED_FEED", "reason": "test"},
        )


DISCOVERY_CASES = [
    ("news_article", SourceRole.NEWSROOM, ("Launch | https://example.com/news/2026/08/launch",), 1, True, True),
    ("press_release", SourceRole.PRESS_RELEASES, ("Press item | https://example.com/press/2026/result",), 1, True, False),
    ("blog_article", SourceRole.BLOG, ("Blog | https://example.com/blog/2026/08/post",), 1, True, True),
    ("event", SourceRole.EVENTS_OR_PROGRAMS, ("Webinar | https://example.com/events/ai-summit",), 1, True, False),
    ("report", SourceRole.REPORTS_OR_DATA, ("Report | https://example.com/reports/market-2026",), 1, True, False),
    ("research", SourceRole.RESEARCH_PUBLICATIONS, ("Research | https://example.com/research/grid-ai",), 1, True, False),
    ("publication", SourceRole.INSIGHTS, ("Publication | https://example.com/publications/outlook",), 1, True, False),
    ("job", SourceRole.CAREERS, ("Engineer | https://example.com/jobs/12345",), 1, True, False),
    ("career", SourceRole.CAREERS, ("Career | https://example.com/careers/researcher",), 1, True, False),
    ("portfolio", SourceRole.PORTFOLIO, ("Company | https://example.com/portfolio/acme",), 1, True, False),
    ("detail_bare_url", SourceRole.NEWSROOM, ("https://example.com/news/2026/08/story",), 1, False, True),
    ("duplicate_url", SourceRole.NEWSROOM, ("A | https://example.com/news/a", "A2 | https://example.com/news/a"), 1, True, False),
    ("self_link", SourceRole.NEWSROOM, ("Home | https://example.com/news",), 0, False, False),
    ("root_link", SourceRole.NEWSROOM, ("Home | https://example.com/",), 0, False, False),
    ("page_path", SourceRole.NEWSROOM, ("Page 2 | https://example.com/news/page/2",), 0, False, False),
    ("page_query", SourceRole.NEWSROOM, ("Page 2 | https://example.com/news?page=2",), 0, False, False),
    ("next_label", SourceRole.NEWSROOM, ("Next | https://example.com/news/next",), 0, False, False),
    ("login", SourceRole.NEWSROOM, ("Login | https://example.com/login",), 0, False, False),
    ("support", SourceRole.NEWSROOM, ("Support | https://example.com/support",), 0, False, False),
    ("mailto_ignored", SourceRole.NEWSROOM, ("Mail | mailto:hello@example.com",), 0, False, False),
    ("ftp_ignored", SourceRole.NEWSROOM, ("FTP | ftp://example.com/news/a",), 0, False, False),
    ("no_pipe_ignored", SourceRole.NEWSROOM, ("Plain label only",), 0, False, False),
    ("representative_detail", SourceRole.OFFICIAL_HOMEPAGE, ("Thing | https://example.com/article/thing",), 1, True, False),
    ("external_out_of_scope", SourceRole.NEWSROOM, ("Other | https://other.example.net/news/a",), 1, True, False),
    ("fragment_dedup", SourceRole.NEWSROOM, ("A | https://example.com/news/a#top", "A | https://example.com/news/a#body"), 1, True, False),
    ("mixed_filters", SourceRole.NEWSROOM, ("Home | https://example.com/", "A | https://example.com/news/a"), 1, True, False),
    ("caps_scheme", SourceRole.NEWSROOM, ("A | HTTPS://example.com/news/a",), 1, True, False),
    ("query_detail", SourceRole.NEWSROOM, ("A | https://example.com/news/article?id=1",), 1, True, False),
    ("report_wrong_role_still_detail", SourceRole.NEWSROOM, ("Report | https://example.com/reports/a",), 1, True, False),
    ("job_wrong_role_kept_as_detail", SourceRole.REPORTS_OR_DATA, ("Job | https://example.com/jobs/a",), 1, True, False),
    ("event_on_news_role", SourceRole.NEWSROOM, ("Event | https://example.com/events/a",), 1, True, False),
    ("portfolio_wrong_role_filtered", SourceRole.NEWSROOM, ("Company | https://example.com/portfolio/a",), 0, False, False),
    ("article_for_reports", SourceRole.REPORTS_OR_DATA, ("Article | https://example.com/article/a",), 1, True, False),
    ("two_valid", SourceRole.NEWSROOM, ("A | https://example.com/news/a", "B | https://example.com/news/b"), 2, True, False),
    ("date_month_only", SourceRole.NEWSROOM, ("A | https://example.com/news/2026/08/a",), 1, True, True),
    ("no_date", SourceRole.NEWSROOM, ("A | https://example.com/news/a",), 1, True, False),
]


def make_discovery_test(case):
    name, role, lines, expected_count, expected_title, expected_date = case

    def test(self: SelectedWebsiteResolverPhase6CBase) -> None:
        evidence = self.evidence(lines=lines, role=role)
        self.assertEqual(evidence.selected_candidate_link_count, expected_count)
        self.assertEqual(evidence.item_title_support, expected_title)
        self.assertEqual(evidence.date_hint_support, expected_date)
        self.assertEqual(evidence.stable_item_identity_support, expected_count > 0)
        if name == "external_out_of_scope":
            self.assertEqual(evidence.in_scope_candidate_link_count, 0)
            self.assertEqual(evidence.out_of_scope_candidate_link_count, 1)

    return test


class SelectedWebsiteResolverDiscoveryTests(SelectedWebsiteResolverPhase6CBase):
    pass


for index, case in enumerate(DISCOVERY_CASES, start=1):
    setattr(
        SelectedWebsiteResolverDiscoveryTests,
        f"test_discovery_case_{index:02d}_{case[0]}",
        make_discovery_test(case),
    )


class SelectedWebsiteResolverFeasibilityTests(SelectedWebsiteResolverPhase6CBase):
    def test_feasible_current_evidence_creates_config(self) -> None:
        result = self.result(current=self.evidence(lines=("A | https://example.com/news/a",)))
        self.assertEqual(result.feasibility_status, SelectedWebsiteResolutionStatus.FEASIBLE)
        self.assertIsNotNone(result.selected_website_acquisition_config)

    def test_feasible_current_output_has_no_final_acquisition_resolution(self) -> None:
        result = self.result(current=self.evidence(lines=("A | https://example.com/news/a",)))
        self.assertFalse(hasattr(result, "acquisition_resolution_id"))

    def test_feasible_config_uses_selected_website_method_only_inside_config(self) -> None:
        result = self.result(current=self.evidence(lines=("A | https://example.com/news/a",)))
        self.assertEqual(result.selected_website_acquisition_config.acquisition_method.value, "selected_website")

    def test_empty_current_with_historical_evidence_needs_review(self) -> None:
        result = self.result(
            current=self.evidence(lines=()),
            historical=self.evidence(lines=("A | https://example.com/news/a",)),
        )
        self.assertEqual(result.feasibility_status, SelectedWebsiteResolutionStatus.NEEDS_REVIEW)
        self.assertIsNone(result.selected_website_acquisition_config)

    def test_empty_current_without_history_is_unsupported(self) -> None:
        result = self.result(current=self.evidence(lines=()))
        self.assertEqual(result.feasibility_status, SelectedWebsiteResolutionStatus.UNSUPPORTED)

    def test_client_rendered_empty_surface_is_unsupported(self) -> None:
        result = self.result(current=self.evidence(lines=(), client_rendering=True))
        self.assertEqual(result.feasibility_status, SelectedWebsiteResolutionStatus.UNSUPPORTED)
        self.assertIn("client_rendering_required_hint", result.technical_limitations)

    def test_fetch_failure_without_history_needs_review(self) -> None:
        result = self.result(
            current=self.evidence(
                lines=(),
                fetch_status=FetchStatus.NETWORK_FAILURE,
                inspectable=False,
            )
        )
        self.assertEqual(result.feasibility_status, SelectedWebsiteResolutionStatus.NEEDS_REVIEW)
        self.assertEqual(result.candidate_item_link_discoverability, "fetch_failed")

    def test_fetch_failure_with_history_needs_review(self) -> None:
        result = self.result(
            current=self.evidence(
                lines=(),
                fetch_status=FetchStatus.TIMEOUT,
                inspectable=False,
            ),
            historical=self.evidence(lines=("A | https://example.com/news/a",)),
        )
        self.assertEqual(result.feasibility_status, SelectedWebsiteResolutionStatus.NEEDS_REVIEW)
        self.assertTrue(result.normalized_item_url_support)

    def test_out_of_scope_only_current_is_unsupported(self) -> None:
        result = self.result(current=self.evidence(lines=("Other | https://other.example.net/news/a",)))
        self.assertEqual(result.feasibility_status, SelectedWebsiteResolutionStatus.UNSUPPORTED)

    def test_pagination_hint_is_limitation_not_blocker(self) -> None:
        result = self.result(current=self.evidence(lines=("A | https://example.com/news/a",), pagination=True))
        self.assertEqual(result.feasibility_status, SelectedWebsiteResolutionStatus.FEASIBLE)
        self.assertIn("pagination_hints_present_not_followed", result.technical_limitations)

    def test_date_missing_is_limited_not_blocker(self) -> None:
        result = self.result(current=self.evidence(lines=("A | https://example.com/news/a",)))
        self.assertEqual(result.feasibility_status, SelectedWebsiteResolutionStatus.FEASIBLE)
        self.assertFalse(result.date_hint_support)
        self.assertIn("date_hint_support_limited", result.technical_limitations)

    def test_date_present_sets_strategy_ref(self) -> None:
        result = self.result(current=self.evidence(lines=("A | https://example.com/news/2026/08/a",)))
        self.assertTrue(result.date_hint_support)
        self.assertIsNotNone(result.selected_website_acquisition_config.date_extraction_strategy_ref)

    def test_title_missing_sets_null_title_strategy(self) -> None:
        result = self.result(current=self.evidence(lines=("https://example.com/news/a",)))
        self.assertFalse(result.item_title_support)
        self.assertIsNone(result.selected_website_acquisition_config.title_extraction_strategy_ref)

    def test_current_and_historical_overlap_consistency(self) -> None:
        result = self.result(
            current=self.evidence(lines=("A | https://example.com/news/a",)),
            historical=self.evidence(lines=("A old | https://example.com/news/a",)),
        )
        self.assertEqual(result.bounded_extraction_consistency, "current_and_historical_compatible")

    def test_current_only_consistency(self) -> None:
        result = self.result(current=self.evidence(lines=("A | https://example.com/news/a",)))
        self.assertEqual(result.bounded_extraction_consistency, "current_only_evidence")

    def test_historical_only_consistency(self) -> None:
        result = self.result(
            current=self.evidence(lines=()),
            historical=self.evidence(lines=("A | https://example.com/news/a",)),
        )
        self.assertEqual(result.bounded_extraction_consistency, "historical_only_current_uncertain")

    def test_unsupported_consistency(self) -> None:
        result = self.result(current=self.evidence(lines=()))
        self.assertEqual(result.bounded_extraction_consistency, "insufficient_evidence")

    def test_result_id_is_deterministic(self) -> None:
        current = self.evidence(lines=("A | https://example.com/news/a",))
        self.assertEqual(self.result(current=current), self.result(current=current))

    def test_result_reason_codes_are_sorted(self) -> None:
        result = self.result(current=self.evidence(lines=("A | https://example.com/news/a",), pagination=True))
        self.assertEqual(tuple(sorted(result.reason_codes)), result.reason_codes)

    def test_result_limitations_are_sorted(self) -> None:
        result = self.result(current=self.evidence(lines=("A | https://example.com/news/a",), pagination=True))
        self.assertEqual(tuple(sorted(result.technical_limitations)), result.technical_limitations)


class SelectedWebsiteResolverRoutingAndPersistenceTests(SelectedWebsiteResolverPhase6CBase):
    def planning_result(self) -> AcquisitionPlanningResult:
        base_plan = self.plan()
        website_plans = (
            base_plan,
            replace(base_plan, selected_website_resolution_plan_id="selected_plan_b", candidate_source_id="candidate_b"),
            replace(base_plan, selected_website_resolution_plan_id="selected_plan_c", candidate_source_id="candidate_c"),
        )
        acquisition = self.acquisition_plan()
        acquisition_plans = (
            acquisition,
            replace(acquisition, acquisition_resolution_plan_id="acq_plan_b", candidate_source_id="candidate_b"),
            replace(acquisition, acquisition_resolution_plan_id="acq_plan_c", candidate_source_id="candidate_c"),
        )
        website_plans = (
            replace(website_plans[0], acquisition_resolution_plan_id="acq_plan_a"),
            replace(website_plans[1], acquisition_resolution_plan_id="acq_plan_b"),
            replace(website_plans[2], acquisition_resolution_plan_id="acq_plan_c"),
        )
        return AcquisitionPlanningResult(
            acquisition_resolution_plans=acquisition_plans,
            feed_verification_plans=(),
            selected_website_resolution_plans=website_plans,
            deferred_feed_candidates=(),
            diagnostics=(),
            phase5_handoff_input_hash="handoff",
            approved_input_count=3,
            planning_policy_version="acquisition_planning_policy_v1",
            input_fingerprint="planning_input",
            output_hash="phase6a_hash",
            generation={"phase": "6a"},
        )

    def feed_payload(self) -> dict:
        return {
            "output_hash": "phase6b_hash",
            "phase6c_routing": [
                {"candidate_source_id": "candidate_a", "selected_website_resolution_plan_id": "selected_plan_a", "routing": "NO_USABLE_VERIFIED_FEED", "reason": "no_feed"},
                {"candidate_source_id": "candidate_b", "selected_website_resolution_plan_id": "selected_plan_b", "routing": "HAS_USABLE_VERIFIED_FEED", "reason": "feed"},
                {"candidate_source_id": "candidate_c", "selected_website_resolution_plan_id": "selected_plan_c", "routing": "NO_USABLE_VERIFIED_FEED", "reason": "bad_feed"},
            ],
        }

    def test_routing_selects_only_no_usable_feed(self) -> None:
        selected = select_phase6c_execution_plans(
            planning_result=self.planning_result(),
            feed_verification_result_payload=self.feed_payload(),
        )
        self.assertEqual([item.candidate_source_id for item in selected], ["candidate_a", "candidate_c"])

    def test_routing_excludes_usable_feed_source(self) -> None:
        selected_ids = {
            item.candidate_source_id
            for item in select_phase6c_execution_plans(
                planning_result=self.planning_result(),
                feed_verification_result_payload=self.feed_payload(),
            )
        }
        self.assertNotIn("candidate_b", selected_ids)

    def test_missing_routing_excludes_plan(self) -> None:
        payload = {"output_hash": "x", "phase6c_routing": []}
        selected = select_phase6c_execution_plans(
            planning_result=self.planning_result(),
            feed_verification_result_payload=payload,
        )
        self.assertEqual(selected, ())

    def test_build_phase6c_fetcher_uses_html_only_policy(self) -> None:
        fetcher = build_phase6c_source_fetcher(session=object())
        self.assertEqual(fetcher.policy.accepted_content_types, ("text/html", "application/xhtml+xml"))
        self.assertEqual(fetcher.policy.batch_size, 2)

    def test_persist_result_set_is_idempotent(self) -> None:
        current = self.evidence(lines=("A | https://example.com/news/a",))
        result = self.result(current=current)
        execution = type("Execution", (), {})()
        execution.to_dict = lambda: {"result": result.to_dict()}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "result.json"
            from src.source_monitoring.selected_website_resolver import SelectedWebsiteResolutionResultSet
            result_set = SelectedWebsiteResolutionResultSet(
                selected_website_resolution_results=(),
                phase6a_input_hash="a",
                phase6b_input_hash="b",
                input_fingerprint="input",
                result_distribution={},
                per_source_summary=(),
                phase6d_routing=(),
                diagnostics=(),
                generation={},
                output_hash="out",
            )
            first = persist_selected_website_resolution_results(result_set=result_set, output_file=path)
            first_mtime = first.stat().st_mtime_ns
            second = persist_selected_website_resolution_results(result_set=result_set, output_file=path)
            self.assertEqual(first, second)
            self.assertEqual(first_mtime, second.stat().st_mtime_ns)

    def test_result_set_execution_counts_can_be_zero_with_no_routes(self) -> None:
        result_set = execute_selected_website_resolution_plans(
            planning_result=self.planning_result(),
            feed_verification_result_payload={"output_hash": "b", "phase6c_routing": []},
            historical_inspections=(),
        )
        self.assertEqual(result_set.selected_website_resolution_results, ())
        self.assertEqual(result_set.generation["http_calls_possible_max"], 0)

    def test_result_set_output_hash_is_populated(self) -> None:
        result_set = execute_selected_website_resolution_plans(
            planning_result=self.planning_result(),
            feed_verification_result_payload={"output_hash": "b", "phase6c_routing": []},
            historical_inspections=(),
        )
        self.assertEqual(len(result_set.output_hash), 64)

    def test_phase6d_routing_keeps_feed_route_for_excluded_source(self) -> None:
        result_set = execute_selected_website_resolution_plans(
            planning_result=self.planning_result(),
            feed_verification_result_payload=self.feed_payload(),
            historical_inspections=(),
            fetcher=build_phase6c_source_fetcher(session=FailingSession()),
        )
        feed_routes = [
            item for item in result_set.phase6d_routing
            if item["candidate_source_id"] == "candidate_b"
        ]
        self.assertEqual(feed_routes[0]["phase6d_route"], "USE_VERIFIED_FEED_RESOLUTION")

    def test_failed_execution_routes_to_review(self) -> None:
        result_set = execute_selected_website_resolution_plans(
            planning_result=self.planning_result(),
            feed_verification_result_payload=self.feed_payload(),
            historical_inspections=(),
            fetcher=build_phase6c_source_fetcher(session=FailingSession()),
        )
        non_feed_routes = [
            item for item in result_set.phase6d_routing
            if item["candidate_source_id"] == "candidate_a"
        ]
        self.assertEqual(non_feed_routes[0]["phase6d_route"], "NEEDS_REVIEW_OR_UNSUPPORTED")

    def test_phase6c_does_not_emit_acquisition_resolutions(self) -> None:
        result_set = execute_selected_website_resolution_plans(
            planning_result=self.planning_result(),
            feed_verification_result_payload={"output_hash": "b", "phase6c_routing": []},
            historical_inspections=(),
        )
        self.assertFalse(hasattr(result_set, "acquisition_resolutions"))


class FailingSession:
    def request(self, method, url, **kwargs):
        raise requests.ConnectionError("network unavailable in unit test")


if __name__ == "__main__":
    unittest.main()
