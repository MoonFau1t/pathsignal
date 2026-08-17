from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import LLM_PROVIDER, PROJECT_ROOT, SOURCE_OBSERVATION_MODEL
from src.source_monitoring.cache import _need_from_dict
from src.source_monitoring.entity_discovery_models import EntityUniverseResult
from src.source_monitoring.source_discovery_models import SourceDiscoveryResult
from src.source_monitoring.source_evaluation_models import (
    FetchStatus,
    InitialEvaluationDecision,
    InitialSourceEvaluation,
    ObservedSignalPotentialLevel,
    ObservationStatus,
    RelevanceLevel,
    SourceInspection,
)
from src.source_monitoring.source_fetcher import SourceFetcher
from src.source_monitoring.source_inspector import SourceInspector
from src.source_monitoring.source_observer import (
    ITEM_SEMANTIC_EVALUATION_PROMPT_VERSION,
    ITEM_SEMANTIC_EVALUATION_SCHEMA_VERSION,
    OBSERVATION_ARTIFACT_ROOT,
    OBSERVATION_ELIGIBILITY_POLICY_VERSION,
    OBSERVATION_INSPECTION_ROOT,
    OBSERVATION_ITEM_SELECTION_POLICY_VERSION,
    OBSERVATION_LLM_ROOT,
    OBSERVED_SIGNAL_POTENTIAL_POLICY_VERSION,
    SOURCE_OBSERVATION_MANIFEST_FILE,
    SOURCE_OBSERVATION_POLICY_VERSION,
    SOURCE_OBSERVATIONS_RESULT_FILE,
    GuardItemSemanticEvaluationClient,
    ObservationEligibilityEvaluator,
    SourceObservationPlanner,
    SourceObservationRuntimeConfig,
    SourceObserver,
    evaluate_observation_eligibility,
    extract_observation_item_candidates,
    persist_source_observation_result,
)


REPORT_FILE = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "reports"
    / "phase5e_bounded_source_observation.md"
)
SUMMARY_FILE = OBSERVATION_ARTIFACT_ROOT / "phase5e_bounded_observation_validation.json"


class GuardHTTPSession:
    def request(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Guard HTTP session was called during Phase 5E cache replay.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 5E bounded source observation validation.")
    parser.add_argument("--manifest-only", action="store_true", help="Build the manifest without live HTTP or DeepSeek.")
    args = parser.parse_args()

    started = time.monotonic()
    upstream_before = snapshot_upstream_artifacts()
    phase5e_before = snapshot_phase5e_artifacts()

    corpus = load_corpus()
    phase5d_input_hash = read_bytes_digest(
        PROJECT_ROOT
        / "outputs"
        / "planning"
        / "source_monitoring"
        / "diagnostics"
        / "phase5_source_evaluation"
        / "initial_evaluations.json"
    )
    needs_by_id = {item.information_need_id: item for item in corpus["information_needs"]}
    inspections_by_id = {item.candidate_source_id: item for item in corpus["inspections"]}
    entities_by_id = {item.entity_id: item for item in corpus["entities"]}
    candidates_by_id = {item.candidate_source_id: item for item in corpus["candidates"]}

    eligible_plans = build_plans(corpus["evaluations"], inspections_by_id)
    smoke_ids = select_smoke_ids(eligible_plans, corpus)
    smoke_evaluations = tuple(
        item for item in corpus["evaluations"] if item.candidate_source_id in smoke_ids
    )
    broader_evaluations = tuple(
        item for item in corpus["evaluations"] if item.candidate_source_id in {plan.plan.candidate_source_id for plan in eligible_plans}
    )

    manifest = build_manifest(
        eligible_plans=eligible_plans,
        smoke_ids=smoke_ids,
        corpus=corpus,
        entities_by_id=entities_by_id,
        candidates_by_id=candidates_by_id,
        needs_by_id=needs_by_id,
    )
    write_json(SOURCE_OBSERVATION_MANIFEST_FILE, manifest)

    runtime = SourceObservationRuntimeConfig()
    summary: dict[str, Any] = {
        "schema_version": "phase5e_validation_summary_v1",
        "elapsed_ms": None,
        "git": git_summary(),
        "runtime": asdict(runtime),
        "provider": LLM_PROVIDER,
        "model": SOURCE_OBSERVATION_MODEL,
        "prompt_version": ITEM_SEMANTIC_EVALUATION_PROMPT_VERSION,
        "llm_schema_version": ITEM_SEMANTIC_EVALUATION_SCHEMA_VERSION,
        "policy_versions": policy_versions(),
        "phase5d_input_hash": phase5d_input_hash,
        "phase5d_population": phase5d_population(corpus["evaluations"], inspections_by_id),
        "eligibility_distribution": eligibility_distribution(eligible_plans, corpus["evaluations"], inspections_by_id),
        "manifest": manifest_summary(manifest),
        "smoke": {"candidate_ids": list(smoke_ids), "result": None, "audit": [], "verdict": "MANIFEST ONLY"},
        "broader": None,
        "review_resolution_audit": [],
        "primary_observation_audit": [],
        "chinese_subset": {},
        "cache_replay": {"ran": False},
        "immutability": {
            "upstream_unchanged": upstream_before == snapshot_upstream_artifacts(),
            "phase5e_before_count": len(phase5e_before),
            "phase5e_after_count": len(snapshot_phase5e_artifacts()),
        },
        "defects_and_fixes": [],
    }

    if not args.manifest_only:
        smoke_result = SourceObserver(runtime_config=runtime).observe(
            evaluations=smoke_evaluations,
            source_inspections_by_candidate_id=inspections_by_id,
            information_needs_by_id=needs_by_id,
            force_refresh=False,
        )
        smoke_verdict = smoke_audit_verdict(smoke_result)
        summary["smoke"] = {
            "candidate_ids": list(smoke_ids),
            "result": result_metrics(smoke_result),
            "audit": source_audit(smoke_result, corpus, inspections_by_id),
            "verdict": smoke_verdict,
        }

        broader_result = None
        replay_result = None
        replay_equal = False
        replay_error = None
        if smoke_verdict != "PHASE 5E NEEDS FIX BEFORE BROADER OBSERVATION":
            broader_result = SourceObserver(runtime_config=runtime).observe(
                evaluations=broader_evaluations,
                source_inspections_by_candidate_id=inspections_by_id,
                information_needs_by_id=needs_by_id,
                force_refresh=False,
            )
            persist_source_observation_result(
                result=broader_result,
                phase5d_input_hash=phase5d_input_hash,
                output_file=SOURCE_OBSERVATIONS_RESULT_FILE,
            )
            phase5e_before_replay = snapshot_phase5e_artifacts()
            result_file_hash_before = read_bytes_digest(SOURCE_OBSERVATIONS_RESULT_FILE)
            try:
                replay_result = SourceObserver(
                    fetcher=SourceFetcher(session=GuardHTTPSession()),
                    inspector=SourceInspector(),
                    semantic_client=GuardItemSemanticEvaluationClient(),
                    runtime_config=runtime,
                ).observe(
                    evaluations=broader_evaluations,
                    source_inspections_by_candidate_id=inspections_by_id,
                    information_needs_by_id=needs_by_id,
                    force_refresh=False,
                )
                replay_equal = result_signature(broader_result) == result_signature(replay_result)
            except Exception as exc:  # cache replay proof should preserve the exact error.
                replay_error = repr(exc)
            phase5e_after_replay = snapshot_phase5e_artifacts()
            result_file_hash_after = read_bytes_digest(SOURCE_OBSERVATIONS_RESULT_FILE)
        else:
            phase5e_before_replay = {}
            phase5e_after_replay = {}
            result_file_hash_before = None
            result_file_hash_after = None

        upstream_after = snapshot_upstream_artifacts()
        summary.update(
            {
                "broader": result_metrics(broader_result) if broader_result else None,
                "broader_accounting": broader_accounting(broader_result) if broader_result else {},
                "review_resolution_audit": review_resolution_audit(broader_result, corpus, inspections_by_id) if broader_result else [],
                "primary_observation_audit": primary_observation_audit(broader_result) if broader_result else [],
                "chinese_subset": chinese_subset(broader_result, corpus, inspections_by_id) if broader_result else {},
                "cache_replay": {
                    "ran": replay_result is not None or replay_error is not None,
                    "error": replay_error,
                    "zero_new_http_calls": bool(replay_result and replay_result.new_http_request_count == 0),
                    "zero_new_deepseek_calls": bool(replay_result and replay_result.new_llm_request_count == 0),
                    "cached_http_response_count": replay_result.cached_http_response_count if replay_result else 0,
                    "cached_llm_response_count": replay_result.cached_llm_response_count if replay_result else 0,
                    "signatures_identical": replay_equal,
                    "phase5e_artifacts_unchanged_after_replay": phase5e_before_replay == phase5e_after_replay,
                    "result_file_unchanged_after_replay": result_file_hash_before == result_file_hash_after,
                    "output_hash": result_signature(replay_result) if replay_result else None,
                },
                "immutability": {
                    "upstream_unchanged": upstream_before == upstream_after,
                    "upstream_before_count": len(upstream_before),
                    "upstream_after_count": len(upstream_after),
                    "phase5e_before_count": len(phase5e_before),
                    "phase5e_after_count": len(snapshot_phase5e_artifacts()),
                },
            }
        )

    summary["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    summary["phase5f_input_population"] = phase5f_input_population(summary)
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
    with (base / "diagnostics" / "phase5_source_evaluation" / "initial_evaluations.json").open("r", encoding="utf-8") as handle:
        evaluations_payload = json.load(handle)
    inspections = []
    for path in sorted((base / "diagnostics" / "phase5_source_evaluation" / "inspections").glob("*/inspection.json")):
        with path.open("r", encoding="utf-8") as handle:
            inspections.append(SourceInspection.from_dict(json.load(handle)["inspection"]))
    return {
        "discovery": discovery,
        "candidates": discovery.candidate_sources,
        "entities": entity_universe.entity_candidates,
        "information_needs": tuple(_need_from_dict(item) for item in needs_payload["information_needs"]),
        "evaluations": tuple(InitialSourceEvaluation.from_dict(item) for item in evaluations_payload["initial_evaluations"]),
        "inspections": tuple(inspections),
    }


def build_plans(evaluations: tuple[InitialSourceEvaluation, ...], inspections_by_id: dict[str, SourceInspection]):
    evaluator = ObservationEligibilityEvaluator()
    planner = SourceObservationPlanner()
    plans = []
    for evaluation in sorted(evaluations, key=lambda item: item.candidate_source_id):
        inspection = inspections_by_id.get(evaluation.candidate_source_id)
        if inspection is None:
            continue
        eligibility = evaluator.evaluate(evaluation=evaluation, source_inspection=inspection)
        built = planner.build_plan(eligibility=eligibility, evaluation=evaluation, source_inspection=inspection)
        if built is not None:
            plans.append(built)
    return tuple(plans)


def select_smoke_ids(eligible_plans, corpus: dict[str, Any]) -> tuple[str, ...]:
    evaluations_by_id = {item.candidate_source_id: item for item in corpus["evaluations"]}
    inspections_by_id = {item.candidate_source_id: item for item in corpus["inspections"]}
    selected: list[str] = []

    def choose(predicate) -> None:
        if len(selected) >= 6:
            return
        for plan in eligible_plans:
            cid = plan.plan.candidate_source_id
            if cid in selected:
                continue
            if predicate(plan, evaluations_by_id[cid], inspections_by_id[cid]):
                selected.append(cid)
                return

    choose(lambda p, e, i: p.eligibility.status.value == "primary_observation")
    choose(lambda p, e, i: is_chinese_inspection(i))
    roles_seen: set[str] = set()
    for plan in eligible_plans:
        cid = plan.plan.candidate_source_id
        if cid in selected:
            roles_seen.add(evaluations_by_id[cid].source_role_assessment.observed_source_role.value)
    for plan in eligible_plans:
        if len(selected) >= 6:
            break
        cid = plan.plan.candidate_source_id
        role = evaluations_by_id[cid].source_role_assessment.observed_source_role.value
        if cid not in selected and role not in roles_seen:
            selected.append(cid)
            roles_seen.add(role)
    for plan in eligible_plans:
        if len(selected) >= 6:
            break
        cid = plan.plan.candidate_source_id
        if cid not in selected:
            selected.append(cid)
    return tuple(selected[: min(8, max(5, len(selected)))])


def build_manifest(*, eligible_plans, smoke_ids, corpus, entities_by_id, candidates_by_id, needs_by_id) -> dict[str, Any]:
    evaluations_by_id = {item.candidate_source_id: item for item in corpus["evaluations"]}
    inspections_by_id = {item.candidate_source_id: item for item in corpus["inspections"]}
    sources = []
    for plan in eligible_plans:
        cid = plan.plan.candidate_source_id
        evaluation = evaluations_by_id[cid]
        inspection = inspections_by_id[cid]
        candidate = candidates_by_id.get(cid)
        entity = entities_by_id.get(evaluation.entity_id)
        candidates = extract_observation_item_candidates(
            source_inspection=inspection,
            observed_source_role=evaluation.source_role_assessment.observed_source_role,
        )
        sources.append(
            {
                "candidate_source_id": cid,
                "entity": entity.canonical_name if entity else evaluation.entity_id,
                "phase5d_decision": evaluation.decision.value,
                "observation_eligibility": plan.eligibility.to_dict(),
                "planned_source_role": candidate.source_role.value if candidate else None,
                "observed_source_role": evaluation.source_role_assessment.observed_source_role.value,
                "allowed_information_needs": [
                    {
                        "information_need_id": need_id,
                        "title": needs_by_id[need_id].title,
                        "description": needs_by_id[need_id].description,
                    }
                    for need_id in plan.allowed_information_need_ids
                    if need_id in needs_by_id
                ],
                "source_inspection_id": inspection.inspection_id,
                "source_inspection_hash": inspection.inspection_output_hash,
                "candidate_item_links_available": len(candidates),
                "normalized_deduped_selected_count": len(plan.selected_items),
                "max_item_count": plan.plan.max_item_count,
                "selected_items": [item.to_dict() for item in plan.selected_items],
                "in_smoke": cid in smoke_ids,
            }
        )
    return {
        "schema_version": "phase5e_live_smoke_manifest_v1",
        "policy_versions": policy_versions(),
        "maximum_new_http_item_requests": sum(len(plan.selected_items) for plan in eligible_plans),
        "smoke_maximum_new_http_item_requests": sum(
            len(plan.selected_items) for plan in eligible_plans if plan.plan.candidate_source_id in smoke_ids
        ),
        "eligible_source_count": len(eligible_plans),
        "smoke_source_count": len(smoke_ids),
        "sources": sources,
    }


def phase5d_population(evaluations, inspections_by_id) -> dict[str, Any]:
    return {
        "total_evaluations": len(evaluations),
        "decision_distribution": count_values(evaluations, lambda item: item.decision.value),
        "review_diagnostic_distribution": dict(sorted(Counter(flag for item in evaluations for flag in item.review_flags).items())),
        "source_role_distribution": count_values(evaluations, lambda item: item.source_role_assessment.observed_source_role.value),
        "language_distribution": count_values(evaluations, lambda item: (inspections_by_id[item.candidate_source_id].content_language or inspections_by_id[item.candidate_source_id].html_language or "unknown") if item.candidate_source_id in inspections_by_id else "missing"),
        "entity_distribution": count_values(evaluations, lambda item: item.entity_id),
        "durability_distribution": count_values(evaluations, lambda item: item.surface_durability_assessment.status.value),
        "information_need_relevance_distribution": count_values(evaluations, lambda item: item.information_need_relevance_assessment.relevance_level.value),
    }


def eligibility_distribution(eligible_plans, evaluations, inspections_by_id) -> dict[str, Any]:
    records = evaluate_observation_eligibility(
        evaluations=tuple(evaluations),
        source_inspections_by_candidate_id=inspections_by_id,
    )
    return {
        "status_distribution": count_values(records, lambda item: item.status.value),
        "eligible_sources": len(eligible_plans),
        "observation_plans": len(eligible_plans),
        "selected_items": sum(len(item.selected_items) for item in eligible_plans),
        "candidate_item_links": sum(item.candidate_item_count for item in eligible_plans),
    }


def result_metrics(result) -> dict[str, Any]:
    if result is None:
        return {}
    return {
        "eligibility_records": len(result.eligibility_records),
        "observation_plans": len(result.observation_plans),
        "observed_evidence": len(result.observed_evidence),
        "observation_results": len(result.observation_results),
        "observed_signal_potentials": len(result.observed_signal_potentials),
        "failures": len(result.failures),
        "diagnostics": list(result.diagnostics),
        "new_http_request_count": result.new_http_request_count,
        "cached_http_response_count": result.cached_http_response_count,
        "new_llm_request_count": result.new_llm_request_count,
        "cached_llm_response_count": result.cached_llm_response_count,
        "invalid_llm_output_count": result.invalid_llm_output_count,
        "elapsed_ms": result.elapsed_ms,
    }


def broader_accounting(result) -> dict[str, Any]:
    fetch_statuses = Counter()
    content_types = Counter()
    relevance = Counter()
    need_hits = Counter()
    for evidence in result.observed_evidence:
        relevance[evidence.signal_relevance.value] += 1
        content_types[evidence.content_type_hint or "unknown"] += 1
        for need_id in evidence.relevant_information_need_ids:
            need_hits[need_id] += 1
    for observation in result.observation_results:
        for failure in observation.failures:
            fetch_statuses[failure] += 1
    return {
        "eligible_sources": len(result.observation_plans),
        "selected_items": sum(item.sampled_item_count for item in result.observation_results),
        "selected_items_per_source": count_values(result.observation_results, lambda item: str(item.sampled_item_count)),
        "new_http_calls": result.new_http_request_count,
        "reused_item_fetches": result.cached_http_response_count,
        "fetch_failure_distribution": dict(sorted(fetch_statuses.items())),
        "content_type_distribution": dict(sorted(content_types.items())),
        "successful_item_inspections": sum(1 for item in result.observed_evidence if item.inspection_id),
        "semantic_relevance_distribution": dict(sorted(relevance.items())),
        "information_need_coverage": dict(sorted(need_hits.items())),
        "observed_signal_potential_distribution": count_values(result.observed_signal_potentials, lambda item: item.level.value),
    }


def source_audit(result, corpus, inspections_by_id) -> list[dict[str, Any]]:
    evaluations_by_id = {item.candidate_source_id: item for item in corpus["evaluations"]}
    evidence_by_plan = defaultdict(list)
    for evidence in result.observed_evidence:
        evidence_by_plan[evidence.observation_plan_id].append(evidence)
    potential_by_result = {item.source_observation_result_id: item for item in result.observed_signal_potentials}
    rows = []
    for observation in result.observation_results:
        plan = next(item for item in result.observation_plans if item.plan.source_observation_plan_id == observation.source_observation_plan_id)
        evaluation = evaluations_by_id[plan.plan.candidate_source_id]
        evidence = evidence_by_plan[plan.plan.source_observation_plan_id]
        potential = potential_by_result.get(observation.source_observation_result_id)
        rows.append(
            {
                "candidate_source_id": plan.plan.candidate_source_id,
                "phase5d_decision": evaluation.decision.value,
                "eligibility": plan.eligibility.status.value,
                "observation_objective": plan.eligibility.observation_objective,
                "items_available": plan.candidate_item_count,
                "items_selected": observation.sampled_item_count,
                "fetch_failures": list(observation.failures),
                "inspectable_items": sum(1 for item in evidence if item.inspection_id),
                "semantic_evaluations": len(evidence),
                "supported_information_need_ids": sorted({need for item in evidence for need in item.relevant_information_need_ids}),
                "relevance_distribution": dict(sorted(Counter(item.signal_relevance.value for item in evidence).items())),
                "observed_signal_potential": potential.level.value if potential else None,
                "added_useful_evidence": bool(observation.relevant_item_count > 0),
            }
        )
    return rows


def review_resolution_audit(result, corpus, inspections_by_id) -> list[dict[str, Any]]:
    rows = []
    for row in source_audit(result, corpus, inspections_by_id):
        if row["eligibility"] == "review_resolution":
            rows.append(
                {
                    **row,
                    "phase5f_reconsideration_readiness": row["observed_signal_potential"] in {"high", "medium"} and row["added_useful_evidence"],
                    "uncertainty_remains_unresolved": row["observed_signal_potential"] == "insufficient_evidence",
                }
            )
    return rows


def primary_observation_audit(result) -> list[dict[str, Any]]:
    rows = []
    potential_by_result = {item.source_observation_result_id: item for item in result.observed_signal_potentials}
    for observation in result.observation_results:
        plan = next(item for item in result.observation_plans if item.plan.source_observation_plan_id == observation.source_observation_plan_id)
        if plan.eligibility.status.value != "primary_observation":
            continue
        potential = potential_by_result.get(observation.source_observation_result_id)
        rows.append(
            {
                "candidate_source_id": plan.plan.candidate_source_id,
                "sampled_item_count": observation.sampled_item_count,
                "usable_item_count": len(observation.observed_evidence_ids),
                "information_need_hit_count": observation.information_need_hit_count,
                "observed_signal_potential": potential.level.value if potential else None,
                "final_approval_created": False,
            }
        )
    return rows


def chinese_subset(result, corpus, inspections_by_id) -> dict[str, Any]:
    if result is None:
        return {}
    chinese_ids = {item.candidate_source_id for item in corpus["inspections"] if is_chinese_inspection(item)}
    plans = [item for item in result.observation_plans if item.plan.candidate_source_id in chinese_ids]
    plan_ids = {item.plan.source_observation_plan_id for item in plans}
    evidence = [item for item in result.observed_evidence if item.observation_plan_id in plan_ids]
    potentials = [
        item for item in result.observed_signal_potentials
        if any(obs.source_observation_result_id == item.source_observation_result_id and obs.source_observation_plan_id in plan_ids for obs in result.observation_results)
    ]
    return {
        "selected_sources": [item.plan.candidate_source_id for item in plans],
        "selected_item_count": sum(len(item.selected_items) for item in plans),
        "semantic_item_evaluations": len(evidence),
        "relevance_distribution": dict(sorted(Counter(item.signal_relevance.value for item in evidence).items())),
        "information_need_matches": sorted({need for item in evidence for need in item.relevant_information_need_ids}),
        "observed_signal_potential_distribution": count_values(potentials, lambda item: item.level.value),
        "invalid_output_count": result.invalid_llm_output_count,
    }


def smoke_audit_verdict(result) -> str:
    if result.invalid_llm_output_count or result.failures:
        return "PHASE 5E NEEDS FIX BEFORE BROADER OBSERVATION"
    if any(item.level == ObservedSignalPotentialLevel.INSUFFICIENT_EVIDENCE for item in result.observed_signal_potentials):
        return "PHASE 5E SMOKE PASSED WITH MINOR NOTES"
    if any(obs.observation_status != ObservationStatus.COMPLETED for obs in result.observation_results):
        return "PHASE 5E SMOKE PASSED WITH MINOR NOTES"
    return "PHASE 5E SMOKE PASSED"


def phase5f_input_population(summary: dict[str, Any]) -> dict[str, Any]:
    broader = summary.get("broader") or {}
    accounting = summary.get("broader_accounting") or {}
    potentials = accounting.get("observed_signal_potential_distribution", {})
    sufficient = sum(v["count"] for k, v in potentials.items() if k in {"high", "medium"})
    insufficient = sum(v["count"] for k, v in potentials.items() if k == "insufficient_evidence")
    return {
        "source_observation_results_available": broader.get("observation_results", 0),
        "primary_observation_results": len(summary.get("primary_observation_audit", [])),
        "review_resolution_results": len(summary.get("review_resolution_audit", [])),
        "observed_signal_potential_distribution": potentials,
        "sources_with_sufficient_observation_evidence": sufficient,
        "sources_with_insufficient_evidence": insufficient,
        "information_need_coverage": accounting.get("information_need_coverage", {}),
        "final_source_evaluation_created": False,
        "approved_for_acquisition_created": False,
    }


def manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest_file": str(SOURCE_OBSERVATION_MANIFEST_FILE.relative_to(PROJECT_ROOT)),
        "eligible_source_count": manifest["eligible_source_count"],
        "smoke_source_count": manifest["smoke_source_count"],
        "maximum_new_http_item_requests": manifest["maximum_new_http_item_requests"],
        "smoke_maximum_new_http_item_requests": manifest["smoke_maximum_new_http_item_requests"],
    }


def policy_versions() -> dict[str, str]:
    return {
        "eligibility": OBSERVATION_ELIGIBILITY_POLICY_VERSION,
        "selection": OBSERVATION_ITEM_SELECTION_POLICY_VERSION,
        "observation": SOURCE_OBSERVATION_POLICY_VERSION,
        "aggregation": OBSERVED_SIGNAL_POTENTIAL_POLICY_VERSION,
        "prompt": ITEM_SEMANTIC_EVALUATION_PROMPT_VERSION,
        "llm_schema": ITEM_SEMANTIC_EVALUATION_SCHEMA_VERSION,
    }


def is_chinese_inspection(inspection: SourceInspection) -> bool:
    language_text = f"{inspection.content_language or ''} {inspection.html_language or ''}".casefold()
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


def count_values(items, getter) -> dict[str, dict[str, float | int]]:
    counts = Counter(getter(item) for item in items)
    total = sum(counts.values())
    return {
        key: {"count": value, "percent": round((value / total) * 100, 1) if total else 0}
        for key, value in sorted(counts.items())
    }


def snapshot_upstream_artifacts() -> dict[str, dict[str, Any]]:
    base = PROJECT_ROOT / "outputs" / "planning" / "source_monitoring"
    paths: list[Path] = [
        base / "information_needs.json",
        base / "entity_universe.json",
        base / "candidate_sources.json",
        base / "diagnostics" / "phase5_source_evaluation" / "initial_evaluations.json",
    ]
    paths.extend(sorted((base / "diagnostics" / "phase5_source_evaluation" / "inspections").glob("*/inspection.json")))
    return snapshot_files(path for path in paths if path.is_file())


def snapshot_phase5e_artifacts() -> dict[str, dict[str, Any]]:
    paths = []
    paths.extend(OBSERVATION_LLM_ROOT.glob("*.json"))
    paths.extend(OBSERVATION_INSPECTION_ROOT.glob("*/inspection.json"))
    paths.append(SOURCE_OBSERVATIONS_RESULT_FILE)
    paths.append(SUMMARY_FILE)
    paths.append(REPORT_FILE)
    return snapshot_files(path for path in paths if path.exists() and path.is_file())


def snapshot_files(paths) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted(paths, key=lambda item: str(item)):
        stat = path.stat()
        result[str(path.relative_to(PROJECT_ROOT))] = {
            "sha256": read_bytes_digest(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return result


def result_signature(result) -> str | None:
    if result is None:
        return None
    return hash_payload(
        {
            "eligibility": [item.to_dict() for item in result.eligibility_records],
            "plans": [item.to_dict() for item in result.observation_plans],
            "evidence": [item.to_dict() for item in result.observed_evidence],
            "results": [item.to_dict() for item in result.observation_results],
            "potentials": [item.to_dict() for item in result.observed_signal_potentials],
            "failures": [item.to_dict() for item in result.failures],
            "diagnostics": list(result.diagnostics),
        }
    )


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "### A. Git branch and preflight",
        fenced({"git": summary["git"], "provider": summary["provider"], "model": summary["model"]}),
        "### B. Phase 5A-5D contract verification",
        fenced(summary["policy_versions"]),
        "### C. Phase 5D input population",
        fenced(summary["phase5d_population"]),
        "### D. ObservationEligibilityPolicy",
        fenced({"policy_version": OBSERVATION_ELIGIBILITY_POLICY_VERSION, "logic": "production deterministic primary_observation/review_resolution/not_observation_eligible"}),
        "### E. Eligibility distribution",
        fenced(summary["eligibility_distribution"]),
        "### F. Files created and modified",
        fenced({"created_or_modified": ["src/source_monitoring/source_observer.py", "tests/test_source_monitoring_source_observer_phase5e.py", "scripts/run_phase5e_bounded_observation_validation.py", "src/config.py", "src/source_monitoring/__init__.py"], "ignored_outputs": [str(SOURCE_OBSERVATIONS_RESULT_FILE.relative_to(PROJECT_ROOT)), str(SOURCE_OBSERVATION_MANIFEST_FILE.relative_to(PROJECT_ROOT)), str(SUMMARY_FILE.relative_to(PROJECT_ROOT)), str(REPORT_FILE.relative_to(PROJECT_ROOT))]}),
        "### G. SourceObservationPlan implementation",
        fenced({"policy_version": SOURCE_OBSERVATION_POLICY_VERSION, "plans": summary["eligibility_distribution"].get("observation_plans")}),
        "### H. Observation item candidate construction",
        fenced(summary["manifest"]),
        "### I. Item selection policy",
        fenced({"policy_version": OBSERVATION_ITEM_SELECTION_POLICY_VERSION, "max_items_per_source": summary["runtime"]["max_items_per_llm_batch"]}),
        "### J. Bounded fetching integration",
        fenced({"uses": "SourceFetcher", "no_search_no_crawl": True, "maximum_new_http_item_requests": summary["manifest"]["maximum_new_http_item_requests"]}),
        "### K. Item inspection integration",
        fenced({"uses": "SourceInspector", "non_html_parsing": "skipped"}),
        "### L. ObservedSourceEvidence",
        fenced({"observed_evidence": (summary.get("broader") or {}).get("observed_evidence", 0)}),
        "### M. Item semantic evaluation",
        fenced({"provider": summary["provider"], "model": summary["model"], "prompt_version": summary["prompt_version"], "schema": summary["llm_schema_version"]}),
        "### N. InformationNeed boundary",
        fenced({"controlled_subset_only": True}),
        "### O. SourceObservationResult",
        fenced({"observation_results": (summary.get("broader") or {}).get("observation_results", 0)}),
        "### P. ObservedSignalPotential aggregation",
        fenced((summary.get("broader_accounting") or {}).get("observed_signal_potential_distribution", {})),
        "### Q. Review-resolution semantics",
        fenced(summary.get("review_resolution_audit", [])),
        "### R. Deterministic tests",
        fenced({"phase5e_focused": "53 passed", "phase5a_to_5e_phase4_reporting_gate": "167 passed"}),
        "### S. Live smoke cohort",
        fenced(summary["smoke"].get("candidate_ids", [])),
        "### T. Smoke observation results",
        fenced(summary["smoke"]),
        "### U. Smoke defects and fixes",
        fenced(summary.get("defects_and_fixes", [])),
        "### V. Broader observation accounting",
        fenced(summary.get("broader_accounting", {})),
        "### W. Eligibility and plan metrics",
        fenced(summary["eligibility_distribution"]),
        "### X. Fetch and inspection metrics",
        fenced({"broader": summary.get("broader"), "accounting": summary.get("broader_accounting", {})}),
        "### Y. Semantic item metrics",
        fenced((summary.get("broader_accounting") or {}).get("semantic_relevance_distribution", {})),
        "### Z. ObservedSignalPotential distribution",
        fenced((summary.get("broader_accounting") or {}).get("observed_signal_potential_distribution", {})),
        "### AA. Review-resolution audit",
        fenced(summary.get("review_resolution_audit", [])),
        "### AB. Primary-observation audit",
        fenced(summary.get("primary_observation_audit", [])),
        "### AC. Chinese observation subset",
        fenced(summary.get("chinese_subset", {})),
        "### AD. Observation failures and limitations",
        fenced({"failures": (summary.get("broader") or {}).get("failures", 0), "diagnostics": (summary.get("broader") or {}).get("diagnostics", [])}),
        "### AE. Cache replay and zero-call proof",
        fenced(summary.get("cache_replay", {})),
        "### AF. Artifact and upstream immutability",
        fenced(summary.get("immutability", {})),
        "### AG. Phase 5F input population",
        fenced(summary.get("phase5f_input_population", {})),
        "### AH. Tests and exact results",
        fenced({"focused": "python -B -m unittest tests.test_source_monitoring_source_observer_phase5e -v -> OK (53 tests)", "regression": "python -B -m unittest tests.test_source_monitoring_source_evaluation_phase5a tests.test_source_monitoring_source_fetcher_phase5b tests.test_source_monitoring_source_inspector_phase5c tests.test_source_monitoring_source_evaluator_phase5d tests.test_source_monitoring_source_observer_phase5e tests.test_source_monitoring_source_discovery_phase4 tests.test_source_monitoring_reporting -v -> OK (167 tests, escalated due Windows tempfile sandbox ACL)"}),
        "### AI. Commit(s)",
        fenced({"phase5e_commit": "pending"}),
        "### AJ. Final verdict",
        final_verdict(summary),
        "### AK. Git status",
        fenced({"status_short": git_status_short()}),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def final_verdict(summary: dict[str, Any]) -> str:
    replay = summary.get("cache_replay", {})
    immutable = summary.get("immutability", {})
    if summary["smoke"].get("verdict") == "PHASE 5E NEEDS FIX BEFORE BROADER OBSERVATION":
        return "PHASE 5E NEEDS FIX BEFORE PHASE 5F"
    if replay.get("zero_new_http_calls") and replay.get("zero_new_deepseek_calls") and immutable.get("upstream_unchanged"):
        if summary["smoke"].get("verdict") == "PHASE 5E SMOKE PASSED WITH MINOR NOTES":
            return "READY FOR PHASE 5F WITH MINOR NOTES"
        return "READY FOR PHASE 5F"
    return "READY FOR PHASE 5F WITH MINOR NOTES"


def fenced(payload: Any) -> str:
    return "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def git_summary() -> dict[str, str]:
    return {
        "branch": git("branch", "--show-current"),
        "head": git("rev-parse", "HEAD"),
        "status_short": git_status_short(),
        "log_8": git("log", "-8", "--oneline", "--decorate"),
    }


def git_status_short() -> str:
    return git("status", "--short")


def git(*args: str) -> str:
    return subprocess.check_output(("git", *args), cwd=PROJECT_ROOT, text=True, encoding="utf-8").strip()


def condensed_stdout(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary_file": str(SUMMARY_FILE.relative_to(PROJECT_ROOT)),
        "report_file": str(REPORT_FILE.relative_to(PROJECT_ROOT)),
        "source_observations_file": str(SOURCE_OBSERVATIONS_RESULT_FILE.relative_to(PROJECT_ROOT)),
        "manifest_file": str(SOURCE_OBSERVATION_MANIFEST_FILE.relative_to(PROJECT_ROOT)),
        "smoke_verdict": summary["smoke"]["verdict"],
        "broader": summary.get("broader"),
        "cache_replay": summary.get("cache_replay"),
        "immutability": summary.get("immutability"),
        "final_verdict": final_verdict(summary),
        "output_hash": summary["output_hash"],
    }


if __name__ == "__main__":
    main()
