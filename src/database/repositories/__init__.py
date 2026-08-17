__all__ = [
    "CareerSignalRepository",
    "CareerSignalRepositoryError",
    "CareerSignalUpsertSummary",
    "CareerSignalWrite",
    "CareerSignalMaterializationCoverage",
    "FilterCoverage",
    "FilterDecisionInput",
    "FilterDecisionRecord",
    "FilterDecisionRepository",
    "FilterDecisionRepositoryError",
    "FilterExecutionRecord",
    "FilterExecutionStart",
    "FilterExecutionStartResult",
    "PlanningArtifactWrite",
    "PlanningBundlePersistSummary",
    "PlanningBundleRepository",
    "PlanningBundleRepositoryError",
    "PlanningBundleWrite",
    "PipelineRunCompletion",
    "PipelineRunFailure",
    "PipelineRunRecord",
    "PipelineRunRepository",
    "PipelineRunRepositoryError",
    "PipelineRunStart",
    "ProfileSnapshotResult",
    "SourceItemRepository",
    "SourceItemRepositoryError",
    "SourceItemPersistenceResult",
    "SourceItemUpsertSummary",
    "RunSearchPlanCoverage",
    "RunSearchPlanRegistration",
    "RunSearchPlanStatusRecord",
    "RunFilterRegistration",
    "RunSourceItemFilterStatusRecord",
    "SearchPlanExecutionStartResult",
    "SearchQueryCoverageRecord",
    "SourceExecutionCompletion",
    "SourceExecutionFailure",
    "SourceExecutionRecord",
    "SourceExecutionRepository",
    "SourceExecutionRepositoryError",
    "SourceExecutionStart",
    "SourceItemDiscoveryRecord",
    "SourceItemDiscoveryWrite",
]


def __getattr__(name: str):
    if name in {
        "CareerSignalMaterializationCoverage",
        "FilterCoverage",
        "FilterDecisionInput",
        "FilterDecisionRecord",
        "FilterDecisionRepository",
        "FilterDecisionRepositoryError",
        "FilterExecutionRecord",
        "FilterExecutionStart",
        "FilterExecutionStartResult",
        "RunFilterRegistration",
        "RunSourceItemFilterStatusRecord",
    }:
        from src.database.repositories.filter_decision_repository import (
            CareerSignalMaterializationCoverage,
            FilterCoverage,
            FilterDecisionInput,
            FilterDecisionRecord,
            FilterDecisionRepository,
            FilterDecisionRepositoryError,
            FilterExecutionRecord,
            FilterExecutionStart,
            FilterExecutionStartResult,
            RunFilterRegistration,
            RunSourceItemFilterStatusRecord,
        )

        exports = {
            "CareerSignalMaterializationCoverage": (
                CareerSignalMaterializationCoverage
            ),
            "FilterCoverage": FilterCoverage,
            "FilterDecisionInput": FilterDecisionInput,
            "FilterDecisionRecord": FilterDecisionRecord,
            "FilterDecisionRepository": FilterDecisionRepository,
            "FilterDecisionRepositoryError": FilterDecisionRepositoryError,
            "FilterExecutionRecord": FilterExecutionRecord,
            "FilterExecutionStart": FilterExecutionStart,
            "FilterExecutionStartResult": FilterExecutionStartResult,
            "RunFilterRegistration": RunFilterRegistration,
            "RunSourceItemFilterStatusRecord": (
                RunSourceItemFilterStatusRecord
            ),
        }

        return exports[name]

    if name in {
        "CareerSignalRepository",
        "CareerSignalRepositoryError",
        "CareerSignalUpsertSummary",
        "CareerSignalWrite",
    }:
        from src.database.repositories.career_signal_repository import (
            CareerSignalRepository,
            CareerSignalRepositoryError,
            CareerSignalUpsertSummary,
            CareerSignalWrite,
        )

        exports = {
            "CareerSignalRepository": CareerSignalRepository,
            "CareerSignalRepositoryError": CareerSignalRepositoryError,
            "CareerSignalUpsertSummary": CareerSignalUpsertSummary,
            "CareerSignalWrite": CareerSignalWrite,
        }

        return exports[name]

    if name in {
        "PlanningArtifactWrite",
        "PlanningBundlePersistSummary",
        "PlanningBundleRepository",
        "PlanningBundleRepositoryError",
        "PlanningBundleWrite",
        "ProfileSnapshotResult",
    }:
        from src.database.repositories.planning_bundle_repository import (
            PlanningArtifactWrite,
            PlanningBundlePersistSummary,
            PlanningBundleRepository,
            PlanningBundleRepositoryError,
            PlanningBundleWrite,
            ProfileSnapshotResult,
        )

        exports = {
            "PlanningArtifactWrite": PlanningArtifactWrite,
            "PlanningBundlePersistSummary": PlanningBundlePersistSummary,
            "PlanningBundleRepository": PlanningBundleRepository,
            "PlanningBundleRepositoryError": PlanningBundleRepositoryError,
            "PlanningBundleWrite": PlanningBundleWrite,
            "ProfileSnapshotResult": ProfileSnapshotResult,
        }

        return exports[name]

    if name in {
        "PipelineRunCompletion",
        "PipelineRunFailure",
        "PipelineRunRecord",
        "PipelineRunRepository",
        "PipelineRunRepositoryError",
        "PipelineRunStart",
    }:
        from src.database.repositories.pipeline_run_repository import (
            PipelineRunCompletion,
            PipelineRunFailure,
            PipelineRunRecord,
            PipelineRunRepository,
            PipelineRunRepositoryError,
            PipelineRunStart,
        )

        exports = {
            "PipelineRunCompletion": PipelineRunCompletion,
            "PipelineRunFailure": PipelineRunFailure,
            "PipelineRunRecord": PipelineRunRecord,
            "PipelineRunRepository": PipelineRunRepository,
            "PipelineRunRepositoryError": PipelineRunRepositoryError,
            "PipelineRunStart": PipelineRunStart,
        }

        return exports[name]

    if name in {
        "RunSearchPlanCoverage",
        "RunSearchPlanRegistration",
        "RunSearchPlanStatusRecord",
        "SearchPlanExecutionStartResult",
        "SearchQueryCoverageRecord",
        "SourceExecutionCompletion",
        "SourceExecutionFailure",
        "SourceExecutionRecord",
        "SourceExecutionRepository",
        "SourceExecutionRepositoryError",
        "SourceExecutionStart",
        "SourceItemDiscoveryRecord",
        "SourceItemDiscoveryWrite",
    }:
        from src.database.repositories.source_execution_repository import (
            RunSearchPlanCoverage,
            RunSearchPlanRegistration,
            RunSearchPlanStatusRecord,
            SearchPlanExecutionStartResult,
            SearchQueryCoverageRecord,
            SourceExecutionCompletion,
            SourceExecutionFailure,
            SourceExecutionRecord,
            SourceExecutionRepository,
            SourceExecutionRepositoryError,
            SourceExecutionStart,
            SourceItemDiscoveryRecord,
            SourceItemDiscoveryWrite,
        )

        exports = {
            "RunSearchPlanCoverage": RunSearchPlanCoverage,
            "RunSearchPlanRegistration": RunSearchPlanRegistration,
            "RunSearchPlanStatusRecord": RunSearchPlanStatusRecord,
            "SearchPlanExecutionStartResult": SearchPlanExecutionStartResult,
            "SearchQueryCoverageRecord": SearchQueryCoverageRecord,
            "SourceExecutionCompletion": SourceExecutionCompletion,
            "SourceExecutionFailure": SourceExecutionFailure,
            "SourceExecutionRecord": SourceExecutionRecord,
            "SourceExecutionRepository": SourceExecutionRepository,
            "SourceExecutionRepositoryError": SourceExecutionRepositoryError,
            "SourceExecutionStart": SourceExecutionStart,
            "SourceItemDiscoveryRecord": SourceItemDiscoveryRecord,
            "SourceItemDiscoveryWrite": SourceItemDiscoveryWrite,
        }

        return exports[name]

    if name in {
        "SourceItemPersistenceResult",
        "SourceItemRepository",
        "SourceItemRepositoryError",
        "SourceItemUpsertSummary",
    }:
        from src.database.repositories.source_item_repository import (
            SourceItemPersistenceResult,
            SourceItemRepository,
            SourceItemRepositoryError,
            SourceItemUpsertSummary,
        )

        exports = {
            "SourceItemPersistenceResult": SourceItemPersistenceResult,
            "SourceItemRepository": SourceItemRepository,
            "SourceItemRepositoryError": SourceItemRepositoryError,
            "SourceItemUpsertSummary": SourceItemUpsertSummary,
        }

        return exports[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
