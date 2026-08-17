from __future__ import annotations

from collections import Counter, defaultdict
import argparse
from dataclasses import asdict
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import LLM_PROVIDER, PROJECT_ROOT, SOURCE_EVALUATION_MODEL
from src.source_monitoring.cache import _need_from_dict
from src.source_monitoring.entity_discovery_models import EntityUniverseResult
from src.source_monitoring.source_discovery_models import (
    CandidateOfficialityStatus,
    CandidateSourceStatus,
    SourceDiscoveryResult,
    SourceRole,
)
from src.source_monitoring.source_evaluation_models import (
    AssessmentMethod,
    EvaluationConfidence,
    InitialEvaluationDecision,
    InitialSourceEvaluation,
    PageType,
    RelevanceLevel,
    SourceInspection,
)
from src.source_monitoring.source_evaluator import (
    INITIAL_EVALUATION_ARTIFACT_ROOT,
    INITIAL_EVALUATION_LLM_ROOT,
    INITIAL_EVALUATION_RESULT_FILE,
    SOURCE_INITIAL_EVALUATION_LLM_SCHEMA_VERSION,
    SOURCE_INITIAL_EVALUATION_PROMPT_VERSION,
    SOURCE_SEMANTIC_BUNDLE_POLICY_VERSION,
    BuiltEvaluationInput,
    GuardInitialEvaluationClient,
    InitialSourceEvaluator,
    SourceEvaluationRuntimeConfig,
    SourceSemanticEvidenceBuilder,
    build_phase5d_inputs,
    persist_initial_evaluation_result,
    select_canonical_initial_evaluation_inspections,
)


REPORT_FILE = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "reports"
    / "phase5d_initial_source_evaluation.md"
)
SUMMARY_FILE = (
    INITIAL_EVALUATION_ARTIFACT_ROOT / "phase5d_initial_evaluation_validation.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 5D initial evaluation validation.")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Load corpus and build bundles without calling DeepSeek.",
    )
    args = parser.parse_args()

    started = time.monotonic()
    artifacts_before = snapshot_upstream_artifacts()
    llm_cache_before = snapshot_files(INITIAL_EVALUATION_LLM_ROOT.glob("*.json"))

    corpus = load_corpus()
    inputs = build_phase5d_inputs(
        inspections=corpus["inspections"],
        candidates=corpus["discovery"].candidate_sources,
        needs_review_candidates=corpus["discovery"].needs_review_candidates,
        entities=corpus["entity_universe"].entity_candidates,
        information_needs=corpus["information_needs"],
        phase4_input_fingerprint=corpus["discovery"].input_fingerprint,
        phase4_output_hash=corpus["discovery"].output_hash,
    )
    compatible_inspections = tuple(
        item
        for item in sorted(corpus["inspections"], key=lambda value: value.candidate_source_id)
        if item.candidate_source_id in inputs
    )
    canonical_inspections = select_canonical_initial_evaluation_inspections(compatible_inspections)
    built_inputs = build_inputs(canonical_inspections, inputs)
    smoke_ids, smoke_reasons = select_smoke_sample(canonical_inspections, inputs, built_inputs)
    smoke_inspections = tuple(
        item for item in canonical_inspections if item.candidate_source_id in smoke_ids
    )
    smoke_inputs = tuple(item for item in built_inputs if item.bundle.candidate_source_id in smoke_ids)

    runtime = SourceEvaluationRuntimeConfig()
    if args.local_only:
        summary = {
            "schema_version": "phase5d_validation_summary_v1",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "git": git_summary(),
            "runtime": asdict(runtime),
            "provider": LLM_PROVIDER,
            "model": SOURCE_EVALUATION_MODEL,
            "prompt_version": SOURCE_INITIAL_EVALUATION_PROMPT_VERSION,
            "llm_schema_version": SOURCE_INITIAL_EVALUATION_LLM_SCHEMA_VERSION,
            "bundle_policy_version": SOURCE_SEMANTIC_BUNDLE_POLICY_VERSION,
            "corpus": corpus_summary(compatible_inspections, canonical_inspections, inputs),
            "bundle_metrics": bundle_metrics(built_inputs),
            "smoke": {
                "candidate_ids": list(smoke_ids),
                "sample": smoke_sample_rows(smoke_inspections, inputs, smoke_inputs, smoke_reasons),
                "result": None,
                "audit": [],
                "verdict": "LOCAL ONLY - DEEPSEEK NOT CALLED",
            },
            "broader": None,
            "cache_replay": {
                "ran": False,
                "zero_new_deepseek_calls": None,
                "cache_hits": 0,
                "unexpected_misses": None,
                "evaluation_signatures_identical": None,
                "llm_checkpoints_unchanged_after_replay": None,
                "parsed_result_file_unchanged_after_replay": None,
            },
            "immutability": {
                "upstream_unchanged": artifacts_before == snapshot_upstream_artifacts(),
                "upstream_before_count": len(artifacts_before),
                "upstream_after_count": len(snapshot_upstream_artifacts()),
                "llm_cache_before_count": len(llm_cache_before),
                "llm_cache_after_count": len(snapshot_files(INITIAL_EVALUATION_LLM_ROOT.glob("*.json"))),
            },
        }
        summary["output_hash"] = hash_payload({**summary, "output_hash": ""})
        print(json.dumps(condensed_stdout(summary), ensure_ascii=False, indent=2, sort_keys=True))
        return

    evaluator = InitialSourceEvaluator(runtime_config=runtime)
    smoke_result = evaluator.evaluate(inspections=smoke_inspections, contexts_by_candidate_id=inputs)
    smoke_audit = audit_result(smoke_result.evaluations)
    smoke_verdict = smoke_audit_verdict(smoke_result)

    if smoke_verdict == "PHASE 5D NEEDS FIX BEFORE BROADER EVALUATION":
        broader_result = None
        replay_result = None
        replay_equal = False
    else:
        broader_result = evaluator.evaluate(
            inspections=canonical_inspections,
            contexts_by_candidate_id=inputs,
        )
        persist_initial_evaluation_result(
            result=broader_result,
            built_inputs=built_inputs,
            output_file=INITIAL_EVALUATION_RESULT_FILE,
        )
        llm_cache_before_replay = snapshot_files(INITIAL_EVALUATION_LLM_ROOT.glob("*.json"))
        result_before_replay = read_bytes_digest(INITIAL_EVALUATION_RESULT_FILE)
        replay_result = InitialSourceEvaluator(
            client=GuardInitialEvaluationClient(),
            runtime_config=runtime,
        ).evaluate(
            inspections=canonical_inspections,
            contexts_by_candidate_id=inputs,
            force_refresh=False,
        )
        llm_cache_after_replay = snapshot_files(INITIAL_EVALUATION_LLM_ROOT.glob("*.json"))
        result_after_replay = read_bytes_digest(INITIAL_EVALUATION_RESULT_FILE)
        replay_equal = result_signature(broader_result.evaluations) == result_signature(
            replay_result.evaluations
        )

    artifacts_after = snapshot_upstream_artifacts()
    llm_cache_after = snapshot_files(INITIAL_EVALUATION_LLM_ROOT.glob("*.json"))

    summary = {
        "schema_version": "phase5d_validation_summary_v1",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "git": git_summary(),
        "runtime": asdict(runtime),
        "provider": LLM_PROVIDER,
        "model": SOURCE_EVALUATION_MODEL,
        "prompt_version": SOURCE_INITIAL_EVALUATION_PROMPT_VERSION,
        "llm_schema_version": SOURCE_INITIAL_EVALUATION_LLM_SCHEMA_VERSION,
        "bundle_policy_version": SOURCE_SEMANTIC_BUNDLE_POLICY_VERSION,
        "corpus": corpus_summary(compatible_inspections, canonical_inspections, inputs),
        "bundle_metrics": bundle_metrics(built_inputs),
        "smoke": {
            "candidate_ids": list(smoke_ids),
            "sample": smoke_sample_rows(smoke_inspections, inputs, smoke_inputs, smoke_reasons),
            "result": result_metrics(smoke_result),
            "audit": smoke_audit,
            "verdict": smoke_verdict,
        },
        "broader": result_metrics(broader_result) if broader_result else None,
        "distributions": distributions(broader_result.evaluations) if broader_result else {},
        "method_metrics": method_metrics(broader_result.evaluations) if broader_result else {},
        "entity_consistency": entity_consistency_audit(broader_result.evaluations) if broader_result else {},
        "chinese_subset": chinese_subset_summary(broader_result.evaluations, canonical_inspections, inputs) if broader_result else {},
        "one_off_audit": one_off_audit(broader_result.evaluations, inputs) if broader_result else [],
        "needs_review_audit": needs_review_audit(broader_result.evaluations) if broader_result else [],
        "rejection_audit": rejection_audit(broader_result.evaluations) if broader_result else [],
        "proceed_audit": proceed_audit(broader_result.evaluations, inputs) if broader_result else [],
        "phase5e_input_population": phase5e_population(broader_result.evaluations, canonical_inspections, inputs) if broader_result else {},
        "cache_replay": {
            "ran": replay_result is not None,
            "zero_new_deepseek_calls": bool(replay_result and replay_result.new_llm_request_count == 0),
            "cache_hits": replay_result.cached_llm_response_count if replay_result else 0,
            "unexpected_misses": replay_result.new_llm_request_count if replay_result else None,
            "evaluation_signatures_identical": replay_equal,
            "llm_checkpoints_unchanged_after_replay": (
                llm_cache_before_replay == llm_cache_after_replay if broader_result else None
            ),
            "parsed_result_file_unchanged_after_replay": (
                result_before_replay == result_after_replay if broader_result else None
            ),
        },
        "immutability": {
            "upstream_unchanged": artifacts_before == artifacts_after,
            "upstream_before_count": len(artifacts_before),
            "upstream_after_count": len(artifacts_after),
            "llm_cache_before_count": len(llm_cache_before),
            "llm_cache_after_count": len(llm_cache_after),
        },
    }
    summary["output_hash"] = hash_payload({**summary, "output_hash": ""})
    write_json(SUMMARY_FILE, summary)
    write_report(REPORT_FILE, summary)
    print(json.dumps(condensed_stdout(summary), ensure_ascii=False, indent=2, sort_keys=True))


def load_corpus() -> dict[str, Any]:
    base = PROJECT_ROOT / "outputs" / "planning" / "source_monitoring"
    with (base / "candidate_sources.json").open("r", encoding="utf-8") as handle:
        discovery = SourceDiscoveryResult.from_dict(json.load(handle))
    with (base / "entity_universe.json").open("r", encoding="utf-8") as handle:
        entity_universe = EntityUniverseResult.from_dict(json.load(handle))
    with (base / "information_needs.json").open("r", encoding="utf-8") as handle:
        needs_payload = json.load(handle)
    information_needs = tuple(_need_from_dict(item) for item in needs_payload["information_needs"])
    inspections = []
    inspection_root = (
        base / "diagnostics" / "phase5_source_evaluation" / "inspections"
    )
    for file_path in sorted(inspection_root.glob("*/inspection.json")):
        with file_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        inspections.append(SourceInspection.from_dict(payload["inspection"]))
    return {
        "discovery": discovery,
        "entity_universe": entity_universe,
        "information_needs": information_needs,
        "inspections": tuple(inspections),
    }


def build_inputs(inspections: tuple[SourceInspection, ...], contexts: dict[str, Any]) -> tuple[BuiltEvaluationInput, ...]:
    builder = SourceSemanticEvidenceBuilder()
    return tuple(
        builder.build(inspection=inspection, context=contexts[inspection.candidate_source_id])
        for inspection in inspections
    )


def select_smoke_sample(
    inspections: tuple[SourceInspection, ...],
    contexts: dict[str, Any],
    built_inputs: tuple[BuiltEvaluationInput, ...],
) -> tuple[tuple[str, ...], dict[str, str]]:
    built_by_id = {item.bundle.candidate_source_id: item for item in built_inputs}
    selected: list[str] = []
    reasons: dict[str, str] = {}

    def choose(label: str, predicate) -> None:
        if len(selected) >= 6:
            return
        for inspection in inspections:
            candidate_id = inspection.candidate_source_id
            if candidate_id in selected:
                continue
            if predicate(inspection, contexts[candidate_id], built_by_id[candidate_id]):
                selected.append(candidate_id)
                reasons[candidate_id] = label
                return

    choose("phase4_accepted_strong_domain", lambda i, c, b: c.phase4_status == CandidateSourceStatus.ACCEPTED and c.candidate.candidate_officiality_status == CandidateOfficialityStatus.OFFICIAL_DOMAIN_MATCH)
    choose("phase4_needs_review", lambda i, c, b: c.phase4_status == CandidateSourceStatus.NEEDS_REVIEW)
    choose("chinese_or_unicode_metadata", lambda i, c, b: is_chinese_inspection(i, c))
    choose("truncated_semantic_evidence", lambda i, c, b: i.semantic_content_truncated)
    choose("client_rendering_hint", lambda i, c, b: i.client_rendering_required_hint)
    choose("role_diversity", lambda i, c, b: c.candidate.source_role not in {contexts[item].candidate.source_role for item in selected})
    choose("detail_or_one_off_candidate", lambda i, c, b: i.has_detail_page_hints)

    for inspection in inspections:
        if len(selected) >= 6:
            break
        if inspection.candidate_source_id not in selected:
            selected.append(inspection.candidate_source_id)
            reasons[inspection.candidate_source_id] = "deterministic_fill"
    return tuple(selected[:6]), reasons


def corpus_summary(
    inspections: tuple[SourceInspection, ...],
    canonical_inspections: tuple[SourceInspection, ...],
    contexts: dict[str, Any],
) -> dict[str, Any]:
    status = Counter(contexts[item.candidate_source_id].phase4_status.value for item in inspections)
    roles = Counter(contexts[item.candidate_source_id].candidate.source_role.value for item in inspections)
    entities = Counter(contexts[item.candidate_source_id].candidate.entity_id for item in inspections)
    languages = Counter((item.content_language or item.html_language or "unknown") for item in inspections)
    candidate_counts = Counter(item.candidate_source_id for item in inspections)
    return {
        "compatible_source_inspections": len(inspections),
        "unique_candidate_source_ids": len({item.candidate_source_id for item in inspections}),
        "canonical_initial_evaluation_inputs": len(canonical_inspections),
        "duplicate_candidate_source_input_count": sum(count - 1 for count in candidate_counts.values() if count > 1),
        "phase4_status_distribution": dict(sorted(status.items())),
        "source_role_distribution": dict(sorted(roles.items())),
        "entity_distribution": dict(sorted(entities.items())),
        "language_distribution": dict(sorted(languages.items())),
        "chinese_or_unicode_count": sum(is_chinese_inspection(item, contexts[item.candidate_source_id]) for item in inspections),
        "truncated_count": sum(item.semantic_content_truncated for item in inspections),
        "client_rendering_hint_count": sum(item.client_rendering_required_hint for item in inspections),
        "weak_or_missing_semantic_evidence_count": sum(not item.semantic_text_windows or item.visible_text_length < 200 for item in inspections),
    }


def smoke_sample_rows(
    inspections: tuple[SourceInspection, ...],
    contexts: dict[str, Any],
    built_inputs: tuple[BuiltEvaluationInput, ...],
    smoke_reasons: dict[str, str],
) -> list[dict[str, Any]]:
    built_by_id = {item.bundle.candidate_source_id: item for item in built_inputs}
    rows = []
    for inspection in inspections:
        context = contexts[inspection.candidate_source_id]
        built = built_by_id[inspection.candidate_source_id]
        rows.append(
            {
                "candidate_source_id": inspection.candidate_source_id,
                "entity": context.entity.canonical_name,
                "planned_source_role": context.candidate.source_role.value,
                "phase4_status": context.phase4_status.value,
                "inspection_id": inspection.inspection_id,
                "inspection_output_hash": inspection.inspection_output_hash,
                "semantic_bundle_chars": bundle_char_count(built),
                "allowed_information_need_count": len(built.bundle.allowed_information_need_ids),
                "reason_for_inclusion": smoke_reasons[inspection.candidate_source_id],
            }
        )
    return rows


def bundle_metrics(built_inputs: tuple[BuiltEvaluationInput, ...]) -> dict[str, Any]:
    sizes = [bundle_char_count(item) for item in built_inputs]
    if not sizes:
        return {}
    return {
        "semantic_bundles_generated": len(built_inputs),
        "total_semantic_bundle_chars": sum(sizes),
        "median_bundle_chars": int(statistics.median(sizes)),
        "p90_bundle_chars": percentile(sizes, 90),
        "maximum_bundle_chars": max(sizes),
    }


def result_metrics(result) -> dict[str, Any]:
    if result is None:
        return {}
    return {
        "evaluations": len(result.evaluations),
        "failures": len(result.failures),
        "diagnostics": list(result.diagnostics),
        "llm_request_count": result.llm_request_count,
        "new_llm_request_count": result.new_llm_request_count,
        "cached_llm_response_count": result.cached_llm_response_count,
        "invalid_output_count": result.invalid_output_count,
        "retry_count": result.retry_count,
        "elapsed_ms": result.elapsed_ms,
        "batch_average_candidates": round(
            len(result.evaluations) / result.llm_request_count, 2
        )
        if result.llm_request_count
        else 0,
    }


def distributions(evaluations: tuple[InitialSourceEvaluation, ...]) -> dict[str, Any]:
    fields = {
        "entity_match": lambda item: item.entity_match_assessment.status.value,
        "officiality": lambda item: item.officiality_assessment.status.value,
        "page_type": lambda item: item.page_type_assessment.page_type.value,
        "durability": lambda item: item.surface_durability_assessment.status.value,
        "role_match": lambda item: item.source_role_assessment.source_role_match_status.value,
        "information_need_relevance": lambda item: item.information_need_relevance_assessment.relevance_level.value,
        "evaluation_confidence": lambda item: item.evaluation_confidence.value,
        "initial_decision": lambda item: item.decision.value,
    }
    return {name: count_percent(evaluations, getter) for name, getter in fields.items()}


def method_metrics(evaluations: tuple[InitialSourceEvaluation, ...]) -> dict[str, Any]:
    dimensions = {
        "entity_match": lambda item: item.entity_match_assessment.assessment_method.value,
        "officiality": lambda item: item.officiality_assessment.assessment_method.value,
        "page_type": lambda item: item.page_type_assessment.assessment_method.value,
        "surface_durability": lambda item: item.surface_durability_assessment.assessment_method.value,
        "source_role": lambda item: item.source_role_assessment.assessment_method.value,
        "information_need_relevance": lambda item: item.information_need_relevance_assessment.assessment_method.value,
    }
    by_dimension = {name: count_percent(evaluations, getter) for name, getter in dimensions.items()}
    fully_deterministic = 0
    at_least_one_llm = 0
    conflict = 0
    for item in evaluations:
        methods = [getter(item) for getter in dimensions.values()]
        if all(method == AssessmentMethod.DETERMINISTIC.value for method in methods):
            fully_deterministic += 1
        if any(method in {AssessmentMethod.LLM.value, AssessmentMethod.HYBRID.value} for method in methods):
            at_least_one_llm += 1
        if any("llm_conflict" in flag for flag in item.review_flags):
            conflict += 1
    return {
        "by_dimension": by_dimension,
        "fully_deterministic_candidates": fully_deterministic,
        "candidates_requiring_at_least_one_llm_judgment": at_least_one_llm,
        "candidates_with_deterministic_llm_conflict_flags": conflict,
    }


def entity_consistency_audit(evaluations: tuple[InitialSourceEvaluation, ...]) -> dict[str, Any]:
    by_entity: dict[str, list[InitialSourceEvaluation]] = defaultdict(list)
    for item in evaluations:
        by_entity[item.entity_id].append(item)
    multi = {entity_id: items for entity_id, items in by_entity.items() if len(items) > 1}
    contradictions = []
    for entity_id, items in multi.items():
        official_by_bundle = Counter(item.officiality_assessment.status.value for item in items)
        if len(official_by_bundle) > 2:
            contradictions.append(
                {
                    "entity_id": entity_id,
                    "officiality_statuses": dict(official_by_bundle),
                    "note": "multiple officiality classes across distinct source surfaces",
                }
            )
    return {
        "entities_with_multiple_sources": len(multi),
        "potential_contradictions": contradictions,
    }


def chinese_subset_summary(
    evaluations: tuple[InitialSourceEvaluation, ...],
    inspections: tuple[SourceInspection, ...],
    contexts: dict[str, Any],
) -> dict[str, Any]:
    chinese_ids = {
        item.candidate_source_id
        for item in inspections
        if is_chinese_inspection(item, contexts[item.candidate_source_id])
    }
    subset = tuple(item for item in evaluations if item.candidate_source_id in chinese_ids)
    return {
        "sample_count": len(subset),
        "entity_match": count_percent(subset, lambda item: item.entity_match_assessment.status.value),
        "officiality": count_percent(subset, lambda item: item.officiality_assessment.status.value),
        "page_type": count_percent(subset, lambda item: item.page_type_assessment.page_type.value),
        "durability": count_percent(subset, lambda item: item.surface_durability_assessment.status.value),
        "role_match": count_percent(subset, lambda item: item.source_role_assessment.source_role_match_status.value),
        "information_need_relevance": count_percent(subset, lambda item: item.information_need_relevance_assessment.relevance_level.value),
        "decision": count_percent(subset, lambda item: item.decision.value),
    }


def one_off_audit(evaluations: tuple[InitialSourceEvaluation, ...], contexts: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in evaluations:
        if item.surface_durability_assessment.status.value != "one_off_content":
            continue
        rows.append(
            {
                "candidate_source_id": item.candidate_source_id,
                "page_type": item.page_type_assessment.page_type.value,
                "planned_source_role": contexts[item.candidate_source_id].candidate.source_role.value,
                "information_need_relevance": item.information_need_relevance_assessment.relevance_level.value,
                "initial_decision": item.decision.value,
                "provenance_preserved": "one_off_content_not_durable_surface" in item.review_flags or item.decision == InitialEvaluationDecision.REJECTED,
            }
        )
    return rows


def needs_review_audit(evaluations: tuple[InitialSourceEvaluation, ...]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_source_id": item.candidate_source_id,
            "primary_reason": primary_review_reason(item),
            "flags": list(item.review_flags),
        }
        for item in evaluations
        if item.decision == InitialEvaluationDecision.NEEDS_REVIEW
    ]


def rejection_audit(evaluations: tuple[InitialSourceEvaluation, ...]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_source_id": item.candidate_source_id,
            "blocking_reason": primary_review_reason(item),
            "flags": list(item.review_flags),
        }
        for item in evaluations
        if item.decision == InitialEvaluationDecision.REJECTED
    ]


def proceed_audit(evaluations: tuple[InitialSourceEvaluation, ...], contexts: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_source_id": item.candidate_source_id,
            "entity_id": item.entity_id,
            "planned_source_role": contexts[item.candidate_source_id].candidate.source_role.value,
            "observed_source_role": item.source_role_assessment.observed_source_role.value,
            "information_need_ids": list(item.information_need_relevance_assessment.supported_information_need_ids),
            "confidence": item.evaluation_confidence.value,
        }
        for item in evaluations
        if item.decision == InitialEvaluationDecision.PROCEED_TO_OBSERVATION
    ]


def phase5e_population(
    evaluations: tuple[InitialSourceEvaluation, ...],
    inspections: tuple[SourceInspection, ...],
    contexts: dict[str, Any],
) -> dict[str, Any]:
    inspection_by_id = {item.candidate_source_id: item for item in inspections}
    proceed = tuple(
        item for item in evaluations if item.decision == InitialEvaluationDecision.PROCEED_TO_OBSERVATION
    )
    return {
        "proceed_to_observation_sources": len(proceed),
        "source_role_distribution": count_percent(proceed, lambda item: contexts[item.candidate_source_id].candidate.source_role.value),
        "entity_distribution": count_percent(proceed, lambda item: item.entity_id),
        "language_distribution": count_percent(proceed, lambda item: inspection_by_id[item.candidate_source_id].content_language or inspection_by_id[item.candidate_source_id].html_language or "unknown"),
        "durability_distribution": count_percent(proceed, lambda item: item.surface_durability_assessment.status.value),
        "information_need_coverage": dict(sorted(Counter(need_id for item in proceed for need_id in item.information_need_relevance_assessment.supported_information_need_ids).items())),
        "evaluation_confidence_distribution": count_percent(proceed, lambda item: item.evaluation_confidence.value),
    }


def smoke_audit_verdict(result) -> str:
    if result.invalid_output_count or result.failures:
        return "PHASE 5D NEEDS FIX BEFORE BROADER EVALUATION"
    if any(
        flag
        for item in result.evaluations
        for flag in item.review_flags
        if "llm_conflict" in flag
    ):
        return "PHASE 5D SMOKE PASSED WITH MINOR NOTES"
    return "PHASE 5D SMOKE PASSED"


def audit_result(evaluations: tuple[InitialSourceEvaluation, ...]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_source_id": item.candidate_source_id,
            "entity_match": item.entity_match_assessment.status.value,
            "officiality": item.officiality_assessment.status.value,
            "page_type": item.page_type_assessment.page_type.value,
            "durability": item.surface_durability_assessment.status.value,
            "observed_source_role": item.source_role_assessment.observed_source_role.value,
            "role_match": item.source_role_assessment.source_role_match_status.value,
            "information_need_relevance": item.information_need_relevance_assessment.relevance_level.value,
            "supported_information_need_ids": list(item.information_need_relevance_assessment.supported_information_need_ids),
            "evaluation_confidence": item.evaluation_confidence.value,
            "initial_monitoring_suitability": item.initial_monitoring_suitability.value,
            "decision": item.decision.value,
            "assessment_methods": {
                "entity_match": item.entity_match_assessment.assessment_method.value,
                "officiality": item.officiality_assessment.assessment_method.value,
                "page_type": item.page_type_assessment.assessment_method.value,
                "surface_durability": item.surface_durability_assessment.assessment_method.value,
                "source_role": item.source_role_assessment.assessment_method.value,
                "information_need_relevance": item.information_need_relevance_assessment.assessment_method.value,
            },
            "flags": list(item.review_flags),
        }
        for item in evaluations
    ]


def primary_review_reason(item: InitialSourceEvaluation) -> str:
    for flag in item.review_flags:
        if flag.startswith("entity_match_") or flag in {"entity_mismatch"}:
            return "entity_identity_uncertain"
        if flag.startswith("officiality_") or "officiality" in flag:
            return "officiality_uncertain"
        if flag.startswith("surface_durability_") or "durability" in flag:
            return "durability_uncertain"
        if "source_role" in flag:
            return "role_ambiguity"
        if "semantic_evidence" in flag:
            return "insufficient_semantic_evidence"
        if "client_rendering" in flag:
            return "client_rendering_limitation"
        if "conflict" in flag:
            return "conflicting_evidence"
        if "information_need" in flag or "relevance" in flag:
            return "information_need_relevance_uncertain"
    return "other_controlled_reason"


def is_chinese_inspection(inspection: SourceInspection, context: Any) -> bool:
    language_text = f"{inspection.content_language or ''} {inspection.html_language or ''} {context.candidate.language or ''}".casefold()
    if "zh" in language_text or "chinese" in language_text:
        return True
    sample = " ".join(
        [
            inspection.page_title or "",
            inspection.meta_description or "",
            " ".join(inspection.heading_summary[:5]),
            " ".join(window.text[:200] for window in inspection.semantic_text_windows[:2]),
        ]
    )
    return any("\u4e00" <= char <= "\u9fff" for char in sample)


def count_percent(items, getter) -> dict[str, dict[str, float | int]]:
    counts = Counter(getter(item) for item in items)
    total = sum(counts.values())
    return {
        key: {"count": value, "percent": round((value / total) * 100, 1) if total else 0}
        for key, value in sorted(counts.items())
    }


def percentile(values: list[int], percentile_value: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile_value / 100))
    return ordered[index]


def bundle_char_count(item: BuiltEvaluationInput) -> int:
    return len(json.dumps(item.prompt_bundle, ensure_ascii=False, sort_keys=True))


def snapshot_upstream_artifacts() -> dict[str, dict[str, Any]]:
    base = PROJECT_ROOT / "outputs" / "planning" / "source_monitoring"
    paths: list[Path] = [
        base / "information_needs.json",
        base / "entity_universe.json",
        base / "candidate_sources.json",
    ]
    paths.extend(sorted((base / "diagnostics" / "phase5_source_evaluation" / "inspections").glob("*/inspection.json")))
    paths.extend(sorted((base / "diagnostics" / "phase5_source_evaluation" / "raw_pages").glob("**/*")))
    paths.extend(sorted((base / "diagnostics" / "phase5_source_evaluation" / "broader_fetch_validation" / "raw_pages").glob("**/*")))
    return snapshot_files(path for path in paths if path.is_file())


def snapshot_files(paths) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted(paths, key=lambda item: str(item)):
        if not path.exists() or not path.is_file():
            continue
        stat = path.stat()
        result[str(path.relative_to(PROJECT_ROOT))] = {
            "sha256": read_bytes_digest(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return result


def read_bytes_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def result_signature(evaluations: tuple[InitialSourceEvaluation, ...]) -> str:
    return hash_payload([item.to_dict() for item in evaluations])


def hash_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_report(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Phase 5D Initial Source Evaluation",
        "",
        f"Branch: {summary['git']['branch']}",
        f"HEAD: {summary['git']['head']}",
        f"Provider/model: {summary['provider']} / {summary['model']}",
        f"Prompt/schema: {summary['prompt_version']} / {summary['llm_schema_version']}",
        "",
        "## Corpus",
        fenced(summary["corpus"]),
        "## Bundle Metrics",
        fenced(summary["bundle_metrics"]),
        "## Smoke Sample",
        fenced(summary["smoke"]),
        "## Broader Result",
        fenced(summary["broader"]),
        "## Distributions",
        fenced(summary["distributions"]),
        "## Method Metrics",
        fenced(summary["method_metrics"]),
        "## Chinese Subset",
        fenced(summary["chinese_subset"]),
        "## One-Off Audit",
        fenced(summary["one_off_audit"]),
        "## Needs-Review Audit",
        fenced(summary["needs_review_audit"]),
        "## Rejection Audit",
        fenced(summary["rejection_audit"]),
        "## Proceed-To-Observation Audit",
        fenced(summary["proceed_audit"]),
        "## Cache Replay",
        fenced(summary["cache_replay"]),
        "## Immutability",
        fenced(summary["immutability"]),
        "## Phase 5E Input Population",
        fenced(summary["phase5e_input_population"]),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fenced(payload: Any) -> str:
    return "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def git_summary() -> dict[str, str]:
    def run(*args: str) -> str:
        return subprocess.check_output(args, cwd=PROJECT_ROOT, text=True, encoding="utf-8").strip()

    return {
        "branch": run("git", "branch", "--show-current"),
        "head": run("git", "rev-parse", "HEAD"),
        "status_short": run("git", "status", "--short"),
    }


def condensed_stdout(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary_file": str(SUMMARY_FILE.relative_to(PROJECT_ROOT)),
        "report_file": str(REPORT_FILE.relative_to(PROJECT_ROOT)),
        "initial_evaluations_file": str(INITIAL_EVALUATION_RESULT_FILE.relative_to(PROJECT_ROOT)),
        "smoke_verdict": summary["smoke"]["verdict"],
        "corpus": summary["corpus"],
        "broader": summary["broader"],
        "cache_replay": summary["cache_replay"],
        "immutability": summary["immutability"],
        "output_hash": summary["output_hash"],
    }


if __name__ == "__main__":
    main()
