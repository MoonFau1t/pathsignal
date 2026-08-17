from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

from src.config import PROJECT_ROOT
from src.database.planning_identity import hash_canonical_value
from src.source_monitoring.acquisition_identity import (
    build_feed_verification_output_hash,
    build_feed_verification_result_fingerprint,
    build_feed_verification_result_id,
)
from src.source_monitoring.acquisition_models import (
    AcquisitionPlanningResult,
    AcquisitionResolutionPlan,
    FeedFormat,
    FeedParseStatus,
    FeedVerificationPlan,
    FeedVerificationResult,
    FeedVerificationStatus,
)
from src.source_monitoring.acquisition_planner import ACQUISITION_RESOLUTION_PLANS_FILE
from src.source_monitoring.source_discovery_identity import normalize_source_url
from src.source_monitoring.source_evaluation_models import FetchStatus
from src.source_monitoring.source_fetcher import SourceFetchPolicy, SourceFetcher


PHASE6B_FEED_VERIFICATION_RESULT_SET_SCHEMA_VERSION = "phase6b_feed_verification_result_set_v1"
FEED_VERIFIER_POLICY_VERSION = "feed_verification_policy_v1"
FEED_DOCUMENT_PARSER_VERSION = "feed_parser_policy_v1"
FEED_VERIFICATION_FETCH_POLICY_VERSION = "phase6b_feed_fetch_policy_v1"
DEFAULT_MAX_SAMPLED_FEED_ENTRIES = 20
FEED_VERIFICATION_RESULTS_FILE = (
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "feed_verification_results.json"
)
FEED_VERIFICATION_DIAGNOSTIC_ROOT = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase6_acquisition"
    / "feed_verification"
)
FEED_RAW_ARTIFACT_ROOT = FEED_VERIFICATION_DIAGNOSTIC_ROOT / "raw_feeds"
FEED_FAILURE_ROOT = FEED_VERIFICATION_DIAGNOSTIC_ROOT / "fetch_failures"
FEED_ACCEPTED_CONTENT_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
    "text/html",
    "application/xhtml+xml",
    "text/plain",
)


@dataclass(frozen=True)
class FeedEntrySample:
    sample_index: int
    title: str | None
    link: str | None
    normalized_link: str | None
    stable_id: str | None
    identity_key: str | None
    date_raw: str | None
    date_parseable: bool
    summary_present: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_index": self.sample_index,
            "title": self.title,
            "link": self.link,
            "normalized_link": self.normalized_link,
            "stable_id": self.stable_id,
            "identity_key": self.identity_key,
            "date_raw": self.date_raw,
            "date_parseable": self.date_parseable,
            "summary_present": self.summary_present,
        }


@dataclass(frozen=True)
class ParsedFeedDocument:
    parse_status: FeedParseStatus
    feed_format: FeedFormat
    feed_title: str | None = None
    feed_home_link: str | None = None
    normalized_feed_home_link: str | None = None
    total_entry_count: int = 0
    sampled_entries: tuple[FeedEntrySample, ...] = ()
    diagnostics: tuple[str, ...] = ()
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "parse_status": self.parse_status.value,
            "feed_format": self.feed_format.value,
            "feed_title": self.feed_title,
            "feed_home_link": self.feed_home_link,
            "normalized_feed_home_link": self.normalized_feed_home_link,
            "total_entry_count": self.total_entry_count,
            "sampled_entries": [item.to_dict() for item in self.sampled_entries],
            "diagnostics": list(self.diagnostics),
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class FeedVerificationExecution:
    result: FeedVerificationResult
    plan: FeedVerificationPlan
    acquisition_plan: AcquisitionResolutionPlan
    fetch_cache_hit: bool
    total_entry_count: int
    unique_entry_identity_count: int
    duplicate_entry_identity_count: int
    stable_item_identity_count: int
    entries_with_titles: int
    entries_with_date_evidence: int
    entries_with_parseable_dates: int
    entries_with_unparseable_dates: int
    source_relationship_status: str
    source_relationship_diagnostic: str
    reason_codes: tuple[str, ...]
    sampled_entries: tuple[FeedEntrySample, ...]
    raw_artifact_ref: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.to_dict(),
            "plan": self.plan.to_dict(),
            "acquisition_plan_ref": {
                "acquisition_resolution_plan_id": self.acquisition_plan.acquisition_resolution_plan_id,
                "candidate_source_id": self.acquisition_plan.candidate_source_id,
                "entity_id": self.acquisition_plan.entity_id,
                "source_url": self.acquisition_plan.source_url,
                "observed_source_role": self.acquisition_plan.observed_source_role.value,
            },
            "fetch_cache_hit": self.fetch_cache_hit,
            "total_entry_count": self.total_entry_count,
            "unique_entry_identity_count": self.unique_entry_identity_count,
            "duplicate_entry_identity_count": self.duplicate_entry_identity_count,
            "stable_item_identity_count": self.stable_item_identity_count,
            "entries_with_titles": self.entries_with_titles,
            "entries_with_date_evidence": self.entries_with_date_evidence,
            "entries_with_parseable_dates": self.entries_with_parseable_dates,
            "entries_with_unparseable_dates": self.entries_with_unparseable_dates,
            "source_relationship_status": self.source_relationship_status,
            "source_relationship_diagnostic": self.source_relationship_diagnostic,
            "reason_codes": list(self.reason_codes),
            "sampled_entries": [item.to_dict() for item in self.sampled_entries],
            "raw_artifact_ref": self.raw_artifact_ref,
        }


@dataclass(frozen=True)
class FeedVerificationResultSet:
    feed_verification_results: tuple[FeedVerificationExecution, ...]
    phase6a_input_hash: str
    input_fingerprint: str
    result_distribution: dict[str, int]
    per_source_summary: tuple[dict[str, Any], ...]
    phase6c_routing: tuple[dict[str, Any], ...]
    diagnostics: tuple[str, ...]
    generation: dict[str, Any]
    output_hash: str
    parser_version: str = FEED_DOCUMENT_PARSER_VERSION
    verification_policy_version: str = FEED_VERIFIER_POLICY_VERSION
    schema_version: str = PHASE6B_FEED_VERIFICATION_RESULT_SET_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "parser_version": self.parser_version,
            "verification_policy_version": self.verification_policy_version,
            "phase6a_input_hash": self.phase6a_input_hash,
            "input_fingerprint": self.input_fingerprint,
            "feed_verification_results": [item.to_dict() for item in self.feed_verification_results],
            "result_distribution": self.result_distribution,
            "per_source_summary": [dict(item) for item in self.per_source_summary],
            "phase6c_routing": [dict(item) for item in self.phase6c_routing],
            "diagnostics": list(self.diagnostics),
            "generation": dict(self.generation),
            "output_hash": self.output_hash,
        }


class FeedVerifier:
    def __init__(
        self,
        *,
        fetcher: SourceFetcher | None = None,
        max_sampled_entries: int = DEFAULT_MAX_SAMPLED_FEED_ENTRIES,
        parser_version: str = FEED_DOCUMENT_PARSER_VERSION,
        verification_policy_version: str = FEED_VERIFIER_POLICY_VERSION,
    ) -> None:
        if max_sampled_entries <= 0:
            raise ValueError("max_sampled_entries must be positive.")
        self.fetcher = fetcher or build_phase6b_source_fetcher()
        self.max_sampled_entries = max_sampled_entries
        self.parser_version = parser_version
        self.verification_policy_version = verification_policy_version

    def verify(
        self,
        *,
        plan: FeedVerificationPlan,
        acquisition_plan: AcquisitionResolutionPlan,
    ) -> FeedVerificationExecution:
        if plan.acquisition_resolution_plan_id != acquisition_plan.acquisition_resolution_plan_id:
            raise ValueError("FeedVerificationPlan parent acquisition plan mismatch.")
        request = self.fetcher.build_request(plan.feed_candidate_url)
        outcome = self.fetcher.fetch(
            request=request,
            source_evaluation_plan_id=plan.feed_verification_plan_id,
            candidate_source_id=plan.candidate_source_id,
        )
        execution = outcome.execution
        parsed = (
            parse_feed_document(
                raw_bytes=outcome.fetched_page.raw_bytes,
                final_url=execution.final_url,
                max_sampled_entries=self.max_sampled_entries,
            )
            if outcome.fetched_page is not None
            else ParsedFeedDocument(
                parse_status=FeedParseStatus.NOT_PARSED,
                feed_format=FeedFormat.UNKNOWN,
                diagnostics=("fetch_not_successful",),
                failure_reason=execution.error_type or execution.fetch_status.value,
            )
        )
        relationship_status, relationship_diag = assess_source_feed_relationship(
            source_url=acquisition_plan.source_url,
            feed_final_url=execution.final_url,
            feed_home_link=parsed.normalized_feed_home_link,
        )
        metrics = _entry_metrics(parsed.sampled_entries)
        status, usable, reason_codes = assess_feed_usability(
            fetch_status=execution.fetch_status,
            parsed=parsed,
            valid_entry_url_count=metrics["valid_entry_url_count"],
            stable_item_identity_support=metrics["stable_item_identity_support"],
            title_support=metrics["title_support"],
            relationship_status=relationship_status,
        )
        syntax_valid = parsed.parse_status == FeedParseStatus.PARSED_VALID
        diagnostics = tuple(
            sorted(
                set(parsed.diagnostics)
                | set(reason_codes)
                | {f"source_relationship:{relationship_status}"}
            )
        )
        fingerprint = build_feed_verification_result_fingerprint(
            feed_verification_plan_id=plan.feed_verification_plan_id,
            feed_verification_plan_fingerprint=plan.input_fingerprint,
            candidate_source_id=plan.candidate_source_id,
            feed_candidate_url=plan.feed_candidate_url,
            final_url=execution.final_url,
            fetch_execution_id=execution.source_fetch_execution_id,
            fetch_status=execution.fetch_status.value,
            http_status=execution.http_status,
            raw_body_sha256=execution.raw_body_sha256,
            parser_version=self.parser_version,
            verification_policy_version=self.verification_policy_version,
            max_sampled_entries=self.max_sampled_entries,
            parse_status=parsed.parse_status.value,
            verified_feed_format=parsed.feed_format.value,
            sampled_entry_count=len(parsed.sampled_entries),
            total_entry_count=parsed.total_entry_count,
            valid_entry_url_count=metrics["valid_entry_url_count"],
            stable_item_identity_count=metrics["stable_item_identity_count"],
            unique_entry_identity_count=metrics["unique_entry_identity_count"],
            duplicate_entry_identity_count=metrics["duplicate_entry_identity_count"],
            entries_with_titles=metrics["entries_with_titles"],
            entries_with_parseable_dates=metrics["entries_with_parseable_dates"],
            source_relationship_status=relationship_status,
            verification_status=status.value,
            reason_codes=reason_codes,
        )
        result_id = build_feed_verification_result_id(
            feed_verification_plan_id=plan.feed_verification_plan_id,
            candidate_source_id=plan.candidate_source_id,
            feed_candidate_url=plan.feed_candidate_url,
            input_fingerprint=fingerprint,
        )
        result = FeedVerificationResult(
            feed_verification_result_id=result_id,
            feed_verification_plan_id=plan.feed_verification_plan_id,
            candidate_source_id=plan.candidate_source_id,
            feed_candidate_url=plan.feed_candidate_url,
            final_url=execution.final_url,
            fetch_execution_id=execution.source_fetch_execution_id,
            fetch_status=execution.fetch_status,
            http_status=execution.http_status,
            content_type=execution.content_type,
            redirect_chain=tuple(item.to_dict() for item in execution.redirect_chain),
            parse_status=parsed.parse_status,
            verified_feed_format=parsed.feed_format,
            feed_title=parsed.feed_title,
            feed_home_link=parsed.normalized_feed_home_link,
            sampled_entry_count=len(parsed.sampled_entries),
            valid_entry_url_count=metrics["valid_entry_url_count"],
            title_support=metrics["title_support"],
            publication_date_support=metrics["publication_date_support"],
            stable_item_identity_support=metrics["stable_item_identity_support"],
            syntax_valid=syntax_valid,
            usable_for_monitoring=usable,
            verification_status=status,
            failure_reason=parsed.failure_reason or execution.error_type,
            diagnostics=diagnostics,
            verification_policy_version=self.verification_policy_version,
            input_fingerprint=fingerprint,
        )
        return FeedVerificationExecution(
            result=result,
            plan=plan,
            acquisition_plan=acquisition_plan,
            fetch_cache_hit=outcome.cache_hit,
            total_entry_count=parsed.total_entry_count,
            unique_entry_identity_count=metrics["unique_entry_identity_count"],
            duplicate_entry_identity_count=metrics["duplicate_entry_identity_count"],
            stable_item_identity_count=metrics["stable_item_identity_count"],
            entries_with_titles=metrics["entries_with_titles"],
            entries_with_date_evidence=metrics["entries_with_date_evidence"],
            entries_with_parseable_dates=metrics["entries_with_parseable_dates"],
            entries_with_unparseable_dates=metrics["entries_with_unparseable_dates"],
            source_relationship_status=relationship_status,
            source_relationship_diagnostic=relationship_diag,
            reason_codes=reason_codes,
            sampled_entries=parsed.sampled_entries,
            raw_artifact_ref=execution.raw_artifact_ref.to_dict() if execution.raw_artifact_ref else None,
        )


def build_phase6b_source_fetcher(
    *,
    session: Any | None = None,
    cache_enabled: bool = True,
    max_response_bytes: int = 1_000_000,
) -> SourceFetcher:
    return SourceFetcher(
        policy=SourceFetchPolicy(
            timeout_seconds=20,
            max_response_bytes=max_response_bytes,
            max_redirects=5,
            accepted_content_types=FEED_ACCEPTED_CONTENT_TYPES,
            fetch_policy_version=FEED_VERIFICATION_FETCH_POLICY_VERSION,
            artifact_root=FEED_RAW_ARTIFACT_ROOT,
            failure_root=FEED_FAILURE_ROOT,
            cache_enabled=cache_enabled,
            batch_size=10,
        ),
        session=session,
    )


def execute_feed_verification_plans(
    *,
    planning_result: AcquisitionPlanningResult,
    fetcher: SourceFetcher | None = None,
    max_sampled_entries: int = DEFAULT_MAX_SAMPLED_FEED_ENTRIES,
    generation_mode: str = "phase6b_feed_verification",
) -> FeedVerificationResultSet:
    acquisition_by_id = {
        item.acquisition_resolution_plan_id: item
        for item in planning_result.acquisition_resolution_plans
    }
    verifier = FeedVerifier(fetcher=fetcher, max_sampled_entries=max_sampled_entries)
    executions: list[FeedVerificationExecution] = []
    diagnostics: list[str] = []
    for plan in sorted(planning_result.feed_verification_plans, key=lambda item: item.feed_verification_plan_id):
        acquisition_plan = acquisition_by_id.get(plan.acquisition_resolution_plan_id)
        if acquisition_plan is None:
            diagnostics.append(f"missing_parent_acquisition_plan:{plan.feed_verification_plan_id}")
            continue
        executions.append(verifier.verify(plan=plan, acquisition_plan=acquisition_plan))

    if len(executions) != len(planning_result.feed_verification_plans):
        diagnostics.append("not_every_feed_verification_plan_produced_result")

    result_distribution = _result_distribution(tuple(executions))
    per_source = _per_source_summary(planning_result=planning_result, executions=tuple(executions))
    routing = _phase6c_routing(planning_result=planning_result, per_source_summary=per_source)
    input_fingerprint = hash_canonical_value(
        {
            "phase6a_output_hash": planning_result.output_hash,
            "feed_plan_ids": tuple(item.feed_verification_plan_id for item in planning_result.feed_verification_plans),
            "parser_version": FEED_DOCUMENT_PARSER_VERSION,
            "verification_policy_version": FEED_VERIFIER_POLICY_VERSION,
            "max_sampled_entries": max_sampled_entries,
        }
    )
    semantic_payload = {
        "phase6a_input_hash": planning_result.output_hash,
        "input_fingerprint": input_fingerprint,
        "feed_verification_results": [_semantic_execution_payload(item) for item in executions],
        "result_distribution": result_distribution,
        "per_source_summary": [dict(item) for item in per_source],
        "phase6c_routing": [dict(item) for item in routing],
        "diagnostics": tuple(sorted(diagnostics)),
    }
    generation = {
        "generation_mode": generation_mode,
        "http_calls_possible_max": len(planning_result.feed_verification_plans),
        "new_fetch_count": sum(1 for item in executions if not item.fetch_cache_hit),
        "cache_hit_count": sum(1 for item in executions if item.fetch_cache_hit),
    }
    payload = {
        **semantic_payload,
        "generation": {
            **generation,
        },
    }
    output_hash = build_feed_verification_output_hash(**semantic_payload)
    return FeedVerificationResultSet(
        feed_verification_results=tuple(executions),
        phase6a_input_hash=planning_result.output_hash,
        input_fingerprint=input_fingerprint,
        result_distribution=result_distribution,
        per_source_summary=per_source,
        phase6c_routing=routing,
        diagnostics=tuple(sorted(diagnostics)),
        generation=generation,
        output_hash=output_hash,
    )


def persist_feed_verification_results(
    *,
    result_set: FeedVerificationResultSet,
    output_file: Path = FEED_VERIFICATION_RESULTS_FILE,
) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result_set.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    if output_file.exists() and output_file.read_text(encoding="utf-8") == text:
        return output_file
    output_file.write_text(text, encoding="utf-8")
    return output_file


def load_phase6a_planning_result(path: Path = ACQUISITION_RESOLUTION_PLANS_FILE) -> AcquisitionPlanningResult:
    return AcquisitionPlanningResult.from_dict(json.loads(path.read_text(encoding="utf-8")))


def parse_feed_document(
    *,
    raw_bytes: bytes,
    final_url: str,
    max_sampled_entries: int = DEFAULT_MAX_SAMPLED_FEED_ENTRIES,
) -> ParsedFeedDocument:
    if not raw_bytes:
        return ParsedFeedDocument(
            parse_status=FeedParseStatus.PARSE_FAILURE,
            feed_format=FeedFormat.UNKNOWN,
            diagnostics=("empty_response",),
            failure_reason="empty_response",
        )
    prefix = raw_bytes[:512].lower()
    if b"<!doctype" in prefix:
        return ParsedFeedDocument(
            parse_status=FeedParseStatus.PARSE_FAILURE,
            feed_format=FeedFormat.UNKNOWN,
            diagnostics=("doctype_disallowed",),
            failure_reason="doctype_disallowed",
        )
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as error:
        return ParsedFeedDocument(
            parse_status=FeedParseStatus.PARSE_FAILURE,
            feed_format=FeedFormat.UNKNOWN,
            diagnostics=("malformed_xml",),
            failure_reason=f"xml_parse_error:{type(error).__name__}",
        )
    name = _local_name(root.tag)
    if name == "rss":
        return _parse_rss(root=root, final_url=final_url, max_sampled_entries=max_sampled_entries)
    if name == "feed":
        return _parse_atom(root=root, final_url=final_url, max_sampled_entries=max_sampled_entries)
    return ParsedFeedDocument(
        parse_status=FeedParseStatus.PARSED_INVALID,
        feed_format=FeedFormat.UNKNOWN,
        diagnostics=(f"unrecognized_root:{name}",),
        failure_reason="not_rss_or_atom",
    )


def assess_feed_usability(
    *,
    fetch_status: FetchStatus,
    parsed: ParsedFeedDocument,
    valid_entry_url_count: int,
    stable_item_identity_support: bool,
    title_support: bool,
    relationship_status: str,
) -> tuple[FeedVerificationStatus, bool, tuple[str, ...]]:
    reasons: list[str] = []
    if fetch_status in {FetchStatus.TIMEOUT, FetchStatus.NETWORK_FAILURE, FetchStatus.HTTP_FAILURE, FetchStatus.REDIRECT_FAILURE}:
        return FeedVerificationStatus.UNREACHABLE, False, (f"fetch_{fetch_status.value}",)
    if fetch_status == FetchStatus.UNSUPPORTED_CONTENT:
        return FeedVerificationStatus.UNSUPPORTED_CONTENT, False, ("unsupported_content_type",)
    if fetch_status == FetchStatus.RESPONSE_TOO_LARGE:
        return FeedVerificationStatus.UNSUPPORTED_CONTENT, False, ("response_too_large",)
    if parsed.parse_status == FeedParseStatus.PARSE_FAILURE:
        if parsed.failure_reason == "empty_response":
            return FeedVerificationStatus.EMPTY_OR_INSUFFICIENT, False, ("empty_response",)
        return FeedVerificationStatus.PARSE_FAILURE, False, (parsed.failure_reason or "parse_failure",)
    if parsed.parse_status == FeedParseStatus.PARSED_INVALID:
        return FeedVerificationStatus.INVALID_FEED, False, (parsed.failure_reason or "invalid_feed",)
    if parsed.feed_format == FeedFormat.UNKNOWN:
        return FeedVerificationStatus.INVALID_FEED, False, ("unknown_feed_format",)
    if not parsed.sampled_entries:
        return FeedVerificationStatus.EMPTY_OR_INSUFFICIENT, False, ("zero_entries",)
    if not stable_item_identity_support:
        reasons.append("no_stable_item_identity")
    if valid_entry_url_count <= 0:
        reasons.append("no_usable_entry_urls")
    if not title_support:
        reasons.append("no_entry_title_support")
    if relationship_status == "unresolved_cross_domain":
        reasons.append("unresolved_cross_domain_relationship")
    if relationship_status == "invalid":
        reasons.append("invalid_source_or_feed_domain")
    if reasons:
        status = (
            FeedVerificationStatus.NEEDS_REVIEW
            if "unresolved_cross_domain_relationship" in reasons
            else FeedVerificationStatus.VERIFIED_BUT_LIMITED
        )
        return status, False, tuple(sorted(reasons))
    return FeedVerificationStatus.VERIFIED_USABLE, True, ("verified_usable",)


def assess_source_feed_relationship(
    *,
    source_url: str,
    feed_final_url: str | None,
    feed_home_link: str | None,
) -> tuple[str, str]:
    source_host = _registered_domain(source_url)
    feed_host = _registered_domain(feed_final_url or "")
    home_host = _registered_domain(feed_home_link or "")
    if not source_host or not feed_host:
        return "invalid", "missing source/feed host"
    if source_host == feed_host:
        return "same_domain", f"feed host {feed_host} matches source host {source_host}"
    if source_host and home_host == source_host:
        return "feed_home_link_related", f"feed home host {home_host} matches source host {source_host}"
    return "unresolved_cross_domain", f"feed host {feed_host} does not match source host {source_host}"


def _parse_rss(*, root: ET.Element, final_url: str, max_sampled_entries: int) -> ParsedFeedDocument:
    channel = _first_child(root, "channel")
    if channel is None:
        return ParsedFeedDocument(
            parse_status=FeedParseStatus.PARSED_INVALID,
            feed_format=FeedFormat.UNKNOWN,
            diagnostics=("rss_channel_missing",),
            failure_reason="rss_channel_missing",
        )
    items = tuple(child for child in list(channel) if _local_name(child.tag) == "item")
    sampled = tuple(
        _rss_entry(item=item, index=index, final_url=final_url)
        for index, item in enumerate(items[:max_sampled_entries])
    )
    home = _text(_first_child(channel, "link"))
    normalized_home = _normalize_optional_url(home, final_url)
    return ParsedFeedDocument(
        parse_status=FeedParseStatus.PARSED_VALID,
        feed_format=FeedFormat.RSS,
        feed_title=_text(_first_child(channel, "title")),
        feed_home_link=home,
        normalized_feed_home_link=normalized_home,
        total_entry_count=len(items),
        sampled_entries=sampled,
        diagnostics=("rss_recognized",),
    )


def _rss_entry(*, item: ET.Element, index: int, final_url: str) -> FeedEntrySample:
    title = _text(_first_child(item, "title"))
    link = _text(_first_child(item, "link"))
    guid = _text(_first_child(item, "guid"))
    date_raw = _text(_first_child(item, "pubDate")) or _text(_first_child(item, "date"))
    normalized_link = _normalize_optional_url(link, final_url)
    identity_key = _identity_key(stable_id=guid, normalized_link=normalized_link)
    description = _text(_first_child(item, "description"))
    return FeedEntrySample(
        sample_index=index,
        title=title,
        link=link,
        normalized_link=normalized_link,
        stable_id=guid,
        identity_key=identity_key,
        date_raw=date_raw,
        date_parseable=_date_parseable(date_raw),
        summary_present=bool(description),
    )


def _parse_atom(*, root: ET.Element, final_url: str, max_sampled_entries: int) -> ParsedFeedDocument:
    entries = tuple(child for child in list(root) if _local_name(child.tag) == "entry")
    sampled = tuple(
        _atom_entry(entry=entry, index=index, final_url=final_url)
        for index, entry in enumerate(entries[:max_sampled_entries])
    )
    home = _atom_link(root)
    normalized_home = _normalize_optional_url(home, final_url)
    return ParsedFeedDocument(
        parse_status=FeedParseStatus.PARSED_VALID,
        feed_format=FeedFormat.ATOM,
        feed_title=_text(_first_child(root, "title")),
        feed_home_link=home,
        normalized_feed_home_link=normalized_home,
        total_entry_count=len(entries),
        sampled_entries=sampled,
        diagnostics=("atom_recognized",),
    )


def _atom_entry(*, entry: ET.Element, index: int, final_url: str) -> FeedEntrySample:
    title = _text(_first_child(entry, "title"))
    link = _atom_link(entry)
    atom_id = _text(_first_child(entry, "id"))
    date_raw = _text(_first_child(entry, "published")) or _text(_first_child(entry, "updated"))
    normalized_link = _normalize_optional_url(link, final_url)
    identity_key = _identity_key(stable_id=atom_id, normalized_link=normalized_link)
    summary_present = bool(_text(_first_child(entry, "summary")) or _text(_first_child(entry, "content")))
    return FeedEntrySample(
        sample_index=index,
        title=title,
        link=link,
        normalized_link=normalized_link,
        stable_id=atom_id,
        identity_key=identity_key,
        date_raw=date_raw,
        date_parseable=_date_parseable(date_raw),
        summary_present=summary_present,
    )


def _entry_metrics(entries: tuple[FeedEntrySample, ...]) -> dict[str, Any]:
    identities = [item.identity_key for item in entries if item.identity_key]
    unique_identities = set(identities)
    entries_with_titles = sum(1 for item in entries if item.title)
    entries_with_date_evidence = sum(1 for item in entries if item.date_raw)
    entries_with_parseable_dates = sum(1 for item in entries if item.date_raw and item.date_parseable)
    return {
        "valid_entry_url_count": sum(1 for item in entries if item.normalized_link),
        "stable_item_identity_count": len(identities),
        "stable_item_identity_support": bool(identities),
        "unique_entry_identity_count": len(unique_identities),
        "duplicate_entry_identity_count": max(0, len(identities) - len(unique_identities)),
        "entries_with_titles": entries_with_titles,
        "title_support": entries_with_titles > 0,
        "entries_with_date_evidence": entries_with_date_evidence,
        "entries_with_parseable_dates": entries_with_parseable_dates,
        "entries_with_unparseable_dates": max(0, entries_with_date_evidence - entries_with_parseable_dates),
        "publication_date_support": entries_with_parseable_dates > 0,
    }


def _result_distribution(executions: tuple[FeedVerificationExecution, ...]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for execution in executions:
        key = execution.result.verification_status.value
        distribution[key] = distribution.get(key, 0) + 1
    return dict(sorted(distribution.items()))


def _per_source_summary(
    *,
    planning_result: AcquisitionPlanningResult,
    executions: tuple[FeedVerificationExecution, ...],
) -> tuple[dict[str, Any], ...]:
    by_candidate: dict[str, list[FeedVerificationExecution]] = {}
    for item in executions:
        by_candidate.setdefault(item.result.candidate_source_id, []).append(item)
    rows = []
    for acquisition_plan in sorted(
        planning_result.acquisition_resolution_plans,
        key=lambda item: item.candidate_source_id,
    ):
        source_results = by_candidate.get(acquisition_plan.candidate_source_id, [])
        usable = sum(
            1
            for item in source_results
            if item.result.verification_status == FeedVerificationStatus.VERIFIED_USABLE
        )
        limited = sum(
            1
            for item in source_results
            if item.result.verification_status == FeedVerificationStatus.VERIFIED_BUT_LIMITED
        )
        rows.append(
            {
                "candidate_source_id": acquisition_plan.candidate_source_id,
                "entity_id": acquisition_plan.entity_id,
                "source_url": acquisition_plan.source_url,
                "planned_feed_candidate_count": acquisition_plan.executable_feed_verification_plan_count,
                "verified_usable_feed_count": usable,
                "verified_but_limited_feed_count": limited,
                "non_usable_or_failed_feed_count": max(0, len(source_results) - usable - limited),
                "has_usable_verified_feed": usable > 0,
                "phase6c_selected_website_fallback_remains_eligible": usable == 0,
            }
        )
    return tuple(rows)


def _phase6c_routing(
    *,
    planning_result: AcquisitionPlanningResult,
    per_source_summary: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    summary_by_candidate = {str(item["candidate_source_id"]): item for item in per_source_summary}
    routes = []
    for website_plan in sorted(
        planning_result.selected_website_resolution_plans,
        key=lambda item: item.candidate_source_id,
    ):
        source_summary = summary_by_candidate[website_plan.candidate_source_id]
        if source_summary["has_usable_verified_feed"]:
            route = "HAS_USABLE_VERIFIED_FEED"
            reason = "usable_feed_verified"
        elif source_summary["planned_feed_candidate_count"] == 0:
            route = "NO_USABLE_VERIFIED_FEED"
            reason = "no_known_feed_candidate_verified"
        else:
            route = "NO_USABLE_VERIFIED_FEED"
            reason = "planned_feed_candidates_not_usable"
        routes.append(
            {
                "candidate_source_id": website_plan.candidate_source_id,
                "selected_website_resolution_plan_id": website_plan.selected_website_resolution_plan_id,
                "routing": route,
                "reason": reason,
            }
        )
    return tuple(routes)


def _semantic_execution_payload(execution: FeedVerificationExecution) -> dict[str, Any]:
    payload = execution.to_dict()
    payload.pop("fetch_cache_hit", None)
    return payload


def _first_child(element: ET.Element, local_name: str) -> ET.Element | None:
    for child in list(element):
        if _local_name(child.tag) == local_name:
            return child
    return None


def _atom_link(element: ET.Element) -> str | None:
    fallback: str | None = None
    for child in list(element):
        if _local_name(child.tag) != "link":
            continue
        rel = (child.attrib.get("rel") or "alternate").casefold()
        href = child.attrib.get("href") or _text(child)
        if rel == "alternate" and href:
            return href.strip()
        if href and fallback is None:
            fallback = href.strip()
    return fallback


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    text = " ".join(element.text.split())
    return text or None


def _local_name(tag: Any) -> str:
    text = str(tag)
    if "}" in text:
        return text.rsplit("}", 1)[1]
    return text


def _normalize_optional_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    resolved = urljoin(base_url, value.strip())
    parsed = urlparse(resolved)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    try:
        return normalize_source_url(resolved)
    except ValueError:
        return None


def _identity_key(*, stable_id: str | None, normalized_link: str | None) -> str | None:
    if stable_id:
        return f"id:{stable_id}"
    if normalized_link:
        return f"url:{normalized_link}"
    return None


def _date_parseable(value: str | None) -> bool:
    if not value:
        return False
    text = value.strip()
    try:
        parsedate_to_datetime(text)
        return True
    except (TypeError, ValueError):
        pass
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _registered_domain(value: str) -> str:
    try:
        host = urlparse(value).netloc.casefold().split("@")[-1].split(":")[0]
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def artifact_signature(path: Path) -> dict[str, Any]:
    filesystem_path = _filesystem_path(path)
    payload = filesystem_path.read_bytes()
    stat = filesystem_path.stat()
    return {
        "path": path.relative_to(PROJECT_ROOT).as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "mtime_ns": stat.st_mtime_ns,
    }


def _filesystem_path(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    if os.name != "nt":
        return resolved
    text = str(resolved)
    if text.startswith("\\\\?\\"):
        return Path(text)
    return Path(f"\\\\?\\{text}")
