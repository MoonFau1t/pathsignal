import hashlib
from pathlib import Path
import shutil
import unittest

import requests

from src.config import PROJECT_ROOT
from src.source_monitoring.acquisition_models import (
    AcquisitionPlanningResult,
    AcquisitionResolutionPlan,
    FeedFormat,
    FeedHintEvidenceRef,
    FeedParseStatus,
    FeedVerificationPlan,
    FeedVerificationStatus,
    PlanStatus,
    SelectedWebsiteResolutionPlan,
)
from src.source_monitoring.feed_verifier import (
    DEFAULT_MAX_SAMPLED_FEED_ENTRIES,
    FEED_ACCEPTED_CONTENT_TYPES,
    FEED_DOCUMENT_PARSER_VERSION,
    FEED_VERIFICATION_FETCH_POLICY_VERSION,
    FEED_VERIFIER_POLICY_VERSION,
    FeedVerifier,
    assess_source_feed_relationship,
    execute_feed_verification_plans,
    parse_feed_document,
)
from src.source_monitoring.source_discovery_models import SourceFormatHint, SourceRole
from src.source_monitoring.source_evaluation_models import FetchStatus
from src.source_monitoring.source_fetcher import SourceFetchPolicy, SourceFetcher


class FeedVerifierPhase6BTests(unittest.TestCase):
    def setUp(self) -> None:
        short_name = hashlib.sha256(self._testMethodName.encode("utf-8")).hexdigest()[:12]
        self.test_root = PROJECT_ROOT / "tmp_phase6b_feed_tests" / short_name
        if self.test_root.exists():
            shutil.rmtree(self.test_root)
        self.test_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self.test_root.exists():
            shutil.rmtree(self.test_root)

    def fetcher(self, session, *, cache_enabled: bool = True, max_bytes: int = 10000) -> SourceFetcher:
        return SourceFetcher(
            policy=SourceFetchPolicy(
                timeout_seconds=5,
                max_response_bytes=max_bytes,
                max_redirects=3,
                accepted_content_types=FEED_ACCEPTED_CONTENT_TYPES,
                fetch_policy_version=FEED_VERIFICATION_FETCH_POLICY_VERSION,
                artifact_root=self.test_root / "raw_feeds",
                failure_root=self.test_root / "failures",
                cache_enabled=cache_enabled,
                batch_size=10,
            ),
            session=session,
            now_fn=TimeSequence(("2026-08-09T00:00:00+00:00",)),
            monotonic_fn=MonotonicSequence(),
        )

    def verify_body(
        self,
        body: bytes,
        *,
        url: str = "https://example.com/feed",
        source_url: str = "https://example.com/news",
        content_type: str = "application/rss+xml",
        max_sampled_entries: int = DEFAULT_MAX_SAMPLED_FEED_ENTRIES,
    ):
        session = FakeSession({url: [response(200, url, body, headers={"Content-Type": content_type})]})
        result_set = execute_feed_verification_plans(
            planning_result=planning_result(feed_url=url, source_url=source_url),
            fetcher=self.fetcher(session),
            max_sampled_entries=max_sampled_entries,
        )
        return result_set.feed_verification_results[0], session

    def test_01_standard_rss_20_is_recognized(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "https://example.com/a", "g1"),)))
        self.assertEqual(execution.result.verified_feed_format, FeedFormat.RSS)
        self.assertEqual(execution.result.parse_status, FeedParseStatus.PARSED_VALID)

    def test_02_standard_atom_10_is_recognized(self):
        execution, _ = self.verify_body(atom(entries=(atom_entry("One", "https://example.com/a", "id1"),)), content_type="application/atom+xml")
        self.assertEqual(execution.result.verified_feed_format, FeedFormat.ATOM)
        self.assertEqual(execution.result.parse_status, FeedParseStatus.PARSED_VALID)

    def test_03_atom_namespace_handling(self):
        parsed = parse_feed_document(raw_bytes=atom(entries=(atom_entry("One", "/a", "id1"),)), final_url="https://example.com/feed")
        self.assertEqual(parsed.feed_format, FeedFormat.ATOM)
        self.assertEqual(parsed.sampled_entries[0].normalized_link, "https://example.com/a")

    def test_04_rss_title_link_guid_date_extraction(self):
        parsed = parse_feed_document(raw_bytes=rss(items=(rss_item("One", "/a", "g1", date="Tue, 01 Jan 2030 00:00:00 GMT"),)), final_url="https://example.com/feed")
        entry = parsed.sampled_entries[0]
        self.assertEqual((entry.title, entry.normalized_link, entry.stable_id, entry.date_parseable), ("One", "https://example.com/a", "g1", True))

    def test_05_atom_title_link_id_published_extraction(self):
        parsed = parse_feed_document(raw_bytes=atom(entries=(atom_entry("One", "/a", "id1", published="2030-01-01T00:00:00Z"),)), final_url="https://example.com/feed")
        entry = parsed.sampled_entries[0]
        self.assertEqual((entry.title, entry.normalized_link, entry.stable_id, entry.date_parseable), ("One", "https://example.com/a", "id1", True))

    def test_06_atom_updated_fallback_counts_as_date_capability(self):
        execution, _ = self.verify_body(atom(entries=(atom_entry("One", "/a", "id1", updated="2030-01-01T00:00:00Z"),)), content_type="application/atom+xml")
        self.assertTrue(execution.result.publication_date_support)

    def test_07_feed_level_home_link_is_extracted(self):
        execution, _ = self.verify_body(rss(link="https://example.com", items=(rss_item("One", "/a", "g1"),)))
        self.assertEqual(execution.result.feed_home_link, "https://example.com")

    def test_08_relative_entry_url_resolution(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "../article", "g1"),)), url="https://example.com/news/feed")
        self.assertEqual(execution.sampled_entries[0].normalized_link, "https://example.com/article")

    def test_09_relative_feed_level_link_resolution(self):
        execution, _ = self.verify_body(rss(link="/news", items=(rss_item("One", "/a", "g1"),)))
        self.assertEqual(execution.result.feed_home_link, "https://example.com/news")

    def test_10_malformed_xml_is_parse_failure(self):
        execution, _ = self.verify_body(b"<rss><channel>", content_type="application/xml")
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.PARSE_FAILURE)

    def test_11_empty_body_is_empty_or_insufficient(self):
        execution, _ = self.verify_body(b"", content_type="application/xml")
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.EMPTY_OR_INSUFFICIENT)

    def test_12_html_error_page_with_http_200_is_not_feed_success(self):
        execution, _ = self.verify_body(b"<html><body>error</body></html>", content_type="text/html")
        self.assertIn(execution.result.verification_status, {FeedVerificationStatus.INVALID_FEED, FeedVerificationStatus.PARSE_FAILURE})
        self.assertFalse(execution.result.usable_for_monitoring)

    def test_13_valid_xml_that_is_not_feed_is_invalid_feed(self):
        execution, _ = self.verify_body(b"<root><item /></root>", content_type="application/xml")
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.INVALID_FEED)

    def test_14_empty_valid_rss_feed_is_insufficient(self):
        execution, _ = self.verify_body(rss(items=()))
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.EMPTY_OR_INSUFFICIENT)

    def test_15_empty_valid_atom_feed_is_insufficient(self):
        execution, _ = self.verify_body(atom(entries=()), content_type="application/atom+xml")
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.EMPTY_OR_INSUFFICIENT)

    def test_16_rss_guid_without_links_has_identity_but_is_limited(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "", "g1"),)))
        self.assertTrue(execution.result.stable_item_identity_support)
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.VERIFIED_BUT_LIMITED)

    def test_17_rss_links_without_guid_has_identity_from_url(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "https://example.com/a", ""),)))
        self.assertTrue(execution.result.stable_item_identity_support)
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.VERIFIED_USABLE)

    def test_18_atom_id_without_usable_item_link_is_limited(self):
        execution, _ = self.verify_body(atom(entries=(atom_entry("One", "", "id1"),)), content_type="application/atom+xml")
        self.assertTrue(execution.result.stable_item_identity_support)
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.VERIFIED_BUT_LIMITED)

    def test_19_stable_item_identity_support_true_with_guid(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "/a", "g1"),)))
        self.assertTrue(execution.result.stable_item_identity_support)

    def test_20_no_stable_identity_support_is_limited(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "", ""),)))
        self.assertFalse(execution.result.stable_item_identity_support)
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.VERIFIED_BUT_LIMITED)

    def test_21_titles_missing_is_limited_but_syntax_valid(self):
        execution, _ = self.verify_body(rss(items=(rss_item("", "/a", "g1"),)))
        self.assertTrue(execution.result.syntax_valid)
        self.assertFalse(execution.result.title_support)

    def test_22_mixed_title_availability_supports_titles(self):
        execution, _ = self.verify_body(rss(items=(rss_item("", "/a", "g1"), rss_item("Two", "/b", "g2"))))
        self.assertTrue(execution.result.title_support)
        self.assertEqual(execution.entries_with_titles, 1)

    def test_23_publication_dates_missing_does_not_block_usable_feed(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "/a", "g1"),)))
        self.assertFalse(execution.result.publication_date_support)
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.VERIFIED_USABLE)

    def test_24_malformed_publication_date_is_counted_separately(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "/a", "g1", date="not-a-date"),)))
        self.assertEqual(execution.entries_with_unparseable_dates, 1)

    def test_25_date_missing_but_stable_identity_still_usable(self):
        execution, _ = self.verify_body(atom(entries=(atom_entry("One", "/a", "id1"),)), content_type="application/atom+xml")
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.VERIFIED_USABLE)

    def test_26_duplicate_guids_detected(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "/a", "g1"), rss_item("Two", "/b", "g1"))))
        self.assertEqual(execution.duplicate_entry_identity_count, 1)

    def test_27_duplicate_normalized_links_detected(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "/a#x", ""), rss_item("Two", "/a#y", ""))))
        self.assertEqual(execution.duplicate_entry_identity_count, 1)

    def test_28_document_order_sampling_is_deterministic(self):
        execution, _ = self.verify_body(rss(items=tuple(rss_item(str(i), f"/{i}", f"g{i}") for i in range(5))))
        self.assertEqual([item.title for item in execution.sampled_entries], ["0", "1", "2", "3", "4"])

    def test_29_sample_entry_budget_enforced(self):
        execution, _ = self.verify_body(rss(items=tuple(rss_item(str(i), f"/{i}", f"g{i}") for i in range(5))), max_sampled_entries=2)
        self.assertEqual(execution.total_entry_count, 5)
        self.assertEqual(execution.result.sampled_entry_count, 2)

    def test_30_oversized_feed_controlled_through_fetch_boundary(self):
        url = "https://example.com/feed"
        session = FakeSession({url: [response(200, url, b"x" * 100, headers={"Content-Type": "application/rss+xml", "Content-Length": "100"})]})
        result_set = execute_feed_verification_plans(
            planning_result=planning_result(feed_url=url),
            fetcher=self.fetcher(session, max_bytes=10),
        )
        execution = result_set.feed_verification_results[0]
        self.assertEqual(execution.result.fetch_status, FetchStatus.RESPONSE_TOO_LARGE)

    def test_31_unsupported_content_type_is_preserved(self):
        url = "https://example.com/feed"
        session = FakeSession({url: [response(200, url, b"bin", headers={"Content-Type": "application/octet-stream"})]})
        result_set = execute_feed_verification_plans(planning_result=planning_result(feed_url=url), fetcher=self.fetcher(session))
        self.assertEqual(result_set.feed_verification_results[0].result.verification_status, FeedVerificationStatus.UNSUPPORTED_CONTENT)

    def test_32_same_domain_relationship(self):
        status, _ = assess_source_feed_relationship(source_url="https://example.com/news", feed_final_url="https://example.com/feed", feed_home_link=None)
        self.assertEqual(status, "same_domain")

    def test_33_related_home_link_relationship(self):
        status, _ = assess_source_feed_relationship(source_url="https://example.com/news", feed_final_url="https://feeds.example.net/rss", feed_home_link="https://example.com")
        self.assertEqual(status, "feed_home_link_related")

    def test_34_unresolved_cross_domain_relationship(self):
        status, _ = assess_source_feed_relationship(source_url="https://example.com", feed_final_url="https://other.example/feed", feed_home_link=None)
        self.assertEqual(status, "unresolved_cross_domain")

    def test_35_redirect_provenance_preserved(self):
        session = FakeSession({
            "https://example.com/feed": [response(302, "https://example.com/feed", b"", headers={"Location": "https://example.com/rss"})],
            "https://example.com/rss": [response(200, "https://example.com/rss", rss(items=(rss_item("One", "/a", "g1"),)), headers={"Content-Type": "application/rss+xml"})],
        })
        result_set = execute_feed_verification_plans(planning_result=planning_result(), fetcher=self.fetcher(session))
        self.assertEqual(len(result_set.feed_verification_results[0].result.redirect_chain), 1)

    def test_36_feed_hint_evidence_provenance_preserved(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "/a", "g1"),)))
        self.assertEqual(execution.plan.feed_hint_evidence_refs[0].feed_hint_reference_id, "feed_hint_ref_test")

    def test_37_rss_valid_but_unusable_limited(self):
        execution, _ = self.verify_body(rss(items=(rss_item("", "", "g1"),)))
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.VERIFIED_BUT_LIMITED)

    def test_38_atom_valid_and_usable(self):
        execution, _ = self.verify_body(atom(entries=(atom_entry("One", "/a", "id1"),)), content_type="application/atom+xml")
        self.assertEqual(execution.result.verification_status, FeedVerificationStatus.VERIFIED_USABLE)

    def test_39_valid_syntax_not_equal_usable_invariant(self):
        execution, _ = self.verify_body(rss(items=()))
        self.assertTrue(execution.result.syntax_valid)
        self.assertFalse(execution.result.usable_for_monitoring)

    def test_40_http_success_not_equal_feed_success_invariant(self):
        execution, _ = self.verify_body(b"<root />", content_type="application/xml")
        self.assertEqual(execution.result.http_status, 200)
        self.assertFalse(execution.result.syntax_valid)

    def test_41_phase5_approval_data_unchanged_after_feed_failure(self):
        plan = planning_result()
        before = plan.to_dict()
        execute_feed_verification_plans(
            planning_result=plan,
            fetcher=self.fetcher(FakeSession({"https://example.com/feed": [requests.Timeout("slow")]})),
        )
        self.assertEqual(plan.to_dict(), before)

    def test_42_verified_usable_feed_does_not_create_acquisition_resolution(self):
        result_set = execute_feed_verification_plans(
            planning_result=planning_result(),
            fetcher=self.fetcher(FakeSession({"https://example.com/feed": [response(200, "https://example.com/feed", rss(items=(rss_item("One", "/a", "g1"),)), headers={"Content-Type": "application/rss+xml"})]})),
        )
        payload = result_set.to_dict()
        self.assertNotIn("acquisition_resolution_id", str(payload).casefold())
        self.assertNotIn("selected_acquisition_config_ref", str(payload).casefold())

    def test_43_verified_usable_feed_does_not_choose_acquisition_method(self):
        execution, _ = self.verify_body(rss(items=(rss_item("One", "/a", "g1"),)))
        self.assertNotIn("acquisition_method", execution.result.to_dict())

    def test_44_no_generic_url_guessing(self):
        execution, session = self.verify_body(rss(items=(rss_item("One", "/a", "g1"),)), url="https://example.com/custom-feed")
        self.assertEqual([call["url"] for call in session.calls], ["https://example.com/custom-feed"])
        self.assertEqual(execution.plan.feed_candidate_url, "https://example.com/custom-feed")

    def test_45_item_urls_not_fetched(self):
        _, session = self.verify_body(rss(items=(rss_item("One", "https://example.com/item", "g1"),)))
        self.assertEqual(session.call_count, 1)

    def test_46_homepage_link_not_fetched(self):
        _, session = self.verify_body(rss(link="https://example.com", items=(rss_item("One", "/a", "g1"),)))
        self.assertEqual(session.call_count, 1)

    def test_47_no_deepseek_call_surface(self):
        payload = execute_feed_verification_plans(
            planning_result=planning_result(),
            fetcher=self.fetcher(FakeSession({"https://example.com/feed": [response(200, "https://example.com/feed", rss(items=(rss_item("One", "/a", "g1"),)), headers={"Content-Type": "application/rss+xml"})]})),
        ).to_dict()
        self.assertNotIn("deepseek", str(payload).casefold())

    def test_48_no_brave_call_surface(self):
        payload = execute_feed_verification_plans(
            planning_result=planning_result(),
            fetcher=self.fetcher(FakeSession({"https://example.com/feed": [response(200, "https://example.com/feed", rss(items=(rss_item("One", "/a", "g1"),)), headers={"Content-Type": "application/rss+xml"})]})),
        ).to_dict()
        self.assertNotIn("brave", str(payload).casefold())

    def test_49_deterministic_result_id_and_hash(self):
        body = rss(items=(rss_item("One", "/a", "g1"),))
        first, _ = self.verify_body(body)
        second, _ = self.verify_body(body)
        self.assertEqual(first.result.feed_verification_result_id, second.result.feed_verification_result_id)
        self.assertEqual(first.result.input_fingerprint, second.result.input_fingerprint)

    def test_50_raw_byte_change_invalidates_result_fingerprint(self):
        first, _ = self.verify_body(rss(items=(rss_item("One", "/a", "g1"),)), url="https://example.com/a")
        second, _ = self.verify_body(rss(items=(rss_item("Two", "/a", "g1"),)), url="https://example.com/b")
        self.assertNotEqual(first.result.input_fingerprint, second.result.input_fingerprint)

    def test_51_compatible_cached_success_avoids_http(self):
        url = "https://example.com/feed"
        body = rss(items=(rss_item("One", "/a", "g1"),))
        plan = planning_result(feed_url=url)
        first = execute_feed_verification_plans(planning_result=plan, fetcher=self.fetcher(FakeSession({url: [response(200, url, body, headers={"Content-Type": "application/rss+xml"})]})))
        second = execute_feed_verification_plans(planning_result=plan, fetcher=self.fetcher(RaisingSession()))
        self.assertEqual(first.feed_verification_results[0].result.feed_verification_result_id, second.feed_verification_results[0].result.feed_verification_result_id)
        self.assertEqual(first.output_hash, second.output_hash)
        self.assertTrue(second.feed_verification_results[0].fetch_cache_hit)

    def test_52_compatible_cached_failure_avoids_http(self):
        url = "https://example.com/feed"
        plan = planning_result(feed_url=url)
        first = execute_feed_verification_plans(planning_result=plan, fetcher=self.fetcher(FakeSession({url: [response(404, url, b"no", headers={"Content-Type": "text/plain"})]})))
        second = execute_feed_verification_plans(planning_result=plan, fetcher=self.fetcher(RaisingSession()))
        self.assertEqual(first.feed_verification_results[0].result.fetch_status, second.feed_verification_results[0].result.fetch_status)
        self.assertTrue(second.feed_verification_results[0].fetch_cache_hit)

    def test_53_raw_artifact_immutable_on_replay(self):
        url = "https://example.com/feed"
        plan = planning_result(feed_url=url)
        first = execute_feed_verification_plans(planning_result=plan, fetcher=self.fetcher(FakeSession({url: [response(200, url, rss(items=(rss_item("One", "/a", "g1"),)), headers={"Content-Type": "application/rss+xml"})]})))
        artifact = PROJECT_ROOT / first.feed_verification_results[0].raw_artifact_ref["artifact_path"]
        before = artifact.stat().st_mtime_ns, artifact.read_bytes()
        execute_feed_verification_plans(planning_result=plan, fetcher=self.fetcher(RaisingSession()))
        after = artifact.stat().st_mtime_ns, artifact.read_bytes()
        self.assertEqual(before, after)

    def test_54_one_feed_failure_does_not_stop_another_plan(self):
        plan = planning_result(feed_urls=("https://example.com/bad", "https://example.com/good"))
        session = FakeSession({
            "https://example.com/bad": [requests.Timeout("slow")],
            "https://example.com/good": [response(200, "https://example.com/good", rss(items=(rss_item("One", "/a", "g1"),)), headers={"Content-Type": "application/rss+xml"})],
        })
        result_set = execute_feed_verification_plans(planning_result=plan, fetcher=self.fetcher(session))
        self.assertEqual(len(result_set.feed_verification_results), 2)
        self.assertIn(FeedVerificationStatus.VERIFIED_USABLE, {item.result.verification_status for item in result_set.feed_verification_results})

    def test_55_invalid_feed_candidate_does_not_corrupt_parent_plan(self):
        plan = planning_result()
        before_parent = plan.acquisition_resolution_plans[0].to_dict()
        execute_feed_verification_plans(planning_result=plan, fetcher=self.fetcher(FakeSession({"https://example.com/feed": [response(200, "https://example.com/feed", b"<root />", headers={"Content-Type": "application/xml"})]})))
        self.assertEqual(plan.acquisition_resolution_plans[0].to_dict(), before_parent)


def planning_result(
    *,
    feed_url: str = "https://example.com/feed",
    feed_urls: tuple[str, ...] | None = None,
    source_url: str = "https://example.com/news",
) -> AcquisitionPlanningResult:
    urls = feed_urls or (feed_url,)
    acquisition_plan = AcquisitionResolutionPlan(
        acquisition_resolution_plan_id="acquisition_resolution_plan_test",
        candidate_source_id="candidate_source_test",
        entity_id="entity_test",
        final_source_evaluation_id="final_source_eval_test",
        source_url=source_url,
        observed_source_role=SourceRole.NEWSROOM,
        supported_information_need_ids=("need_test",),
        phase5_handoff_fingerprint="handoff_fp",
        final_source_evaluation_fingerprint="final_fp",
        source_inspection_id="inspection_test",
        source_inspection_hash="inspection_hash",
        source_observation_result_id="observation_test",
        source_observation_result_hash="observation_hash",
        known_technical_limitation_flags=(),
        strategy_order=("verify_known_feed_candidates", "selected_website_fallback", "phase6d_needs_review_or_unsupported"),
        feed_candidate_count=len(urls),
        executable_feed_verification_plan_count=len(urls),
        deferred_feed_candidate_count=0,
        selected_website_fallback_planned=True,
        dependency_model={"selected_website_fallback": {"condition": "execute_if_no_verified_usable_feed"}},
        planning_policy_version="acquisition_planning_policy_v1",
        input_fingerprint="acquisition_plan_fp",
    )
    feed_plans = tuple(
        FeedVerificationPlan(
            feed_verification_plan_id=f"feed_verification_plan_test_{index}",
            acquisition_resolution_plan_id=acquisition_plan.acquisition_resolution_plan_id,
            candidate_source_id=acquisition_plan.candidate_source_id,
            final_source_evaluation_id=acquisition_plan.final_source_evaluation_id,
            feed_candidate_url=url,
            feed_hint_evidence_refs=(
                FeedHintEvidenceRef(
                    feed_hint_reference_id="feed_hint_ref_test",
                    source_inspection_id="inspection_test",
                    source_inspection_hash="inspection_hash",
                    hint_index=index,
                    href=url,
                    normalized_url=url,
                    rel="alternate",
                    mime_type="application/rss+xml",
                    title="RSS",
                    candidate_format_hint=SourceFormatHint.RSS_CANDIDATE,
                    verification_status="unverified",
                ),
            ),
            candidate_format_hint=SourceFormatHint.RSS_CANDIDATE,
            verification_policy_version=FEED_VERIFIER_POLICY_VERSION,
            fetch_policy_ref={"method": "GET"},
            parser_policy_version=FEED_DOCUMENT_PARSER_VERSION,
            input_fingerprint=f"feed_plan_fp_{index}",
        )
        for index, url in enumerate(urls)
    )
    return AcquisitionPlanningResult(
        acquisition_resolution_plans=(acquisition_plan,),
        feed_verification_plans=feed_plans,
        selected_website_resolution_plans=(
            SelectedWebsiteResolutionPlan(
                selected_website_resolution_plan_id="selected_website_plan_test",
                acquisition_resolution_plan_id=acquisition_plan.acquisition_resolution_plan_id,
                candidate_source_id=acquisition_plan.candidate_source_id,
                final_source_evaluation_id=acquisition_plan.final_source_evaluation_id,
                source_url=source_url,
                source_inspection_id="inspection_test",
                source_inspection_hash="inspection_hash",
                source_observation_result_id="observation_test",
                source_observation_result_hash="observation_hash",
                observed_source_role=SourceRole.NEWSROOM,
                evidence_input_refs=("source_inspection:inspection_test",),
                execution_dependency={"condition": "execute_if_no_verified_usable_feed"},
                resolution_policy_version="selected_website_resolution_policy_v1",
                input_fingerprint="website_plan_fp",
                plan_status=PlanStatus.PLANNED,
            ),
        ),
        deferred_feed_candidates=(),
        diagnostics=(),
        phase5_handoff_input_hash="phase5_handoff_hash",
        approved_input_count=1,
        planning_policy_version="acquisition_planning_policy_v1",
        input_fingerprint="planning_fp",
        output_hash="phase6a_output_hash",
        generation={"http_calls": 0},
    )


def rss_item(title: str, link: str, guid: str, *, date: str | None = None) -> str:
    return (
        "<item>"
        f"<title>{title}</title>"
        f"<link>{link}</link>"
        f"<guid>{guid}</guid>"
        f"{f'<pubDate>{date}</pubDate>' if date is not None else ''}"
        "<description>summary</description>"
        "</item>"
    )


def rss(*, link: str = "https://example.com", items: tuple[str, ...]) -> bytes:
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<rss version='2.0'><channel>"
        "<title>Example Feed</title>"
        f"<link>{link}</link>"
        "<description>Feed</description>"
        f"{''.join(items)}"
        "</channel></rss>"
    ).encode("utf-8")


def atom_entry(title: str, link: str, atom_id: str, *, published: str | None = None, updated: str | None = None) -> str:
    link_markup = f"<link href='{link}' />" if link else ""
    return (
        "<entry>"
        f"<title>{title}</title>"
        f"{link_markup}"
        f"<id>{atom_id}</id>"
        f"{f'<published>{published}</published>' if published is not None else ''}"
        f"{f'<updated>{updated}</updated>' if updated is not None else ''}"
        "<summary>summary</summary>"
        "</entry>"
    )


def atom(*, entries: tuple[str, ...]) -> bytes:
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<feed xmlns='http://www.w3.org/2005/Atom'>"
        "<title>Example Atom</title>"
        "<link href='https://example.com' />"
        "<updated>2030-01-01T00:00:00Z</updated>"
        f"{''.join(entries)}"
        "</feed>"
    ).encode("utf-8")


def response(status_code: int, url: str, body_or_chunks, *, headers: dict[str, str] | None = None) -> "FakeResponse":
    default_headers = {"Content-Type": "application/rss+xml"}
    if headers:
        default_headers.update(headers)
    return FakeResponse(status_code=status_code, url=url, body_or_chunks=body_or_chunks, headers=default_headers)


class FakeResponse:
    def __init__(self, *, status_code: int, url: str, body_or_chunks, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.url = url
        self.headers = headers
        self.encoding = None
        self.chunks = body_or_chunks if isinstance(body_or_chunks, list) else [body_or_chunks]

    def iter_content(self, chunk_size: int = 8192):
        for chunk in self.chunks:
            yield chunk


class FakeSession:
    def __init__(self, responses_by_url: dict[str, list[object]]) -> None:
        self.responses_by_url = {key: list(value) for key, value in responses_by_url.items()}
        self.calls: list[dict[str, object]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        queue = self.responses_by_url.get(url)
        if not queue:
            raise requests.ConnectionError(f"no fake response for {url}")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class RaisingSession:
    def request(self, *args, **kwargs):
        raise AssertionError("external HTTP call attempted during cache replay")


class TimeSequence:
    def __init__(self, values: tuple[str, ...]) -> None:
        self.values = list(values)

    def __call__(self) -> str:
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


class MonotonicSequence:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.01
        return self.value


if __name__ == "__main__":
    unittest.main()
