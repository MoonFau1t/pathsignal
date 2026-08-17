import hashlib
import importlib
import io
import json
from contextlib import ExitStack, redirect_stdout
from dataclasses import fields
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.ai_filter import execute_ai_filter
from src.career_signal_priority import (
    PriorityIntegrationBatchResult,
    ScoredCareerSignal,
)
from src.career_signal_scoring import PriorityScoreResult, PriorityTier
from src.config import DEFAULT_DATABASE_FILE
from src.database.connection import open_database_connection
from src.database.migrations import initialize_database
from src.database.repositories.career_signal_repository import (
    CareerSignalRepository,
)
from src.database.repositories.filter_decision_repository import (
    FilterDecisionRepository,
    HISTORICAL_DUPLICATE_REASON,
)
from src.database.repositories.pipeline_run_repository import (
    PipelineRunRepository,
)
from src.database.repositories.planning_bundle_repository import (
    PlanningBundleRepository,
)
from src.database.repositories.source_execution_repository import (
    SourceExecutionRepository,
)
from src.database.repositories.source_item_repository import SourceItemRepository
from src.models import (
    AIFilterExecutionReport,
    AIFilterResult,
    PipelineRunOutput,
    RawItem,
    SearchAPIExecutionReport,
    SignalCategory,
    SourceType,
)
from src.normalizer import normalize_raw_items_to_career_signals
from src.pipeline import MockPipeline, execute_pipeline_runtime
from src.priority_assessment import (
    AssessmentProfile,
    ComponentStatus,
    PriorityAssessmentResult,
    SemanticComponentResult,
)
from src.search_api_client import execute_search_api_plans
from tests.test_source_execution_pipeline_integration import FakeSearchClient
from tests.test_source_execution_repository import make_bundle_write


class ControlledFilterError(RuntimeError):
    pass


def _filter_fingerprint(raw_item):
    value = (
        f"{raw_item.source_type.value}|{raw_item.title}|{raw_item.url}|"
        f"{raw_item.raw_text[:200]}"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


class ScriptedFilterClient:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []

    def filter_item(self, raw_item, user_profile, target_career_paths):
        self.calls.append(raw_item)
        outcome = (
            self.outcomes[len(self.calls) - 1]
            if len(self.calls) <= len(self.outcomes)
            else "rejected"
        )
        if isinstance(outcome, BaseException):
            raise outcome
        values = outcome if isinstance(outcome, dict) else {}
        accepted = outcome == "accepted" or values.get("accepted", False)
        return AIFilterResult(
            raw_item_fingerprint=_filter_fingerprint(raw_item),
            title=raw_item.title,
            url=raw_item.url,
            is_relevant=accepted,
            confidence=values.get("confidence", 0.91 if accepted else 0.17),
            reason=values.get("reason", "accepted" if accepted else "rejected"),
            suggested_category=values.get(
                "category",
                SignalCategory.JOB if accepted else SignalCategory.UNKNOWN,
            ),
            matched_career_path_ids=values.get("matched", ["path_strategy"]),
            action=values.get("action", "keep" if accepted else "drop"),
            metadata=values.get("metadata", {}),
        )


class DuplicateRepresentationSearchClient:
    dry_run = False
    last_result_diagnostics = []

    def __init__(self):
        self.calls = []

    def search(self, search_plan):
        self.calls.append(search_plan.plan_id)
        if len(self.calls) > 1:
            return []
        common = {
            "source_type": SourceType.SEARCH_API,
            "organization": "Example",
            "url": "https://example.com/canonical",
            "published_at": None,
        }
        return [
            RawItem(
                **common,
                title="Earliest representation",
                raw_text="earliest",
                metadata={"provider": "brave", "position": 0},
            ),
            RawItem(
                **common,
                title="Later representation",
                raw_text="later",
                metadata={"provider": "brave", "position": 1},
            ),
        ]


class StaticSearchClient:
    dry_run = False
    last_result_diagnostics = []

    def __init__(self, raw_items):
        self.raw_items = list(raw_items)
        self.calls = []

    def search(self, search_plan):
        self.calls.append(search_plan.plan_id)
        return list(self.raw_items)


def _minimal_scored_signal(signal):
    components = {
        "user_policy_fit": SemanticComponentResult(
            status=ComponentStatus.AVAILABLE,
            score=0.5,
            reason="Synthetic policy fit.",
            evidence=("Synthetic policy evidence.",),
        ),
        "opportunity_feasibility": SemanticComponentResult(
            status=ComponentStatus.AVAILABLE,
            score=0.5,
            reason="Synthetic feasibility.",
            evidence=("Synthetic feasibility evidence.",),
        ),
    }
    return ScoredCareerSignal(
        career_signal=signal,
        priority_assessment=PriorityAssessmentResult(
            schema_version="priority_assessment_v1",
            signal_id=signal.signal_id,
            assessment_profile=AssessmentProfile.OPPORTUNITY,
            components=components,
            warnings=(),
        ),
        priority_score=PriorityScoreResult(
            signal_id=signal.signal_id,
            priority_score=50.0,
            tier=PriorityTier.MEDIUM,
            profile=AssessmentProfile.OPPORTUNITY,
            components={},
            matched_path_ids=(),
            policy_version="test_policy",
            renormalization_denominator=1.0,
            warnings=(),
        ),
        assessment_profile=AssessmentProfile.OPPORTUNITY,
    )


class RecordingPriorityAssessor:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        career_signals = tuple(kwargs["career_signals"])
        if career_signals:
            self.calls.append(career_signals)
        return PriorityIntegrationBatchResult(
            scored_career_signals=tuple(
                _minimal_scored_signal(signal)
                for signal in career_signals
            )
        )


class DelegatingFilterRepository:
    def __init__(self, repository):
        self.repository = repository

    def __getattr__(self, name):
        return getattr(self.repository, name)


class FailingRegistrationRepository(DelegatingFilterRepository):
    def register_run_filter_items(self, run_id):
        raise RuntimeError("controlled registration failure")


class FailingFailurePersistenceRepository(DelegatingFilterRepository):
    def fail_filter_execution(self, *args, **kwargs):
        raise RuntimeError("controlled failure persistence error")


class Phase8BPipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "phase8b.db"
        initialize_database(database_path=self.database_path)
        self.run_repository = PipelineRunRepository(self.database_path)
        self.planning_repository = PlanningBundleRepository(self.database_path)
        self.execution_repository = SourceExecutionRepository(self.database_path)
        self.source_repository = SourceItemRepository(self.database_path)
        self.career_repository = CareerSignalRepository(self.database_path)
        self.filter_repository = FilterDecisionRepository(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def count(self, table, where="", params=()):
        connection = open_database_connection(self.database_path)
        try:
            return int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} {where}", params
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def rows(self, sql, params=()):
        connection = open_database_connection(self.database_path)
        try:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]
        finally:
            connection.close()

    def execute_sql(self, sql, params=()):
        connection = open_database_connection(self.database_path)
        try:
            connection.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def build_pipeline(
        self,
        *,
        outcomes=(),
        search_client=None,
        max_plans=3,
        filter_repository="actual",
        run_repository="actual",
        planning_repository="actual",
        execution_repository="actual",
        source_repository="actual",
        career_repository="actual",
        ai_filter_executor=None,
        normalizer=None,
        raw_item_loader=None,
        execution_mode="live",
        events=None,
        ai_filter_execution_mode="live",
        priority_assessor=None,
    ):
        bundle = make_bundle_write()
        search_client = search_client or FakeSearchClient()
        filter_client = ScriptedFilterClient(outcomes)
        events = events if events is not None else []

        def filter_executor(raw_items, profile, paths):
            events.append(("filter", [item.title for item in raw_items]))
            return execute_ai_filter(
                raw_items,
                profile,
                paths,
                filter_client,
            )

        def recording_normalizer(raw_items, results):
            events.append(("normalizer", [item.title for item in raw_items]))
            return normalize_raw_items_to_career_signals(raw_items, results)

        def resolve(value, actual):
            return actual if value == "actual" else value

        pipeline = MockPipeline(
            raw_item_loader=raw_item_loader or (lambda: []),
            user_profile_loader=lambda: bundle.user_profile,
            search_scope_loader=lambda: bundle.search_scope,
            career_path_generator=lambda profile: bundle.target_career_paths,
            search_query_generator=lambda paths: bundle.search_queries,
            search_plan_builder=lambda queries, scope: bundle.search_plans,
            search_api_executor=(
                lambda plans, lifecycle=None: execute_search_api_plans(
                    plans,
                    search_client,
                    max_plans=max_plans,
                    execution_lifecycle=lifecycle,
                )
            ),
            rss_executor=lambda scope, plans, lifecycle=None: ([], 0),
            selected_website_executor=(
                lambda scope, plans, lifecycle=None: ([], 0)
            ),
            ai_filter_executor=ai_filter_executor or filter_executor,
            normalizer=normalizer or recording_normalizer,
            source_item_repository=resolve(source_repository, self.source_repository),
            career_signal_repository=resolve(
                career_repository, self.career_repository
            ),
            planning_bundle_repository=resolve(
                planning_repository, self.planning_repository
            ),
            user_preferences_loader=lambda: bundle.user_preferences,
            planning_model_provider=bundle.model_provider,
            planning_model_name=bundle.model_name,
            planning_prompt_version=bundle.prompt_version,
            planning_generator_config=bundle.generator_config,
            pipeline_run_repository=resolve(run_repository, self.run_repository),
            source_execution_repository=resolve(
                execution_repository, self.execution_repository
            ),
            execution_mode=execution_mode,
            filter_decision_repository=resolve(
                filter_repository, self.filter_repository
            ),
            ai_filter_execution_mode=ai_filter_execution_mode,
            ai_filter_provider="deepseek",
            ai_filter_model="deepseek-chat",
            priority_assessor=priority_assessor,
        )
        return pipeline, filter_client, events

    def execute(self, pipeline):
        with redirect_stdout(io.StringIO()):
            return execute_pipeline_runtime(
                pipeline,
                output_persister=lambda output: output.to_dict(),
            )

    def only_run(self):
        runs = self.run_repository.list_recent_runs(limit=20)
        self.assertEqual(len(runs), 1)
        return runs[0]


class InjectionAndRegistrationTests(Phase8BPipelineTestCase):
    def test_filter_repository_is_independently_optional(self):
        pipeline, _, _ = self.build_pipeline(filter_repository=None)
        self.assertIsNone(pipeline.filter_decision_repository)

    def test_filter_repository_is_accepted_by_constructor(self):
        pipeline, _, _ = self.build_pipeline()
        self.assertIs(pipeline.filter_decision_repository, self.filter_repository)

    def test_every_unique_discovered_item_is_registered(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(pipeline)
        coverage = self.filter_repository.get_run_filter_coverage(
            self.only_run().run_id
        )
        self.assertEqual(coverage.discovered_source_items, 3)
        self.assertEqual(coverage.registered_filter_items, 3)

    def test_duplicate_discoveries_register_once(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=["rejected"],
            search_client=FakeSearchClient(
                duplicate_results=True,
                shared_result=True,
            ),
        )
        self.execute(pipeline)
        self.assertEqual(self.count("run_source_item_filter_statuses"), 1)

    def test_registration_failure_prevents_filtering(self):
        repository = FailingRegistrationRepository(self.filter_repository)
        pipeline, client, _ = self.build_pipeline(filter_repository=repository)
        with self.assertRaisesRegex(RuntimeError, "registration failed"):
            self.execute(pipeline)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.only_run().failure_stage, "filter_item_registration")

    def test_zero_discovered_items_is_valid(self):
        bundle = make_bundle_write()
        client = FakeSearchClient(
            zero_result_plan_ids=[plan.plan_id for plan in bundle.search_plans]
        )
        pipeline, filter_client, _ = self.build_pipeline(search_client=client)
        self.execute(pipeline)
        self.assertEqual(filter_client.calls, [])
        self.assertEqual(self.count("run_source_item_filter_statuses"), 0)

    def test_registration_occurs_before_first_filter_call(self):
        observed = []
        pipeline, client, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        original = client.filter_item

        def observe(*args, **kwargs):
            observed.append(self.count("run_source_item_filter_statuses"))
            return original(*args, **kwargs)

        client.filter_item = observe
        self.execute(pipeline)
        self.assertEqual(observed, [3, 3, 3])


class ReconciliationAndDeferralTests(Phase8BPipelineTestCase):
    def test_duplicate_raw_items_are_filtered_once(self):
        pipeline, client, _ = self.build_pipeline(
            outcomes=["accepted"],
            search_client=FakeSearchClient(
                duplicate_results=True,
                shared_result=True,
            ),
        )
        self.execute(pipeline)
        self.assertEqual(len(client.calls), 1)

    def test_canonical_raw_item_is_earliest_representation(self):
        pipeline, client, _ = self.build_pipeline(
            outcomes=["accepted"],
            search_client=DuplicateRepresentationSearchClient(),
            max_plans=1,
        )
        output, _ = self.execute(pipeline)
        self.assertEqual(client.calls[0].title, "Earliest representation")
        self.assertEqual(output.filtered_raw_items[0].title, "Earliest representation")

    def test_duplicates_are_reconciled_before_filtering(self):
        pipeline, client, _ = self.build_pipeline(
            outcomes=["rejected"] * 3,
            search_client=FakeSearchClient(duplicate_results=True),
        )
        self.execute(pipeline)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(self.count("source_items"), 3)

    def test_distinct_source_items_remain_distinct(self):
        pipeline, client, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(pipeline)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(self.count("run_source_item_filter_statuses"), 3)

    def test_one_run_source_item_never_gets_duplicate_llm_calls(self):
        pipeline, client, _ = self.build_pipeline(
            outcomes=["rejected"],
            search_client=FakeSearchClient(duplicate_results=True, shared_result=True),
        )
        self.execute(pipeline)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.count("filter_executions"), 1)

    def test_reconciliation_failure_is_surfaced(self):
        pipeline, _, _ = self.build_pipeline()
        with patch.object(
            pipeline,
            "_reconcile_filter_candidates",
            side_effect=RuntimeError("controlled lookup failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "reconciliation failed"):
                self.execute(pipeline)
        self.assertEqual(self.only_run().failure_stage, "filter_candidate_reconciliation")

    def test_all_reconciled_items_enter_filtering(self):
        pipeline, client, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(pipeline)
        self.assertEqual(len(client.calls), 3)

    def test_search_more_than_historical_cap_filters_every_new_item(self):
        raw_items = [
            RawItem(
                source_type=SourceType.SEARCH_API,
                title=f"Search result {index}",
                organization="Example",
                url=f"https://example.com/search/{index}",
                published_at=None,
                raw_text=f"Search evidence {index}.",
                metadata={"provider": "brave", "position": index},
            )
            for index in range(35)
        ]
        pipeline, client, _ = self.build_pipeline(
            outcomes=["rejected"] * 35,
            search_client=StaticSearchClient(raw_items),
            max_plans=1,
        )
        self.execute(pipeline)
        coverage = self.filter_repository.get_run_filter_coverage(
            self.only_run().run_id
        )
        self.assertEqual(len(client.calls), 35)
        self.assertEqual(coverage.filter_execution_count, 35)
        self.assertEqual(coverage.filter_decision_count, 35)
        self.assertEqual(coverage.deferred, 0)

    def test_historical_search_api_duplicate_skips_semantic_processing(self):
        raw_item = RawItem(
            source_type=SourceType.SEARCH_API,
            title="Historical strategy role",
            organization="Example",
            url="https://example.com/historical-role",
            published_at=None,
            raw_text="AI strategy analyst role.",
            metadata={"provider": "brave", "position": 1},
        )
        priority = RecordingPriorityAssessor()

        first_pipeline, first_filter, _ = self.build_pipeline(
            outcomes=["accepted"],
            search_client=StaticSearchClient([raw_item]),
            max_plans=1,
            priority_assessor=priority,
        )
        first_output, _ = self.execute(first_pipeline)

        self.assertEqual(len(first_filter.calls), 1)
        self.assertEqual(len(first_output.scored_career_signals), 1)
        self.assertEqual(len(priority.calls), 1)
        self.assertEqual(self.count("career_signals"), 1)

        second_pipeline, second_filter, _ = self.build_pipeline(
            outcomes=["accepted"],
            search_client=StaticSearchClient([raw_item]),
            max_plans=1,
            priority_assessor=priority,
        )
        second_output, _ = self.execute(second_pipeline)

        self.assertEqual(second_filter.calls, [])
        self.assertEqual(second_output.career_signals, [])
        self.assertEqual(second_output.scored_career_signals, [])
        self.assertEqual(len(priority.calls), 1)
        self.assertEqual(self.count("career_signals"), 1)
        self.assertEqual(self.count("filter_decisions"), 1)
        self.assertEqual(self.count("filter_executions"), 1)

        [source_item] = self.rows(
            "SELECT source_item_id, seen_count FROM source_items"
        )
        self.assertEqual(source_item["seen_count"], 2)

        runs = self.rows(
            "SELECT run_id FROM pipeline_runs ORDER BY started_at, run_id"
        )
        second_run_id = runs[1]["run_id"]
        [status] = self.rows(
            """
            SELECT status, deferred_reason, source_item_id
            FROM run_source_item_filter_statuses
            WHERE run_id = ?
            """,
            (second_run_id,),
        )
        self.assertEqual(status["source_item_id"], source_item["source_item_id"])
        self.assertEqual(status["status"], "deferred")
        self.assertEqual(status["deferred_reason"], HISTORICAL_DUPLICATE_REASON)

    def test_mixed_search_api_batch_filters_only_new_source_items(self):
        duplicate = RawItem(
            source_type=SourceType.SEARCH_API,
            title="Existing strategy role",
            organization="Example",
            url="https://example.com/existing-role",
            published_at=None,
            raw_text="Existing AI strategy analyst role.",
            metadata={"provider": "brave", "position": 1},
        )
        new_item = RawItem(
            source_type=SourceType.SEARCH_API,
            title="New strategy role",
            organization="Example",
            url="https://example.com/new-role",
            published_at=None,
            raw_text="New AI strategy analyst role.",
            metadata={"provider": "brave", "position": 2},
        )
        priority = RecordingPriorityAssessor()

        first_pipeline, first_filter, _ = self.build_pipeline(
            outcomes=["accepted"],
            search_client=StaticSearchClient([duplicate]),
            max_plans=1,
            priority_assessor=priority,
        )
        self.execute(first_pipeline)
        self.assertEqual(len(first_filter.calls), 1)

        mixed_pipeline, mixed_filter, _ = self.build_pipeline(
            outcomes=["accepted"],
            search_client=StaticSearchClient([duplicate, new_item]),
            max_plans=1,
            priority_assessor=priority,
        )
        mixed_output, _ = self.execute(mixed_pipeline)

        self.assertEqual(
            [item.title for item in mixed_filter.calls],
            ["New strategy role"],
        )
        self.assertEqual(
            [signal.title for signal in mixed_output.career_signals],
            ["New strategy role"],
        )
        self.assertEqual(len(mixed_output.scored_career_signals), 1)
        self.assertEqual(len(priority.calls), 2)
        self.assertEqual(self.count("source_items"), 2)
        self.assertEqual(self.count("career_signals"), 2)
        self.assertEqual(self.count("filter_decisions"), 2)
        self.assertEqual(self.count("filter_executions"), 2)

        source_items = self.rows(
            """
            SELECT title, seen_count
            FROM source_items
            ORDER BY title
            """
        )
        self.assertEqual(
            [(row["title"], row["seen_count"]) for row in source_items],
            [("Existing strategy role", 2), ("New strategy role", 1)],
        )

        runs = self.rows(
            "SELECT run_id FROM pipeline_runs ORDER BY started_at, run_id"
        )
        mixed_run_id = runs[1]["run_id"]
        statuses = self.rows(
            """
            SELECT s.title, f.status, f.deferred_reason
            FROM run_source_item_filter_statuses f
            JOIN source_items s ON s.source_item_id = f.source_item_id
            WHERE f.run_id = ?
            ORDER BY s.title
            """,
            (mixed_run_id,),
        )
        self.assertEqual(
            [
                (row["title"], row["status"], row["deferred_reason"])
                for row in statuses
            ],
            [
                (
                    "Existing strategy role",
                    "deferred",
                    HISTORICAL_DUPLICATE_REASON,
                ),
                ("New strategy role", "accepted", None),
            ],
        )

    def test_rejected_items_do_not_create_item_limit_deferrals(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(pipeline)
        coverage = self.filter_repository.get_run_filter_coverage(
            self.only_run().run_id
        )
        self.assertEqual((coverage.rejected, coverage.deferred), (3, 0))


class ExecutionAndDecisionTests(Phase8BPipelineTestCase):
    def test_each_actual_invocation_creates_one_execution(self):
        pipeline, client, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(pipeline)
        self.assertEqual(self.count("filter_executions"), len(client.calls))

    def test_provider_and_model_round_trip(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"])
        self.execute(pipeline)
        execution = self.filter_repository.list_filter_executions(
            self.only_run().run_id
        )[0]
        self.assertEqual((execution.provider, execution.model), ("deepseek", "deepseek-chat"))

    def test_execution_mode_round_trips(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=["rejected"], ai_filter_execution_mode="dry_run"
        )
        self.execute(pipeline)
        execution = self.filter_repository.list_filter_executions(
            self.only_run().run_id
        )[0]
        self.assertEqual(execution.execution_mode, "dry_run")

    def test_accepted_output_persists_accepted_decision(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["accepted"])
        self.execute(pipeline)
        decision = self.filter_repository.list_filter_decisions_for_run(
            self.only_run().run_id
        )[0]
        self.assertEqual(decision.decision, "accepted")

    def test_rejected_output_persists_rejected_decision(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"])
        self.execute(pipeline)
        decision = self.filter_repository.list_filter_decisions_for_run(
            self.only_run().run_id
        )[0]
        self.assertEqual(decision.decision, "rejected")

    def test_reason_and_confidence_round_trip(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=[{"reason": "real reason", "confidence": 0.42}],
        )
        self.execute(pipeline)
        decision = self.filter_repository.list_filter_decisions_for_run(
            self.only_run().run_id
        )[0]
        self.assertEqual((decision.reason, decision.confidence), ("real reason", 0.42))

    def test_missing_reason_and_confidence_remain_null(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=[{"reason": None, "confidence": None}]
        )
        self.execute(pipeline)
        decision = self.filter_repository.list_filter_decisions_for_run(
            self.only_run().run_id
        )[0]
        self.assertIsNone(decision.reason)
        self.assertIsNone(decision.confidence)

    def test_matched_paths_and_action_are_stored_when_available(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=[{"matched": ["path_product"], "action": "review"}],
        )
        self.execute(pipeline)
        decision = self.filter_repository.list_filter_decisions_for_run(
            self.only_run().run_id
        )[0]
        self.assertEqual(decision.matched_career_path_ids, ("path_product",))
        self.assertEqual(decision.metadata["suggested_action"], "review")

    def test_prompt_version_is_not_fabricated(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"])
        self.execute(pipeline)
        execution = self.filter_repository.list_filter_executions(
            self.only_run().run_id
        )[0]
        self.assertIsNone(execution.prompt_version)
        self.assertIsNone(execution.prompt_fingerprint)


class FailureAndNormalizationTests(Phase8BPipelineTestCase):
    def test_filter_exception_marks_execution_and_ledger_failed(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=[ControlledFilterError("first failed")]
        )
        self.execute(pipeline)
        execution = self.filter_repository.list_filter_executions(
            self.only_run().run_id
        )[0]
        self.assertEqual(execution.status, "failed")
        self.assertEqual(
            self.filter_repository.get_run_filter_coverage(self.only_run().run_id).failed,
            1,
        )

    def test_failed_item_receives_no_decision(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=[ControlledFilterError("failed")] * 3
        )
        self.execute(pipeline)
        self.assertEqual(self.count("filter_decisions"), 0)

    def test_processing_continues_after_filter_failure(self):
        pipeline, client, _ = self.build_pipeline(
            outcomes=[ControlledFilterError("failed"), "accepted", "rejected"]
        )
        self.execute(pipeline)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(self.count("filter_decisions"), 2)

    def test_failure_is_not_converted_to_rejection(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=[ControlledFilterError("failed")] * 3
        )
        self.execute(pipeline)
        coverage = self.filter_repository.get_run_filter_coverage(
            self.only_run().run_id
        )
        self.assertEqual((coverage.failed, coverage.rejected), (3, 0))

    def test_failure_error_remains_diagnosable(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=[ControlledFilterError("diagnostic marker")]
        )
        self.execute(pipeline)
        execution = self.filter_repository.list_filter_executions(
            self.only_run().run_id
        )[0]
        self.assertIn("diagnostic marker", execution.error_message)

    def test_failure_persistence_error_is_surfaced(self):
        repository = FailingFailurePersistenceRepository(self.filter_repository)
        pipeline, _, _ = self.build_pipeline(
            outcomes=[ControlledFilterError("original failure")],
            filter_repository=repository,
        )
        with self.assertRaisesRegex(RuntimeError, "could not be persisted") as context:
            self.execute(pipeline)
        self.assertIn("original failure", str(context.exception.__cause__))
        self.assertEqual(
            self.only_run().failure_stage,
            "filter_execution_failure_persistence",
        )

    def test_only_accepted_items_enter_normalizer(self):
        pipeline, _, events = self.build_pipeline(
            outcomes=["accepted", "rejected", ControlledFilterError("failed")]
        )
        self.execute(pipeline)
        normalizer_event = [event for event in events if event[0] == "normalizer"][0]
        self.assertEqual(len(normalizer_event[1]), 1)

    def test_rejected_items_do_not_enter_normalizer(self):
        pipeline, _, events = self.build_pipeline(
            outcomes=["rejected"] * 3
        )
        self.execute(pipeline)
        normalizer_event = [event for event in events if event[0] == "normalizer"][0]
        self.assertEqual(normalizer_event[1], [])

    def test_normalizer_remains_llm_and_network_free(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["accepted"])
        with patch("requests.get") as request_get, patch(
            "openai.OpenAI"
        ) as openai_client:
            self.execute(pipeline)
        request_get.assert_not_called()
        openai_client.assert_not_called()

    def test_career_signal_fields_remain_deterministic(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["accepted"])
        output, _ = self.execute(pipeline)
        signal = output.career_signals[0]
        self.assertEqual(signal.title, output.filtered_raw_items[0].title)
        self.assertEqual(signal.relevance_score, 91.0)

    def test_career_signal_identity_is_unchanged(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["accepted"])
        output, _ = self.execute(pipeline)
        first_signal_id = output.career_signals[0].signal_id
        second_pipeline, _, _ = self.build_pipeline(outcomes=["accepted"])
        second_output, _ = self.execute(second_pipeline)
        self.assertEqual(second_output.career_signals, [])
        [row] = self.rows("SELECT signal_id FROM career_signals")
        self.assertEqual(row["signal_id"], first_signal_id)


class MaterializationAndAccountingTests(Phase8BPipelineTestCase):
    def test_accepted_source_item_with_signal_passes_materialization(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["accepted"])
        self.execute(pipeline)
        run = self.only_run()
        self.assertEqual(run.summary["accepted_with_career_signal_count"], 1)
        self.assertEqual(run.summary["accepted_without_career_signal_count"], 0)

    def test_accepted_source_item_without_signal_fails_validation(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["accepted"])
        with redirect_stdout(io.StringIO()):
            output = pipeline.run()
        self.execute_sql("DELETE FROM career_signals")
        with self.assertRaisesRegex(RuntimeError, "materialization validation failed"):
            pipeline.complete_pipeline_run(output)
        self.assertEqual(self.only_run().failure_stage, "career_signal_materialization_validation")

    def test_nonaccepted_items_do_not_require_signals(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=["rejected", ControlledFilterError("failed")]
        )
        self.execute(pipeline)
        self.assertEqual(self.count("career_signals"), 0)
        self.assertEqual(self.only_run().status, "completed")

    def test_idempotently_reused_linked_signal_counts_as_materialized(self):
        first, _, _ = self.build_pipeline(outcomes=["accepted"])
        self.execute(first)
        second, _, _ = self.build_pipeline(outcomes=["accepted"])
        self.execute(second)
        self.assertEqual(self.count("career_signals"), 1)
        second_run = self.run_repository.list_recent_runs(limit=1)[0]
        self.assertEqual(second_run.summary["accepted_with_career_signal_count"], 0)
        self.assertEqual(second_run.summary["accepted_filter_count"], 0)
        self.assertEqual(second_run.summary["deferred_filter_count"], 3)

    def test_filter_decision_has_no_career_signal_ownership(self):
        columns = {
            row["name"]
            for row in self.rows("PRAGMA table_info(filter_decisions)")
        }
        self.assertNotIn("career_signal_id", columns)

    def test_all_final_ledger_states_pass_accounting(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=["accepted", "rejected", ControlledFilterError("failed")],
        )
        self.execute(pipeline)
        coverage = self.filter_repository.assert_run_filter_accounting_complete(
            self.only_run().run_id
        )
        self.assertEqual((coverage.accepted, coverage.rejected, coverage.failed), (1, 1, 1))

    def test_missing_ledger_row_prevents_completion(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"])
        with redirect_stdout(io.StringIO()):
            output = pipeline.run()
        self.execute_sql(
            "DELETE FROM filter_decisions WHERE source_item_id = "
            "(SELECT MAX(source_item_id) FROM run_source_item_filter_statuses)"
        )
        self.execute_sql(
            "DELETE FROM run_source_item_filter_statuses WHERE source_item_id = "
            "(SELECT MAX(source_item_id) FROM run_source_item_filter_statuses)"
        )
        with self.assertRaisesRegex(RuntimeError, "filter accounting"):
            pipeline.complete_pipeline_run(output)

    def test_pending_item_prevents_completion(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"])
        with redirect_stdout(io.StringIO()):
            output = pipeline.run()
        self.execute_sql(
            "DELETE FROM filter_decisions WHERE source_item_id = "
            "(SELECT MAX(source_item_id) FROM run_source_item_filter_statuses)"
        )
        self.execute_sql(
            "UPDATE run_source_item_filter_statuses SET status='pending', "
            "filter_execution_id=NULL, deferred_reason=NULL, "
            "started_at=NULL, completed_at=NULL "
            "WHERE source_item_id = "
            "(SELECT MAX(source_item_id) FROM run_source_item_filter_statuses)"
        )
        with self.assertRaisesRegex(RuntimeError, "filter accounting"):
            pipeline.complete_pipeline_run(output)

    def test_running_item_prevents_completion(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"])
        with redirect_stdout(io.StringIO()):
            output = pipeline.run()
        self.execute_sql(
            "UPDATE run_source_item_filter_statuses SET status='running', "
            "completed_at=NULL WHERE status='rejected'"
        )
        with self.assertRaisesRegex(RuntimeError, "filter accounting"):
            pipeline.complete_pipeline_run(output)

    def test_malformed_completed_execution_prevents_completion(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"])
        with redirect_stdout(io.StringIO()):
            output = pipeline.run()
        self.execute_sql("DELETE FROM filter_decisions")
        with self.assertRaisesRegex(RuntimeError, "filter accounting"):
            pipeline.complete_pipeline_run(output)

    def test_accounting_validation_happens_before_run_completion(self):
        events = []
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"])
        original_assert = self.filter_repository.assert_run_filter_accounting_complete
        original_complete = self.run_repository.complete_run
        self.filter_repository.assert_run_filter_accounting_complete = (
            lambda run_id: (events.append("accounting"), original_assert(run_id))[1]
        )
        self.run_repository.complete_run = (
            lambda *args, **kwargs: (
                events.append("completion"),
                original_complete(*args, **kwargs),
            )[1]
        )
        self.execute(pipeline)
        self.assertLess(events.index("accounting"), events.index("completion"))

    def test_internal_summary_counts_are_accurate(self):
        pipeline, _, _ = self.build_pipeline(
            outcomes=["accepted", "rejected"]
        )
        self.execute(pipeline)
        summary = self.only_run().summary
        self.assertEqual(summary["unique_filter_candidate_count"], 3)
        self.assertEqual(summary["accepted_filter_count"], 1)
        self.assertEqual(summary["rejected_filter_count"], 2)
        self.assertEqual(summary["deferred_filter_count"], 0)
        self.assertEqual(summary["filter_execution_count"], 3)
        self.assertEqual(summary["filter_decision_count"], 3)


class CompatibilityAndMainTests(Phase8BPipelineTestCase):
    def test_no_filter_repository_preserves_aggregate_filter_call(self):
        calls = []

        def aggregate(raw_items, profile, paths):
            calls.append(len(raw_items))
            return AIFilterExecutionReport()

        pipeline, _, _ = self.build_pipeline(
            filter_repository=None,
            career_repository=None,
            ai_filter_executor=aggregate,
        )
        self.execute(pipeline)
        self.assertEqual(calls, [3])
        self.assertEqual(self.count("filter_executions"), 0)

    def test_filter_repository_does_not_force_other_optional_repositories(self):
        pipeline, _, _ = self.build_pipeline(
            filter_repository=self.filter_repository,
            run_repository=None,
            planning_repository=None,
            execution_repository=None,
            source_repository=None,
            career_repository=None,
            execution_mode="mock",
        )
        self.assertIs(pipeline.filter_decision_repository, self.filter_repository)
        self.assertIsNone(pipeline.source_item_repository)
        self.assertIsNone(pipeline.career_signal_repository)

    def test_phase7_execution_accounting_remains_complete(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(pipeline)
        coverage = self.execution_repository.assert_run_search_plan_accounting_complete(
            self.only_run().run_id
        )
        self.assertEqual(coverage.completed, 3)

    def test_planning_bundle_reuse_remains_unchanged(self):
        first, _, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(first)
        second, _, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(second)
        runs = self.run_repository.list_recent_runs(limit=2)
        self.assertEqual(runs[0].planning_bundle_id, runs[1].planning_bundle_id)

    def test_source_item_identity_reuses_rows_across_runs(self):
        first, _, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(first)
        second, _, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(second)
        self.assertEqual(self.count("source_items"), 3)
        self.assertEqual(self.count("run_source_item_filter_statuses"), 6)

    def test_second_run_gets_new_executions_and_decisions(self):
        first, _, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(first)
        second, _, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(second)
        self.assertEqual(self.count("filter_executions"), 3)
        self.assertEqual(self.count("filter_decisions"), 3)
        second_run = self.run_repository.list_recent_runs(limit=1)[0]
        self.assertEqual(second_run.summary["deferred_filter_count"], 3)

    def test_saved_json_contract_has_no_provenance_tables(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["accepted"])
        output, saved = self.execute(pipeline)
        self.assertEqual(set(saved), set(output.to_dict()))
        self.assertNotIn("filter_executions", saved)
        self.assertNotIn("filter_decisions", saved)

    def test_pipeline_run_output_contract_is_unchanged(self):
        field_names = {field.name for field in fields(PipelineRunOutput)}
        self.assertNotIn("filter_executions", field_names)
        self.assertNotIn("filter_decisions", field_names)
        self.assertNotIn("filter_coverage", field_names)

    def test_source_items_and_signals_have_no_direct_run_ownership(self):
        for table in ("source_items", "career_signals"):
            columns = {row["name"] for row in self.rows(f"PRAGMA table_info({table})")}
            self.assertNotIn("run_id", columns)

    def test_mock_raw_items_are_not_fabricated_as_source_items(self):
        mock_item = RawItem(
            source_type=SourceType.MOCK_JOB,
            title="Mock",
            organization="Example",
            url="https://example.com/mock",
            published_at=None,
            raw_text="mock",
        )
        pipeline, _, _ = self.build_pipeline(
            outcomes=["rejected"] * 3,
            raw_item_loader=lambda: [mock_item],
        )
        self.execute(pipeline)
        self.assertEqual(self.count("source_items"), 3)

    def test_source_dry_run_items_are_not_registered_or_filtered(self):
        pipeline, client, _ = self.build_pipeline(
            search_client=FakeSearchClient(dry_run=True),
        )
        self.execute(pipeline)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.count("source_items"), 0)
        self.assertEqual(self.count("run_source_item_filter_statuses"), 0)

    def test_incomplete_rss_and_selected_websites_do_not_block(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["rejected"] * 3)
        self.execute(pipeline)
        self.assertEqual(self.only_run().status, "completed")

    def test_no_migration_008_exists(self):
        self.assertFalse(Path("src/database/sql/008_filter_decision_integration.sql").exists())
        migrations = self.rows("SELECT version FROM schema_migrations ORDER BY version")
        self.assertNotIn("008", {row["version"] for row in migrations})

    def test_tests_use_only_temporary_database(self):
        self.assertNotEqual(self.database_path.resolve(), DEFAULT_DATABASE_FILE.resolve())
        self.assertTrue(str(self.database_path).startswith(self.temp_dir.name))

    def test_main_constructs_and_injects_filter_repository(self):
        main_module = importlib.import_module("src.main")
        marker = object()
        captured = {}

        class FakePipeline:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with ExitStack() as stack:
            stack.enter_context(patch.object(main_module, "ensure_project_directories"))
            stack.enter_context(patch.object(main_module, "validate_required_planning_inputs"))
            stack.enter_context(patch.object(main_module, "get_database_path", return_value=self.database_path))
            stack.enter_context(patch.object(main_module, "initialize_database"))
            for name in (
                "PipelineRunRepository",
                "SourceExecutionRepository",
                "PlanningBundleRepository",
                "SourceItemRepository",
                "CareerSignalRepository",
            ):
                stack.enter_context(patch.object(main_module, name, return_value=object()))
            constructor = stack.enter_context(
                patch.object(main_module, "FilterDecisionRepository", return_value=marker)
            )
            stack.enter_context(patch.object(main_module, "BraveSearchClient"))
            stack.enter_context(patch.object(main_module, "RSSClient"))
            stack.enter_context(patch.object(main_module, "SelectedWebsiteClient"))
            stack.enter_context(patch.object(main_module, "AIFilterClient"))
            interpretation_constructor = stack.enter_context(
                patch.object(
                    main_module,
                    "CareerIntelligenceInterpretationClient",
                )
            )
            stack.enter_context(
                patch.object(main_module, "load_user_preferences_from_json", return_value={})
            )
            stack.enter_context(patch.object(main_module, "_file_sha256", return_value=None))
            stack.enter_context(patch.object(main_module, "MockPipeline", FakePipeline))
            runtime = stack.enter_context(patch.object(main_module, "execute_pipeline_runtime"))
            main_module.main()

            self.assertTrue(callable(captured["interpretation_executor"]))
            context = object()
            captured["interpretation_executor"](context)
            interpretation_constructor.assert_called_once_with(
                provider=main_module.LLM_PROVIDER,
                api_key=main_module.LLM_API_KEY,
                base_url=main_module.LLM_BASE_URL,
                model=main_module.AI_FILTER_MODEL,
            )
            interpretation_constructor.return_value.interpret.assert_called_once_with(
                context
            )

        constructor.assert_called_once_with(database_path=self.database_path)
        self.assertIs(captured["filter_decision_repository"], marker)
        runtime.assert_called_once()

    def test_importing_main_does_not_initialize_database(self):
        main_module = importlib.import_module("src.main")
        with patch.object(main_module, "initialize_database") as initialize:
            importlib.reload(main_module)
        initialize.assert_not_called()

    def test_no_live_network_or_llm_call_occurs(self):
        pipeline, _, _ = self.build_pipeline(outcomes=["accepted"])
        with patch("requests.get") as request_get, patch(
            "openai.OpenAI"
        ) as openai_client:
            self.execute(pipeline)
        request_get.assert_not_called()
        openai_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
