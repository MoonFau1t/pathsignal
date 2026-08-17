import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openai import OpenAI

from src.config import (
    BRAVE_API_KEY,
    CANDIDATE_SOURCES_FILE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
    SEARCH_API_DRY_RUN,
    SEARCH_API_TIMEOUT_SECONDS,
    SOURCE_DISCOVERY_CACHE_ENABLED,
    SOURCE_DISCOVERY_CLASSIFIER_BATCH_SIZE,
    SOURCE_DISCOVERY_CLASSIFIER_MODEL,
    SOURCE_DISCOVERY_CLASSIFIER_TEMPERATURE,
    SOURCE_DISCOVERY_EXECUTION_BATCH_SIZE,
    SOURCE_DISCOVERY_MAX_EVIDENCE_PER_CLASSIFIER_BATCH,
    SOURCE_DISCOVERY_MAX_PLANS,
    SOURCE_DISCOVERY_MAX_RESULTS_PER_PLAN,
    SOURCE_DISCOVERY_PLANS_FILE,
)
from src.models import SearchPlan, SearchQueryType, SourceType, TargetCareerPath
from src.search_api_client import BraveSearchClient, SearchAPIError
from src.source_monitoring.cache import (
    load_cached_source_discovery_planning_result,
    load_cached_source_discovery_result,
    save_source_discovery_planning_result,
    save_source_discovery_result,
)
from src.source_monitoring.entity_discovery_models import (
    EntityCandidate,
    EntityUniverseResult,
    OfficialDomainVerificationStatus,
    PrimaryEntityKind,
)
from src.source_monitoring.entity_identity import normalize_domain
from src.source_monitoring.entity_prioritization_models import (
    EntityPrioritizationResult,
    EntityPriorityAssessment,
    PriorityTier,
)
from src.source_monitoring.models import InformationNeed
from src.source_monitoring.source_discovery_identity import (
    build_candidate_source_id,
    build_rejected_candidate_source_id,
    build_source_discovery_evidence_id,
    build_source_discovery_execution_id,
    build_source_discovery_execution_input_fingerprint,
    build_source_discovery_output_hash,
    build_source_discovery_plan_id,
    build_source_discovery_planning_input_fingerprint,
    build_source_discovery_planning_output_hash,
    equivalent_query_key,
    infer_source_format_hint,
    normalize_source_url,
    root_domain_from_url,
)
from src.source_monitoring.source_discovery_models import (
    ENTITY_KIND_SOURCE_ROLE_POLICY_VERSION,
    SOURCE_DISCOVERY_BUDGET_POLICY_VERSION,
    SOURCE_DISCOVERY_CLASSIFIER_PROMPT_VERSION,
    SOURCE_DISCOVERY_PLAN_RANKING_POLICY_VERSION,
    SOURCE_DISCOVERY_PRECLASSIFICATION_POLICY_VERSION,
    SOURCE_DISCOVERY_QUERY_TEMPLATE_VERSION,
    SOURCE_DISCOVERY_URL_NORMALIZATION_POLICY_VERSION,
    SOURCE_ROLE_ONTOLOGY_VERSION,
    CandidateOfficialityStatus,
    CandidateSource,
    DiscoveryExecutionStatus,
    DiscoveryPlanStatus,
    DiscoveryStrategy,
    RejectedCandidateSource,
    SourceDiscoveryBudget,
    SourceDiscoveryEvidence,
    SourceDiscoveryExecution,
    SourceDiscoveryPlan,
    SourceDiscoveryPlanningResult,
    SourceDiscoveryResult,
    SourceFormatHint,
    SourceRole,
)
from src.source_monitoring.source_role_ontology import (
    applicable_source_roles,
    get_source_role_definition,
    get_source_role_ontology,
    source_role_policy_snapshot,
)
from src.storage import save_json


class SourceDiscoveryError(Exception):
    """
    Raised when Phase 4 source discovery cannot proceed safely.
    """


class SourceDiscoveryClassifierClient:
    """
    Dedicated OpenAI-compatible classifier for ambiguous Phase 4 evidence.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = SOURCE_DISCOVERY_CLASSIFIER_TEMPERATURE,
    ) -> None:
        self.provider = provider
        self.model = model
        self.temperature = temperature
        if not api_key or api_key.startswith("your_"):
            raise SourceDiscoveryError(
                "LLM_API_KEY is missing. Provide an injected fake client in tests "
                "or configure DeepSeek before live ambiguous classification."
            )
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def classify(
        self,
        *,
        entity_context: dict[str, Any],
        priority_context: dict[str, Any],
        information_needs: tuple[InformationNeed, ...],
        evidence_items: tuple[SourceDiscoveryEvidence, ...],
        controlled_roles: tuple[SourceRole, ...],
    ) -> dict[str, Any]:
        prompt = _build_classifier_prompt(
            entity_context=entity_context,
            priority_context=priority_context,
            information_needs=information_needs,
            evidence_items=evidence_items,
            controlled_roles=controlled_roles,
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify ambiguous source-discovery search results into "
                        "controlled SourceRole values only. Return only valid JSON. "
                        "Do not invent URLs, approve final sources, or claim "
                        "freshness/cadence/quality."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=self.temperature,
            stream=False,
        )
        text = _extract_llm_response_text(response)
        if text is None or not text.strip():
            raise SourceDiscoveryError("SourceDiscovery classifier returned empty text.")
        parsed = json.loads(_normalize_json_response_text(text))
        if not isinstance(parsed, dict):
            raise SourceDiscoveryError("SourceDiscovery classifier returned non-object JSON.")
        return parsed


def build_source_discovery_plans(
    *,
    entity_prioritization_result: EntityPrioritizationResult,
    entity_universe_result: EntityUniverseResult,
    information_needs: tuple[InformationNeed, ...],
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    force_refresh: bool = False,
    cache_enabled: bool = SOURCE_DISCOVERY_CACHE_ENABLED,
    cache_file: Path = SOURCE_DISCOVERY_PLANS_FILE,
    max_results_per_plan: int = SOURCE_DISCOVERY_MAX_RESULTS_PER_PLAN,
    max_plans: int = SOURCE_DISCOVERY_MAX_PLANS,
) -> SourceDiscoveryPlanningResult:
    input_fingerprint = build_source_discovery_planning_input_fingerprint(
        phase3_output_hash=entity_prioritization_result.output_hash,
        phase2_output_hash=entity_universe_result.output_hash,
        entity_identity_domain_data=_entity_identity_domain_payload(
            entity_universe_result
        ),
        information_needs=sorted(
            information_needs, key=lambda item: item.information_need_id
        ),
        target_career_paths=sorted(
            target_career_paths, key=lambda item: item.path_id
        ),
        user_preferences=user_preferences,
        role_ontology=[item.to_dict() for item in get_source_role_ontology()],
        source_role_policy=source_role_policy_snapshot(),
        budget_policy_version=SOURCE_DISCOVERY_BUDGET_POLICY_VERSION,
        plan_ranking_policy_version=SOURCE_DISCOVERY_PLAN_RANKING_POLICY_VERSION,
        query_template_version=SOURCE_DISCOVERY_QUERY_TEMPLATE_VERSION,
        plan_limits={
            "max_results_per_plan": max_results_per_plan,
            "max_plans": max_plans,
        },
    )

    if cache_enabled and not force_refresh:
        cached, diagnostics = load_cached_source_discovery_planning_result(
            cache_file=cache_file,
            input_fingerprint=input_fingerprint,
        )
        if cached is not None:
            return cached
    else:
        diagnostics = ()

    entity_by_id = {item.entity_id: item for item in entity_universe_result.entity_candidates}
    budgets: list[SourceDiscoveryBudget] = []
    all_plans: list[SourceDiscoveryPlan] = []
    deferred_entity_ids: list[str] = []
    plan_cap_remaining = max_plans if max_plans and max_plans > 0 else None
    run_diagnostics: list[str] = list(diagnostics)

    for assessment in sorted(
        entity_prioritization_result.priority_assessments,
        key=lambda item: (item.rank, item.entity_id),
    ):
        entity = entity_by_id.get(assessment.entity_id)
        if entity is None:
            deferred_entity_ids.append(assessment.entity_id)
            run_diagnostics.append(
                f"{assessment.entity_id}: missing Phase 2 entity candidate."
            )
            budgets.append(_zero_budget(assessment, "missing Phase 2 entity candidate"))
            continue

        budget = allocate_source_discovery_budget(assessment, entity)
        candidates = generate_candidate_source_discovery_plans(
            assessment=assessment,
            entity=entity,
            information_needs=information_needs,
            target_career_paths=target_career_paths,
            user_preferences=user_preferences,
            max_results_per_plan=max_results_per_plan,
        )
        ranked = rank_and_truncate_candidate_plans(
            candidate_plans=candidates,
            budget=budget,
            global_plan_cap_remaining=plan_cap_remaining,
        )
        executable_count = sum(
            1 for item in ranked if item.status == DiscoveryPlanStatus.EXECUTABLE
        )
        if plan_cap_remaining is not None:
            plan_cap_remaining = max(0, plan_cap_remaining - executable_count)
        budget = replace(budget, allocated_plan_count=executable_count)
        budgets.append(budget)
        all_plans.extend(ranked)
        if executable_count == 0:
            deferred_entity_ids.append(entity.entity_id)
            if not ranked:
                run_diagnostics.append(
                    f"{entity.entity_id}: zero executable plans; no suitable candidate plans."
                )

    executable_plan_ids = tuple(
        item.plan_id for item in all_plans if item.status == DiscoveryPlanStatus.EXECUTABLE
    )
    deferred_plan_ids = tuple(
        item.plan_id for item in all_plans if item.status != DiscoveryPlanStatus.EXECUTABLE
    )
    output_hash = build_source_discovery_planning_output_hash(
        budgets=tuple(sorted(budgets, key=lambda item: item.entity_id)),
        plans=tuple(sorted(all_plans, key=lambda item: (item.entity_id, item.candidate_plan_rank, item.plan_id))),
        deferred_entity_ids=tuple(sorted(deferred_entity_ids)),
    )
    result = SourceDiscoveryPlanningResult(
        planning_result_hash=output_hash,
        budgets=tuple(sorted(budgets, key=lambda item: item.entity_id)),
        plans=tuple(
            sorted(all_plans, key=lambda item: (item.entity_id, item.candidate_plan_rank, item.plan_id))
        ),
        executable_plan_ids=executable_plan_ids,
        deferred_plan_ids=deferred_plan_ids,
        deferred_entity_ids=tuple(sorted(set(deferred_entity_ids))),
        diagnostics=tuple(run_diagnostics),
        role_ontology_version=SOURCE_ROLE_ONTOLOGY_VERSION,
        entity_kind_role_policy_version=ENTITY_KIND_SOURCE_ROLE_POLICY_VERSION,
        budget_policy_version=SOURCE_DISCOVERY_BUDGET_POLICY_VERSION,
        plan_ranking_policy_version=SOURCE_DISCOVERY_PLAN_RANKING_POLICY_VERSION,
        query_template_version=SOURCE_DISCOVERY_QUERY_TEMPLATE_VERSION,
        input_fingerprint=input_fingerprint,
        output_hash=output_hash,
    )
    if cache_enabled:
        save_source_discovery_planning_result(cache_file, result)
    return result


def allocate_source_discovery_budget(
    assessment: EntityPriorityAssessment,
    entity: EntityCandidate,
) -> SourceDiscoveryBudget:
    max_by_tier = {
        PriorityTier.TIER_A_IMMEDIATE: 4,
        PriorityTier.TIER_B_STANDARD: 3,
        PriorityTier.TIER_C_SELECTIVE: 2,
        PriorityTier.TIER_D_DEFERRED: 1,
    }
    readiness = assessment.evidence_readiness_score
    domain = _best_official_domain(entity)
    needs_domain = (
        not domain
        or assessment.evidence_readiness_assessment.official_domain_status
        in {"none", "unresolved", "third_party"}
    )
    low_readiness = readiness < 50
    maximum = max_by_tier[assessment.priority_tier]
    if (
        assessment.priority_tier == PriorityTier.TIER_D_DEFERRED
        and low_readiness
        and assessment.entity_priority_score < 35
    ):
        maximum = 0
        rationale = "Low priority and low evidence readiness; execution deferred."
    elif needs_domain:
        rationale = "Budget reserves identity/domain discovery before role expansion."
    elif readiness >= 70:
        rationale = "High evidence readiness supports domain-first role discovery."
    else:
        rationale = "Limited role discovery with auditable budget cap."

    return SourceDiscoveryBudget(
        entity_id=assessment.entity_id,
        priority_tier=assessment.priority_tier,
        maximum_plan_count=maximum,
        allocated_plan_count=0,
        readiness_score=readiness,
        needs_domain_verification=needs_domain,
        low_evidence_readiness=low_readiness,
        probable_official_domain=domain,
        rationale=rationale,
    )


def generate_candidate_source_discovery_plans(
    *,
    assessment: EntityPriorityAssessment,
    entity: EntityCandidate,
    information_needs: tuple[InformationNeed, ...],
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
    max_results_per_plan: int,
) -> tuple[SourceDiscoveryPlan, ...]:
    roles = prioritize_source_roles(
        assessment=assessment,
        entity=entity,
        information_needs=information_needs,
        target_career_paths=target_career_paths,
        user_preferences=user_preferences,
    )
    domain = _best_official_domain(entity)
    if domain is None and assessment.evidence_readiness_score < 50:
        roles = tuple(
            role for role in roles if role == SourceRole.OFFICIAL_HOMEPAGE
        )
    languages = _supported_languages(entity)
    supporting_need_ids = tuple(
        sorted(
            set(entity.related_information_need_ids)
            & {need.information_need_id for need in information_needs}
        )
    )
    supporting_path_ids = tuple(
        sorted(
            set(entity.related_target_career_path_ids)
            & {path.path_id for path in target_career_paths}
        )
    )
    plans: list[SourceDiscoveryPlan] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()

    for role_index, role in enumerate(roles):
        for language in languages:
            name = _best_name_for_language(entity, language)
            if not name:
                continue
            query = _build_query(
                entity_name=name,
                source_role=role,
                language=language,
                domain=domain,
                information_needs=information_needs,
            )
            strategy = (
                DiscoveryStrategy.DOMAIN_FIRST
                if domain
                else (
                    DiscoveryStrategy.IDENTITY_RESOLUTION
                    if role == SourceRole.OFFICIAL_HOMEPAGE
                    else DiscoveryStrategy.NAME_FIRST
                )
            )
            key = equivalent_query_key(
                entity_id=entity.entity_id,
                source_role=role,
                language=language,
                strategy=strategy.value,
                query=query,
                domain_constraint=domain,
            )
            status = DiscoveryPlanStatus.DEFERRED_DUPLICATE_EQUIVALENT_QUERY
            deferral_reason = "duplicate_equivalent_query"
            if key not in seen:
                seen.add(key)
                status = DiscoveryPlanStatus.DEFERRED_BUDGET_LIMIT
                deferral_reason = "budget_limit"
            score = _score_candidate_plan(
                assessment=assessment,
                source_role=role,
                role_index=role_index,
                language=language,
                strategy=strategy,
                domain=domain,
                information_needs=information_needs,
            )
            plans.append(
                SourceDiscoveryPlan(
                    plan_id=build_source_discovery_plan_id(
                        entity_id=entity.entity_id,
                        source_role=role,
                        strategy=strategy.value,
                        query_language=language,
                        query=query,
                        domain_constraint=domain,
                    ),
                    entity_id=entity.entity_id,
                    canonical_name=entity.canonical_name,
                    source_role=role,
                    strategy=strategy,
                    query_language=language,
                    query=query,
                    domain_constraint=domain,
                    max_result_count=max_results_per_plan,
                    candidate_plan_rank=0,
                    status=status,
                    phase3_tier=assessment.priority_tier,
                    budget_provenance={
                        "priority_tier": assessment.priority_tier.value,
                        "priority_score": assessment.entity_priority_score,
                        "evidence_readiness_score": assessment.evidence_readiness_score,
                    },
                    supporting_information_need_ids=supporting_need_ids,
                    supporting_target_career_path_ids=supporting_path_ids,
                    deferral_reason=deferral_reason,
                    ranking_score=score,
                )
            )
    return tuple(plans)


def prioritize_source_roles(
    *,
    assessment: EntityPriorityAssessment,
    entity: EntityCandidate,
    information_needs: tuple[InformationNeed, ...],
    target_career_paths: list[TargetCareerPath],
    user_preferences: dict[str, Any],
) -> tuple[SourceRole, ...]:
    roles = list(applicable_source_roles(entity.primary_entity_kind))
    text = " ".join(
        [
            *(need.title for need in information_needs),
            *(need.description for need in information_needs),
            *(path.title for path in target_career_paths),
            *(path.description for path in target_career_paths),
            json.dumps(user_preferences, ensure_ascii=False),
        ]
    ).casefold()
    boosts = {
        SourceRole.RESEARCH_PUBLICATIONS: ("research", "paper", "论文", "研究"),
        SourceRole.REPORTS_OR_DATA: ("report", "data", "报告", "数据"),
        SourceRole.CAREERS: ("career", "job", "招聘", "职业"),
        SourceRole.PORTFOLIO: ("portfolio", "investment", "投资", "被投"),
        SourceRole.POLICY_UPDATES: ("policy", "regulation", "政策", "法规"),
        SourceRole.EVENTS_OR_PROGRAMS: ("event", "program", "活动", "项目"),
        SourceRole.NEWSROOM: ("news", "announcement", "新闻", "公告"),
        SourceRole.INSIGHTS: ("insight", "strategy", "洞察", "观点"),
    }

    def sort_key(role: SourceRole) -> tuple[int, int, str]:
        base_index = roles.index(role)
        boost = sum(1 for token in boosts.get(role, ()) if token in text)
        if assessment.evidence_readiness_score < 50 and role == SourceRole.OFFICIAL_HOMEPAGE:
            boost += 4
        return (-boost, base_index, role.value)

    return tuple(sorted(roles, key=sort_key))


def rank_and_truncate_candidate_plans(
    *,
    candidate_plans: tuple[SourceDiscoveryPlan, ...],
    budget: SourceDiscoveryBudget,
    global_plan_cap_remaining: int | None = None,
) -> tuple[SourceDiscoveryPlan, ...]:
    ranked = sorted(
        candidate_plans,
        key=lambda item: (-item.ranking_score, item.query_language, item.query, item.plan_id),
    )
    identity_resolution_available = budget.needs_domain_verification and any(
        item.status != DiscoveryPlanStatus.DEFERRED_DUPLICATE_EQUIVALENT_QUERY
        and item.strategy == DiscoveryStrategy.IDENTITY_RESOLUTION
        and item.source_role == SourceRole.OFFICIAL_HOMEPAGE
        for item in ranked
    )
    maximum = budget.maximum_plan_count
    if global_plan_cap_remaining is not None:
        maximum = min(maximum, max(0, global_plan_cap_remaining))
    selected = 0
    output: list[SourceDiscoveryPlan] = []
    for rank, plan in enumerate(ranked, start=1):
        status = plan.status
        reason = plan.deferral_reason
        if status != DiscoveryPlanStatus.DEFERRED_DUPLICATE_EQUIVALENT_QUERY:
            if (
                identity_resolution_available
                and plan.strategy == DiscoveryStrategy.NAME_FIRST
                and plan.source_role != SourceRole.OFFICIAL_HOMEPAGE
            ):
                status = DiscoveryPlanStatus.DEFERRED_UNRESOLVED_DOMAIN
                reason = "identity_resolution_required_before_role_discovery"
            elif selected < maximum:
                status = DiscoveryPlanStatus.EXECUTABLE
                reason = None
                selected += 1
            else:
                status = (
                    DiscoveryPlanStatus.DEFERRED_LOW_PRIORITY_LOW_READINESS
                    if budget.maximum_plan_count == 0
                    else DiscoveryPlanStatus.DEFERRED_BUDGET_LIMIT
                )
                reason = (
                    "low_priority_and_low_readiness"
                    if budget.maximum_plan_count == 0
                    else "budget_limit"
                )
        output.append(
            replace(
                plan,
                candidate_plan_rank=rank,
                status=status,
                deferral_reason=reason,
            )
        )
    return tuple(output)


def discover_candidate_sources(
    *,
    planning_result: SourceDiscoveryPlanningResult,
    entity_prioritization_result: EntityPrioritizationResult,
    entity_universe_result: EntityUniverseResult,
    information_needs: tuple[InformationNeed, ...],
    force_refresh: bool = False,
    entity_offset: int = 0,
    max_entities: int | None = None,
    max_plans: int | None = None,
    execution_batch_size: int = SOURCE_DISCOVERY_EXECUTION_BATCH_SIZE,
    classifier_batch_size: int = SOURCE_DISCOVERY_CLASSIFIER_BATCH_SIZE,
    search_client: Any | None = None,
    classifier_client: Any | None = None,
    cache_enabled: bool = SOURCE_DISCOVERY_CACHE_ENABLED,
    cache_file: Path = CANDIDATE_SOURCES_FILE,
    checkpoint_dir: Path | None = None,
    provider: str = "brave",
    classifier_model: str = SOURCE_DISCOVERY_CLASSIFIER_MODEL,
) -> SourceDiscoveryResult:
    execution_fingerprint = build_source_discovery_execution_input_fingerprint(
        planning_output_hash=planning_result.output_hash,
        phase3_output_hash=entity_prioritization_result.output_hash,
        phase2_output_hash=entity_universe_result.output_hash,
        provider_configuration={
            "search_provider": provider,
            "search_dry_run": bool(getattr(search_client, "dry_run", SEARCH_API_DRY_RUN)),
            "classifier_provider": LLM_PROVIDER,
            "classifier_model": classifier_model,
            "classifier_prompt_version": SOURCE_DISCOVERY_CLASSIFIER_PROMPT_VERSION,
        },
        limits={
            "entity_offset": entity_offset,
            "max_entities": max_entities,
            "max_plans": max_plans,
            "execution_batch_size": execution_batch_size,
            "classifier_batch_size": classifier_batch_size,
        },
        deterministic_policy_versions={
            "preclassification": SOURCE_DISCOVERY_PRECLASSIFICATION_POLICY_VERSION,
            "url_normalization": SOURCE_DISCOVERY_URL_NORMALIZATION_POLICY_VERSION,
        },
    )
    if cache_enabled and not force_refresh:
        cached, cache_diagnostics = load_cached_source_discovery_result(
            cache_file=cache_file,
            input_fingerprint=execution_fingerprint,
        )
        if cached is not None:
            return cached
    else:
        cache_diagnostics = ()

    selected_plans, deferred_by_limits = _select_executable_plans(
        planning_result.plans,
        entity_offset=entity_offset,
        max_entities=max_entities,
        max_plans=max_plans,
    )
    client = search_client or BraveSearchClient(
        api_key=BRAVE_API_KEY,
        timeout_seconds=SEARCH_API_TIMEOUT_SECONDS,
        dry_run=SEARCH_API_DRY_RUN,
    )
    checkpoint_dir = checkpoint_dir or cache_file.parent / "checkpoints" / "source_discovery"
    executions, evidence, execution_diagnostics = execute_source_discovery_plans(
        plans=selected_plans,
        search_client=client,
        provider=provider,
        checkpoint_dir=checkpoint_dir,
        execution_batch_size=execution_batch_size,
    )
    candidates, rejected, needs_review, classification_diagnostics = (
        assemble_candidate_sources(
            evidence=evidence,
            plans=planning_result.plans,
            entity_universe_result=entity_universe_result,
            entity_prioritization_result=entity_prioritization_result,
            information_needs=information_needs,
            classifier_client=classifier_client,
            classifier_batch_size=classifier_batch_size,
            classifier_model=classifier_model,
            checkpoint_dir=checkpoint_dir,
        )
    )
    failed_execution_ids = tuple(
        item.execution_id
        for item in executions
        if item.status == DiscoveryExecutionStatus.FAILED
    )
    complete = not deferred_by_limits and not failed_execution_ids
    output_hash = build_source_discovery_output_hash(
        executions=tuple(sorted(executions, key=lambda item: item.execution_id)),
        evidence=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
        candidate_sources=tuple(sorted(candidates, key=lambda item: item.candidate_source_id)),
        rejected_candidates=tuple(sorted(rejected, key=lambda item: item.rejected_candidate_id)),
        needs_review_candidates=tuple(sorted(needs_review, key=lambda item: item.candidate_source_id)),
        deferred_plan_ids=tuple(sorted(deferred_by_limits)),
        failed_execution_ids=tuple(sorted(failed_execution_ids)),
    )
    result = SourceDiscoveryResult(
        planning_result_hash=planning_result.planning_result_hash,
        budgets=planning_result.budgets,
        plans=planning_result.plans,
        executions=tuple(sorted(executions, key=lambda item: item.execution_id)),
        evidence=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
        candidate_sources=tuple(sorted(candidates, key=lambda item: item.candidate_source_id)),
        rejected_candidates=tuple(sorted(rejected, key=lambda item: item.rejected_candidate_id)),
        needs_review_candidates=tuple(sorted(needs_review, key=lambda item: item.candidate_source_id)),
        deferred_entity_ids=planning_result.deferred_entity_ids,
        deferred_plan_ids=tuple(sorted(set(planning_result.deferred_plan_ids + tuple(deferred_by_limits)))),
        failed_execution_ids=tuple(sorted(failed_execution_ids)),
        diagnostics=tuple(cache_diagnostics)
        + execution_diagnostics
        + classification_diagnostics,
        execution_metadata={
            "provider": provider,
            "classifier_model": classifier_model,
            "classifier_batch_size": classifier_batch_size,
            "execution_batch_size": execution_batch_size,
            "complete_cache_reusable": complete,
        },
        role_ontology_version=SOURCE_ROLE_ONTOLOGY_VERSION,
        budget_policy_version=SOURCE_DISCOVERY_BUDGET_POLICY_VERSION,
        plan_ranking_policy_version=SOURCE_DISCOVERY_PLAN_RANKING_POLICY_VERSION,
        query_template_version=SOURCE_DISCOVERY_QUERY_TEMPLATE_VERSION,
        classifier_prompt_version=SOURCE_DISCOVERY_CLASSIFIER_PROMPT_VERSION,
        url_normalization_policy_version=SOURCE_DISCOVERY_URL_NORMALIZATION_POLICY_VERSION,
        preclassification_policy_version=(
            SOURCE_DISCOVERY_PRECLASSIFICATION_POLICY_VERSION
        ),
        input_fingerprint=execution_fingerprint,
        output_hash=output_hash,
        generation_mode="complete" if complete else "partial",
    )
    if cache_enabled and complete:
        save_source_discovery_result(cache_file, result)
    return result


def execute_source_discovery_plans(
    *,
    plans: tuple[SourceDiscoveryPlan, ...],
    search_client: Any,
    provider: str,
    checkpoint_dir: Path,
    execution_batch_size: int,
) -> tuple[
    tuple[SourceDiscoveryExecution, ...],
    tuple[SourceDiscoveryEvidence, ...],
    tuple[str, ...],
]:
    executions: list[SourceDiscoveryExecution] = []
    evidence: list[SourceDiscoveryEvidence] = []
    diagnostics: list[str] = []
    batches = tuple(
        plans[index:index + max(1, execution_batch_size)]
        for index in range(0, len(plans), max(1, execution_batch_size))
    )
    for batch_index, batch in enumerate(batches, start=1):
        raw_checkpoint: list[dict[str, Any]] = []
        for plan in batch:
            execution_id = build_source_discovery_execution_id(
                plan_id=plan.plan_id,
                provider=provider,
            )
            try:
                raw_items = search_client.search(_to_transport_search_plan(plan))
                raw_checkpoint.append(
                    {
                        "execution_id": execution_id,
                        "plan_id": plan.plan_id,
                        "raw_items": [
                            _raw_item_to_checkpoint(item) for item in raw_items
                        ],
                    }
                )
                checkpoint_dir.mkdir(parents=True, exist_ok=True)
                checkpoint_path = checkpoint_dir / f"source_discovery_batch_{batch_index}.json"
                save_json(
                    {
                        "batch_index": batch_index,
                        "provider": provider,
                        "items": raw_checkpoint,
                    },
                    checkpoint_path,
                )
                executions.append(
                    SourceDiscoveryExecution(
                        execution_id=execution_id,
                        plan_id=plan.plan_id,
                        entity_id=plan.entity_id,
                        status=(
                            DiscoveryExecutionStatus.EXECUTED
                            if raw_items
                            else DiscoveryExecutionStatus.EXECUTED_NO_RESULTS
                        ),
                        provider=provider,
                        query=plan.query,
                        result_count=len(raw_items),
                        checkpoint_path=str(checkpoint_path),
                    )
                )
                for rank, item in enumerate(raw_items, start=1):
                    evidence.append(_evidence_from_raw_item(plan, execution_id, item, rank, provider))
            except (SearchAPIError, Exception) as error:
                diagnostics.append(
                    f"SourceDiscoveryPlan {plan.plan_id} failed: {type(error).__name__}: {error}"
                )
                executions.append(
                    SourceDiscoveryExecution(
                        execution_id=execution_id,
                        plan_id=plan.plan_id,
                        entity_id=plan.entity_id,
                        status=DiscoveryExecutionStatus.FAILED,
                        provider=provider,
                        query=plan.query,
                        result_count=0,
                        diagnostics=(str(error),),
                    )
                )
    return tuple(executions), tuple(evidence), tuple(diagnostics)


def assemble_candidate_sources(
    *,
    evidence: tuple[SourceDiscoveryEvidence, ...],
    plans: tuple[SourceDiscoveryPlan, ...],
    entity_universe_result: EntityUniverseResult,
    entity_prioritization_result: EntityPrioritizationResult,
    information_needs: tuple[InformationNeed, ...],
    classifier_client: Any | None,
    classifier_batch_size: int,
    classifier_model: str,
    checkpoint_dir: Path,
) -> tuple[
    tuple[CandidateSource, ...],
    tuple[RejectedCandidateSource, ...],
    tuple[CandidateSource, ...],
    tuple[str, ...],
]:
    plan_by_id = {plan.plan_id: plan for plan in plans}
    entity_by_id = {entity.entity_id: entity for entity in entity_universe_result.entity_candidates}
    priority_by_id = {
        assessment.entity_id: assessment
        for assessment in entity_prioritization_result.priority_assessments
    }
    candidates_by_key: dict[tuple[str, str, str], CandidateSource] = {}
    rejected_by_id: dict[str, RejectedCandidateSource] = {}
    ambiguous: list[SourceDiscoveryEvidence] = []
    diagnostics: list[str] = []

    for item in sorted(evidence, key=lambda value: value.evidence_id):
        plan = plan_by_id[item.plan_id]
        entity = entity_by_id.get(item.entity_id)
        if entity is None:
            rejected = _reject_evidence(item, "missing_entity_context", ("missing Phase 2 entity",))
            rejected_by_id[rejected.rejected_candidate_id] = rejected
            continue
        decision = preclassify_source_evidence(item, plan, entity)
        if decision["decision"] == "ambiguous":
            ambiguous.append(item)
            continue
        if decision["decision"] == "reject":
            rejected = _reject_evidence(
                item,
                str(decision["reason"]),
                tuple(decision.get("diagnostics", ())),
                provenance={"classifier": "deterministic_preclassification"},
            )
            rejected_by_id[rejected.rejected_candidate_id] = rejected
            continue
        candidate = _candidate_from_classification(
            evidence_item=item,
            plan=plan,
            source_role=decision["source_role"],
            officiality=decision["officiality"],
            confidence=float(decision["confidence"]),
            rationale=str(decision["rationale"]),
            review_flags=tuple(decision.get("review_flags", ())),
            provenance={"classifier": "deterministic_preclassification"},
        )
        _merge_candidate(candidates_by_key, candidate)

    if ambiguous:
        client = classifier_client or SourceDiscoveryClassifierClient(
            provider=LLM_PROVIDER,
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=classifier_model,
        )
        accepted, rejected, review, classifier_diagnostics = classify_ambiguous_evidence(
            ambiguous_evidence=tuple(ambiguous),
            plan_by_id=plan_by_id,
            entity_by_id=entity_by_id,
            priority_by_id=priority_by_id,
            information_needs=information_needs,
            classifier_client=client,
            classifier_batch_size=min(
                classifier_batch_size,
                SOURCE_DISCOVERY_MAX_EVIDENCE_PER_CLASSIFIER_BATCH,
            ),
            checkpoint_dir=checkpoint_dir,
        )
        diagnostics.extend(classifier_diagnostics)
        for candidate in accepted + review:
            _merge_candidate(candidates_by_key, candidate)
        for item in rejected:
            rejected_by_id[item.rejected_candidate_id] = item

    candidates = tuple(candidates_by_key.values())
    needs_review = tuple(
        item for item in candidates if item.review_flags or item.confidence < 0.7
    )
    accepted = tuple(item for item in candidates if item not in needs_review)
    return accepted, tuple(rejected_by_id.values()), needs_review, tuple(diagnostics)


def preclassify_source_evidence(
    evidence: SourceDiscoveryEvidence,
    plan: SourceDiscoveryPlan,
    entity: EntityCandidate,
) -> dict[str, Any]:
    normalized_url = evidence.normalized_url
    parsed = urlparse(normalized_url)
    path = parsed.path.casefold()
    root_domain = evidence.root_domain
    official_domain = _best_official_domain(entity)
    format_hint = infer_source_format_hint(normalized_url)
    if not normalized_url:
        return {
            "decision": "reject",
            "reason": "missing_url",
            "diagnostics": ("Search result URL is empty.",),
        }
    if root_domain in _unsupported_social_domains():
        return {
            "decision": "reject",
            "reason": "unsupported_social_media_profile",
            "diagnostics": ("Social-media profiles are not durable official sections.",),
        }
    if _looks_like_job_detail(path):
        return {
            "decision": "reject",
            "reason": "individual_job_detail_page",
            "diagnostics": ("Careers discovery targets hubs, not job-detail pages.",),
        }
    if _looks_like_one_off_article(path) and plan.source_role not in {
        SourceRole.BLOG,
        SourceRole.NEWSROOM,
    }:
        return {
            "decision": "reject",
            "reason": "individual_article_not_durable_section",
            "diagnostics": ("Result appears to be a one-off article page.",),
        }
    inferred_role = _infer_role_from_path(path) or plan.source_role
    if format_hint in {SourceFormatHint.RSS_CANDIDATE, SourceFormatHint.ATOM_CANDIDATE}:
        inferred_role = plan.source_role
    if official_domain and _domain_matches(root_domain, official_domain):
        return {
            "decision": "accept",
            "source_role": inferred_role,
            "officiality": CandidateOfficialityStatus.OFFICIAL_DOMAIN_MATCH,
            "confidence": 0.9 if inferred_role == plan.source_role else 0.78,
            "rationale": "URL matches probable official domain and durable path hints.",
            "review_flags": () if inferred_role == plan.source_role else ("role_inferred_from_path",),
        }
    if _looks_like_third_party_directory(root_domain, path):
        return {
            "decision": "reject",
            "reason": "third_party_directory_page",
            "diagnostics": ("Result is an obvious third-party directory page.",),
        }
    if plan.source_role == SourceRole.OFFICIAL_HOMEPAGE and _name_appears(evidence, entity):
        return {
            "decision": "accept",
            "source_role": SourceRole.OFFICIAL_HOMEPAGE,
            "officiality": CandidateOfficialityStatus.UNRESOLVED,
            "confidence": 0.64,
            "rationale": "Name-first homepage result may identify an official domain.",
            "review_flags": ("needs_domain_verification",),
        }
    return {"decision": "ambiguous"}


def classify_ambiguous_evidence(
    *,
    ambiguous_evidence: tuple[SourceDiscoveryEvidence, ...],
    plan_by_id: dict[str, SourceDiscoveryPlan],
    entity_by_id: dict[str, EntityCandidate],
    priority_by_id: dict[str, EntityPriorityAssessment],
    information_needs: tuple[InformationNeed, ...],
    classifier_client: Any,
    classifier_batch_size: int,
    checkpoint_dir: Path,
) -> tuple[
    tuple[CandidateSource, ...],
    tuple[RejectedCandidateSource, ...],
    tuple[CandidateSource, ...],
    tuple[str, ...],
]:
    accepted: list[CandidateSource] = []
    rejected: list[RejectedCandidateSource] = []
    review: list[CandidateSource] = []
    diagnostics: list[str] = []
    grouped_by_entity: dict[str, list[SourceDiscoveryEvidence]] = {}
    for item in ambiguous_evidence:
        grouped_by_entity.setdefault(item.entity_id, []).append(item)

    batch_index = 0
    for entity_id in sorted(grouped_by_entity):
        entity_batch_items = tuple(
            sorted(grouped_by_entity[entity_id], key=lambda item: item.evidence_id)
        )
        batches = tuple(
            entity_batch_items[index:index + max(1, classifier_batch_size)]
            for index in range(
                0,
                len(entity_batch_items),
                max(1, classifier_batch_size),
            )
        )
        for batch in batches:
            batch_index += 1
            first_entity = entity_by_id[batch[0].entity_id]
            first_priority = priority_by_id[batch[0].entity_id]
            parsed = classifier_client.classify(
                entity_context=_entity_context(first_entity),
                priority_context=_priority_context(first_priority),
                information_needs=information_needs,
                evidence_items=batch,
                controlled_roles=applicable_source_roles(first_entity.primary_entity_kind),
            )
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            save_json(
                parsed,
                checkpoint_dir / f"source_discovery_classifier_batch_{batch_index}.json",
            )
            items = parsed.get("classifications")
            if not isinstance(items, list):
                diagnostics.append(f"classifier batch {batch_index}: missing classifications list")
                continue
            evidence_by_id = {item.evidence_id: item for item in batch}
            for index, classification in enumerate(items):
                if not isinstance(classification, dict):
                    diagnostics.append(f"classifier item {index}: classification is not an object")
                    continue
                item_accepted, item_rejected, item_review = _validate_classifier_item(
                    classification=classification,
                    evidence_by_id=evidence_by_id,
                    plan_by_id=plan_by_id,
                    entity_by_id=entity_by_id,
                    classifier_index=index,
                )
                accepted.extend(item_accepted)
                rejected.extend(item_rejected)
                review.extend(item_review)
    return tuple(accepted), tuple(rejected), tuple(review), tuple(diagnostics)


def _validate_classifier_item(
    *,
    classification: dict[str, Any],
    evidence_by_id: dict[str, SourceDiscoveryEvidence],
    plan_by_id: dict[str, SourceDiscoveryPlan],
    entity_by_id: dict[str, EntityCandidate],
    classifier_index: int,
) -> tuple[list[CandidateSource], list[RejectedCandidateSource], list[CandidateSource]]:
    evidence_id = str(classification.get("evidence_id", ""))
    evidence = evidence_by_id.get(evidence_id)
    if evidence is None:
        return [], [
            RejectedCandidateSource(
                rejected_candidate_id=build_rejected_candidate_source_id(
                    entity_id=str(classification.get("entity_id", "")),
                    url=str(classification.get("url", "")),
                    reason="unknown_or_invented_evidence_id",
                ),
                evidence_id=evidence_id or None,
                provisional_candidate_id=None,
                entity_id=str(classification.get("entity_id", "")),
                url=str(classification.get("url", "")),
                rejection_reason="unknown_or_invented_evidence_id",
                diagnostics=("Classifier referenced evidence outside the supplied batch.",),
                classifier_index=classifier_index,
                provenance={"classifier": "deepseek_ambiguous_evidence"},
            )
        ], []

    plan = plan_by_id[evidence.plan_id]
    errors: list[str] = []
    if normalize_source_url(str(classification.get("url", evidence.url))) != evidence.normalized_url:
        errors.append("invented_url")
    decision = str(classification.get("decision", "needs_review"))
    source_role_value = (
        classification.get("source_role")
        or classification.get("role")
        or classification.get("controlled_role")
    )
    try:
        role = SourceRole(str(source_role_value or ""))
    except ValueError:
        if decision != "reject":
            errors.append("uncontrolled_source_role")
        role = SourceRole.OTHER_OFFICIAL_SECTION
    entity = entity_by_id.get(evidence.entity_id)
    if (
        decision != "reject"
        and (entity is None or role not in applicable_source_roles(entity.primary_entity_kind))
    ):
        errors.append("unsupported_role_for_entity_kind")
    if _contains_forbidden_classifier_claim(classification):
        errors.append("forbidden_observed_source_claim")
    if decision not in {"accept", "needs_review", "reject"}:
        errors.append("invalid_decision")
    if errors or decision == "reject":
        reason = "; ".join(sorted(set(errors))) or str(
            classification.get("rejection_reason", "classifier_rejected")
        )
        return [], [
            _reject_evidence(
                evidence,
                reason,
                tuple(sorted(set(errors))) or (reason,),
                classifier_index=classifier_index,
                provenance={"classifier": "deepseek_ambiguous_evidence"},
            )
        ], []

    officiality = _officiality_from_text(
        str(classification.get("officiality_status", "unresolved"))
    )
    candidate = _candidate_from_classification(
        evidence_item=evidence,
        plan=plan,
        source_role=role,
        officiality=officiality,
        confidence=max(0.0, min(float(classification.get("confidence", 0.5)), 1.0)),
        rationale=str(
            classification.get("rationale")
            or classification.get("reason")
            or "Classifier accepted controlled role."
        ),
        review_flags=tuple(
            str(item)
            for item in classification.get("review_flags", [])
            if item is not None
        ) if isinstance(classification.get("review_flags", []), list) else (),
        provenance={"classifier": "deepseek_ambiguous_evidence"},
    )
    if decision == "needs_review":
        return [], [], [candidate]
    return [candidate], [], []


def _candidate_from_classification(
    *,
    evidence_item: SourceDiscoveryEvidence,
    plan: SourceDiscoveryPlan,
    source_role: SourceRole,
    officiality: CandidateOfficialityStatus,
    confidence: float,
    rationale: str,
    review_flags: tuple[str, ...],
    provenance: dict[str, Any],
) -> CandidateSource:
    normalized_url = evidence_item.normalized_url
    return CandidateSource(
        candidate_source_id=build_candidate_source_id(
            entity_id=evidence_item.entity_id,
            normalized_url=normalized_url,
            source_role=source_role,
        ),
        entity_id=evidence_item.entity_id,
        canonical_url=normalized_url,
        normalized_url=normalized_url,
        root_domain=evidence_item.root_domain,
        source_role=source_role,
        source_format_hint=infer_source_format_hint(normalized_url),
        language=evidence_item.language,
        candidate_officiality_status=officiality,
        discovery_methods=(plan.strategy.value,),
        supporting_evidence_ids=(evidence_item.evidence_id,),
        confidence=confidence,
        rationale=rationale,
        review_flags=tuple(sorted(set(review_flags))),
        provenance={
            **provenance,
            "plan_id": plan.plan_id,
            "query_language": plan.query_language,
            "source_discovery_phase": "phase4_candidate_source_discovery",
        },
    )


def _merge_candidate(
    candidates_by_key: dict[tuple[str, str, str], CandidateSource],
    candidate: CandidateSource,
) -> None:
    key = (candidate.entity_id, candidate.normalized_url, candidate.source_role.value)
    existing = candidates_by_key.get(key)
    if existing is None:
        candidates_by_key[key] = candidate
        return
    candidates_by_key[key] = replace(
        existing,
        discovery_methods=tuple(
            sorted(set(existing.discovery_methods + candidate.discovery_methods))
        ),
        supporting_evidence_ids=tuple(
            sorted(set(existing.supporting_evidence_ids + candidate.supporting_evidence_ids))
        ),
        confidence=max(existing.confidence, candidate.confidence),
        review_flags=tuple(sorted(set(existing.review_flags + candidate.review_flags))),
    )


def _reject_evidence(
    evidence: SourceDiscoveryEvidence,
    reason: str,
    diagnostics: tuple[str, ...],
    classifier_index: int | None = None,
    provenance: dict[str, Any] | None = None,
) -> RejectedCandidateSource:
    return RejectedCandidateSource(
        rejected_candidate_id=build_rejected_candidate_source_id(
            entity_id=evidence.entity_id,
            url=evidence.normalized_url,
            reason=reason,
        ),
        evidence_id=evidence.evidence_id,
        provisional_candidate_id=None,
        entity_id=evidence.entity_id,
        url=evidence.normalized_url,
        rejection_reason=reason,
        diagnostics=diagnostics,
        classifier_index=classifier_index,
        provenance=provenance or {},
    )


def _to_transport_search_plan(plan: SourceDiscoveryPlan) -> SearchPlan:
    return SearchPlan(
        plan_id=plan.plan_id,
        query_id=plan.plan_id,
        query_text=plan.query,
        query_type=SearchQueryType.GENERAL_RESEARCH,
        career_path_id="source_monitoring_phase4",
        career_path_title="Source Monitoring Candidate Source Discovery",
        scope_id="source_monitoring_source_discovery",
        source_types=[SourceType.SEARCH_API],
        allowed_domains=[plan.domain_constraint] if plan.domain_constraint else [],
        languages=[plan.query_language],
        max_results=plan.max_result_count,
        priority=plan.ranking_score,
        metadata={
            "phase": "source_monitoring_phase4",
            "entity_id": plan.entity_id,
            "source_role": plan.source_role.value,
            "strategy": plan.strategy.value,
        },
    )


def _evidence_from_raw_item(
    plan: SourceDiscoveryPlan,
    execution_id: str,
    raw_item: Any,
    rank: int,
    provider: str,
) -> SourceDiscoveryEvidence:
    metadata = dict(getattr(raw_item, "metadata", {}) or {})
    url = str(getattr(raw_item, "url", "") or "")
    normalized = normalize_source_url(url)
    title = str(getattr(raw_item, "title", "") or "")
    return SourceDiscoveryEvidence(
        evidence_id=build_source_discovery_evidence_id(
            execution_id=execution_id,
            plan_id=plan.plan_id,
            result_rank=rank,
            url=normalized,
            title=title,
        ),
        execution_id=execution_id,
        plan_id=plan.plan_id,
        entity_id=plan.entity_id,
        result_rank=rank,
        title=title,
        url=url,
        normalized_url=normalized,
        root_domain=root_domain_from_url(normalized),
        snippet=str(getattr(raw_item, "raw_text", "") or ""),
        language=str(metadata.get("language") or plan.query_language),
        provider=str(metadata.get("provider") or provider),
        raw_metadata=metadata,
        retrieved_at=str(metadata.get("retrieved_at") or ""),
    )


def _raw_item_to_checkpoint(raw_item: Any) -> dict[str, Any]:
    return {
        "title": str(getattr(raw_item, "title", "") or ""),
        "url": str(getattr(raw_item, "url", "") or ""),
        "raw_text": str(getattr(raw_item, "raw_text", "") or ""),
        "metadata": dict(getattr(raw_item, "metadata", {}) or {}),
    }


def _select_executable_plans(
    plans: tuple[SourceDiscoveryPlan, ...],
    *,
    entity_offset: int,
    max_entities: int | None,
    max_plans: int | None,
) -> tuple[tuple[SourceDiscoveryPlan, ...], tuple[str, ...]]:
    executable = [
        plan for plan in plans if plan.status == DiscoveryPlanStatus.EXECUTABLE
    ]
    entity_order: list[str] = []
    for plan in executable:
        if plan.entity_id not in entity_order:
            entity_order.append(plan.entity_id)
    allowed_entities = set(entity_order[max(0, entity_offset):])
    if max_entities is not None:
        allowed_entities = set(entity_order[max(0, entity_offset):max(0, entity_offset) + max(0, max_entities)])
    selected = [plan for plan in executable if plan.entity_id in allowed_entities]
    if max_plans is not None:
        selected = selected[:max(0, max_plans)]
    selected_ids = {plan.plan_id for plan in selected}
    deferred = tuple(plan.plan_id for plan in executable if plan.plan_id not in selected_ids)
    return tuple(selected), deferred


def _build_query(
    *,
    entity_name: str,
    source_role: SourceRole,
    language: str,
    domain: str | None,
    information_needs: tuple[InformationNeed, ...],
) -> str:
    definition = get_source_role_definition(source_role)
    role_terms = definition.query_terms_by_language.get(language, ())
    role_term = role_terms[0] if role_terms else source_role.value.replace("_", " ")
    need_term = _information_need_query_term(information_needs, language)
    role_piece = f"{role_term} {need_term}".strip()
    if domain:
        return " ".join(f"site:{domain} {entity_name} {role_piece}".split())
    if source_role == SourceRole.OFFICIAL_HOMEPAGE:
        if language == "zh":
            return " ".join(f'"{entity_name}" 官网'.split())
        return " ".join(f'"{entity_name}" official website'.split())
    return " ".join(f'"{entity_name}" {role_piece}'.split())


def _information_need_query_term(
    information_needs: tuple[InformationNeed, ...],
    language: str,
) -> str:
    text = " ".join(
        f"{need.title} {need.description}" for need in information_needs[:3]
    ).casefold()
    if language == "zh":
        if "ai" in text or "artificial intelligence" in text:
            return "人工智能"
        return ""
    if "artificial intelligence" in text or "ai " in f"{text} ":
        return "artificial intelligence"
    return ""


def _score_candidate_plan(
    *,
    assessment: EntityPriorityAssessment,
    source_role: SourceRole,
    role_index: int,
    language: str,
    strategy: DiscoveryStrategy,
    domain: str | None,
    information_needs: tuple[InformationNeed, ...],
) -> float:
    score = 100.0 - role_index * 5
    score += assessment.entity_priority_score / 10
    score += assessment.evidence_readiness_score / 20
    if strategy == DiscoveryStrategy.DOMAIN_FIRST:
        score += 8
    if strategy == DiscoveryStrategy.IDENTITY_RESOLUTION:
        score += 10
    if language == "en":
        score += 2
    if language == "zh" and any(_has_chinese(need.title + need.description) for need in information_needs):
        score += 4
    if not domain and source_role != SourceRole.OFFICIAL_HOMEPAGE:
        score -= 30
    if source_role == SourceRole.OFFICIAL_HOMEPAGE and assessment.evidence_readiness_score < 50:
        score += 15
    return score


def _zero_budget(
    assessment: EntityPriorityAssessment,
    reason: str,
) -> SourceDiscoveryBudget:
    return SourceDiscoveryBudget(
        entity_id=assessment.entity_id,
        priority_tier=assessment.priority_tier,
        maximum_plan_count=0,
        allocated_plan_count=0,
        readiness_score=assessment.evidence_readiness_score,
        needs_domain_verification=True,
        low_evidence_readiness=True,
        probable_official_domain=None,
        rationale=reason,
    )


def _best_official_domain(entity: EntityCandidate) -> str | None:
    candidates = sorted(
        entity.official_domain_candidates,
        key=lambda item: (
            item.verification_status
            != OfficialDomainVerificationStatus.VERIFIED_OFFICIAL,
            item.verification_status
            != OfficialDomainVerificationStatus.PROBABLE_OFFICIAL,
            -item.confidence,
            normalize_domain(item.domain),
        ),
    )
    for candidate in candidates:
        if candidate.verification_status in {
            OfficialDomainVerificationStatus.VERIFIED_OFFICIAL,
            OfficialDomainVerificationStatus.PROBABLE_OFFICIAL,
        }:
            domain = normalize_domain(candidate.domain)
            if domain:
                return domain
    return None


def _supported_languages(entity: EntityCandidate) -> tuple[str, ...]:
    languages = []
    for language in ("en", "zh"):
        if _best_name_for_language(entity, language):
            languages.append(language)
    return tuple(languages or ("en",))


def _best_name_for_language(entity: EntityCandidate, language: str) -> str:
    names = tuple(entity.names_by_language.get(language, ()))
    if names:
        return names[0]
    if language == "en":
        return entity.canonical_name
    return ""


def _entity_identity_domain_payload(
    entity_universe_result: EntityUniverseResult,
) -> list[dict[str, Any]]:
    return [
        {
            "entity_id": entity.entity_id,
            "canonical_name": entity.canonical_name,
            "names_by_language": entity.names_by_language,
            "primary_entity_kind": entity.primary_entity_kind.value,
            "entity_type_codes": entity.entity_type_codes,
            "related_information_need_ids": entity.related_information_need_ids,
            "related_target_career_path_ids": entity.related_target_career_path_ids,
            "official_domain_candidates": [
                domain.to_dict() for domain in entity.official_domain_candidates
            ],
        }
        for entity in sorted(
            entity_universe_result.entity_candidates,
            key=lambda item: item.entity_id,
        )
    ]


def _entity_context(entity: EntityCandidate) -> dict[str, Any]:
    return {
        "entity_id": entity.entity_id,
        "canonical_name": entity.canonical_name,
        "names_by_language": entity.names_by_language,
        "primary_entity_kind": entity.primary_entity_kind.value,
        "official_domains": [
            domain.to_dict() for domain in entity.official_domain_candidates
        ],
    }


def _priority_context(assessment: EntityPriorityAssessment) -> dict[str, Any]:
    return {
        "entity_id": assessment.entity_id,
        "priority_tier": assessment.priority_tier.value,
        "entity_priority_score": assessment.entity_priority_score,
        "evidence_readiness_score": assessment.evidence_readiness_score,
    }


def _build_classifier_prompt(
    *,
    entity_context: dict[str, Any],
    priority_context: dict[str, Any],
    information_needs: tuple[InformationNeed, ...],
    evidence_items: tuple[SourceDiscoveryEvidence, ...],
    controlled_roles: tuple[SourceRole, ...],
) -> str:
    return json.dumps(
        {
            "instructions": [
                "Classify only supplied evidence IDs and URLs.",
                "Use only controlled_roles.",
                "Do not claim freshness, cadence, quality, RSS validity, or source performance.",
                "Return valid JSON as {classifications:[...]} with decision accept, needs_review, or reject.",
            ],
            "entity_context": entity_context,
            "priority_context": priority_context,
            "information_needs": [need.to_dict() for need in information_needs],
            "controlled_roles": [role.value for role in controlled_roles],
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "title": item.title,
                    "url": item.normalized_url,
                    "snippet": item.snippet,
                    "root_domain": item.root_domain,
                }
                for item in evidence_items
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _infer_role_from_path(path: str) -> SourceRole | None:
    hints: tuple[tuple[SourceRole, tuple[str, ...]], ...] = (
        (SourceRole.CAREERS, ("careers", "jobs", "join-us", "recruit")),
        (SourceRole.NEWSROOM, ("newsroom", "news", "media")),
        (SourceRole.PRESS_RELEASES, ("press-release", "press", "releases")),
        (SourceRole.RESEARCH_PUBLICATIONS, ("research", "publication", "papers")),
        (SourceRole.PORTFOLIO, ("portfolio", "companies", "investments")),
        (SourceRole.POLICY_UPDATES, ("policy", "guidance", "regulation")),
        (SourceRole.REPORTS_OR_DATA, ("reports", "data", "statistics")),
        (SourceRole.EVENTS_OR_PROGRAMS, ("events", "programs", "initiatives")),
        (SourceRole.BLOG, ("blog", "stories", "articles")),
    )
    for role, tokens in hints:
        if any(token in path for token in tokens):
            return role
    if path in {"", "/"}:
        return SourceRole.OFFICIAL_HOMEPAGE
    return None


def _looks_like_job_detail(path: str) -> bool:
    return bool(
        re.search(r"/jobs?/[0-9a-f-]{6,}", path)
        or "job-detail" in path
        or "jobid=" in path
        or "/apply/" in path
    )


def _looks_like_one_off_article(path: str) -> bool:
    return bool(re.search(r"/20[0-9]{2}/[01][0-9]/", path) or re.search(r"/[0-9]{4,}($|[-_/])", path))


def _looks_like_third_party_directory(root_domain: str, path: str) -> bool:
    third_party = {
        "crunchbase.com",
        "linkedin.com",
        "wikipedia.org",
        "pitchbook.com",
        "glassdoor.com",
        "indeed.com",
    }
    return root_domain in third_party or "/company/" in path and root_domain.endswith("linkedin.com")


def _unsupported_social_domains() -> set[str]:
    return {
        "facebook.com",
        "instagram.com",
        "x.com",
        "twitter.com",
        "linkedin.com",
        "youtube.com",
        "tiktok.com",
        "weibo.com",
        "wechat.com",
    }


def _domain_matches(root_domain: str, official_domain: str) -> bool:
    root = normalize_domain(root_domain)
    official = normalize_domain(official_domain)
    return root == official or root.endswith(f".{official}")


def _name_appears(evidence: SourceDiscoveryEvidence, entity: EntityCandidate) -> bool:
    haystack = f"{evidence.title} {evidence.snippet}".casefold()
    names = [entity.canonical_name]
    for values in entity.names_by_language.values():
        names.extend(values)
    return any(name and name.casefold() in haystack for name in names)


def _officiality_from_text(value: str) -> CandidateOfficialityStatus:
    normalized = value.strip().casefold()
    mapping = {
        "probably_official": CandidateOfficialityStatus.PROBABLY_OFFICIAL,
        "official_domain_match": CandidateOfficialityStatus.OFFICIAL_DOMAIN_MATCH,
        "official": CandidateOfficialityStatus.PROBABLY_OFFICIAL,
        "unresolved": CandidateOfficialityStatus.UNRESOLVED,
        "third_party": CandidateOfficialityStatus.THIRD_PARTY,
        "reject": CandidateOfficialityStatus.REJECTED,
        "rejected": CandidateOfficialityStatus.REJECTED,
    }
    return mapping.get(normalized, CandidateOfficialityStatus.UNRESOLVED)


def _contains_forbidden_classifier_claim(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False).casefold()
    forbidden = (
        "publishes daily",
        "weekly publication",
        "publication cadence",
        "freshness",
        "fresh source",
        "historical signal yield",
        "observed source performance",
        "source quality",
        "rss validity",
        "valid rss",
        "approved source",
    )
    return any(pattern in text for pattern in forbidden)


def _has_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _extract_llm_response_text(response: Any) -> str | None:
    choices = getattr(response, "choices", None)
    if not choices:
        return None
    message = getattr(choices[0], "message", None)
    if message is None:
        return None
    return getattr(message, "content", None)


def _normalize_json_response_text(response_text: str) -> str:
    text = response_text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    return text
