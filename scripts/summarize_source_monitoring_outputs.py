import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import ENTITY_PRIORITIES_FILE, ENTITY_UNIVERSE_FILE
from src.source_monitoring.entity_discovery_models import EntityUniverseResult
from src.source_monitoring.entity_identity import (
    normalize_domain,
    normalize_organization_name,
)
from src.source_monitoring.entity_prioritization_models import (
    EntityPrioritizationResult,
)
from src.config import CANDIDATE_SOURCES_FILE, SOURCE_DISCOVERY_PLANS_FILE
from src.source_monitoring.source_discovery_models import (
    DiscoveryPlanStatus,
    SourceDiscoveryPlanningResult,
    SourceDiscoveryResult,
)
from src.storage import load_json


DEFAULT_REPORT_DIR = Path("outputs/planning/source_monitoring/reports")


def generate_source_monitoring_reports(
    *,
    entity_universe_path: Path = ENTITY_UNIVERSE_FILE,
    entity_priorities_path: Path = ENTITY_PRIORITIES_FILE,
    source_discovery_plans_path: Path = SOURCE_DISCOVERY_PLANS_FILE,
    candidate_sources_path: Path = CANDIDATE_SOURCES_FILE,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> tuple[Path, Path, Path, Path, Path, Path]:
    """
    Generate deterministic non-canonical views of Source Monitoring outputs.
    """

    universe = EntityUniverseResult.from_dict(load_json(entity_universe_path))
    priorities = _load_priorities_if_present(entity_priorities_path)
    report_dir.mkdir(parents=True, exist_ok=True)

    universe_summary = report_dir / "entity_universe_summary.md"
    priorities_summary = report_dir / "entity_priorities_summary.md"
    priorities_compact = report_dir / "entity_priorities_compact.json"
    source_plans_summary = report_dir / "source_discovery_plans_summary.md"
    candidate_sources_summary = report_dir / "candidate_sources_summary.md"
    candidate_sources_compact = report_dir / "candidate_sources_compact.json"

    universe_summary.write_text(
        build_entity_universe_summary(universe),
        encoding="utf-8",
    )
    priorities_summary.write_text(
        build_entity_priorities_summary(priorities),
        encoding="utf-8",
    )
    priorities_compact.write_text(
        json.dumps(
            build_compact_priority_records(priorities),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    planning = _load_source_discovery_planning_if_present(
        source_discovery_plans_path
    )
    candidate_result = _load_candidate_sources_if_present(candidate_sources_path)
    source_plans_summary.write_text(
        build_source_discovery_plans_summary(planning, priorities),
        encoding="utf-8",
    )
    candidate_sources_summary.write_text(
        build_candidate_sources_summary(candidate_result, planning, priorities),
        encoding="utf-8",
    )
    candidate_sources_compact.write_text(
        json.dumps(
            build_compact_candidate_source_records(candidate_result),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return (
        universe_summary,
        priorities_summary,
        priorities_compact,
        source_plans_summary,
        candidate_sources_summary,
        candidate_sources_compact,
    )


def build_entity_universe_summary(universe: EntityUniverseResult) -> str:
    primary_counts = Counter(
        item.primary_entity_kind.value for item in universe.entity_candidates
    )
    type_counts = Counter(
        code
        for entity in universe.entity_candidates
        for code in entity.entity_type_codes
    )
    domain_status_counts = Counter(
        domain.verification_status.value
        for entity in universe.entity_candidates
        for domain in entity.official_domain_candidates
    )
    rejected_counts = Counter(
        item.rejection_reason for item in universe.rejected_candidates
    )
    duplicate_flags = _duplicate_name_flags(universe)

    lines = [
        "# Entity Universe Summary",
        "",
        "_Derived non-canonical view. Official cache remains entity_universe.json._",
        "",
        "## Counts",
        "",
        f"- Plans: {len(universe.entity_discovery_plans)}",
        f"- Evidence records: {len(universe.entity_discovery_evidence)}",
        f"- Entities: {len(universe.entity_candidates)}",
        f"- Rejected candidates: {len(universe.rejected_candidates)}",
        f"- Identity conflicts: {len(universe.unresolved_identity_conflicts)}",
        f"- Uncovered entity types: {len(universe.uncovered_entity_type_candidate_ids)}",
        "",
        "## Primary Kind Distribution",
        "",
        _markdown_counter(primary_counts),
        "",
        "## Entity Type Coverage",
        "",
        _markdown_counter(type_counts),
        "",
        "## Official Domain Status Distribution",
        "",
        _markdown_counter(domain_status_counts),
        "",
        "## Compact Entity List",
        "",
    ]
    for entity in sorted(
        universe.entity_candidates,
        key=lambda item: (normalize_organization_name(item.canonical_name), item.entity_id),
    ):
        domains = ", ".join(
            f"{normalize_domain(domain.domain)} ({domain.verification_status.value})"
            for domain in entity.official_domain_candidates
        ) or "none"
        zh_names = ", ".join(entity.names_by_language.get("zh", ())) or "-"
        lines.append(
            "- "
            f"{entity.canonical_name} | {entity.entity_id} | "
            f"{entity.primary_entity_kind.value} | "
            f"types={', '.join(entity.entity_type_codes)} | "
            f"zh={zh_names} | domains={domains} | confidence={entity.confidence:.2f}"
        )
    lines.extend(
        [
            "",
            "## Rejected Candidate Reasons",
            "",
            _markdown_counter(rejected_counts),
            "",
            "## Likely Duplicate-Name Review Flags",
            "",
        ]
    )
    if duplicate_flags:
        lines.extend(f"- {item}" for item in duplicate_flags)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def build_entity_priorities_summary(
    priorities: EntityPrioritizationResult | None,
) -> str:
    lines = [
        "# Entity Priorities Summary",
        "",
        "_Derived non-canonical view. Official cache remains entity_priorities.json._",
        "",
    ]
    if priorities is None:
        lines.append("Entity priorities cache not found.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| Rank | Canonical Name | Tier | Priority | Readiness | Dimensions | Primary Kind | Geography | Review Flags | Rationale |",
            "| ---: | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for item in sorted(priorities.priority_assessments, key=lambda item: item.rank):
        semantic = item.semantic_assessment
        dimensions = (
            f"path={semantic.path_relevance.score}; "
            f"stage={semantic.stage_relevance.score}/"
            f"{semantic.stage_relevance.status.value}; "
            f"expected={semantic.expected_signal_potential.score}; "
            f"strategic={semantic.strategic_importance.score}"
        )
        flags = ", ".join(item.review_flags) or "-"
        lines.append(
            "| "
            f"{item.rank} | {_escape_table(item.canonical_name)} | "
            f"{item.priority_tier.value} | {item.entity_priority_score} | "
            f"{item.evidence_readiness_score} | {_escape_table(dimensions)} | "
            f"{item.primary_entity_kind.value} | "
            f"{item.geography_assessment.score} | {_escape_table(flags)} | "
            f"{_escape_table(item.rationale)} |"
        )
    return "\n".join(lines) + "\n"


def build_compact_priority_records(
    priorities: EntityPrioritizationResult | None,
) -> list[dict[str, Any]]:
    if priorities is None:
        return []

    records: list[dict[str, Any]] = []
    for item in sorted(priorities.priority_assessments, key=lambda item: item.rank):
        semantic = item.semantic_assessment
        records.append(
            {
                "rank": item.rank,
                "entity_id": item.entity_id,
                "canonical_name": item.canonical_name,
                "priority_tier": item.priority_tier.value,
                "entity_priority_score": item.entity_priority_score,
                "evidence_readiness_score": item.evidence_readiness_score,
                "primary_entity_kind": item.primary_entity_kind.value,
                "geography_score": item.geography_assessment.score,
                "semantic_scores": {
                    "path_relevance": semantic.path_relevance.score,
                    "stage_relevance": semantic.stage_relevance.score,
                    "stage_relevance_status": semantic.stage_relevance.status.value,
                    "expected_signal_potential": (
                        semantic.expected_signal_potential.score
                    ),
                    "strategic_importance": semantic.strategic_importance.score,
                },
                "review_flags": list(item.review_flags),
                "rationale": item.rationale,
            }
        )
    return records


def build_source_discovery_plans_summary(
    planning: SourceDiscoveryPlanningResult | None,
    priorities: EntityPrioritizationResult | None,
) -> str:
    lines = [
        "# Source Discovery Plans Summary",
        "",
        "_Derived non-canonical view. Official cache remains source_discovery_plans.json._",
        "",
    ]
    if planning is None:
        lines.append("Source discovery planning cache not found.")
        return "\n".join(lines) + "\n"

    priority_by_entity = (
        {item.entity_id: item for item in priorities.priority_assessments}
        if priorities is not None
        else {}
    )
    executable = [
        plan for plan in planning.plans
        if plan.status == DiscoveryPlanStatus.EXECUTABLE
    ]
    deferred = [
        plan for plan in planning.plans
        if plan.status != DiscoveryPlanStatus.EXECUTABLE
    ]
    tier_counts = Counter(plan.phase3_tier.value for plan in planning.plans)
    max_budget_by_tier = defaultdict(int)
    for budget in planning.budgets:
        max_budget_by_tier[budget.priority_tier.value] = max(
            max_budget_by_tier[budget.priority_tier.value],
            budget.maximum_plan_count,
        )
    lines.extend(
        [
            "## Counts",
            "",
            f"- Audited entities: {len(planning.budgets)}",
            f"- Executable plans: {len(executable)}",
            f"- Deferred plans: {len(deferred)}",
            f"- Deferred entities: {len(planning.deferred_entity_ids)}",
            f"- Estimated Brave requests: {len(executable)}",
            "",
            "## Entities by Tier",
            "",
            _markdown_counter(Counter(b.priority_tier.value for b in planning.budgets)),
            "",
            "## Maximum Plan Budget by Tier",
            "",
            _markdown_counter(Counter(max_budget_by_tier)),
            "",
            "## Executable Plans by Tier",
            "",
            _markdown_counter(Counter(plan.phase3_tier.value for plan in executable)),
            "",
            "## Deferred Plans by Tier",
            "",
            _markdown_counter(Counter(plan.phase3_tier.value for plan in deferred)),
            "",
            "## Plan Counts by Source Role",
            "",
            _markdown_counter(Counter(plan.source_role.value for plan in planning.plans)),
            "",
            "## Discovery Strategy Counts",
            "",
            _markdown_counter(Counter(plan.strategy.value for plan in planning.plans)),
            "",
            "## Query Language Counts",
            "",
            _markdown_counter(Counter(plan.query_language for plan in planning.plans)),
            "",
            "## Entities Using Less Than Maximum Budget",
            "",
        ]
    )
    less_than_max = [
        budget
        for budget in sorted(planning.budgets, key=lambda item: item.entity_id)
        if budget.allocated_plan_count < budget.maximum_plan_count
    ]
    for budget in less_than_max:
        if budget.allocated_plan_count < budget.maximum_plan_count:
            assessment = priority_by_entity.get(budget.entity_id)
            name = assessment.canonical_name if assessment else budget.entity_id
            lines.append(
                f"- {name} | {budget.entity_id} | "
                f"{budget.allocated_plan_count}/{budget.maximum_plan_count} | "
                f"{budget.rationale}"
            )
    if not less_than_max:
        lines.append("- none")

    lines.extend(["", "## Zero Executable Plan Entities", ""])
    zero_entities = [
        budget for budget in planning.budgets if budget.allocated_plan_count == 0
    ]
    if zero_entities:
        for budget in zero_entities:
            lines.append(
                f"- {budget.entity_id}: {budget.rationale}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Deferred Source Roles and Plans", ""])
    if deferred:
        for plan in sorted(deferred, key=lambda item: (item.entity_id, item.candidate_plan_rank)):
            lines.append(
                "- "
                f"{plan.entity_id} | rank={plan.candidate_plan_rank} | "
                f"{plan.source_role.value} | {plan.query_language} | "
                f"{plan.status.value} | {plan.deferral_reason or '-'}"
            )
    else:
        lines.append("- none")

    if tier_counts:
        lines.extend(["", "## Tier Plan Records", ""])
        for key in sorted(tier_counts):
            lines.append(f"- {key}: {tier_counts[key]}")
    return "\n".join(lines) + "\n"


def build_candidate_sources_summary(
    result: SourceDiscoveryResult | None,
    planning: SourceDiscoveryPlanningResult | None,
    priorities: EntityPrioritizationResult | None,
) -> str:
    lines = [
        "# Candidate Sources Summary",
        "",
        "_Derived non-canonical view. Official cache remains candidate_sources.json._",
        "",
    ]
    if result is None:
        lines.append("Candidate source discovery output not found.")
        if planning is not None:
            lines.append(
                f"Executable plans available for a future run: {len(planning.executable_plan_ids)}."
            )
        return "\n".join(lines) + "\n"

    priority_by_entity = (
        {item.entity_id: item for item in priorities.priority_assessments}
        if priorities is not None
        else {}
    )
    plan_by_id = {plan.plan_id: plan for plan in result.plans}
    evidence_by_id = {item.evidence_id: item for item in result.evidence}
    lines.extend(
        [
            "| Entity | Tier | URL | Role | Format | Officiality | Confidence | Evidence | Strategy | Language | Review Flags | Rationale |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for candidate in sorted(
        result.candidate_sources + result.needs_review_candidates,
        key=lambda item: (item.entity_id, item.source_role.value, item.normalized_url),
    ):
        assessment = priority_by_entity.get(candidate.entity_id)
        tier = assessment.priority_tier.value if assessment else "-"
        plan = _first_plan_for_candidate(candidate, evidence_by_id, plan_by_id)
        lines.append(
            "| "
            f"{candidate.entity_id} | {tier} | {_escape_table(candidate.normalized_url)} | "
            f"{candidate.source_role.value} | {candidate.source_format_hint.value} | "
            f"{candidate.candidate_officiality_status.value} | "
            f"{candidate.confidence:.2f} | {len(candidate.supporting_evidence_ids)} | "
            f"{plan.strategy.value if plan else '-'} | "
            f"{plan.query_language if plan else candidate.language} | "
            f"{_escape_table(', '.join(candidate.review_flags) or '-')} | "
            f"{_escape_table(candidate.rationale)} |"
        )
    return "\n".join(lines) + "\n"


def build_compact_candidate_source_records(
    result: SourceDiscoveryResult | None,
) -> list[dict[str, Any]]:
    if result is None:
        return []
    records = []
    for candidate in sorted(
        result.candidate_sources + result.needs_review_candidates,
        key=lambda item: item.candidate_source_id,
    ):
        records.append(
            {
                "candidate_source_id": candidate.candidate_source_id,
                "entity_id": candidate.entity_id,
                "normalized_url": candidate.normalized_url,
                "root_domain": candidate.root_domain,
                "source_role": candidate.source_role.value,
                "source_format_hint": candidate.source_format_hint.value,
                "officiality": candidate.candidate_officiality_status.value,
                "confidence": candidate.confidence,
                "supporting_evidence_count": len(candidate.supporting_evidence_ids),
                "review_flags": list(candidate.review_flags),
                "rationale": candidate.rationale,
            }
        )
    return records


def _load_priorities_if_present(
    entity_priorities_path: Path,
) -> EntityPrioritizationResult | None:
    if not entity_priorities_path.exists():
        return None
    return EntityPrioritizationResult.from_dict(load_json(entity_priorities_path))


def _load_source_discovery_planning_if_present(
    path: Path,
) -> SourceDiscoveryPlanningResult | None:
    if not path.exists():
        return None
    return SourceDiscoveryPlanningResult.from_dict(load_json(path))


def _load_candidate_sources_if_present(path: Path) -> SourceDiscoveryResult | None:
    if not path.exists():
        return None
    return SourceDiscoveryResult.from_dict(load_json(path))


def _first_plan_for_candidate(candidate, evidence_by_id, plan_by_id):
    for evidence_id in candidate.supporting_evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is not None:
            return plan_by_id.get(evidence.plan_id)
    return None


def _markdown_counter(counter: Counter) -> str:
    if not counter:
        return "- none"
    return "\n".join(
        f"- {key}: {counter[key]}"
        for key in sorted(counter)
    )


def _duplicate_name_flags(universe: EntityUniverseResult) -> list[str]:
    by_name: dict[str, list[str]] = defaultdict(list)
    for entity in universe.entity_candidates:
        normalized = normalize_organization_name(entity.canonical_name)
        if normalized:
            by_name[normalized].append(entity.entity_id)
    return [
        f"{name}: {', '.join(sorted(ids))}"
        for name, ids in sorted(by_name.items())
        if len(ids) > 1
    ]


def _escape_table(value: str) -> str:
    return " ".join(str(value).replace("|", "\\|").split())


if __name__ == "__main__":
    generated = generate_source_monitoring_reports()
    for path in generated:
        print(path)
