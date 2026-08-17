from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any

from src.config import PROJECT_ROOT
from src.database.planning_identity import hash_canonical_value
from src.source_monitoring.source_evaluation_identity import (
    build_final_source_evaluation_id,
)
from src.source_monitoring.source_evaluation_models import (
    EntityMatchStatus,
    EvaluationConfidence,
    FinalEvaluationDecision,
    FinalSourceEvaluation,
    InitialEvaluationDecision,
    InitialSourceEvaluation,
    ObservedSignalPotential,
    ObservedSignalPotentialLevel,
    ObservedSourceEvidence,
    OfficialityStatus,
    PageType,
    RelevanceLevel,
    SourceObservationPlan,
    SourceObservationResult,
    SourceRoleMatchStatus,
    SourceValueLevel,
    SurfaceDurabilityStatus,
)


FINAL_SOURCE_EVALUATION_POLICY_VERSION = "final_source_evaluation_policy_v1"
FINAL_EVIDENCE_COMPOSITION_TRACE_SCHEMA_VERSION = "final_evidence_composition_trace_v1"
PHASE5_CANONICAL_RESULT_SCHEMA_VERSION = "phase5_canonical_source_evaluations_v1"
PHASE5_FINAL_ARTIFACT_ROOT = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase5_source_evaluation"
)
FINAL_SOURCE_EVALUATIONS_FILE = PHASE5_FINAL_ARTIFACT_ROOT / "final_source_evaluations.json"
CANONICAL_SOURCE_EVALUATIONS_FILE = (
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "source_evaluations.json"
)
PHASE6_HANDOFF_FILE = PHASE5_FINAL_ARTIFACT_ROOT / "phase6_source_handoff.json"


class FinalEvidenceState(str, Enum):
    STRONG = "strong"
    SUFFICIENT = "sufficient"
    SUPPORTIVE = "supportive"
    UNCERTAIN = "uncertain"
    WEAK = "weak"
    BLOCKED = "blocked"
    ABSENT = "absent"
    CONFLICTING = "conflicting"


class ReviewResolutionState(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    RESOLVED_POSITIVE = "resolved_positive"
    RESOLVED_NEGATIVE = "resolved_negative"
    PARTIALLY_RESOLVED = "partially_resolved"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class FinalEvidenceCompositionTrace:
    composition_trace_id: str
    candidate_source_id: str
    initial_source_evaluation_id: str
    observation_result_id: str | None
    observed_signal_potential_id: str | None
    identity_foundation_state: FinalEvidenceState
    surface_suitability_state: FinalEvidenceState
    information_fit_state: FinalEvidenceState
    observation_evidence_state: FinalEvidenceState
    evidence_quality_state: FinalEvidenceState
    review_resolution_state: ReviewResolutionState
    hard_blockers: tuple[str, ...]
    positive_evidence: tuple[str, ...]
    counter_evidence: tuple[str, ...]
    unresolved_uncertainties: tuple[str, ...]
    evidence_conflicts: tuple[str, ...]
    final_decision_reason_codes: tuple[str, ...]
    policy_version: str
    input_fingerprint: str
    schema_version: str = FINAL_EVIDENCE_COMPOSITION_TRACE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class Phase6SourceHandoff:
    candidate_source_id: str
    final_source_evaluation_id: str
    entity_id: str
    observed_source_role: str
    supported_information_need_ids: tuple[str, ...]
    source_value: SourceValueLevel
    evaluation_confidence: EvaluationConfidence
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class FinalSourceEvaluationBatchResult:
    final_evaluations: tuple[FinalSourceEvaluation, ...]
    composition_traces: tuple[FinalEvidenceCompositionTrace, ...]
    phase6_handoff: tuple[Phase6SourceHandoff, ...]
    needs_review_backlog: tuple[dict[str, Any], ...]
    rejected_source_provenance: tuple[dict[str, Any], ...]
    reconciliation_rows: tuple[dict[str, Any], ...]
    diagnostics: tuple[str, ...]
    input_fingerprint: str
    output_hash: str
    policy_version: str = FINAL_SOURCE_EVALUATION_POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(self)


@dataclass(frozen=True)
class FinalEvaluationInputs:
    initial_evaluations: tuple[InitialSourceEvaluation, ...]
    observation_eligibility_records: tuple[dict[str, Any], ...]
    observation_plans: tuple[SourceObservationPlan, ...]
    observation_results: tuple[SourceObservationResult, ...]
    observed_source_evidence: tuple[ObservedSourceEvidence, ...]
    observed_signal_potentials: tuple[ObservedSignalPotential, ...]
    phase5d_input_hash: str
    phase5e_output_hash: str


class FinalSourceEvaluator:
    def evaluate(self, inputs: FinalEvaluationInputs) -> FinalSourceEvaluationBatchResult:
        eligibility_by_candidate = {
            str(item.get("candidate_source_id")): item for item in inputs.observation_eligibility_records
        }
        plan_by_id = {item.source_observation_plan_id: item for item in inputs.observation_plans}
        result_by_candidate: dict[str, SourceObservationResult] = {}
        for observation in inputs.observation_results:
            plan = plan_by_id.get(observation.source_observation_plan_id)
            if plan is not None:
                result_by_candidate[plan.candidate_source_id] = observation
        potential_by_result = {
            item.source_observation_result_id: item for item in inputs.observed_signal_potentials
        }
        evidence_by_result: dict[str, tuple[ObservedSourceEvidence, ...]] = {}
        evidence_by_plan: dict[str, list[ObservedSourceEvidence]] = {}
        for evidence in inputs.observed_source_evidence:
            evidence_by_plan.setdefault(evidence.observation_plan_id, []).append(evidence)
        for result in inputs.observation_results:
            evidence_by_result[result.source_observation_result_id] = tuple(
                sorted(
                    evidence_by_plan.get(result.source_observation_plan_id, []),
                    key=lambda item: item.observed_evidence_id,
                )
            )

        finals: list[FinalSourceEvaluation] = []
        traces: list[FinalEvidenceCompositionTrace] = []
        reconciliation: list[dict[str, Any]] = []
        diagnostics: list[str] = []

        for initial in sorted(inputs.initial_evaluations, key=lambda item: item.candidate_source_id):
            observation = result_by_candidate.get(initial.candidate_source_id)
            potential = (
                potential_by_result.get(observation.source_observation_result_id)
                if observation is not None
                else _absent_signal_potential(initial)
            )
            eligibility = eligibility_by_candidate.get(initial.candidate_source_id)
            evidence = (
                evidence_by_result.get(observation.source_observation_result_id, ())
                if observation is not None
                else ()
            )
            trace = compose_final_trace(
                initial=initial,
                eligibility=eligibility,
                observation=observation,
                potential=potential,
                evidence=evidence,
                phase5d_input_hash=inputs.phase5d_input_hash,
                phase5e_output_hash=inputs.phase5e_output_hash,
            )
            final = build_final_source_evaluation(initial=initial, observation=observation, potential=potential, trace=trace)
            traces.append(trace)
            finals.append(final)
            reconciliation.append(
                {
                    "candidate_source_id": initial.candidate_source_id,
                    "entity_id": initial.entity_id,
                    "initial_source_evaluation_id": initial.initial_source_evaluation_id,
                    "phase5d_decision": initial.decision.value,
                    "observation_eligibility": str(eligibility.get("status")) if eligibility else "absent",
                    "observation_result_id": observation.source_observation_result_id if observation else None,
                    "observed_signal_potential": potential.level.value,
                    "observation_absence_reason": None if observation else _observation_absence_reason(initial, eligibility),
                    "final_source_evaluation_id": final.final_source_evaluation_id,
                    "final_decision": final.final_decision.value,
                }
            )

        if len({item.candidate_source_id for item in finals}) != len(inputs.initial_evaluations):
            diagnostics.append("final_evaluation_candidate_count_mismatch")

        handoff = tuple(
            Phase6SourceHandoff(
                candidate_source_id=item.candidate_source_id,
                final_source_evaluation_id=item.final_source_evaluation_id,
                entity_id=item.entity_id,
                observed_source_role=next(
                    initial.source_role_assessment.observed_source_role.value
                    for initial in inputs.initial_evaluations
                    if initial.candidate_source_id == item.candidate_source_id
                ),
                supported_information_need_ids=tuple(sorted(item.observed_signal_potential.information_need_hit_count)),
                source_value=item.source_value,
                evaluation_confidence=item.evaluation_confidence,
                reason_codes=next(
                    trace.final_decision_reason_codes
                    for trace in traces
                    if trace.candidate_source_id == item.candidate_source_id
                ),
            )
            for item in finals
            if item.final_decision == FinalEvaluationDecision.APPROVED_FOR_ACQUISITION
        )
        backlog = tuple(
            _backlog_record(final, traces)
            for final in finals
            if final.final_decision == FinalEvaluationDecision.NEEDS_REVIEW
        )
        rejected = tuple(
            _rejected_record(final, traces)
            for final in finals
            if final.final_decision == FinalEvaluationDecision.REJECTED
        )
        input_fingerprint = hash_canonical_value(
            {
                "policy_version": FINAL_SOURCE_EVALUATION_POLICY_VERSION,
                "phase5d_input_hash": inputs.phase5d_input_hash,
                "phase5e_output_hash": inputs.phase5e_output_hash,
                "initial_ids": [item.initial_source_evaluation_id for item in inputs.initial_evaluations],
                "observation_result_ids": [item.source_observation_result_id for item in inputs.observation_results],
            }
        )
        payload = {
            "final_evaluations": [item.to_dict() for item in finals],
            "composition_traces": [item.to_dict() for item in traces],
            "phase6_handoff": [item.to_dict() for item in handoff],
            "needs_review_backlog": list(backlog),
            "rejected_source_provenance": list(rejected),
            "reconciliation_rows": reconciliation,
            "diagnostics": diagnostics,
            "input_fingerprint": input_fingerprint,
            "policy_version": FINAL_SOURCE_EVALUATION_POLICY_VERSION,
        }
        return FinalSourceEvaluationBatchResult(
            final_evaluations=tuple(finals),
            composition_traces=tuple(traces),
            phase6_handoff=handoff,
            needs_review_backlog=backlog,
            rejected_source_provenance=rejected,
            reconciliation_rows=tuple(reconciliation),
            diagnostics=tuple(diagnostics),
            input_fingerprint=input_fingerprint,
            output_hash=hash_canonical_value(payload),
        )


def compose_final_trace(
    *,
    initial: InitialSourceEvaluation,
    eligibility: dict[str, Any] | None,
    observation: SourceObservationResult | None,
    potential: ObservedSignalPotential,
    evidence: tuple[ObservedSourceEvidence, ...],
    phase5d_input_hash: str,
    phase5e_output_hash: str,
) -> FinalEvidenceCompositionTrace:
    identity_state, identity_pos, identity_counter, identity_uncertain, identity_blockers = _identity_dimension(initial)
    surface_state, surface_pos, surface_counter, surface_uncertain, surface_blockers = _surface_dimension(initial, observation)
    info_state, info_pos, info_counter, info_uncertain = _information_dimension(initial, potential)
    obs_state, obs_pos, obs_counter, obs_uncertain, obs_conflicts = _observation_dimension(initial, observation, potential, evidence)
    quality_state, quality_pos, quality_counter, quality_uncertain = _quality_dimension(initial, observation, potential)
    review_state = _review_resolution_state(initial, eligibility, observation, potential, evidence, info_state, obs_state)

    hard_blockers = _stable_tuple([*identity_blockers, *surface_blockers])
    positive = _stable_tuple([*identity_pos, *surface_pos, *info_pos, *obs_pos, *quality_pos])
    counter = _stable_tuple([*identity_counter, *surface_counter, *info_counter, *obs_counter, *quality_counter])
    unresolved = _stable_tuple([*identity_uncertain, *surface_uncertain, *info_uncertain, *obs_uncertain, *quality_uncertain])
    conflicts = _stable_tuple(obs_conflicts)
    decision, reason_codes = decide_final(
        initial=initial,
        eligibility=eligibility,
        potential=potential,
        observation=observation,
        hard_blockers=hard_blockers,
        unresolved_uncertainties=unresolved,
        evidence_conflicts=conflicts,
        identity_state=identity_state,
        surface_state=surface_state,
        info_state=info_state,
        obs_state=obs_state,
        quality_state=quality_state,
        review_state=review_state,
    )
    _ = decision
    fingerprint = hash_canonical_value(
        {
            "policy_version": FINAL_SOURCE_EVALUATION_POLICY_VERSION,
            "candidate_source_id": initial.candidate_source_id,
            "initial_source_evaluation_id": initial.initial_source_evaluation_id,
            "observation_result_id": observation.source_observation_result_id if observation else None,
            "observed_signal_potential_id": potential.observed_signal_potential_id,
            "states": [identity_state.value, surface_state.value, info_state.value, obs_state.value, quality_state.value, review_state.value],
            "hard_blockers": hard_blockers,
            "positive_evidence": positive,
            "counter_evidence": counter,
            "unresolved_uncertainties": unresolved,
            "evidence_conflicts": conflicts,
            "reason_codes": reason_codes,
            "phase5d_input_hash": phase5d_input_hash,
            "phase5e_output_hash": phase5e_output_hash,
        }
    )
    return FinalEvidenceCompositionTrace(
        composition_trace_id=f"final_evidence_trace_{fingerprint[:16]}",
        candidate_source_id=initial.candidate_source_id,
        initial_source_evaluation_id=initial.initial_source_evaluation_id,
        observation_result_id=observation.source_observation_result_id if observation else None,
        observed_signal_potential_id=potential.observed_signal_potential_id,
        identity_foundation_state=identity_state,
        surface_suitability_state=surface_state,
        information_fit_state=info_state,
        observation_evidence_state=obs_state,
        evidence_quality_state=quality_state,
        review_resolution_state=review_state,
        hard_blockers=hard_blockers,
        positive_evidence=positive,
        counter_evidence=counter,
        unresolved_uncertainties=unresolved,
        evidence_conflicts=conflicts,
        final_decision_reason_codes=reason_codes,
        policy_version=FINAL_SOURCE_EVALUATION_POLICY_VERSION,
        input_fingerprint=fingerprint,
    )


def decide_final(
    *,
    initial: InitialSourceEvaluation,
    eligibility: dict[str, Any] | None,
    potential: ObservedSignalPotential,
    observation: SourceObservationResult | None,
    hard_blockers: tuple[str, ...],
    unresolved_uncertainties: tuple[str, ...],
    evidence_conflicts: tuple[str, ...],
    identity_state: FinalEvidenceState,
    surface_state: FinalEvidenceState,
    info_state: FinalEvidenceState,
    obs_state: FinalEvidenceState,
    quality_state: FinalEvidenceState,
    review_state: ReviewResolutionState,
) -> tuple[FinalEvaluationDecision, tuple[str, ...]]:
    reasons: list[str] = []
    if hard_blockers:
        reasons.extend(f"hard_blocker:{item}" for item in hard_blockers)
        return FinalEvaluationDecision.REJECTED, _stable_tuple(reasons)

    observation_complete = observation is not None and observation.sampled_item_count >= 2 and not observation.failures
    supportive_observation = potential.level in {
        ObservedSignalPotentialLevel.HIGH,
        ObservedSignalPotentialLevel.MEDIUM,
    } and potential.relevant_item_count > 0
    low_complete_observation = observation_complete and potential.level == ObservedSignalPotentialLevel.LOW
    foundational = identity_state in {FinalEvidenceState.STRONG, FinalEvidenceState.SUFFICIENT} and surface_state in {
        FinalEvidenceState.STRONG,
        FinalEvidenceState.SUFFICIENT,
        FinalEvidenceState.SUPPORTIVE,
    }
    meaningful_info = info_state in {FinalEvidenceState.STRONG, FinalEvidenceState.SUPPORTIVE}
    quality_ok = quality_state in {FinalEvidenceState.STRONG, FinalEvidenceState.SUFFICIENT, FinalEvidenceState.SUPPORTIVE}
    material_uncertainties = tuple(
        item
        for item in unresolved_uncertainties
        if item
        not in {
            "phase5d_low_confidence",
            "observation_absent",
        }
    )

    if (
        foundational
        and meaningful_info
        and supportive_observation
        and quality_ok
        and not material_uncertainties
        and not evidence_conflicts
        and (
            initial.decision == InitialEvaluationDecision.PROCEED_TO_OBSERVATION
            or review_state == ReviewResolutionState.RESOLVED_POSITIVE
        )
    ):
        reasons.extend(["foundational_evidence_sufficient", "observation_supportive", "information_fit_meaningful"])
        return FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, _stable_tuple(reasons)

    if (
        initial.information_need_relevance_assessment.relevance_level == RelevanceLevel.LOW
        and low_complete_observation
        and potential.relevant_item_count == 0
        and identity_state != FinalEvidenceState.UNCERTAIN
    ):
        reasons.extend(["convergent_negative_information_fit", "complete_low_observation"])
        return FinalEvaluationDecision.REJECTED, _stable_tuple(reasons)

    if initial.decision == InitialEvaluationDecision.REJECTED:
        reasons.append("phase5d_rejection_without_persistent_hard_blocker")
        return FinalEvaluationDecision.NEEDS_REVIEW, _stable_tuple(reasons)

    if evidence_conflicts:
        reasons.extend(["evidence_conflict_requires_review", *evidence_conflicts])
    if material_uncertainties:
        reasons.extend(["unresolved_material_uncertainty", *material_uncertainties])
    if potential.level == ObservedSignalPotentialLevel.INSUFFICIENT_EVIDENCE:
        reasons.append("observation_insufficient")
    if observation is None:
        reasons.append(_observation_absence_reason(initial, eligibility))
    if initial.decision == InitialEvaluationDecision.NEEDS_REVIEW:
        reasons.append("phase5d_needs_review_preserved")
    if not reasons:
        reasons.append("evidence_not_sufficient_for_approval_or_rejection")
    return FinalEvaluationDecision.NEEDS_REVIEW, _stable_tuple(reasons)


def build_final_source_evaluation(
    *,
    initial: InitialSourceEvaluation,
    observation: SourceObservationResult | None,
    potential: ObservedSignalPotential,
    trace: FinalEvidenceCompositionTrace,
) -> FinalSourceEvaluation:
    decision, _ = decide_final(
        initial=initial,
        eligibility=None,
        potential=potential,
        observation=observation,
        hard_blockers=trace.hard_blockers,
        unresolved_uncertainties=trace.unresolved_uncertainties,
        evidence_conflicts=trace.evidence_conflicts,
        identity_state=trace.identity_foundation_state,
        surface_state=trace.surface_suitability_state,
        info_state=trace.information_fit_state,
        obs_state=trace.observation_evidence_state,
        quality_state=trace.evidence_quality_state,
        review_state=trace.review_resolution_state,
    )
    source_value = _final_source_value(initial, decision, potential)
    confidence = _final_confidence(initial, decision, trace, potential)
    fingerprint = hash_canonical_value(
        {
            "policy_version": FINAL_SOURCE_EVALUATION_POLICY_VERSION,
            "initial_source_evaluation_id": initial.initial_source_evaluation_id,
            "observation_result_id": observation.source_observation_result_id if observation else None,
            "candidate_source_id": initial.candidate_source_id,
            "entity_id": initial.entity_id,
            "source_value": source_value.value,
            "confidence": confidence.value,
            "observed_signal_potential": potential.to_dict(),
            "trace_id": trace.composition_trace_id,
            "reason_codes": trace.final_decision_reason_codes,
            "decision": decision.value,
        }
    )
    return FinalSourceEvaluation(
        final_source_evaluation_id=build_final_source_evaluation_id(
            initial_source_evaluation_id=initial.initial_source_evaluation_id,
            candidate_source_id=initial.candidate_source_id,
            observation_result_id=observation.source_observation_result_id if observation else None,
            input_fingerprint=fingerprint,
        ),
        initial_source_evaluation_id=initial.initial_source_evaluation_id,
        observation_result_id=observation.source_observation_result_id if observation else None,
        candidate_source_id=initial.candidate_source_id,
        entity_id=initial.entity_id,
        source_value=source_value,
        evaluation_confidence=confidence,
        observed_signal_potential=potential,
        final_rationale=_final_rationale(decision, trace.final_decision_reason_codes),
        review_flags=trace.unresolved_uncertainties + trace.evidence_conflicts,
        final_decision=decision,
        policy_version=FINAL_SOURCE_EVALUATION_POLICY_VERSION,
        input_fingerprint=fingerprint,
    )


def persist_final_source_evaluations(*, result: FinalSourceEvaluationBatchResult, output_file: Path = FINAL_SOURCE_EVALUATIONS_FILE) -> Path:
    payload = result.to_dict()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if output_file.exists() and output_file.read_text(encoding="utf-8") == text:
        return output_file
    output_file.write_text(text, encoding="utf-8")
    return output_file


def persist_canonical_phase5_output(*, result: FinalSourceEvaluationBatchResult, output_file: Path = CANONICAL_SOURCE_EVALUATIONS_FILE) -> Path:
    payload = {
        "schema_version": PHASE5_CANONICAL_RESULT_SCHEMA_VERSION,
        "policy_version": FINAL_SOURCE_EVALUATION_POLICY_VERSION,
        "final_evaluations": [item.to_dict() for item in result.final_evaluations],
        "composition_traces": [item.to_dict() for item in result.composition_traces],
        "phase6_handoff": [item.to_dict() for item in result.phase6_handoff],
        "needs_review_backlog": list(result.needs_review_backlog),
        "rejected_source_provenance": list(result.rejected_source_provenance),
        "reconciliation_rows": list(result.reconciliation_rows),
        "diagnostics": list(result.diagnostics),
        "input_fingerprint": result.input_fingerprint,
        "output_hash": result.output_hash,
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if output_file.exists() and output_file.read_text(encoding="utf-8") == text:
        return output_file
    output_file.write_text(text, encoding="utf-8")
    return output_file


def persist_phase6_handoff(*, result: FinalSourceEvaluationBatchResult, output_file: Path = PHASE6_HANDOFF_FILE) -> Path:
    payload = {
        "schema_version": "phase6_source_handoff_v1",
        "policy_version": FINAL_SOURCE_EVALUATION_POLICY_VERSION,
        "approved_sources": [item.to_dict() for item in result.phase6_handoff],
        "acquisition_method_selected": False,
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if output_file.exists() and output_file.read_text(encoding="utf-8") == text:
        return output_file
    output_file.write_text(text, encoding="utf-8")
    return output_file


def _identity_dimension(initial: InitialSourceEvaluation):
    status = initial.entity_match_assessment.status
    officiality = initial.officiality_assessment.status
    positive: list[str] = []
    counter: list[str] = []
    uncertain: list[str] = []
    blockers: list[str] = []
    if status == EntityMatchStatus.MISMATCH:
        blockers.append("entity_mismatch")
        return FinalEvidenceState.BLOCKED, positive, counter, uncertain, blockers
    if officiality == OfficialityStatus.THIRD_PARTY:
        blockers.append("third_party_source_for_entity_surface")
        return FinalEvidenceState.BLOCKED, positive, counter, uncertain, blockers
    if status in {EntityMatchStatus.CONFIRMED, EntityMatchStatus.PROBABLE}:
        positive.append(f"entity_match:{status.value}")
    else:
        uncertain.append("entity_match_uncertain")
    if officiality in {OfficialityStatus.OFFICIAL, OfficialityStatus.PROBABLE_OFFICIAL, OfficialityStatus.AFFILIATED}:
        positive.append(f"officiality:{officiality.value}")
    else:
        uncertain.append("officiality_uncertain")
    if uncertain:
        return FinalEvidenceState.UNCERTAIN, positive, counter, uncertain, blockers
    if status == EntityMatchStatus.CONFIRMED and officiality == OfficialityStatus.OFFICIAL:
        return FinalEvidenceState.STRONG, positive, counter, uncertain, blockers
    return FinalEvidenceState.SUFFICIENT, positive, counter, uncertain, blockers


def _surface_dimension(initial: InitialSourceEvaluation, observation: SourceObservationResult | None):
    positive: list[str] = []
    counter: list[str] = []
    uncertain: list[str] = []
    blockers: list[str] = []
    page_type = initial.page_type_assessment.page_type
    durability = initial.surface_durability_assessment.status
    role_match = initial.source_role_assessment.source_role_match_status
    if durability == SurfaceDurabilityStatus.ONE_OFF_CONTENT and page_type in {
        PageType.ARTICLE_DETAIL,
        PageType.JOB_DETAIL,
        PageType.REPORT_DETAIL,
        PageType.EVENT_DETAIL,
    }:
        blockers.append("one_off_detail_page")
        return FinalEvidenceState.BLOCKED, positive, counter, uncertain, blockers
    if page_type in {PageType.SEARCH_RESULTS, PageType.OTHER, PageType.UNKNOWN}:
        counter.append(f"weak_page_type:{page_type.value}")
    else:
        positive.append(f"page_type:{page_type.value}")
    if durability in {SurfaceDurabilityStatus.DURABLE_SURFACE, SurfaceDurabilityStatus.LIKELY_DURABLE_SURFACE}:
        positive.append(f"durability:{durability.value}")
    elif durability == SurfaceDurabilityStatus.UNCERTAIN:
        uncertain.append("durability_uncertain")
    if role_match in {SourceRoleMatchStatus.MATCH, SourceRoleMatchStatus.COMPATIBLE}:
        positive.append(f"source_role:{role_match.value}")
    elif role_match == SourceRoleMatchStatus.MISMATCH:
        counter.append("source_role_mismatch")
    else:
        uncertain.append("source_role_uncertain")
    if observation is not None and observation.sampled_item_count >= 2 and not observation.failures:
        positive.append("bounded_items_support_surface")
    if counter:
        return FinalEvidenceState.WEAK, positive, counter, uncertain, blockers
    if uncertain:
        return FinalEvidenceState.UNCERTAIN, positive, counter, uncertain, blockers
    return FinalEvidenceState.SUFFICIENT, positive, counter, uncertain, blockers


def _information_dimension(initial: InitialSourceEvaluation, potential: ObservedSignalPotential):
    positive: list[str] = []
    counter: list[str] = []
    uncertain: list[str] = []
    relevance = initial.information_need_relevance_assessment.relevance_level
    if relevance in {RelevanceLevel.HIGH, RelevanceLevel.MEDIUM}:
        positive.append(f"phase5d_information_fit:{relevance.value}")
    elif relevance == RelevanceLevel.LOW:
        counter.append("phase5d_information_fit_low")
    else:
        uncertain.append("phase5d_information_fit_uncertain")
    if potential.relevant_item_count > 0:
        positive.append("observed_information_need_hits")
    elif potential.level == ObservedSignalPotentialLevel.LOW:
        counter.append("observed_information_fit_low")
    if counter and not positive:
        return FinalEvidenceState.WEAK, positive, counter, uncertain
    if uncertain and not positive:
        return FinalEvidenceState.UNCERTAIN, positive, counter, uncertain
    if relevance == RelevanceLevel.HIGH:
        return FinalEvidenceState.STRONG, positive, counter, uncertain
    return FinalEvidenceState.SUPPORTIVE if positive else FinalEvidenceState.WEAK, positive, counter, uncertain


def _observation_dimension(
    initial: InitialSourceEvaluation,
    observation: SourceObservationResult | None,
    potential: ObservedSignalPotential,
    evidence: tuple[ObservedSourceEvidence, ...],
):
    positive: list[str] = []
    counter: list[str] = []
    uncertain: list[str] = []
    conflicts: list[str] = []
    if observation is None:
        uncertain.append("observation_absent")
        return FinalEvidenceState.ABSENT, positive, counter, uncertain, conflicts
    if observation.failures:
        uncertain.append("observation_technical_failures")
    if observation.sampled_item_count >= 2:
        positive.append("bounded_sample_usable")
    if potential.level in {ObservedSignalPotentialLevel.HIGH, ObservedSignalPotentialLevel.MEDIUM}:
        positive.append(f"observed_signal_potential:{potential.level.value}")
    elif potential.level == ObservedSignalPotentialLevel.LOW:
        counter.append("observed_signal_potential_low")
    else:
        uncertain.append("observed_signal_potential_insufficient")
    if initial.information_need_relevance_assessment.relevance_level in {RelevanceLevel.HIGH, RelevanceLevel.MEDIUM} and potential.level == ObservedSignalPotentialLevel.LOW:
        conflicts.append("phase5d_positive_relevance_vs_low_observation")
    if initial.information_need_relevance_assessment.relevance_level == RelevanceLevel.LOW and potential.level in {
        ObservedSignalPotentialLevel.HIGH,
        ObservedSignalPotentialLevel.MEDIUM,
    }:
        conflicts.append("phase5d_low_relevance_vs_supportive_observation")
    invalid_needs = [
        need_id
        for item in evidence
        for need_id in item.relevant_information_need_ids
        if need_id not in initial.information_need_relevance_assessment.allowed_information_need_ids
    ]
    if invalid_needs:
        conflicts.append("unsupported_information_need_id_in_observation")
    if conflicts:
        return FinalEvidenceState.CONFLICTING, positive, counter, uncertain, conflicts
    if uncertain:
        return FinalEvidenceState.UNCERTAIN, positive, counter, uncertain, conflicts
    if counter and not positive:
        return FinalEvidenceState.WEAK, positive, counter, uncertain, conflicts
    return FinalEvidenceState.SUPPORTIVE if positive else FinalEvidenceState.WEAK, positive, counter, uncertain, conflicts


def _quality_dimension(
    initial: InitialSourceEvaluation,
    observation: SourceObservationResult | None,
    potential: ObservedSignalPotential,
):
    positive: list[str] = []
    counter: list[str] = []
    uncertain: list[str] = []
    if initial.evaluation_confidence in {EvaluationConfidence.HIGH, EvaluationConfidence.MEDIUM}:
        positive.append(f"phase5d_confidence:{initial.evaluation_confidence.value}")
    else:
        uncertain.append("phase5d_low_confidence")
    if observation is not None:
        if not observation.failures and observation.sampled_item_count >= 2:
            positive.append("observation_complete")
        else:
            uncertain.append("observation_incomplete")
    for limitation in potential.limitations:
        uncertain.append(f"observation_limitation:{limitation}")
    if uncertain and not positive:
        return FinalEvidenceState.UNCERTAIN, positive, counter, uncertain
    return FinalEvidenceState.SUFFICIENT if positive and not uncertain else FinalEvidenceState.SUPPORTIVE, positive, counter, uncertain


def _review_resolution_state(
    initial: InitialSourceEvaluation,
    eligibility: dict[str, Any] | None,
    observation: SourceObservationResult | None,
    potential: ObservedSignalPotential,
    evidence: tuple[ObservedSourceEvidence, ...],
    info_state: FinalEvidenceState,
    obs_state: FinalEvidenceState,
) -> ReviewResolutionState:
    if initial.decision != InitialEvaluationDecision.NEEDS_REVIEW:
        return ReviewResolutionState.NOT_APPLICABLE
    if not eligibility or eligibility.get("status") != "review_resolution":
        return ReviewResolutionState.UNRESOLVED
    if observation is None:
        return ReviewResolutionState.UNRESOLVED
    if potential.level in {ObservedSignalPotentialLevel.HIGH, ObservedSignalPotentialLevel.MEDIUM} and evidence:
        if info_state in {FinalEvidenceState.STRONG, FinalEvidenceState.SUPPORTIVE} and obs_state == FinalEvidenceState.SUPPORTIVE:
            return ReviewResolutionState.RESOLVED_POSITIVE
        return ReviewResolutionState.PARTIALLY_RESOLVED
    if potential.level == ObservedSignalPotentialLevel.LOW and observation.sampled_item_count >= 2 and potential.relevant_item_count == 0:
        return ReviewResolutionState.RESOLVED_NEGATIVE
    return ReviewResolutionState.PARTIALLY_RESOLVED


def _final_source_value(
    initial: InitialSourceEvaluation,
    decision: FinalEvaluationDecision,
    potential: ObservedSignalPotential,
) -> SourceValueLevel:
    if decision == FinalEvaluationDecision.REJECTED:
        return SourceValueLevel.LOW
    if potential.level == ObservedSignalPotentialLevel.HIGH:
        return SourceValueLevel.HIGH
    if potential.level == ObservedSignalPotentialLevel.MEDIUM:
        return SourceValueLevel.MEDIUM
    if decision == FinalEvaluationDecision.NEEDS_REVIEW:
        return initial.source_value if initial.source_value != SourceValueLevel.HIGH else SourceValueLevel.MEDIUM
    return initial.source_value


def _final_confidence(
    initial: InitialSourceEvaluation,
    decision: FinalEvaluationDecision,
    trace: FinalEvidenceCompositionTrace,
    potential: ObservedSignalPotential,
) -> EvaluationConfidence:
    if trace.evidence_conflicts or trace.unresolved_uncertainties:
        return EvaluationConfidence.LOW
    if decision == FinalEvaluationDecision.APPROVED_FOR_ACQUISITION and potential.level == ObservedSignalPotentialLevel.HIGH:
        return EvaluationConfidence.HIGH
    if decision in {FinalEvaluationDecision.APPROVED_FOR_ACQUISITION, FinalEvaluationDecision.REJECTED}:
        return EvaluationConfidence.MEDIUM
    return initial.evaluation_confidence if initial.evaluation_confidence != EvaluationConfidence.HIGH else EvaluationConfidence.MEDIUM


def _absent_signal_potential(initial: InitialSourceEvaluation) -> ObservedSignalPotential:
    digest = hash_canonical_value(
        {
            "policy_version": FINAL_SOURCE_EVALUATION_POLICY_VERSION,
            "initial_source_evaluation_id": initial.initial_source_evaluation_id,
            "absence": "no_phase5e_observation",
        }
    )
    return ObservedSignalPotential(
        observed_signal_potential_id=f"observed_signal_potential_absent_{digest[:12]}",
        source_observation_result_id=f"no_observation_{initial.initial_source_evaluation_id}",
        level=ObservedSignalPotentialLevel.INSUFFICIENT_EVIDENCE,
        sampled_item_count=0,
        relevant_item_count=0,
        information_need_hit_count={},
        supporting_observed_evidence_ids=(),
        rationale="No Phase 5E observation was available for this source.",
        limitations=("no_phase5e_observation",),
        supporting_metrics={"composition_policy_version": FINAL_SOURCE_EVALUATION_POLICY_VERSION},
    )


def _observation_absence_reason(initial: InitialSourceEvaluation, eligibility: dict[str, Any] | None) -> str:
    if initial.decision == InitialEvaluationDecision.REJECTED:
        return "phase5d_rejected_not_observed"
    if eligibility is None:
        return "observation_eligibility_absent"
    if eligibility.get("status") == "not_observation_eligible":
        blockers = eligibility.get("blocking_reasons") or []
        return "not_observation_eligible:" + ",".join(str(item) for item in blockers)
    return "eligible_observation_missing"


def _final_rationale(decision: FinalEvaluationDecision, reason_codes: tuple[str, ...]) -> str:
    return f"{decision.value}: " + ", ".join(reason_codes[:4])


def _backlog_record(final: FinalSourceEvaluation, traces: tuple[FinalEvidenceCompositionTrace, ...] | list[FinalEvidenceCompositionTrace]) -> dict[str, Any]:
    trace = next(item for item in traces if item.candidate_source_id == final.candidate_source_id)
    return {
        "candidate_source_id": final.candidate_source_id,
        "final_source_evaluation_id": final.final_source_evaluation_id,
        "unresolved_uncertainties": list(trace.unresolved_uncertainties),
        "evidence_conflicts": list(trace.evidence_conflicts),
        "reason_codes": list(trace.final_decision_reason_codes),
        "observation_result_id": final.observation_result_id,
    }


def _rejected_record(final: FinalSourceEvaluation, traces: tuple[FinalEvidenceCompositionTrace, ...] | list[FinalEvidenceCompositionTrace]) -> dict[str, Any]:
    trace = next(item for item in traces if item.candidate_source_id == final.candidate_source_id)
    return {
        "candidate_source_id": final.candidate_source_id,
        "final_source_evaluation_id": final.final_source_evaluation_id,
        "hard_blockers": list(trace.hard_blockers),
        "counter_evidence": list(trace.counter_evidence),
        "reason_codes": list(trace.final_decision_reason_codes),
        "observation_result_id": final.observation_result_id,
    }


def _stable_tuple(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(sorted(result))


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _json_ready(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if hasattr(value, "to_dict"):
        return _json_ready(value.to_dict())
    return value
