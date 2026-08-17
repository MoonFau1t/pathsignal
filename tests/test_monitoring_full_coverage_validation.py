from __future__ import annotations

from io import BytesIO, TextIOWrapper
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts.run_monitoring_full_coverage_validation import (
    TrackedCompletions,
    checkpoint_runtime_outputs,
    configure_utf8_stream,
    diagnostic_print,
    load_filter_execution_rows,
)
from src.ai_filter import (
    AIFilterClient,
    _build_filter_prompt,
    _fingerprint_raw_item,
)
from src.models import (
    CareerPathCategory,
    RawItem,
    SourceType,
    TargetCareerPath,
    UserProfile,
)


CHINESE_ORGANIZATION = "前瞻产业研究院"
CHINESE_TITLE = "人工智能行业研究报告"
CHINESE_SUMMARY = "中国企业数字化转型"


def make_profile() -> UserProfile:
    return UserProfile(
        profile_id="unicode-profile",
        name="Synthetic User",
        background_summary="AI strategy research.",
    )


def make_path() -> TargetCareerPath:
    return TargetCareerPath(
        path_id="path-ai-strategy",
        title="AI Strategy",
        category=CareerPathCategory.AI_STRATEGY,
        description="AI strategy work.",
        fit_score=0.9,
        keywords=["AI strategy"],
    )


def make_item(*, unicode_content: bool = True) -> RawItem:
    return RawItem(
        source_type=SourceType.SELECTED_WEBSITE,
        title=CHINESE_TITLE if unicode_content else "AI industry report",
        organization=(
            CHINESE_ORGANIZATION if unicode_content else "Example Institute"
        ),
        url="https://example.test/reports/ai",
        published_at="2026-08-14T00:00:00+00:00",
        raw_text=CHINESE_SUMMARY if unicode_content else "Enterprise AI strategy.",
        metadata={"provider": "selected_website"},
    )


def llm_response() -> SimpleNamespace:
    content = json.dumps(
        {
            "is_relevant": True,
            "confidence": 0.75,
            "reason": "Relevant synthetic report.",
            "suggested_category": "market_trend",
            "matched_career_path_ids": ["path-ai-strategy"],
            "action": "keep",
        }
    )
    return SimpleNamespace(
        id="mock-response",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
    )


class Payload:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return self.value


class UnicodeValidationTests(unittest.TestCase):
    def test_previous_cp1252_diagnostic_operation_reproduces_failure(self) -> None:
        buffer = BytesIO()
        stream = TextIOWrapper(buffer, encoding="cp1252", errors="strict")
        with self.assertRaises(UnicodeEncodeError):
            diagnostic_print(
                f"FILTER_ITEM 16 title={CHINESE_TITLE!r}",
                stream=stream,
            )
        stream.close()

    def test_utf8_diagnostics_preserve_exact_unicode(self) -> None:
        buffer = BytesIO()
        stream = TextIOWrapper(buffer, encoding="cp1252", errors="strict")
        configure_utf8_stream(stream)
        message = f"FILTER_ITEM 16 title={CHINESE_TITLE!r}"
        diagnostic_print(message, stream=stream)
        stream.flush()
        self.assertEqual(buffer.getvalue().decode("utf-8").strip(), message)
        self.assertEqual(stream.encoding.casefold(), "utf-8")
        self.assertEqual(stream.errors, "strict")
        stream.close()

    def test_prompt_and_request_identity_preserve_unicode(self) -> None:
        item = make_item()
        prompt = _build_filter_prompt(item, make_profile(), [make_path()])
        self.assertIn(CHINESE_ORGANIZATION, prompt)
        self.assertIn(CHINESE_TITLE, prompt)
        self.assertIn(CHINESE_SUMMARY, prompt)
        first = _fingerprint_raw_item(item)
        second = _fingerprint_raw_item(item)
        without_unicode = _fingerprint_raw_item(make_item(unicode_content=False))
        self.assertEqual(first, second)
        self.assertNotEqual(first, without_unicode)

    def test_mocked_provider_receives_exact_unicode_content(self) -> None:
        response = llm_response()
        with patch("src.ai_filter.OpenAI") as openai_factory:
            create = openai_factory.return_value.chat.completions.create
            create.return_value = response
            client = AIFilterClient(
                provider="deepseek",
                api_key="test-key",
                base_url="https://deepseek.example.test",
                model="test-model",
                dry_run=False,
            )
            result = client.filter_item(make_item(), make_profile(), [make_path()])

        user_message = create.call_args.kwargs["messages"][1]["content"]
        self.assertIn(CHINESE_ORGANIZATION, user_message)
        self.assertIn(CHINESE_TITLE, user_message)
        self.assertIn(CHINESE_SUMMARY, user_message)
        self.assertEqual(result.title, CHINESE_TITLE)

    def test_english_prompt_behavior_is_unchanged(self) -> None:
        item = make_item(unicode_content=False)
        prompt = _build_filter_prompt(item, make_profile(), [make_path()])
        self.assertIn('"title": "AI industry report"', prompt)
        self.assertIn('"organization": "Example Institute"', prompt)
        self.assertIn('"raw_text": "Enterprise AI strategy."', prompt)

    def test_tracked_completion_passes_unicode_messages_unchanged(self) -> None:
        response = llm_response()
        captured = {}

        class Underlying:
            def create(self, *args, **kwargs):
                captured.update(kwargs)
                return response

        telemetry = {"http": [], "model": []}
        tracked = TrackedCompletions(Underlying(), "ai_filter", telemetry)
        messages = [{"role": "user", "content": CHINESE_SUMMARY}]
        with patch(
            "scripts.run_monitoring_full_coverage_validation.diagnostic_print"
        ):
            tracked.create(model="test-model", messages=messages)

        self.assertEqual(captured["messages"], messages)
        self.assertEqual(
            telemetry["model"][0]["input_character_count"],
            len(CHINESE_SUMMARY),
        )
        self.assertEqual(telemetry["model"][0]["status"], "completed")


class ValidationReportingTests(unittest.TestCase):
    def test_joined_status_query_qualifies_both_status_columns(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE filter_executions (
                filter_execution_id INTEGER PRIMARY KEY,
                run_id TEXT NOT NULL,
                status TEXT NOT NULL,
                error_type TEXT,
                error_message TEXT
            );
            CREATE TABLE run_source_item_filter_statuses (
                filter_execution_id INTEGER NOT NULL,
                source_item_id INTEGER NOT NULL,
                status TEXT NOT NULL
            );
            INSERT INTO filter_executions VALUES
                (1, 'run-test', 'failed', 'SyntheticError', 'synthetic');
            INSERT INTO run_source_item_filter_statuses VALUES
                (1, 42, 'failed');
            """
        )
        rows = load_filter_execution_rows(connection, "run-test")
        connection.close()
        self.assertEqual(
            rows,
            [
                {
                    "filter_execution_id": 1,
                    "source_item_id": 42,
                    "filter_execution_status": "failed",
                    "item_filter_status": "failed",
                    "error_type": "SyntheticError",
                    "error_message": "synthetic",
                }
            ],
        )

    def test_checkpoint_retains_all_runtime_only_outputs(self) -> None:
        result = SimpleNamespace(
            run_id="run-capture",
            status="completed",
            summary={"monitoring_filter_eligible_count": 1},
            source_results=(Payload({"source_execution_id": 1}),),
            observed_raw_items=(Payload({"title": CHINESE_TITLE}),),
            ai_filter_results=(Payload({"action": "keep"}),),
            filtered_raw_items=(Payload({"title": CHINESE_TITLE}),),
            career_signals=(Payload({"signal_id": "signal-1"}),),
            scored_career_signals=(
                Payload(
                    {
                        "priority_assessment": {
                            "components": {"signal_significance": {"score": 1}}
                        },
                        "priority_score": {
                            "priority_score": 80,
                            "tier": "medium_high",
                        },
                    }
                ),
            ),
            priority_assessment_diagnostics=(),
            career_signal_routing=Payload(
                {"opportunities": [], "intelligence": ["signal-1"], "unrouted": []}
            ),
            career_intelligence_interpretation=Payload(
                {
                    "input_signal_ids": ["signal-1"],
                    "themes": [{"title": CHINESE_SUMMARY}],
                    "key_developments": [{"title": CHINESE_TITLE}],
                    "career_implications": [{"summary": CHINESE_SUMMARY}],
                    "warnings": [],
                }
            ),
            career_intelligence_brief=Payload(
                {
                    "opportunities": [],
                    "key_developments": [{"title": CHINESE_TITLE}],
                    "themes": [{"title": CHINESE_SUMMARY}],
                    "career_implications": [{"summary": CHINESE_SUMMARY}],
                    "warnings": [],
                }
            ),
        )
        telemetry = {
            "http": [{"status": "completed"}],
            "model": [{"usage": {"total_tokens": 15}}],
        }
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "validation.json"
            report = {"schema_version": "test"}
            checkpoint_runtime_outputs(
                path=path,
                report=report,
                result=result,
                telemetry=telemetry,
            )
            text = path.read_text(encoding="utf-8")
            payload = json.loads(text)

        self.assertIn(CHINESE_TITLE, text)
        self.assertIn(CHINESE_SUMMARY, text)
        self.assertEqual(payload["runtime_status"], "completed")
        self.assertEqual(
            payload["scored_career_signals"][0]["priority_score"]["tier"],
            "medium_high",
        )
        self.assertEqual(payload["routing"]["intelligence"], ["signal-1"])
        self.assertEqual(
            payload["interpretation"]["input_signal_ids"], ["signal-1"]
        )
        self.assertEqual(
            payload["brief"]["key_developments"][0]["title"], CHINESE_TITLE
        )
        self.assertEqual(payload["telemetry"], telemetry)


if __name__ == "__main__":
    unittest.main()
