from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import PROJECT_ROOT, SOURCE_INSPECTION_MAX_CANDIDATES
from src.database.planning_identity import hash_canonical_value
from src.source_monitoring.cache import _need_from_dict
from src.source_monitoring.entity_discovery_models import EntityUniverseResult
from src.source_monitoring.source_discovery_models import (
    CandidateSource,
    CandidateSourceStatus,
    SourceDiscoveryResult,
)
from src.source_monitoring.source_evaluation_identity import build_source_evaluation_plan_id
from src.source_monitoring.source_evaluation_models import (
    EvaluationScope,
    SourceEvaluationPlan,
    SourceFetchExecution,
)
from src.source_monitoring.source_evaluator import build_phase5d_inputs
from src.source_monitoring.source_fetcher import (
    SOURCE_FETCH_ARTIFACT_ROOT,
    SOURCE_FETCH_FAILURE_ROOT,
    SourceFetcher,
    execute_source_fetch_requests,
)
from src.source_monitoring.source_inspector import (
    INSPECTION_ARTIFACT_ROOT,
    inspect_source_pages,
    persist_inspection_checkpoint,
)
from src.source_monitoring.source_role_ontology import SOURCE_ROLE_ONTOLOGY_VERSION


SUMMARY_FILE = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase5_source_evaluation"
    / "phase5c_inspection_validation.json"
)
REPORT_FILE = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "reports"
    / "phase5c_deterministic_inspection_validation.md"
)


@dataclass(frozen=True)
class _CandidateInspectionAdapter:
    candidate_source_id: str


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 5C source inspection validation.")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Load and select the corpus without fetching or writing inspection artifacts.",
    )
    args = parser.parse_args()

    started = time.monotonic()
    corpus = load_corpus()
    eligible = eligible_candidate_sources(corpus["discovery"])
    selected = select_source_inspection_corpus(
        eligible,
        max_candidates=SOURCE_INSPECTION_MAX_CANDIDATES,
    )
    contexts = build_source_inspection_contexts(corpus=corpus, selected=selected)
    fetch_items = build_fetch_items(selected=selected, contexts=contexts)

    if args.local_only:
        summary = summary_payload(
            started=started,
            eligible_count=len(eligible),
            selected=selected,
            fetch_items=fetch_items,
            fetch_executions=(),
            inspectable_count=0,
            checkpoint_count=0,
            local_only=True,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return

    outcomes = execute_source_fetch_corpus(
        requests_to_execute=fetch_items,
        fetcher=SourceFetcher(),
    )
    fetch_executions = tuple(outcome.execution for outcome in outcomes)
    inspection_outcomes = inspect_source_pages(fetch_executions=fetch_executions)
    checkpoints = tuple(
        path
        for path in (
            persist_inspection_checkpoint(outcome=outcome)
            for outcome in inspection_outcomes
        )
        if path is not None
    )
    summary = summary_payload(
        started=started,
        eligible_count=len(eligible),
        selected=selected,
        fetch_items=fetch_items,
        fetch_executions=fetch_executions,
        inspectable_count=sum(outcome.inspectable for outcome in inspection_outcomes),
        checkpoint_count=len(checkpoints),
        local_only=False,
    )
    write_json(SUMMARY_FILE, summary)
    write_report(REPORT_FILE, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def load_corpus() -> dict[str, Any]:
    base = PROJECT_ROOT / "outputs" / "planning" / "source_monitoring"
    with (base / "candidate_sources.json").open("r", encoding="utf-8") as handle:
        discovery = SourceDiscoveryResult.from_dict(json.load(handle))
    with (base / "entity_universe.json").open("r", encoding="utf-8") as handle:
        entity_universe = EntityUniverseResult.from_dict(json.load(handle))
    with (base / "information_needs.json").open("r", encoding="utf-8") as handle:
        needs_payload = json.load(handle)
    return {
        "discovery": discovery,
        "entity_universe": entity_universe,
        "information_needs": tuple(
            _need_from_dict(item) for item in needs_payload["information_needs"]
        ),
    }


def eligible_candidate_sources(
    discovery: SourceDiscoveryResult,
) -> tuple[tuple[CandidateSource, CandidateSourceStatus], ...]:
    return tuple(
        (item, CandidateSourceStatus.ACCEPTED)
        for item in discovery.candidate_sources
    ) + tuple(
        (item, CandidateSourceStatus.NEEDS_REVIEW)
        for item in discovery.needs_review_candidates
    )


def select_source_inspection_corpus(
    candidates: tuple[tuple[CandidateSource, CandidateSourceStatus], ...],
    *,
    max_candidates: int = SOURCE_INSPECTION_MAX_CANDIDATES,
) -> tuple[tuple[CandidateSource, CandidateSourceStatus], ...]:
    if max_candidates < 0:
        raise ValueError("SOURCE_INSPECTION_MAX_CANDIDATES must be non-negative.")
    return tuple(candidates[:max_candidates])


def build_source_inspection_contexts(
    *,
    corpus: dict[str, Any],
    selected: tuple[tuple[CandidateSource, CandidateSourceStatus], ...],
):
    adapters = tuple(
        _CandidateInspectionAdapter(candidate.candidate_source_id)
        for candidate, _status in selected
    )
    accepted = tuple(
        candidate
        for candidate, status in selected
        if status == CandidateSourceStatus.ACCEPTED
    )
    needs_review = tuple(
        candidate
        for candidate, status in selected
        if status == CandidateSourceStatus.NEEDS_REVIEW
    )
    return build_phase5d_inputs(
        inspections=adapters,
        candidates=accepted,
        needs_review_candidates=needs_review,
        entities=corpus["entity_universe"].entity_candidates,
        information_needs=corpus["information_needs"],
        phase4_input_fingerprint=corpus["discovery"].input_fingerprint,
        phase4_output_hash=corpus["discovery"].output_hash,
    )


def build_fetch_items(
    *,
    selected: tuple[tuple[CandidateSource, CandidateSourceStatus], ...],
    contexts,
):
    fetcher = SourceFetcher()
    items = []
    for candidate, _status in selected:
        context = contexts.get(candidate.candidate_source_id)
        if context is None:
            continue
        plan = build_source_inspection_plan(context=context)
        items.append(
            (
                fetcher.build_request(candidate.normalized_url or candidate.canonical_url),
                plan.source_evaluation_plan_id,
                candidate.candidate_source_id,
            )
        )
    return tuple(items)


def execute_source_fetch_corpus(*, requests_to_execute, fetcher) -> tuple:
    batch_size = max(0, int(fetcher.policy.batch_size))
    if batch_size == 0:
        return ()
    outcomes = []
    for start in range(0, len(requests_to_execute), batch_size):
        outcomes.extend(
            execute_source_fetch_requests(
                requests_to_execute=tuple(requests_to_execute[start : start + batch_size]),
                fetcher=fetcher,
            )
        )
    return tuple(outcomes)


def build_source_inspection_plan(*, context) -> SourceEvaluationPlan:
    candidate = context.candidate
    input_fingerprint = hash_canonical_value(
        {
            "schema_version": "source_evaluation_plan_input_phase5c_v1",
            "candidate": candidate.to_dict(),
            "phase4_status": context.phase4_status.value,
            "allowed_information_need_ids": context.allowed_information_need_ids,
        }
    )
    plan_id = build_source_evaluation_plan_id(
        candidate_source_id=candidate.candidate_source_id,
        entity_id=candidate.entity_id,
        candidate_url=candidate.normalized_url or candidate.canonical_url,
        planned_source_role=candidate.source_role,
        phase4_candidate_status=context.phase4_status.value,
        input_fingerprint=input_fingerprint,
    )
    return SourceEvaluationPlan.from_candidate_source(
        candidate=candidate,
        phase4_candidate_status=context.phase4_status,
        allowed_information_need_ids=context.allowed_information_need_ids,
        source_evaluation_plan_id=plan_id,
        input_fingerprint=input_fingerprint,
        phase4_input_fingerprint=context.phase4_input_fingerprint,
        phase4_output_hash=context.phase4_output_hash,
        source_role_ontology_version=SOURCE_ROLE_ONTOLOGY_VERSION,
        candidate_priority_rank=context.candidate_priority_rank,
        evaluation_scope=EvaluationScope.SOURCE_SURFACE,
    )


def summary_payload(
    *,
    started: float,
    eligible_count: int,
    selected: tuple[tuple[CandidateSource, CandidateSourceStatus], ...],
    fetch_items,
    fetch_executions: tuple[SourceFetchExecution, ...],
    inspectable_count: int,
    checkpoint_count: int,
    local_only: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "phase5c_inspection_validation_v1",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "local_only": local_only,
        "configured_max_candidates": SOURCE_INSPECTION_MAX_CANDIDATES,
        "eligible_candidate_sources": eligible_count,
        "selected_candidate_sources": len(selected),
        "fetch_requests": len(fetch_items),
        "fetch_execution_records": len(fetch_executions),
        "inspectable_outcomes": inspectable_count,
        "inspection_checkpoints_written": checkpoint_count,
        "artifact_roots": [
            str(SOURCE_FETCH_ARTIFACT_ROOT.relative_to(PROJECT_ROOT)),
            str(INSPECTION_ARTIFACT_ROOT.relative_to(PROJECT_ROOT)),
            str(SOURCE_FETCH_FAILURE_ROOT.relative_to(PROJECT_ROOT)),
        ],
        "candidate_status_counts": _status_counts(selected),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_report(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Phase 5C Deterministic Inspection Validation",
        "",
        "Generated from Phase 4 eligible CandidateSources using Phase 5B bounded fetch and Phase 5C deterministic inspection.",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _status_counts(
    selected: tuple[tuple[CandidateSource, CandidateSourceStatus], ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _candidate, status in selected:
        counts[status.value] = counts.get(status.value, 0) + 1
    return dict(sorted(counts.items()))


if __name__ == "__main__":
    main()
