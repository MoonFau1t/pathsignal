import unittest
from unittest.mock import patch

from src.career_intelligence_brief import CareerIntelligenceBriefError
from src.career_intelligence_interpretation import EMPTY_INPUT_WARNING
from src.models import SignalCategory
from tests.test_career_intelligence_interpretation_integration import (
    RecordingInterpretationExecutor,
    make_pipeline,
)


GENERATED_AT = "2026-08-12T04:00:00+00:00"


class MainPipelineBriefIntegrationTests(unittest.TestCase):
    def test_mixed_batch_exposes_opportunities_and_interpretation_sections(self):
        executor = RecordingInterpretationExecutor()
        pipeline, _, _ = make_pipeline(
            (
                SignalCategory.JOB,
                SignalCategory.NEWS,
                SignalCategory.MARKET_TREND,
            ),
            interpretation_executor=executor,
        )

        with patch("src.pipeline.utc_now_iso", return_value=GENERATED_AT):
            output = pipeline.run()

        brief = output.career_intelligence_brief
        self.assertEqual(output.generated_at, GENERATED_AT)
        self.assertEqual(brief.generated_at, GENERATED_AT)
        self.assertEqual(len(brief.opportunities), 1)
        self.assertEqual(len(brief.key_developments), 1)
        self.assertEqual(
            brief.key_developments,
            output.career_intelligence_interpretation.key_developments,
        )
        self.assertEqual(
            output.to_dict()["career_intelligence_brief"],
            brief.to_dict(),
        )

    def test_opportunity_only_pipeline_builds_valid_brief_without_interpretation_call(self):
        executor = RecordingInterpretationExecutor()
        pipeline, _, _ = make_pipeline(
            (SignalCategory.JOB,),
            interpretation_executor=executor,
        )

        output = pipeline.run()

        self.assertEqual(executor.contexts, [])
        self.assertEqual(len(output.career_intelligence_brief.opportunities), 1)
        self.assertEqual(output.career_intelligence_brief.key_developments, ())
        self.assertEqual(
            output.career_intelligence_brief.warnings,
            (EMPTY_INPUT_WARNING,),
        )

    def test_builder_error_propagates_at_explicit_pipeline_stage(self):
        pipeline, _, _ = make_pipeline(
            (),
            interpretation_executor=RecordingInterpretationExecutor(),
        )
        error = CareerIntelligenceBriefError("Synthetic assembly failure.")

        with patch(
            "src.pipeline.build_career_intelligence_brief",
            side_effect=error,
        ):
            with self.assertRaises(CareerIntelligenceBriefError) as context:
                pipeline.run()

        self.assertIs(context.exception, error)
        self.assertEqual(pipeline.pipeline_run_stage, "career_intelligence_brief")


if __name__ == "__main__":
    unittest.main()
