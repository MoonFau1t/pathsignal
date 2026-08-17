import json
import tempfile
import unittest
from pathlib import Path

from scripts.summarize_source_monitoring_outputs import (
    generate_source_monitoring_reports,
)
from src.storage import save_json
from tests.test_source_monitoring_entity_prioritization import (
    FakePriorityClient,
    entity,
    need,
    prioritize_entities,
    target_path,
    universe,
)


class SourceMonitoringReportingTests(unittest.TestCase):
    def test_reports_work_without_phase3_priorities(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            universe_path = root / "entity_universe.json"
            priorities_path = root / "entity_priorities.json"
            source_plans_path = root / "source_discovery_plans.json"
            candidate_sources_path = root / "candidate_sources.json"
            report_dir = root / "reports"
            save_json(universe(), universe_path)

            (
                universe_report,
                priorities_report,
                compact_json,
                source_plans_report,
                candidate_sources_report,
                candidate_sources_compact,
            ) = (
                generate_source_monitoring_reports(
                    entity_universe_path=universe_path,
                    entity_priorities_path=priorities_path,
                    source_discovery_plans_path=source_plans_path,
                    candidate_sources_path=candidate_sources_path,
                    report_dir=report_dir,
                )
            )

            self.assertIn("Derived non-canonical view", universe_report.read_text())
            self.assertIn("Entities: 1", universe_report.read_text())
            self.assertIn("Entity priorities cache not found", priorities_report.read_text())
            self.assertEqual(json.loads(compact_json.read_text()), [])
            self.assertIn(
                "Source discovery planning cache not found",
                source_plans_report.read_text(),
            )
            self.assertIn(
                "Candidate source discovery output not found",
                candidate_sources_report.read_text(),
            )
            self.assertEqual(json.loads(candidate_sources_compact.read_text()), [])

    def test_priority_reports_are_compact_and_preserve_chinese(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            universe_path = root / "entity_universe.json"
            priorities_path = root / "entity_priorities.json"
            source_plans_path = root / "source_discovery_plans.json"
            candidate_sources_path = root / "candidate_sources.json"
            report_dir = root / "reports"
            chinese_entity = entity(
                names_by_language={
                    "en": ("Example AI",),
                    "zh": ("\u793a\u4f8b\u667a\u80fd",),
                }
            )
            phase2 = universe(entities=(chinese_entity,))
            phase3 = prioritize_entities(
                entity_universe_result=phase2,
                information_needs=(need(),),
                target_career_paths=[target_path()],
                user_preferences={},
                client=FakePriorityClient(),
                cache_enabled=False,
            )
            save_json(phase2, universe_path)
            save_json(phase3, priorities_path)

            (
                universe_report,
                priorities_report,
                compact_json,
                source_plans_report,
                candidate_sources_report,
                candidate_sources_compact,
            ) = (
                generate_source_monitoring_reports(
                    entity_universe_path=universe_path,
                    entity_priorities_path=priorities_path,
                    source_discovery_plans_path=source_plans_path,
                    candidate_sources_path=candidate_sources_path,
                    report_dir=report_dir,
                )
            )
            compact_payload = json.loads(compact_json.read_text(encoding="utf-8"))

            self.assertIn("\u793a\u4f8b\u667a\u80fd", universe_report.read_text(encoding="utf-8"))
            self.assertIn("tier_", priorities_report.read_text(encoding="utf-8"))
            self.assertEqual(compact_payload[0]["canonical_name"], "Example AI")
            self.assertNotIn("raw_metadata", json.dumps(compact_payload))
            self.assertTrue(source_plans_report.exists())
            self.assertTrue(candidate_sources_report.exists())
            self.assertEqual(
                json.loads(candidate_sources_compact.read_text(encoding="utf-8")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
