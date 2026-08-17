from contextlib import redirect_stdout
from dataclasses import dataclass
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.config import DEFAULT_DATABASE_FILE
from src.database.migrations import initialize_database
from src.database.repositories.career_signal_repository import (
    CareerSignalRepository,
)
from src.database.repositories.source_item_repository import SourceItemRepository
from src.database.source_identity import fingerprint_raw_item
from src.models import (
    AIFilterExecutionReport,
    AIFilterResult,
    CareerPathCategory,
    CareerSignal,
    RawItem,
    RawItemFilterStatus,
    SearchAPIExecutionReport,
    SearchPlan,
    SearchQueryType,
    SearchScope,
    SignalCategory,
    SourceType,
    TargetCareerPath,
    UserProfile,
)
from src.normalizer import (
    _fingerprint_raw_item as normalizer_raw_item_fingerprint,
    normalize_raw_items_to_career_signals,
)
from src.pipeline import MockPipeline
from src.signal_identity import build_signal_id
from src.storage import convert_to_json_ready, save_json


@dataclass
class FakeUpsertSummary:
    received_count: int
    unique_count: int
    inserted_count: int
    updated_count: int


class RecordingSourceItemRepository:
    def __init__(self, *, missing_fingerprints=None, row_overrides=None):
        self.upsert_calls = []
        self.get_calls = []
        self.rows_by_fingerprint = {}
        self.missing_fingerprints = set(missing_fingerprints or [])
        self.row_overrides = row_overrides or {}
        self.next_source_item_id = 1

    def upsert_many(self, raw_items):
        batch = list(raw_items)
        self.upsert_calls.append(batch)

        for raw_item in batch:
            fingerprint = fingerprint_raw_item(raw_item)

            if fingerprint in self.row_overrides:
                self.rows_by_fingerprint[fingerprint] = self.row_overrides[fingerprint]
                continue

            if fingerprint not in self.rows_by_fingerprint:
                self.rows_by_fingerprint[fingerprint] = {
                    "source_item_id": self.next_source_item_id,
                    "fingerprint": fingerprint,
                }
                self.next_source_item_id += 1

        return FakeUpsertSummary(
            received_count=len(batch),
            unique_count=len({fingerprint_raw_item(item) for item in batch}),
            inserted_count=len(batch),
            updated_count=0,
        )

    def get_by_fingerprint(self, fingerprint):
        self.get_calls.append(fingerprint)

        if fingerprint in self.missing_fingerprints:
            return None

        return self.rows_by_fingerprint.get(fingerprint)


class RecordingCareerSignalRepository:
    def __init__(self, *, error: Exception | None = None):
        self.upsert_calls = []
        self.error = error

    def upsert_many(self, records):
        if self.error is not None:
            raise self.error

        batch = list(records)
        self.upsert_calls.append(batch)

        return FakeUpsertSummary(
            received_count=len(batch),
            unique_count=len({record.career_signal.signal_id for record in batch}),
            inserted_count=len(batch),
            updated_count=0,
        )


def build_raw_item(
    *,
    source_type: SourceType = SourceType.SEARCH_API,
    title: str = "External strategy role",
    url: str = "https://example.com/external",
    provider: str = "brave",
    mode: str | None = None,
    raw_text: str | None = None,
) -> RawItem:
    metadata = {
        "provider": provider,
    }

    if mode is not None:
        metadata["mode"] = mode

    return RawItem(
        source_type=source_type,
        title=title,
        organization="Example Co",
        url=url,
        published_at=None,
        raw_text=raw_text or title,
        metadata=metadata,
    )


def build_user_profile() -> UserProfile:
    return UserProfile(
        profile_id="profile_1",
        name="Test User",
        background_summary="Strategy analyst interested in AI.",
    )


def build_career_path() -> TargetCareerPath:
    return TargetCareerPath(
        path_id="path_1",
        title="AI Strategy",
        category=CareerPathCategory.AI_STRATEGY,
        description="AI strategy roles.",
        fit_score=90,
    )


def build_search_plan() -> SearchPlan:
    return SearchPlan(
        plan_id="plan_1",
        query_id="query_1",
        query_text="strategy analyst",
        query_type=SearchQueryType.JOB_SEARCH,
        career_path_id="path_1",
        career_path_title="AI Strategy",
        scope_id="scope_1",
        source_types=[
            SourceType.SEARCH_API,
            SourceType.RSS,
            SourceType.SELECTED_WEBSITE,
        ],
    )


def build_ai_filter_executor(events=None, accepted_raw_items=None):
    events = [] if events is None else events

    def ai_filter_executor(raw_items, user_profile, career_paths):
        events.append("ai_filter")
        accepted_ids = {
            id(raw_item)
            for raw_item in (accepted_raw_items if accepted_raw_items is not None else raw_items)
        }
        filtered_raw_items = []
        ai_filter_results = []
        raw_item_statuses = []

        for index, raw_item in enumerate(raw_items):
            is_relevant = id(raw_item) in accepted_ids
            fingerprint = normalizer_raw_item_fingerprint(raw_item)
            ai_filter_results.append(
                AIFilterResult(
                    raw_item_fingerprint=fingerprint,
                    title=raw_item.title,
                    url=raw_item.url,
                    is_relevant=is_relevant,
                    confidence=0.9 if is_relevant else 0.2,
                    reason="accepted" if is_relevant else "rejected",
                    suggested_category=(
                        SignalCategory.JOB if is_relevant else SignalCategory.UNKNOWN
                    ),
                )
            )

            if is_relevant:
                filtered_raw_items.append(raw_item)

            raw_item_statuses.append(
                RawItemFilterStatus(
                    raw_item_fingerprint=fingerprint,
                    raw_item_index=index,
                    source_type=raw_item.source_type,
                    title=raw_item.title,
                    url=raw_item.url,
                    status=(
                        "processed_accepted"
                        if is_relevant
                        else "processed_rejected"
                    ),
                    reason="test",
                    is_relevant=is_relevant,
                )
            )

        return AIFilterExecutionReport(
            filtered_raw_items=filtered_raw_items,
            ai_filter_results=ai_filter_results,
            raw_item_statuses=raw_item_statuses,
            executed_count=len(raw_items),
        )

    return ai_filter_executor


def build_pipeline(
    *,
    source_repository=None,
    career_repository=None,
    mock_raw_items=None,
    search_api_raw_items=None,
    rss_raw_items=None,
    selected_website_raw_items=None,
    accepted_raw_items=None,
    events=None,
    normalizer=None,
) -> MockPipeline:
    mock_raw_items = [] if mock_raw_items is None else mock_raw_items
    search_api_raw_items = (
        [] if search_api_raw_items is None else search_api_raw_items
    )
    rss_raw_items = [] if rss_raw_items is None else rss_raw_items
    selected_website_raw_items = (
        [] if selected_website_raw_items is None else selected_website_raw_items
    )
    events = [] if events is None else events

    def wrapped_normalizer(raw_items, ai_results):
        events.append("normalizer")

        if normalizer is not None:
            return normalizer(raw_items, ai_results)

        return normalize_raw_items_to_career_signals(raw_items, ai_results)

    return MockPipeline(
        raw_item_loader=lambda: mock_raw_items,
        user_profile_loader=build_user_profile,
        search_scope_loader=lambda: SearchScope(
            scope_id="scope_1",
            name="Test scope",
            source_types=[
                SourceType.SEARCH_API,
                SourceType.RSS,
                SourceType.SELECTED_WEBSITE,
            ],
        ),
        career_path_generator=lambda user_profile: [build_career_path()],
        search_query_generator=lambda career_paths: [],
        search_plan_builder=lambda search_queries, search_scope: [build_search_plan()],
        search_api_executor=lambda search_plans: SearchAPIExecutionReport(
            raw_items=search_api_raw_items,
            executed_plan_count=1 if search_api_raw_items else 0,
        ),
        rss_executor=lambda search_scope, search_plans: (
            rss_raw_items,
            1 if rss_raw_items else 0,
        ),
        selected_website_executor=lambda search_scope, search_plans: (
            selected_website_raw_items,
            1 if selected_website_raw_items else 0,
        ),
        ai_filter_executor=build_ai_filter_executor(
            events=events,
            accepted_raw_items=accepted_raw_items,
        ),
        normalizer=wrapped_normalizer,
        source_item_repository=source_repository,
        career_signal_repository=career_repository,
    )


class CareerSignalPipelineDependencyTests(unittest.TestCase):
    def test_mock_pipeline_accepts_optional_career_signal_repository(self):
        career_repository = RecordingCareerSignalRepository()

        pipeline = build_pipeline(career_repository=career_repository)

        self.assertIs(pipeline.career_signal_repository, career_repository)

    def test_no_repositories_preserves_old_behavior(self):
        output = build_pipeline(
            search_api_raw_items=[build_raw_item()],
        ).run()

        self.assertEqual(output.summary.total_career_signals, 1)

    def test_source_item_repository_only_preserves_phase3_behavior(self):
        source_repository = RecordingSourceItemRepository()
        raw_item = build_raw_item()

        build_pipeline(
            source_repository=source_repository,
            search_api_raw_items=[raw_item],
        ).run()

        self.assertEqual(source_repository.upsert_calls, [[raw_item]])

    def test_both_repositories_enable_full_persistence(self):
        source_repository = RecordingSourceItemRepository()
        career_repository = RecordingCareerSignalRepository()
        raw_item = build_raw_item()

        build_pipeline(
            source_repository=source_repository,
            career_repository=career_repository,
            search_api_raw_items=[raw_item],
        ).run()

        self.assertEqual(len(career_repository.upsert_calls), 1)

    def test_career_repository_without_source_repository_raises_configuration_error(self):
        with self.assertRaisesRegex(RuntimeError, "requires SourceItemRepository"):
            build_pipeline(
                career_repository=RecordingCareerSignalRepository(),
                search_api_raw_items=[build_raw_item()],
            ).run()


class CareerSignalPipelineEligibilityTests(unittest.TestCase):
    def test_accepted_search_api_signal_is_persisted(self):
        career_repository = RecordingCareerSignalRepository()

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=career_repository,
            search_api_raw_items=[build_raw_item(source_type=SourceType.SEARCH_API)],
        ).run()

        self.assertEqual(len(career_repository.upsert_calls[0]), 1)

    def test_accepted_rss_signal_is_persisted(self):
        career_repository = RecordingCareerSignalRepository()

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=career_repository,
            rss_raw_items=[
                build_raw_item(
                    source_type=SourceType.RSS,
                    provider="rss",
                    url="https://example.com/rss",
                )
            ],
        ).run()

        self.assertEqual(career_repository.upsert_calls[0][0].career_signal.source_type, SourceType.RSS)

    def test_accepted_selected_website_signal_is_persisted(self):
        career_repository = RecordingCareerSignalRepository()

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=career_repository,
            selected_website_raw_items=[
                build_raw_item(
                    source_type=SourceType.SELECTED_WEBSITE,
                    provider="selected_website",
                    url="https://example.com/site",
                )
            ],
        ).run()

        self.assertEqual(
            career_repository.upsert_calls[0][0].career_signal.source_type,
            SourceType.SELECTED_WEBSITE,
        )

    def test_rejected_external_raw_item_stays_in_source_items_but_creates_no_signal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "rejected.db"
            initialize_database(database_path=database_path)
            source_repository = SourceItemRepository(database_path=database_path)
            career_repository = CareerSignalRepository(database_path=database_path)
            raw_item = build_raw_item()

            build_pipeline(
                source_repository=source_repository,
                career_repository=career_repository,
                search_api_raw_items=[raw_item],
                accepted_raw_items=[],
            ).run()

            self.assertEqual(source_repository.count(), 1)
            self.assertEqual(career_repository.count(), 0)

    def test_mock_derived_signal_is_not_persisted(self):
        career_repository = RecordingCareerSignalRepository()
        mock_item = build_raw_item(
            source_type=SourceType.MOCK_JOB,
            provider="mock",
            url="https://example.com/mock",
        )

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=career_repository,
            mock_raw_items=[mock_item],
        ).run()

        self.assertEqual(career_repository.upsert_calls, [])

    def test_dry_run_derived_signal_is_not_persisted(self):
        career_repository = RecordingCareerSignalRepository()
        dry_run_item = build_raw_item(mode="dry_run")

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=career_repository,
            search_api_raw_items=[dry_run_item],
        ).run()

        self.assertEqual(career_repository.upsert_calls, [])

    def test_empty_eligible_signal_batch_does_not_call_career_repository(self):
        career_repository = RecordingCareerSignalRepository()

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=career_repository,
        ).run()

        self.assertEqual(career_repository.upsert_calls, [])


class CareerSignalPipelineSourceLinkageTests(unittest.TestCase):
    def test_persisted_signal_has_correct_non_null_source_item_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "linked.db"
            initialize_database(database_path=database_path)
            source_repository = SourceItemRepository(database_path=database_path)
            career_repository = CareerSignalRepository(database_path=database_path)
            raw_item = build_raw_item()

            build_pipeline(
                source_repository=source_repository,
                career_repository=career_repository,
                search_api_raw_items=[raw_item],
            ).run()

            source_row = source_repository.get_by_fingerprint(
                fingerprint_raw_item(raw_item)
            )
            signal_row = career_repository.get_by_signal_id(build_signal_id(raw_item))

            self.assertEqual(signal_row["source_item_id"], source_row["source_item_id"])

    def test_source_item_id_points_to_corresponding_raw_item_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "corresponding.db"
            initialize_database(database_path=database_path)
            source_repository = SourceItemRepository(database_path=database_path)
            career_repository = CareerSignalRepository(database_path=database_path)
            first = build_raw_item(url="https://example.com/first")
            second = build_raw_item(url="https://example.com/second")

            build_pipeline(
                source_repository=source_repository,
                career_repository=career_repository,
                search_api_raw_items=[first, second],
            ).run()

            first_signal = career_repository.get_by_signal_id(build_signal_id(first))
            second_signal = career_repository.get_by_signal_id(build_signal_id(second))

            self.assertNotEqual(first_signal["source_item_id"], second_signal["source_item_id"])

    def test_matching_does_not_depend_only_on_title(self):
        source_repository = RecordingSourceItemRepository()
        career_repository = RecordingCareerSignalRepository()
        first = build_raw_item(title="Same title", url="https://example.com/one")
        second = build_raw_item(title="Same title", url="https://example.com/two")

        build_pipeline(
            source_repository=source_repository,
            career_repository=career_repository,
            search_api_raw_items=[first, second],
        ).run()

        source_ids = {
            record.career_signal.signal_id: record.source_item_id
            for record in career_repository.upsert_calls[0]
        }

        self.assertNotEqual(
            source_ids[build_signal_id(first)],
            source_ids[build_signal_id(second)],
        )

    def test_matching_does_not_depend_only_on_url(self):
        source_repository = RecordingSourceItemRepository()
        career_repository = RecordingCareerSignalRepository()
        search_item = build_raw_item(
            source_type=SourceType.SEARCH_API,
            title="Search title",
            url="https://example.com/same",
            provider="brave",
        )
        rss_item = build_raw_item(
            source_type=SourceType.RSS,
            title="RSS title",
            url="https://example.com/same",
            provider="rss",
        )

        build_pipeline(
            source_repository=source_repository,
            career_repository=career_repository,
            search_api_raw_items=[search_item],
            rss_raw_items=[rss_item],
        ).run()

        source_ids = [
            record.source_item_id
            for record in career_repository.upsert_calls[0]
        ]

        self.assertEqual(len(set(source_ids)), 2)

    def test_missing_source_items_row_raises_clear_error(self):
        raw_item = build_raw_item()
        source_repository = RecordingSourceItemRepository(
            missing_fingerprints={fingerprint_raw_item(raw_item)}
        )

        with self.assertRaisesRegex(RuntimeError, "not found in source_items"):
            build_pipeline(
                source_repository=source_repository,
                career_repository=RecordingCareerSignalRepository(),
                search_api_raw_items=[raw_item],
            ).run()

    def test_ambiguous_mapping_raises_instead_of_guessing(self):
        source_repository = RecordingSourceItemRepository()
        first = build_raw_item(provider="brave")
        second = build_raw_item(provider="alternate")

        with self.assertRaisesRegex(RuntimeError, "Ambiguous CareerSignal source mapping"):
            build_pipeline(
                source_repository=source_repository,
                career_repository=RecordingCareerSignalRepository(),
                search_api_raw_items=[first, second],
            ).run()

    def test_conflicting_source_linkage_surfaces_from_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "conflict.db"
            initialize_database(database_path=database_path)
            source_repository = SourceItemRepository(database_path=database_path)
            career_repository = CareerSignalRepository(database_path=database_path)
            first = build_raw_item(provider="first")
            second = build_raw_item(provider="second")
            source_repository.upsert_one(first)
            first_source = source_repository.get_by_fingerprint(
                fingerprint_raw_item(first)
            )
            signal = CareerSignal(
                signal_id=build_signal_id(first),
                category=SignalCategory.JOB,
                title=first.title,
                organization=first.organization,
                url=first.url,
                published_at=None,
                summary="Existing",
                source_type=first.source_type,
            )
            career_repository.upsert_one(
                signal,
                source_item_id=first_source["source_item_id"],
            )

            with self.assertRaisesRegex(RuntimeError, "CareerSignal persistence failed"):
                build_pipeline(
                    source_repository=source_repository,
                    career_repository=career_repository,
                    search_api_raw_items=[second],
                ).run()

    def test_multiple_eligible_external_signals_receive_distinct_links(self):
        career_repository = RecordingCareerSignalRepository()
        first = build_raw_item(url="https://example.com/one")
        second = build_raw_item(url="https://example.com/two")

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=career_repository,
            search_api_raw_items=[first, second],
        ).run()

        source_ids = [
            record.source_item_id
            for record in career_repository.upsert_calls[0]
        ]

        self.assertEqual(len(set(source_ids)), 2)


class CareerSignalPipelineOrderingTests(unittest.TestCase):
    def test_raw_item_persistence_occurs_before_ai_filtering(self):
        events = []

        class OrderedSourceRepository(RecordingSourceItemRepository):
            def upsert_many(self, raw_items):
                events.append("source_persist")
                return super().upsert_many(raw_items)

        build_pipeline(
            source_repository=OrderedSourceRepository(),
            career_repository=RecordingCareerSignalRepository(),
            search_api_raw_items=[build_raw_item()],
            events=events,
        ).run()

        self.assertLess(events.index("source_persist"), events.index("ai_filter"))

    def test_ai_filtering_occurs_before_normalization(self):
        events = []

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=RecordingCareerSignalRepository(),
            search_api_raw_items=[build_raw_item()],
            events=events,
        ).run()

        self.assertLess(events.index("ai_filter"), events.index("normalizer"))

    def test_career_signal_persistence_occurs_after_normalization(self):
        events = []

        class OrderedCareerRepository(RecordingCareerSignalRepository):
            def upsert_many(self, records):
                events.append("career_persist")
                return super().upsert_many(records)

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=OrderedCareerRepository(),
            search_api_raw_items=[build_raw_item()],
            events=events,
        ).run()

        self.assertLess(events.index("normalizer"), events.index("career_persist"))

    def test_career_signal_persistence_occurs_before_successful_output_return(self):
        events = []

        class OrderedCareerRepository(RecordingCareerSignalRepository):
            def upsert_many(self, records):
                events.append("career_persist")
                return super().upsert_many(records)

        output = build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=OrderedCareerRepository(),
            search_api_raw_items=[build_raw_item()],
            events=events,
        ).run()
        events.append("returned")

        self.assertEqual(output.summary.pipeline_status, "normalization_completed")
        self.assertLess(events.index("career_persist"), events.index("returned"))

    def test_career_repository_is_called_once_when_eligible_records_exist(self):
        career_repository = RecordingCareerSignalRepository()

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=career_repository,
            search_api_raw_items=[
                build_raw_item(url="https://example.com/one"),
                build_raw_item(url="https://example.com/two"),
            ],
        ).run()

        self.assertEqual(len(career_repository.upsert_calls), 1)

    def test_all_eligible_records_are_passed_in_one_combined_batch(self):
        career_repository = RecordingCareerSignalRepository()

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=career_repository,
            search_api_raw_items=[build_raw_item(url="https://example.com/search")],
            rss_raw_items=[
                build_raw_item(
                    source_type=SourceType.RSS,
                    provider="rss",
                    url="https://example.com/rss",
                )
            ],
        ).run()

        self.assertEqual(len(career_repository.upsert_calls[0]), 2)


class CareerSignalPipelineErrorTests(unittest.TestCase):
    def test_career_repository_failure_is_surfaced(self):
        with self.assertRaisesRegex(RuntimeError, "CareerSignal persistence failed"):
            build_pipeline(
                source_repository=RecordingSourceItemRepository(),
                career_repository=RecordingCareerSignalRepository(
                    error=RuntimeError("write failed")
                ),
                search_api_raw_items=[build_raw_item()],
            ).run()

    def test_original_repository_exception_remains_as_cause(self):
        repository_error = RuntimeError("write failed")

        with self.assertRaises(RuntimeError) as context:
            build_pipeline(
                source_repository=RecordingSourceItemRepository(),
                career_repository=RecordingCareerSignalRepository(
                    error=repository_error
                ),
                search_api_raw_items=[build_raw_item()],
            ).run()

        self.assertIs(context.exception.__cause__, repository_error)

    def test_no_successful_output_returned_after_persistence_failure(self):
        with self.assertRaises(RuntimeError):
            output = build_pipeline(
                source_repository=RecordingSourceItemRepository(),
                career_repository=RecordingCareerSignalRepository(
                    error=RuntimeError("write failed")
                ),
                search_api_raw_items=[build_raw_item()],
            ).run()
            self.fail(f"Unexpected output returned: {output}")

    def test_no_partial_career_signal_batch_remains_after_repository_transaction_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "rollback.db"
            initialize_database(database_path=database_path)
            valid_source_repository = SourceItemRepository(database_path=database_path)
            valid_raw_item = build_raw_item(url="https://example.com/valid")
            invalid_raw_item = build_raw_item(url="https://example.com/invalid")
            valid_source_repository.upsert_one(valid_raw_item)
            valid_row = valid_source_repository.get_by_fingerprint(
                fingerprint_raw_item(valid_raw_item)
            )
            fake_source_repository = RecordingSourceItemRepository(
                row_overrides={
                    fingerprint_raw_item(valid_raw_item): valid_row,
                    fingerprint_raw_item(invalid_raw_item): {
                        "source_item_id": 999,
                    },
                }
            )
            career_repository = CareerSignalRepository(database_path=database_path)

            with self.assertRaises(RuntimeError):
                build_pipeline(
                    source_repository=fake_source_repository,
                    career_repository=career_repository,
                    search_api_raw_items=[valid_raw_item, invalid_raw_item],
                ).run()

            self.assertEqual(career_repository.count(), 0)

    def test_source_resolution_failure_prevents_career_persistence(self):
        raw_item = build_raw_item()
        career_repository = RecordingCareerSignalRepository()

        with self.assertRaises(RuntimeError):
            build_pipeline(
                source_repository=RecordingSourceItemRepository(
                    missing_fingerprints={fingerprint_raw_item(raw_item)}
                ),
                career_repository=career_repository,
                search_api_raw_items=[raw_item],
            ).run()

        self.assertEqual(career_repository.upsert_calls, [])

    def test_unmatched_normalized_signal_raises_clear_error(self):
        def empty_normalizer(raw_items, ai_results):
            return []

        with self.assertRaisesRegex(RuntimeError, "could not be matched"):
            build_pipeline(
                source_repository=RecordingSourceItemRepository(),
                career_repository=RecordingCareerSignalRepository(),
                search_api_raw_items=[build_raw_item()],
                normalizer=empty_normalizer,
            ).run()


class CareerSignalPipelineCompatibilityTests(unittest.TestCase):
    def test_pipeline_run_output_contract_remains_unchanged(self):
        raw_item = build_raw_item()

        with patch("src.pipeline.utc_now_iso", return_value="2026-07-23T00:00:00+00:00"):
            without_career = build_pipeline(
                source_repository=RecordingSourceItemRepository(),
                search_api_raw_items=[raw_item],
            ).run()
            with_career = build_pipeline(
                source_repository=RecordingSourceItemRepository(),
                career_repository=RecordingCareerSignalRepository(),
                search_api_raw_items=[raw_item],
            ).run()

        self.assertEqual(
            convert_to_json_ready(with_career),
            convert_to_json_ready(without_career),
        )

    def test_saved_json_structure_remains_unchanged(self):
        raw_item = build_raw_item()

        with tempfile.TemporaryDirectory() as temp_dir:
            without_path = Path(temp_dir) / "without.json"
            with_path = Path(temp_dir) / "with.json"

            with patch(
                "src.pipeline.utc_now_iso",
                return_value="2026-07-23T00:00:00+00:00",
            ):
                save_json(
                    build_pipeline(
                        source_repository=RecordingSourceItemRepository(),
                        search_api_raw_items=[raw_item],
                    ).run(),
                    without_path,
                )
                save_json(
                    build_pipeline(
                        source_repository=RecordingSourceItemRepository(),
                        career_repository=RecordingCareerSignalRepository(),
                        search_api_raw_items=[raw_item],
                    ).run(),
                    with_path,
                )

            without_payload = json.loads(without_path.read_text(encoding="utf-8"))
            with_payload = json.loads(with_path.read_text(encoding="utf-8"))

        self.assertEqual(with_payload.keys(), without_payload.keys())
        self.assertEqual(with_payload["summary"].keys(), without_payload["summary"].keys())

    def test_existing_raw_item_persistence_summary_remains_intact(self):
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            build_pipeline(
                source_repository=RecordingSourceItemRepository(),
                search_api_raw_items=[build_raw_item()],
            ).run()

        self.assertIn("SourceItem persistence:", stdout.getvalue())

    def test_career_signal_persistence_summary_is_distinct_and_concise(self):
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            build_pipeline(
                source_repository=RecordingSourceItemRepository(),
                career_repository=RecordingCareerSignalRepository(),
                search_api_raw_items=[build_raw_item()],
            ).run()

        output = stdout.getvalue()
        self.assertIn("SourceItem persistence:", output)
        self.assertIn("CareerSignal persistence:", output)
        self.assertNotIn("payload_json", output)

    def test_importing_src_main_has_no_database_side_effect(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = (
            DEFAULT_DATABASE_FILE.stat().st_mtime
            if before_exists
            else None
        )

        sys.modules.pop("src.main", None)
        importlib.import_module("src.main")

        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)

        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)

    def test_normal_main_initializes_migrations_and_injects_both_repositories(self):
        main_module = importlib.import_module("src.main")
        captured = {}

        class FakeSourceRepository:
            def __init__(self, database_path):
                self.database_path = database_path

        class FakeCareerRepository:
            def __init__(self, database_path):
                self.database_path = database_path

        class FakePipeline:
            def __init__(self, **kwargs):
                captured["pipeline_kwargs"] = kwargs

            def run(self):
                summary = MagicMock()
                summary.total_target_career_paths = 0
                summary.total_search_queries = 0
                summary.total_search_plans = 0
                summary.total_search_api_plans_executed = 0
                summary.total_search_api_plans_deferred = 0
                summary.total_search_api_result_failures = 0
                summary.total_rss_feeds_executed = 0
                summary.total_selected_websites_executed = 0
                summary.total_raw_items = 0
                summary.total_raw_items_sent_to_ai_filter = 0
                summary.total_ai_filter_results = 0
                summary.total_filtered_raw_items = 0
                summary.total_rejected_raw_items = 0
                summary.total_career_signals = 0
                return MagicMock(summary=summary)

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "main.db"

            with patch.object(main_module, "get_database_path", return_value=database_path), \
                patch.object(main_module, "initialize_database") as initialize_mock, \
                patch.object(main_module, "SourceItemRepository", FakeSourceRepository), \
                patch.object(main_module, "CareerSignalRepository", FakeCareerRepository), \
                patch.object(main_module, "MockPipeline", FakePipeline), \
                patch.object(main_module, "ensure_project_directories"), \
                patch.object(main_module, "validate_required_planning_inputs"), \
                patch.object(main_module, "load_user_preferences_from_json", return_value={}), \
                patch.object(main_module, "save_json", return_value=Path(temp_dir) / "out.json"), \
                redirect_stdout(io.StringIO()):
                main_module.main()

        initialize_mock.assert_called_once_with(database_path=database_path)
        pipeline_kwargs = captured["pipeline_kwargs"]
        self.assertIsInstance(
            pipeline_kwargs["source_item_repository"],
            FakeSourceRepository,
        )
        self.assertIsInstance(
            pipeline_kwargs["career_signal_repository"],
            FakeCareerRepository,
        )

    def test_tests_never_touch_configured_production_database(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = (
            DEFAULT_DATABASE_FILE.stat().st_mtime
            if before_exists
            else None
        )

        build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=RecordingCareerSignalRepository(),
            search_api_raw_items=[build_raw_item()],
        ).run()

        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)

        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)

    def test_no_live_network_or_llm_request_occurs(self):
        output = build_pipeline(
            source_repository=RecordingSourceItemRepository(),
            career_repository=RecordingCareerSignalRepository(),
            search_api_raw_items=[build_raw_item()],
        ).run()

        self.assertEqual(output.summary.total_raw_items, 1)


if __name__ == "__main__":
    unittest.main()
