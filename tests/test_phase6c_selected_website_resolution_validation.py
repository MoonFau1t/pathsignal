from __future__ import annotations

from types import SimpleNamespace
import unittest

from scripts.run_phase6c_selected_website_resolution_validation import validate_run


class Phase6CSelectedWebsiteResolutionValidationTests(unittest.TestCase):
    def result_set(
        self,
        count: int,
        *,
        new_fetch_count: int,
        cache_hit_count: int,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            selected_website_resolution_results=tuple(object() for _ in range(count)),
            generation={
                "executed_selected_website_plan_count": count,
                "http_calls_possible_max": count,
                "new_fetch_count": new_fetch_count,
                "cache_hit_count": cache_hit_count,
            },
        )

    def checks(
        self,
        count: int,
        *,
        live_count: int | None = None,
        replay_count: int | None = None,
        replay_new_fetch_count: int = 0,
        replay_http_calls: list[dict] | None = None,
    ) -> dict[str, bool]:
        actual_live_count = count if live_count is None else live_count
        actual_replay_count = count if replay_count is None else replay_count
        return validate_run(
            eligible_manifest_count=count,
            live=self.result_set(
                actual_live_count,
                new_fetch_count=actual_live_count,
                cache_hit_count=0,
            ),
            replay=self.result_set(
                actual_replay_count,
                new_fetch_count=replay_new_fetch_count,
                cache_hit_count=actual_replay_count,
            ),
            replay_checks={"all_match": actual_live_count == actual_replay_count},
            replay_http_calls=[] if replay_http_calls is None else replay_http_calls,
            artifact_immutability=True,
            upstream_immutability=True,
        )

    def test_historical_two_item_manifest_passes(self) -> None:
        self.assertTrue(self.checks(2)["all_pass"])

    def test_six_item_manifest_passes(self) -> None:
        self.assertTrue(self.checks(6)["all_pass"])

    def test_single_item_manifest_passes(self) -> None:
        self.assertTrue(self.checks(1)["all_pass"])

    def test_result_count_mismatch_fails(self) -> None:
        checks = self.checks(6, live_count=5, replay_count=5)

        self.assertFalse(checks["live_result_count_matches_manifest"])
        self.assertFalse(checks["all_pass"])

    def test_replay_count_and_zero_new_http_pass(self) -> None:
        checks = self.checks(6)

        self.assertTrue(checks["replay_result_count_matches_manifest"])
        self.assertTrue(checks["replay_new_fetch_count_is_zero"])
        self.assertTrue(checks["replay_http_guard_is_empty"])
        self.assertTrue(checks["all_pass"])

    def test_unexpected_replay_http_fails(self) -> None:
        checks = self.checks(
            6,
            replay_new_fetch_count=1,
            replay_http_calls=[{"method": "GET", "url": "https://example.com"}],
        )

        self.assertFalse(checks["replay_new_fetch_count_is_zero"])
        self.assertFalse(checks["replay_http_guard_is_empty"])
        self.assertFalse(checks["all_pass"])


if __name__ == "__main__":
    unittest.main()
