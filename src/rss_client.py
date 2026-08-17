from typing import Any

import feedparser
import requests

from src.models import RSSFeed, RawItem, SearchPlan, SourceType, utc_now_iso


class RSSClient:
    """
    RSS client for reading configured RSS feeds.

    Phase 8 converts RSS entries into RawItem objects.
    """

    def __init__(
        self,
        timeout_seconds: int = 20,
        dry_run: bool = True,
        max_items_per_feed: int = 5,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.dry_run = dry_run
        self.max_items_per_feed = max_items_per_feed

    def fetch_feed(
        self,
        rss_feed: RSSFeed,
        search_plans: list[SearchPlan],
    ) -> list[RawItem]:
        """
        Fetch one RSS feed and convert matching entries into RawItem objects.
        """

        if self.dry_run:
            return self._build_dry_run_items(rss_feed, search_plans)

        response = requests.get(
            rss_feed.url,
            timeout=self.timeout_seconds,
            headers={
                "User-Agent": "AgentWorkFlow/0.1 RSS Reader"
            },
        )
        response.raise_for_status()

        parsed_feed = feedparser.parse(response.content)

        raw_items: list[RawItem] = []

        for position, entry in enumerate(parsed_feed.entries, start=1):
            if len(raw_items) >= self.max_items_per_feed:
                break

            title = _safe_get(entry, "title")
            link = _safe_get(entry, "link")
            summary = _safe_get(entry, "summary")
            published_at = (
                _safe_get(entry, "published")
                or _safe_get(entry, "updated")
                or utc_now_iso()
            )

            if not title and not link:
                continue

            if not _matches_search_context(
                text=f"{title} {summary}",
                search_plans=search_plans,
            ):
                continue

            raw_items.append(
                RawItem(
                    source_type=SourceType.RSS,
                    title=title or "Untitled RSS item",
                    organization=rss_feed.name,
                    url=link,
                    published_at=published_at,
                    raw_text=summary or title,
                    metadata={
                        "provider": "rss",
                        "feed_name": rss_feed.name,
                        "feed_url": rss_feed.url,
                        "position": position,
                        "raw_entry": _entry_to_dict(entry),
                    },
                )
            )

        return raw_items

    def _build_dry_run_items(
        self,
        rss_feed: RSSFeed,
        search_plans: list[SearchPlan],
    ) -> list[RawItem]:
        """
        Return fake RSS RawItems for local testing.
        """

        sample_query = (
            search_plans[0].query_text
            if search_plans
            else "career intelligence"
        )

        return [
            RawItem(
                source_type=SourceType.RSS,
                title=f"[DRY RUN] RSS signal from {rss_feed.name}",
                organization=rss_feed.name,
                url=rss_feed.url,
                published_at=utc_now_iso(),
                raw_text=(
                    "This is a dry-run RSS item. "
                    f"It is connected to search context: {sample_query}"
                ),
                metadata={
                    "provider": "rss",
                    "mode": "dry_run",
                    "feed_name": rss_feed.name,
                    "feed_url": rss_feed.url,
                },
            )
        ]


def execute_rss_feeds(
    rss_feeds: list[RSSFeed],
    search_plans: list[SearchPlan],
    client: RSSClient,
    max_feeds: int = 5,
    execution_lifecycle: Any | None = None,
) -> tuple[list[RawItem], int]:
    """
    Execute RSS feeds and return RawItem results.

    Individual feed failures are skipped in Phase 8.
    Proper logging will be added in Phase 15.
    """

    selected_feeds = rss_feeds[:max_feeds]

    all_raw_items: list[RawItem] = []
    executed_count = 0

    for rss_feed in selected_feeds:
        source_execution_id = None
        if execution_lifecycle is not None:
            source_execution_id = (
                execution_lifecycle.start_config_source_execution(
                    source_type=SourceType.RSS,
                    source_key=rss_feed.url,
                    source_name=rss_feed.name,
                    source_locator=rss_feed.url,
                    provider="rss",
                    execution_mode=(
                        "dry_run" if client.dry_run else "live"
                    ),
                    requested_result_limit=client.max_items_per_feed,
                    metadata={"notes": rss_feed.notes},
                )
            )
        try:
            feed_items = client.fetch_feed(
                rss_feed=rss_feed,
                search_plans=search_plans,
            )
        except Exception as error:
            if execution_lifecycle is not None:
                execution_lifecycle.fail_source_execution(
                    source_execution_id=source_execution_id,
                    error=error,
                )
            continue

        if execution_lifecycle is not None:
            execution_lifecycle.complete_source_execution(
                source_execution_id=source_execution_id,
                raw_items=feed_items,
            )
        all_raw_items.extend(feed_items)
        executed_count += 1

    return all_raw_items, executed_count


def _safe_get(entry: Any, key: str) -> str:
    """
    Safely get a string field from a feedparser entry.
    """

    value = entry.get(key, "")
    return str(value).strip() if value is not None else ""


def _entry_to_dict(entry: Any) -> dict:
    """
    Convert feedparser entry to a regular dictionary.
    """

    return {
        key: entry.get(key)
        for key in entry.keys()
    }


def _matches_search_context(
    text: str,
    search_plans: list[SearchPlan],
) -> bool:
    """
    Check whether an RSS entry roughly matches the current search context.

    If no search plans exist, keep the item.
    """

    if not search_plans:
        return True

    text_lower = text.lower()

    for plan in search_plans[:10]:
        terms = _extract_terms(plan.query_text)

        if any(term in text_lower for term in terms):
            return True

    return False


def _extract_terms(query_text: str) -> list[str]:
    """
    Extract simple searchable terms from a query.
    """

    stopwords = {
        "or",
        "and",
        "the",
        "a",
        "an",
        "open",
        "role",
        "china",
        "hong",
        "kong",
        "singapore",
        "remote",
    }

    words = [
        word.strip("()[],.").lower()
        for word in query_text.split()
    ]

    return [
        word
        for word in words
        if len(word) >= 3 and word not in stopwords
    ]
