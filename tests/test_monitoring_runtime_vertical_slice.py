from dataclasses import fields
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.ai_filter import execute_ai_filter
from src.career_intelligence_interpretation import (
    CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
    EMPTY_INPUT_WARNING,
    CareerIntelligenceInterpretationError,
    CareerIntelligenceInterpretationResult,
)
from src.career_intelligence_brief import CareerIntelligenceBriefError
from src.career_signal_priority import (
    PriorityIntegrationBatchResult,
    ScoredCareerSignal,
)
from src.career_signal_scoring import PriorityScoreResult, PriorityTier
from src.config import DEFAULT_DATABASE_FILE
from src.database.connection import open_database_connection
from src.database.migrations import discover_migrations, initialize_database
from src.database.repositories.career_signal_repository import (
    CareerSignalRepository,
)
from src.database.repositories.filter_decision_repository import (
    HISTORICAL_DUPLICATE_REASON,
    FilterDecisionRepository,
)
from src.database.repositories.pipeline_run_repository import (
    PipelineRunRepository,
)
from src.database.repositories.source_execution_repository import (
    SourceExecutionRepository,
)
from src.database.repositories.source_item_repository import (
    SourceItemRepository,
)
from src.database.source_identity import fingerprint_raw_item
from src.models import (
    AIFilterResult,
    CareerPathCategory,
    PipelineRunOutput,
    PipelineSummary,
    RawItem,
    SignalCategory,
    SourceType,
    TargetCareerPath,
    UserProfile,
)
from src.monitoring_runtime import (
    FeedMonitoringAdapter,
    MonitoringAcquisitionDispatcher,
    MonitoringRuntime,
    MonitoringRuntimeCompatibilityError,
    SelectedWebsiteMonitoringAdapter,
    load_phase7_monitoring_handoffs,
)
from src.normalizer import normalize_raw_items_to_career_signals
from src.priority_assessment import (
    AssessmentProfile,
    ComponentStatus,
    PriorityAssessmentResult,
    SemanticComponentResult,
)
from src.rss_client import RSSClient
from src.selected_website_client import SelectedWebsiteClient
from src.source_monitoring.acquisition_models import (
    AcquisitionMethod,
    Phase7MonitoringHandoff,
)
from src.source_monitoring.source_discovery_models import SourceRole


def make_profile() -> UserProfile:
    return UserProfile(
        profile_id="profile-monitoring",
        name="Monitoring User",
        background_summary="Strategy and AI research.",
        skills=["strategy", "AI"],
    )


def make_path() -> TargetCareerPath:
    return TargetCareerPath(
        path_id="path-monitoring",
        title="AI Strategy",
        category=CareerPathCategory.AI_STRATEGY,
        description="AI strategy and market intelligence.",
        fit_score=0.9,
        keywords=["AI", "strategy"],
    )


def make_handoff(
    key: str,
    method: AcquisitionMethod = AcquisitionMethod.RSS,
    *,
    source_url: str | None = None,
) -> Phase7MonitoringHandoff:
    is_feed = method in {AcquisitionMethod.RSS, AcquisitionMethod.ATOM}
    resolved_source_url = source_url or f"https://{key}.example.com/news"
    return Phase7MonitoringHandoff(
        phase7_monitoring_handoff_id=f"handoff-{key}",
        acquisition_resolution_id=f"resolution-{key}",
        candidate_source_id=f"candidate-{key}",
        entity_id=f"entity-{key}",
        source_url=resolved_source_url,
        acquisition_method=method,
        acquisition_config_ref=f"config-{key}",
        supported_information_need_ids=("need-monitoring",),
        source_role=SourceRole.NEWSROOM,
        provenance={
            "final_source_evaluation_id": f"evaluation-{key}",
            "verified_feed_url": (
                f"https://{key}.example.com/feed.xml" if is_feed else None
            ),
            "selected_feed_verification_result_id": (
                f"feed-result-{key}" if is_feed else None
            ),
            "selected_website_acquisition_config_id": (
                f"config-{key}" if not is_feed else None
            ),
            "max_discovered_items_per_run": 5,
        },
    )


def make_item(
    key: str,
    *,
    source_type: SourceType = SourceType.RSS,
    provider: str = "rss",
    url: str | None = None,
    raw_text: str | None = None,
    guid: str | None = None,
) -> RawItem:
    return RawItem(
        source_type=source_type,
        title=f"Article {key.upper()}",
        organization="Example Publisher",
        url=url or f"https://example.com/articles/{key}",
        published_at="2026-08-10T00:00:00+00:00",
        raw_text=raw_text or f"AI strategy content for {key}.",
        metadata={
            "provider": provider,
            "guid": guid or f"guid-{key}",
        },
    )


def make_monitoring_scored_signal(signal, score=80.0):
    profile = (
        AssessmentProfile.OPPORTUNITY
        if signal.category == SignalCategory.JOB
        else AssessmentProfile.INTELLIGENCE
    )
    components = (
        {
            "user_policy_fit": SemanticComponentResult(
                status=ComponentStatus.AVAILABLE,
                score=0.75,
                reason="Synthetic policy fit.",
                evidence=("Synthetic policy evidence.",),
            ),
            "opportunity_feasibility": SemanticComponentResult(
                status=ComponentStatus.AVAILABLE,
                score=0.75,
                reason="Synthetic feasibility.",
                evidence=("Synthetic feasibility evidence.",),
            ),
        }
        if profile == AssessmentProfile.OPPORTUNITY
        else {}
    )
    return ScoredCareerSignal(
        career_signal=signal,
        priority_assessment=PriorityAssessmentResult(
            schema_version="priority_assessment_v1",
            signal_id=signal.signal_id,
            assessment_profile=profile,
            components=components,
            warnings=(),
        ),
        priority_score=PriorityScoreResult(
            signal_id=signal.signal_id,
            priority_score=score,
            tier=PriorityTier.MEDIUM_HIGH,
            profile=profile,
            components={},
            matched_path_ids=(),
            policy_version="test_policy",
            renormalization_denominator=1.0,
            warnings=(),
        ),
        assessment_profile=profile,
    )


class RecordingInterpretationExecutor:
    def __init__(self, error=None):
        self.contexts = []
        self.error = error

    def __call__(self, context):
        self.contexts.append(context)
        if self.error is not None:
            raise self.error
        return CareerIntelligenceInterpretationResult(
            schema_version=CAREER_INTELLIGENCE_INTERPRETATION_SCHEMA_VERSION,
            input_signal_ids=tuple(
                item.career_signal.signal_id
                for item in context.intelligence_signals
            ),
            themes=(),
            key_developments=(),
            career_implications=(),
            warnings=(),
        )


class ScriptedAdapter:
    def __init__(self):
        self.responses: dict[str, list[object]] = {}
        self.call_counts: dict[str, int] = {}
        self.calls: list[Phase7MonitoringHandoff] = []

    def set_responses(self, handoff, *responses):
        self.responses[handoff.phase7_monitoring_handoff_id] = list(responses)

    def __call__(self, handoff):
        self.calls.append(handoff)
        key = handoff.phase7_monitoring_handoff_id
        call_index = self.call_counts.get(key, 0)
        self.call_counts[key] = call_index + 1
        configured = self.responses.get(key, [[]])
        response = configured[min(call_index, len(configured) - 1)]
        if isinstance(response, BaseException):
            raise response
        return list(response)


class ScriptedFilterClient:
    def __init__(self, decisions=None):
        self.decisions = decisions or {}
        self.calls: list[RawItem] = []

    def filter_item(self, raw_item, user_profile, target_career_paths):
        self.calls.append(raw_item)
        decision = self.decisions.get(raw_item.title, "accepted")
        if isinstance(decision, BaseException):
            raise decision
        accepted = decision == "accepted"
        return AIFilterResult(
            raw_item_fingerprint="test-filter-fingerprint",
            title=raw_item.title,
            url=raw_item.url,
            is_relevant=accepted,
            confidence=0.91 if accepted else 0.1,
            reason=str(decision),
            suggested_category=(
                SignalCategory.MARKET_TREND
                if accepted
                else SignalCategory.UNKNOWN
            ),
            matched_career_path_ids=["path-monitoring"],
            action="keep" if accepted else "drop",
        )


class MonitoringVerticalSliceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "phase9b.db"
        initialize_database(self.database_path)
        self.runs = PipelineRunRepository(self.database_path)
        self.executions = SourceExecutionRepository(self.database_path)
        self.source_items = SourceItemRepository(self.database_path)
        self.filters = FilterDecisionRepository(self.database_path)
        self.career_signals = CareerSignalRepository(self.database_path)
        self.feed_adapter = ScriptedAdapter()
        self.website_adapter = ScriptedAdapter()
        self.filter_client = ScriptedFilterClient()
        self.normalizer_calls: list[list[RawItem]] = []
        self.runtime = self.build_runtime()
        self.profile = make_profile()
        self.paths = [make_path()]

    def tearDown(self):
        self.temp_dir.cleanup()

    def build_runtime(
        self,
        *,
        max_candidates_per_source=5,
        filter_client=None,
        normalizer=None,
        ai_filter_executor="default",
        priority_assessor=None,
        interpretation_executor=None,
    ):
        selected_filter_client = filter_client or self.filter_client

        def default_filter_executor(raw_items, profile, paths):
            return execute_ai_filter(
                raw_items,
                profile,
                paths,
                selected_filter_client,
            )

        def recording_normalizer(raw_items, results):
            self.normalizer_calls.append(list(raw_items))
            return normalize_raw_items_to_career_signals(raw_items, results)

        return MonitoringRuntime(
            dispatcher=MonitoringAcquisitionDispatcher(
                feed_adapter=self.feed_adapter,
                selected_website_adapter=self.website_adapter,
            ),
            pipeline_run_repository=self.runs,
            source_execution_repository=self.executions,
            source_item_repository=self.source_items,
            filter_decision_repository=self.filters,
            career_signal_repository=self.career_signals,
            ai_filter_executor=(
                default_filter_executor
                if ai_filter_executor == "default"
                else ai_filter_executor
            ),
            normalizer=normalizer or recording_normalizer,
            execution_mode="live",
            max_candidates_per_source=max_candidates_per_source,
            ai_filter_execution_mode="fake",
            ai_filter_provider="fake-provider",
            ai_filter_model="fake-model",
            priority_assessor=priority_assessor,
            interpretation_executor=interpretation_executor,
        )

    def run_runtime(
        self,
        handoffs,
        *,
        acquisition_only=False,
        user_preferences=None,
    ):
        return self.runtime.run(
            handoffs=handoffs,
            user_profile=self.profile,
            target_career_paths=self.paths,
            user_preferences=(
                {} if user_preferences is None else user_preferences
            ),
            acquisition_only=acquisition_only,
        )

    def count(self, table, where="", params=()):
        connection = open_database_connection(self.database_path)
        try:
            return int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} {where}",
                    params,
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def rows(self, sql, params=()):
        connection = open_database_connection(self.database_path)
        try:
            return [
                dict(row)
                for row in connection.execute(sql, params).fetchall()
            ]
        finally:
            connection.close()


class HandoffLoadingAndDispatchTests(MonitoringVerticalSliceTestCase):
    def test_01_canonical_output_loads_three_typed_handoffs(self):
        handoffs = load_phase7_monitoring_handoffs()
        self.assertEqual(len(handoffs), 3)
        self.assertTrue(all(isinstance(item, Phase7MonitoringHandoff) for item in handoffs))

    def test_02_sap_handoff_uses_verified_feed(self):
        sap = next(item for item in load_phase7_monitoring_handoffs() if "sap.com" in item.source_url)
        self.assertEqual(sap.acquisition_method, AcquisitionMethod.RSS)
        self.assertEqual(sap.provenance["verified_feed_url"], "https://news.sap.com/feed/")

    def test_03_ieee_handoff_is_selected_website(self):
        ieee = next(item for item in load_phase7_monitoring_handoffs() if "ieee-pes.org" in item.source_url)
        self.assertEqual(ieee.acquisition_method, AcquisitionMethod.SELECTED_WEBSITE)
        self.assertIn("selected_website_config", ieee.acquisition_config_ref)

    def test_04_qianzhan_handoff_is_selected_website(self):
        qianzhan = next(item for item in load_phase7_monitoring_handoffs() if "qianzhan.com" in item.source_url)
        self.assertEqual(qianzhan.acquisition_method, AcquisitionMethod.SELECTED_WEBSITE)

    def test_05_loaded_handoffs_include_final_evaluation_id(self):
        handoffs = load_phase7_monitoring_handoffs()
        self.assertTrue(all(item.provenance.get("final_source_evaluation_id") for item in handoffs))

    def test_06_runtime_dispatches_only_resolved_method(self):
        feed = make_handoff("feed")
        selected = make_handoff("site", AcquisitionMethod.SELECTED_WEBSITE)
        self.feed_adapter.set_responses(feed, [])
        self.website_adapter.set_responses(selected, [])
        self.run_runtime([feed, selected])
        self.assertEqual(self.feed_adapter.calls, [feed])
        self.assertEqual(self.website_adapter.calls, [selected])

    def test_07_duplicate_handoff_is_rejected_before_runtime(self):
        handoff = make_handoff("duplicate")
        with self.assertRaises(MonitoringRuntimeCompatibilityError):
            self.run_runtime([handoff, handoff])
        self.assertEqual(self.count("pipeline_runs"), 0)


class MonitoringSourceExecutionTests(MonitoringVerticalSliceTestCase):
    def test_08_each_handoff_creates_one_source_execution(self):
        first = make_handoff("one")
        second = make_handoff("two", AcquisitionMethod.SELECTED_WEBSITE)
        self.run_runtime([first, second])
        self.assertEqual(self.count("source_executions"), 2)

    def test_09_config_execution_has_no_search_plan(self):
        handoff = make_handoff("no-plan")
        self.run_runtime([handoff])
        row = self.rows("SELECT * FROM source_executions")[0]
        self.assertIsNone(row["planning_search_plan_id"])

    def test_10_source_provenance_round_trips(self):
        handoff = make_handoff("provenance")
        result = self.run_runtime([handoff])
        execution = self.executions.list_source_executions(result.run_id)[0]
        self.assertEqual(execution.metadata["source_monitoring_handoff_id"], handoff.phase7_monitoring_handoff_id)
        self.assertEqual(execution.metadata["final_source_evaluation_id"], "evaluation-provenance")

    def test_11_successful_source_completes(self):
        handoff = make_handoff("success")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        result = self.run_runtime([handoff])
        self.assertEqual(result.source_results[0].status, "completed")

    def test_12_failed_source_is_recorded_failed(self):
        handoff = make_handoff("failure")
        self.feed_adapter.set_responses(handoff, TimeoutError("feed timeout"))
        result = self.run_runtime([handoff])
        self.assertEqual(result.source_results[0].status, "failed")
        self.assertEqual(result.source_results[0].error_type, "TimeoutError")

    def test_13_source_failure_does_not_erase_other_discoveries(self):
        failed = make_handoff("failed")
        successful = make_handoff("successful", AcquisitionMethod.SELECTED_WEBSITE)
        self.feed_adapter.set_responses(failed, TimeoutError("timeout"))
        self.website_adapter.set_responses(successful, [make_item("c", source_type=SourceType.SELECTED_WEBSITE, provider="selected_website")])
        result = self.run_runtime([failed, successful])
        self.assertEqual([item.status for item in result.source_results], ["failed", "completed"])
        self.assertEqual(self.count("source_items"), 1)
        self.assertEqual(self.count("source_item_discoveries"), 1)


class MonitoringDedupAndIdentityTests(MonitoringVerticalSliceTestCase):
    def test_14_within_execution_duplicate_is_processed_once(self):
        handoff = make_handoff("dedup")
        self.feed_adapter.set_responses(handoff, [make_item("a"), make_item("a"), make_item("b")])
        self.run_runtime([handoff])
        self.assertEqual(len(self.filter_client.calls), 2)

    def test_15_within_execution_duplicate_has_one_discovery(self):
        handoff = make_handoff("dedup-discovery")
        self.feed_adapter.set_responses(handoff, [make_item("a"), make_item("a")])
        self.run_runtime([handoff])
        self.assertEqual(self.count("source_item_discoveries"), 1)

    def test_16_distinct_canonical_items_remain_distinct(self):
        handoff = make_handoff("distinct")
        self.feed_adapter.set_responses(handoff, [make_item("a"), make_item("b")])
        self.run_runtime([handoff])
        self.assertEqual(self.count("source_items"), 2)

    def test_17_first_observation_reports_new(self):
        handoff = make_handoff("new")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        result = self.run_runtime([handoff])
        self.assertEqual(result.source_results[0].new_source_item_count, 1)
        self.assertEqual(result.source_results[0].existing_source_item_count, 0)

    def test_18_later_observation_reuses_same_source_item(self):
        handoff = make_handoff("reuse")
        self.feed_adapter.set_responses(handoff, [make_item("a")], [make_item("a")])
        first = self.run_runtime([handoff])
        first_id = self.executions.list_discoveries(first.source_results[0].source_execution_id)[0].source_item_id
        second = self.run_runtime([handoff])
        second_id = self.executions.list_discoveries(second.source_results[0].source_execution_id)[0].source_item_id
        self.assertEqual(first_id, second_id)

    def test_19_source_item_count_does_not_grow_on_rediscovery(self):
        handoff = make_handoff("stable-count")
        self.feed_adapter.set_responses(handoff, [make_item("a")], [make_item("a")])
        self.run_runtime([handoff])
        self.run_runtime([handoff])
        self.assertEqual(self.count("source_items"), 1)

    def test_20_seen_count_and_last_seen_remain_compatible(self):
        handoff = make_handoff("seen")
        self.feed_adapter.set_responses(handoff, [make_item("a")], [make_item("a", raw_text="updated")])
        self.run_runtime([handoff])
        first = self.rows("SELECT * FROM source_items")[0]
        self.run_runtime([handoff])
        second = self.rows("SELECT * FROM source_items")[0]
        self.assertEqual(second["seen_count"], 2)
        self.assertEqual(second["first_seen_at"], first["first_seen_at"])
        self.assertGreaterEqual(second["last_seen_at"], first["last_seen_at"])

    def test_21_second_execution_adds_rediscovery_provenance(self):
        handoff = make_handoff("rediscovery")
        self.feed_adapter.set_responses(handoff, [make_item("a")], [make_item("a")])
        self.run_runtime([handoff])
        self.run_runtime([handoff])
        self.assertEqual(self.count("source_item_discoveries"), 2)


class HistoricalDuplicateFilteringTests(MonitoringVerticalSliceTestCase):
    def setUp(self):
        super().setUp()
        self.handoff = make_handoff("history")
        self.feed_adapter.set_responses(self.handoff, [make_item("a")], [make_item("a")])
        self.first = self.run_runtime([self.handoff])
        self.filter_call_count = len(self.filter_client.calls)
        self.second = self.run_runtime([self.handoff])

    def test_22_existing_item_is_deferred_historical_duplicate(self):
        status = self.filters.list_run_filter_statuses(self.second.run_id)[0]
        self.assertEqual(status.status, "deferred")
        self.assertEqual(status.deferred_reason, HISTORICAL_DUPLICATE_REASON)

    def test_23_historical_item_creates_no_filter_execution(self):
        self.assertEqual(self.filters.list_filter_executions(self.second.run_id), [])

    def test_24_historical_item_creates_no_filter_decision(self):
        self.assertEqual(self.filters.list_filter_decisions_for_run(self.second.run_id), [])

    def test_25_historical_item_causes_zero_repeated_llm_calls(self):
        self.assertEqual(len(self.filter_client.calls), self.filter_call_count)

    def test_26_historical_item_is_not_rejected(self):
        status = self.filters.list_run_filter_statuses(self.second.run_id)[0]
        self.assertNotEqual(status.status, "rejected")

    def test_27_historical_run_completes_accounting(self):
        self.assertEqual(self.runs.get_run(self.second.run_id).status, "completed")

    def test_28_historical_rediscovery_does_not_run_normalizer(self):
        self.assertEqual(self.normalizer_calls[-1], [])


class MonitoringInterpretationIntegrationTests(MonitoringVerticalSliceTestCase):
    def _configure_interpretation_runtime(self, executor):
        def intelligence_normalizer(raw_items, results):
            self.normalizer_calls.append(list(raw_items))
            signals = normalize_raw_items_to_career_signals(raw_items, results)
            for signal in signals:
                signal.category = SignalCategory.NEWS
            return signals

        def priority_assessor(**kwargs):
            return PriorityIntegrationBatchResult(
                scored_career_signals=tuple(
                    make_monitoring_scored_signal(signal, 91.0)
                    for signal in kwargs["career_signals"]
                )
            )

        self.runtime = self.build_runtime(
            normalizer=intelligence_normalizer,
            priority_assessor=priority_assessor,
            interpretation_executor=executor,
        )

    def test_monitoring_interprets_one_current_intelligence_batch(self):
        handoff = make_handoff("stage4c-batch")
        self.feed_adapter.set_responses(
            handoff,
            [make_item("a"), make_item("b")],
        )
        preferences = {"soft_preferences": ["mission-driven work"]}
        executor = RecordingInterpretationExecutor()
        self._configure_interpretation_runtime(executor)

        result = self.run_runtime(
            [handoff],
            user_preferences=preferences,
        )

        self.assertEqual(len(result.career_signal_routing.intelligence), 2)
        self.assertEqual(len(executor.contexts), 1)
        context = executor.contexts[0]
        self.assertEqual(
            tuple(item.career_signal.signal_id for item in context.intelligence_signals),
            tuple(
                item.career_signal.signal_id
                for item in result.career_signal_routing.intelligence
            ),
        )
        self.assertIs(context.user_preferences, preferences)
        self.assertIs(context.target_career_paths[0], self.paths[0])
        self.assertFalse(hasattr(context, "user_profile"))
        self.assertEqual(
            result.career_intelligence_interpretation.input_signal_ids,
            tuple(
                item.career_signal.signal_id
                for item in result.career_signal_routing.intelligence
            ),
        )
        self.assertEqual(result.career_intelligence_brief.opportunities, ())
        self.assertEqual(
            result.career_intelligence_brief.key_developments,
            result.career_intelligence_interpretation.key_developments,
        )

    def test_historical_duplicate_adds_zero_interpretation_calls(self):
        handoff = make_handoff("stage4c-history")
        item = make_item("a")
        self.feed_adapter.set_responses(handoff, [item], [item])
        executor = RecordingInterpretationExecutor()
        self._configure_interpretation_runtime(executor)

        first = self.run_runtime([handoff])
        first_call_count = len(executor.contexts)
        second = self.run_runtime([handoff])

        self.assertEqual(first_call_count, 1)
        self.assertEqual(len(executor.contexts), first_call_count)
        self.assertEqual(second.career_signals, ())
        self.assertEqual(second.career_signal_routing.intelligence, ())
        self.assertEqual(
            second.career_intelligence_interpretation.warnings,
            (EMPTY_INPUT_WARNING,),
        )
        self.assertEqual(len(first.career_signal_routing.intelligence), 1)

    def test_mixed_historical_and_new_interprets_only_new_signal(self):
        handoff = make_handoff("stage4c-mixed")
        old_item = make_item("old")
        new_item = make_item("new")
        self.feed_adapter.set_responses(
            handoff,
            [old_item],
            [old_item, new_item],
        )
        executor = RecordingInterpretationExecutor()
        self._configure_interpretation_runtime(executor)

        self.run_runtime([handoff])
        second = self.run_runtime([handoff])

        self.assertEqual(len(executor.contexts), 2)
        self.assertEqual(len(second.career_signals), 1)
        self.assertEqual(
            tuple(
                item.career_signal.signal_id
                for item in executor.contexts[-1].intelligence_signals
            ),
            (second.career_signals[0].signal_id,),
        )

    def test_interpretation_error_fails_monitoring_at_explicit_stage(self):
        handoff = make_handoff("stage4c-failure")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        error = CareerIntelligenceInterpretationError("Malformed live response.")
        executor = RecordingInterpretationExecutor(error=error)
        self._configure_interpretation_runtime(executor)

        with self.assertRaisesRegex(
            Exception,
            "career_intelligence_interpretation",
        ) as context:
            self.run_runtime([handoff])

        self.assertIs(context.exception.__cause__, error)
        run = self.runs.list_recent_runs(limit=1)[0]
        self.assertEqual(run.status, "failed")
        self.assertEqual(
            run.failure_stage,
            "career_intelligence_interpretation",
        )

    def test_brief_error_fails_monitoring_at_explicit_stage(self):
        handoff = make_handoff("stage5b-failure")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        executor = RecordingInterpretationExecutor()
        self._configure_interpretation_runtime(executor)
        error = CareerIntelligenceBriefError("Synthetic assembly failure.")

        with patch(
            "src.monitoring_runtime.build_career_intelligence_brief",
            side_effect=error,
        ):
            with self.assertRaisesRegex(Exception, "career_intelligence_brief") as context:
                self.run_runtime([handoff])

        self.assertIs(context.exception.__cause__, error)
        run = self.runs.list_recent_runs(limit=1)[0]
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.failure_stage, "career_intelligence_brief")

class NewItemFilterAndCareerSignalTests(MonitoringVerticalSliceTestCase):
    def test_29_new_item_is_filter_eligible(self):
        handoff = make_handoff("eligible")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        result = self.run_runtime([handoff])
        self.assertEqual(result.summary["monitoring_filter_eligible_count"], 1)

    def test_30_accepted_new_item_has_filter_provenance(self):
        handoff = make_handoff("accepted")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        result = self.run_runtime([handoff])
        self.assertEqual(len(self.filters.list_filter_executions(result.run_id)), 1)
        self.assertEqual(self.filters.list_filter_decisions_for_run(result.run_id)[0].decision, "accepted")

    def test_31_rejected_new_item_has_rejected_decision(self):
        handoff = make_handoff("rejected")
        item = make_item("a")
        self.feed_adapter.set_responses(handoff, [item])
        self.filter_client.decisions[item.title] = "rejected"
        result = self.run_runtime([handoff])
        self.assertEqual(self.filters.list_filter_decisions_for_run(result.run_id)[0].decision, "rejected")

    def test_32_filter_failure_remains_final_and_visible(self):
        handoff = make_handoff("filter-failure")
        item = make_item("a")
        self.feed_adapter.set_responses(handoff, [item])
        self.filter_client.decisions[item.title] = RuntimeError("controlled filter failure")
        result = self.run_runtime([handoff])
        execution = self.filters.list_filter_executions(result.run_id)[0]
        status = self.filters.list_run_filter_statuses(result.run_id)[0]
        self.assertEqual(execution.status, "failed")
        self.assertEqual(status.status, "failed")
        self.assertEqual(
            self.filters.get_run_filter_coverage(result.run_id).deferred,
            0,
        )

    def test_33_only_accepted_items_reach_normalizer(self):
        handoff = make_handoff("normalizer")
        accepted = make_item("a")
        rejected = make_item("b")
        self.feed_adapter.set_responses(handoff, [accepted, rejected])
        self.filter_client.decisions[rejected.title] = "rejected"
        self.run_runtime([handoff])
        self.assertEqual(self.normalizer_calls[-1], [accepted])

    def test_34_accepted_new_item_persists_career_signal(self):
        handoff = make_handoff("career")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        result = self.run_runtime([handoff])
        self.assertEqual(self.career_signals.count(), 1)
        self.assertEqual(len(result.career_signals), 1)

    def test_34b_monitoring_scored_signal_uses_shared_routing(self):
        handoff = make_handoff("priority-routing")
        self.feed_adapter.set_responses(handoff, [make_item("a")])

        def priority_assessor(**kwargs):
            return PriorityIntegrationBatchResult(
                scored_career_signals=tuple(
                    make_monitoring_scored_signal(signal, 91.0)
                    for signal in kwargs["career_signals"]
                )
            )

        self.runtime = self.build_runtime(priority_assessor=priority_assessor)
        result = self.run_runtime([handoff])

        self.assertEqual(result.career_signals[0].category, SignalCategory.UNKNOWN)
        self.assertEqual(result.career_signal_routing.opportunities, ())
        self.assertEqual(result.career_signal_routing.intelligence, ())
        self.assertEqual(
            [
                item.scored_career_signal.career_signal.signal_id
                for item in result.career_signal_routing.unrouted
            ],
            [result.career_signals[0].signal_id],
        )

    def test_35_rediscovery_does_not_duplicate_career_signal(self):
        handoff = make_handoff("career-repeat")
        self.feed_adapter.set_responses(handoff, [make_item("a")], [make_item("a")])
        self.run_runtime([handoff])
        self.run_runtime([handoff])
        self.assertEqual(self.career_signals.count(), 1)

    def test_36_career_signal_materialization_validation_is_complete(self):
        handoff = make_handoff("career-coverage")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        result = self.run_runtime([handoff])
        coverage = self.filters.get_run_career_signal_materialization(result.run_id)
        self.assertEqual(coverage.accepted_without_career_signal, 0)
        self.assertEqual(coverage.accepted_with_career_signal, 1)

    def _assert_all_new_items_filtered(self, count):
        handoffs = []
        for batch_start in range(0, count, 5):
            handoff = make_handoff(f"filter-all-{count}-{batch_start}")
            handoffs.append(handoff)
            self.feed_adapter.set_responses(
                handoff,
                [
                    make_item(f"item-{index}")
                    for index in range(batch_start, min(batch_start + 5, count))
                ],
            )
        result = self.run_runtime(handoffs)
        coverage = self.filters.get_run_filter_coverage(result.run_id)
        self.assertEqual(coverage.filter_execution_count, count)
        self.assertEqual(coverage.filter_decision_count, count)
        self.assertEqual(coverage.deferred, 0)

    def test_37_three_new_items_all_reach_ai_filter(self):
        self._assert_all_new_items_filtered(3)

    def test_37b_ten_new_items_all_reach_ai_filter(self):
        self._assert_all_new_items_filtered(10)

    def test_37c_thirty_new_items_all_reach_ai_filter(self):
        self._assert_all_new_items_filtered(30)

    def test_37d_thirty_five_new_items_all_reach_ai_filter(self):
        self._assert_all_new_items_filtered(35)

    def test_37e_mixed_new_and_historical_filters_every_new_item(self):
        historical = [make_item(f"historical-{index}") for index in range(10)]
        new = [make_item(f"new-{index}") for index in range(20)]
        handoffs = [make_handoff(f"filter-mixed-{index}") for index in range(6)]
        for index, handoff in enumerate(handoffs):
            if index < 2:
                batch = historical[index * 5 : (index + 1) * 5]
                self.feed_adapter.set_responses(handoff, batch, batch)
            else:
                batch_start = (index - 2) * 5
                self.feed_adapter.set_responses(
                    handoff,
                    [],
                    new[batch_start : batch_start + 5],
                )
        self.run_runtime(handoffs)
        result = self.run_runtime(handoffs)
        coverage = self.filters.get_run_filter_coverage(result.run_id)
        self.assertEqual(coverage.filter_execution_count, 20)
        self.assertEqual(coverage.filter_decision_count, 20)
        self.assertEqual(coverage.deferred, 10)
        self.assertEqual(
            {
                status.deferred_reason
                for status in self.filters.list_run_filter_statuses(result.run_id)
                if status.status == "deferred"
            },
            {HISTORICAL_DUPLICATE_REASON},
        )

    def test_38_completed_run_has_no_pending_filter_items(self):
        handoff = make_handoff("final-filter")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        result = self.run_runtime([handoff])
        coverage = self.filters.get_run_filter_coverage(result.run_id)
        self.assertEqual((coverage.pending, coverage.running), (0, 0))


class MonitoringMaterializationTests(MonitoringVerticalSliceTestCase):
    def test_39_selected_adapter_uses_existing_surface_client(self):
        handoff = make_handoff("site", AcquisitionMethod.SELECTED_WEBSITE)
        client = Mock()
        client.fetch_website.return_value = [make_item("a", source_type=SourceType.SELECTED_WEBSITE, provider="selected_website")]
        result = SelectedWebsiteMonitoringAdapter(client)(handoff)
        client.fetch_website.assert_called_once()
        self.assertEqual(result[0].url, "https://example.com/articles/a")

    def test_40_historical_selected_item_has_no_detail_fetch(self):
        handoff = make_handoff("site-history", AcquisitionMethod.SELECTED_WEBSITE)
        item = make_item("a", source_type=SourceType.SELECTED_WEBSITE, provider="selected_website")
        self.website_adapter.set_responses(handoff, [item], [item])
        with patch("requests.get", side_effect=AssertionError("detail fetch")):
            self.run_runtime([handoff])
            self.run_runtime([handoff])
        self.assertEqual(len(self.filter_client.calls), 1)

    def test_41_sufficient_new_selected_item_persists_directly(self):
        handoff = make_handoff("site-direct", AcquisitionMethod.SELECTED_WEBSITE)
        item = make_item("a", source_type=SourceType.SELECTED_WEBSITE, provider="selected_website")
        self.website_adapter.set_responses(handoff, [item])
        self.run_runtime([handoff])
        self.assertEqual(self.rows("SELECT raw_text FROM source_items")[0]["raw_text"], item.raw_text)

    def test_42_selected_surface_discovery_is_bounded(self):
        handoff = make_handoff("bounded", AcquisitionMethod.SELECTED_WEBSITE)
        response = Mock(text="<a href='/a'>A</a><a href='/b'>B</a><a href='/c'>C</a>")
        response.raise_for_status.return_value = None
        client = SelectedWebsiteClient(dry_run=False, max_items_per_site=2)
        with patch("src.selected_website_client.requests.get", return_value=response):
            items = SelectedWebsiteMonitoringAdapter(client)(handoff)
        self.assertEqual(len(items), 2)

    def test_42b_runtime_enforces_the_declared_candidate_bound(self):
        handoff = make_handoff("runtime-bound")
        self.runtime = self.build_runtime(max_candidates_per_source=2)
        self.feed_adapter.set_responses(
            handoff,
            [make_item("a"), make_item("b"), make_item("c")],
        )

        result = self.run_runtime([handoff])

        self.assertEqual(len(result.observed_raw_items), 2)
        self.assertEqual(self.source_items.count(), 2)
        execution = self.executions.list_source_executions(
            result.run_id
        )[0]
        self.assertEqual(execution.requested_result_limit, 2)
        self.assertEqual(execution.returned_item_count, 2)

    def test_43_sufficient_rss_entry_uses_feed_content(self):
        handoff = make_handoff("feed-content")
        client = Mock()
        item = make_item("a", raw_text="Feed summary")
        client.fetch_feed.return_value = [item]
        result = FeedMonitoringAdapter(client)(handoff)
        self.assertEqual(result[0].raw_text, "Feed summary")
        client.fetch_feed.assert_called_once()

    def test_44_historical_rss_item_has_no_detail_refetch(self):
        handoff = make_handoff("rss-history")
        item = make_item("a")
        self.feed_adapter.set_responses(handoff, [item], [item])
        with patch("requests.get", side_effect=AssertionError("detail fetch")):
            self.run_runtime([handoff])
            self.run_runtime([handoff])
        self.assertEqual(len(self.filter_client.calls), 1)

    def test_45_feed_guid_does_not_override_canonical_url(self):
        first = make_item("a", guid="first-guid")
        second = make_item("a", guid="second-guid")
        self.assertEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_46_cross_method_url_reuses_one_source_item(self):
        feed = make_handoff("cross-feed")
        site = make_handoff("cross-site", AcquisitionMethod.SELECTED_WEBSITE)
        url = "https://example.com/articles/shared"
        self.feed_adapter.set_responses(feed, [make_item("feed", url=url)])
        self.website_adapter.set_responses(site, [make_item("site", source_type=SourceType.SELECTED_WEBSITE, provider="selected_website", url=url)])
        self.run_runtime([feed])
        self.run_runtime([site])
        self.assertEqual(self.count("source_items"), 1)


class MonitoringAccountingCompatibilityTests(MonitoringVerticalSliceTestCase):
    def test_47_all_observed_items_have_filter_ledger_rows(self):
        handoff = make_handoff("ledger")
        self.feed_adapter.set_responses(handoff, [make_item("a"), make_item("b")])
        result = self.run_runtime([handoff])
        self.assertEqual(len(self.filters.list_run_filter_statuses(result.run_id)), 2)

    def test_48_source_failure_is_visible_in_summary(self):
        handoff = make_handoff("summary-failure")
        self.feed_adapter.set_responses(handoff, TimeoutError("timeout"))
        result = self.run_runtime([handoff])
        self.assertEqual(result.summary["monitoring_source_failure_count"], 1)

    def test_49_phase7_execution_accounting_has_no_running_rows(self):
        handoff = make_handoff("execution-accounting")
        self.run_runtime([handoff])
        result = self.executions.list_source_executions(self.rows("SELECT run_id FROM pipeline_runs")[0]["run_id"])
        self.assertTrue(all(item.status in {"completed", "failed"} for item in result))

    def test_50_phase8_filter_accounting_remains_complete(self):
        handoff = make_handoff("filter-accounting")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        result = self.run_runtime([handoff])
        coverage = self.filters.assert_run_filter_accounting_complete(result.run_id)
        self.assertEqual(coverage.missing_unregistered, 0)

    def test_51_non_monitoring_source_identity_is_unchanged(self):
        first = make_item("a", source_type=SourceType.SEARCH_API, provider="brave")
        second = make_item("a", source_type=SourceType.SEARCH_API, provider="other")
        self.assertNotEqual(fingerprint_raw_item(first), fingerprint_raw_item(second))

    def test_52_planning_bundle_tables_are_not_used(self):
        handoff = make_handoff("no-planning")
        self.run_runtime([handoff])
        self.assertEqual(self.count("planning_bundles"), 0)

    def test_53_pipeline_run_output_contract_is_unchanged(self):
        output = PipelineRunOutput("v1", "test", "now", PipelineSummary(0))
        self.assertNotIn("monitoring", output.to_dict())
        self.assertNotIn("monitoring", {item.name for item in fields(PipelineRunOutput)})

    def test_54_no_migration_008_or_monitoring_tables_exist(self):
        self.assertEqual(discover_migrations()[-1].version, "007")
        tables = {item["name"] for item in self.rows("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse(any(name.startswith("monitoring_") for name in tables))

    def test_55_fake_runtime_uses_no_live_network_or_llm(self):
        handoff = make_handoff("offline")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        with patch("requests.get", side_effect=AssertionError("network")):
            result = self.run_runtime([handoff])
        self.assertEqual(result.status, "completed")

    def test_56_tests_do_not_touch_development_database(self):
        configured = Path(DEFAULT_DATABASE_FILE)
        before = (configured.exists(), configured.stat().st_size if configured.exists() else None, configured.stat().st_mtime_ns if configured.exists() else None)
        handoff = make_handoff("temp-only")
        self.run_runtime([handoff])
        after = (configured.exists(), configured.stat().st_size if configured.exists() else None, configured.stat().st_mtime_ns if configured.exists() else None)
        self.assertEqual(after, before)

    def test_57_acquisition_only_stops_with_new_items_pending(self):
        runtime = self.build_runtime(ai_filter_executor=None)
        self.runtime = runtime
        handoff = make_handoff("acquisition-only")
        self.feed_adapter.set_responses(handoff, [make_item("a")])
        result = self.run_runtime([handoff], acquisition_only=True)
        self.assertEqual(result.status, "filter_pending")
        self.assertEqual(self.filters.list_run_filter_statuses(result.run_id)[0].status, "pending")


class MockedTwoRunVerticalSliceTests(MonitoringVerticalSliceTestCase):
    def test_58_two_runs_filter_only_new_items_from_fresh_responses(self):
        handoff_a = make_handoff("source-a")
        handoff_b = make_handoff("source-b", AcquisitionMethod.SELECTED_WEBSITE)
        self.feed_adapter.set_responses(
            handoff_a,
            [make_item("a"), make_item("b")],
            [make_item("a"), make_item("b"), make_item("d")],
        )
        self.website_adapter.set_responses(
            handoff_b,
            [make_item("c", source_type=SourceType.SELECTED_WEBSITE, provider="selected_website")],
            [make_item("c", source_type=SourceType.SELECTED_WEBSITE, provider="selected_website")],
        )

        first = self.run_runtime([handoff_a, handoff_b])
        first_filter_calls = len(self.filter_client.calls)
        second = self.run_runtime([handoff_a, handoff_b])

        self.assertEqual(first_filter_calls, 3)
        self.assertEqual(len(self.filter_client.calls), 4)
        self.assertEqual(self.source_items.count(), 4)
        self.assertEqual(self.count("source_item_discoveries"), 7)
        self.assertEqual(len(self.filters.list_filter_executions(second.run_id)), 1)
        self.assertEqual(len(self.filters.list_filter_decisions_for_run(second.run_id)), 1)
        statuses = self.filters.list_run_filter_statuses(second.run_id)
        self.assertEqual([item.status for item in statuses], ["deferred", "deferred", "deferred", "accepted"])
        self.assertEqual(self.career_signals.count(), 4)
        self.assertEqual(self.feed_adapter.call_counts[handoff_a.phase7_monitoring_handoff_id], 2)
        self.assertEqual(self.website_adapter.call_counts[handoff_b.phase7_monitoring_handoff_id], 2)
        self.assertNotEqual(first.run_id, second.run_id)


if __name__ == "__main__":
    unittest.main()
