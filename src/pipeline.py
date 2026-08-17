from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib
from typing import TYPE_CHECKING, Any

from src.career_signal_routing import route_scored_career_signals
from src.career_intelligence_interpretation_runtime import (
    InterpretationExecutor,
    interpret_routed_intelligence,
)
from src.career_intelligence_brief import build_career_intelligence_brief
from src.database.planning_identity import (
    build_planning_input_fingerprint,
    hash_user_profile,
)
from src.database.source_identity import fingerprint_raw_item
from src.models import (
    AIFilterExecutionReport,
    AIFilterResult,
    CareerSignal,
    PipelineRunOutput,
    PipelineSummary,
    RawItem,
    RawItemFilterStatus,
    SearchAPIExecutionReport,
    SearchPlan,
    SearchPlanExecutionStatus,
    SearchQuery,
    SearchScope,
    SignalCategory,
    SourceType,
    TargetCareerPath,
    UserProfile,
    utc_now_iso,
)
from src.signal_identity import build_signal_id

if TYPE_CHECKING:
    from src.database.repositories.career_signal_repository import (
        CareerSignalRepository,
        CareerSignalUpsertSummary,
        CareerSignalWrite,
    )
    from src.database.repositories.source_item_repository import (
        SourceItemRepository,
        SourceItemUpsertSummary,
    )
    from src.database.repositories.planning_bundle_repository import (
        PlanningArtifactWrite,
        PlanningBundleRepository,
        PlanningBundlePersistSummary,
    )
    from src.database.repositories.pipeline_run_repository import (
        PipelineRunRecord,
        PipelineRunRepository,
    )
    from src.database.repositories.source_execution_repository import (
        SourceExecutionRepository,
    )
    from src.database.repositories.filter_decision_repository import (
        FilterDecisionRepository,
    )


PIPELINE_VERSION = "v1"
PIPELINE_PHASE = "phase_10_normalizer_to_career_signal"
LIVE_EXECUTION_MODE = "live"
_PIPELINE_RUN_ERROR_MESSAGE_LIMIT = 1000


@dataclass(frozen=True)
class FilterCandidate:
    source_item_id: int
    raw_item: RawItem
    raw_item_index: int


class MockPipeline:
    """
    V1 Phase 10 pipeline.

    This phase executes:
    - Search API planning
    - RSS feed acquisition
    - selected website acquisition
    - AI Filter
    - normalization to CareerSignal
    - priority assessment and scoring
    - opportunity/intelligence routing
    - career intelligence interpretation
    - final brief assembly

    The pipeline still does not perform global CareerSignal deduplication.
    """

    def __init__(
        self,
        raw_item_loader: Callable[[], list[RawItem]],
        user_profile_loader: Callable[[], UserProfile],
        search_scope_loader: Callable[[], SearchScope],
        career_path_generator: Callable[[UserProfile], list[TargetCareerPath]],
        search_query_generator: Callable[[list[TargetCareerPath]], list[SearchQuery]],
        search_plan_builder: Callable[
            [list[SearchQuery], SearchScope],
            list[SearchPlan],
        ],
        search_api_executor: Callable[..., SearchAPIExecutionReport],
        rss_executor: Callable[..., tuple[list[RawItem], int]],
        selected_website_executor: Callable[..., tuple[list[RawItem], int]],
        ai_filter_executor: Callable[
            [list[RawItem], UserProfile, list[TargetCareerPath]],
            AIFilterExecutionReport,
        ],
        normalizer: Callable[
            [list[RawItem], list[AIFilterResult]],
            list[CareerSignal],
        ],
        source_item_repository: "SourceItemRepository | None" = None,
        career_signal_repository: "CareerSignalRepository | None" = None,
        planning_bundle_repository: "PlanningBundleRepository | None" = None,
        user_preferences_loader: Callable[[], dict[str, Any]] | None = None,
        planning_model_provider: str | None = None,
        planning_model_name: str | None = None,
        planning_prompt_version: str | None = None,
        planning_generator_config: dict[str, Any] | None = None,
        planning_force_refresh: bool = False,
        profile_source_path: str | None = None,
        profile_source_file_hash: str | None = None,
        profile_schema_version: str | None = None,
        planning_artifact_loader: (
            Callable[[], tuple["PlanningArtifactWrite", ...]] | None
        ) = None,
        pipeline_run_repository: "PipelineRunRepository | None" = None,
        source_execution_repository: "SourceExecutionRepository | None" = None,
        execution_mode: str = LIVE_EXECUTION_MODE,
        filter_decision_repository: "FilterDecisionRepository | None" = None,
        ai_filter_execution_mode: str | None = None,
        ai_filter_provider: str | None = None,
        ai_filter_model: str | None = None,
        priority_assessor: Callable[..., Any] | None = None,
        priority_as_of_loader: Callable[[], str] | None = None,
        interpretation_executor: InterpretationExecutor | None = None,
    ) -> None:
        self.raw_item_loader = raw_item_loader
        self.user_profile_loader = user_profile_loader
        self.search_scope_loader = search_scope_loader
        self.career_path_generator = career_path_generator
        self.search_query_generator = search_query_generator
        self.search_plan_builder = search_plan_builder
        self.search_api_executor = search_api_executor
        self.rss_executor = rss_executor
        self.selected_website_executor = selected_website_executor
        self.ai_filter_executor = ai_filter_executor
        self.normalizer = normalizer
        self.source_item_repository = source_item_repository
        self.career_signal_repository = career_signal_repository
        self.planning_bundle_repository = planning_bundle_repository
        self.user_preferences_loader = user_preferences_loader
        self.planning_model_provider = planning_model_provider
        self.planning_model_name = planning_model_name
        self.planning_prompt_version = planning_prompt_version
        self.planning_generator_config = planning_generator_config or {}
        self.planning_force_refresh = planning_force_refresh
        self.profile_source_path = profile_source_path
        self.profile_source_file_hash = profile_source_file_hash
        self.profile_schema_version = profile_schema_version
        self.planning_artifact_loader = planning_artifact_loader
        self.pipeline_run_repository = pipeline_run_repository
        self.source_execution_repository = source_execution_repository
        self.filter_decision_repository = filter_decision_repository
        self.execution_mode = _normalize_execution_mode(execution_mode)
        self.ai_filter_execution_mode = ai_filter_execution_mode
        self.ai_filter_provider = ai_filter_provider
        self.ai_filter_model = ai_filter_model
        self.priority_assessor = priority_assessor
        self.priority_as_of_loader = priority_as_of_loader
        self.interpretation_executor = interpretation_executor
        self.planning_bundle_id: int | None = None
        self.planning_input_fingerprint: str | None = None
        self.planning_generation_mode: str | None = None
        self.pipeline_run_id: str | None = None
        self.pipeline_run_stage: str | None = None
        self._pipeline_run_closed = False
        self._pipeline_run_metadata: dict[str, Any] = {}
        self._pipeline_run_summary: dict[str, Any] = {}
        self._planning_search_plan_row_ids: dict[str, int] = {}
        self._provenance_persisted_fingerprints: set[str] = set()
        self._new_source_item_ids_this_run: set[int] = set()
        self._historical_duplicate_source_item_ids: set[int] = set()
        self._filter_items_registered = False

    def run(self) -> PipelineRunOutput:
        """
        Run the Phase 10 pipeline.
        """

        self._prepare_pipeline_execution()
        self._start_pipeline_run()

        try:
            self._set_pipeline_run_stage("initialization")
            self._validate_repository_configuration()

            self._set_pipeline_run_stage("input_loading")
            user_profile = self.user_profile_loader()
            user_preferences = self._load_user_preferences_for_planning()
            search_scope = self.search_scope_loader()

            self._set_pipeline_run_stage("planning")
            (
                target_career_paths,
                search_queries,
                search_plans,
            ) = self._resolve_planning(
                user_profile=user_profile,
                search_scope=search_scope,
                user_preferences=user_preferences,
            )

            self._set_pipeline_run_stage("planning_bundle_attachment")
            self._attach_pipeline_run_planning_bundle()

            self._set_pipeline_run_stage("search_plan_registration")
            self._register_run_search_plans(search_plans)

            self._set_pipeline_run_stage("external_search")
            mock_raw_items = self.raw_item_loader()

            execution_lifecycle = (
                self if self._source_execution_tracking_enabled() else None
            )
            if execution_lifecycle is not None and not search_scope.enable_search_api:
                self.account_search_api_disabled(search_plans)
                search_api_report = _disabled_search_api_report(search_plans)
            else:
                search_api_report = (
                    self.search_api_executor(search_plans, execution_lifecycle)
                    if execution_lifecycle is not None
                    else self.search_api_executor(search_plans)
                )
            search_api_raw_items = search_api_report.raw_items
            executed_search_api_plan_count = search_api_report.executed_plan_count

            rss_lifecycle = (
                execution_lifecycle
                if (
                    execution_lifecycle is not None
                    and search_scope is not None
                    and search_scope.enable_rss
                )
                else None
            )
            if execution_lifecycle is not None and not search_scope.enable_rss:
                rss_raw_items, executed_rss_feed_count = [], 0
            else:
                rss_raw_items, executed_rss_feed_count = (
                    self.rss_executor(search_scope, search_plans, rss_lifecycle)
                    if execution_lifecycle is not None
                    else self.rss_executor(search_scope, search_plans)
                )

            (
                selected_website_raw_items,
                executed_selected_website_count,
            ) = (
                ([], 0)
                if (
                    execution_lifecycle is not None
                    and not search_scope.enable_selected_websites
                )
                else (
                    self.selected_website_executor(
                        search_scope,
                        search_plans,
                        (
                            execution_lifecycle
                            if search_scope.enable_selected_websites
                            else None
                        ),
                    )
                    if execution_lifecycle is not None
                    else self.selected_website_executor(search_scope, search_plans)
                )
            )

            external_raw_items = (
                search_api_raw_items
                + rss_raw_items
                + selected_website_raw_items
            )
            persistable_external_raw_items = [
                raw_item
                for raw_item in external_raw_items
                if raw_item.metadata.get("mode") != "dry_run"
            ]
            unpersisted_external_raw_items = [
                raw_item
                for raw_item in persistable_external_raw_items
                if fingerprint_raw_item(raw_item)
                not in self._provenance_persisted_fingerprints
            ]

            self._set_pipeline_run_stage("source_item_persistence")
            self._persist_external_raw_items(unpersisted_external_raw_items)

            raw_items = mock_raw_items + external_raw_items
            self._pipeline_run_summary["raw_item_count"] = len(raw_items)

            if self._filter_decision_tracking_enabled():
                ai_filter_report = self._execute_filter_with_provenance(
                    external_raw_items=external_raw_items,
                    external_raw_item_index_offset=len(mock_raw_items),
                    user_profile=user_profile,
                    target_career_paths=target_career_paths,
                )
            else:
                self._set_pipeline_run_stage("ai_filter")
                ai_filter_report = self.ai_filter_executor(
                    raw_items,
                    user_profile,
                    target_career_paths,
                )
            filtered_raw_items = ai_filter_report.filtered_raw_items
            ai_filter_results = ai_filter_report.ai_filter_results
            ai_filter_executed_count = ai_filter_report.executed_count
            eligible_source_item_ids_by_signal_id = (
                self._resolve_eligible_source_item_ids_by_signal_id(
                    raw_items=raw_items,
                    raw_item_statuses=ai_filter_report.raw_item_statuses,
                    persistable_external_raw_items=persistable_external_raw_items,
                )
            )

            self._set_pipeline_run_stage("normalization")
            career_signals = self.normalizer(
                filtered_raw_items,
                ai_filter_results,
            )

            self._set_pipeline_run_stage("career_signal_persistence")
            self._persist_career_signals(
                career_signals=career_signals,
                eligible_source_item_ids_by_signal_id=(
                    eligible_source_item_ids_by_signal_id
                ),
            )
            priority_result = self._assess_career_signal_priorities(
                career_signals=career_signals,
                filtered_raw_items=filtered_raw_items,
                ai_filter_results=ai_filter_results,
                user_profile=user_profile,
                user_preferences=user_preferences,
                target_career_paths=target_career_paths,
            )
            career_signal_routing = route_scored_career_signals(
                priority_result.scored_career_signals
            )
            self._set_pipeline_run_stage("career_intelligence_interpretation")
            career_intelligence_interpretation = interpret_routed_intelligence(
                routing_result=career_signal_routing,
                target_career_paths=target_career_paths,
                user_preferences=user_preferences,
                interpretation_executor=self.interpretation_executor,
            )
            self._set_pipeline_run_stage("career_intelligence_brief")
            generated_at = utc_now_iso()
            career_intelligence_brief = build_career_intelligence_brief(
                routing_result=career_signal_routing,
                interpretation=career_intelligence_interpretation,
                target_career_paths=target_career_paths,
                generated_at=generated_at,
            )

            deferred_search_api_plan_count = len(
                [
                    status
                    for status in search_api_report.plan_statuses
                    if status.status
                    in {"deferred_due_to_limit", "skipped_due_to_offset"}
                ]
            )
            failed_before_filter_count = len(
                [
                    status
                    for status in ai_filter_report.raw_item_statuses
                    if status.status == "failed_before_filter"
                ]
            )
            ai_filter_accepted_count = len(
                [
                    status
                    for status in ai_filter_report.raw_item_statuses
                    if status.status == "processed_accepted"
                ]
            )
            ai_filter_rejected_count = len(
                [
                    status
                    for status in ai_filter_report.raw_item_statuses
                    if status.status == "processed_rejected"
                ]
            )
            self._pipeline_run_summary.update(
                {
                    "accepted_item_count": ai_filter_accepted_count,
                    "rejected_item_count": ai_filter_rejected_count,
                    "career_signal_count": len(career_signals),
                }
            )

            summary = PipelineSummary(
                total_raw_items=len(raw_items),
                total_mock_raw_items=len(mock_raw_items),
                total_search_api_raw_items=len(search_api_raw_items),
                total_rss_raw_items=len(rss_raw_items),
                total_selected_website_raw_items=len(selected_website_raw_items),
                total_target_career_paths=len(target_career_paths),
                total_search_queries=len(search_queries),
                total_search_plans=len(search_plans),
                total_search_api_plans_executed=executed_search_api_plan_count,
                total_search_api_plans_deferred=deferred_search_api_plan_count,
                total_search_api_result_failures=len(
                    [
                        diagnostic
                        for diagnostic in search_api_report.result_diagnostics
                        if diagnostic.status == "failed_parse"
                    ]
                ),
                total_rss_feeds_executed=executed_rss_feed_count,
                total_selected_websites_executed=(
                    executed_selected_website_count
                ),
                total_ai_filter_results=len(ai_filter_results),
                total_raw_items_sent_to_ai_filter=ai_filter_executed_count,
                total_raw_items_failed_before_filter=failed_before_filter_count,
                total_filtered_raw_items=len(filtered_raw_items),
                total_rejected_raw_items=(
                    len(ai_filter_results) - len(filtered_raw_items)
                ),
                total_ai_filter_accepted=ai_filter_accepted_count,
                total_ai_filter_rejected=ai_filter_rejected_count,
                total_duplicate_raw_item_urls=_count_duplicate_urls(raw_items),
                total_career_signals=len(career_signals),
                user_profile_loaded=user_profile is not None,
                search_scope_loaded=search_scope is not None,
                search_api_executed=executed_search_api_plan_count > 0,
                rss_executed=executed_rss_feed_count > 0,
                selected_websites_executed=executed_selected_website_count > 0,
                ai_filter_executed=ai_filter_executed_count > 0,
                pipeline_status="normalization_completed",
            )

            self._set_pipeline_run_stage("output_construction")
            return PipelineRunOutput(
                pipeline_version=PIPELINE_VERSION,
                phase=PIPELINE_PHASE,
                generated_at=generated_at,
                summary=summary,
                user_profile=user_profile,
                search_scope=search_scope,
                target_career_paths=target_career_paths,
                search_queries=search_queries,
                search_plans=search_plans,
                search_api_plan_statuses=search_api_report.plan_statuses,
                search_api_result_diagnostics=(
                    search_api_report.result_diagnostics
                ),
                raw_items=raw_items,
                raw_item_filter_statuses=ai_filter_report.raw_item_statuses,
                ai_filter_results=ai_filter_results,
                filtered_raw_items=filtered_raw_items,
                career_signals=career_signals,
                scored_career_signals=list(
                    priority_result.scored_career_signals
                ),
                priority_assessment_diagnostics=list(
                    priority_result.diagnostics
                ),
                career_signal_routing=career_signal_routing,
                career_intelligence_interpretation=(
                    career_intelligence_interpretation
                ),
                career_intelligence_brief=career_intelligence_brief,
            )
        except Exception as error:
            self._fail_pipeline_run(error)
            raise

    def complete_pipeline_run(
        self,
        output: PipelineRunOutput,
    ) -> "PipelineRunRecord | None":
        if not self._pipeline_run_lifecycle_enabled():
            return None

        if self.pipeline_run_id is None or self._pipeline_run_closed:
            raise RuntimeError(
                "PipelineRun completion requested without an active run."
            )

        from src.database.repositories.pipeline_run_repository import (
            PipelineRunCompletion,
        )

        if self._source_execution_tracking_enabled():
            self._set_pipeline_run_stage("search_plan_accounting_validation")
            try:
                self._update_execution_accounting_summary()
            except Exception as error:
                self._fail_pipeline_run(error)
                raise RuntimeError(
                    "PipelineRun SearchPlan accounting validation failed."
                ) from error

        if self._filter_decision_tracking_enabled():
            self._set_pipeline_run_stage(
                "career_signal_materialization_validation"
            )
            try:
                self._update_career_signal_materialization_summary()
            except Exception as error:
                self._fail_pipeline_run(error)
                raise RuntimeError(
                    "Accepted SourceItem CareerSignal materialization "
                    "validation failed."
                ) from error

            self._set_pipeline_run_stage("filter_accounting_validation")
            try:
                self._update_filter_accounting_summary()
            except Exception as error:
                self._fail_pipeline_run(error)
                raise RuntimeError(
                    "PipelineRun filter accounting validation failed."
                ) from error

        self._set_pipeline_run_stage("pipeline_run_completion")
        completion = PipelineRunCompletion(
            summary=self._build_pipeline_run_completion_summary(output),
            metadata=dict(self._pipeline_run_metadata),
        )

        try:
            record = self.pipeline_run_repository.complete_run(
                self.pipeline_run_id,
                completion,
                require_planning_bundle=(
                    self.planning_bundle_repository is not None
                ),
            )
        except Exception as error:
            self._fail_pipeline_run(error)
            raise RuntimeError("PipelineRun completion failed.") from error

        self._pipeline_run_closed = True
        _report_pipeline_run_completed(record, output)
        return record

    def fail_pipeline_run(
        self,
        error: BaseException,
        *,
        failure_stage: str,
    ) -> None:
        self._set_pipeline_run_stage(failure_stage)
        self._fail_pipeline_run(error)

    def _prepare_pipeline_execution(self) -> None:
        if (
            self._pipeline_run_lifecycle_enabled()
            and self.pipeline_run_id is not None
            and not self._pipeline_run_closed
        ):
            raise RuntimeError(
                "Cannot start another execution while a PipelineRun is still running."
            )

        self.planning_bundle_id = None
        self.planning_input_fingerprint = None
        self.planning_generation_mode = None
        self.pipeline_run_id = None
        self.pipeline_run_stage = None
        self._pipeline_run_closed = False
        self._pipeline_run_metadata = {}
        self._pipeline_run_summary = {}
        self._planning_search_plan_row_ids = {}
        self._provenance_persisted_fingerprints = set()
        self._new_source_item_ids_this_run = set()
        self._historical_duplicate_source_item_ids = set()
        self._filter_items_registered = False

    def _pipeline_run_lifecycle_enabled(self) -> bool:
        return (
            self.pipeline_run_repository is not None
            and self.execution_mode == LIVE_EXECUTION_MODE
        )

    def _source_execution_tracking_enabled(self) -> bool:
        return (
            self.source_execution_repository is not None
            and self.planning_bundle_repository is not None
            and self._pipeline_run_lifecycle_enabled()
        )

    def _filter_decision_tracking_enabled(self) -> bool:
        return (
            self.filter_decision_repository is not None
            and self.source_item_repository is not None
            and self.career_signal_repository is not None
            and self._source_execution_tracking_enabled()
        )

    def _start_pipeline_run(self) -> None:
        if not self._pipeline_run_lifecycle_enabled():
            return

        from src.database.repositories.pipeline_run_repository import (
            PipelineRunStart,
        )

        self._pipeline_run_metadata = {
            "planning_persistence_enabled": (
                self.planning_bundle_repository is not None
            ),
            "source_item_persistence_enabled": (
                self.source_item_repository is not None
            ),
            "career_signal_persistence_enabled": (
                self.career_signal_repository is not None
            ),
            "source_execution_persistence_enabled": (
                self.source_execution_repository is not None
            ),
            "filter_decision_persistence_enabled": (
                self._filter_decision_tracking_enabled()
            ),
        }

        try:
            record = self.pipeline_run_repository.start_run(
                PipelineRunStart(
                    pipeline_version=PIPELINE_VERSION,
                    phase=PIPELINE_PHASE,
                    execution_mode=self.execution_mode,
                    metadata=dict(self._pipeline_run_metadata),
                )
            )
        except Exception as error:
            raise RuntimeError(
                "PipelineRun start failed before planning began."
            ) from error

        self.pipeline_run_id = record.run_id
        self._set_pipeline_run_stage("initialization")
        print(
            "Pipeline Run started: "
            f"run_id={record.run_id}, mode={self.execution_mode}"
        )

    def _attach_pipeline_run_planning_bundle(self) -> None:
        if not self._pipeline_run_lifecycle_enabled():
            return

        if self.planning_bundle_repository is None:
            return

        if self.pipeline_run_id is None:
            raise RuntimeError(
                "Planning Bundle attachment failed: no active PipelineRun."
            )

        if self.planning_bundle_id is None:
            raise RuntimeError(
                "Planning Bundle attachment failed: planning completed without "
                "a selected Bundle ID."
            )

        try:
            self.pipeline_run_repository.attach_planning_bundle(
                self.pipeline_run_id,
                self.planning_bundle_id,
            )
        except Exception as error:
            raise RuntimeError(
                "Planning Bundle attachment to PipelineRun failed."
            ) from error

        self._pipeline_run_summary["planning_bundle_id"] = (
            self.planning_bundle_id
        )
        if self.planning_generation_mode is not None:
            self._pipeline_run_summary["planning_generation_mode"] = (
                self.planning_generation_mode
            )
            self._pipeline_run_summary["planning_bundle_reused"] = (
                self.planning_generation_mode == "database_reuse"
            )

    def _register_run_search_plans(
        self,
        search_plans: list[SearchPlan],
    ) -> None:
        if not self._source_execution_tracking_enabled():
            return
        if self.pipeline_run_id is None or self.planning_bundle_id is None:
            raise RuntimeError(
                "SearchPlan registration requires an attached Planning Bundle."
            )

        try:
            registration = (
                self.source_execution_repository.register_run_search_plans(
                    self.pipeline_run_id
                )
            )
            plan_rows = self.planning_bundle_repository.list_plans_for_bundle(
                self.planning_bundle_id
            )
        except Exception as error:
            raise RuntimeError(
                "Run SearchPlan registration failed before external search."
            ) from error

        plan_row_ids: dict[str, int] = {}
        for row in plan_rows:
            plan_identity = str(row["plan_identity"])
            if plan_identity in plan_row_ids:
                raise RuntimeError(
                    "Planning Bundle contains duplicate SearchPlan identities."
                )
            plan_row_ids[plan_identity] = int(row["search_plan_row_id"])

        active_plan_ids = [plan.plan_id for plan in search_plans]
        if (
            len(active_plan_ids) != len(set(active_plan_ids))
            or set(plan_row_ids) != set(active_plan_ids)
            or registration.registered_plan_count != len(search_plans)
        ):
            raise RuntimeError(
                "Run SearchPlan registration does not match active planning output."
            )

        self._planning_search_plan_row_ids = plan_row_ids
        self._pipeline_run_summary["total_search_plan_count"] = len(search_plans)

    def account_search_api_plan_selection(
        self,
        *,
        search_plans: list[SearchPlan],
        executable_plans: list[SearchPlan],
        selected_plans: list[SearchPlan],
        plan_offset: int,
        max_plans: int,
    ) -> None:
        if not self._source_execution_tracking_enabled():
            return
        if self.pipeline_run_id is None:
            raise RuntimeError("SearchPlan selection has no active PipelineRun.")

        self._set_pipeline_run_stage("search_plan_skip_accounting")
        active_ids = [plan.plan_id for plan in search_plans]
        executable_ids = [plan.plan_id for plan in executable_plans]
        selected_ids = {plan.plan_id for plan in selected_plans}
        active_id_set = set(active_ids)
        executable_id_set = set(executable_ids)

        if (
            len(active_ids) != len(active_id_set)
            or len(executable_ids) != len(executable_id_set)
            or len(selected_plans) != len(selected_ids)
            or not executable_id_set.issubset(active_id_set)
            or not selected_ids.issubset(executable_id_set)
            or active_id_set != set(self._planning_search_plan_row_ids)
        ):
            raise RuntimeError("Search API Plan selection is inconsistent.")

        skipped_by_reason: dict[str, list[int]] = {
            "plan_offset": [],
            "max_plans_limit": [],
            "not_executable_for_search_api": [],
        }
        for index, plan in enumerate(executable_plans):
            if plan.plan_id in selected_ids:
                continue
            reason = "plan_offset" if index < plan_offset else "max_plans_limit"
            skipped_by_reason[reason].append(
                self._planning_search_plan_row_ids[plan.plan_id]
            )

        skipped_by_reason["not_executable_for_search_api"] = [
            self._planning_search_plan_row_ids[plan.plan_id]
            for plan in search_plans
            if plan.plan_id not in executable_id_set
        ]

        try:
            for reason, planning_search_plan_ids in skipped_by_reason.items():
                if not planning_search_plan_ids:
                    continue
                self.source_execution_repository.mark_plans_skipped(
                    self.pipeline_run_id,
                    planning_search_plan_ids,
                    reason,
                    metadata={
                        "plan_offset": plan_offset,
                        "max_plans": max_plans,
                    },
                )
        except Exception as error:
            raise RuntimeError("SearchPlan skip accounting failed.") from error
        self._set_pipeline_run_stage("external_search")

    def account_search_api_disabled(
        self,
        search_plans: list[SearchPlan],
    ) -> None:
        if not self._source_execution_tracking_enabled():
            return
        if self.pipeline_run_id is None:
            raise RuntimeError("Search API disable accounting has no active Run.")
        self._set_pipeline_run_stage("search_plan_skip_accounting")
        try:
            self.source_execution_repository.mark_plans_skipped(
                self.pipeline_run_id,
                [
                    self._planning_search_plan_row_ids[plan.plan_id]
                    for plan in search_plans
                ],
                "search_api_disabled",
            )
        except Exception as error:
            raise RuntimeError("Disabled Search API accounting failed.") from error
        self._set_pipeline_run_stage("external_search")

    def start_search_plan_source_execution(
        self,
        *,
        search_plan: SearchPlan,
        selection_order: int,
        provider: str,
        execution_mode: str,
    ) -> int:
        if not self._source_execution_tracking_enabled():
            raise RuntimeError("SourceExecution tracking is not enabled.")
        if self.pipeline_run_id is None:
            raise RuntimeError("SourceExecution start has no active PipelineRun.")
        planning_search_plan_id = self._planning_search_plan_row_ids.get(
            search_plan.plan_id
        )
        if planning_search_plan_id is None:
            raise RuntimeError(
                "Active SearchPlan has no exact Planning Bundle row mapping."
            )

        from src.database.repositories.source_execution_repository import (
            SourceExecutionStart,
        )

        self._set_pipeline_run_stage("source_execution_start")
        try:
            result = self.source_execution_repository.start_search_plan_execution(
                self.pipeline_run_id,
                planning_search_plan_id,
                SourceExecutionStart(
                    source_type=SourceType.SEARCH_API.value,
                    provider=provider,
                    source_key=search_plan.plan_id,
                    source_name=search_plan.query_text,
                    execution_mode=execution_mode,
                    requested_result_limit=search_plan.max_results,
                    metadata={
                        "plan_identity": search_plan.plan_id,
                        "query_identity": search_plan.query_id,
                    },
                ),
                selection_order=selection_order,
            )
        except Exception as error:
            raise RuntimeError("SearchPlan SourceExecution start failed.") from error
        self._set_pipeline_run_stage("external_search")

        return result.source_execution.source_execution_id

    def start_config_source_execution(
        self,
        *,
        source_type: SourceType,
        source_key: str,
        source_name: str,
        source_locator: str,
        provider: str,
        execution_mode: str,
        requested_result_limit: int | None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        if not self._source_execution_tracking_enabled():
            raise RuntimeError("SourceExecution tracking is not enabled.")
        if self.pipeline_run_id is None:
            raise RuntimeError("SourceExecution start has no active PipelineRun.")

        from src.database.repositories.source_execution_repository import (
            SourceExecutionStart,
        )

        self._set_pipeline_run_stage("source_execution_start")
        try:
            record = self.source_execution_repository.start_source_execution(
                self.pipeline_run_id,
                SourceExecutionStart(
                    source_type=source_type.value,
                    provider=provider,
                    source_key=source_key,
                    source_name=source_name,
                    source_locator=source_locator,
                    execution_mode=execution_mode,
                    requested_result_limit=requested_result_limit,
                    metadata=metadata,
                ),
            )
        except Exception as error:
            raise RuntimeError("Config-driven SourceExecution start failed.") from error
        self._set_pipeline_run_stage("external_search")

        return record.source_execution_id

    def complete_source_execution(
        self,
        *,
        source_execution_id: int,
        raw_items: list[RawItem],
    ) -> None:
        from src.database.repositories.source_execution_repository import (
            SourceExecutionCompletion,
            SourceItemDiscoveryWrite,
        )

        try:
            persistable_raw_items = [
                raw_item
                for raw_item in raw_items
                if raw_item.metadata.get("mode") != "dry_run"
            ]
            discoverable_raw_items = (
                persistable_raw_items
                if self.source_item_repository is not None
                else []
            )
            self._set_pipeline_run_stage("source_item_persistence")
            persistence_results = (
                self._persist_external_raw_items_with_outcomes(
                    discoverable_raw_items
                )
            )

            self._set_pipeline_run_stage("source_item_discovery")
            discoveries: list[SourceItemDiscoveryWrite] = []
            seen_source_item_ids: set[int] = set()
            for fallback_position, (raw_item, persistence) in enumerate(
                zip(discoverable_raw_items, persistence_results, strict=True)
            ):
                source_item_id = persistence.source_item_id
                self._provenance_persisted_fingerprints.add(
                    fingerprint_raw_item(raw_item)
                )
                if source_item_id in seen_source_item_ids:
                    continue
                seen_source_item_ids.add(source_item_id)
                if persistence.created_new:
                    self._new_source_item_ids_this_run.add(source_item_id)
                elif source_item_id not in self._new_source_item_ids_this_run:
                    self._historical_duplicate_source_item_ids.add(source_item_id)
                discoveries.append(
                    SourceItemDiscoveryWrite(
                        source_item_id=source_item_id,
                        result_position=_raw_item_result_position(
                            raw_item,
                            fallback_position,
                        ),
                        metadata={
                            "identity_outcome": (
                                "created_new"
                                if persistence.created_new
                                else "existing"
                            ),
                            "source_item_fingerprint": persistence.fingerprint,
                        },
                    )
                )

            if discoveries:
                self.source_execution_repository.record_discoveries(
                    source_execution_id,
                    discoveries,
                )

            self._set_pipeline_run_stage("source_execution_completion")
            self.source_execution_repository.complete_execution(
                source_execution_id,
                SourceExecutionCompletion(
                    returned_item_count=len(raw_items),
                    discovered_item_count=len(discoveries),
                ),
            )
        except Exception as error:
            self._fail_source_execution_after_error(source_execution_id, error)
            raise
        self._set_pipeline_run_stage("external_search")

    def fail_source_execution(
        self,
        *,
        source_execution_id: int,
        error: BaseException,
    ) -> None:
        self._fail_source_execution_after_error(source_execution_id, error)

    def _fail_source_execution_after_error(
        self,
        source_execution_id: int,
        error: BaseException,
    ) -> None:
        from src.database.repositories.source_execution_repository import (
            SourceExecutionFailure,
        )

        try:
            self.source_execution_repository.fail_execution(
                source_execution_id,
                SourceExecutionFailure(
                    error_type=type(error).__name__,
                    error_message=_bounded_pipeline_run_error_message(error),
                ),
            )
        except Exception as lifecycle_error:
            _add_pipeline_run_failure_note(error, lifecycle_error)
            raise error

    def _resolve_source_item_id(self, raw_item: RawItem) -> int:
        if self.source_item_repository is None:
            raise RuntimeError(
                "SourceItem discovery requires SourceItemRepository persistence."
            )
        try:
            row = self.source_item_repository.get_by_fingerprint(
                fingerprint_raw_item(raw_item)
            )
        except Exception as error:
            raise RuntimeError(
                "SourceItem discovery fingerprint lookup failed."
            ) from error
        if row is None:
            raise RuntimeError(
                "Persisted RawItem could not be resolved for discovery."
            )
        return int(row["source_item_id"])

    def _set_pipeline_run_stage(self, stage: str) -> None:
        self.pipeline_run_stage = stage

    def _fail_pipeline_run(self, error: BaseException) -> None:
        if (
            not self._pipeline_run_lifecycle_enabled()
            or self.pipeline_run_id is None
            or self._pipeline_run_closed
        ):
            return

        from src.database.repositories.pipeline_run_repository import (
            PipelineRunFailure,
        )

        stage = self.pipeline_run_stage or "initialization"
        failure = PipelineRunFailure(
            failure_stage=stage,
            error_type=type(error).__name__,
            error_message=_bounded_pipeline_run_error_message(error),
            summary=dict(self._pipeline_run_summary),
            metadata=dict(self._pipeline_run_metadata),
        )

        try:
            self.pipeline_run_repository.fail_run(
                self.pipeline_run_id,
                failure,
            )
        except Exception as lifecycle_error:
            _add_pipeline_run_failure_note(error, lifecycle_error)
        finally:
            self._pipeline_run_closed = True

    def _build_pipeline_run_completion_summary(
        self,
        output: PipelineRunOutput,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = dict(self._pipeline_run_summary)
        summary.update({
            "raw_item_count": output.summary.total_raw_items,
            "accepted_item_count": output.summary.total_ai_filter_accepted,
            "rejected_item_count": output.summary.total_ai_filter_rejected,
            "career_signal_count": output.summary.total_career_signals,
        })

        if self.planning_bundle_id is not None:
            summary["planning_bundle_id"] = self.planning_bundle_id

        if self.planning_generation_mode is not None:
            summary["planning_generation_mode"] = self.planning_generation_mode
            summary["planning_bundle_reused"] = (
                self.planning_generation_mode == "database_reuse"
            )

        return summary

    def _update_execution_accounting_summary(self) -> None:
        if self.pipeline_run_id is None:
            raise RuntimeError(
                "SearchPlan accounting validation has no active PipelineRun."
            )

        coverage = (
            self.source_execution_repository
            .assert_run_search_plan_accounting_complete(self.pipeline_run_id)
        )
        source_executions = (
            self.source_execution_repository.list_source_executions(
                self.pipeline_run_id
            )
        )
        discovery_count = sum(
            len(
                self.source_execution_repository.list_discoveries(
                    execution.source_execution_id
                )
            )
            for execution in source_executions
        )
        self._pipeline_run_summary.update(
            {
                "total_search_plan_count": coverage.total_bundle_plans,
                "completed_search_plan_count": coverage.completed,
                "failed_search_plan_count": coverage.failed,
                "skipped_search_plan_count": coverage.skipped,
                "source_execution_count": len(source_executions),
                "discovery_count": discovery_count,
            }
        )

    def _execute_filter_with_provenance(
        self,
        *,
        external_raw_items: list[RawItem],
        external_raw_item_index_offset: int,
        user_profile: UserProfile,
        target_career_paths: list[TargetCareerPath],
    ) -> AIFilterExecutionReport:
        if self.pipeline_run_id is None:
            raise RuntimeError(
                "Filter provenance requires an active PipelineRun."
            )

        self._set_pipeline_run_stage("filter_item_registration")
        try:
            if not self._filter_items_registered:
                registration = (
                    self.filter_decision_repository.register_run_filter_items(
                        self.pipeline_run_id
                    )
                )
                self._filter_items_registered = True
                self._pipeline_run_summary["unique_filter_candidate_count"] = (
                    registration.registered_filter_item_count
                )
                self._defer_historical_duplicate_filter_items()
            registered_statuses = (
                self.filter_decision_repository.list_run_filter_statuses(
                    self.pipeline_run_id
                )
            )
        except Exception as error:
            raise RuntimeError(
                "Run SourceItem filter registration failed before filtering."
            ) from error

        self._set_pipeline_run_stage("filter_candidate_reconciliation")
        try:
            candidates = self._reconcile_filter_candidates(
                external_raw_items=external_raw_items,
                external_raw_item_index_offset=external_raw_item_index_offset,
                registered_source_item_ids={
                    status.source_item_id
                    for status in registered_statuses
                    if status.status == "pending"
                },
            )
        except Exception as error:
            raise RuntimeError(
                "Run SourceItem filter candidate reconciliation failed."
            ) from error

        filtered_raw_items: list[RawItem] = []
        ai_filter_results: list[AIFilterResult] = []
        raw_item_statuses: list[RawItemFilterStatus] = []

        for candidate in candidates:
            report = self._execute_filter_candidate(
                candidate=candidate,
                user_profile=user_profile,
                target_career_paths=target_career_paths,
            )
            filtered_raw_items.extend(report.filtered_raw_items)
            ai_filter_results.extend(report.ai_filter_results)
            raw_item_statuses.extend(report.raw_item_statuses)

        self._set_pipeline_run_stage("ai_filter")
        return AIFilterExecutionReport(
            filtered_raw_items=filtered_raw_items,
            ai_filter_results=ai_filter_results,
            raw_item_statuses=raw_item_statuses,
            executed_count=len(candidates),
        )

    def _defer_historical_duplicate_filter_items(self) -> None:
        if (
            not self._historical_duplicate_source_item_ids
            or self.filter_decision_repository is None
            or self.pipeline_run_id is None
        ):
            return

        from src.database.repositories.filter_decision_repository import (
            HISTORICAL_DUPLICATE_REASON,
        )

        self.filter_decision_repository.mark_items_deferred(
            self.pipeline_run_id,
            sorted(self._historical_duplicate_source_item_ids),
            HISTORICAL_DUPLICATE_REASON,
            metadata={"identity_outcome": "existing"},
        )

    def _reconcile_filter_candidates(
        self,
        *,
        external_raw_items: list[RawItem],
        external_raw_item_index_offset: int,
        registered_source_item_ids: set[int],
    ) -> list[FilterCandidate]:
        """Select the earliest stable RawItem for each discovered SourceItem."""

        candidates: list[FilterCandidate] = []
        seen_source_item_ids: set[int] = set()

        for external_index, raw_item in enumerate(external_raw_items):
            if raw_item.metadata.get("mode") == "dry_run":
                continue

            source_item_id = self._resolve_source_item_id(raw_item)
            if source_item_id in seen_source_item_ids:
                continue
            seen_source_item_ids.add(source_item_id)
            if source_item_id not in registered_source_item_ids:
                continue
            candidates.append(
                FilterCandidate(
                    source_item_id=source_item_id,
                    raw_item=raw_item,
                    raw_item_index=(
                        external_raw_item_index_offset + external_index
                    ),
                )
            )

        if not registered_source_item_ids.issubset(seen_source_item_ids):
            missing_candidates = sorted(
                registered_source_item_ids - seen_source_item_ids
            )
            raise RuntimeError(
                "Discovered SourceItems and canonical RawItem candidates do not "
                "match: "
                f"missing_candidates={missing_candidates}."
            )

        return candidates

    def _execute_filter_candidate(
        self,
        *,
        candidate: FilterCandidate,
        user_profile: UserProfile,
        target_career_paths: list[TargetCareerPath],
    ) -> AIFilterExecutionReport:
        return execute_filter_candidate_with_provenance(
            run_id=self.pipeline_run_id,
            candidate=candidate,
            filter_decision_repository=self.filter_decision_repository,
            ai_filter_executor=self.ai_filter_executor,
            user_profile=user_profile,
            target_career_paths=target_career_paths,
            execution_mode=self.ai_filter_execution_mode,
            provider=self.ai_filter_provider,
            model=self.ai_filter_model,
            stage_setter=self._set_pipeline_run_stage,
        )

    def _persist_filter_execution_failure(
        self,
        *,
        filter_execution_id: int,
        filter_error: BaseException,
    ) -> None:
        persist_filter_execution_failure(
            filter_decision_repository=self.filter_decision_repository,
            filter_execution_id=filter_execution_id,
            filter_error=filter_error,
            stage_setter=self._set_pipeline_run_stage,
        )

    def _update_career_signal_materialization_summary(self) -> None:
        coverage = (
            self.filter_decision_repository
            .get_run_career_signal_materialization(self.pipeline_run_id)
        )
        self._pipeline_run_summary.update(
            {
                "accepted_with_career_signal_count": (
                    coverage.accepted_with_career_signal
                ),
                "accepted_without_career_signal_count": (
                    coverage.accepted_without_career_signal
                ),
            }
        )
        if coverage.missing_source_item_ids:
            raise RuntimeError(
                "Accepted SourceItems have no persisted CareerSignal: "
                f"{list(coverage.missing_source_item_ids)}."
            )

    def _update_filter_accounting_summary(self) -> None:
        coverage = (
            self.filter_decision_repository
            .assert_run_filter_accounting_complete(self.pipeline_run_id)
        )
        self._pipeline_run_summary.update(
            {
                "unique_filter_candidate_count": (
                    coverage.registered_filter_items
                ),
                "accepted_filter_count": coverage.accepted,
                "rejected_filter_count": coverage.rejected,
                "deferred_filter_count": coverage.deferred,
                "failed_filter_count": coverage.failed,
                "filter_execution_count": coverage.filter_execution_count,
                "filter_decision_count": coverage.filter_decision_count,
            }
        )

    def build_planning_input_fingerprint(
        self,
        *,
        user_profile: UserProfile,
        user_preferences: dict[str, Any],
        search_scope: SearchScope,
    ) -> str:
        return build_planning_input_fingerprint(
            profile_content_hash=hash_user_profile(user_profile),
            user_preferences=user_preferences,
            search_scope=search_scope,
            model_provider=self.planning_model_provider,
            model_name=self.planning_model_name,
            prompt_version=self.planning_prompt_version,
            generator_config=self.planning_generator_config,
        )

    def _resolve_planning(
        self,
        *,
        user_profile: UserProfile,
        search_scope: SearchScope,
        user_preferences: dict[str, Any],
    ) -> tuple[list[TargetCareerPath], list[SearchQuery], list[SearchPlan]]:
        if self.planning_bundle_repository is None:
            target_career_paths = self.career_path_generator(user_profile)
            search_queries = self.search_query_generator(target_career_paths)
            search_plans = self.search_plan_builder(
                search_queries,
                search_scope,
            )
            return target_career_paths, search_queries, search_plans

        input_fingerprint = self.build_planning_input_fingerprint(
            user_profile=user_profile,
            user_preferences=user_preferences,
            search_scope=search_scope,
        )
        self.planning_input_fingerprint = input_fingerprint

        if not self.planning_force_refresh:
            reusable_bundle = self._find_reusable_planning_bundle(input_fingerprint)

            if reusable_bundle is not None:
                return self._hydrate_reusable_planning_bundle(reusable_bundle)

        target_career_paths = self.career_path_generator(user_profile)
        search_queries = self.search_query_generator(target_career_paths)
        search_plans = self.search_plan_builder(
            search_queries,
            search_scope,
        )
        generation_mode = _infer_planning_generation_mode(target_career_paths)

        summary = self._persist_planning_bundle(
            user_profile=user_profile,
            user_preferences=user_preferences,
            search_scope=search_scope,
            target_career_paths=target_career_paths,
            search_queries=search_queries,
            search_plans=search_plans,
            generation_mode=generation_mode,
        )
        self.planning_bundle_id = summary.planning_bundle_id
        self.planning_generation_mode = generation_mode
        _report_planning_bundle_persisted(summary, generation_mode)

        return target_career_paths, search_queries, search_plans

    def _load_user_preferences_for_planning(self) -> dict[str, Any]:
        if self.user_preferences_loader is None:
            return {}

        preferences = self.user_preferences_loader()

        if not isinstance(preferences, dict):
            raise RuntimeError(
                "Planning Bundle input loading failed: "
                "UserPreferences must be a dictionary."
            )

        return preferences

    def _find_reusable_planning_bundle(
        self,
        input_fingerprint: str,
    ) -> dict | None:
        try:
            return self.planning_bundle_repository.find_reusable_bundle(
                input_fingerprint
            )
        except Exception as error:
            raise RuntimeError("Planning Bundle lookup failed.") from error

    def _hydrate_reusable_planning_bundle(
        self,
        reusable_bundle: dict,
    ) -> tuple[list[TargetCareerPath], list[SearchQuery], list[SearchPlan]]:
        try:
            planning_bundle_id = int(reusable_bundle["planning_bundle_id"])
            hydrated = self.planning_bundle_repository.hydrate_planning_bundle(
                planning_bundle_id
            )
        except Exception as error:
            raise RuntimeError(
                "Malformed reusable Planning Bundle could not be hydrated."
            ) from error

        self.planning_bundle_id = hydrated.planning_bundle_id
        self.planning_generation_mode = "database_reuse"
        _report_planning_bundle_reused(
            bundle_id=hydrated.planning_bundle_id,
            path_count=len(hydrated.target_career_paths),
            query_count=len(hydrated.search_queries),
            plan_count=len(hydrated.search_plans),
        )

        return (
            hydrated.target_career_paths,
            hydrated.search_queries,
            hydrated.search_plans,
        )

    def _assess_career_signal_priorities(
        self,
        *,
        career_signals: list[CareerSignal],
        filtered_raw_items: list[RawItem],
        ai_filter_results: list[AIFilterResult],
        user_profile: UserProfile,
        user_preferences: dict[str, Any],
        target_career_paths: list[TargetCareerPath],
    ) -> Any:
        if self.priority_assessor is None:
            from src.career_signal_priority import PriorityIntegrationBatchResult

            return PriorityIntegrationBatchResult(scored_career_signals=())
        if self.user_preferences_loader is None:
            raise RuntimeError(
                "Priority Assessment requires current runtime UserPreferences."
            )

        self._set_pipeline_run_stage("priority_assessment")
        as_of = (
            self.priority_as_of_loader()
            if self.priority_as_of_loader is not None
            else utc_now_iso()
        )
        return self.priority_assessor(
            career_signals=tuple(career_signals),
            filtered_raw_items=tuple(filtered_raw_items),
            ai_filter_results=tuple(ai_filter_results),
            user_profile=user_profile,
            user_preferences=user_preferences,
            target_career_paths=tuple(target_career_paths),
            as_of=as_of,
        )

    def _persist_planning_bundle(
        self,
        *,
        user_profile: UserProfile,
        user_preferences: dict[str, Any],
        search_scope: SearchScope,
        target_career_paths: list[TargetCareerPath],
        search_queries: list[SearchQuery],
        search_plans: list[SearchPlan],
        generation_mode: str,
    ) -> "PlanningBundlePersistSummary":
        from src.database.repositories.planning_bundle_repository import (
            PlanningBundleWrite,
        )

        try:
            artifacts = (
                self.planning_artifact_loader()
                if self.planning_artifact_loader is not None
                else ()
            )
            return self.planning_bundle_repository.persist_planning_bundle(
                PlanningBundleWrite(
                    user_profile=user_profile,
                    user_preferences=user_preferences,
                    search_scope=search_scope,
                    target_career_paths=target_career_paths,
                    search_queries=search_queries,
                    search_plans=search_plans,
                    generation_mode=generation_mode,
                    model_provider=self.planning_model_provider,
                    model_name=self.planning_model_name,
                    prompt_version=self.planning_prompt_version,
                    generator_config=self.planning_generator_config,
                    source_path=self.profile_source_path,
                    source_file_hash=self.profile_source_file_hash,
                    schema_version=self.profile_schema_version,
                    artifacts=artifacts,
                )
            )
        except Exception as error:
            raise RuntimeError(
                "Planning Bundle persistence failed before external search execution."
            ) from error

    def _persist_external_raw_items(self, raw_items: list[RawItem]) -> None:
        if self.source_item_repository is None or not raw_items:
            return

        try:
            summary = self.source_item_repository.upsert_many(raw_items)
        except Exception as error:
            raise RuntimeError(
                "RawItem persistence failed before AI filtering."
            ) from error

        _report_source_item_upsert(summary)

    def _persist_external_raw_items_with_outcomes(
        self,
        raw_items: list[RawItem],
    ) -> list[Any]:
        if self.source_item_repository is None or not raw_items:
            return []

        try:
            outcomes = [
                self.source_item_repository.upsert_one_with_outcome(raw_item)
                for raw_item in raw_items
            ]
        except Exception as error:
            raise RuntimeError(
                "RawItem persistence failed before AI filtering."
            ) from error

        _report_source_item_upsert(
            _source_item_upsert_summary_from_outcomes(
                received_count=len(raw_items),
                outcomes=outcomes,
            )
        )
        return outcomes

    def _validate_repository_configuration(self) -> None:
        if (
            self.career_signal_repository is not None
            and self.source_item_repository is None
        ):
            raise RuntimeError(
                "CareerSignalRepository requires SourceItemRepository "
                "so CareerSignals can be linked to source_items."
            )

    def _resolve_eligible_source_item_ids_by_signal_id(
        self,
        raw_items: list[RawItem],
        raw_item_statuses,
        persistable_external_raw_items: list[RawItem],
    ) -> dict[str, int]:
        if (
            self.career_signal_repository is None
            or self.source_item_repository is None
            or not persistable_external_raw_items
        ):
            return {}

        persistable_fingerprints = {
            fingerprint_raw_item(raw_item)
            for raw_item in persistable_external_raw_items
        }
        source_item_ids_by_signal_id: dict[str, int] = {}

        for status in raw_item_statuses:
            if status.status != "processed_accepted":
                continue

            if status.raw_item_index < 0 or status.raw_item_index >= len(raw_items):
                raise RuntimeError(
                    "AI Filter reported an invalid RawItem index for "
                    "CareerSignal persistence."
                )

            raw_item = raw_items[status.raw_item_index]
            fingerprint = fingerprint_raw_item(raw_item)

            if fingerprint not in persistable_fingerprints:
                continue

            try:
                source_item_row = self.source_item_repository.get_by_fingerprint(
                    fingerprint
                )
            except Exception as error:
                raise RuntimeError(
                    "Failed to resolve source_item_id for accepted external RawItem."
                ) from error

            if source_item_row is None:
                raise RuntimeError(
                    "Accepted external RawItem was not found in source_items."
                )

            signal_id = build_signal_id(raw_item)
            source_item_id = int(source_item_row["source_item_id"])
            existing_source_item_id = source_item_ids_by_signal_id.get(signal_id)

            if (
                existing_source_item_id is not None
                and existing_source_item_id != source_item_id
            ):
                raise RuntimeError(
                    "Ambiguous CareerSignal source mapping for "
                    f"{signal_id!r}: multiple source_item_id values."
                )

            source_item_ids_by_signal_id[signal_id] = source_item_id

        return source_item_ids_by_signal_id

    def _persist_career_signals(
        self,
        career_signals: list[CareerSignal],
        eligible_source_item_ids_by_signal_id: dict[str, int],
    ) -> None:
        persist_linked_career_signals(
            career_signal_repository=self.career_signal_repository,
            career_signals=career_signals,
            eligible_source_item_ids_by_signal_id=(
                eligible_source_item_ids_by_signal_id
            ),
        )


def execute_filter_candidate_with_provenance(
    *,
    run_id: str,
    candidate: FilterCandidate,
    filter_decision_repository: Any,
    ai_filter_executor: Callable[
        [list[RawItem], UserProfile, list[TargetCareerPath]],
        AIFilterExecutionReport,
    ],
    user_profile: UserProfile,
    target_career_paths: list[TargetCareerPath],
    execution_mode: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    stage_setter: Callable[[str], None] | None = None,
) -> AIFilterExecutionReport:
    """Run the canonical Phase 8B per-item filter provenance flow."""

    from src.database.repositories.filter_decision_repository import (
        FilterDecisionInput,
        FilterExecutionStart,
    )

    set_stage = stage_setter or (lambda stage: None)
    set_stage("filter_execution_start")
    try:
        started = filter_decision_repository.start_filter_execution(
            run_id,
            [candidate.source_item_id],
            FilterExecutionStart(
                execution_mode=execution_mode,
                provider=provider,
                model=model,
            ),
        )
    except Exception as error:
        raise RuntimeError(
            "FilterExecution start failed before the AI Filter call."
        ) from error

    filter_execution_id = started.execution.filter_execution_id
    set_stage("ai_filter")
    try:
        report = ai_filter_executor(
            [candidate.raw_item],
            user_profile,
            target_career_paths,
        )
    except Exception as filter_error:
        persist_filter_execution_failure(
            filter_decision_repository=filter_decision_repository,
            filter_execution_id=filter_execution_id,
            filter_error=filter_error,
            stage_setter=set_stage,
        )
        return _build_failed_filter_report(candidate, filter_error)

    try:
        report = _normalize_single_filter_report(report, candidate)
    except Exception as reconciliation_error:
        persist_filter_execution_failure(
            filter_decision_repository=filter_decision_repository,
            filter_execution_id=filter_execution_id,
            filter_error=reconciliation_error,
            stage_setter=set_stage,
        )
        set_stage("filter_candidate_reconciliation")
        raise RuntimeError(
            "AI Filter returned a malformed per-item execution report."
        ) from reconciliation_error

    status = report.raw_item_statuses[0]
    result = report.ai_filter_results[0]
    if status.status in {"failed", "failed_before_filter"}:
        filter_error = RuntimeError(
            _filter_failure_message(status=status, result=result)
        )
        persist_filter_execution_failure(
            filter_decision_repository=filter_decision_repository,
            filter_execution_id=filter_execution_id,
            filter_error=filter_error,
            stage_setter=set_stage,
        )
        set_stage("ai_filter")
        return report

    decision = "accepted" if result.is_relevant else "rejected"
    decision_metadata: dict[str, Any] = {}
    if result.action is not None and str(result.action).strip():
        decision_metadata["suggested_action"] = str(result.action).strip()
    if result.suggested_category is not None:
        decision_metadata["suggested_category"] = _enum_or_text(
            result.suggested_category
        )

    set_stage("filter_execution_completion")
    try:
        filter_decision_repository.complete_filter_execution(
            filter_execution_id,
            [
                FilterDecisionInput(
                    source_item_id=candidate.source_item_id,
                    decision=decision,
                    reason=result.reason,
                    confidence=result.confidence,
                    matched_career_path_ids=result.matched_career_path_ids,
                    metadata=decision_metadata or None,
                )
            ],
        )
    except Exception as error:
        raise RuntimeError(
            "FilterExecution completion and decision persistence failed."
        ) from error

    set_stage("ai_filter")
    return report


def persist_filter_execution_failure(
    *,
    filter_decision_repository: Any,
    filter_execution_id: int,
    filter_error: BaseException,
    stage_setter: Callable[[str], None] | None = None,
) -> None:
    """Persist the canonical failed FilterExecution transition."""

    try:
        filter_decision_repository.fail_filter_execution(
            filter_execution_id,
            type(filter_error).__name__,
            _bounded_pipeline_run_error_message(filter_error),
        )
    except Exception as provenance_error:
        if stage_setter is not None:
            stage_setter("filter_execution_failure_persistence")
        wrapped_error = RuntimeError(
            "AI Filter failure could not be persisted to its "
            "FilterExecution provenance record."
        )
        if hasattr(wrapped_error, "add_note"):
            wrapped_error.add_note(
                "Filter provenance persistence error: "
                f"{type(provenance_error).__name__}: "
                f"{_bounded_pipeline_run_error_message(provenance_error)}"
            )
        raise wrapped_error from filter_error


def persist_linked_career_signals(
    *,
    career_signal_repository: Any,
    career_signals: list[CareerSignal],
    eligible_source_item_ids_by_signal_id: dict[str, int],
) -> None:
    """Persist normalized CareerSignals with canonical SourceItem links."""

    if career_signal_repository is None:
        return
    if not eligible_source_item_ids_by_signal_id:
        return

    from src.database.repositories.career_signal_repository import (
        CareerSignalWrite,
    )

    records: list[CareerSignalWrite] = []
    seen_eligible_signal_ids: set[str] = set()
    for career_signal in career_signals:
        source_item_id = eligible_source_item_ids_by_signal_id.get(
            career_signal.signal_id
        )
        if source_item_id is None:
            continue
        seen_eligible_signal_ids.add(career_signal.signal_id)
        records.append(
            CareerSignalWrite(
                career_signal=career_signal,
                source_item_id=source_item_id,
            )
        )

    missing_signal_ids = (
        set(eligible_source_item_ids_by_signal_id)
        - seen_eligible_signal_ids
    )
    if missing_signal_ids:
        raise RuntimeError(
            "Accepted external RawItems could not be matched to "
            "normalized CareerSignals: "
            f"{sorted(missing_signal_ids)}."
        )
    if not records:
        return

    try:
        summary = career_signal_repository.upsert_many(records)
    except Exception as error:
        raise RuntimeError(
            "CareerSignal persistence failed after normalization."
        ) from error
    _report_career_signal_upsert(summary)


def execute_pipeline_runtime(
    pipeline: MockPipeline,
    *,
    output_persister: Callable[[PipelineRunOutput], Any],
    success_reporter: Callable[[PipelineRunOutput, Any], None] | None = None,
) -> tuple[PipelineRunOutput, Any]:
    """
    Execute the Pipeline and close its lifecycle after required output work.
    """

    output = pipeline.run()

    try:
        _set_pipeline_runtime_stage(pipeline, "output_persistence")
        persisted_output = output_persister(output)

        if success_reporter is not None:
            _set_pipeline_runtime_stage(pipeline, "final_reporting")
            success_reporter(output, persisted_output)

        complete_pipeline_run = getattr(pipeline, "complete_pipeline_run", None)
        if callable(complete_pipeline_run):
            complete_pipeline_run(output)
    except Exception as error:
        fail_pipeline_run = getattr(pipeline, "_fail_pipeline_run", None)
        if callable(fail_pipeline_run):
            fail_pipeline_run(error)
        raise

    return output, persisted_output


def _set_pipeline_runtime_stage(pipeline: MockPipeline, stage: str) -> None:
    set_stage = getattr(pipeline, "_set_pipeline_run_stage", None)
    if callable(set_stage):
        set_stage(stage)


def _normalize_single_filter_report(
    report: AIFilterExecutionReport,
    candidate: FilterCandidate,
) -> AIFilterExecutionReport:
    if not isinstance(report, AIFilterExecutionReport):
        raise TypeError("AI Filter must return AIFilterExecutionReport.")
    if (
        report.executed_count != 1
        or len(report.ai_filter_results) != 1
        or len(report.raw_item_statuses) != 1
    ):
        raise RuntimeError(
            "A per-item AI Filter call must report exactly one execution, "
            "result, and status."
        )

    result = report.ai_filter_results[0]
    status = report.raw_item_statuses[0]
    allowed_statuses = {
        "processed_accepted",
        "processed_rejected",
        "failed",
        "failed_before_filter",
    }
    if status.status not in allowed_statuses:
        raise RuntimeError(
            f"Unsupported per-item AI Filter status {status.status!r}."
        )
    if (
        status.source_type != candidate.raw_item.source_type
        or status.title != candidate.raw_item.title
        or status.url != candidate.raw_item.url
        or result.title != candidate.raw_item.title
        or result.url != candidate.raw_item.url
    ):
        raise RuntimeError(
            "AI Filter result does not belong to the canonical RawItem."
        )

    accepted = status.status == "processed_accepted"
    rejected = status.status == "processed_rejected"
    failed = status.status in {"failed", "failed_before_filter"}
    if accepted != bool(result.is_relevant):
        raise RuntimeError(
            "AI Filter accepted status and relevance decision disagree."
        )
    if rejected and bool(result.is_relevant):
        raise RuntimeError(
            "AI Filter rejected status contains an accepted result."
        )
    if failed and bool(result.is_relevant):
        raise RuntimeError(
            "A failed AI Filter item cannot be relevant."
        )

    if accepted:
        if (
            len(report.filtered_raw_items) != 1
            or fingerprint_raw_item(report.filtered_raw_items[0])
            != fingerprint_raw_item(candidate.raw_item)
        ):
            raise RuntimeError(
                "Accepted AI Filter report must retain its canonical RawItem."
            )
        filtered_raw_items = [candidate.raw_item]
    else:
        if report.filtered_raw_items:
            raise RuntimeError(
                "Rejected or failed AI Filter report retained a RawItem."
            )
        filtered_raw_items = []

    return AIFilterExecutionReport(
        filtered_raw_items=filtered_raw_items,
        ai_filter_results=[result],
        raw_item_statuses=[
            replace(status, raw_item_index=candidate.raw_item_index)
        ],
        executed_count=1,
    )


def _build_failed_filter_report(
    candidate: FilterCandidate,
    error: BaseException,
) -> AIFilterExecutionReport:
    raw_item = candidate.raw_item
    reason = f"AI Filter failed: {error}"
    result = AIFilterResult(
        raw_item_fingerprint=_ai_filter_raw_item_fingerprint(raw_item),
        title=raw_item.title,
        url=raw_item.url,
        is_relevant=False,
        confidence=0.0,
        reason=reason,
        suggested_category=SignalCategory.UNKNOWN,
        matched_career_path_ids=[],
        action="review",
        metadata={"filter_error": str(error)},
    )
    status = RawItemFilterStatus(
        raw_item_fingerprint=result.raw_item_fingerprint,
        raw_item_index=candidate.raw_item_index,
        source_type=raw_item.source_type,
        title=raw_item.title,
        url=raw_item.url,
        status="failed",
        reason=reason,
        is_relevant=False,
        metadata={
            "ai_filter_action": result.action,
            "ai_filter_confidence": result.confidence,
        },
    )
    return AIFilterExecutionReport(
        ai_filter_results=[result],
        raw_item_statuses=[status],
        executed_count=1,
    )


def _filter_failure_message(
    *,
    status: RawItemFilterStatus,
    result: AIFilterResult,
) -> str:
    metadata_error = result.metadata.get("filter_error")
    if metadata_error is not None and str(metadata_error).strip():
        return str(metadata_error).strip()
    if result.reason is not None and str(result.reason).strip():
        return str(result.reason).strip()
    if status.reason is not None and str(status.reason).strip():
        return str(status.reason).strip()
    return "AI Filter invocation failed without an error message."


def _ai_filter_raw_item_fingerprint(raw_item: RawItem) -> str:
    fingerprint_source = (
        f"{raw_item.source_type.value}|"
        f"{raw_item.title}|"
        f"{raw_item.url}|"
        f"{raw_item.raw_text[:200]}"
    )
    return hashlib.sha256(
        fingerprint_source.encode("utf-8")
    ).hexdigest()[:16]


def _enum_or_text(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _count_duplicate_urls(raw_items: list[RawItem]) -> int:
    """
    Count duplicate URL occurrences after the first occurrence.
    """

    seen_urls: set[str] = set()
    duplicate_count = 0

    for raw_item in raw_items:
        normalized_url = raw_item.url.strip().lower()

        if not normalized_url:
            continue

        if normalized_url in seen_urls:
            duplicate_count += 1
            continue

        seen_urls.add(normalized_url)

    return duplicate_count


def _report_source_item_upsert(summary: "SourceItemUpsertSummary") -> None:
    print(
        "SourceItem persistence: "
        f"received={summary.received_count}, "
        f"unique={summary.unique_count}, "
        f"inserted={summary.inserted_count}, "
        f"updated={summary.updated_count}"
    )


def _source_item_upsert_summary_from_outcomes(
    *,
    received_count: int,
    outcomes: list[Any],
) -> "SourceItemUpsertSummary":
    from src.database.repositories.source_item_repository import (
        SourceItemUpsertSummary,
    )

    unique_outcomes: dict[int, Any] = {}
    for outcome in outcomes:
        unique_outcomes.setdefault(outcome.source_item_id, outcome)

    return SourceItemUpsertSummary(
        received_count=received_count,
        unique_count=len(unique_outcomes),
        inserted_count=len(
            {
                outcome.source_item_id
                for outcome in unique_outcomes.values()
                if outcome.created_new
            }
        ),
        updated_count=len(
            {
                outcome.source_item_id
                for outcome in unique_outcomes.values()
                if not outcome.created_new
            }
        ),
    )


def _report_career_signal_upsert(summary: "CareerSignalUpsertSummary") -> None:
    print(
        "CareerSignal persistence: "
        f"received={summary.received_count}, "
        f"unique={summary.unique_count}, "
        f"inserted={summary.inserted_count}, "
        f"updated={summary.updated_count}"
    )


def _infer_planning_generation_mode(
    target_career_paths: list[TargetCareerPath],
) -> str:
    if any(
        career_path.metadata.get("used_cache") is True
        for career_path in target_career_paths
    ):
        return "file_cache"

    return "generated"


def _raw_item_result_position(
    raw_item: RawItem,
    fallback_position: int,
) -> int:
    position = raw_item.metadata.get("position")
    if isinstance(position, int) and not isinstance(position, bool) and position >= 0:
        return position
    return fallback_position


def _disabled_search_api_report(
    search_plans: list[SearchPlan],
) -> SearchAPIExecutionReport:
    return SearchAPIExecutionReport(
        plan_statuses=[
            SearchPlanExecutionStatus(
                plan_id=plan.plan_id,
                query_id=plan.query_id,
                career_path_id=plan.career_path_id,
                career_path_title=plan.career_path_title,
                status="not_executable_for_search_api",
                reason="Search API is disabled for this SearchScope.",
                priority=plan.priority,
            )
            for plan in search_plans
        ]
    )


def _report_planning_bundle_reused(
    *,
    bundle_id: int,
    path_count: int,
    query_count: int,
    plan_count: int,
) -> None:
    print(
        "Planning Bundle reused: "
        f"bundle_id={bundle_id}, "
        f"paths={path_count}, "
        f"queries={query_count}, "
        f"plans={plan_count}"
    )


def _report_planning_bundle_persisted(
    summary: "PlanningBundlePersistSummary",
    generation_mode: str,
) -> None:
    print(
        "Planning Bundle persisted: "
        f"bundle_id={summary.planning_bundle_id}, "
        f"paths={summary.path_count}, "
        f"queries={summary.query_count}, "
        f"plans={summary.plan_count}, "
        f"generation_mode={generation_mode}"
    )


def _normalize_execution_mode(execution_mode: str) -> str:
    normalized = str(execution_mode).strip().lower()
    return normalized or LIVE_EXECUTION_MODE


def _bounded_pipeline_run_error_message(error: BaseException) -> str:
    message = str(error).strip() or type(error).__name__
    return message[:_PIPELINE_RUN_ERROR_MESSAGE_LIMIT]


def _add_pipeline_run_failure_note(
    original_error: BaseException,
    lifecycle_error: BaseException,
) -> None:
    note = (
        "PipelineRun failure persistence also failed: "
        f"{type(lifecycle_error).__name__}: "
        f"{_bounded_pipeline_run_error_message(lifecycle_error)}"
    )
    add_note = getattr(original_error, "add_note", None)

    if callable(add_note):
        add_note(note)


def _report_pipeline_run_completed(
    record: "PipelineRunRecord",
    output: PipelineRunOutput,
) -> None:
    print(
        "Pipeline Run completed: "
        f"run_id={record.run_id}, "
        f"bundle_id={record.planning_bundle_id}, "
        f"raw_items={output.summary.total_raw_items}, "
        f"career_signals={output.summary.total_career_signals}"
    )
