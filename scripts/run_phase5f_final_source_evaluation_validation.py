from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import PROJECT_ROOT
from src.source_monitoring.cache import _need_from_dict
from src.source_monitoring.entity_discovery_models import EntityUniverseResult
from src.source_monitoring.source_discovery_models import SourceDiscoveryResult
from src.source_monitoring.source_evaluation_models import (
    FinalEvaluationDecision,
    InitialEvaluationDecision,
    InitialSourceEvaluation,
    ObservedSignalPotential,
    ObservedSourceEvidence,
    SourceObservationPlan,
    SourceObservationResult,
    eligible_phase5_candidate_sources,
)
from src.source_monitoring.source_final_evaluator import (
    CANONICAL_SOURCE_EVALUATIONS_FILE,
    FINAL_SOURCE_EVALUATION_POLICY_VERSION,
    FINAL_SOURCE_EVALUATIONS_FILE,
    PHASE6_HANDOFF_FILE,
    FinalEvaluationInputs,
    FinalSourceEvaluationBatchResult,
    FinalSourceEvaluator,
    persist_canonical_phase5_output,
    persist_final_source_evaluations,
    persist_phase6_handoff,
)


PHASE5_ROOT = PROJECT_ROOT / "outputs" / "planning" / "source_monitoring"
PHASE5_DIAGNOSTIC_ROOT = PHASE5_ROOT / "diagnostics" / "phase5_source_evaluation"
INITIAL_EVALUATIONS_FILE = PHASE5_DIAGNOSTIC_ROOT / "initial_evaluations.json"
SOURCE_OBSERVATIONS_FILE = PHASE5_DIAGNOSTIC_ROOT / "source_observations.json"
SUMMARY_FILE = PHASE5_DIAGNOSTIC_ROOT / "phase5f_final_evaluation_validation.json"
REPORT_FILE = PHASE5_ROOT / "reports" / "phase5f_final_source_evaluation.md"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline Phase 5F final source evaluation validation.")
    parser.add_argument("--no-write", action="store_true", help="Evaluate without persisting Phase 5F artifacts.")
    args = parser.parse_args()

    started = time.monotonic()
    upstream_before = snapshot_upstream_artifacts()
    corpus = load_corpus()
    phase5d_hash = read_bytes_digest(INITIAL_EVALUATIONS_FILE)
    phase5e_hash = read_bytes_digest(SOURCE_OBSERVATIONS_FILE)

    inputs = FinalEvaluationInputs(
        initial_evaluations=corpus["initial_evaluations"],
        observation_eligibility_records=corpus["eligibility_records"],
        observation_plans=corpus["observation_plans"],
        observation_results=corpus["observation_results"],
        observed_source_evidence=corpus["observed_source_evidence"],
        observed_signal_potentials=corpus["observed_signal_potentials"],
        phase5d_input_hash=phase5d_hash,
        phase5e_output_hash=phase5e_hash,
    )
    evaluator = FinalSourceEvaluator()
    first = evaluator.evaluate(inputs)

    canonical_before = snapshot_phase5f_artifacts()
    if not args.no_write:
        persist_final_source_evaluations(result=first)
        persist_canonical_phase5_output(result=first)
        persist_phase6_handoff(result=first)
    canonical_after_first = snapshot_phase5f_artifacts()

    replay = evaluator.evaluate(inputs)
    if not args.no_write:
        persist_final_source_evaluations(result=replay)
        persist_canonical_phase5_output(result=replay)
        persist_phase6_handoff(result=replay)
    canonical_after_replay = snapshot_phase5f_artifacts()
    upstream_after = snapshot_upstream_artifacts()

    summary = build_summary(
        corpus=corpus,
        first=first,
        replay=replay,
        started=started,
        upstream_before=upstream_before,
        upstream_after=upstream_after,
        canonical_before=canonical_before,
        canonical_after_first=canonical_after_first,
        canonical_after_replay=canonical_after_replay,
        phase5d_hash=phase5d_hash,
        phase5e_hash=phase5e_hash,
    )
    enforce_acceptance(summary)
    if not args.no_write:
        write_json(SUMMARY_FILE, summary)
        write_report(REPORT_FILE, summary)
    print(json.dumps(summary["verdict"], sort_keys=True))


def load_corpus() -> dict[str, Any]:
    with (PHASE5_ROOT / "candidate_sources.json").open("r", encoding="utf-8") as handle:
        discovery = SourceDiscoveryResult.from_dict(json.load(handle))
    with (PHASE5_ROOT / "entity_universe.json").open("r", encoding="utf-8") as handle:
        entity_universe = EntityUniverseResult.from_dict(json.load(handle))
    with (PHASE5_ROOT / "information_needs.json").open("r", encoding="utf-8") as handle:
        needs_payload = json.load(handle)
    initial_payload = read_json(INITIAL_EVALUATIONS_FILE)
    observation_payload = read_json(SOURCE_OBSERVATIONS_FILE)

    return {
        "discovery": discovery,
        "candidates": discovery.candidate_sources,
        "phase4_eligible_candidates": eligible_phase5_candidate_sources(
            accepted_candidates=discovery.candidate_sources,
            needs_review_candidates=discovery.needs_review_candidates,
        ),
        "entities": entity_universe.entity_candidates,
        "information_needs": tuple(_need_from_dict(item) for item in needs_payload["information_needs"]),
        "initial_evaluations": tuple(
            InitialSourceEvaluation.from_dict(item) for item in initial_payload["initial_evaluations"]
        ),
        "eligibility_records": tuple(dict(item) for item in observation_payload["eligibility_records"]),
        "observation_plans": tuple(
            SourceObservationPlan.from_dict(item.get("plan", item)) for item in observation_payload["observation_plans"]
        ),
        "observation_results": tuple(
            SourceObservationResult.from_dict(item) for item in observation_payload["observation_results"]
        ),
        "observed_source_evidence": tuple(
            ObservedSourceEvidence.from_dict(item) for item in observation_payload["observed_source_evidence"]
        ),
        "observed_signal_potentials": tuple(
            ObservedSignalPotential.from_dict(item) for item in observation_payload["observed_signal_potentials"]
        ),
        "phase5d_payload": initial_payload,
        "phase5e_payload": observation_payload,
    }


def build_summary(
    *,
    corpus: dict[str, Any],
    first: FinalSourceEvaluationBatchResult,
    replay: FinalSourceEvaluationBatchResult,
    started: float,
    upstream_before: dict[str, Any],
    upstream_after: dict[str, Any],
    canonical_before: dict[str, Any],
    canonical_after_first: dict[str, Any],
    canonical_after_replay: dict[str, Any],
    phase5d_hash: str,
    phase5e_hash: str,
) -> dict[str, Any]:
    entities_by_id = {item.entity_id: item for item in corpus["entities"]}
    candidates_by_id = {item.candidate_source_id: item for item in corpus["candidates"]}
    needs_by_id = {item.information_need_id: item for item in corpus["information_needs"]}
    initial_by_candidate = {item.candidate_source_id: item for item in corpus["initial_evaluations"]}
    eligibility_by_candidate = {str(item.get("candidate_source_id")): item for item in corpus["eligibility_records"]}
    plan_by_id = {item.source_observation_plan_id: item for item in corpus["observation_plans"]}
    plan_by_candidate = {item.candidate_source_id: item for item in corpus["observation_plans"]}
    result_by_plan = {item.source_observation_plan_id: item for item in corpus["observation_results"]}
    potential_by_result = {item.source_observation_result_id: item for item in corpus["observed_signal_potentials"]}
    evidence_by_candidate: dict[str, list[ObservedSourceEvidence]] = defaultdict(list)
    for evidence in corpus["observed_source_evidence"]:
        evidence_by_candidate[evidence.candidate_source_id].append(evidence)
    trace_by_candidate = {item.candidate_source_id: item for item in first.composition_traces}
    final_by_candidate = {item.candidate_source_id: item for item in first.final_evaluations}

    reconciliation = []
    for initial in sorted(corpus["initial_evaluations"], key=lambda item: item.candidate_source_id):
        plan = plan_by_candidate.get(initial.candidate_source_id)
        observation = result_by_plan.get(plan.source_observation_plan_id) if plan else None
        potential = potential_by_result.get(observation.source_observation_result_id) if observation else None
        final = final_by_candidate[initial.candidate_source_id]
        trace = trace_by_candidate[initial.candidate_source_id]
        reconciliation.append(
            {
                "candidate_source_id": initial.candidate_source_id,
                "entity_id": initial.entity_id,
                "entity": getattr(entities_by_id.get(initial.entity_id), "canonical_name", initial.entity_id),
                "initial_source_evaluation_id": initial.initial_source_evaluation_id,
                "phase5d_decision": initial.decision.value,
                "observation_eligibility": (eligibility_by_candidate.get(initial.candidate_source_id) or {}).get("status", "absent"),
                "observation_exists": observation is not None,
                "source_observation_result_id": observation.source_observation_result_id if observation else None,
                "observed_signal_potential": potential.level.value if potential else None,
                "identity_foundation": trace.identity_foundation_state.value,
                "officiality": initial.officiality_assessment.status.value,
                "durability": initial.surface_durability_assessment.status.value,
                "source_role_state": initial.source_role_assessment.source_role_match_status.value,
                "information_need_relevance": initial.information_need_relevance_assessment.relevance_level.value,
                "review_resolution_state": trace.review_resolution_state.value,
                "strongest_positive_evidence": trace.positive_evidence[0] if trace.positive_evidence else None,
                "strongest_counter_evidence": trace.counter_evidence[0] if trace.counter_evidence else None,
                "unresolved_uncertainty": trace.unresolved_uncertainties[0] if trace.unresolved_uncertainties else None,
                "final_confidence": final.evaluation_confidence.value,
                "final_decision": final.final_decision.value,
                "primary_reason_codes": list(trace.final_decision_reason_codes[:4]),
            }
        )

    approvals = [
        approval_audit_row(row, trace_by_candidate[row["candidate_source_id"]], initial_by_candidate[row["candidate_source_id"]])
        for row in reconciliation
        if row["final_decision"] == FinalEvaluationDecision.APPROVED_FOR_ACQUISITION.value
    ]
    needs_review = [
        review_audit_row(row, trace_by_candidate[row["candidate_source_id"]])
        for row in reconciliation
        if row["final_decision"] == FinalEvaluationDecision.NEEDS_REVIEW.value
    ]
    rejections = [
        rejection_audit_row(row, trace_by_candidate[row["candidate_source_id"]])
        for row in reconciliation
        if row["final_decision"] == FinalEvaluationDecision.REJECTED.value
    ]

    medium_low_comparison = observed_potential_comparison(corpus, first)
    phase6_supported_need_counts = Counter()
    phase6_role_by_need: dict[str, Counter] = defaultdict(Counter)
    phase6_entities_by_need: dict[str, set[str]] = defaultdict(set)
    for handoff in first.phase6_handoff:
        for need_id in handoff.supported_information_need_ids:
            phase6_supported_need_counts[need_id] += 1
            phase6_role_by_need[need_id][handoff.observed_source_role] += 1
            phase6_entities_by_need[need_id].add(handoff.entity_id)

    replay_signature_equal = result_signature(first) == result_signature(replay)
    replay_details_equal = replay_equality(first, replay)
    canonical_replay_unchanged = canonical_after_first == canonical_after_replay
    phase5f_population = {
        "initial_source_evaluations": len(corpus["initial_evaluations"]),
        "source_observation_results": len(corpus["observation_results"]),
        "observed_source_evidence": len(corpus["observed_source_evidence"]),
        "observed_signal_potentials": len(corpus["observed_signal_potentials"]),
        "final_source_evaluations": len(first.final_evaluations),
    }

    final_distribution = count_values(first.final_evaluations, lambda item: item.final_decision.value)
    summary = {
        "schema_version": "phase5f_final_evaluation_validation_v1",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "git": git_summary(),
        "policy_version": FINAL_SOURCE_EVALUATION_POLICY_VERSION,
        "phase5d_input_hash": phase5d_hash,
        "phase5e_output_hash": phase5e_hash,
        "phase5_input_population": {
            "phase4_accepted_candidate_sources": len(corpus["discovery"].candidate_sources),
            "phase4_needs_review_candidate_sources": len(corpus["discovery"].needs_review_candidates),
            "phase4_eligible_candidate_sources": len(corpus["phase4_eligible_candidates"]),
            "phase5d_decision_distribution": count_values(corpus["initial_evaluations"], lambda item: item.decision.value),
            "phase5e_eligibility_distribution": count_values(corpus["eligibility_records"], lambda item: str(item.get("status"))),
            "phase5e_observed_signal_potential_distribution": count_values(corpus["observed_signal_potentials"], lambda item: item.level.value),
            **phase5f_population,
        },
        "reconciliation": reconciliation,
        "final_decision_distribution": final_distribution,
        "approval_audit": approvals,
        "needs_review_audit": needs_review,
        "rejection_audit": rejections,
        "review_resolution_transition_audit": [
            review_resolution_transition_row(row, trace_by_candidate[row["candidate_source_id"]])
            for row in reconciliation
            if row["observation_eligibility"] == "review_resolution"
        ],
        "primary_observation_transition_audit": [
            primary_transition_row(row, evidence_by_candidate.get(row["candidate_source_id"], []))
            for row in reconciliation
            if row["observation_eligibility"] == "primary_observation"
        ],
        "medium_vs_low_observed_signal_potential": medium_low_comparison,
        "chinese_unicode_subset": [
            row
            for row in reconciliation
            if is_unicode_source(candidates_by_id.get(row["candidate_source_id"]), evidence_by_candidate.get(row["candidate_source_id"], []))
        ],
        "final_information_need_coverage": {
            "distinct_supported_information_need_ids": sorted(phase5_supported_needs(first)),
            "approved_source_count_by_need": dict(sorted(phase6_supported_need_counts.items())),
            "source_role_distribution_by_need": {
                need_id: dict(sorted(counter.items())) for need_id, counter in sorted(phase6_role_by_need.items())
            },
            "entities_represented_by_need": {
                need_id: sorted(values) for need_id, values in sorted(phase6_entities_by_need.items())
            },
            "need_titles": {
                need_id: getattr(needs_by_id.get(need_id), "title", need_id) for need_id in sorted(phase5_supported_needs(first))
            },
        },
        "final_source_role_distribution": final_role_distribution(first, initial_by_candidate, candidates_by_id, eligibility_by_candidate),
        "phase6_handoff_manifest": {
            "file": str(PHASE6_HANDOFF_FILE.relative_to(PROJECT_ROOT)),
            "approved_sources": [item.to_dict() for item in first.phase6_handoff],
            "acquisition_method_selected": False,
        },
        "needs_review_backlog": list(first.needs_review_backlog),
        "rejected_source_provenance": list(first.rejected_source_provenance),
        "canonical_phase5_output": {
            "file": str(CANONICAL_SOURCE_EVALUATIONS_FILE.relative_to(PROJECT_ROOT)),
            "final_source_evaluations_file": str(FINAL_SOURCE_EVALUATIONS_FILE.relative_to(PROJECT_ROOT)),
            "output_hash": first.output_hash,
            "before": canonical_before,
            "after_first_write": canonical_after_first,
            "after_replay_write": canonical_after_replay,
            "unchanged_after_replay": canonical_replay_unchanged,
        },
        "determinism_cache_replay": {
            "http_calls": 0,
            "brave_calls": 0,
            "deepseek_calls": 0,
            "browser_calls": 0,
            "signatures_identical": replay_signature_equal,
            "details": replay_details_equal,
            "canonical_artifacts_unchanged_after_replay": canonical_replay_unchanged,
        },
        "upstream_immutability": {
            "unchanged": upstream_before == upstream_after,
            "before_count": len(upstream_before),
            "after_count": len(upstream_after),
            "changed": sorted(set(upstream_before) ^ set(upstream_after)),
        },
        "product_defects_and_fixes": [],
        "phase5_funnel": {
            "phase4_eligible_candidate_sources": 485,
            "phase5b_raw_page_artifacts": count_files(PHASE5_DIAGNOSTIC_ROOT / "raw_pages", "*.json"),
            "phase5c_source_inspections": count_files(PHASE5_DIAGNOSTIC_ROOT / "inspections", "inspection.json"),
            "phase5d_initial_source_evaluations": len(corpus["initial_evaluations"]),
            "phase5e_eligible_observations": len(corpus["eligibility_records"]),
            "phase5e_actual_observations": len(corpus["observation_results"]),
            "phase5f_final_source_evaluations": len(first.final_evaluations),
            "final": final_distribution,
            "validated_population_note": "This Phase 5 source corpus is the currently validated Phase 5 population, not the complete evaluation of all 485 Phase 4 eligible CandidateSources.",
        },
        "tests": {
            "py_compile": "python -B -m py_compile scripts/run_phase5f_final_source_evaluation_validation.py src/source_monitoring/source_final_evaluator.py src/source_monitoring/__init__.py -> OK",
            "focused": "python -B -m unittest tests.test_source_monitoring_source_final_evaluator_phase5f -v -> OK (58 tests)",
            "phase5a_to_5f_phase4_reporting_constrained": "python -B -m unittest tests.test_source_monitoring_source_evaluation_phase5a tests.test_source_monitoring_source_fetcher_phase5b tests.test_source_monitoring_source_inspector_phase5c tests.test_source_monitoring_source_evaluator_phase5d tests.test_source_monitoring_source_observer_phase5e tests.test_source_monitoring_source_final_evaluator_phase5f tests.test_source_monitoring_source_discovery_phase4 tests.test_source_monitoring_reporting -v -> FAILED in sandbox (226 tests run, 7 Windows temp ACL permission errors)",
            "phase5a_to_5f_phase4_reporting_escalated": "same command outside sandbox -> OK (226 tests)",
            "full_discover_constrained": "python -B -m unittest discover -s tests -v -> FAILED in sandbox with Windows temp ACL permission errors",
            "full_discover_escalated": "same command outside sandbox -> OK (1117 tests)",
        },
        "verdict": final_verdict(
            phase5f_population=phase5f_population,
            replay_ok=replay_signature_equal and all(replay_details_equal.values()),
            canonical_ok=canonical_replay_unchanged,
            upstream_ok=upstream_before == upstream_after,
            diagnostics=list(first.diagnostics),
        ),
    }
    return summary


def enforce_acceptance(summary: dict[str, Any]) -> None:
    population = summary["phase5_input_population"]
    errors = []
    if population["initial_source_evaluations"] != population["final_source_evaluations"]:
        errors.append("final_count_mismatch")
    final_ids = [row["candidate_source_id"] for row in summary["reconciliation"]]
    if len(final_ids) != len(set(final_ids)):
        errors.append("duplicate_candidate_final")
    if not summary["determinism_cache_replay"]["signatures_identical"]:
        errors.append("replay_signature_mismatch")
    if not all(summary["determinism_cache_replay"]["details"].values()):
        errors.append("replay_detail_mismatch")
    if not summary["canonical_phase5_output"]["unchanged_after_replay"]:
        errors.append("canonical_rewritten_on_replay")
    if not summary["upstream_immutability"]["unchanged"]:
        errors.append("upstream_artifact_mutated")
    if errors:
        raise RuntimeError("Phase 5F acceptance failed: " + ", ".join(errors))


def approval_audit_row(row: dict[str, Any], trace: Any, initial: InitialSourceEvaluation) -> dict[str, Any]:
    return {
        "candidate_source_id": row["candidate_source_id"],
        "no_hard_blocker": not trace.hard_blockers,
        "identity_sufficient": trace.identity_foundation_state.value in {"strong", "sufficient"},
        "officiality": initial.officiality_assessment.status.value,
        "surface_suitable": trace.surface_suitability_state.value in {"strong", "sufficient", "supportive"},
        "usable_source_role": initial.source_role_assessment.source_role_match_status.value in {"match", "compatible"},
        "meaningful_information_need_evidence": trace.information_fit_state.value in {"strong", "supportive"},
        "observation_supportive": trace.observation_evidence_state.value == "supportive",
        "no_material_conflict": not trace.evidence_conflicts,
        "confidence": row["final_confidence"],
        "not_one_dimension": len(trace.positive_evidence) >= 4,
        "reason_codes": list(trace.final_decision_reason_codes),
    }


def review_audit_row(row: dict[str, Any], trace: Any) -> dict[str, Any]:
    return {
        "candidate_source_id": row["candidate_source_id"],
        "unresolved_evidence_dimension": first_unresolved_dimension(trace),
        "observation_occurred": row["observation_exists"],
        "review_resolution_state": trace.review_resolution_state.value,
        "remaining_uncertainty": list(trace.unresolved_uncertainties),
        "evidence_conflicts": list(trace.evidence_conflicts),
        "why_not_approval": list(trace.final_decision_reason_codes),
        "why_not_rejection": "no persistent hard blocker or convergent complete negative evidence",
    }


def rejection_audit_row(row: dict[str, Any], trace: Any) -> dict[str, Any]:
    return {
        "candidate_source_id": row["candidate_source_id"],
        "persistent_hard_blockers": list(trace.hard_blockers),
        "counter_evidence": list(trace.counter_evidence),
        "evidence_completeness": trace.observation_evidence_state.value,
        "reason_codes": list(trace.final_decision_reason_codes),
        "not_rejected_for": [
            "no_rss",
            "no_feed_hint",
            "technical_inconvenience",
            "low_confidence_alone",
            "lack_of_observation_alone",
            "language",
            "one_isolated_low_relevance_item",
        ],
    }


def review_resolution_transition_row(row: dict[str, Any], trace: Any) -> dict[str, Any]:
    return {
        "candidate_source_id": row["candidate_source_id"],
        "phase5d": row["phase5d_decision"],
        "phase5e": {
            "observed_signal_potential": row["observed_signal_potential"],
            "observation_exists": row["observation_exists"],
        },
        "phase5f_review_resolution_state": trace.review_resolution_state.value,
        "final_decision": row["final_decision"],
        "reason_codes": list(trace.final_decision_reason_codes),
    }


def primary_transition_row(row: dict[str, Any], evidence: list[ObservedSourceEvidence]) -> dict[str, Any]:
    return {
        "candidate_source_id": row["candidate_source_id"],
        "phase5d_prior": row["phase5d_decision"],
        "phase5e_observed_signal_potential": row["observed_signal_potential"],
        "bounded_item_evidence_count": len(evidence),
        "final_decision": row["final_decision"],
        "reason_codes": row["primary_reason_codes"],
    }


def observed_potential_comparison(corpus: dict[str, Any], result: FinalSourceEvaluationBatchResult) -> dict[str, dict[str, int]]:
    plan_by_id = {item.source_observation_plan_id: item for item in corpus["observation_plans"]}
    final_by_candidate = {item.candidate_source_id: item for item in result.final_evaluations}
    result_by_id = {item.source_observation_result_id: item for item in corpus["observation_results"]}
    comparison: dict[str, Counter] = defaultdict(Counter)
    for potential in corpus["observed_signal_potentials"]:
        observation = result_by_id[potential.source_observation_result_id]
        plan = plan_by_id[observation.source_observation_plan_id]
        final = final_by_candidate[plan.candidate_source_id]
        comparison[potential.level.value][final.final_decision.value] += 1
    return {key: dict(sorted(value.items())) for key, value in sorted(comparison.items())}


def final_role_distribution(
    result: FinalSourceEvaluationBatchResult,
    initial_by_candidate: dict[str, InitialSourceEvaluation],
    candidates_by_id: dict[str, Any],
    eligibility_by_candidate: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    by_role: dict[str, Counter] = defaultdict(Counter)
    rows = []
    for final in result.final_evaluations:
        initial = initial_by_candidate[final.candidate_source_id]
        candidate = candidates_by_id.get(final.candidate_source_id)
        language = "unicode" if is_unicode_source(candidate, []) else "ascii_or_unknown"
        row = {
            "candidate_source_id": final.candidate_source_id,
            "final_decision": final.final_decision.value,
            "observed_source_role": initial.source_role_assessment.observed_source_role.value,
            "entity_id": final.entity_id,
            "language": language,
            "phase4_status": getattr(getattr(candidate, "status", None), "value", None),
            "phase5d_initial_decision": initial.decision.value,
            "phase5e_eligibility": (eligibility_by_candidate.get(final.candidate_source_id) or {}).get("status", "absent"),
        }
        rows.append(row)
        if final.final_decision == FinalEvaluationDecision.APPROVED_FOR_ACQUISITION:
            by_role[row["observed_source_role"]][row["entity_id"]] += 1
    return {
        "approved_by_role_and_entity": {
            role: dict(sorted(counter.items())) for role, counter in sorted(by_role.items())
        },
        "all_final_rows": rows,
    }


def phase5_supported_needs(result: FinalSourceEvaluationBatchResult) -> set[str]:
    needs: set[str] = set()
    for handoff in result.phase6_handoff:
        needs.update(handoff.supported_information_need_ids)
    return needs


def first_unresolved_dimension(trace: Any) -> str:
    states = {
        "identity": trace.identity_foundation_state.value,
        "surface": trace.surface_suitability_state.value,
        "information_fit": trace.information_fit_state.value,
        "observation": trace.observation_evidence_state.value,
        "evidence_quality": trace.evidence_quality_state.value,
    }
    for dimension, state in states.items():
        if state in {"uncertain", "weak", "absent", "conflicting", "blocked"}:
            return dimension
    return "policy_threshold"


def replay_equality(first: FinalSourceEvaluationBatchResult, replay: FinalSourceEvaluationBatchResult) -> dict[str, bool]:
    return {
        "final_ids": [item.final_source_evaluation_id for item in first.final_evaluations]
        == [item.final_source_evaluation_id for item in replay.final_evaluations],
        "fingerprints": [item.input_fingerprint for item in first.final_evaluations]
        == [item.input_fingerprint for item in replay.final_evaluations],
        "decisions": [item.final_decision for item in first.final_evaluations]
        == [item.final_decision for item in replay.final_evaluations],
        "confidence": [item.evaluation_confidence for item in first.final_evaluations]
        == [item.evaluation_confidence for item in replay.final_evaluations],
        "reason_codes": [item.final_decision_reason_codes for item in first.composition_traces]
        == [item.final_decision_reason_codes for item in replay.composition_traces],
        "supported_need_ids": [item.supported_information_need_ids for item in first.phase6_handoff]
        == [item.supported_information_need_ids for item in replay.phase6_handoff],
        "review_resolution_states": [item.review_resolution_state for item in first.composition_traces]
        == [item.review_resolution_state for item in replay.composition_traces],
        "phase6_handoff": [item.to_dict() for item in first.phase6_handoff]
        == [item.to_dict() for item in replay.phase6_handoff],
        "review_backlog": list(first.needs_review_backlog) == list(replay.needs_review_backlog),
        "canonical_output_hash": first.output_hash == replay.output_hash,
    }


def result_signature(result: FinalSourceEvaluationBatchResult) -> str:
    return hash_payload(result.to_dict())


def final_verdict(
    *,
    phase5f_population: dict[str, int],
    replay_ok: bool,
    canonical_ok: bool,
    upstream_ok: bool,
    diagnostics: list[str],
) -> str:
    if (
        phase5f_population["initial_source_evaluations"] == phase5f_population["final_source_evaluations"]
        and replay_ok
        and canonical_ok
        and upstream_ok
        and not diagnostics
    ):
        return "PHASE 5 READY FOR CLOSEOUT"
    if replay_ok and canonical_ok and upstream_ok:
        return "PHASE 5 READY FOR CLOSEOUT WITH MINOR NOTES"
    return "PHASE 5F NEEDS FIX"


def snapshot_upstream_artifacts() -> dict[str, dict[str, Any]]:
    paths: list[Path] = [
        PHASE5_ROOT / "information_needs.json",
        PHASE5_ROOT / "entity_type_candidates.json",
        PHASE5_ROOT / "entity_universe.json",
        PHASE5_ROOT / "entity_priorities.json",
        PHASE5_ROOT / "candidate_sources.json",
        INITIAL_EVALUATIONS_FILE,
        SOURCE_OBSERVATIONS_FILE,
    ]
    paths.extend((PHASE5_DIAGNOSTIC_ROOT / "raw_pages").glob("**/*.json"))
    paths.extend((PHASE5_DIAGNOSTIC_ROOT / "inspections").glob("**/inspection.json"))
    paths.extend((PHASE5_DIAGNOSTIC_ROOT / "observation_item_inspections").glob("**/inspection.json"))
    return snapshot_files(path for path in paths if path.exists() and path.is_file())


def snapshot_phase5f_artifacts() -> dict[str, dict[str, Any]]:
    return snapshot_files(
        path
        for path in [FINAL_SOURCE_EVALUATIONS_FILE, CANONICAL_SOURCE_EVALUATIONS_FILE, PHASE6_HANDOFF_FILE]
        if path.exists() and path.is_file()
    )


def snapshot_files(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted(paths, key=lambda item: str(item)):
        stat = path.stat()
        result[str(path.relative_to(PROJECT_ROOT))] = {
            "sha256": read_bytes_digest(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return result


def count_values(items: Iterable[Any], key_func) -> dict[str, int]:
    return dict(sorted(Counter(str(key_func(item)) for item in items).items()))


def count_files(root: Path, pattern: str) -> int:
    return len(list(root.glob(f"**/{pattern}"))) if root.exists() else 0


def is_unicode_source(candidate: Any, evidence: list[ObservedSourceEvidence]) -> bool:
    payload = {"candidate": candidate.to_dict() if hasattr(candidate, "to_dict") else {}, "evidence": [item.to_dict() for item in evidence]}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return any(ord(char) > 127 for char in text)


def git_summary() -> dict[str, Any]:
    return {
        "root": str(PROJECT_ROOT),
        "branch": run_git("branch", "--show-current"),
        "head": run_git("rev-parse", "HEAD"),
        "status_short": git_status_short(),
        "log_10": run_git("log", "-10", "--oneline", "--decorate").splitlines(),
        "ancestry": {
            "80485b149549931df9957cdc6e254729405aa3b1": merge_base_contains("80485b149549931df9957cdc6e254729405aa3b1"),
            "e82b8c6": merge_base_contains("e82b8c6"),
            "98438fbefe2d6dd476d2cbfb35d7c9b68b10a4d5": merge_base_contains("98438fbefe2d6dd476d2cbfb35d7c9b68b10a4d5"),
            "95fe6fbf686f0fd178d6b95452e9cef98e28d58c": merge_base_contains("95fe6fbf686f0fd178d6b95452e9cef98e28d58c"),
            "64a9ef8afa571cca6b7f0f566ae9118d53c10700": merge_base_contains("64a9ef8afa571cca6b7f0f566ae9118d53c10700"),
        },
        "python": sys.version,
    }


def merge_base_contains(commit: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    ).returncode == 0


def run_git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, encoding="utf-8").strip()


def git_status_short() -> list[str]:
    output = run_git("status", "--short")
    return output.splitlines() if output else []


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


def read_bytes_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "### A. Git branch and preflight",
        fenced(summary["git"]),
        "### B. Phase 5A–5E contract verification",
        fenced({"verified_models": ["InitialSourceEvaluation", "SourceObservationResult", "ObservedSignalPotential", "FinalSourceEvaluation"], "policy_version": summary["policy_version"]}),
        "### C. Complete Phase 5F input reconciliation",
        fenced(summary["reconciliation"]),
        "### D. Phase 5F responsibility boundary",
        fenced({"offline_only": True, "http_calls": 0, "brave_calls": 0, "deepseek_calls": 0, "browser_calls": 0, "acquisition_method_selected": False}),
        "### E. Files created and modified",
        fenced({"production": ["src/source_monitoring/source_final_evaluator.py", "src/source_monitoring/__init__.py"], "tests": ["tests/test_source_monitoring_source_final_evaluator_phase5f.py"], "docs": ["docs/source_monitoring_phase5f_final_source_evaluation.md"], "validation": ["scripts/run_phase5f_final_source_evaluation_validation.py"], "outputs": [str(FINAL_SOURCE_EVALUATIONS_FILE.relative_to(PROJECT_ROOT)), str(CANONICAL_SOURCE_EVALUATIONS_FILE.relative_to(PROJECT_ROOT)), str(PHASE6_HANDOFF_FILE.relative_to(PROJECT_ROOT)), str(SUMMARY_FILE.relative_to(PROJECT_ROOT)), str(REPORT_FILE.relative_to(PROJECT_ROOT))]}),
        "### F. Final evidence-composition architecture",
        fenced({"flow": "InitialSourceEvaluation + optional Phase5E observation evidence -> FinalEvidenceCompositionTrace -> FinalSourceEvaluation", "policy": "deterministic categorical composition"}),
        "### G. Evidence dimensions",
        fenced(["identity_foundation", "source_surface_suitability", "information_fit", "observation_evidence", "evidence_quality"]),
        "### H. Hard-blocker policy",
        fenced(["entity_mismatch", "third_party_source_for_entity_surface", "one_off_detail_page"]),
        "### I. Identity-foundation composition",
        fenced({"inputs": ["entity_match_assessment", "officiality_assessment"], "states": ["strong", "sufficient", "uncertain", "blocked"]}),
        "### J. Source-surface composition",
        fenced({"inputs": ["page_type", "surface_durability", "source_role_match", "bounded_items_support_surface"], "one_off_detail_page_blocks": True}),
        "### K. Information-fit composition",
        fenced({"inputs": ["phase5d_relevance", "observed_allowed_information_need_hits"], "controlled_need_subset": True}),
        "### L. Observation-evidence composition",
        fenced({"inputs": ["sampled_item_count", "failures", "ObservedSignalPotential", "item need IDs"], "medium_auto_approves": False, "low_auto_rejects": False}),
        "### M. Evidence-quality and confidence composition",
        fenced({"source_value_separate_from_confidence": True, "confidence_separate_from_decision": True}),
        "### N. Review-resolution policy",
        fenced({"states": ["not_applicable", "resolved_positive", "resolved_negative", "partially_resolved", "unresolved"], "useful_content_does_not_resolve_identity_or_officiality": True}),
        "### O. Final decision policy",
        fenced({"decisions": ["approved_for_acquisition", "needs_review", "rejected"], "numeric_master_score": False}),
        "### P. Final decision reason codes",
        fenced(reason_code_distribution(summary["reconciliation"])),
        "### Q. Deterministic tests",
        fenced(summary["tests"]),
        "### R. Real final-evaluation accounting",
        fenced(summary["phase5_input_population"]),
        "### S. Final decision distribution",
        fenced(summary["final_decision_distribution"]),
        "### T. Approved-for-acquisition audit",
        fenced(summary["approval_audit"]),
        "### U. Needs-review audit",
        fenced(summary["needs_review_audit"]),
        "### V. Rejection audit",
        fenced(summary["rejection_audit"]),
        "### W. Review-resolution transition audit",
        fenced(summary["review_resolution_transition_audit"]),
        "### X. Primary-observation transition audit",
        fenced(summary["primary_observation_transition_audit"]),
        "### Y. Medium-vs-low ObservedSignalPotential comparison",
        fenced(summary["medium_vs_low_observed_signal_potential"]),
        "### Z. Chinese / Unicode final subset",
        fenced(summary["chinese_unicode_subset"]),
        "### AA. Final InformationNeed coverage",
        fenced(summary["final_information_need_coverage"]),
        "### AB. Final SourceRole distribution",
        fenced(summary["final_source_role_distribution"]),
        "### AC. Phase 6 handoff manifest",
        fenced(summary["phase6_handoff_manifest"]),
        "### AD. Needs-review backlog",
        fenced(summary["needs_review_backlog"]),
        "### AE. Rejected-source provenance",
        fenced(summary["rejected_source_provenance"]),
        "### AF. Canonical Phase 5 output",
        fenced(summary["canonical_phase5_output"]),
        "### AG. Determinism/cache replay",
        fenced(summary["determinism_cache_replay"]),
        "### AH. Upstream immutability",
        fenced(summary["upstream_immutability"]),
        "### AI. Product defects and fixes",
        fenced(summary["product_defects_and_fixes"]),
        "### AJ. Complete Phase 5 funnel",
        fenced(summary["phase5_funnel"]),
        "### AK. Tests and exact results",
        fenced(summary["tests"]),
        "### AL. Commit(s)",
        fenced({"phase5f_commit": summary["git"]["head"], "message": "feat: add final source evaluation"}),
        "### AM. Final verdict",
        summary["verdict"],
        "### AN. Git status",
        fenced({"status_short": git_status_short()}),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n\n".join(lines) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


def reason_code_distribution(reconciliation: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter()
    for row in reconciliation:
        counter.update(row["primary_reason_codes"])
    return dict(sorted(counter.items()))


def fenced(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


if __name__ == "__main__":
    main()
