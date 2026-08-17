from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, TypeVar

from src.career_signal_routing import route_scored_career_signals
from src.career_intelligence_brief import build_career_intelligence_brief
from src.career_intelligence_interpretation_runtime import (
    InterpretationExecutor,
    interpret_routed_intelligence,
)
from src.database.repositories.career_signal_repository import (
    CareerSignalRepository,
)
from src.database.repositories.filter_decision_repository import (
    DEFERRED,
    HISTORICAL_DUPLICATE_REASON,
    PENDING,
    FilterDecisionRepository,
)
from src.database.repositories.pipeline_run_repository import (
    PipelineRunCompletion,
    PipelineRunFailure,
    PipelineRunRepository,
    PipelineRunStart,
)
from src.database.repositories.source_execution_repository import (
    RUNNING,
    SourceExecutionCompletion,
    SourceExecutionFailure,
    SourceExecutionRepository,
    SourceExecutionStart,
    SourceItemDiscoveryWrite,
)
from src.database.repositories.source_item_repository import (
    SourceItemPersistenceResult,
    SourceItemRepository,
)
from src.database.source_identity import fingerprint_raw_item
from src.models import (
    AIFilterExecutionReport,
    AIFilterResult,
    CareerSignal,
    RSSFeed,
    RawItem,
    SelectedWebsite,
    TargetCareerPath,
    UserProfile,
    utc_now_iso,
)
from src.pipeline import (
    PIPELINE_VERSION,
    FilterCandidate,
    execute_filter_candidate_with_provenance,
    persist_linked_career_signals,
)
from src.rss_client import RSSClient
from src.selected_website_client import SelectedWebsiteClient
from src.signal_identity import build_signal_id
from src.source_monitoring.acquisition_models import (
    AcquisitionMethod,
    Phase7MonitoringHandoff,
)
from src.source_monitoring.acquisition_resolver import (
    ACQUISITION_RESOLUTIONS_FILE,
)


HandoffResult = TypeVar("HandoffResult")
MonitoringAdapter = Callable[[Phase7MonitoringHandoff], HandoffResult]
MONITORING_PIPELINE_PHASE = "phase_9b_monitoring_runtime"
LIVE_VALIDATION_PENDING_STATUS = "filter_pending"
MAX_RUNTIME_ERROR_MESSAGE_LENGTH = 1000


class MonitoringRuntimeCompatibilityError(Exception):
    """Raised when a resolved Monitoring handoff cannot enter the runtime."""


class MonitoringRuntimeError(RuntimeError):
    """Raised when the Monitoring runtime cannot complete canonical work."""


def load_phase7_monitoring_handoffs(
    path: str | Path = ACQUISITION_RESOLUTIONS_FILE,
) -> tuple[Phase7MonitoringHandoff, ...]:
    """Load typed, already-resolved handoffs from the canonical Phase 6 output."""

    source_path = Path(path)
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        raw_handoffs = payload["phase7_monitoring_handoffs"]
        if not isinstance(raw_handoffs, list):
            raise TypeError("phase7_monitoring_handoffs must be a list.")

        final_evaluation_ids = _final_evaluation_ids_by_candidate(payload)
        handoffs = []
        for raw_handoff in raw_handoffs:
            handoff_payload = dict(raw_handoff)
            candidate_source_id = str(
                handoff_payload["candidate_source_id"]
            )
            provenance = dict(handoff_payload.get("provenance") or {})
            final_evaluation_id = final_evaluation_ids.get(
                candidate_source_id
            )
            if final_evaluation_id:
                provenance.setdefault(
                    "final_source_evaluation_id",
                    final_evaluation_id,
                )
            handoff_payload["provenance"] = provenance
            handoffs.append(
                Phase7MonitoringHandoff.from_dict(handoff_payload)
            )

        _validate_unique_handoffs(handoffs)
        return tuple(handoffs)
    except Exception as error:
        raise MonitoringRuntimeCompatibilityError(
            f"Failed to load Phase7MonitoringHandoffs from {source_path}."
        ) from error


class MonitoringAcquisitionDispatcher:
    """Dispatch an already-resolved handoff without repeating source planning."""

    def __init__(
        self,
        *,
        feed_adapter: MonitoringAdapter[Any],
        selected_website_adapter: MonitoringAdapter[Any],
    ) -> None:
        self.feed_adapter = feed_adapter
        self.selected_website_adapter = selected_website_adapter

    def dispatch(self, handoff: Phase7MonitoringHandoff) -> Any:
        if handoff.acquisition_method in {
            AcquisitionMethod.RSS,
            AcquisitionMethod.ATOM,
        }:
            return self.feed_adapter(handoff)
        if handoff.acquisition_method == AcquisitionMethod.SELECTED_WEBSITE:
            return self.selected_website_adapter(handoff)
        raise MonitoringRuntimeCompatibilityError(
            "Resolved Monitoring handoff has an unsupported acquisition method: "
            f"{handoff.acquisition_method!r}."
        )


def build_monitoring_source_execution_start(
    handoff: Phase7MonitoringHandoff,
    *,
    execution_mode: str | None = None,
    requested_result_limit: int | None = None,
) -> SourceExecutionStart:
    """Map stable handoff references into the existing SourceExecution shape."""

    provenance = handoff.provenance if isinstance(handoff.provenance, dict) else {}
    locator = handoff.source_url
    if handoff.acquisition_method in {
        AcquisitionMethod.RSS,
        AcquisitionMethod.ATOM,
    }:
        locator = str(
            provenance.get("verified_feed_url")
            or provenance.get("feed_candidate_url")
            or handoff.source_url
        )

    metadata: dict[str, Any] = {
        "source_monitoring_handoff_id": handoff.phase7_monitoring_handoff_id,
        "source_monitoring_schema_version": handoff.schema_version,
        "candidate_source_id": handoff.candidate_source_id,
        "entity_id": handoff.entity_id,
        "acquisition_resolution_id": handoff.acquisition_resolution_id,
        "acquisition_method": handoff.acquisition_method.value,
        "acquisition_config_ref": handoff.acquisition_config_ref,
        "source_role": handoff.source_role.value,
        "supported_information_need_ids": list(
            handoff.supported_information_need_ids
        ),
    }
    for key in (
        "final_source_evaluation_id",
        "feed_verification_result_id",
        "selected_feed_verification_result_id",
        "verified_feed_url",
        "feed_candidate_url",
        "feed_format",
        "verified_feed_format",
        "selected_website_resolution_result_id",
        "selected_website_acquisition_config_id",
    ):
        value = provenance.get(key)
        if value is not None and str(value).strip():
            metadata[key] = value

    return SourceExecutionStart(
        source_type=handoff.acquisition_method.value,
        provider=handoff.acquisition_method.value,
        source_key=handoff.candidate_source_id,
        source_name=handoff.entity_id,
        source_locator=locator,
        execution_mode=execution_mode,
        requested_result_limit=requested_result_limit,
        metadata=metadata,
    )


class FeedMonitoringAdapter:
    """Execute a resolved feed handoff through the existing RSS client."""

    def __init__(self, client: RSSClient) -> None:
        self.client = client

    def __call__(
        self,
        handoff: Phase7MonitoringHandoff,
    ) -> list[RawItem]:
        if handoff.acquisition_method not in {
            AcquisitionMethod.RSS,
            AcquisitionMethod.ATOM,
        }:
            raise MonitoringRuntimeCompatibilityError(
                "Feed adapter received a non-feed acquisition method."
            )
        feed_url = str(
            handoff.provenance.get("verified_feed_url")
            or handoff.provenance.get("feed_candidate_url")
            or handoff.source_url
        )
        raw_items = self.client.fetch_feed(
            RSSFeed(
                name=handoff.entity_id,
                url=feed_url,
                notes=f"Monitoring handoff {handoff.phase7_monitoring_handoff_id}",
            ),
            [],
        )
        return [
            _with_handoff_provenance(raw_item, handoff)
            for raw_item in raw_items
        ]


class SelectedWebsiteMonitoringAdapter:
    """Execute a resolved selected-site handoff through the existing client."""

    def __init__(self, client: SelectedWebsiteClient) -> None:
        self.client = client

    def __call__(
        self,
        handoff: Phase7MonitoringHandoff,
    ) -> list[RawItem]:
        if handoff.acquisition_method != AcquisitionMethod.SELECTED_WEBSITE:
            raise MonitoringRuntimeCompatibilityError(
                "Selected website adapter received a non-website method."
            )
        raw_items = self.client.fetch_website(
            SelectedWebsite(
                name=handoff.entity_id,
                url=handoff.source_url,
                notes=f"Monitoring handoff {handoff.phase7_monitoring_handoff_id}",
            ),
            [],
        )
        return [
            _with_handoff_provenance(raw_item, handoff)
            for raw_item in raw_items
        ]


@dataclass(frozen=True)
class MonitoringCandidateOutcome:
    source_item_id: int
    fingerprint: str
    result_position: int
    created_new: bool
    historical_duplicate: bool
    filter_eligible: bool
    raw_item: RawItem


@dataclass(frozen=True)
class MonitoringCandidateBatchResult:
    run_id: str
    source_execution_id: int
    outcomes: tuple[MonitoringCandidateOutcome, ...]

    @property
    def created_count(self) -> int:
        return sum(item.created_new for item in self.outcomes)

    @property
    def existing_count(self) -> int:
        return len(self.outcomes) - self.created_count


class MonitoringCandidateRegistrar:
    """Persist canonical discoveries and classify historical rediscoveries."""

    def __init__(
        self,
        *,
        source_item_repository: SourceItemRepository,
        source_execution_repository: SourceExecutionRepository,
        filter_decision_repository: FilterDecisionRepository,
    ) -> None:
        self.source_item_repository = source_item_repository
        self.source_execution_repository = source_execution_repository
        self.filter_decision_repository = filter_decision_repository

    def persist_candidates(
        self,
        *,
        run_id: str,
        source_execution_id: int,
        raw_items: Iterable[RawItem],
    ) -> MonitoringCandidateBatchResult:
        try:
            execution = self.source_execution_repository.get_source_execution(
                source_execution_id
            )
            if execution is None:
                raise MonitoringRuntimeCompatibilityError(
                    f"SourceExecution {source_execution_id} was not found."
                )
            if execution.run_id != run_id:
                raise MonitoringRuntimeCompatibilityError(
                    "SourceExecution does not belong to the requested PipelineRun."
                )
            if execution.status != RUNNING:
                raise MonitoringRuntimeCompatibilityError(
                    "Monitoring candidates require a running SourceExecution."
                )

            existing_run_status_ids = {
                record.source_item_id
                for record in self.filter_decision_repository.list_run_filter_statuses(
                    run_id
                )
            }
            positioned_items = _unique_positioned_items(raw_items)
            persisted = [
                (
                    position,
                    raw_item,
                    self.source_item_repository.upsert_one_with_outcome(raw_item),
                )
                for position, raw_item in positioned_items
            ]

            self.source_execution_repository.record_discoveries(
                source_execution_id,
                [
                    SourceItemDiscoveryWrite(
                        source_item_id=result.source_item_id,
                        result_position=position,
                        metadata={
                            "identity_outcome": (
                                "created_new" if result.created_new else "existing"
                            ),
                            "source_item_fingerprint": result.fingerprint,
                        },
                    )
                    for position, _, result in persisted
                ],
            )
            self.filter_decision_repository.register_run_filter_items(run_id)

            historical_ids = [
                result.source_item_id
                for _, _, result in persisted
                if not result.created_new
                and result.source_item_id not in existing_run_status_ids
            ]
            if historical_ids:
                self.filter_decision_repository.mark_items_deferred(
                    run_id,
                    historical_ids,
                    HISTORICAL_DUPLICATE_REASON,
                    metadata={"monitoring_identity_outcome": "existing"},
                )

            status_by_source_item_id = {
                record.source_item_id: record
                for record in self.filter_decision_repository.list_run_filter_statuses(
                    run_id
                )
            }
            outcomes = tuple(
                _candidate_outcome(
                    position=position,
                    raw_item=raw_item,
                    persistence=result,
                    status=status_by_source_item_id[result.source_item_id],
                )
                for position, raw_item, result in persisted
            )
            return MonitoringCandidateBatchResult(
                run_id=run_id,
                source_execution_id=source_execution_id,
                outcomes=outcomes,
            )
        except MonitoringRuntimeCompatibilityError:
            raise
        except Exception as error:
            raise MonitoringRuntimeCompatibilityError(
                "Failed to persist Monitoring candidate identities."
            ) from error


@dataclass(frozen=True)
class MonitoringSourceResult:
    handoff_id: str
    candidate_source_id: str
    source_execution_id: int
    status: str
    returned_candidate_count: int
    canonical_candidate_count: int
    new_source_item_count: int
    existing_source_item_count: int
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class MonitoringRuntimeResult:
    run_id: str
    status: str
    source_results: tuple[MonitoringSourceResult, ...]
    observed_raw_items: tuple[RawItem, ...]
    ai_filter_results: tuple[AIFilterResult, ...]
    filtered_raw_items: tuple[RawItem, ...]
    career_signals: tuple[CareerSignal, ...]
    summary: dict[str, Any]
    scored_career_signals: tuple[Any, ...] = ()
    priority_assessment_diagnostics: tuple[Any, ...] = ()
    career_signal_routing: Any | None = None
    career_intelligence_interpretation: Any | None = None
    career_intelligence_brief: Any | None = None


class MonitoringRuntime:
    """Run resolved Monitoring handoffs through canonical runtime storage."""

    def __init__(
        self,
        *,
        dispatcher: MonitoringAcquisitionDispatcher,
        pipeline_run_repository: PipelineRunRepository,
        source_execution_repository: SourceExecutionRepository,
        source_item_repository: SourceItemRepository,
        filter_decision_repository: FilterDecisionRepository,
        career_signal_repository: CareerSignalRepository,
        ai_filter_executor: Callable[
            [list[RawItem], UserProfile, list[TargetCareerPath]],
            AIFilterExecutionReport,
        ]
        | None,
        normalizer: Callable[
            [list[RawItem], list[AIFilterResult]],
            list[CareerSignal],
        ],
        execution_mode: str = "live",
        max_candidates_per_source: int | None = None,
        ai_filter_execution_mode: str | None = None,
        ai_filter_provider: str | None = None,
        ai_filter_model: str | None = None,
        priority_assessor: Callable[..., Any] | None = None,
        priority_as_of_loader: Callable[[], str] | None = None,
        interpretation_executor: InterpretationExecutor | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.pipeline_run_repository = pipeline_run_repository
        self.source_execution_repository = source_execution_repository
        self.source_item_repository = source_item_repository
        self.filter_decision_repository = filter_decision_repository
        self.career_signal_repository = career_signal_repository
        self.ai_filter_executor = ai_filter_executor
        self.normalizer = normalizer
        self.execution_mode = str(execution_mode).strip() or "live"
        self.max_candidates_per_source = _optional_nonnegative_integer(
            max_candidates_per_source,
            "max_candidates_per_source",
        )
        self.ai_filter_execution_mode = ai_filter_execution_mode
        self.ai_filter_provider = ai_filter_provider
        self.ai_filter_model = ai_filter_model
        self.priority_assessor = priority_assessor
        self.priority_as_of_loader = priority_as_of_loader
        self.interpretation_executor = interpretation_executor
        self.registrar = MonitoringCandidateRegistrar(
            source_item_repository=source_item_repository,
            source_execution_repository=source_execution_repository,
            filter_decision_repository=filter_decision_repository,
        )

    def run(
        self,
        *,
        handoffs: Iterable[Phase7MonitoringHandoff],
        user_profile: UserProfile,
        target_career_paths: list[TargetCareerPath],
        user_preferences: dict[str, Any] | None = None,
        acquisition_only: bool = False,
    ) -> MonitoringRuntimeResult:
        handoff_list = list(handoffs)
        _validate_unique_handoffs(handoff_list)
        if not acquisition_only and self.ai_filter_executor is None:
            raise MonitoringRuntimeError(
                "Completed Monitoring runtime requires an AI Filter executor."
            )
        if not acquisition_only and self.priority_assessor is not None:
            if user_preferences is None:
                raise MonitoringRuntimeError(
                    "Priority Assessment requires current runtime UserPreferences."
                )
            if not isinstance(user_preferences, dict):
                raise MonitoringRuntimeError(
                    "Monitoring UserPreferences must be a dictionary."
                )
        current_user_preferences = (
            user_preferences if user_preferences is not None else {}
        )

        run = self.pipeline_run_repository.start_run(
            PipelineRunStart(
                pipeline_version=PIPELINE_VERSION,
                phase=MONITORING_PIPELINE_PHASE,
                execution_mode=self.execution_mode,
                metadata={
                    "monitoring_runtime": "phase9b",
                    "monitoring_handoff_count": len(handoff_list),
                    "acquisition_only": acquisition_only,
                },
            )
        )
        stage = "source_execution"
        source_results: list[MonitoringSourceResult] = []
        all_outcomes: list[MonitoringCandidateOutcome] = []

        try:
            for handoff in handoff_list:
                source_result, outcomes = self._execute_handoff(
                    run_id=run.run_id,
                    handoff=handoff,
                )
                source_results.append(source_result)
                all_outcomes.extend(outcomes)

            stage = "filter_item_registration"
            self.filter_decision_repository.register_run_filter_items(
                run.run_id
            )
            observed_raw_items = tuple(
                outcome.raw_item for outcome in all_outcomes
            )
            new_candidates = _new_filter_candidates(all_outcomes)
            initial_summary = self._build_summary(
                source_results=source_results,
                outcomes=all_outcomes,
                new_candidate_count=len(new_candidates),
            )

            if acquisition_only:
                return MonitoringRuntimeResult(
                    run_id=run.run_id,
                    status=LIVE_VALIDATION_PENDING_STATUS,
                    source_results=tuple(source_results),
                    observed_raw_items=observed_raw_items,
                    ai_filter_results=(),
                    filtered_raw_items=(),
                    career_signals=(),
                    scored_career_signals=(),
                    priority_assessment_diagnostics=(),
                    career_signal_routing=route_scored_career_signals(()),
                    career_intelligence_interpretation=None,
                    career_intelligence_brief=None,
                    summary=initial_summary,
                )

            stage = "ai_filter"
            (
                ai_filter_results,
                filtered_raw_items,
                accepted_source_item_ids_by_signal_id,
            ) = self._filter_new_candidates(
                run_id=run.run_id,
                candidates=new_candidates,
                user_profile=user_profile,
                target_career_paths=target_career_paths,
            )

            stage = "normalization"
            career_signals = self.normalizer(
                filtered_raw_items,
                ai_filter_results,
            )
            stage = "career_signal_persistence"
            persist_linked_career_signals(
                career_signal_repository=self.career_signal_repository,
                career_signals=career_signals,
                eligible_source_item_ids_by_signal_id=(
                    accepted_source_item_ids_by_signal_id
                ),
            )
            priority_result = self._assess_career_signal_priorities(
                career_signals=career_signals,
                filtered_raw_items=filtered_raw_items,
                ai_filter_results=ai_filter_results,
                user_profile=user_profile,
                user_preferences=current_user_preferences,
                target_career_paths=target_career_paths,
            )
            career_signal_routing = route_scored_career_signals(
                priority_result.scored_career_signals
            )
            stage = "career_intelligence_interpretation"
            career_intelligence_interpretation = interpret_routed_intelligence(
                routing_result=career_signal_routing,
                target_career_paths=target_career_paths,
                user_preferences=current_user_preferences,
                interpretation_executor=self.interpretation_executor,
            )
            stage = "career_intelligence_brief"
            career_intelligence_brief = build_career_intelligence_brief(
                routing_result=career_signal_routing,
                interpretation=career_intelligence_interpretation,
                target_career_paths=target_career_paths,
                generated_at=utc_now_iso(),
            )

            stage = "run_accounting"
            coverage = (
                self.filter_decision_repository
                .assert_run_filter_accounting_complete(run.run_id)
            )
            materialization = (
                self.filter_decision_repository
                .get_run_career_signal_materialization(run.run_id)
            )
            if materialization.missing_source_item_ids:
                raise MonitoringRuntimeError(
                    "Accepted Monitoring SourceItems have no CareerSignal: "
                    f"{list(materialization.missing_source_item_ids)}."
                )
            _assert_source_execution_accounting(
                source_execution_repository=self.source_execution_repository,
                run_id=run.run_id,
                expected_handoff_count=len(handoff_list),
            )

            summary = dict(initial_summary)
            summary.update(
                {
                    "monitoring_filter_execution_count": (
                        coverage.filter_execution_count
                    ),
                    "monitoring_filter_decision_count": (
                        coverage.filter_decision_count
                    ),
                    "monitoring_accepted_count": coverage.accepted,
                    "monitoring_rejected_count": coverage.rejected,
                    "monitoring_deferred_count": coverage.deferred,
                    "monitoring_failed_filter_count": coverage.failed,
                    "monitoring_career_signal_count": len(career_signals),
                }
            )
            stage = "pipeline_run_completion"
            completed_run = self.pipeline_run_repository.complete_run(
                run.run_id,
                PipelineRunCompletion(
                    summary=summary,
                    metadata={"monitoring_runtime": "phase9b"},
                ),
                require_planning_bundle=False,
            )
            return MonitoringRuntimeResult(
                run_id=run.run_id,
                status=completed_run.status,
                source_results=tuple(source_results),
                observed_raw_items=observed_raw_items,
                ai_filter_results=tuple(ai_filter_results),
                filtered_raw_items=tuple(filtered_raw_items),
                career_signals=tuple(career_signals),
                scored_career_signals=priority_result.scored_career_signals,
                priority_assessment_diagnostics=priority_result.diagnostics,
                career_signal_routing=career_signal_routing,
                career_intelligence_interpretation=(
                    career_intelligence_interpretation
                ),
                career_intelligence_brief=career_intelligence_brief,
                summary=summary,
            )
        except Exception as error:
            self._fail_run(
                run_id=run.run_id,
                stage=stage,
                error=error,
                source_results=source_results,
                outcomes=all_outcomes,
            )
            if isinstance(error, MonitoringRuntimeError):
                raise
            raise MonitoringRuntimeError(
                f"Monitoring runtime failed during {stage}."
            ) from error

    def _execute_handoff(
        self,
        *,
        run_id: str,
        handoff: Phase7MonitoringHandoff,
    ) -> tuple[
        MonitoringSourceResult,
        tuple[MonitoringCandidateOutcome, ...],
    ]:
        requested_result_limit = _requested_result_limit(
            handoff,
            self.max_candidates_per_source,
        )
        execution = self.source_execution_repository.start_source_execution(
            run_id,
            build_monitoring_source_execution_start(
                handoff,
                execution_mode=self.execution_mode,
                requested_result_limit=requested_result_limit,
            ),
        )
        try:
            raw_items = _validated_raw_items(
                self.dispatcher.dispatch(handoff)
            )
            if requested_result_limit is not None:
                raw_items = raw_items[:requested_result_limit]
        except Exception as acquisition_error:
            failed = self.source_execution_repository.fail_execution(
                execution.source_execution_id,
                SourceExecutionFailure(
                    error_type=type(acquisition_error).__name__,
                    error_message=_concise_error_message(acquisition_error),
                ),
            )
            return (
                MonitoringSourceResult(
                    handoff_id=handoff.phase7_monitoring_handoff_id,
                    candidate_source_id=handoff.candidate_source_id,
                    source_execution_id=execution.source_execution_id,
                    status=failed.status,
                    returned_candidate_count=0,
                    canonical_candidate_count=0,
                    new_source_item_count=0,
                    existing_source_item_count=0,
                    error_type=failed.error_type,
                    error_message=failed.error_message,
                ),
                (),
            )

        try:
            batch = self.registrar.persist_candidates(
                run_id=run_id,
                source_execution_id=execution.source_execution_id,
                raw_items=raw_items,
            )
            completed = self.source_execution_repository.complete_execution(
                execution.source_execution_id,
                SourceExecutionCompletion(
                    returned_item_count=len(raw_items),
                    discovered_item_count=len(batch.outcomes),
                ),
            )
            return (
                MonitoringSourceResult(
                    handoff_id=handoff.phase7_monitoring_handoff_id,
                    candidate_source_id=handoff.candidate_source_id,
                    source_execution_id=execution.source_execution_id,
                    status=completed.status,
                    returned_candidate_count=len(raw_items),
                    canonical_candidate_count=len(batch.outcomes),
                    new_source_item_count=batch.created_count,
                    existing_source_item_count=batch.existing_count,
                ),
                batch.outcomes,
            )
        except Exception as persistence_error:
            current = self.source_execution_repository.get_source_execution(
                execution.source_execution_id
            )
            if current is not None and current.status == RUNNING:
                self.source_execution_repository.fail_execution(
                    execution.source_execution_id,
                    SourceExecutionFailure(
                        error_type=type(persistence_error).__name__,
                        error_message=_concise_error_message(
                            persistence_error
                        ),
                    ),
                )
            raise MonitoringRuntimeError(
                "Monitoring discovery persistence failed for handoff "
                f"{handoff.phase7_monitoring_handoff_id!r}."
            ) from persistence_error

    def _filter_new_candidates(
        self,
        *,
        run_id: str,
        candidates: list[FilterCandidate],
        user_profile: UserProfile,
        target_career_paths: list[TargetCareerPath],
    ) -> tuple[list[AIFilterResult], list[RawItem], dict[str, int]]:
        ai_filter_results: list[AIFilterResult] = []
        filtered_raw_items: list[RawItem] = []
        accepted_source_item_ids_by_signal_id: dict[str, int] = {}
        for candidate in candidates:
            report = execute_filter_candidate_with_provenance(
                run_id=run_id,
                candidate=candidate,
                filter_decision_repository=self.filter_decision_repository,
                ai_filter_executor=self.ai_filter_executor,
                user_profile=user_profile,
                target_career_paths=target_career_paths,
                execution_mode=self.ai_filter_execution_mode,
                provider=self.ai_filter_provider,
                model=self.ai_filter_model,
            )
            ai_filter_results.extend(report.ai_filter_results)
            filtered_raw_items.extend(report.filtered_raw_items)
            if report.raw_item_statuses[0].status == "processed_accepted":
                signal_id = build_signal_id(candidate.raw_item)
                previous_source_item_id = (
                    accepted_source_item_ids_by_signal_id.get(signal_id)
                )
                if (
                    previous_source_item_id is not None
                    and previous_source_item_id != candidate.source_item_id
                ):
                    raise MonitoringRuntimeError(
                        "Ambiguous accepted Monitoring SourceItem mapping for "
                        f"CareerSignal {signal_id!r}."
                    )
                accepted_source_item_ids_by_signal_id[signal_id] = (
                    candidate.source_item_id
                )
        return (
            ai_filter_results,
            filtered_raw_items,
            accepted_source_item_ids_by_signal_id,
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

    def _build_summary(
        self,
        *,
        source_results: list[MonitoringSourceResult],
        outcomes: list[MonitoringCandidateOutcome],
        new_candidate_count: int,
    ) -> dict[str, Any]:
        return {
            "monitoring_handoff_count": len(source_results),
            "monitoring_source_execution_count": len(source_results),
            "monitoring_candidate_count": len(outcomes),
            "monitoring_new_source_item_count": new_candidate_count,
            "monitoring_existing_source_item_count": sum(
                not item.created_new for item in outcomes
            ),
            "monitoring_historical_duplicate_count": sum(
                item.historical_duplicate for item in outcomes
            ),
            "monitoring_filter_eligible_count": new_candidate_count,
            "monitoring_source_failure_count": sum(
                item.status == "failed" for item in source_results
            ),
        }

    def _fail_run(
        self,
        *,
        run_id: str,
        stage: str,
        error: BaseException,
        source_results: list[MonitoringSourceResult],
        outcomes: list[MonitoringCandidateOutcome],
    ) -> None:
        try:
            run = self.pipeline_run_repository.get_run(run_id)
            if run is None or run.status != RUNNING:
                return
            self.pipeline_run_repository.fail_run(
                run_id,
                PipelineRunFailure(
                    failure_stage=stage,
                    error_type=type(error).__name__,
                    error_message=_concise_error_message(error),
                    summary=self._build_summary(
                        source_results=source_results,
                        outcomes=outcomes,
                        new_candidate_count=len(
                            {
                                item.source_item_id
                                for item in outcomes
                                if item.created_new
                            }
                        ),
                    ),
                    metadata={"monitoring_runtime": "phase9b"},
                ),
            )
        except Exception as lifecycle_error:
            if hasattr(error, "add_note"):
                error.add_note(
                    "PipelineRun failure persistence also failed: "
                    f"{type(lifecycle_error).__name__}: "
                    f"{_concise_error_message(lifecycle_error)}"
                )


def _unique_positioned_items(
    raw_items: Iterable[RawItem],
) -> list[tuple[int, RawItem]]:
    unique_items: list[tuple[int, RawItem]] = []
    seen_fingerprints: set[str] = set()
    for position, raw_item in enumerate(raw_items):
        fingerprint = fingerprint_raw_item(raw_item)
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        unique_items.append((position, raw_item))
    return unique_items


def _candidate_outcome(
    *,
    position: int,
    raw_item: RawItem,
    persistence: SourceItemPersistenceResult,
    status: Any,
) -> MonitoringCandidateOutcome:
    historical_duplicate = (
        status.status == DEFERRED
        and status.deferred_reason == HISTORICAL_DUPLICATE_REASON
    )
    return MonitoringCandidateOutcome(
        source_item_id=persistence.source_item_id,
        fingerprint=persistence.fingerprint,
        result_position=position,
        created_new=persistence.created_new,
        historical_duplicate=historical_duplicate,
        filter_eligible=status.status == PENDING,
        raw_item=raw_item,
    )


def _new_filter_candidates(
    outcomes: Iterable[MonitoringCandidateOutcome],
) -> list[FilterCandidate]:
    candidates: list[FilterCandidate] = []
    seen_source_item_ids: set[int] = set()
    for raw_item_index, outcome in enumerate(outcomes):
        if (
            not outcome.created_new
            or outcome.source_item_id in seen_source_item_ids
        ):
            continue
        seen_source_item_ids.add(outcome.source_item_id)
        candidates.append(
            FilterCandidate(
                source_item_id=outcome.source_item_id,
                raw_item=outcome.raw_item,
                raw_item_index=raw_item_index,
            )
        )
    return candidates


def _validated_raw_items(value: Any) -> list[RawItem]:
    if value is None or isinstance(value, (str, bytes, dict)):
        raise TypeError("Monitoring adapter must return RawItem objects.")
    raw_items = list(value)
    if not all(isinstance(item, RawItem) for item in raw_items):
        raise TypeError("Monitoring adapter returned a non-RawItem candidate.")
    return raw_items


def _requested_result_limit(
    handoff: Phase7MonitoringHandoff,
    runtime_limit: int | None,
) -> int | None:
    configured_limit = handoff.provenance.get(
        "max_discovered_items_per_run"
    )
    if configured_limit is not None:
        configured_limit = _optional_nonnegative_integer(
            configured_limit,
            "max_discovered_items_per_run",
        )
    if runtime_limit is None:
        return configured_limit
    if configured_limit is None:
        return runtime_limit
    return min(runtime_limit, configured_limit)


def _assert_source_execution_accounting(
    *,
    source_execution_repository: SourceExecutionRepository,
    run_id: str,
    expected_handoff_count: int,
) -> None:
    executions = source_execution_repository.list_source_executions(run_id)
    if len(executions) != expected_handoff_count:
        raise MonitoringRuntimeError(
            "Monitoring SourceExecution count does not match handoff count: "
            f"expected={expected_handoff_count}, actual={len(executions)}."
        )
    running_ids = [
        item.source_execution_id
        for item in executions
        if item.status == RUNNING
    ]
    if running_ids:
        raise MonitoringRuntimeError(
            "Monitoring SourceExecutions remain running: "
            f"{running_ids}."
        )


def _with_handoff_provenance(
    raw_item: RawItem,
    handoff: Phase7MonitoringHandoff,
) -> RawItem:
    metadata = dict(raw_item.metadata)
    metadata.update(
        {
            "monitoring_handoff_id": (
                handoff.phase7_monitoring_handoff_id
            ),
            "monitoring_candidate_source_id": handoff.candidate_source_id,
            "monitoring_entity_id": handoff.entity_id,
            "monitoring_acquisition_resolution_id": (
                handoff.acquisition_resolution_id
            ),
            "monitoring_acquisition_method": (
                handoff.acquisition_method.value
            ),
            "monitoring_acquisition_config_ref": (
                handoff.acquisition_config_ref
            ),
        }
    )
    return RawItem(
        source_type=raw_item.source_type,
        title=raw_item.title,
        organization=raw_item.organization,
        url=raw_item.url,
        published_at=raw_item.published_at,
        raw_text=raw_item.raw_text,
        metadata=metadata,
    )


def _final_evaluation_ids_by_candidate(
    payload: dict[str, Any],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in payload.get("acquisition_resolution_results", []):
        plan_ref = dict(item.get("acquisition_plan_ref") or {})
        candidate_source_id = str(
            plan_ref.get("candidate_source_id", "")
        ).strip()
        final_evaluation_id = str(
            plan_ref.get("final_source_evaluation_id", "")
        ).strip()
        if candidate_source_id and final_evaluation_id:
            result[candidate_source_id] = final_evaluation_id
    return result


def _validate_unique_handoffs(
    handoffs: Iterable[Phase7MonitoringHandoff],
) -> None:
    handoff_ids: set[str] = set()
    candidate_source_ids: set[str] = set()
    for handoff in handoffs:
        if handoff.phase7_monitoring_handoff_id in handoff_ids:
            raise MonitoringRuntimeCompatibilityError(
                "Monitoring handoff IDs must be unique."
            )
        if handoff.candidate_source_id in candidate_source_ids:
            raise MonitoringRuntimeCompatibilityError(
                "Monitoring candidate source IDs must be unique."
            )
        handoff_ids.add(handoff.phase7_monitoring_handoff_id)
        candidate_source_ids.add(handoff.candidate_source_id)


def _optional_nonnegative_integer(
    value: Any,
    field_name: str,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MonitoringRuntimeCompatibilityError(
            f"{field_name} must be a non-negative integer."
        )
    return value


def _concise_error_message(error: BaseException) -> str:
    message = " ".join(str(error).strip().split())
    if not message:
        message = type(error).__name__
    return message[:MAX_RUNTIME_ERROR_MESSAGE_LENGTH]
