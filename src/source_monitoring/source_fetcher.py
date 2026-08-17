from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from src.config import (
    PROJECT_ROOT,
    SOURCE_FETCH_BATCH_SIZE,
    SOURCE_FETCH_MAX_BYTES,
    SOURCE_FETCH_MAX_REDIRECTS,
    SOURCE_FETCH_TIMEOUT_SECONDS,
    SOURCE_FETCH_USER_AGENT,
)
from src.source_monitoring.source_evaluation_identity import (
    build_source_fetch_execution_id,
    build_source_fetch_request_fingerprint,
)
from src.source_monitoring.source_evaluation_models import (
    SOURCE_FETCH_POLICY_VERSION,
    SOURCE_USER_AGENT_POLICY_VERSION,
    FetchMethod,
    FetchedPage,
    FetchStatus,
    RawPageArtifactRef,
    RedirectHop,
    SourceFetchExecution,
    SourceFetchRequest,
)


SOURCE_FETCH_ARTIFACT_ROOT = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase5_source_evaluation"
    / "raw_pages"
)
SOURCE_FETCH_FAILURE_ROOT = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase5_source_evaluation"
    / "fetch_failures"
)

HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
RECOGNIZED_NON_HTML_CONTENT_TYPES = (
    "application/pdf",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
    "text/xml",
    "text/plain",
    "application/json",
)
DEFAULT_ACCEPTED_CONTENT_TYPES = HTML_CONTENT_TYPES + RECOGNIZED_NON_HTML_CONTENT_TYPES
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
DEFAULT_CHUNK_SIZE = 8192


class SourceFetchError(Exception):
    """
    Raised for Phase 5B fetcher programming/configuration errors.
    """


@dataclass(frozen=True)
class SourceFetchPolicy:
    timeout_seconds: int = SOURCE_FETCH_TIMEOUT_SECONDS
    max_response_bytes: int = SOURCE_FETCH_MAX_BYTES
    max_redirects: int = SOURCE_FETCH_MAX_REDIRECTS
    accepted_content_types: tuple[str, ...] = DEFAULT_ACCEPTED_CONTENT_TYPES
    user_agent: str = SOURCE_FETCH_USER_AGENT
    user_agent_policy_version: str = SOURCE_USER_AGENT_POLICY_VERSION
    fetch_policy_version: str = SOURCE_FETCH_POLICY_VERSION
    artifact_root: Path = SOURCE_FETCH_ARTIFACT_ROOT
    failure_root: Path = SOURCE_FETCH_FAILURE_ROOT
    cache_enabled: bool = True
    batch_size: int = SOURCE_FETCH_BATCH_SIZE


@dataclass(frozen=True)
class SourceFetchOutcome:
    execution: SourceFetchExecution
    fetched_page: FetchedPage | None
    cache_hit: bool


class SourceFetcher:
    """
    Bounded SourceFetchRequest executor for Phase 5B network facts only.
    """

    def __init__(
        self,
        *,
        policy: SourceFetchPolicy | None = None,
        session: Any | None = None,
        now_fn: Any | None = None,
        monotonic_fn: Any | None = None,
    ) -> None:
        self.policy = policy or SourceFetchPolicy()
        self.session = session or requests.Session()
        self.now_fn = now_fn or _utc_now_iso
        self.monotonic_fn = monotonic_fn or time.monotonic

    def build_request(self, requested_url: str) -> SourceFetchRequest:
        request_fingerprint = build_source_fetch_request_fingerprint(
            requested_url=requested_url,
            method=FetchMethod.GET,
            timeout_seconds=self.policy.timeout_seconds,
            max_response_bytes=self.policy.max_response_bytes,
            max_redirects=self.policy.max_redirects,
            accepted_content_types=self.policy.accepted_content_types,
            user_agent_policy_version=self.policy.user_agent_policy_version,
            fetch_policy_version=self.policy.fetch_policy_version,
        )
        return SourceFetchRequest(
            requested_url=requested_url,
            method=FetchMethod.GET,
            timeout_seconds=self.policy.timeout_seconds,
            max_response_bytes=self.policy.max_response_bytes,
            max_redirects=self.policy.max_redirects,
            accepted_content_types=self.policy.accepted_content_types,
            user_agent_policy_version=self.policy.user_agent_policy_version,
            fetch_policy_version=self.policy.fetch_policy_version,
            request_fingerprint=request_fingerprint,
        )

    def fetch(
        self,
        *,
        request: SourceFetchRequest,
        source_evaluation_plan_id: str,
        candidate_source_id: str,
    ) -> SourceFetchOutcome:
        _validate_request(request)

        if self.policy.cache_enabled:
            cached = self._load_compatible_cache(
                request=request,
                source_evaluation_plan_id=source_evaluation_plan_id,
                candidate_source_id=candidate_source_id,
            )
            if cached is not None:
                return SourceFetchOutcome(
                    execution=cached.execution,
                    fetched_page=cached.fetched_page,
                    cache_hit=True,
                )
            cached_failure = self._load_compatible_failure_cache(
                request=request,
                source_evaluation_plan_id=source_evaluation_plan_id,
                candidate_source_id=candidate_source_id,
            )
            if cached_failure is not None:
                return SourceFetchOutcome(
                    execution=cached_failure.execution,
                    fetched_page=None,
                    cache_hit=True,
                )

        if not _is_http_url(request.requested_url):
            return self._failure_outcome(
                request=request,
                source_evaluation_plan_id=source_evaluation_plan_id,
                candidate_source_id=candidate_source_id,
                final_url=request.requested_url,
                fetch_status=FetchStatus.NETWORK_FAILURE,
                error_type="malformed_url",
                error_message="URL must use http or https scheme.",
                started_at_ms=None,
                redirect_chain=(),
            )

        started = self.monotonic_fn()
        current_url = request.requested_url
        seen_urls = {current_url}
        redirects: list[RedirectHop] = []

        while True:
            try:
                response = self.session.request(
                    request.method.value,
                    current_url,
                    headers=self._headers(),
                    timeout=request.timeout_seconds,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.Timeout as error:
                return self._failure_outcome(
                    request=request,
                    source_evaluation_plan_id=source_evaluation_plan_id,
                    candidate_source_id=candidate_source_id,
                    final_url=current_url,
                    fetch_status=FetchStatus.TIMEOUT,
                    error_type=type(error).__name__,
                    error_message=str(error),
                    started_at_ms=started,
                    redirect_chain=tuple(redirects),
                )
            except requests.RequestException as error:
                return self._failure_outcome(
                    request=request,
                    source_evaluation_plan_id=source_evaluation_plan_id,
                    candidate_source_id=candidate_source_id,
                    final_url=current_url,
                    fetch_status=FetchStatus.NETWORK_FAILURE,
                    error_type=type(error).__name__,
                    error_message=str(error),
                    started_at_ms=started,
                    redirect_chain=tuple(redirects),
                )

            status_code = int(response.status_code)
            response_url = str(getattr(response, "url", current_url) or current_url)

            if status_code in REDIRECT_STATUS_CODES:
                location = _header(response, "Location")
                if not location:
                    return self._response_failure(
                        request=request,
                        source_evaluation_plan_id=source_evaluation_plan_id,
                        candidate_source_id=candidate_source_id,
                        response=response,
                        final_url=response_url,
                        fetch_status=FetchStatus.REDIRECT_FAILURE,
                        error_type="missing_redirect_location",
                        error_message="Redirect response did not include Location.",
                        started_at_ms=started,
                        redirect_chain=tuple(redirects),
                    )

                destination = urljoin(response_url, location)
                if not _is_http_url(destination):
                    return self._response_failure(
                        request=request,
                        source_evaluation_plan_id=source_evaluation_plan_id,
                        candidate_source_id=candidate_source_id,
                        response=response,
                        final_url=response_url,
                        fetch_status=FetchStatus.REDIRECT_FAILURE,
                        error_type="malformed_redirect_location",
                        error_message="Redirect target is not an HTTP(S) URL.",
                        started_at_ms=started,
                        redirect_chain=tuple(redirects),
                    )
                if destination in seen_urls:
                    return self._response_failure(
                        request=request,
                        source_evaluation_plan_id=source_evaluation_plan_id,
                        candidate_source_id=candidate_source_id,
                        response=response,
                        final_url=destination,
                        fetch_status=FetchStatus.REDIRECT_FAILURE,
                        error_type="redirect_loop",
                        error_message="Redirect loop detected.",
                        started_at_ms=started,
                        redirect_chain=tuple(redirects),
                    )
                if len(redirects) >= request.max_redirects:
                    return self._response_failure(
                        request=request,
                        source_evaluation_plan_id=source_evaluation_plan_id,
                        candidate_source_id=candidate_source_id,
                        response=response,
                        final_url=destination,
                        fetch_status=FetchStatus.REDIRECT_FAILURE,
                        error_type="too_many_redirects",
                        error_message="Redirect limit exceeded.",
                        started_at_ms=started,
                        redirect_chain=tuple(redirects),
                    )

                redirects.append(
                    RedirectHop(
                        source_url=response_url,
                        destination_url=destination,
                        status_code=status_code,
                        hop_order=len(redirects),
                    )
                )
                seen_urls.add(destination)
                current_url = destination
                continue

            if 300 <= status_code <= 399:
                return self._response_failure(
                    request=request,
                    source_evaluation_plan_id=source_evaluation_plan_id,
                    candidate_source_id=candidate_source_id,
                    response=response,
                    final_url=response_url,
                    fetch_status=FetchStatus.REDIRECT_FAILURE,
                    error_type="unhandled_redirect_status",
                    error_message=f"Unhandled redirect status HTTP {status_code}.",
                    started_at_ms=started,
                    redirect_chain=tuple(redirects),
                )

            content_length = _content_length(response)
            if content_length is not None and content_length > request.max_response_bytes:
                return self._response_failure(
                    request=request,
                    source_evaluation_plan_id=source_evaluation_plan_id,
                    candidate_source_id=candidate_source_id,
                    response=response,
                    final_url=response_url,
                    fetch_status=FetchStatus.RESPONSE_TOO_LARGE,
                    error_type="content_length_too_large",
                    error_message="Content-Length exceeds maximum response bytes.",
                    started_at_ms=started,
                    redirect_chain=tuple(redirects),
                    content_length_reported=content_length,
                )

            content_type = _content_type(response)
            if 200 <= status_code <= 299 and not _is_accepted_content_type(
                content_type,
                request.accepted_content_types,
            ):
                return self._response_failure(
                    request=request,
                    source_evaluation_plan_id=source_evaluation_plan_id,
                    candidate_source_id=candidate_source_id,
                    response=response,
                    final_url=response_url,
                    fetch_status=FetchStatus.UNSUPPORTED_CONTENT,
                    error_type="unsupported_content_type",
                    error_message=f"Unsupported content type: {content_type or 'unknown'}.",
                    started_at_ms=started,
                    redirect_chain=tuple(redirects),
                    content_length_reported=content_length,
                )

            if status_code == 204:
                body = b""
            else:
                try:
                    body = _read_bounded_body(
                        response=response,
                        max_response_bytes=request.max_response_bytes,
                    )
                except _ResponseTooLarge as error:
                    return self._response_failure(
                        request=request,
                        source_evaluation_plan_id=source_evaluation_plan_id,
                        candidate_source_id=candidate_source_id,
                        response=response,
                        final_url=response_url,
                        fetch_status=FetchStatus.RESPONSE_TOO_LARGE,
                        error_type="stream_exceeded_max_bytes",
                        error_message=str(error),
                        started_at_ms=started,
                        redirect_chain=tuple(redirects),
                        content_length_reported=content_length,
                        response_size_bytes=error.byte_count,
                    )
                except requests.Timeout as error:
                    return self._response_failure(
                        request=request,
                        source_evaluation_plan_id=source_evaluation_plan_id,
                        candidate_source_id=candidate_source_id,
                        response=response,
                        final_url=response_url,
                        fetch_status=FetchStatus.TIMEOUT,
                        error_type=type(error).__name__,
                        error_message=str(error),
                        started_at_ms=started,
                        redirect_chain=tuple(redirects),
                    )
                except requests.RequestException as error:
                    return self._response_failure(
                        request=request,
                        source_evaluation_plan_id=source_evaluation_plan_id,
                        candidate_source_id=candidate_source_id,
                        response=response,
                        final_url=response_url,
                        fetch_status=FetchStatus.NETWORK_FAILURE,
                        error_type=type(error).__name__,
                        error_message=str(error),
                        started_at_ms=started,
                        redirect_chain=tuple(redirects),
                    )

            if not 200 <= status_code <= 299:
                return self._response_failure(
                    request=request,
                    source_evaluation_plan_id=source_evaluation_plan_id,
                    candidate_source_id=candidate_source_id,
                    response=response,
                    final_url=response_url,
                    fetch_status=FetchStatus.HTTP_FAILURE,
                    error_type=f"http_{status_code}",
                    error_message=f"HTTP {status_code} response.",
                    started_at_ms=started,
                    redirect_chain=tuple(redirects),
                    content_length_reported=content_length,
                    response_size_bytes=len(body),
                )

            return self._success_outcome(
                request=request,
                source_evaluation_plan_id=source_evaluation_plan_id,
                candidate_source_id=candidate_source_id,
                response=response,
                final_url=response_url,
                body=body,
                started_at_ms=started,
                redirect_chain=tuple(redirects),
                content_length_reported=content_length,
            )

    def fetch_many(
        self,
        items: tuple[tuple[SourceFetchRequest, str, str], ...],
    ) -> tuple[SourceFetchOutcome, ...]:
        outcomes: list[SourceFetchOutcome] = []
        bounded_items = items[: max(0, self.policy.batch_size)]
        for request, plan_id, candidate_id in bounded_items:
            outcomes.append(
                self.fetch(
                    request=request,
                    source_evaluation_plan_id=plan_id,
                    candidate_source_id=candidate_id,
                )
            )
        return tuple(outcomes)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": ", ".join(self.policy.accepted_content_types),
            "User-Agent": self.policy.user_agent,
        }

    def _success_outcome(
        self,
        *,
        request: SourceFetchRequest,
        source_evaluation_plan_id: str,
        candidate_source_id: str,
        response: Any,
        final_url: str,
        body: bytes,
        started_at_ms: float,
        redirect_chain: tuple[RedirectHop, ...],
        content_length_reported: int | None,
    ) -> SourceFetchOutcome:
        retrieved_at = self.now_fn()
        content_type = _content_type(response) or ""
        detected_encoding = _detect_encoding(response, body)
        body_sha = hashlib.sha256(body).hexdigest()
        fetch_status = _success_status(content_type, body)
        execution_id = build_source_fetch_execution_id(
            source_evaluation_plan_id=source_evaluation_plan_id,
            candidate_source_id=candidate_source_id,
            request_fingerprint=request.request_fingerprint,
            final_url=final_url,
            fetch_status=fetch_status.value,
            raw_body_sha256=body_sha,
        )
        artifact_ref = self._write_raw_artifact(
            request=request,
            execution_id=execution_id,
            body=body,
            sha256=body_sha,
            content_type=content_type,
            encoding=detected_encoding,
            retrieved_at=retrieved_at,
        )
        execution = self._build_execution(
            source_fetch_execution_id=execution_id,
            request=request,
            source_evaluation_plan_id=source_evaluation_plan_id,
            candidate_source_id=candidate_source_id,
            final_url=final_url,
            fetch_status=fetch_status,
            http_status=int(response.status_code),
            redirect_chain=redirect_chain,
            response=response,
            retrieved_at=retrieved_at,
            elapsed_ms=_elapsed_ms(self.monotonic_fn(), started_at_ms),
            raw_body_sha256=body_sha,
            raw_artifact_ref=artifact_ref,
            error_type=None,
            error_message=None,
            content_length_reported=content_length_reported,
            response_size_bytes=len(body),
        )
        fetched_page = FetchedPage(
            fetch_execution_id=execution.source_fetch_execution_id,
            response_metadata=_response_metadata(execution),
            raw_bytes=body,
            decoded_text=_decode_body(body, detected_encoding),
            raw_artifact_ref=artifact_ref,
        )
        self._write_execution_metadata(
            request=request,
            execution=execution,
            cache_payload={
                "request": request.to_dict(),
                "execution": execution.to_dict(),
                "artifact": artifact_ref.to_dict(),
                "cache_model": "immutable_historical_snapshot",
            },
        )
        return SourceFetchOutcome(
            execution=execution,
            fetched_page=fetched_page,
            cache_hit=False,
        )

    def _failure_outcome(
        self,
        *,
        request: SourceFetchRequest,
        source_evaluation_plan_id: str,
        candidate_source_id: str,
        final_url: str,
        fetch_status: FetchStatus,
        error_type: str,
        error_message: str,
        started_at_ms: float | None,
        redirect_chain: tuple[RedirectHop, ...],
    ) -> SourceFetchOutcome:
        retrieved_at = self.now_fn()
        execution_id = build_source_fetch_execution_id(
            source_evaluation_plan_id=source_evaluation_plan_id,
            candidate_source_id=candidate_source_id,
            request_fingerprint=request.request_fingerprint,
            final_url=final_url,
            fetch_status=fetch_status.value,
            raw_body_sha256=None,
        )
        execution = self._build_execution(
            source_fetch_execution_id=execution_id,
            request=request,
            source_evaluation_plan_id=source_evaluation_plan_id,
            candidate_source_id=candidate_source_id,
            final_url=final_url,
            fetch_status=fetch_status,
            http_status=None,
            redirect_chain=redirect_chain,
            response=None,
            retrieved_at=retrieved_at,
            elapsed_ms=(
                None
                if started_at_ms is None
                else _elapsed_ms(self.monotonic_fn(), started_at_ms)
            ),
            raw_body_sha256=None,
            raw_artifact_ref=None,
            error_type=error_type,
            error_message=error_message,
            content_length_reported=None,
            response_size_bytes=None,
        )
        self._write_failure_metadata(request=request, execution=execution)
        return SourceFetchOutcome(execution=execution, fetched_page=None, cache_hit=False)

    def _response_failure(
        self,
        *,
        request: SourceFetchRequest,
        source_evaluation_plan_id: str,
        candidate_source_id: str,
        response: Any,
        final_url: str,
        fetch_status: FetchStatus,
        error_type: str,
        error_message: str,
        started_at_ms: float,
        redirect_chain: tuple[RedirectHop, ...],
        content_length_reported: int | None = None,
        response_size_bytes: int | None = None,
    ) -> SourceFetchOutcome:
        retrieved_at = self.now_fn()
        execution_id = build_source_fetch_execution_id(
            source_evaluation_plan_id=source_evaluation_plan_id,
            candidate_source_id=candidate_source_id,
            request_fingerprint=request.request_fingerprint,
            final_url=final_url,
            fetch_status=fetch_status.value,
            raw_body_sha256=None,
        )
        execution = self._build_execution(
            source_fetch_execution_id=execution_id,
            request=request,
            source_evaluation_plan_id=source_evaluation_plan_id,
            candidate_source_id=candidate_source_id,
            final_url=final_url,
            fetch_status=fetch_status,
            http_status=int(response.status_code),
            redirect_chain=redirect_chain,
            response=response,
            retrieved_at=retrieved_at,
            elapsed_ms=_elapsed_ms(self.monotonic_fn(), started_at_ms),
            raw_body_sha256=None,
            raw_artifact_ref=None,
            error_type=error_type,
            error_message=error_message,
            content_length_reported=content_length_reported,
            response_size_bytes=response_size_bytes,
        )
        self._write_failure_metadata(request=request, execution=execution)
        return SourceFetchOutcome(execution=execution, fetched_page=None, cache_hit=False)

    def _build_execution(
        self,
        *,
        source_fetch_execution_id: str,
        request: SourceFetchRequest,
        source_evaluation_plan_id: str,
        candidate_source_id: str,
        final_url: str,
        fetch_status: FetchStatus,
        http_status: int | None,
        redirect_chain: tuple[RedirectHop, ...],
        response: Any | None,
        retrieved_at: str,
        elapsed_ms: int | None,
        raw_body_sha256: str | None,
        raw_artifact_ref: RawPageArtifactRef | None,
        error_type: str | None,
        error_message: str | None,
        content_length_reported: int | None,
        response_size_bytes: int | None,
    ) -> SourceFetchExecution:
        return SourceFetchExecution(
            source_fetch_execution_id=source_fetch_execution_id,
            source_evaluation_plan_id=source_evaluation_plan_id,
            candidate_source_id=candidate_source_id,
            request_fingerprint=request.request_fingerprint,
            requested_url=request.requested_url,
            final_url=final_url,
            fetch_status=fetch_status,
            http_status=http_status,
            redirect_chain=redirect_chain,
            content_type=_content_type(response),
            content_length_reported=content_length_reported,
            declared_encoding=_declared_encoding(response),
            detected_encoding=_detect_encoding(response, b"") if response else None,
            content_language=_header(response, "Content-Language"),
            response_size_bytes=response_size_bytes,
            etag=_header(response, "ETag"),
            last_modified=_header(response, "Last-Modified"),
            retrieved_at=retrieved_at,
            elapsed_ms=elapsed_ms,
            raw_body_sha256=raw_body_sha256,
            raw_artifact_ref=raw_artifact_ref,
            error_type=error_type,
            error_message=error_message,
            fetch_policy_version=request.fetch_policy_version,
        )

    def _write_raw_artifact(
        self,
        *,
        request: SourceFetchRequest,
        execution_id: str,
        body: bytes,
        sha256: str,
        content_type: str,
        encoding: str | None,
        retrieved_at: str,
    ) -> RawPageArtifactRef:
        artifact_dir = _project_path(self.policy.artifact_root / request.request_fingerprint)
        _mkdir(artifact_dir)
        artifact_path = artifact_dir / f"{execution_id}_{sha256[:16]}.body"
        if not _path_exists(artifact_path):
            _write_bytes(artifact_path, body)
        relative_path = _project_relative_path(artifact_path)
        return RawPageArtifactRef(
            artifact_path=relative_path,
            sha256=sha256,
            byte_size=len(body),
            content_type=content_type,
            encoding=encoding,
            retrieved_at=retrieved_at,
        )

    def _write_execution_metadata(
        self,
        *,
        request: SourceFetchRequest,
        execution: SourceFetchExecution,
        cache_payload: dict[str, Any],
    ) -> None:
        metadata_path = self._cache_metadata_path(request)
        _write_json_if_changed(metadata_path, cache_payload)
        execution_path = metadata_path.parent / f"{execution.source_fetch_execution_id}.json"
        _write_json_if_changed(execution_path, cache_payload)

    def _write_failure_metadata(
        self,
        *,
        request: SourceFetchRequest,
        execution: SourceFetchExecution,
    ) -> None:
        failure_dir = _project_path(self.policy.failure_root / request.request_fingerprint)
        _mkdir(failure_dir)
        failure_path = failure_dir / f"{execution.source_fetch_execution_id}.json"
        _write_json_if_changed(
            failure_path,
            {
                "request": request.to_dict(),
                "execution": execution.to_dict(),
                "cache_model": "failure_diagnostic_not_success_cache",
            },
        )

    def _load_compatible_cache(
        self,
        *,
        request: SourceFetchRequest,
        source_evaluation_plan_id: str,
        candidate_source_id: str,
    ) -> SourceFetchOutcome | None:
        metadata_path = self._cache_metadata_path(request)
        cache_paths = []
        if _path_exists(metadata_path):
            cache_paths.append(metadata_path)
        cache_paths.extend(
            path
            for path in _glob(metadata_path.parent, "*.json")
            if path.name != "metadata.json"
        )
        for cache_path in sorted(cache_paths, key=lambda item: str(item)):
            outcome = self._load_cache_payload(
                cache_path=cache_path,
                request=request,
                source_evaluation_plan_id=source_evaluation_plan_id,
                candidate_source_id=candidate_source_id,
            )
            if outcome is not None:
                return outcome
        return None

    def _load_cache_payload(
        self,
        *,
        cache_path: Path,
        request: SourceFetchRequest,
        source_evaluation_plan_id: str,
        candidate_source_id: str,
    ) -> SourceFetchOutcome | None:
        try:
            payload = json.loads(_read_text(cache_path))
        except (OSError, ValueError):
            return None

        request_payload = payload.get("request")
        execution_payload = payload.get("execution")
        if not isinstance(execution_payload, dict):
            return None
        try:
            execution = SourceFetchExecution.from_dict(execution_payload)
        except Exception:
            return None

        if isinstance(request_payload, dict):
            try:
                cached_request = SourceFetchRequest.from_dict(request_payload)
            except Exception:
                return None
            if cached_request != request:
                return None
        elif (
            execution.request_fingerprint != request.request_fingerprint
            or execution.requested_url != request.requested_url
        ):
            return None
        if execution.source_evaluation_plan_id != source_evaluation_plan_id:
            return None
        if execution.candidate_source_id != candidate_source_id:
            return None
        if execution.raw_artifact_ref is None:
            return None

        artifact_path = PROJECT_ROOT / execution.raw_artifact_ref.artifact_path
        if not _path_exists(artifact_path):
            return None
        body = _read_bytes(artifact_path)
        if hashlib.sha256(body).hexdigest() != execution.raw_artifact_ref.sha256:
            return None

        fetched_page = FetchedPage(
            fetch_execution_id=execution.source_fetch_execution_id,
            response_metadata=_response_metadata(execution),
            raw_bytes=body,
            decoded_text=_decode_body(body, execution.detected_encoding),
            raw_artifact_ref=execution.raw_artifact_ref,
        )
        return SourceFetchOutcome(
            execution=execution,
            fetched_page=fetched_page,
            cache_hit=True,
        )

    def _load_compatible_failure_cache(
        self,
        *,
        request: SourceFetchRequest,
        source_evaluation_plan_id: str,
        candidate_source_id: str,
    ) -> SourceFetchOutcome | None:
        failure_dir = _project_path(self.policy.failure_root / request.request_fingerprint)
        if not _path_exists(failure_dir):
            return None
        for failure_path in sorted(_glob(failure_dir, "*.json"), key=lambda item: str(item)):
            try:
                payload = json.loads(_read_text(failure_path))
            except (OSError, ValueError):
                continue
            request_payload = payload.get("request")
            execution_payload = payload.get("execution")
            if not isinstance(request_payload, dict) or not isinstance(execution_payload, dict):
                continue
            try:
                cached_request = SourceFetchRequest.from_dict(request_payload)
                execution = SourceFetchExecution.from_dict(execution_payload)
            except Exception:
                continue
            if cached_request != request:
                continue
            if execution.source_evaluation_plan_id != source_evaluation_plan_id:
                continue
            if execution.candidate_source_id != candidate_source_id:
                continue
            if execution.raw_artifact_ref is not None:
                continue
            return SourceFetchOutcome(
                execution=execution,
                fetched_page=None,
                cache_hit=True,
            )
        return None

    def _cache_metadata_path(self, request: SourceFetchRequest) -> Path:
        return _project_path(
            self.policy.artifact_root / request.request_fingerprint / "metadata.json"
        )


def execute_source_fetch_requests(
    *,
    requests_to_execute: tuple[tuple[SourceFetchRequest, str, str], ...],
    fetcher: SourceFetcher,
) -> tuple[SourceFetchOutcome, ...]:
    return fetcher.fetch_many(requests_to_execute)


def _validate_request(request: SourceFetchRequest) -> None:
    if request.method != FetchMethod.GET:
        raise SourceFetchError("Phase 5B supports GET SourceFetchRequests only.")


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _header(response: Any | None, name: str) -> str | None:
    if response is None:
        return None
    headers = getattr(response, "headers", {}) or {}
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower())
    else:
        value = None
    return None if value is None else str(value)


def _content_type(response: Any | None) -> str | None:
    value = _header(response, "Content-Type")
    if value is None:
        return None
    return value.strip()


def _base_content_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().casefold()


def _is_html_content(content_type: str | None) -> bool:
    return _base_content_type(content_type) in HTML_CONTENT_TYPES


def _is_accepted_content_type(
    content_type: str | None,
    accepted_content_types: tuple[str, ...],
) -> bool:
    if not content_type:
        return False
    normalized = _base_content_type(content_type)
    accepted = {_base_content_type(item) for item in accepted_content_types}
    return "*/*" in accepted or normalized in accepted


def _success_status(content_type: str | None, body: bytes) -> FetchStatus:
    if not body:
        return FetchStatus.COMPLETED_EMPTY_RESPONSE
    if _is_html_content(content_type):
        return FetchStatus.COMPLETED_HTML
    return FetchStatus.COMPLETED_NON_HTML


def _content_length(response: Any) -> int | None:
    value = _header(response, "Content-Length")
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _declared_encoding(response: Any | None) -> str | None:
    content_type = _content_type(response)
    if not content_type:
        return None
    for part in content_type.split(";")[1:]:
        key, _, value = part.partition("=")
        if key.strip().casefold() == "charset" and value.strip():
            return value.strip().strip("\"'")
    return None


def _detect_encoding(response: Any | None, body: bytes) -> str | None:
    declared = _declared_encoding(response)
    if declared:
        return declared
    encoding = getattr(response, "encoding", None) if response else None
    if encoding:
        return str(encoding)
    if body:
        try:
            body.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            return None
    if body.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def _decode_body(body: bytes, encoding: str | None) -> str | None:
    if not body:
        return ""
    for candidate in tuple(item for item in (encoding, "utf-8") if item):
        try:
            return body.decode(candidate, errors="replace")
        except LookupError:
            continue
    return None


def _read_bounded_body(response: Any, max_response_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=DEFAULT_CHUNK_SIZE):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_response_bytes:
            raise _ResponseTooLarge(total)
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def _response_metadata(execution: SourceFetchExecution) -> dict[str, Any]:
    return {
        "source_fetch_execution_id": execution.source_fetch_execution_id,
        "request_fingerprint": execution.request_fingerprint,
        "requested_url": execution.requested_url,
        "final_url": execution.final_url,
        "fetch_status": execution.fetch_status.value,
        "http_status": execution.http_status,
        "content_type": execution.content_type,
        "content_length_reported": execution.content_length_reported,
        "declared_encoding": execution.declared_encoding,
        "detected_encoding": execution.detected_encoding,
        "content_language": execution.content_language,
        "response_size_bytes": execution.response_size_bytes,
        "etag": execution.etag,
        "last_modified": execution.last_modified,
        "retrieved_at": execution.retrieved_at,
        "elapsed_ms": execution.elapsed_ms,
        "raw_body_sha256": execution.raw_body_sha256,
    }


def _project_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _project_relative_path(path: Path) -> str:
    try:
        return _project_path(path).relative_to(PROJECT_ROOT).as_posix()
    except ValueError as error:
        raise SourceFetchError("Raw artifact path must be under PROJECT_ROOT.") from error


def _filesystem_path(path: Path) -> Path:
    resolved = _project_path(path).resolve(strict=False)
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


def _read_text(path: Path) -> str:
    return _filesystem_path(path).read_text(encoding="utf-8")


def _read_bytes(path: Path) -> bytes:
    return _filesystem_path(path).read_bytes()


def _write_bytes(path: Path, payload: bytes) -> None:
    filesystem_path = _filesystem_path(path)
    filesystem_path.parent.mkdir(parents=True, exist_ok=True)
    filesystem_path.write_bytes(payload)


def _glob(path: Path, pattern: str) -> tuple[Path, ...]:
    return tuple(_filesystem_path(path).glob(pattern))


def _write_json_if_changed(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    filesystem_path = _filesystem_path(path)
    filesystem_path.parent.mkdir(parents=True, exist_ok=True)
    if filesystem_path.exists() and filesystem_path.read_text(encoding="utf-8") == text:
        return
    filesystem_path.write_text(text, encoding="utf-8")


def _elapsed_ms(finished: float, started: float) -> int:
    return max(0, int(round((finished - started) * 1000)))


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class _ResponseTooLarge(Exception):
    def __init__(self, byte_count: int) -> None:
        super().__init__("Response stream exceeded maximum response bytes.")
        self.byte_count = byte_count


def parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
