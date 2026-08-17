import inspect
import unittest

from src.source_monitoring.source_discovery_models import SourceRole
from src.source_monitoring.source_evaluation_models import (
    AssessmentMethod,
    EntityMatchAssessment,
    EntityMatchStatus,
    EvaluationConfidence,
    FinalEvaluationDecision,
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
    RelevanceLevel,
    SourceObservationPlan,
    SourceObservationResult,
    SourceRoleAssessment,
    SourceRoleMatchStatus,
    SourceValueLevel,
    SurfaceDurabilityAssessment,
    SurfaceDurabilityStatus,
)
from src.source_monitoring import source_final_evaluator
from src.source_monitoring.source_final_evaluator import (
    FinalEvaluationInputs,
    FinalEvidenceState,
    FinalSourceEvaluator,
    ReviewResolutionState,
)


def assessment_confidence(status):
    return EvaluationConfidence.LOW if status in {
        EntityMatchStatus.UNCERTAIN,
        OfficialityStatus.UNCERTAIN,
        SurfaceDurabilityStatus.UNCERTAIN,
        SourceRoleMatchStatus.UNCERTAIN,
        RelevanceLevel.UNCERTAIN,
    } else EvaluationConfidence.MEDIUM


def make_initial(
    *,
    cid="candidate_a",
    decision=InitialEvaluationDecision.PROCEED_TO_OBSERVATION,
    entity_status=EntityMatchStatus.CONFIRMED,
    officiality=OfficialityStatus.OFFICIAL,
    page_type=PageType.LISTING_PAGE,
    durability=SurfaceDurabilityStatus.DURABLE_SURFACE,
    role_match=SourceRoleMatchStatus.MATCH,
    relevance=RelevanceLevel.MEDIUM,
    confidence=EvaluationConfidence.MEDIUM,
    source_value=SourceValueLevel.MEDIUM,
    rationale="bounded evidence",
):
    supported_needs = () if relevance in {RelevanceLevel.LOW, RelevanceLevel.UNCERTAIN} else ("need_a",)
    return InitialSourceEvaluation(
        initial_source_evaluation_id=f"initial_{cid}",
        source_evaluation_plan_id=f"plan_{cid}",
        source_inspection_id=f"inspection_{cid}",
        semantic_evidence_bundle_id=f"bundle_{cid}",
        candidate_source_id=cid,
        entity_id=f"entity_{cid}",
        entity_match_assessment=EntityMatchAssessment(
            status=entity_status,
            confidence=assessment_confidence(entity_status),
            rationale=rationale,
            evidence_refs=("identity",),
            assessment_method=AssessmentMethod.HYBRID,
        ),
        officiality_assessment=OfficialityAssessment(
            status=officiality,
            confidence=assessment_confidence(officiality),
            rationale=rationale,
            evidence_refs=("officiality",),
            assessment_method=AssessmentMethod.HYBRID,
        ),
        page_type_assessment=PageTypeAssessment(
            page_type=page_type,
            confidence=EvaluationConfidence.MEDIUM,
            rationale=rationale,
            evidence_refs=("page_type",),
            assessment_method=AssessmentMethod.HYBRID,
        ),
        surface_durability_assessment=SurfaceDurabilityAssessment(
            status=durability,
            confidence=assessment_confidence(durability),
            rationale=rationale,
            evidence_refs=("durability",),
            assessment_method=AssessmentMethod.HYBRID,
        ),
        source_role_assessment=SourceRoleAssessment(
            planned_source_role=SourceRole.NEWSROOM,
            observed_source_role=SourceRole.NEWSROOM,
            source_role_match_status=role_match,
            confidence=assessment_confidence(role_match),
            rationale=rationale,
            evidence_refs=("role",),
            assessment_method=AssessmentMethod.HYBRID,
        ),
        information_need_relevance_assessment=InformationNeedRelevanceAssessment(
            allowed_information_need_ids=("need_a", "need_b"),
            supported_information_need_ids=supported_needs,
            relevance_level=relevance,
            confidence=assessment_confidence(relevance),
            rationale=rationale,
            evidence_refs=("need",),
            assessment_method=AssessmentMethod.LLM,
        ),
        initial_monitoring_suitability=relevance,
        source_value=source_value,
        evaluation_confidence=confidence,
        rationale=rationale,
        review_flags=("manual_review",) if decision == InitialEvaluationDecision.NEEDS_REVIEW else (),
        decision=decision,
        evaluator_policy_version="phase5d_test",
    )


def make_plan(initial):
    return SourceObservationPlan(
        source_observation_plan_id=f"observation_plan_{initial.candidate_source_id}",
        candidate_source_id=initial.candidate_source_id,
        initial_source_evaluation_id=initial.initial_source_evaluation_id,
        sampling_strategy=ObservationSamplingStrategy.BOUNDED_SOURCE_SAMPLE,
        max_item_count=5,
        lookback_window_days=90,
        observation_policy_version="phase5e_test",
        input_fingerprint=f"obs_plan_fp_{initial.candidate_source_id}",
    )


def make_evidence(plan, *, need_ids=("need_a",), relevance=RelevanceLevel.HIGH, suffix="a", title="Relevant update"):
    return ObservedSourceEvidence(
        observed_evidence_id=f"observed_evidence_{plan.candidate_source_id}_{suffix}",
        observation_plan_id=plan.source_observation_plan_id,
        candidate_source_id=plan.candidate_source_id,
        item_url=f"https://example.com/{plan.candidate_source_id}/{suffix}",
        item_title=title,
        publication_date_hint="2026-08-01",
        content_type_hint="article",
        relevant_information_need_ids=tuple(need_ids),
        signal_relevance=relevance,
        observation_provenance={"bounded": True},
    )


def make_observation(plan, evidence, *, sampled=2, relevant=None, failures=(), status=ObservationStatus.COMPLETED):
    relevant_count = len(evidence) if relevant is None else relevant
    hit_count = {}
    for item in evidence:
        for need_id in item.relevant_information_need_ids:
            hit_count[need_id] = hit_count.get(need_id, 0) + 1
    return SourceObservationResult(
        source_observation_result_id=f"observation_result_{plan.candidate_source_id}",
        source_observation_plan_id=plan.source_observation_plan_id,
        observation_status=status,
        sampled_item_count=sampled,
        recent_item_count=sampled,
        relevant_item_count=relevant_count,
        information_need_hit_count=hit_count,
        observed_date_span_start="2026-08-01",
        observed_date_span_end="2026-08-02",
        observed_evidence_ids=tuple(item.observed_evidence_id for item in evidence),
        failures=tuple(failures),
        diagnostics=(),
        observation_policy_version="phase5e_test",
    )


def make_potential(observation, *, level=ObservedSignalPotentialLevel.MEDIUM, relevant=None, evidence=(), limitations=()):
    relevant_count = observation.relevant_item_count if relevant is None else relevant
    return ObservedSignalPotential(
        observed_signal_potential_id=f"signal_{observation.source_observation_result_id}",
        source_observation_result_id=observation.source_observation_result_id,
        level=level,
        sampled_item_count=observation.sampled_item_count,
        relevant_item_count=relevant_count,
        information_need_hit_count=dict(observation.information_need_hit_count),
        supporting_observed_evidence_ids=tuple(item.observed_evidence_id for item in evidence),
        rationale="bounded item evidence",
        limitations=tuple(limitations),
        supporting_metrics={"sampled_item_count": observation.sampled_item_count},
    )


def evaluate_case(
    *,
    cid="candidate_a",
    decision=InitialEvaluationDecision.PROCEED_TO_OBSERVATION,
    entity_status=EntityMatchStatus.CONFIRMED,
    officiality=OfficialityStatus.OFFICIAL,
    page_type=PageType.LISTING_PAGE,
    durability=SurfaceDurabilityStatus.DURABLE_SURFACE,
    role_match=SourceRoleMatchStatus.MATCH,
    relevance=RelevanceLevel.MEDIUM,
    confidence=EvaluationConfidence.MEDIUM,
    source_value=SourceValueLevel.MEDIUM,
    observed=True,
    eligibility_status="primary_observation",
    potential_level=ObservedSignalPotentialLevel.MEDIUM,
    sampled=2,
    relevant_items=2,
    evidence_needs=("need_a",),
    failures=(),
    limitations=(),
    evidence=True,
    phase5d_hash="phase5d_hash",
    phase5e_hash="phase5e_hash",
    rationale="bounded evidence",
):
    initial = make_initial(
        cid=cid,
        decision=decision,
        entity_status=entity_status,
        officiality=officiality,
        page_type=page_type,
        durability=durability,
        role_match=role_match,
        relevance=relevance,
        confidence=confidence,
        source_value=source_value,
        rationale=rationale,
    )
    eligibility = {
        "candidate_source_id": cid,
        "initial_source_evaluation_id": initial.initial_source_evaluation_id,
        "status": eligibility_status,
        "blocking_reasons": (),
    }
    plans = ()
    observations = ()
    evidence_items = ()
    potentials = ()
    if observed:
        plan = make_plan(initial)
        evidence_items = (
            tuple(
                make_evidence(plan, need_ids=evidence_needs, suffix=str(index), title="有关更新")
                for index in range(max(1, relevant_items))
            )
            if evidence
            else ()
        )
        observation = make_observation(plan, evidence_items, sampled=sampled, relevant=relevant_items, failures=failures)
        potential = make_potential(
            observation,
            level=potential_level,
            relevant=relevant_items,
            evidence=evidence_items,
            limitations=limitations,
        )
        plans = (plan,)
        observations = (observation,)
        potentials = (potential,)
    inputs = FinalEvaluationInputs(
        initial_evaluations=(initial,),
        observation_eligibility_records=(eligibility,),
        observation_plans=plans,
        observation_results=observations,
        observed_source_evidence=evidence_items,
        observed_signal_potentials=potentials,
        phase5d_input_hash=phase5d_hash,
        phase5e_output_hash=phase5e_hash,
    )
    result = FinalSourceEvaluator().evaluate(inputs)
    return result, result.final_evaluations[0], result.composition_traces[0]


def evaluate_many(*configs):
    initials = []
    eligibilities = []
    plans = []
    observations = []
    evidence_items = []
    potentials = []
    for index, config in enumerate(configs):
        cid = config.get("cid", f"candidate_{index}")
        result, final, trace = evaluate_case(**{**config, "cid": cid})
        _ = final, trace
        initial = make_initial(
            cid=cid,
            decision=config.get("decision", InitialEvaluationDecision.PROCEED_TO_OBSERVATION),
            entity_status=config.get("entity_status", EntityMatchStatus.CONFIRMED),
            officiality=config.get("officiality", OfficialityStatus.OFFICIAL),
            page_type=config.get("page_type", PageType.LISTING_PAGE),
            durability=config.get("durability", SurfaceDurabilityStatus.DURABLE_SURFACE),
            role_match=config.get("role_match", SourceRoleMatchStatus.MATCH),
            relevance=config.get("relevance", RelevanceLevel.MEDIUM),
        )
        initials.append(initial)
        eligibilities.append(
            {
                "candidate_source_id": cid,
                "initial_source_evaluation_id": initial.initial_source_evaluation_id,
                "status": config.get("eligibility_status", "primary_observation"),
                "blocking_reasons": (),
            }
        )
        if config.get("observed", True):
            plan = make_plan(initial)
            evidence = tuple(
                make_evidence(plan, suffix=str(item), title="Relevant update")
                for item in range(config.get("relevant_items", 2))
            )
            observation = make_observation(plan, evidence, relevant=config.get("relevant_items", 2))
            potential = make_potential(observation, level=config.get("potential_level", ObservedSignalPotentialLevel.MEDIUM), evidence=evidence)
            plans.append(plan)
            observations.append(observation)
            evidence_items.extend(evidence)
            potentials.append(potential)
    return FinalSourceEvaluator().evaluate(
        FinalEvaluationInputs(
            initial_evaluations=tuple(initials),
            observation_eligibility_records=tuple(eligibilities),
            observation_plans=tuple(plans),
            observation_results=tuple(observations),
            observed_source_evidence=tuple(evidence_items),
            observed_signal_potentials=tuple(potentials),
            phase5d_input_hash="phase5d",
            phase5e_output_hash="phase5e",
        )
    )


class Phase5FFinalSourceEvaluatorTests(unittest.TestCase):
    def test_policy_matrix_categorical_composition(self):
        cases = [
            (
                "strong_suitable_positive_supportive",
                {},
                FinalEvaluationDecision.APPROVED_FOR_ACQUISITION,
            ),
            (
                "uncertain_identity_medium_support_still_review",
                {"entity_status": EntityMatchStatus.UNCERTAIN},
                FinalEvaluationDecision.NEEDS_REVIEW,
            ),
            (
                "blocked_identity_high_support_rejected",
                {"entity_status": EntityMatchStatus.MISMATCH, "potential_level": ObservedSignalPotentialLevel.HIGH},
                FinalEvaluationDecision.REJECTED,
            ),
            (
                "uncertain_surface_supportive_review",
                {"durability": SurfaceDurabilityStatus.UNCERTAIN},
                FinalEvaluationDecision.NEEDS_REVIEW,
            ),
            (
                "unsuitable_surface_detail_rejected",
                {"page_type": PageType.ARTICLE_DETAIL, "durability": SurfaceDurabilityStatus.ONE_OFF_CONTENT},
                FinalEvaluationDecision.REJECTED,
            ),
            (
                "negative_info_low_observation_rejected",
                {"relevance": RelevanceLevel.LOW, "potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0},
                FinalEvaluationDecision.REJECTED,
            ),
            (
                "positive_info_low_observation_conflict_review",
                {"relevance": RelevanceLevel.HIGH, "potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0},
                FinalEvaluationDecision.NEEDS_REVIEW,
            ),
            (
                "supportive_prior_absent_observation_review",
                {"observed": False},
                FinalEvaluationDecision.NEEDS_REVIEW,
            ),
        ]
        for name, config, expected in cases:
            with self.subTest(name=name):
                _, final, trace = evaluate_case(cid=f"matrix_{name}", **config)
                self.assertEqual(final.final_decision, expected)
                self.assertNotIn("score", " ".join(trace.final_decision_reason_codes))


CASES = [
    ("all_current_candidates_get_exactly_one_final", [{}, {"cid": "candidate_b", "observed": False}, {"cid": "candidate_c", "entity_status": EntityMatchStatus.MISMATCH}], None, {"count": 3}),
    ("candidate_ids_are_unique", [{}, {"cid": "candidate_b", "observed": False}], None, {"unique": True}),
    ("medium_signal_does_not_auto_approve", {"entity_status": EntityMatchStatus.UNCERTAIN}, FinalEvaluationDecision.NEEDS_REVIEW, {}),
    ("low_signal_does_not_auto_reject", {"relevance": RelevanceLevel.HIGH, "potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0}, FinalEvaluationDecision.NEEDS_REVIEW, {"reason": "phase5d_positive_relevance_vs_low_observation"}),
    ("officiality_alone_does_not_auto_approve", {"observed": False}, FinalEvaluationDecision.NEEDS_REVIEW, {"reason": "observation_insufficient"}),
    ("entity_mismatch_is_hard_blocker", {"entity_status": EntityMatchStatus.MISMATCH}, FinalEvaluationDecision.REJECTED, {"hard": "entity_mismatch"}),
    ("third_party_is_hard_blocker", {"officiality": OfficialityStatus.THIRD_PARTY}, FinalEvaluationDecision.REJECTED, {"hard": "third_party_source_for_entity_surface"}),
    ("one_off_detail_page_is_hard_blocker", {"page_type": PageType.ARTICLE_DETAIL, "durability": SurfaceDurabilityStatus.ONE_OFF_CONTENT}, FinalEvaluationDecision.REJECTED, {"hard": "one_off_detail_page"}),
    ("no_rss_is_not_a_hard_blocker", {"observed": False}, FinalEvaluationDecision.NEEDS_REVIEW, {"not_hard": "no_rss"}),
    ("low_confidence_alone_does_not_reject", {"confidence": EvaluationConfidence.LOW}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {}),
    ("no_observation_does_not_reject", {"observed": False}, FinalEvaluationDecision.NEEDS_REVIEW, {}),
    ("primary_supportive_source_approves", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"handoff": 1}),
    ("primary_low_conflicting_source_needs_review", {"relevance": RelevanceLevel.MEDIUM, "potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0}, FinalEvaluationDecision.NEEDS_REVIEW, {}),
    ("high_potential_cannot_override_identity_mismatch", {"entity_status": EntityMatchStatus.MISMATCH, "potential_level": ObservedSignalPotentialLevel.HIGH}, FinalEvaluationDecision.REJECTED, {}),
    ("review_resolution_medium_can_upgrade", {"decision": InitialEvaluationDecision.NEEDS_REVIEW, "eligibility_status": "review_resolution"}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"review_state": ReviewResolutionState.RESOLVED_POSITIVE}),
    ("review_resolution_medium_identity_uncertain_remains_review", {"decision": InitialEvaluationDecision.NEEDS_REVIEW, "eligibility_status": "review_resolution", "entity_status": EntityMatchStatus.UNCERTAIN}, FinalEvaluationDecision.NEEDS_REVIEW, {}),
    ("review_resolution_low_negative_can_reject", {"decision": InitialEvaluationDecision.NEEDS_REVIEW, "eligibility_status": "review_resolution", "relevance": RelevanceLevel.LOW, "potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0}, FinalEvaluationDecision.REJECTED, {"review_state": ReviewResolutionState.RESOLVED_NEGATIVE}),
    ("review_resolution_low_conflict_remains_review", {"decision": InitialEvaluationDecision.NEEDS_REVIEW, "eligibility_status": "review_resolution", "relevance": RelevanceLevel.MEDIUM, "potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0}, FinalEvaluationDecision.NEEDS_REVIEW, {}),
    ("phase5d_rejected_hard_blocker_stays_rejected", {"decision": InitialEvaluationDecision.REJECTED, "entity_status": EntityMatchStatus.MISMATCH, "observed": False}, FinalEvaluationDecision.REJECTED, {}),
    ("phase5d_rejected_without_hard_blocker_needs_review", {"decision": InitialEvaluationDecision.REJECTED, "observed": False}, FinalEvaluationDecision.NEEDS_REVIEW, {"reason": "phase5d_rejection_without_persistent_hard_blocker"}),
    ("phase5d_rejected_with_convergent_negative_rejects", {"decision": InitialEvaluationDecision.REJECTED, "relevance": RelevanceLevel.LOW, "potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0}, FinalEvaluationDecision.REJECTED, {}),
    ("insufficient_evidence_preserves_review", {"potential_level": ObservedSignalPotentialLevel.INSUFFICIENT_EVIDENCE, "relevant_items": 0}, FinalEvaluationDecision.NEEDS_REVIEW, {}),
    ("insufficient_evidence_with_hard_blocker_rejects", {"entity_status": EntityMatchStatus.MISMATCH, "potential_level": ObservedSignalPotentialLevel.INSUFFICIENT_EVIDENCE, "relevant_items": 0}, FinalEvaluationDecision.REJECTED, {}),
    ("incomplete_low_observation_does_not_reject", {"relevance": RelevanceLevel.LOW, "potential_level": ObservedSignalPotentialLevel.LOW, "sampled": 1, "relevant_items": 0}, FinalEvaluationDecision.NEEDS_REVIEW, {}),
    ("failed_low_observation_does_not_reject", {"relevance": RelevanceLevel.LOW, "potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0, "failures": ("timeout",)}, FinalEvaluationDecision.NEEDS_REVIEW, {}),
    ("source_role_mismatch_alone_does_not_reject", {"role_match": SourceRoleMatchStatus.MISMATCH}, FinalEvaluationDecision.NEEDS_REVIEW, {}),
    ("unsupported_need_ids_create_conflict", {"evidence_needs": ("need_x",)}, FinalEvaluationDecision.NEEDS_REVIEW, {"reason": "unsupported_information_need_id_in_observation"}),
    ("observed_need_support_stays_controlled_subset", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"supported_needs": ("need_a",)}),
    ("review_resolution_resolved_positive_state", {"decision": InitialEvaluationDecision.NEEDS_REVIEW, "eligibility_status": "review_resolution"}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"review_state": ReviewResolutionState.RESOLVED_POSITIVE}),
    ("review_resolution_resolved_negative_state", {"decision": InitialEvaluationDecision.NEEDS_REVIEW, "eligibility_status": "review_resolution", "relevance": RelevanceLevel.LOW, "potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0}, FinalEvaluationDecision.REJECTED, {"review_state": ReviewResolutionState.RESOLVED_NEGATIVE}),
    ("review_resolution_partially_resolved_state", {"decision": InitialEvaluationDecision.NEEDS_REVIEW, "eligibility_status": "review_resolution", "evidence": False}, FinalEvaluationDecision.NEEDS_REVIEW, {"review_state": ReviewResolutionState.PARTIALLY_RESOLVED}),
    ("review_resolution_unresolved_state", {"decision": InitialEvaluationDecision.NEEDS_REVIEW, "eligibility_status": "review_resolution", "observed": False}, FinalEvaluationDecision.NEEDS_REVIEW, {"review_state": ReviewResolutionState.UNRESOLVED}),
    ("evidence_conflict_lowers_confidence", {"relevance": RelevanceLevel.HIGH, "potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0}, FinalEvaluationDecision.NEEDS_REVIEW, {"confidence": EvaluationConfidence.LOW}),
    ("positive_evidence_convergence_approves", {"potential_level": ObservedSignalPotentialLevel.HIGH}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"positive_min": 5}),
    ("negative_evidence_convergence_rejects", {"relevance": RelevanceLevel.LOW, "potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0}, FinalEvaluationDecision.REJECTED, {}),
    ("observation_absence_reason_required", {"observed": False, "eligibility_status": "not_observation_eligible"}, FinalEvaluationDecision.NEEDS_REVIEW, {"reason": "not_observation_eligible:"}),
    ("rejected_sources_not_in_handoff", {"entity_status": EntityMatchStatus.MISMATCH}, FinalEvaluationDecision.REJECTED, {"handoff": 0}),
    ("needs_review_sources_not_in_handoff", {"observed": False}, FinalEvaluationDecision.NEEDS_REVIEW, {"handoff": 0}),
    ("approved_sources_enter_handoff", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"handoff": 1}),
    ("phase6_handoff_has_no_acquisition_method", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"no_acquisition_method": True}),
    ("feed_link_hint_remains_unverified", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"source_absent": "FeedLinkHint"}),
    ("final_output_has_no_raw_html", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"not_text": "<html"}),
    ("final_output_has_no_raw_deepseek_response", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"not_text": "raw_deepseek"}),
    ("no_external_client_calls_exist", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"no_external_clients": True}),
    ("deterministic_final_rationale", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"stable_rationale": True}),
    ("stable_reason_code_ordering", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"reason_sorted": True}),
    ("stable_final_fingerprint", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"stable_fingerprint": True}),
    ("changed_phase5d_hash_invalidates_final_fingerprint", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"phase5d_hash_changes": True}),
    ("changed_phase5e_hash_invalidates_final_fingerprint", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"phase5e_hash_changes": True}),
    ("unchanged_input_yields_identical_output", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"stable_output_hash": True}),
    ("source_value_separate_from_confidence", {"potential_level": ObservedSignalPotentialLevel.LOW, "relevant_items": 0, "source_value": SourceValueLevel.HIGH}, FinalEvaluationDecision.NEEDS_REVIEW, {"source_confidence_separate": True}),
    ("confidence_separate_from_decision", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"decision_confidence_separate": True}),
    ("unicode_source_evidence_treated_identically", {"cid": "候选来源", "rationale": "中文证据"}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {}),
    ("canonical_output_records_deterministic", {}, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, {"stable_output_hash": True}),
    ("needs_review_backlog_complete", {"observed": False}, FinalEvaluationDecision.NEEDS_REVIEW, {"backlog": 1}),
    ("rejected_provenance_complete", {"entity_status": EntityMatchStatus.MISMATCH}, FinalEvaluationDecision.REJECTED, {"rejected": 1}),
    ("phase6_handoff_only_contains_approved_sources", [{}, {"cid": "candidate_b", "observed": False}, {"cid": "candidate_c", "entity_status": EntityMatchStatus.MISMATCH}], None, {"handoff_only_approved": True}),
]


def add_generated_tests():
    def make_test(case_name, config, expected, checks):
        def test(self):
            if isinstance(config, list):
                result = evaluate_many(*config)
                if checks.get("count") is not None:
                    self.assertEqual(len(result.final_evaluations), checks["count"])
                if checks.get("unique"):
                    self.assertEqual(
                        len({item.candidate_source_id for item in result.final_evaluations}),
                        len(result.final_evaluations),
                    )
                if checks.get("handoff_only_approved"):
                    approved = {
                        item.candidate_source_id
                        for item in result.final_evaluations
                        if item.final_decision == FinalEvaluationDecision.APPROVED_FOR_ACQUISITION
                    }
                    self.assertTrue(result.phase6_handoff)
                    self.assertTrue(all(item.candidate_source_id in approved for item in result.phase6_handoff))
                return

            result, final, trace = evaluate_case(**config)
            self.assertEqual(final.final_decision, expected)
            if checks.get("reason"):
                self.assertIn(checks["reason"], " ".join(trace.final_decision_reason_codes))
            if checks.get("hard"):
                self.assertIn(checks["hard"], trace.hard_blockers)
            if checks.get("not_hard"):
                self.assertNotIn(checks["not_hard"], trace.hard_blockers)
            if checks.get("review_state"):
                self.assertEqual(trace.review_resolution_state, checks["review_state"])
            if checks.get("confidence"):
                self.assertEqual(final.evaluation_confidence, checks["confidence"])
            if checks.get("handoff") is not None:
                self.assertEqual(len(result.phase6_handoff), checks["handoff"])
            if checks.get("backlog") is not None:
                self.assertEqual(len(result.needs_review_backlog), checks["backlog"])
            if checks.get("rejected") is not None:
                self.assertEqual(len(result.rejected_source_provenance), checks["rejected"])
            if checks.get("supported_needs"):
                self.assertEqual(result.phase6_handoff[0].supported_information_need_ids, checks["supported_needs"])
            if checks.get("positive_min"):
                self.assertGreaterEqual(len(trace.positive_evidence), checks["positive_min"])
            if checks.get("no_acquisition_method"):
                self.assertNotIn("acquisition_method", result.phase6_handoff[0].to_dict())
            if checks.get("source_absent"):
                self.assertNotIn(checks["source_absent"], inspect.getsource(source_final_evaluator))
            if checks.get("not_text"):
                self.assertNotIn(checks["not_text"], str(final.to_dict()).casefold())
            if checks.get("no_external_clients"):
                module_source = inspect.getsource(source_final_evaluator)
                for forbidden in ("SourceFetcher", "DeepSeek", "Brave", "requests", "httpx", "browser"):
                    self.assertNotIn(forbidden, module_source)
            if checks.get("stable_rationale"):
                _, again, _ = evaluate_case(**config)
                self.assertEqual(final.final_rationale, again.final_rationale)
            if checks.get("reason_sorted"):
                self.assertEqual(tuple(sorted(trace.final_decision_reason_codes)), trace.final_decision_reason_codes)
            if checks.get("stable_fingerprint"):
                _, again, _ = evaluate_case(**config)
                self.assertEqual(final.input_fingerprint, again.input_fingerprint)
            if checks.get("phase5d_hash_changes"):
                _, changed, _ = evaluate_case(**{**config, "phase5d_hash": "changed"})
                self.assertNotEqual(final.input_fingerprint, changed.input_fingerprint)
            if checks.get("phase5e_hash_changes"):
                _, changed, _ = evaluate_case(**{**config, "phase5e_hash": "changed"})
                self.assertNotEqual(final.input_fingerprint, changed.input_fingerprint)
            if checks.get("stable_output_hash"):
                again, _, _ = evaluate_case(**config)
                self.assertEqual(result.output_hash, again.output_hash)
            if checks.get("source_confidence_separate"):
                self.assertNotEqual(final.source_value.value, final.evaluation_confidence.value)
            if checks.get("decision_confidence_separate"):
                self.assertEqual(final.final_decision, FinalEvaluationDecision.APPROVED_FOR_ACQUISITION)
                self.assertEqual(final.evaluation_confidence, EvaluationConfidence.MEDIUM)

        test.__name__ = f"test_{case_name}"
        return test

    for index, (name, config, expected, checks) in enumerate(CASES, start=1):
        setattr(Phase5FFinalSourceEvaluatorTests, f"test_{index:02d}_{name}", make_test(name, config, expected, checks))


add_generated_tests()
