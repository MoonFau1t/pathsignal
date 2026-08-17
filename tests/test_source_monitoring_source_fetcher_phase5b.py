from pathlib import Path
import hashlib
import shutil
import unittest

import requests

from src.config import PROJECT_ROOT
from src.source_monitoring.source_evaluation_models import (
    FetchStatus,
    FetchedPage,
)
from src.source_monitoring.source_fetcher import (
    SourceFetchPolicy,
    SourceFetcher,
    execute_source_fetch_requests,
)


class SourceFetcherPhase5BTests(unittest.TestCase):
    def setUp(self) -> None:
        short_name = hashlib.sha256(
            self._testMethodName.encode("utf-8")
        ).hexdigest()[:12]
        self.test_root = PROJECT_ROOT / "tmp_phase5b_fetcher_tests" / short_name
        if self.test_root.exists():
            shutil.rmtree(self.test_root)
        self.test_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self.test_root.exists():
            shutil.rmtree(self.test_root)

    def fetcher(
        self,
        session,
        *,
        max_bytes: int = 200,
        max_redirects: int = 3,
        cache_enabled: bool = True,
        times: tuple[str, ...] = ("2026-08-08T00:00:00+00:00",),
    ) -> SourceFetcher:
        now = TimeSequence(times)
        return SourceFetcher(
            policy=SourceFetchPolicy(
                timeout_seconds=5,
                max_response_bytes=max_bytes,
                max_redirects=max_redirects,
                artifact_root=self.test_root / "raw_pages",
                failure_root=self.test_root / "fetch_failures",
                cache_enabled=cache_enabled,
                batch_size=10,
            ),
            session=session,
            now_fn=now,
            monotonic_fn=MonotonicSequence(),
        )

    def test_normal_http_200_html_persists_raw_artifact_and_fetched_page(self):
        body = b"<html><body>Hello</body></html>"
        session = FakeSession({"https://example.com": [response(200, "https://example.com", body)]})
        fetcher = self.fetcher(session)
        request = fetcher.build_request("https://example.com")

        outcome = fetcher.fetch(
            request=request,
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )

        self.assertFalse(outcome.cache_hit)
        self.assertEqual(outcome.execution.fetch_status, FetchStatus.COMPLETED_HTML)
        self.assertEqual(outcome.execution.http_status, 200)
        self.assertEqual(outcome.execution.response_size_bytes, len(body))
        self.assertEqual(outcome.fetched_page.raw_bytes, body)
        artifact = PROJECT_ROOT / outcome.execution.raw_artifact_ref.artifact_path
        self.assertEqual(artifact.read_bytes(), body)
        self.assertFalse(Path(outcome.execution.raw_artifact_ref.artifact_path).is_absolute())

    def test_utf8_and_chinese_html_decoding_preserves_bytes(self):
        samples = (
            "Plain UTF-8 cafe",
            "中文页面内容",
        )
        for index, text in enumerate(samples):
            with self.subTest(text=text):
                body = f"<html><body>{text}</body></html>".encode("utf-8")
                url = f"https://example.com/{index}"
                session = FakeSession({
                    url: [
                        response(
                            200,
                            url,
                            body,
                            headers={"Content-Type": "text/html; charset=utf-8"},
                        )
                    ]
                })
                fetcher = self.fetcher(session, cache_enabled=False)
                outcome = fetcher.fetch(
                    request=fetcher.build_request(url),
                    source_evaluation_plan_id="plan",
                    candidate_source_id=f"candidate-{index}",
                )

                self.assertEqual(outcome.execution.raw_body_sha256, outcome.execution.raw_artifact_ref.sha256)
                self.assertEqual((PROJECT_ROOT / outcome.execution.raw_artifact_ref.artifact_path).read_bytes(), body)
                self.assertIn(text, outcome.fetched_page.decoded_text)
                self.assertEqual(outcome.execution.declared_encoding, "utf-8")

    def test_streamed_response_encoding_detection_does_not_read_consumed_content(self):
        body = "中文无声明编码".encode("utf-8")
        url = "https://example.com/no-charset"
        fake_response = response(
            200,
            url,
            body,
            headers={"Content-Type": "text/html"},
        )
        fake_response.raise_on_apparent_encoding = True
        session = FakeSession({url: [fake_response]})
        fetcher = self.fetcher(session, cache_enabled=False)

        outcome = fetcher.fetch(
            request=fetcher.build_request(url),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )

        self.assertEqual(outcome.execution.fetch_status, FetchStatus.COMPLETED_HTML)
        self.assertEqual(outcome.execution.detected_encoding, "utf-8")
        self.assertIn("中文", outcome.fetched_page.decoded_text)

    def test_redirect_to_final_html_preserves_requested_final_and_hop(self):
        session = FakeSession({
            "http://example.com": [
                response(
                    301,
                    "http://example.com",
                    b"",
                    headers={"Location": "https://example.com/news"},
                )
            ],
            "https://example.com/news": [
                response(200, "https://example.com/news", b"<html>news</html>")
            ],
        })
        fetcher = self.fetcher(session)

        outcome = fetcher.fetch(
            request=fetcher.build_request("http://example.com"),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )

        self.assertEqual(outcome.execution.requested_url, "http://example.com")
        self.assertEqual(outcome.execution.final_url, "https://example.com/news")
        self.assertEqual(len(outcome.execution.redirect_chain), 1)
        self.assertEqual(outcome.execution.redirect_chain[0].status_code, 301)

    def test_multiple_bounded_redirects_are_followed(self):
        session = FakeSession({
            "https://a.example": [response(302, "https://a.example", b"", headers={"Location": "https://b.example"})],
            "https://b.example": [response(308, "https://b.example", b"", headers={"Location": "https://c.example"})],
            "https://c.example": [response(200, "https://c.example", b"<html>ok</html>")],
        })
        fetcher = self.fetcher(session, max_redirects=2)
        outcome = fetcher.fetch(
            request=fetcher.build_request("https://a.example"),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )
        self.assertEqual(outcome.execution.fetch_status, FetchStatus.COMPLETED_HTML)
        self.assertEqual([hop.status_code for hop in outcome.execution.redirect_chain], [302, 308])

    def test_redirect_loop_and_redirect_limit_are_controlled_failures(self):
        loop = FakeSession({
            "https://a.example": [response(302, "https://a.example", b"", headers={"Location": "https://b.example"})],
            "https://b.example": [response(302, "https://b.example", b"", headers={"Location": "https://a.example"})],
        })
        loop_outcome = self.fetcher(loop, max_redirects=5).fetch(
            request=self.fetcher(loop).build_request("https://a.example"),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )
        self.assertEqual(loop_outcome.execution.fetch_status, FetchStatus.REDIRECT_FAILURE)
        self.assertEqual(loop_outcome.execution.error_type, "redirect_loop")

        exceeded = FakeSession({
            "https://a.example": [response(302, "https://a.example", b"", headers={"Location": "https://b.example"})],
            "https://b.example": [response(302, "https://b.example", b"", headers={"Location": "https://c.example"})],
        })
        fetcher = self.fetcher(exceeded, max_redirects=1)
        outcome = fetcher.fetch(
            request=fetcher.build_request("https://a.example"),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )
        self.assertEqual(outcome.execution.fetch_status, FetchStatus.REDIRECT_FAILURE)
        self.assertEqual(outcome.execution.error_type, "too_many_redirects")

    def test_http_204_and_http_failures_are_network_facts_not_semantic_rejections(self):
        cases = (
            (204, FetchStatus.COMPLETED_EMPTY_RESPONSE),
            (403, FetchStatus.HTTP_FAILURE),
            (404, FetchStatus.HTTP_FAILURE),
            (429, FetchStatus.HTTP_FAILURE),
            (500, FetchStatus.HTTP_FAILURE),
            (503, FetchStatus.HTTP_FAILURE),
        )
        for status_code, expected_status in cases:
            with self.subTest(status_code=status_code):
                url = f"https://example.com/{status_code}"
                session = FakeSession({url: [response(status_code, url, b"body")]})
                fetcher = self.fetcher(session, cache_enabled=False)
                outcome = fetcher.fetch(
                    request=fetcher.build_request(url),
                    source_evaluation_plan_id="plan",
                    candidate_source_id=f"candidate-{status_code}",
                )
                self.assertEqual(outcome.execution.fetch_status, expected_status)
                self.assertEqual(outcome.execution.http_status, status_code)
                if status_code >= 400:
                    self.assertIsNone(outcome.execution.raw_artifact_ref)
                    self.assertIsNone(outcome.fetched_page)

    def test_timeout_and_connection_failure_return_auditable_executions(self):
        errors = (
            (requests.Timeout("slow"), FetchStatus.TIMEOUT),
            (requests.ConnectionError("dns"), FetchStatus.NETWORK_FAILURE),
        )
        for error, expected in errors:
            with self.subTest(error=type(error).__name__):
                session = FakeSession({"https://example.com": [error]})
                fetcher = self.fetcher(session, cache_enabled=False)
                outcome = fetcher.fetch(
                    request=fetcher.build_request("https://example.com"),
                    source_evaluation_plan_id="plan",
                    candidate_source_id=f"candidate-{type(error).__name__}",
                )
                self.assertEqual(outcome.execution.fetch_status, expected)
                self.assertIsNotNone(outcome.execution.error_type)
                self.assertIsNone(outcome.fetched_page)

    def test_pdf_and_xml_like_content_complete_without_parsing_or_feed_validation(self):
        cases = (
            ("application/pdf", FetchStatus.COMPLETED_NON_HTML),
            ("application/rss+xml", FetchStatus.COMPLETED_NON_HTML),
            ("application/xml", FetchStatus.COMPLETED_NON_HTML),
            ("text/plain", FetchStatus.COMPLETED_NON_HTML),
        )
        for content_type, expected in cases:
            with self.subTest(content_type=content_type):
                url = f"https://example.com/{content_type.replace('/', '-')}"
                session = FakeSession({url: [response(200, url, b"payload", headers={"Content-Type": content_type})]})
                fetcher = self.fetcher(session, cache_enabled=False)
                outcome = fetcher.fetch(
                    request=fetcher.build_request(url),
                    source_evaluation_plan_id="plan",
                    candidate_source_id=content_type,
                )
                self.assertEqual(outcome.execution.fetch_status, expected)
                self.assertEqual(outcome.execution.content_type, content_type)

    def test_unsupported_content_type_is_explicit_failure(self):
        session = FakeSession({
            "https://example.com/bin": [
                response(200, "https://example.com/bin", b"payload", headers={"Content-Type": "application/octet-stream"})
            ]
        })
        fetcher = self.fetcher(session)
        outcome = fetcher.fetch(
            request=fetcher.build_request("https://example.com/bin"),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )
        self.assertEqual(outcome.execution.fetch_status, FetchStatus.UNSUPPORTED_CONTENT)
        self.assertEqual(outcome.execution.error_type, "unsupported_content_type")

    def test_content_length_too_large_fails_before_stream_read(self):
        fake_response = response(
            200,
            "https://example.com/large",
            b"x" * 50,
            headers={"Content-Length": "50"},
        )
        session = FakeSession({"https://example.com/large": [fake_response]})
        fetcher = self.fetcher(session, max_bytes=10)
        outcome = fetcher.fetch(
            request=fetcher.build_request("https://example.com/large"),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )
        self.assertEqual(outcome.execution.fetch_status, FetchStatus.RESPONSE_TOO_LARGE)
        self.assertFalse(fake_response.iterated)
        self.assertIsNone(outcome.execution.raw_artifact_ref)

    def test_streaming_response_exceeding_limit_fails_without_partial_artifact(self):
        session = FakeSession({
            "https://example.com/chunked": [
                response(200, "https://example.com/chunked", [b"12345", b"67890", b"!"])
            ]
        })
        fetcher = self.fetcher(session, max_bytes=10)
        outcome = fetcher.fetch(
            request=fetcher.build_request("https://example.com/chunked"),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )
        self.assertEqual(outcome.execution.fetch_status, FetchStatus.RESPONSE_TOO_LARGE)
        self.assertEqual(outcome.execution.response_size_bytes, 11)
        self.assertIsNone(outcome.execution.raw_artifact_ref)

    def test_identical_bytes_same_body_sha_different_retrieval_times_do_not_matter(self):
        body = b"same bytes"
        session = FakeSession({
            "https://example.com/a": [response(200, "https://example.com/a", body)],
            "https://example.com/b": [response(200, "https://example.com/b", body)],
        })
        fetcher = self.fetcher(
            session,
            cache_enabled=False,
            times=("2026-08-08T00:00:00+00:00", "2026-08-08T00:01:00+00:00"),
        )
        first = fetcher.fetch(
            request=fetcher.build_request("https://example.com/a"),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate-a",
        )
        second = fetcher.fetch(
            request=fetcher.build_request("https://example.com/b"),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate-b",
        )
        self.assertEqual(first.execution.raw_body_sha256, second.execution.raw_body_sha256)
        self.assertNotEqual(first.execution.retrieved_at, second.execution.retrieved_at)

    def test_fetched_page_serialization_does_not_embed_raw_body(self):
        page = FetchedPage(
            fetch_execution_id="fetch",
            response_metadata={"content_type": "text/html"},
            raw_bytes=b"<html>secret body</html>",
            decoded_text="<html>secret body</html>",
            raw_artifact_ref=None,
        )
        payload = str(page.to_dict())
        self.assertNotIn("secret body", payload)
        self.assertIn("runtime_payload_omitted", payload)

    def test_one_failed_source_does_not_stop_batch_and_success_artifacts_remain(self):
        session = FakeSession({
            "https://ok.example": [response(200, "https://ok.example", b"<html>ok</html>")],
            "https://slow.example": [requests.Timeout("slow")],
            "https://denied.example": [response(403, "https://denied.example", b"denied")],
            "https://ok2.example": [response(200, "https://ok2.example", b"<html>ok2</html>")],
        })
        fetcher = self.fetcher(session, cache_enabled=False)
        items = tuple(
            (fetcher.build_request(url), "plan", candidate)
            for url, candidate in (
                ("https://ok.example", "a"),
                ("https://slow.example", "b"),
                ("https://denied.example", "c"),
                ("https://ok2.example", "d"),
            )
        )
        outcomes = execute_source_fetch_requests(
            requests_to_execute=items,
            fetcher=fetcher,
        )
        self.assertEqual(len(outcomes), 4)
        self.assertEqual(
            [item.execution.fetch_status for item in outcomes],
            [
                FetchStatus.COMPLETED_HTML,
                FetchStatus.TIMEOUT,
                FetchStatus.HTTP_FAILURE,
                FetchStatus.COMPLETED_HTML,
            ],
        )
        for outcome in (outcomes[0], outcomes[3]):
            artifact = PROJECT_ROOT / outcome.execution.raw_artifact_ref.artifact_path
            self.assertTrue(artifact.exists())

    def test_compatible_cache_reuse_avoids_second_http_call(self):
        session = FakeSession({
            "https://example.com": [response(200, "https://example.com", b"<html>cached</html>")]
        })
        fetcher = self.fetcher(session)
        request = fetcher.build_request("https://example.com")
        first = fetcher.fetch(
            request=request,
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )

        replay = self.fetcher(RaisingSession())
        second = replay.fetch(
            request=request,
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )

        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(second.execution.raw_body_sha256, first.execution.raw_body_sha256)
        self.assertEqual(second.fetched_page.raw_bytes, first.fetched_page.raw_bytes)

    def test_same_request_for_different_candidates_replays_both_snapshots(self):
        session = FakeSession({
            "https://example.com": [
                response(200, "https://example.com", b"<html>first</html>"),
                response(200, "https://example.com", b"<html>second</html>"),
            ]
        })
        fetcher = self.fetcher(
            session,
            times=(
                "2026-08-08T00:00:00+00:00",
                "2026-08-08T00:01:00+00:00",
            ),
        )
        request = fetcher.build_request("https://example.com")
        first = fetcher.fetch(
            request=request,
            source_evaluation_plan_id="plan_a",
            candidate_source_id="candidate_a",
        )
        second = fetcher.fetch(
            request=request,
            source_evaluation_plan_id="plan_b",
            candidate_source_id="candidate_b",
        )

        replay = self.fetcher(RaisingSession())
        first_replay = replay.fetch(
            request=request,
            source_evaluation_plan_id="plan_a",
            candidate_source_id="candidate_a",
        )
        second_replay = replay.fetch(
            request=request,
            source_evaluation_plan_id="plan_b",
            candidate_source_id="candidate_b",
        )

        self.assertFalse(first.cache_hit)
        self.assertFalse(second.cache_hit)
        self.assertTrue(first_replay.cache_hit)
        self.assertTrue(second_replay.cache_hit)
        self.assertEqual(first_replay.execution.raw_body_sha256, first.execution.raw_body_sha256)
        self.assertEqual(second_replay.execution.raw_body_sha256, second.execution.raw_body_sha256)

    def test_compatible_failure_diagnostic_replay_avoids_second_http_call(self):
        session = FakeSession({
            "https://example.com": [response(403, "https://example.com", b"forbidden")]
        })
        fetcher = self.fetcher(session)
        request = fetcher.build_request("https://example.com")
        first = fetcher.fetch(
            request=request,
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )

        replay = self.fetcher(RaisingSession())
        second = replay.fetch(
            request=request,
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )

        self.assertFalse(first.cache_hit)
        self.assertEqual(first.execution.fetch_status, FetchStatus.HTTP_FAILURE)
        self.assertTrue(second.cache_hit)
        self.assertEqual(second.execution.fetch_status, FetchStatus.HTTP_FAILURE)
        self.assertEqual(second.execution.http_status, 403)
        self.assertIsNone(second.fetched_page)

    def test_relative_failure_root_persists_failure_diagnostic(self):
        short_name = hashlib.sha256(
            f"{self._testMethodName}:relative".encode("utf-8")
        ).hexdigest()[:12]
        relative_root = Path("tmp_phase5b_fetcher_tests") / short_name
        fetcher = SourceFetcher(
            policy=SourceFetchPolicy(
                timeout_seconds=5,
                max_response_bytes=200,
                max_redirects=3,
                artifact_root=relative_root / "raw_pages",
                failure_root=relative_root / "fetch_failures",
                batch_size=10,
            ),
            session=FakeSession({
                "https://example.com": [response(403, "https://example.com", b"forbidden")]
            }),
            now_fn=TimeSequence(("2026-08-08T00:00:00+00:00",)),
            monotonic_fn=MonotonicSequence(),
        )
        request = fetcher.build_request("https://example.com")

        outcome = fetcher.fetch(
            request=request,
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )

        failure_path = (
            PROJECT_ROOT
            / relative_root
            / "fetch_failures"
            / request.request_fingerprint
            / f"{outcome.execution.source_fetch_execution_id}.json"
        )
        self.assertEqual(outcome.execution.fetch_status, FetchStatus.HTTP_FAILURE)
        self.assertTrue(failure_path.exists())

        replay = SourceFetcher(
            policy=SourceFetchPolicy(
                timeout_seconds=5,
                max_response_bytes=200,
                max_redirects=3,
                artifact_root=relative_root / "raw_pages",
                failure_root=relative_root / "fetch_failures",
                batch_size=10,
            ),
            session=RaisingSession(),
            now_fn=TimeSequence(("2026-08-08T00:00:00+00:00",)),
            monotonic_fn=MonotonicSequence(),
        ).fetch(
            request=request,
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )
        self.assertTrue(replay.cache_hit)
        self.assertEqual(replay.execution.fetch_status, FetchStatus.HTTP_FAILURE)

    def test_incompatible_request_fingerprint_does_not_reuse_old_artifact(self):
        session = FakeSession({
            "https://example.com": [response(200, "https://example.com", b"<html>cached</html>")]
        })
        fetcher = self.fetcher(session, max_bytes=200)
        original = fetcher.fetch(
            request=fetcher.build_request("https://example.com"),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )

        changed_policy_session = FakeSession({
            "https://example.com": [response(200, "https://example.com", b"<html>new</html>")]
        })
        changed_fetcher = self.fetcher(changed_policy_session, max_bytes=201)
        changed = changed_fetcher.fetch(
            request=changed_fetcher.build_request("https://example.com"),
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )

        self.assertFalse(changed.cache_hit)
        self.assertEqual(changed_policy_session.call_count, 1)
        self.assertNotEqual(changed.execution.request_fingerprint, original.execution.request_fingerprint)

    def test_malformed_url_produces_network_failure_without_http_call(self):
        session = FakeSession({})
        fetcher = self.fetcher(session)
        request = fetcher.build_request("not a url")
        outcome = fetcher.fetch(
            request=request,
            source_evaluation_plan_id="plan",
            candidate_source_id="candidate",
        )
        self.assertEqual(outcome.execution.fetch_status, FetchStatus.NETWORK_FAILURE)
        self.assertEqual(outcome.execution.error_type, "malformed_url")
        self.assertEqual(session.call_count, 0)


def response(
    status_code: int,
    url: str,
    body_or_chunks,
    *,
    headers: dict[str, str] | None = None,
) -> "FakeResponse":
    default_headers = {"Content-Type": "text/html; charset=utf-8"}
    if headers:
        default_headers.update(headers)
    return FakeResponse(
        status_code=status_code,
        url=url,
        body_or_chunks=body_or_chunks,
        headers=default_headers,
    )


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        body_or_chunks,
        headers: dict[str, str],
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.headers = headers
        self.iterated = False
        self.encoding = None
        self.raise_on_apparent_encoding = False
        if isinstance(body_or_chunks, list):
            self.chunks = body_or_chunks
        else:
            self.chunks = [body_or_chunks]

    def iter_content(self, chunk_size: int = 8192):
        self.iterated = True
        for chunk in self.chunks:
            yield chunk

    @property
    def apparent_encoding(self):
        if self.raise_on_apparent_encoding:
            raise RuntimeError("apparent_encoding attempted to read consumed body")
        return None


class FakeSession:
    def __init__(self, responses_by_url: dict[str, list[object]]) -> None:
        self.responses_by_url = {key: list(value) for key, value in responses_by_url.items()}
        self.calls: list[dict[str, object]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        queue = self.responses_by_url.get(url)
        if not queue:
            raise requests.ConnectionError(f"no fake response for {url}")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class RaisingSession:
    def request(self, *args, **kwargs):
        raise AssertionError("external HTTP call attempted during cache replay")


class TimeSequence:
    def __init__(self, values: tuple[str, ...]) -> None:
        self.values = list(values)

    def __call__(self) -> str:
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


class MonotonicSequence:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.01
        return self.value


if __name__ == "__main__":
    unittest.main()
