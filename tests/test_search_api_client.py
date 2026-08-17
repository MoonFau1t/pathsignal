import unittest
from unittest.mock import patch

import requests

from src.models import SearchPlan, SearchQueryType, SourceType
from src.search_api_client import BraveSearchClient, SearchAPIError


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error

        return self._payload


def build_plan(query_text="strategy analyst", max_results=10):
    return SearchPlan(
        plan_id="plan_1",
        query_id="query_1",
        query_text=query_text,
        query_type=SearchQueryType.JOB_SEARCH,
        career_path_id="path_1",
        career_path_title="AI Strategy",
        scope_id="scope_1",
        source_types=[SourceType.SEARCH_API],
        max_results=max_results,
        priority=1.0,
    )


class BraveSearchClientTests(unittest.TestCase):
    def test_dry_run_works_without_api_key(self):
        client = BraveSearchClient(api_key="", dry_run=True)

        with patch("src.search_api_client.requests.get") as request_get:
            items = client.search(build_plan())

        request_get.assert_not_called()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].metadata["provider"], "brave")
        self.assertEqual(items[0].metadata["mode"], "dry_run")

    def test_real_mode_fails_when_api_key_is_missing(self):
        client = BraveSearchClient(api_key="", dry_run=False)

        with self.assertRaisesRegex(SearchAPIError, "BRAVE_API_KEY"):
            client.search(build_plan())

    def test_successful_brave_response_is_converted_to_raw_items(self):
        payload = {
            "web": {
                "results": [
                    {
                        "title": "Strategy Analyst",
                        "url": "https://example.com/jobs/1",
                        "description": "A relevant strategy role.",
                        "age": "2 days ago",
                        "page_age": "2026-07-15T00:00:00",
                        "language": "en",
                        "profile": {"name": "Example"},
                        "meta_url": {"hostname": "example.com"},
                    }
                ]
            }
        }
        client = BraveSearchClient(api_key="key", dry_run=False)

        with patch(
            "src.search_api_client.requests.get",
            return_value=FakeResponse(payload=payload),
        ) as request_get:
            items = client.search(build_plan(max_results=50))

        request_get.assert_called_once()
        params = request_get.call_args.kwargs["params"]
        self.assertEqual(params["q"], "strategy analyst")
        self.assertEqual(params["count"], 20)
        self.assertEqual(params["result_filter"], "web")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.source_type, SourceType.SEARCH_API)
        self.assertEqual(item.title, "Strategy Analyst")
        self.assertEqual(item.organization, "example.com")
        self.assertEqual(item.url, "https://example.com/jobs/1")
        self.assertEqual(item.raw_text, "A relevant strategy role.")
        self.assertIsNone(item.published_at)
        self.assertEqual(item.metadata["provider"], "brave")
        self.assertEqual(item.metadata["search_plan_id"], "plan_1")
        self.assertEqual(item.metadata["age"], "2 days ago")
        self.assertEqual(item.metadata["page_age"], "2026-07-15T00:00:00")
        self.assertIn("raw_result", item.metadata)

    def test_missing_optional_fields_are_handled_safely(self):
        payload = {
            "web": {
                "results": [
                    {
                        "title": None,
                        "url": "https://www.example.com/news",
                    }
                ]
            }
        }
        client = BraveSearchClient(api_key="key", dry_run=False)

        with patch(
            "src.search_api_client.requests.get",
            return_value=FakeResponse(payload=payload),
        ):
            items = client.search(build_plan())

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Untitled search result")
        self.assertEqual(items[0].organization, "example.com")
        self.assertEqual(items[0].raw_text, "")

    def test_missing_web_field_is_handled(self):
        client = BraveSearchClient(api_key="key", dry_run=False)

        with patch(
            "src.search_api_client.requests.get",
            return_value=FakeResponse(payload={}),
        ):
            with self.assertRaisesRegex(SearchAPIError, "malformed provider payload.*missing web"):
                client.search(build_plan())

    def test_malformed_web_results_field_is_handled(self):
        client = BraveSearchClient(api_key="key", dry_run=False)
        malformed_payloads = (
            {"web": {}},
            {"web": {"results": None}},
            {"web": {"results": {"url": "https://example.com"}}},
            {"web": "not-an-object"},
            [],
        )

        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                with patch(
                    "src.search_api_client.requests.get",
                    return_value=FakeResponse(payload=payload),
                ):
                    with self.assertRaisesRegex(
                        SearchAPIError,
                        "malformed provider payload",
                    ):
                        client.search(build_plan())

    def test_empty_results_returns_empty_list(self):
        client = BraveSearchClient(api_key="key", dry_run=False)

        with patch(
            "src.search_api_client.requests.get",
            return_value=FakeResponse(payload={"web": {"results": []}}),
        ):
            self.assertEqual(client.search(build_plan()), [])

    def test_http_401_and_403_are_handled(self):
        for status_code in (401, 403):
            with self.subTest(status_code=status_code):
                client = BraveSearchClient(api_key="key", dry_run=False)
                with patch(
                    "src.search_api_client.requests.get",
                    return_value=FakeResponse(status_code=status_code),
                ):
                    with self.assertRaisesRegex(SearchAPIError, "authentication"):
                        client.search(build_plan())

    def test_http_429_is_handled(self):
        client = BraveSearchClient(api_key="key", dry_run=False)

        with patch(
            "src.search_api_client.requests.get",
            return_value=FakeResponse(status_code=429),
        ):
            with self.assertRaisesRegex(SearchAPIError, "rate limit"):
                client.search(build_plan())

    def test_http_5xx_is_handled(self):
        client = BraveSearchClient(api_key="key", dry_run=False)

        with patch(
            "src.search_api_client.requests.get",
            return_value=FakeResponse(status_code=503),
        ):
            with self.assertRaisesRegex(SearchAPIError, "server error"):
                client.search(build_plan())

    def test_timeout_is_handled(self):
        client = BraveSearchClient(api_key="key", dry_run=False)

        with patch(
            "src.search_api_client.requests.get",
            side_effect=requests.Timeout,
        ):
            with self.assertRaisesRegex(SearchAPIError, "timed out"):
                client.search(build_plan())

    def test_connection_error_is_handled(self):
        client = BraveSearchClient(api_key="key", dry_run=False)

        with patch(
            "src.search_api_client.requests.get",
            side_effect=requests.ConnectionError,
        ):
            with self.assertRaisesRegex(SearchAPIError, "connection failed"):
                client.search(build_plan())

    def test_invalid_json_is_handled(self):
        client = BraveSearchClient(api_key="key", dry_run=False)

        with patch(
            "src.search_api_client.requests.get",
            return_value=FakeResponse(json_error=ValueError("bad json")),
        ):
            with self.assertRaisesRegex(SearchAPIError, "invalid JSON"):
                client.search(build_plan())

    def test_empty_query_is_rejected(self):
        client = BraveSearchClient(api_key="key", dry_run=True)

        with self.assertRaisesRegex(SearchAPIError, "empty query"):
            client.search(build_plan(query_text="  "))


if __name__ == "__main__":
    unittest.main()
