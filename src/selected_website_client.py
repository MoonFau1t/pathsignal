from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.models import RawItem, SearchPlan, SelectedWebsite, SourceType, utc_now_iso


class SelectedWebsiteClient:
    """
    Client for reading manually selected websites.

    Phase 8 does not perform deep crawling.
    It only reads configured pages and extracts lightweight signals.
    """

    def __init__(
        self,
        timeout_seconds: int = 20,
        dry_run: bool = True,
        max_items_per_site: int = 5,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.dry_run = dry_run
        self.max_items_per_site = max_items_per_site

    def fetch_website(
        self,
        website: SelectedWebsite,
        search_plans: list[SearchPlan],
    ) -> list[RawItem]:
        """
        Fetch one selected website and convert page/link information into RawItems.
        """

        if self.dry_run:
            return self._build_dry_run_items(website, search_plans)

        response = requests.get(
            website.url,
            timeout=self.timeout_seconds,
            headers={
                "User-Agent": "AgentWorkFlow/0.1 Selected Website Reader"
            },
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        page_title = _get_page_title(soup)
        page_description = _get_meta_description(soup)

        link_items = self._extract_matching_links(
            website=website,
            soup=soup,
            search_plans=search_plans,
        )

        if link_items:
            return link_items

        return [
            RawItem(
                source_type=SourceType.SELECTED_WEBSITE,
                title=page_title or f"Selected website: {website.name}",
                organization=website.name,
                url=website.url,
                published_at=utc_now_iso(),
                raw_text=page_description or page_title or website.notes,
                metadata={
                    "provider": "selected_website",
                    "website_name": website.name,
                    "website_url": website.url,
                    "notes": website.notes,
                    "extraction_mode": "page_summary",
                },
            )
        ]

    def _extract_matching_links(
        self,
        website: SelectedWebsite,
        soup: BeautifulSoup,
        search_plans: list[SearchPlan],
    ) -> list[RawItem]:
        """
        Extract links whose anchor text roughly matches search context.
        """

        raw_items: list[RawItem] = []

        anchors = soup.find_all("a", href=True)

        for position, anchor in enumerate(anchors, start=1):
            if len(raw_items) >= self.max_items_per_site:
                break

            anchor_text = anchor.get_text(" ", strip=True)
            href = anchor.get("href", "")

            if not anchor_text or not href:
                continue

            if not _matches_search_context(anchor_text, search_plans):
                continue

            absolute_url = urljoin(website.url, href)

            raw_items.append(
                RawItem(
                    source_type=SourceType.SELECTED_WEBSITE,
                    title=anchor_text,
                    organization=website.name,
                    url=absolute_url,
                    published_at=utc_now_iso(),
                    raw_text=anchor_text,
                    metadata={
                        "provider": "selected_website",
                        "website_name": website.name,
                        "website_url": website.url,
                        "position": position,
                        "extraction_mode": "matching_link",
                    },
                )
            )

        return raw_items

    def _build_dry_run_items(
        self,
        website: SelectedWebsite,
        search_plans: list[SearchPlan],
    ) -> list[RawItem]:
        """
        Return fake selected website RawItems for local testing.
        """

        sample_query = (
            search_plans[0].query_text
            if search_plans
            else "career intelligence"
        )

        return [
            RawItem(
                source_type=SourceType.SELECTED_WEBSITE,
                title=f"[DRY RUN] Selected website signal from {website.name}",
                organization=website.name,
                url=website.url,
                published_at=utc_now_iso(),
                raw_text=(
                    "This is a dry-run selected website item. "
                    f"It is connected to search context: {sample_query}"
                ),
                metadata={
                    "provider": "selected_website",
                    "mode": "dry_run",
                    "website_name": website.name,
                    "website_url": website.url,
                    "notes": website.notes,
                },
            )
        ]


def execute_selected_websites(
    selected_websites: list[SelectedWebsite],
    search_plans: list[SearchPlan],
    client: SelectedWebsiteClient,
    max_sites: int = 5,
    execution_lifecycle: Any | None = None,
) -> tuple[list[RawItem], int]:
    """
    Execute selected website reads and return RawItem results.

    Individual website failures are skipped in Phase 8.
    Proper logging will be added in Phase 15.
    """

    selected_sites = selected_websites[:max_sites]

    all_raw_items: list[RawItem] = []
    executed_count = 0

    for website in selected_sites:
        source_execution_id = None
        if execution_lifecycle is not None:
            source_execution_id = (
                execution_lifecycle.start_config_source_execution(
                    source_type=SourceType.SELECTED_WEBSITE,
                    source_key=website.url,
                    source_name=website.name,
                    source_locator=website.url,
                    provider="selected_website",
                    execution_mode=(
                        "dry_run" if client.dry_run else "live"
                    ),
                    requested_result_limit=client.max_items_per_site,
                    metadata={"notes": website.notes},
                )
            )
        try:
            website_items = client.fetch_website(
                website=website,
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
                raw_items=website_items,
            )
        all_raw_items.extend(website_items)
        executed_count += 1

    return all_raw_items, executed_count


def _get_page_title(soup: BeautifulSoup) -> str:
    """
    Extract page title from HTML.
    """

    if soup.title and soup.title.string:
        return soup.title.string.strip()

    return ""


def _get_meta_description(soup: BeautifulSoup) -> str:
    """
    Extract meta description from HTML.
    """

    meta = soup.find("meta", attrs={"name": "description"})

    if meta and meta.get("content"):
        return str(meta["content"]).strip()

    return ""


def _matches_search_context(
    text: str,
    search_plans: list[SearchPlan],
) -> bool:
    """
    Check whether link text roughly matches current search context.

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
