import importlib
from pathlib import Path
import unittest

from src.config import DEFAULT_DATABASE_FILE
from src.database.source_identity import fingerprint_raw_item
from src.models import (
    AIFilterExecutionReport,
    AIFilterResult,
    RawItem,
    RawItemFilterStatus,
    SearchAPIExecutionReport,
    SignalCategory,
    SourceType,
)
from src.normalizer import (
    _fingerprint_raw_item,
    normalize_raw_items_to_career_signals,
)
from src.pipeline import MockPipeline
from src.signal_identity import build_signal_id


def build_raw_item(
    *,
    source_type: SourceType = SourceType.SEARCH_API,
    title: str = "Strategy Analyst",
    organization: str = "Example Co",
    url: str = "https://example.com/jobs/1",
    published_at: str | None = "2026-01-02T00:00:00+00:00",
    raw_text: str = "body",
    metadata: dict | None = None,
) -> RawItem:
    return RawItem(
        source_type=source_type,
        title=title,
        organization=organization,
        url=url,
        published_at=published_at,
        raw_text=raw_text,
        metadata={"provider": "brave"} if metadata is None else metadata,
    )


class SignalIdentityTests(unittest.TestCase):
    def test_same_raw_item_is_deterministic(self):
        raw_item = build_raw_item()

        self.assertEqual(build_signal_id(raw_item), build_signal_id(raw_item))

    def test_public_function_reproduces_captured_legacy_ids(self):
        cases = [
            (build_raw_item(), "signal_3e877c5b5a8269e1"),
            (
                build_raw_item(
                    title="\u6218\u7565\u5c97\u4f4d",
                    url="https://example.com/jobs/cn",
                ),
                "signal_3b17e4be2f78528f",
            ),
            (
                build_raw_item(
                    source_type=SourceType.RSS,
                    metadata={"provider": "rss"},
                ),
                "signal_24430dd9dfd05f46",
            ),
            (build_raw_item(url="", published_at=None), "signal_d6092f03c2ff4e72"),
        ]

        for raw_item, expected_signal_id in cases:
            with self.subTest(expected_signal_id=expected_signal_id):
                self.assertEqual(build_signal_id(raw_item), expected_signal_id)

    def test_source_type_title_and_url_affect_identity(self):
        base_signal_id = build_signal_id(build_raw_item())

        changed_items = [
            build_raw_item(source_type=SourceType.RSS),
            build_raw_item(title="Product Analyst"),
            build_raw_item(url="https://example.com/jobs/2"),
        ]

        for raw_item in changed_items:
            with self.subTest(raw_item=raw_item):
                self.assertNotEqual(build_signal_id(raw_item), base_signal_id)

    def test_non_identity_fields_do_not_affect_identity(self):
        self.assertEqual(
            build_signal_id(
                build_raw_item(
                    organization="Org A",
                    published_at=None,
                    raw_text="body A",
                    metadata={"a": 1},
                )
            ),
            "signal_3e877c5b5a8269e1",
        )
        self.assertEqual(
            build_signal_id(
                build_raw_item(
                    organization="Org B",
                    published_at="2026-03-04T00:00:00+00:00",
                    raw_text="body B",
                    metadata={"b": 2},
                )
            ),
            "signal_3e877c5b5a8269e1",
        )

    def test_unicode_identity_is_deterministic(self):
        raw_item = build_raw_item(
            title="\u6218\u7565\u5c97\u4f4d",
            url="https://example.com/jobs/cn",
        )

        self.assertEqual(build_signal_id(raw_item), "signal_3b17e4be2f78528f")
        self.assertEqual(build_signal_id(raw_item), "signal_3b17e4be2f78528f")

    def test_normalizer_uses_shared_signal_identity(self):
        raw_item = build_raw_item()
        filter_result = AIFilterResult(
            raw_item_fingerprint=_fingerprint_raw_item(raw_item),
            title=raw_item.title,
            url=raw_item.url,
            is_relevant=True,
            confidence=0.9,
            reason="accepted",
            suggested_category=SignalCategory.JOB,
        )

        signals = normalize_raw_items_to_career_signals([raw_item], [filter_result])

        self.assertEqual(signals[0].signal_id, build_signal_id(raw_item))

    def test_phase4b_matching_uses_shared_signal_identity(self):
        raw_item = build_raw_item()
        career_repository = RecordingCareerSignalRepository()

        build_pipeline(
            career_repository=career_repository,
            search_api_raw_items=[raw_item],
        ).run()

        record = career_repository.upsert_calls[0][0]
        self.assertEqual(record.career_signal.signal_id, build_signal_id(raw_item))

    def test_signal_identity_has_single_production_implementation(self):
        production_sources = {
            path: path.read_text(encoding="utf-8")
            for path in Path("src").rglob("*.py")
        }

        self.assertEqual(
            sum(text.count("def build_signal_id") for text in production_sources.values()),
            1,
        )
        self.assertFalse(
            any("_build_signal_id" in text for text in production_sources.values())
        )

    def test_importing_signal_identity_has_no_database_side_effect(self):
        before_exists = DEFAULT_DATABASE_FILE.exists()
        before_mtime = (
            DEFAULT_DATABASE_FILE.stat().st_mtime
            if before_exists
            else None
        )

        importlib.reload(importlib.import_module("src.signal_identity"))

        self.assertEqual(DEFAULT_DATABASE_FILE.exists(), before_exists)
        if before_exists:
            self.assertEqual(DEFAULT_DATABASE_FILE.stat().st_mtime, before_mtime)


class RecordingSourceItemRepository:
    def __init__(self):
        self.rows_by_fingerprint = {}
        self.next_source_item_id = 1

    def upsert_many(self, raw_items):
        batch = list(raw_items)

        for raw_item in batch:
            fingerprint = fingerprint_raw_item(raw_item)
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
        return self.rows_by_fingerprint.get(fingerprint)


class RecordingCareerSignalRepository:
    def __init__(self):
        self.upsert_calls = []

    def upsert_many(self, records):
        batch = list(records)
        self.upsert_calls.append(batch)

        return FakeUpsertSummary(
            received_count=len(batch),
            unique_count=len({record.career_signal.signal_id for record in batch}),
            inserted_count=len(batch),
            updated_count=0,
        )


class FakeUpsertSummary:
    def __init__(
        self,
        *,
        received_count: int,
        unique_count: int,
        inserted_count: int,
        updated_count: int,
    ):
        self.received_count = received_count
        self.unique_count = unique_count
        self.inserted_count = inserted_count
        self.updated_count = updated_count


def build_pipeline(
    *,
    career_repository,
    search_api_raw_items,
) -> MockPipeline:
    raw_items = list(search_api_raw_items)

    def ai_filter_executor(items, user_profile, career_paths):
        ai_filter_results = []
        raw_item_statuses = []

        for index, raw_item in enumerate(items):
            fingerprint = _fingerprint_raw_item(raw_item)
            ai_filter_results.append(
                AIFilterResult(
                    raw_item_fingerprint=fingerprint,
                    title=raw_item.title,
                    url=raw_item.url,
                    is_relevant=True,
                    confidence=0.9,
                    reason="accepted",
                    suggested_category=SignalCategory.JOB,
                )
            )
            raw_item_statuses.append(
                RawItemFilterStatus(
                    raw_item_fingerprint=fingerprint,
                    raw_item_index=index,
                    source_type=raw_item.source_type,
                    title=raw_item.title,
                    url=raw_item.url,
                    status="processed_accepted",
                    reason="accepted",
                    is_relevant=True,
                )
            )

        return AIFilterExecutionReport(
            filtered_raw_items=items,
            ai_filter_results=ai_filter_results,
            raw_item_statuses=raw_item_statuses,
            executed_count=len(items),
        )

    return MockPipeline(
        raw_item_loader=lambda: [],
        user_profile_loader=lambda: None,
        search_scope_loader=lambda: None,
        career_path_generator=lambda user_profile: [],
        search_query_generator=lambda career_paths: [],
        search_plan_builder=lambda search_queries, search_scope: [],
        search_api_executor=lambda search_plans: SearchAPIExecutionReport(
            raw_items=raw_items,
            executed_plan_count=1 if raw_items else 0,
        ),
        rss_executor=lambda search_scope, search_plans: ([], 0),
        selected_website_executor=lambda search_scope, search_plans: ([], 0),
        ai_filter_executor=ai_filter_executor,
        normalizer=normalize_raw_items_to_career_signals,
        source_item_repository=RecordingSourceItemRepository(),
        career_signal_repository=career_repository,
    )


if __name__ == "__main__":
    unittest.main()
