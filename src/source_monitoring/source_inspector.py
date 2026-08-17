from dataclasses import dataclass
import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.config import PROJECT_ROOT
from src.database.planning_identity import hash_canonical_value
from src.source_monitoring.source_discovery_identity import (
    normalize_source_url,
    root_domain_from_url,
)
from src.source_monitoring.source_discovery_models import SourceFormatHint
from src.source_monitoring.source_evaluation_identity import (
    build_semantic_text_window_id,
    build_source_inspection_id,
)
from src.source_monitoring.source_evaluation_models import (
    SOURCE_INSPECTOR_VERSION,
    SOURCE_SEMANTIC_WINDOW_POLICY_VERSION,
    UNTRUSTED_WEBPAGE_EVIDENCE_MARKER,
    FeedLinkHint,
    FetchedPage,
    FetchStatus,
    RawPageArtifactRef,
    SemanticTextWindow,
    SemanticTextWindowType,
    SourceFetchExecution,
    SourceInspection,
)


SOURCE_INSPECTION_POLICY_VERSION = "source_inspection_policy_v1"
HTML_INSPECTABLE_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
INSPECTION_ARTIFACT_ROOT = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase5_source_evaluation"
    / "inspections"
)

EXCLUDED_TEXT_TAGS = ("script", "style", "noscript", "template", "svg")
BOILERPLATE_TAGS = ("script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside")
IGNORED_LINK_SCHEMES = {"", "javascript", "data", "mailto", "tel"}
RSS_MIME_TYPES = {"application/rss+xml", "application/rdf+xml"}
ATOM_MIME_TYPES = {"application/atom+xml"}

HINT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "article": ("article", "articles", "story", "stories", "news", "press", "新闻", "资讯", "文章"),
    "job": ("career", "careers", "job", "jobs", "join", "recruit", "招聘", "职位", "人才", "加入"),
    "report": ("report", "reports", "research", "publication", "paper", "whitepaper", "data", "报告", "研究", "论文", "数据", "洞察"),
    "event": ("event", "events", "webinar", "conference", "program", "programs", "活动", "会议", "项目"),
}
SECTION_KEYWORDS = ("about", "overview", "insights", "research", "news", "media", "resources", "关于", "概况", "资讯")
DETAIL_KEYWORDS = ("article", "detail", "story", "press-release", "report", "event", "post", "content")
PAGINATION_KEYWORDS = ("next", "prev", "previous", "more", "下一页", "上一页", "更多")


@dataclass(frozen=True)
class SourceInspectionPolicy:
    max_window_chars: int = 2000
    max_total_semantic_chars: int = 12000
    max_windows: int = 8
    max_headings: int = 30
    max_navigation_labels: int = 30
    max_representative_links: int = 30
    max_hint_entries_per_category: int = 30
    max_feed_hints: int = 20
    max_jsonld_blocks: int = 8
    max_jsonld_block_chars: int = 20000
    max_structured_data_values: int = 40
    max_main_excerpt_chars: int = 4000
    client_rendering_text_threshold: int = 160
    client_rendering_script_threshold: int = 3
    parser: str = "lxml"
    inspector_version: str = SOURCE_INSPECTOR_VERSION
    policy_version: str = SOURCE_INSPECTION_POLICY_VERSION
    semantic_window_policy_version: str = SOURCE_SEMANTIC_WINDOW_POLICY_VERSION


@dataclass(frozen=True)
class SourceInspectionOutcome:
    inspection: SourceInspection | None
    inspectable: bool
    skipped_reason: str | None
    diagnostics: dict[str, Any]


class SourceInspectionError(Exception):
    """
    Raised for deterministic Phase 5C inspection programming/input errors.
    """


class SourceInspector:
    """
    Deterministic offline inspector for successful Phase 5B HTML snapshots.
    """

    def __init__(self, policy: SourceInspectionPolicy | None = None) -> None:
        self.policy = policy or SourceInspectionPolicy()

    def inspect_fetch_execution(self, execution: SourceFetchExecution) -> SourceInspectionOutcome:
        if execution.raw_artifact_ref is None:
            return _skipped("no_raw_artifact", execution)
        raw_bytes = _read_artifact_bytes(execution.raw_artifact_ref)
        page = FetchedPage(
            fetch_execution_id=execution.source_fetch_execution_id,
            response_metadata={},
            raw_bytes=raw_bytes,
            decoded_text=_decode_bytes(raw_bytes, execution.detected_encoding),
            raw_artifact_ref=execution.raw_artifact_ref,
        )
        return self.inspect_page(fetch_execution=execution, fetched_page=page)

    def inspect_page(
        self,
        *,
        fetch_execution: SourceFetchExecution,
        fetched_page: FetchedPage,
    ) -> SourceInspectionOutcome:
        diagnostics: dict[str, Any] = {
            "parser": self.policy.parser,
            "http_calls": 0,
            "brave_calls": 0,
            "deepseek_calls": 0,
        }
        skip_reason = _skip_reason(fetch_execution)
        if skip_reason is not None:
            return SourceInspectionOutcome(
                inspection=None,
                inspectable=False,
                skipped_reason=skip_reason,
                diagnostics={**diagnostics, "skipped_reason": skip_reason},
            )
        if fetched_page.raw_artifact_ref is None:
            return SourceInspectionOutcome(
                inspection=None,
                inspectable=False,
                skipped_reason="missing_runtime_artifact_ref",
                diagnostics={**diagnostics, "skipped_reason": "missing_runtime_artifact_ref"},
            )
        body_sha = hashlib.sha256(fetched_page.raw_bytes).hexdigest()
        if body_sha != fetch_execution.raw_body_sha256:
            raise SourceInspectionError("FetchedPage raw bytes do not match SourceFetchExecution raw_body_sha256.")
        html_text = fetched_page.decoded_text or _decode_bytes(
            fetched_page.raw_bytes,
            fetch_execution.detected_encoding,
        )
        soup = BeautifulSoup(html_text, self.policy.parser)
        base_url, base_diagnostics = _base_url(soup, fetch_execution.final_url)
        diagnostics.update(base_diagnostics)
        script_count = len(soup.find_all("script"))
        jsonld = _extract_jsonld(soup, self.policy)
        metadata = _extract_metadata(soup, base_url)
        clean_soup = _content_soup(soup)
        headings = _headings(clean_soup, self.policy.max_headings)
        nav_labels = _navigation_labels(clean_soup, self.policy.max_navigation_labels)
        link_info = _links(clean_soup, base_url, fetch_execution.final_url, self.policy)
        feed_hints = _feed_hints(soup, base_url, self.policy)
        visible_text = _visible_text(clean_soup, self.policy)
        main_excerpt = visible_text[: self.policy.max_main_excerpt_chars]
        client_rendering_hint = (
            len(visible_text) < self.policy.client_rendering_text_threshold
            and script_count >= self.policy.client_rendering_script_threshold
        )

        root_domain = root_domain_from_url(fetch_execution.final_url)
        canonical_root_domain = (
            root_domain_from_url(metadata["canonical_url"])
            if metadata["canonical_url"]
            else None
        )
        source_format_hints = _source_format_hints(feed_hints)
        input_fingerprint = _inspection_input_fingerprint(
            fetch_execution=fetch_execution,
            policy=self.policy,
        )
        inspection_id = build_source_inspection_id(
            fetch_execution_id=fetch_execution.source_fetch_execution_id,
            candidate_source_id=fetch_execution.candidate_source_id,
            raw_body_sha256=body_sha,
            inspection_input_fingerprint=input_fingerprint,
        )
        windows, truncated = _semantic_windows(
            inspection_id=inspection_id,
            title=metadata["page_title"],
            meta_description=metadata["meta_description"],
            headings=headings,
            navigation_labels=nav_labels,
            main_excerpt=main_excerpt,
            representative_links=link_info["representative_links"],
            structured_types=jsonld["types"],
            structured_names=jsonld["organization_names"],
            policy=self.policy,
        )
        inspection_payload = {
            "inspection_id": inspection_id,
            "fetch_execution_id": fetch_execution.source_fetch_execution_id,
            "candidate_source_id": fetch_execution.candidate_source_id,
            "requested_url": fetch_execution.requested_url,
            "final_url": fetch_execution.final_url,
            "canonical_url": metadata["canonical_url"],
            "root_domain": root_domain,
            "canonical_root_domain": canonical_root_domain,
            "page_title": metadata["page_title"],
            "meta_description": metadata["meta_description"],
            "html_language": metadata["html_language"],
            "content_language": fetch_execution.content_language,
            "open_graph_title": metadata["open_graph_title"],
            "open_graph_description": metadata["open_graph_description"],
            "structured_data_types": tuple(jsonld["types"]),
            "structured_data_organization_names": tuple(jsonld["organization_names"]),
            "heading_summary": tuple(headings),
            "navigation_labels": tuple(nav_labels),
            "internal_link_count": link_info["internal_link_count"],
            "external_link_count": link_info["external_link_count"],
            "same_domain_link_count": link_info["same_domain_link_count"],
            "has_pagination_hints": link_info["has_pagination_hints"],
            "has_article_link_hints": bool(link_info["article_link_hints"]),
            "has_job_link_hints": bool(link_info["job_link_hints"]),
            "has_report_link_hints": bool(link_info["report_link_hints"]),
            "has_event_link_hints": bool(link_info["event_link_hints"]),
            "has_section_hub_hints": bool(link_info["section_hub_hints"]),
            "has_detail_page_hints": bool(link_info["detail_page_hints"]),
            "feed_link_hints": tuple(feed_hints),
            "source_format_hints": tuple(source_format_hints),
            "visible_text_length": len(visible_text),
            "semantic_text_windows": tuple(windows),
            "semantic_content_truncated": truncated or len(visible_text) > len(main_excerpt),
            "client_rendering_required_hint": client_rendering_hint,
            "inspector_version": self.policy.inspector_version,
            "raw_body_sha256": body_sha,
            "raw_artifact_ref": fetch_execution.raw_artifact_ref,
            "inspection_input_fingerprint": input_fingerprint,
            "inspection_output_hash": "",
        }
        output_hash = hash_canonical_value(
            {
                "schema_version": "source_inspection_output_hash_v1",
                **_json_ready_for_hash(inspection_payload),
            }
        )
        inspection = SourceInspection(
            **{**inspection_payload, "inspection_output_hash": output_hash}
        )
        diagnostics.update(
            {
                "jsonld_block_count": jsonld["block_count"],
                "valid_jsonld_block_count": jsonld["valid_block_count"],
                "malformed_jsonld_block_count": jsonld["malformed_block_count"],
                "heading_count": len(headings),
                "navigation_label_count": len(nav_labels),
                "valid_http_link_count": link_info["valid_http_link_count"],
                "article_link_hint_count": len(link_info["article_link_hints"]),
                "job_link_hint_count": len(link_info["job_link_hints"]),
                "report_link_hint_count": len(link_info["report_link_hints"]),
                "event_link_hint_count": len(link_info["event_link_hints"]),
                "section_hub_hint_count": len(link_info["section_hub_hints"]),
                "detail_page_hint_count": len(link_info["detail_page_hints"]),
                "feed_link_hint_count": len(feed_hints),
                "script_count": script_count,
                "main_present": clean_soup.find("main") is not None,
                "nav_present": clean_soup.find("nav") is not None,
                "article_count": len(clean_soup.find_all("article")),
                "section_count": len(clean_soup.find_all("section")),
                "aside_count": len(soup.find_all("aside")),
                "decoded_html_characters": len(html_text),
                "visible_text_length": len(visible_text),
                "semantic_window_count": len(windows),
                "semantic_window_total_chars": sum(window.character_count for window in windows),
                "semantic_content_truncated": inspection.semantic_content_truncated,
                "client_rendering_required_hint": client_rendering_hint,
                "replacement_character_count": html_text.count("\ufffd"),
            }
        )
        return SourceInspectionOutcome(
            inspection=inspection,
            inspectable=True,
            skipped_reason=None,
            diagnostics=diagnostics,
        )


def inspect_source_pages(
    *,
    fetch_executions: tuple[SourceFetchExecution, ...],
    inspector: SourceInspector | None = None,
) -> tuple[SourceInspectionOutcome, ...]:
    active_inspector = inspector or SourceInspector()
    return tuple(active_inspector.inspect_fetch_execution(item) for item in fetch_executions)


def persist_inspection_checkpoint(
    *,
    outcome: SourceInspectionOutcome,
    output_root: Path = INSPECTION_ARTIFACT_ROOT,
) -> Path | None:
    if outcome.inspection is None:
        return None
    path = output_root / outcome.inspection.inspection_id / "inspection.json"
    _mkdir(path.parent)
    payload = {
        "inspection": outcome.inspection.to_dict(),
        "diagnostics": outcome.diagnostics,
        "checkpoint_model": "deterministic_recomputable_snapshot",
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if _path_exists(path) and _read_text(path) == text:
        return path
    _write_text(path, text)
    return path


def _skip_reason(execution: SourceFetchExecution) -> str | None:
    if execution.fetch_status != FetchStatus.COMPLETED_HTML:
        return f"fetch_status_not_html:{execution.fetch_status.value}"
    if not _is_html_content_type(execution.content_type):
        return f"content_type_not_html:{execution.content_type or 'none'}"
    if execution.raw_artifact_ref is None:
        return "missing_raw_artifact"
    if execution.raw_body_sha256 is None:
        return "missing_raw_body_sha256"
    return None


def _skipped(reason: str, execution: SourceFetchExecution) -> SourceInspectionOutcome:
    return SourceInspectionOutcome(
        inspection=None,
        inspectable=False,
        skipped_reason=reason,
        diagnostics={
            "skipped_reason": reason,
            "fetch_status": execution.fetch_status.value,
            "content_type": execution.content_type,
            "http_calls": 0,
            "brave_calls": 0,
            "deepseek_calls": 0,
        },
    )


def _is_html_content_type(content_type: str | None) -> bool:
    value = (content_type or "").split(";", 1)[0].strip().casefold()
    return value in HTML_INSPECTABLE_CONTENT_TYPES


def _read_artifact_bytes(ref: RawPageArtifactRef) -> bytes:
    path = PROJECT_ROOT / ref.artifact_path
    body = _read_bytes(path)
    if hashlib.sha256(body).hexdigest() != ref.sha256:
        raise SourceInspectionError("Raw artifact bytes do not match RawPageArtifactRef sha256.")
    return body


def _filesystem_path(path: Path) -> Path:
    resolved = path.resolve(strict=False) if path.is_absolute() else (PROJECT_ROOT / path).resolve(strict=False)
    if os.name != "nt":
        return resolved
    text = str(resolved)
    if text.startswith("\\\\?\\"):
        return Path(text)
    return Path(f"\\\\?\\{text}")


def _path_exists(path: Path) -> bool:
    return _filesystem_path(path).exists()


def _mkdir(path: Path) -> None:
    _filesystem_path(path).mkdir(parents=True, exist_ok=True)


def _read_bytes(path: Path) -> bytes:
    return _filesystem_path(path).read_bytes()


def _read_text(path: Path) -> str:
    return _filesystem_path(path).read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    filesystem_path = _filesystem_path(path)
    filesystem_path.parent.mkdir(parents=True, exist_ok=True)
    filesystem_path.write_text(text, encoding="utf-8")


def _decode_bytes(body: bytes, encoding: str | None) -> str:
    for candidate in (*_sniff_html_encodings(body), encoding, "utf-8", "gb18030", "latin-1"):
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _sniff_html_encodings(body: bytes) -> tuple[str, ...]:
    head = body[:4096]
    if head.startswith(b"\xef\xbb\xbf"):
        return ("utf-8-sig",)
    values: list[str] = []
    for pattern in (
        rb"<meta[^>]+charset\s*=\s*['\"]?\s*([A-Za-z0-9._:-]+)",
        rb"<meta[^>]+content\s*=\s*['\"][^'\"]*charset\s*=\s*([A-Za-z0-9._:-]+)",
    ):
        for match in re.finditer(pattern, head, flags=re.IGNORECASE):
            value = match.group(1).decode("ascii", errors="ignore").strip()
            if value:
                values.append(value)
    return tuple(_dedupe(values))


def _base_url(soup: BeautifulSoup, final_url: str) -> tuple[str, dict[str, Any]]:
    base_tag = soup.find("base", href=True)
    if not base_tag:
        return final_url, {"base_href_used": False}
    raw_href = str(base_tag.get("href", "")).strip()
    resolved = urljoin(final_url, raw_href)
    if _is_http_url(resolved):
        return resolved, {"base_href_used": True, "base_href": raw_href}
    return final_url, {"base_href_used": False, "invalid_base_href": raw_href}


def _extract_metadata(soup: BeautifulSoup, base_url: str) -> dict[str, str | None]:
    title_tag = soup.find("title")
    raw_canonical = _first_attr(soup, "link", "href", rel="canonical")
    canonical_url = None
    if raw_canonical:
        resolved = urljoin(base_url, raw_canonical)
        if _is_http_url(resolved):
            canonical_url = normalize_source_url(resolved)
    return {
        "page_title": _clean_text(title_tag.get_text(" ", strip=True)) if title_tag else None,
        "meta_description": _meta_content(soup, attrs=("name",), values=("description",)),
        "open_graph_title": _meta_content(soup, attrs=("property",), values=("og:title",)),
        "open_graph_description": _meta_content(soup, attrs=("property",), values=("og:description",)),
        "canonical_url": canonical_url,
        "html_language": _clean_text(str(soup.html.get("lang"))) if soup.html and soup.html.get("lang") else None,
    }


def _meta_content(soup: BeautifulSoup, *, attrs: tuple[str, ...], values: tuple[str, ...]) -> str | None:
    wanted = {item.casefold() for item in values}
    for tag in soup.find_all("meta"):
        for attr in attrs:
            if str(tag.get(attr, "")).casefold() in wanted:
                return _clean_text(str(tag.get("content", ""))) or None
    return None


def _first_attr(soup: BeautifulSoup, tag_name: str, attr_name: str, **attrs: str) -> str | None:
    for tag in soup.find_all(tag_name):
        matched = True
        for key, value in attrs.items():
            actual = tag.get(key)
            if key == "rel":
                actual_values = [str(item).casefold() for item in actual] if isinstance(actual, list) else str(actual or "").casefold().split()
                matched = value.casefold() in actual_values
            else:
                matched = str(actual or "").casefold() == value.casefold()
            if not matched:
                break
        if matched and tag.get(attr_name):
            return str(tag.get(attr_name))
    return None


def _extract_jsonld(soup: BeautifulSoup, policy: SourceInspectionPolicy) -> dict[str, Any]:
    types: list[str] = []
    names: list[str] = []
    block_count = 0
    valid_count = 0
    malformed_count = 0
    for script in soup.find_all("script"):
        script_type = str(script.get("type", "")).split(";", 1)[0].strip().casefold()
        if script_type != "application/ld+json":
            continue
        block_count += 1
        if block_count > policy.max_jsonld_blocks:
            continue
        text = script.string if script.string is not None else script.get_text()
        text = text[: policy.max_jsonld_block_chars]
        try:
            payload = json.loads(text)
        except Exception:
            malformed_count += 1
            continue
        valid_count += 1
        _collect_jsonld_values(payload, types, names, policy)
    return {
        "block_count": block_count,
        "valid_block_count": valid_count,
        "malformed_block_count": malformed_count,
        "types": _dedupe(types)[: policy.max_structured_data_values],
        "organization_names": _dedupe(names)[: policy.max_structured_data_values],
    }


def _collect_jsonld_values(payload: Any, types: list[str], names: list[str], policy: SourceInspectionPolicy) -> None:
    if len(types) >= policy.max_structured_data_values and len(names) >= policy.max_structured_data_values:
        return
    if isinstance(payload, list):
        for item in payload:
            _collect_jsonld_values(item, types, names, policy)
        return
    if not isinstance(payload, dict):
        return
    type_value = payload.get("@type")
    type_values = type_value if isinstance(type_value, list) else [type_value]
    for item in type_values:
        if item:
            types.append(_clean_text(str(item)))
    lower_types = {str(item).casefold() for item in type_values if item}
    if lower_types & {"organization", "corporation", "localbusiness", "educationalorganization", "governmentorganization"}:
        name = payload.get("name")
        if isinstance(name, str):
            names.append(_clean_text(name))
    for key in ("publisher", "author", "provider", "creator"):
        value = payload.get(key)
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            names.append(_clean_text(value["name"]))
    graph = payload.get("@graph")
    if graph is not None:
        _collect_jsonld_values(graph, types, names, policy)
    for value in payload.values():
        if isinstance(value, (dict, list)):
            _collect_jsonld_values(value, types, names, policy)


def _content_soup(soup: BeautifulSoup) -> BeautifulSoup:
    clean_soup = copy.copy(soup)
    for tag_name in EXCLUDED_TEXT_TAGS:
        for tag in clean_soup.find_all(tag_name):
            tag.decompose()
    return clean_soup


def _headings(soup: BeautifulSoup, limit: int) -> list[str]:
    values: list[str] = []
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = _clean_text(tag.get_text(" ", strip=True))
        if text:
            values.append(f"{tag.name}:{text}")
    return _dedupe(values)[:limit]


def _navigation_labels(soup: BeautifulSoup, limit: int) -> list[str]:
    values: list[str] = []
    nav_nodes = list(soup.find_all("nav"))
    nav_nodes.extend(soup.find_all(attrs={"role": "navigation"}))
    for nav in nav_nodes:
        if nav.get("aria-label"):
            values.append(_clean_text(str(nav.get("aria-label"))))
        for tag in nav.find_all(["a", "button"]):
            text = _clean_text(tag.get_text(" ", strip=True))
            if text:
                values.append(text)
    return _dedupe(values)[:limit]


def _links(soup: BeautifulSoup, base_url: str, final_url: str, policy: SourceInspectionPolicy) -> dict[str, Any]:
    root_domain = root_domain_from_url(final_url)
    final_host = urlparse(final_url).hostname or ""
    valid_links: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    hints: dict[str, list[str]] = {
        "article_link_hints": [],
        "job_link_hints": [],
        "report_link_hints": [],
        "event_link_hints": [],
        "section_hub_hints": [],
        "detail_page_hints": [],
    }
    has_pagination = False
    for tag in soup.find_all("a"):
        href = str(tag.get("href", "")).strip()
        if _ignored_href(href):
            continue
        resolved = urljoin(base_url, href)
        if not _is_http_url(resolved):
            continue
        normalized = normalize_source_url(resolved)
        if normalized in seen_urls:
            continue
        seen_urls.add(normalized)
        text = _clean_text(tag.get_text(" ", strip=True))
        valid_links.append({"url": normalized, "text": text})
        combined = f"{normalized} {text}".casefold()
        for category, words in HINT_KEYWORDS.items():
            if _contains_any(combined, words):
                hints[f"{category}_link_hints"].append(normalized)
        if _contains_any(combined, SECTION_KEYWORDS):
            hints["section_hub_hints"].append(normalized)
        if _contains_any(combined, DETAIL_KEYWORDS):
            hints["detail_page_hints"].append(normalized)
        rel_values = _rel_values(tag.get("rel"))
        if {"next", "prev", "previous"} & set(rel_values) or _contains_any(combined, PAGINATION_KEYWORDS) or re.search(r"([?&]page=|/page/\\d+)", normalized):
            has_pagination = True
    internal = [item for item in valid_links if root_domain_from_url(item["url"]) == root_domain]
    same_domain = [
        item for item in valid_links
        if (urlparse(item["url"]).hostname or "").casefold().removeprefix("www.") == final_host.casefold().removeprefix("www.")
    ]
    external = [item for item in valid_links if root_domain_from_url(item["url"]) != root_domain]
    return {
        "valid_http_link_count": len(valid_links),
        "internal_link_count": len(internal),
        "external_link_count": len(external),
        "same_domain_link_count": len(same_domain),
        "representative_links": valid_links[: policy.max_representative_links],
        "has_pagination_hints": has_pagination,
        **{
            key: _dedupe(value)[: policy.max_hint_entries_per_category]
            for key, value in hints.items()
        },
    }


def _feed_hints(soup: BeautifulSoup, base_url: str, policy: SourceInspectionPolicy) -> list[FeedLinkHint]:
    hints: list[FeedLinkHint] = []
    for tag in soup.find_all("link"):
        rel_values = _rel_values(tag.get("rel"))
        mime_type = str(tag.get("type", "")).split(";", 1)[0].strip().casefold()
        if "alternate" not in rel_values or mime_type not in RSS_MIME_TYPES | ATOM_MIME_TYPES:
            continue
        href = str(tag.get("href", "")).strip()
        resolved = urljoin(base_url, href)
        if not _is_http_url(resolved):
            continue
        hints.append(
            FeedLinkHint(
                href=normalize_source_url(resolved),
                rel=" ".join(rel_values),
                mime_type=mime_type,
                title=_clean_text(str(tag.get("title", ""))) or None,
            )
        )
        if len(hints) >= policy.max_feed_hints:
            break
    return hints


def _source_format_hints(feed_hints: list[FeedLinkHint]) -> list[SourceFormatHint]:
    values = [SourceFormatHint.HTML_PAGE]
    for hint in feed_hints:
        if hint.mime_type in RSS_MIME_TYPES:
            values.append(SourceFormatHint.RSS_CANDIDATE)
        if hint.mime_type in ATOM_MIME_TYPES:
            values.append(SourceFormatHint.ATOM_CANDIDATE)
    return _dedupe(values)


def _visible_text(soup: BeautifulSoup, policy: SourceInspectionPolicy) -> str:
    root = soup.find("main") or soup.body or soup
    root = copy.copy(root)
    for tag_name in BOILERPLATE_TAGS:
        for tag in root.find_all(tag_name):
            tag.decompose()
    lines = [
        _clean_text(item)
        for item in root.get_text("\n", strip=True).splitlines()
    ]
    return "\n".join(_dedupe([line for line in lines if line]))


def _semantic_windows(
    *,
    inspection_id: str,
    title: str | None,
    meta_description: str | None,
    headings: list[str],
    navigation_labels: list[str],
    main_excerpt: str,
    representative_links: list[dict[str, str]],
    structured_types: list[str],
    structured_names: list[str],
    policy: SourceInspectionPolicy,
) -> tuple[list[SemanticTextWindow], bool]:
    candidates: list[tuple[SemanticTextWindowType, str, str, str | None]] = []
    if title:
        candidates.append((SemanticTextWindowType.PAGE_TITLE, "title", title, "html_title"))
    if meta_description:
        candidates.append((SemanticTextWindowType.META_DESCRIPTION, "meta[name=description]", meta_description, "html_meta"))
    if headings:
        candidates.append((SemanticTextWindowType.HEADING_CONTEXT, "h1-h3", "\n".join(headings), "heading_summary"))
    if navigation_labels:
        candidates.append((SemanticTextWindowType.NAVIGATION, "nav", "\n".join(navigation_labels), "navigation_labels"))
    if main_excerpt:
        candidates.append((SemanticTextWindowType.MAIN_CONTENT_EXCERPT, "main_or_body", main_excerpt, "visible_text_excerpt"))
    if representative_links:
        link_text = "\n".join(
            f"{item['text']} | {item['url']}" if item["text"] else item["url"]
            for item in representative_links
        )
        candidates.append((SemanticTextWindowType.REPRESENTATIVE_LINK_CLUSTER, "a[href]", link_text, "representative_links"))
    structured_parts = []
    if structured_types:
        structured_parts.append("types: " + ", ".join(structured_types))
    if structured_names:
        structured_parts.append("names: " + ", ".join(structured_names))
    if structured_parts:
        candidates.append((SemanticTextWindowType.STRUCTURED_DATA_EXCERPT, "script[type=application/ld+json]", "\n".join(structured_parts), "jsonld_observations"))

    windows: list[SemanticTextWindow] = []
    total_chars = 0
    truncated = False
    for window_type, location, text, context in candidates:
        if len(windows) >= policy.max_windows or total_chars >= policy.max_total_semantic_chars:
            truncated = True
            break
        normalized = _clean_text_block(text)
        if not normalized:
            continue
        max_allowed = min(policy.max_window_chars, policy.max_total_semantic_chars - total_chars)
        if len(normalized) > max_allowed:
            normalized = normalized[:max_allowed].rstrip()
            truncated = True
        window_id = build_semantic_text_window_id(
            source_inspection_id=inspection_id,
            window_type=window_type.value,
            source_location=location,
            text=normalized,
        )
        windows.append(
            SemanticTextWindow(
                window_id=window_id,
                window_type=window_type,
                source_location=location,
                text=normalized,
                character_count=len(normalized),
                structural_context=context,
                evidence_provenance={
                    "untrusted_content_marker": UNTRUSTED_WEBPAGE_EVIDENCE_MARKER,
                    "extraction_method": "deterministic_phase5c_inspector",
                },
                max_character_count=policy.max_window_chars,
                policy_version=policy.semantic_window_policy_version,
            )
        )
        total_chars += len(normalized)
    return windows, truncated


def _inspection_input_fingerprint(
    *,
    fetch_execution: SourceFetchExecution,
    policy: SourceInspectionPolicy,
) -> str:
    return hash_canonical_value(
        {
            "schema_version": "source_inspection_input_fingerprint_v1",
            "fetch_execution_id": fetch_execution.source_fetch_execution_id,
            "candidate_source_id": fetch_execution.candidate_source_id,
            "final_url": normalize_source_url(fetch_execution.final_url),
            "raw_body_sha256": fetch_execution.raw_body_sha256,
            "inspector_version": policy.inspector_version,
            "policy_version": policy.policy_version,
            "max_window_chars": policy.max_window_chars,
            "max_total_semantic_chars": policy.max_total_semantic_chars,
            "max_windows": policy.max_windows,
            "parser": policy.parser,
        }
    )


def _json_ready_for_hash(value: Any) -> Any:
    if isinstance(value, SourceFormatHint):
        return value.value
    if isinstance(value, (FeedLinkHint, SemanticTextWindow, RawPageArtifactRef)):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_json_ready_for_hash(item) for item in value]
    if isinstance(value, list):
        return [_json_ready_for_hash(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready_for_hash(item) for key, item in value.items()}
    return value


def _ignored_href(href: str) -> bool:
    if not href or href.startswith("#"):
        return True
    parsed = urlparse(href)
    return parsed.scheme.casefold() in IGNORED_LINK_SCHEMES and bool(parsed.scheme)


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _rel_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).casefold() for item in value]
    return str(value or "").casefold().split()


def _contains_any(value: str, words: tuple[str, ...]) -> bool:
    folded = value.casefold()
    return any(word.casefold() in folded for word in words)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _clean_text_block(value: str) -> str:
    lines = [_clean_text(line) for line in (value or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _dedupe(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        if value is None or value == "":
            continue
        key = value.value if hasattr(value, "value") else value
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
