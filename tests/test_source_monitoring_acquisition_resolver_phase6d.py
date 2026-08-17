from __future__ import annotations

from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path

from src.source_monitoring.acquisition_models import (
    AcquisitionMethod,
    AcquisitionPlanningResult,
    AcquisitionResolution,
    AcquisitionResolutionPlan,
    AcquisitionResolutionStatus,
    FeedFormat,
    PlanStatus,
)
from src.source_monitoring.acquisition_resolver import (
    AcquisitionResolutionPolicy,
    FeedResolutionEvidence,
    FinalAcquisitionResolver,
    parse_feed_verification_result,
    persist_final_acquisition_resolution_results,
    resolve_acquisition_plans,
    select_primary_feed,
)
from src.source_monitoring.source_discovery_models import SourceRole


class Phase6DAcquisitionResolverBase(unittest.TestCase):
    def plan(self, candidate: str = "candidate_a", *, role: SourceRole = SourceRole.NEWSROOM) -> AcquisitionResolutionPlan:
        return AcquisitionResolutionPlan(
            acquisition_resolution_plan_id=f"acq_plan_{candidate}",
            candidate_source_id=candidate,
            entity_id=f"entity_{candidate}",
            final_source_evaluation_id=f"final_{candidate}",
            source_url=f"https://{candidate}.example.com/news",
            observed_source_role=role,
            supported_information_need_ids=("need_a", "need_b"),
            phase5_handoff_fingerprint=f"handoff_{candidate}",
            final_source_evaluation_fingerprint=f"final_hash_{candidate}",
            source_inspection_id=f"inspection_{candidate}",
            source_inspection_hash=f"inspection_hash_{candidate}",
            source_observation_result_id=f"observation_{candidate}",
            source_observation_result_hash=f"observation_hash_{candidate}",
            known_technical_limitation_flags=(),
            strategy_order=("rss", "atom", "selected_website"),
            feed_candidate_count=1,
            executable_feed_verification_plan_count=1,
            deferred_feed_candidate_count=0,
            selected_website_fallback_planned=True,
            dependency_model={"selected_website": "execute_if_no_verified_usable_feed"},
            planning_policy_version="acquisition_planning_policy_v1",
            input_fingerprint=f"plan_input_{candidate}",
            plan_status=PlanStatus.PLANNED,
        )

    def planning(self, plans: tuple[AcquisitionResolutionPlan, ...] | None = None) -> AcquisitionPlanningResult:
        plans = plans or (self.plan(),)
        return AcquisitionPlanningResult(
            acquisition_resolution_plans=plans,
            feed_verification_plans=(),
            selected_website_resolution_plans=(),
            deferred_feed_candidates=(),
            diagnostics=(),
            phase5_handoff_input_hash="phase5_hash",
            approved_input_count=len(plans),
            planning_policy_version="acquisition_planning_policy_v1",
            input_fingerprint="phase6a_input",
            output_hash="phase6a_hash",
            generation={},
        )

    def feed_payload(
        self,
        candidate: str = "candidate_a",
        *,
        result_id: str = "feed_result_a",
        url: str = "https://candidate_a.example.com/feed",
        final_url: str | None = None,
        fmt: str = "rss",
        status: str = "verified_usable",
        parse_status: str = "parsed_valid",
        usable: bool | None = None,
        syntax: bool = True,
        valid_urls: int = 10,
        title: bool = True,
        date: bool = True,
        stable: bool = True,
        relationship: str = "same_domain",
        fingerprint: str | None = None,
        reason_codes: tuple[str, ...] = ("verified_usable",),
    ) -> dict:
        if usable is None:
            usable = status == "verified_usable"
        if status != "verified_usable" and valid_urls == 10:
            valid_urls = 0
        final_url = final_url or url
        return {
            "result": {
                "schema_version": "feed_verification_result_v1",
                "feed_verification_result_id": result_id,
                "feed_verification_plan_id": f"feed_plan_{result_id}",
                "candidate_source_id": candidate,
                "feed_candidate_url": url,
                "final_url": final_url,
                "fetch_execution_id": f"fetch_{result_id}",
                "fetch_status": "completed_non_html",
                "http_status": 200,
                "content_type": "application/rss+xml" if fmt == "rss" else "application/atom+xml",
                "redirect_chain": [],
                "parse_status": parse_status,
                "verified_feed_format": fmt,
                "feed_title": f"Feed {result_id}",
                "feed_home_link": f"https://{candidate}.example.com",
                "sampled_entry_count": valid_urls,
                "valid_entry_url_count": valid_urls,
                "title_support": title,
                "publication_date_support": date,
                "stable_item_identity_support": stable,
                "syntax_valid": syntax,
                "usable_for_monitoring": usable,
                "verification_status": status,
                "failure_reason": None if usable else status,
                "diagnostics": ["rss_recognized" if fmt == "rss" else "atom_recognized", f"source_relationship:{relationship}", status],
                "verification_policy_version": "feed_verification_policy_v1",
                "input_fingerprint": fingerprint or f"fingerprint_{result_id}",
            },
            "plan": {
                "feed_verification_plan_id": f"feed_plan_{result_id}",
                "parser_policy_version": "feed_parser_policy_v1",
            },
            "source_relationship_status": relationship,
            "source_relationship_diagnostic": f"{relationship} diagnostic",
            "reason_codes": list(reason_codes),
        }

    def feed_result_set(self, *executions: dict) -> dict:
        return {
            "schema_version": "phase6b_feed_verification_result_set_v1",
            "output_hash": "phase6b_hash",
            "feed_verification_results": list(executions),
        }

    def website_payload(
        self,
        candidate: str = "candidate_a",
        *,
        result_id: str = "website_result_a",
        config_id: str = "website_config_a",
        status: str = "feasible",
        include_config: bool = True,
        fingerprint: str | None = None,
        source_url: str | None = None,
    ) -> dict:
        source_url = source_url or f"https://{candidate}.example.com/news"
        config = None
        if include_config:
            config = {
                "schema_version": "selected_website_acquisition_config_v1",
                "selected_website_acquisition_config_id": config_id,
                "source_url": source_url,
                "acquisition_method": "selected_website",
                "item_discovery_strategy_version": "selected_website_item_discovery_policy_v1",
                "allowed_domain_scope": [f"{candidate}.example.com"],
                "item_link_normalization_policy": "normalize_source_url_v1_fragmentless",
                "max_discovered_items_per_run": 20,
                "title_extraction_strategy_ref": "phase5c_representative_link_text_v1",
                "date_extraction_strategy_ref": None,
                "dedup_identity_strategy_version": "selected_website_normalized_url_identity_v1",
                "source_role": "newsroom",
                "provenance": {"phase": "phase6c"},
                "input_fingerprint": f"config_fingerprint_{config_id}",
            }
        return {
            "result": {
                "schema_version": "selected_website_resolution_result_v1",
                "selected_website_resolution_result_id": result_id,
                "selected_website_resolution_plan_id": f"website_plan_{candidate}",
                "candidate_source_id": candidate,
                "final_source_evaluation_id": f"final_{candidate}",
                "source_url": source_url,
                "feasibility_status": status,
                "candidate_item_link_discoverability": "discoverable" if status == "feasible" else "not_observed",
                "normalized_item_url_support": status == "feasible",
                "item_title_support": status == "feasible",
                "date_hint_support": False,
                "item_type_role_support": status == "feasible",
                "bounded_extraction_consistency": "current_only_evidence",
                "technical_limitations": ["date_hint_support_limited"] if status == "feasible" else [],
                "selected_website_acquisition_config": config,
                "reason_codes": ["selected_website_feasible"] if status == "feasible" else [f"selected_website_{status}"],
                "resolution_policy_version": "selected_website_feasibility_policy_v1",
                "input_fingerprint": fingerprint or f"website_fingerprint_{result_id}",
            }
        }

    def website_result_set(self, *executions: dict) -> dict:
        return {
            "schema_version": "phase6c_selected_website_resolution_result_set_v1",
            "output_hash": "phase6c_hash",
            "selected_website_resolution_results": list(executions),
        }

    def resolve(self, *, feed_payload: dict | None = None, website_payload: dict | None = None, plans: tuple[AcquisitionResolutionPlan, ...] | None = None, policy: AcquisitionResolutionPolicy | None = None):
        return resolve_acquisition_plans(
            planning_result=self.planning(plans),
            feed_verification_result_payload=feed_payload or self.feed_result_set(),
            selected_website_result_payload=website_payload or self.website_result_set(),
            policy=policy,
        )


class Phase6DCoreResolutionTests(Phase6DAcquisitionResolverBase):
    def test_01_one_usable_rss_feed_resolves_rss(self) -> None:
        result = self.resolve(feed_payload=self.feed_result_set(self.feed_payload()))
        resolution = result.acquisition_resolution_results[0].resolution
        self.assertEqual(resolution.resolution_status, AcquisitionResolutionStatus.RESOLVED)
        self.assertEqual(resolution.acquisition_method, AcquisitionMethod.RSS)

    def test_02_one_usable_atom_feed_resolves_atom(self) -> None:
        feed = self.feed_payload(fmt="atom", result_id="atom_a", url="https://candidate_a.example.com/atom")
        result = self.resolve(feed_payload=self.feed_result_set(feed))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.acquisition_method, AcquisitionMethod.ATOM)

    def test_03_usable_feed_preferred_over_selected_website(self) -> None:
        result = self.resolve(
            feed_payload=self.feed_result_set(self.feed_payload()),
            website_payload=self.website_result_set(self.website_payload()),
        )
        self.assertEqual(result.acquisition_resolution_results[0].resolution.acquisition_method, AcquisitionMethod.RSS)

    def test_04_feed_hint_alone_cannot_resolve_feed(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.acquisition_method, AcquisitionMethod.SELECTED_WEBSITE)

    def test_05_feed_verification_plan_alone_cannot_resolve(self) -> None:
        result = self.resolve()
        self.assertEqual(result.acquisition_resolution_results[0].resolution.resolution_status, AcquisitionResolutionStatus.NEEDS_REVIEW)

    def test_06_valid_but_empty_feed_does_not_resolve_feed(self) -> None:
        empty = self.feed_payload(status="empty_or_insufficient", valid_urls=0, usable=False, reason_codes=("zero_entries",))
        result = self.resolve(feed_payload=self.feed_result_set(empty), website_payload=self.website_result_set(self.website_payload()))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.acquisition_method, AcquisitionMethod.SELECTED_WEBSITE)

    def test_07_verified_but_limited_does_not_resolve_feed(self) -> None:
        limited = self.feed_payload(status="verified_but_limited", valid_urls=0, usable=False, reason_codes=("no_usable_entry_urls",))
        result = self.resolve(feed_payload=self.feed_result_set(limited), website_payload=self.website_result_set(self.website_payload()))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.acquisition_method, AcquisitionMethod.SELECTED_WEBSITE)
        self.assertIn("feed_verified_but_limited", result.acquisition_resolution_results[0].resolution.resolution_reason_codes)

    def test_08_no_usable_feed_feasible_website_resolves_selected_website(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.acquisition_method, AcquisitionMethod.SELECTED_WEBSITE)

    def test_09_no_feed_plans_feasible_website_resolves_selected_website(self) -> None:
        result = self.resolve(feed_payload=self.feed_result_set(), website_payload=self.website_result_set(self.website_payload()))
        self.assertEqual(result.method_distribution["selected_website"], 1)

    def test_10_website_needs_review_resolves_needs_review(self) -> None:
        website = self.website_payload(status="needs_review", include_config=False)
        result = self.resolve(website_payload=self.website_result_set(website))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.resolution_status, AcquisitionResolutionStatus.NEEDS_REVIEW)

    def test_11_website_unsupported_resolves_unsupported_when_complete(self) -> None:
        website = self.website_payload(status="unsupported", include_config=False)
        result = self.resolve(feed_payload=self.feed_result_set(), website_payload=self.website_result_set(website))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.resolution_status, AcquisitionResolutionStatus.UNSUPPORTED)

    def test_12_transient_feed_with_unsupported_website_needs_review(self) -> None:
        unreachable = self.feed_payload(status="unreachable", parse_status="not_parsed", fmt="unknown", usable=False, syntax=False, reason_codes=("fetch_timeout",))
        website = self.website_payload(status="unsupported", include_config=False)
        result = self.resolve(feed_payload=self.feed_result_set(unreachable), website_payload=self.website_result_set(website))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.resolution_status, AcquisitionResolutionStatus.NEEDS_REVIEW)

    def test_13_unsupported_keeps_phase5_approval_accounting(self) -> None:
        website = self.website_payload(status="unsupported", include_config=False)
        result = self.resolve(feed_payload=self.feed_result_set(), website_payload=self.website_result_set(website))
        self.assertEqual(result.population_accounting["approved_source_count"], 1)

    def test_14_resolved_requires_method_invariant(self) -> None:
        with self.assertRaises(ValueError):
            AcquisitionResolution(
                acquisition_resolution_id="bad",
                acquisition_resolution_plan_id="plan",
                candidate_source_id="candidate",
                entity_id="entity",
                final_source_evaluation_id="final",
                source_url="https://example.com",
                resolution_status=AcquisitionResolutionStatus.RESOLVED,
                acquisition_method=None,
                feed_verification_result_ids=(),
                selected_website_resolution_result_id=None,
                selected_acquisition_config_ref=None,
                verified_feed_format=None,
                technical_limitation_flags=(),
                resolution_reason_codes=(),
                evidence_quality="bad",
                resolution_policy_version="policy",
                input_fingerprint="fingerprint",
            )

    def test_15_needs_review_requires_null_method(self) -> None:
        with self.assertRaises(ValueError):
            AcquisitionResolution(
                acquisition_resolution_id="bad",
                acquisition_resolution_plan_id="plan",
                candidate_source_id="candidate",
                entity_id="entity",
                final_source_evaluation_id="final",
                source_url="https://example.com",
                resolution_status=AcquisitionResolutionStatus.NEEDS_REVIEW,
                acquisition_method=AcquisitionMethod.RSS,
                feed_verification_result_ids=(),
                selected_website_resolution_result_id=None,
                selected_acquisition_config_ref=None,
                verified_feed_format=None,
                technical_limitation_flags=(),
                resolution_reason_codes=(),
                evidence_quality="bad",
                resolution_policy_version="policy",
                input_fingerprint="fingerprint",
            )

    def test_16_unsupported_requires_null_method(self) -> None:
        with self.assertRaises(ValueError):
            AcquisitionResolution(
                acquisition_resolution_id="bad",
                acquisition_resolution_plan_id="plan",
                candidate_source_id="candidate",
                entity_id="entity",
                final_source_evaluation_id="final",
                source_url="https://example.com",
                resolution_status=AcquisitionResolutionStatus.UNSUPPORTED,
                acquisition_method=AcquisitionMethod.SELECTED_WEBSITE,
                feed_verification_result_ids=(),
                selected_website_resolution_result_id=None,
                selected_acquisition_config_ref=None,
                verified_feed_format=None,
                technical_limitation_flags=(),
                resolution_reason_codes=(),
                evidence_quality="bad",
                resolution_policy_version="policy",
                input_fingerprint="fingerprint",
            )

    def test_17_rss_method_requires_rss_evidence(self) -> None:
        with self.assertRaises(ValueError):
            AcquisitionResolution(
                acquisition_resolution_id="bad",
                acquisition_resolution_plan_id="plan",
                candidate_source_id="candidate",
                entity_id="entity",
                final_source_evaluation_id="final",
                source_url="https://example.com",
                resolution_status=AcquisitionResolutionStatus.RESOLVED,
                acquisition_method=AcquisitionMethod.RSS,
                feed_verification_result_ids=("feed",),
                selected_website_resolution_result_id=None,
                selected_acquisition_config_ref=None,
                verified_feed_format=FeedFormat.ATOM,
                technical_limitation_flags=(),
                resolution_reason_codes=(),
                evidence_quality="bad",
                resolution_policy_version="policy",
                input_fingerprint="fingerprint",
            )

    def test_18_atom_method_requires_atom_evidence(self) -> None:
        with self.assertRaises(ValueError):
            AcquisitionResolution(
                acquisition_resolution_id="bad",
                acquisition_resolution_plan_id="plan",
                candidate_source_id="candidate",
                entity_id="entity",
                final_source_evaluation_id="final",
                source_url="https://example.com",
                resolution_status=AcquisitionResolutionStatus.RESOLVED,
                acquisition_method=AcquisitionMethod.ATOM,
                feed_verification_result_ids=("feed",),
                selected_website_resolution_result_id=None,
                selected_acquisition_config_ref=None,
                verified_feed_format=FeedFormat.RSS,
                technical_limitation_flags=(),
                resolution_reason_codes=(),
                evidence_quality="bad",
                resolution_policy_version="policy",
                input_fingerprint="fingerprint",
            )

    def test_19_selected_website_requires_feasible_result(self) -> None:
        website = self.website_payload(status="needs_review", include_config=False)
        result = self.resolve(website_payload=self.website_result_set(website))
        self.assertNotEqual(result.acquisition_resolution_results[0].resolution.acquisition_method, AcquisitionMethod.SELECTED_WEBSITE)

    def test_20_selected_website_requires_config(self) -> None:
        website = self.website_payload(status="feasible", include_config=False)
        with self.assertRaises(ValueError):
            self.resolve(website_payload=self.website_result_set(website))


class Phase6DFeedSelectionTests(Phase6DAcquisitionResolverBase):
    def evidence(self, feed: dict) -> FeedResolutionEvidence:
        return FeedResolutionEvidence(
            result=parse_feed_verification_result(feed["result"]),
            execution_payload=feed,
            plan_payload=feed["plan"],
            source_relationship_status=feed["source_relationship_status"],
            source_relationship_diagnostic=feed["source_relationship_diagnostic"],
            reason_codes=tuple(feed["reason_codes"]),
        )

    def test_21_multiple_usable_feeds_handled_deterministically(self) -> None:
        a = self.feed_payload(result_id="feed_b", url="https://candidate_a.example.com/feed-b")
        b = self.feed_payload(result_id="feed_a", url="https://candidate_a.example.com/feed-a")
        result = self.resolve(feed_payload=self.feed_result_set(a, b))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.feed_verification_result_ids, ("feed_a",))

    def test_22_usable_feed_ordering_not_insertion_dependent(self) -> None:
        a = self.feed_payload(result_id="feed_a", url="https://candidate_a.example.com/feed-a")
        b = self.feed_payload(result_id="feed_b", url="https://candidate_a.example.com/feed-b")
        one = self.resolve(feed_payload=self.feed_result_set(a, b))
        two = self.resolve(feed_payload=self.feed_result_set(b, a))
        self.assertEqual(one.output_hash, two.output_hash)

    def test_23_relationship_quality_preferred(self) -> None:
        weaker = self.feed_payload(result_id="feed_a", url="https://candidate_a.example.com/a", relationship="unresolved_cross_domain")
        stronger = self.feed_payload(result_id="feed_b", url="https://candidate_a.example.com/b", relationship="same_domain")
        self.assertEqual(select_primary_feed((self.evidence(weaker), self.evidence(stronger))).result.feed_verification_result_id, "feed_b")

    def test_24_stable_identity_preferred(self) -> None:
        weak = self.feed_payload(result_id="feed_a", stable=False, url="https://candidate_a.example.com/a")
        strong = self.feed_payload(result_id="feed_b", stable=True, url="https://candidate_a.example.com/b")
        self.assertEqual(select_primary_feed((self.evidence(weak), self.evidence(strong))).result.feed_verification_result_id, "feed_b")

    def test_25_more_usable_urls_preferred(self) -> None:
        weak = self.feed_payload(result_id="feed_a", valid_urls=5, url="https://candidate_a.example.com/a")
        strong = self.feed_payload(result_id="feed_b", valid_urls=10, url="https://candidate_a.example.com/b")
        self.assertEqual(select_primary_feed((self.evidence(weak), self.evidence(strong))).result.feed_verification_result_id, "feed_b")

    def test_26_title_capability_preferred(self) -> None:
        weak = self.feed_payload(result_id="feed_a", title=False, url="https://candidate_a.example.com/a")
        strong = self.feed_payload(result_id="feed_b", title=True, url="https://candidate_a.example.com/b")
        self.assertEqual(select_primary_feed((self.evidence(weak), self.evidence(strong))).result.feed_verification_result_id, "feed_b")

    def test_27_date_capability_preferred(self) -> None:
        weak = self.feed_payload(result_id="feed_a", date=False, url="https://candidate_a.example.com/a")
        strong = self.feed_payload(result_id="feed_b", date=True, url="https://candidate_a.example.com/b")
        self.assertEqual(select_primary_feed((self.evidence(weak), self.evidence(strong))).result.feed_verification_result_id, "feed_b")

    def test_28_normalized_url_tie_break_deterministic(self) -> None:
        later = self.feed_payload(result_id="feed_later", url="https://candidate_a.example.com/z")
        earlier = self.feed_payload(result_id="feed_earlier", url="https://candidate_a.example.com/a")
        self.assertEqual(select_primary_feed((self.evidence(later), self.evidence(earlier))).result.feed_verification_result_id, "feed_earlier")

    def test_29_alternate_usable_feed_provenance_preserved(self) -> None:
        a = self.feed_payload(result_id="feed_a", url="https://candidate_a.example.com/a")
        b = self.feed_payload(result_id="feed_b", url="https://candidate_a.example.com/b")
        result = self.resolve(feed_payload=self.feed_result_set(a, b))
        execution = result.acquisition_resolution_results[0]
        self.assertEqual([item.result.feed_verification_result_id for item in execution.alternate_feed_evidence], ["feed_b"])

    def test_30_sap_like_multiple_usable_feeds_resolves_rss(self) -> None:
        topic = self.feed_payload(result_id="sap_topic", url="https://news.sap.com/topics/artificial-intelligence/feed")
        global_feed = self.feed_payload(result_id="sap_global", url="https://news.sap.com/feed")
        result = self.resolve(feed_payload=self.feed_result_set(topic, global_feed))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.acquisition_method, AcquisitionMethod.RSS)


class Phase6DIdentityAndHandoffTests(Phase6DAcquisitionResolverBase):
    def test_31_same_semantic_evidence_same_resolution_id(self) -> None:
        payload = self.feed_result_set(self.feed_payload())
        one = self.resolve(feed_payload=payload)
        two = self.resolve(feed_payload=payload)
        self.assertEqual(one.acquisition_resolution_results[0].resolution.acquisition_resolution_id, two.acquisition_resolution_results[0].resolution.acquisition_resolution_id)

    def test_32_changed_feed_result_hash_invalidates_resolution_fingerprint(self) -> None:
        one = self.resolve(feed_payload=self.feed_result_set(self.feed_payload(fingerprint="one")))
        two = self.resolve(feed_payload=self.feed_result_set(self.feed_payload(fingerprint="two")))
        self.assertNotEqual(one.acquisition_resolution_results[0].resolution.input_fingerprint, two.acquisition_resolution_results[0].resolution.input_fingerprint)

    def test_33_changed_website_result_hash_invalidates_resolution_fingerprint(self) -> None:
        one = self.resolve(website_payload=self.website_result_set(self.website_payload(fingerprint="one")))
        two = self.resolve(website_payload=self.website_result_set(self.website_payload(fingerprint="two")))
        self.assertNotEqual(one.acquisition_resolution_results[0].resolution.input_fingerprint, two.acquisition_resolution_results[0].resolution.input_fingerprint)

    def test_34_changed_resolution_policy_invalidates_fingerprint(self) -> None:
        one = self.resolve(feed_payload=self.feed_result_set(self.feed_payload()))
        policy = AcquisitionResolutionPolicy(resolution_policy_version="final_acquisition_resolution_policy_v2")
        two = self.resolve(feed_payload=self.feed_result_set(self.feed_payload()), policy=policy)
        self.assertNotEqual(one.acquisition_resolution_results[0].resolution.input_fingerprint, two.acquisition_resolution_results[0].resolution.input_fingerprint)

    def test_35_timestamps_do_not_affect_identity(self) -> None:
        feed_one = self.feed_payload()
        feed_two = self.feed_payload()
        feed_one["retrieved_at"] = "2026-01-01T00:00:00Z"
        feed_two["retrieved_at"] = "2026-08-09T00:00:00Z"
        one = self.resolve(feed_payload=self.feed_result_set(feed_one))
        two = self.resolve(feed_payload=self.feed_result_set(feed_two))
        self.assertEqual(one.output_hash, two.output_hash)

    def test_36_every_plan_gets_one_resolution(self) -> None:
        plans = (self.plan("candidate_a"), self.plan("candidate_b"))
        result = self.resolve(plans=plans)
        self.assertEqual(result.population_accounting["final_acquisition_resolution_count"], 2)

    def test_37_duplicate_source_resolution_rejected(self) -> None:
        plans = (self.plan("candidate_a"), replace(self.plan("candidate_a"), acquisition_resolution_plan_id="other"))
        with self.assertRaises(ValueError):
            self.resolve(plans=plans)

    def test_38_phase7_handoff_created_only_for_resolved(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        self.assertEqual(len(result.to_dict()["phase7_monitoring_handoffs"]), 1)

    def test_39_needs_review_excluded_from_phase7(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload(status="needs_review", include_config=False)))
        self.assertEqual(len(result.to_dict()["phase7_monitoring_handoffs"]), 0)

    def test_40_unsupported_excluded_from_phase7(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload(status="unsupported", include_config=False)))
        self.assertEqual(len(result.to_dict()["phase7_monitoring_handoffs"]), 0)

    def test_41_feed_handoff_preserves_verified_feed_ref(self) -> None:
        result = self.resolve(feed_payload=self.feed_result_set(self.feed_payload(result_id="feed_keep")))
        handoff = result.acquisition_resolution_results[0].phase7_handoff
        self.assertEqual(handoff.acquisition_config_ref, "feed_keep")

    def test_42_website_handoff_preserves_config_ref(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload(config_id="cfg_keep")))
        handoff = result.acquisition_resolution_results[0].phase7_handoff
        self.assertEqual(handoff.acquisition_config_ref, "cfg_keep")

    def test_43_supported_information_need_ids_preserved(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        self.assertEqual(result.acquisition_resolution_results[0].phase7_handoff.supported_information_need_ids, ("need_a", "need_b"))

    def test_44_source_role_preserved(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        self.assertEqual(result.acquisition_resolution_results[0].phase7_handoff.source_role, SourceRole.NEWSROOM)

    def test_45_candidate_entity_final_provenance_preserved(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        execution = result.acquisition_resolution_results[0]
        self.assertEqual(execution.resolution.candidate_source_id, "candidate_a")
        self.assertEqual(execution.resolution.entity_id, "entity_candidate_a")
        self.assertEqual(execution.resolution.final_source_evaluation_id, "final_candidate_a")

    def test_46_no_raw_xml_serialized_into_handoff(self) -> None:
        result = self.resolve(feed_payload=self.feed_result_set(self.feed_payload()))
        text = json.dumps(result.to_dict())
        self.assertNotIn("<rss", text.casefold())

    def test_47_no_raw_html_serialized_into_handoff(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        text = json.dumps(result.to_dict())
        self.assertNotIn("<html", text.casefold())

    def test_48_no_external_call_counters(self) -> None:
        result = self.resolve(feed_payload=self.feed_result_set(self.feed_payload()))
        self.assertEqual(result.generation["http_calls"], 0)
        self.assertEqual(result.generation["brave_calls"], 0)
        self.assertEqual(result.generation["deepseek_calls"], 0)
        self.assertEqual(result.generation["browser_calls"], 0)

    def test_49_ieee_like_empty_feed_plus_feasible_html_resolves_website(self) -> None:
        empty = self.feed_payload(status="empty_or_insufficient", valid_urls=0, usable=False, reason_codes=("zero_entries",))
        result = self.resolve(feed_payload=self.feed_result_set(empty), website_payload=self.website_result_set(self.website_payload()))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.acquisition_method, AcquisitionMethod.SELECTED_WEBSITE)

    def test_50_qianzhan_like_zero_feed_plus_feasible_html_resolves_website(self) -> None:
        result = self.resolve(feed_payload=self.feed_result_set(), website_payload=self.website_result_set(self.website_payload()))
        self.assertEqual(result.acquisition_resolution_results[0].resolution.acquisition_method, AcquisitionMethod.SELECTED_WEBSITE)

    def test_51_final_output_hash_deterministic(self) -> None:
        one = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        two = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        self.assertEqual(one.output_hash, two.output_hash)

    def test_52_canonical_replay_zero_external_calls(self) -> None:
        one = self.resolve(feed_payload=self.feed_result_set(self.feed_payload()))
        two = self.resolve(feed_payload=self.feed_result_set(self.feed_payload()))
        self.assertEqual(one.output_hash, two.output_hash)
        self.assertEqual(two.generation["http_calls"], 0)

    def test_53_persist_result_set_is_idempotent(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "acquisition_resolutions.json"
            first = persist_final_acquisition_resolution_results(result_set=result, output_file=path)
            first_mtime = first.stat().st_mtime_ns
            second = persist_final_acquisition_resolution_results(result_set=result, output_file=path)
            self.assertEqual(first, second)
            self.assertEqual(first_mtime, second.stat().st_mtime_ns)

    def test_54_needs_review_backlog_populated(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload(status="needs_review", include_config=False)))
        self.assertEqual(len(result.needs_review_backlog), 1)

    def test_55_unsupported_backlog_populated(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload(status="unsupported", include_config=False)))
        self.assertEqual(len(result.unsupported_provenance), 1)

    def test_56_method_distribution_tracks_unresolved(self) -> None:
        result = self.resolve()
        self.assertEqual(result.method_distribution["unresolved"], 1)

    def test_57_phase7_handoff_accounting_set_equality(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        self.assertTrue(result.phase7_handoff_accounting["handoff_matches_resolved_sources"])

    def test_58_feed_handoff_contains_runtime_parser_strategy(self) -> None:
        result = self.resolve(feed_payload=self.feed_result_set(self.feed_payload()))
        provenance = result.acquisition_resolution_results[0].phase7_handoff.provenance
        self.assertEqual(provenance["runtime_parser_strategy_ref"], "phase7_feed_runtime_parse_rss_atom_v1")

    def test_59_website_handoff_contains_discovery_strategy(self) -> None:
        result = self.resolve(website_payload=self.website_result_set(self.website_payload()))
        provenance = result.acquisition_resolution_results[0].phase7_handoff.provenance
        self.assertEqual(provenance["item_discovery_strategy_version"], "selected_website_item_discovery_policy_v1")

    def test_60_result_set_contains_no_monitoring_execution(self) -> None:
        result = self.resolve(feed_payload=self.feed_result_set(self.feed_payload()))
        self.assertFalse(result.generation["monitoring_execution_started"])

    def test_61_same_normalized_rss_target_emits_one_handoff(self) -> None:
        plans = (self.plan("candidate_a"), self.plan("candidate_b"))
        feeds = self.feed_result_set(
            self.feed_payload(
                "candidate_a",
                result_id="feed_a",
                final_url="https://www.shared.example/feed/",
            ),
            self.feed_payload(
                "candidate_b",
                result_id="feed_b",
                url="https://candidate_b.example.com/feed",
                final_url="https://shared.example/feed",
            ),
        )
        result = self.resolve(plans=plans, feed_payload=feeds)

        self.assertEqual(len(result.to_dict()["phase7_monitoring_handoffs"]), 1)

    def test_62_different_rss_targets_emit_two_handoffs(self) -> None:
        plans = (self.plan("candidate_a"), self.plan("candidate_b"))
        feeds = self.feed_result_set(
            self.feed_payload("candidate_a", result_id="feed_a"),
            self.feed_payload(
                "candidate_b",
                result_id="feed_b",
                url="https://candidate_b.example.com/feed",
            ),
        )
        result = self.resolve(plans=plans, feed_payload=feeds)

        self.assertEqual(len(result.to_dict()["phase7_monitoring_handoffs"]), 2)

    def test_63_rss_and_selected_website_identities_remain_distinct(self) -> None:
        shared_url = "https://shared.example/feed"
        plans = (
            self.plan("candidate_a"),
            replace(self.plan("candidate_b"), source_url=shared_url),
        )
        result = self.resolve(
            plans=plans,
            feed_payload=self.feed_result_set(
                self.feed_payload(
                    "candidate_a",
                    result_id="feed_a",
                    final_url=shared_url,
                )
            ),
            website_payload=self.website_result_set(
                self.website_payload(
                    "candidate_b",
                    result_id="website_b",
                    config_id="config_b",
                    source_url=shared_url,
                )
            ),
        )

        handoffs = result.to_dict()["phase7_monitoring_handoffs"]
        self.assertEqual(len(handoffs), 2)
        self.assertEqual(
            {item["acquisition_method"] for item in handoffs},
            {"rss", "selected_website"},
        )

    def test_64_converged_handoff_preserves_candidate_provenance(self) -> None:
        plans = (
            self.plan("candidate_a", role=SourceRole.RESEARCH_PUBLICATIONS),
            self.plan("candidate_b", role=SourceRole.OTHER_OFFICIAL_SECTION),
        )
        feeds = self.feed_result_set(
            self.feed_payload(
                "candidate_a",
                result_id="feed_a",
                final_url="https://shared.example/feed",
            ),
            self.feed_payload(
                "candidate_b",
                result_id="feed_b",
                url="https://candidate_b.example.com/feed",
                final_url="https://shared.example/feed",
            ),
        )
        result = self.resolve(plans=plans, feed_payload=feeds)

        provenance = result.to_dict()["phase7_monitoring_handoffs"][0][
            "provenance"
        ]["contributing_candidate_sources"]
        self.assertEqual(
            [item["candidate_source_id"] for item in provenance],
            ["candidate_a", "candidate_b"],
        )
        self.assertEqual(
            {item["source_role"] for item in provenance},
            {"research_publications", "other_official_section"},
        )
        self.assertEqual(
            {item["acquisition_config_ref"] for item in provenance},
            {"feed_a", "feed_b"},
        )
        self.assertTrue(
            all(item["acquisition_resolution_id"] for item in provenance)
        )

    def test_65_canonical_handoff_output_is_input_order_independent(self) -> None:
        plan_a = self.plan("candidate_a")
        plan_b = self.plan("candidate_b")
        feed_a = self.feed_payload(
            "candidate_a",
            result_id="feed_a",
            final_url="https://shared.example/feed",
        )
        feed_b = self.feed_payload(
            "candidate_b",
            result_id="feed_b",
            url="https://candidate_b.example.com/feed",
            final_url="https://shared.example/feed",
        )
        forward = self.resolve(
            plans=(plan_a, plan_b),
            feed_payload=self.feed_result_set(feed_a, feed_b),
        )
        reverse = self.resolve(
            plans=(plan_b, plan_a),
            feed_payload=self.feed_result_set(feed_b, feed_a),
        )

        self.assertEqual(forward.output_hash, reverse.output_hash)
        self.assertEqual(
            forward.to_dict()["phase7_monitoring_handoffs"],
            reverse.to_dict()["phase7_monitoring_handoffs"],
        )

    def test_66_handoff_accounting_uses_canonical_population(self) -> None:
        plans = (self.plan("candidate_a"), self.plan("candidate_b"))
        feeds = self.feed_result_set(
            self.feed_payload(
                "candidate_a",
                result_id="feed_a",
                final_url="https://shared.example/feed",
            ),
            self.feed_payload(
                "candidate_b",
                result_id="feed_b",
                url="https://candidate_b.example.com/feed",
                final_url="https://shared.example/feed",
            ),
        )
        accounting = self.resolve(
            plans=plans,
            feed_payload=feeds,
        ).phase7_handoff_accounting

        self.assertEqual(accounting["resolved_source_count"], 2)
        self.assertEqual(accounting["phase7_handoff_count"], 1)
        self.assertEqual(accounting["canonical_acquisition_identity_count"], 1)
        self.assertEqual(accounting["converged_candidate_source_count"], 1)
        self.assertTrue(accounting["handoff_matches_resolved_sources"])


if __name__ == "__main__":
    unittest.main()
