from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace
from typing import Any, Iterator, TextIO


PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORTS))

import requests
from openai import OpenAI

from src.ai_filter import AIFilterClient, execute_ai_filter
from src.career_intelligence_interpretation import (
    CareerIntelligenceInterpretationClient,
)
from src.career_path_generator import _build_target_career_paths_from_ai_json
from src.career_signal_priority import assess_and_score_career_signal_batch
from src.config import (
    AI_FILTER_MODEL,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
    PROJECT_ROOT,
    RSS_MAX_ITEMS_PER_FEED,
    SEARCH_API_TIMEOUT_SECONDS,
    SELECTED_WEBSITE_MAX_ITEMS_PER_SITE,
    TARGET_CAREER_PATHS_FILE,
    USER_PREFERENCES_FILE,
    USER_PROFILE_FILE,
)
from src.database.connection import open_database_connection
from src.database.migrations import initialize_database
from src.database.repositories.career_signal_repository import (
    CareerSignalRepository,
)
from src.database.repositories.filter_decision_repository import (
    FilterDecisionRepository,
)
from src.database.repositories.pipeline_run_repository import PipelineRunRepository
from src.database.repositories.source_execution_repository import (
    SourceExecutionRepository,
)
from src.database.repositories.source_item_repository import SourceItemRepository
from src.monitoring_runtime import (
    FeedMonitoringAdapter,
    MonitoringAcquisitionDispatcher,
    MonitoringRuntime,
    SelectedWebsiteMonitoringAdapter,
    load_phase7_monitoring_handoffs,
)
from src.normalizer import normalize_raw_items_to_career_signals
from src.priority_assessment import PriorityAssessmentClient
from src.profile_loader import (
    load_user_preferences_from_json,
    load_user_profile_from_json,
)
from src.rss_client import RSSClient
from src.selected_website_client import SelectedWebsiteClient


VALIDATION_SCHEMA_VERSION = "monitoring_full_coverage_validation_v2"
PRODUCTION_PATHS = {
    "production_db": PROJECT_ROOT / "data" / "agentworkflow.db",
    "acquisition_resolutions": (
        PROJECT_ROOT
        / "outputs"
        / "planning"
        / "source_monitoring"
        / "acquisition_resolutions.json"
    ),
    "source_evaluations": (
        PROJECT_ROOT
        / "outputs"
        / "planning"
        / "source_monitoring"
        / "source_evaluations.json"
    ),
    "acquisition_resolution_plans": (
        PROJECT_ROOT
        / "outputs"
        / "planning"
        / "source_monitoring"
        / "acquisition_resolution_plans.json"
    ),
    "feed_verification_results": (
        PROJECT_ROOT
        / "outputs"
        / "planning"
        / "source_monitoring"
        / "feed_verification_results.json"
    ),
    "selected_website_resolution_results": (
        PROJECT_ROOT
        / "outputs"
        / "planning"
        / "source_monitoring"
        / "selected_website_resolution_results.json"
    ),
    "search_scope": PROJECT_ROOT / "inputs" / "search_scope.json",
}


def configure_utf8_stream(stream: TextIO) -> TextIO:
    """Use strict UTF-8 for validation diagnostics without altering content."""

    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="strict")
    return stream


def configure_utf8_stdio() -> None:
    configure_utf8_stream(sys.stdout)
    configure_utf8_stream(sys.stderr)


def diagnostic_print(message: str, *, stream: TextIO | None = None) -> None:
    print(message, file=stream or sys.stdout, flush=True)


def load_filter_execution_rows(
    connection: Any,
    run_id: str,
) -> list[dict[str, Any]]:
    """Load joined filter evidence with both status columns unambiguous."""

    rows = connection.execute(
        """
        SELECT fe.filter_execution_id,
               rs.source_item_id,
               fe.status AS filter_execution_status,
               rs.status AS item_filter_status,
               fe.error_type,
               fe.error_message
          FROM filter_executions AS fe
          JOIN run_source_item_filter_statuses AS rs
            ON rs.filter_execution_id = fe.filter_execution_id
         WHERE fe.run_id = ?
         ORDER BY fe.filter_execution_id
        """,
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def capture_runtime_outputs(result: Any) -> dict[str, Any]:
    """Capture every runtime-only semantic output before report enrichment."""

    return {
        "run_id": result.run_id,
        "runtime_status": result.status,
        "summary": _json_ready(result.summary),
        "source_results": _json_ready(result.source_results),
        "observed_raw_items": _json_ready(result.observed_raw_items),
        "ai_filter_results": _json_ready(result.ai_filter_results),
        "filtered_raw_items": _json_ready(result.filtered_raw_items),
        "career_signals": _json_ready(result.career_signals),
        "scored_career_signals": _json_ready(result.scored_career_signals),
        "priority_assessment_diagnostics": _json_ready(
            result.priority_assessment_diagnostics
        ),
        "routing": _json_ready(result.career_signal_routing),
        "interpretation": _json_ready(
            result.career_intelligence_interpretation
        ),
        "brief": _json_ready(result.career_intelligence_brief),
    }


def checkpoint_runtime_outputs(
    *,
    path: Path,
    report: dict[str, Any],
    result: Any,
    telemetry: dict[str, list[dict[str, Any]]],
) -> Path:
    """Persist runtime-only values before fallible repository enrichment."""

    report.update(capture_runtime_outputs(result))
    report["status"] = result.status
    report["telemetry"] = _json_ready(telemetry)
    report["capture_checkpoint_written_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    return write_validation_artifact(path, report)


def write_validation_artifact(path: Path, report: dict[str, Any]) -> Path:
    """Atomically persist validation evidence as strict UTF-8 JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=_json_default,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


class TrackedCompletions:
    def __init__(
        self,
        underlying: Any,
        call_family: str,
        telemetry: dict[str, list[dict[str, Any]]],
    ) -> None:
        self.underlying = underlying
        self.call_family = call_family
        self.telemetry = telemetry

    def create(self, *args: Any, **kwargs: Any) -> Any:
        messages = kwargs.get("messages") or []
        call_type = self.call_family
        if self.call_family == "priority_assessment":
            combined = " ".join(
                str(item.get("content", ""))
                for item in messages
                if isinstance(item, dict)
            ).casefold()
            if "user_policy_fit" in combined:
                call_type = "priority_opportunity"
            elif "career_relevance_strength" in combined:
                call_type = "priority_intelligence"

        model_calls = self.telemetry["model"]
        call_number = len(model_calls) + 1
        family_call = (
            sum(item["call_family"] == self.call_family for item in model_calls)
            + 1
        )
        diagnostic_print(
            f"MODEL_START {call_number} family={call_type} "
            f"family_call={family_call}"
        )
        started = time.monotonic()
        base = {
            "call_number": call_number,
            "call_family": self.call_family,
            "call_type": call_type,
            "model": kwargs.get("model"),
            "message_count": len(messages),
            "input_character_count": sum(
                len(str(item.get("content", "")))
                for item in messages
                if isinstance(item, dict)
            ),
        }
        try:
            response = self.underlying.create(*args, **kwargs)
        except Exception as error:
            elapsed = time.monotonic() - started
            model_calls.append(
                {
                    **base,
                    "status": "failed",
                    "elapsed_seconds": round(elapsed, 3),
                    "error_type": type(error).__name__,
                    "error_message": str(error)[:1000],
                }
            )
            diagnostic_print(
                f"MODEL_FAIL {call_number} family={call_type} "
                f"type={type(error).__name__} elapsed={elapsed:.2f}s"
            )
            raise

        elapsed = time.monotonic() - started
        usage = _usage_payload(getattr(response, "usage", None))
        model_calls.append(
            {
                **base,
                "status": "completed",
                "elapsed_seconds": round(elapsed, 3),
                "usage": usage,
                "response_id": getattr(response, "id", None),
            }
        )
        total_tokens = usage.get("total_tokens") if usage else None
        diagnostic_print(
            f"MODEL_DONE {call_number} family={call_type} "
            f"elapsed={elapsed:.2f}s total_tokens={total_tokens}"
        )
        return response


def tracked_openai(
    call_family: str,
    telemetry: dict[str, list[dict[str, Any]]],
) -> SimpleNamespace:
    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=TrackedCompletions(
                client.chat.completions,
                call_family,
                telemetry,
            )
        )
    )


@contextmanager
def track_http_calls(
    telemetry: dict[str, list[dict[str, Any]]],
) -> Iterator[None]:
    original_get = requests.get

    def tracked_get(url: str, *args: Any, **kwargs: Any) -> Any:
        call_number = len(telemetry["http"]) + 1
        diagnostic_print(f"HTTP_START {call_number} {url}")
        started = time.monotonic()
        try:
            response = original_get(url, *args, **kwargs)
        except Exception as error:
            elapsed = time.monotonic() - started
            telemetry["http"].append(
                {
                    "call_number": call_number,
                    "url": str(url),
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error_message": str(error)[:1000],
                    "elapsed_seconds": round(elapsed, 3),
                }
            )
            diagnostic_print(
                f"HTTP_FAIL {call_number} type={type(error).__name__} "
                f"elapsed={elapsed:.2f}s"
            )
            raise

        elapsed = time.monotonic() - started
        telemetry["http"].append(
            {
                "call_number": call_number,
                "url": str(url),
                "status": "completed",
                "status_code": response.status_code,
                "elapsed_seconds": round(elapsed, 3),
                "redirect_count": len(response.history),
                "final_url": str(response.url),
            }
        )
        diagnostic_print(
            f"HTTP_DONE {call_number} status={response.status_code} "
            f"elapsed={elapsed:.2f}s redirects={len(response.history)}"
        )
        return response

    requests.get = tracked_get
    try:
        yield
    finally:
        requests.get = original_get


def execute_live_validation(
    *,
    handoff_path: Path,
    database_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    telemetry: dict[str, list[dict[str, Any]]] = {"http": [], "model": []}
    hashes_before = _production_hashes()
    report: dict[str, Any] = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "started_at": started_at,
        "database_path": str(database_path),
        "database_existed_before": database_path.exists(),
        "handoff_path": str(handoff_path),
        "production_hashes_before": hashes_before,
        "configuration": {
            "provider": LLM_PROVIDER,
            "base_url": LLM_BASE_URL,
            "model": AI_FILTER_MODEL,
            "ai_filter_live_for_validation": True,
            "ai_filter_item_cap_present": False,
            "runtime_max_candidates_per_source": None,
            "rss_max_items_per_feed": RSS_MAX_ITEMS_PER_FEED,
            "selected_website_max_items_per_site": (
                SELECTED_WEBSITE_MAX_ITEMS_PER_SITE
            ),
            "search_executed": False,
            "source_onboarding_executed": False,
        },
    }

    try:
        if database_path.exists():
            raise FileExistsError(
                "Monitoring validation database must be fresh: "
                f"{database_path}"
            )
        handoffs = load_phase7_monitoring_handoffs(handoff_path)
        report["handoffs"] = [_handoff_payload(item) for item in handoffs]

        target_payload = json.loads(
            TARGET_CAREER_PATHS_FILE.read_text(encoding="utf-8")
        )
        target_paths = _build_target_career_paths_from_ai_json(
            {"target_career_paths": target_payload["target_career_paths"]}
        )
        user_profile = load_user_profile_from_json(USER_PROFILE_FILE)
        user_preferences = load_user_preferences_from_json(USER_PREFERENCES_FILE)

        initialize_database(database_path)
        run_repository = PipelineRunRepository(database_path)
        execution_repository = SourceExecutionRepository(database_path)
        source_repository = SourceItemRepository(database_path)
        filter_repository = FilterDecisionRepository(database_path)
        career_repository = CareerSignalRepository(database_path)

        rss_client = RSSClient(
            timeout_seconds=SEARCH_API_TIMEOUT_SECONDS,
            dry_run=False,
            max_items_per_feed=RSS_MAX_ITEMS_PER_FEED,
        )
        website_client = SelectedWebsiteClient(
            timeout_seconds=SEARCH_API_TIMEOUT_SECONDS,
            dry_run=False,
            max_items_per_site=SELECTED_WEBSITE_MAX_ITEMS_PER_SITE,
        )
        ai_client = AIFilterClient(
            provider=LLM_PROVIDER,
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=AI_FILTER_MODEL,
            dry_run=False,
        )
        ai_client.client = tracked_openai("ai_filter", telemetry)
        priority_client = PriorityAssessmentClient(
            provider=LLM_PROVIDER,
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=AI_FILTER_MODEL,
            client=tracked_openai("priority_assessment", telemetry),
        )
        interpretation_client = CareerIntelligenceInterpretationClient(
            provider=LLM_PROVIDER,
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=AI_FILTER_MODEL,
            client=tracked_openai("interpretation", telemetry),
        )

        def ai_filter_executor(raw_items: list[Any], profile: Any, paths: Any) -> Any:
            title = raw_items[0].title if raw_items else ""
            diagnostic_print(
                f"FILTER_ITEM {len(telemetry['model']) + 1} "
                f"title={title[:120]!r}"
            )
            return execute_ai_filter(
                raw_items=raw_items,
                user_profile=profile,
                target_career_paths=paths,
                client=ai_client,
            )

        def priority_assessor(**kwargs: Any) -> Any:
            return assess_and_score_career_signal_batch(
                priority_assessment_client=priority_client,
                **kwargs,
            )

        runtime = MonitoringRuntime(
            dispatcher=MonitoringAcquisitionDispatcher(
                feed_adapter=FeedMonitoringAdapter(rss_client),
                selected_website_adapter=SelectedWebsiteMonitoringAdapter(
                    website_client
                ),
            ),
            pipeline_run_repository=run_repository,
            source_execution_repository=execution_repository,
            source_item_repository=source_repository,
            filter_decision_repository=filter_repository,
            career_signal_repository=career_repository,
            ai_filter_executor=ai_filter_executor,
            normalizer=normalize_raw_items_to_career_signals,
            execution_mode="live_validation",
            max_candidates_per_source=None,
            ai_filter_execution_mode="live",
            ai_filter_provider=LLM_PROVIDER,
            ai_filter_model=AI_FILTER_MODEL,
            priority_assessor=priority_assessor,
            interpretation_executor=interpretation_client.interpret,
        )

        with track_http_calls(telemetry):
            result = runtime.run(
                handoffs=handoffs,
                user_profile=user_profile,
                target_career_paths=target_paths,
                user_preferences=user_preferences,
            )

        checkpoint_runtime_outputs(
            path=report_path,
            report=report,
            result=result,
            telemetry=telemetry,
        )

        try:
            report.update(
                assemble_repository_evidence(
                    database_path=database_path,
                    run_id=result.run_id,
                    source_results=result.source_results,
                    career_signals=result.career_signals,
                    handoffs=report["handoffs"],
                    telemetry=telemetry,
                    execution_repository=execution_repository,
                    filter_repository=filter_repository,
                )
            )
        except Exception as error:
            report["report_assembly_error"] = {
                "error_type": type(error).__name__,
                "error_message": str(error),
                "traceback": traceback.format_exc(),
            }
    except Exception as error:
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        report["error_message"] = str(error)
        report["traceback"] = traceback.format_exc()
    finally:
        report["telemetry"] = _json_ready(telemetry)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["total_runtime_seconds"] = round(time.monotonic() - started, 3)
        report["database_exists_after"] = database_path.exists()
        report["production_hashes_after"] = _production_hashes()
        report["production_hashes_unchanged"] = (
            hashes_before == report["production_hashes_after"]
        )
        write_validation_artifact(report_path, report)

    return report


def assemble_repository_evidence(
    *,
    database_path: Path,
    run_id: str,
    source_results: Any,
    career_signals: Any,
    handoffs: list[dict[str, Any]],
    telemetry: dict[str, list[dict[str, Any]]],
    execution_repository: Any,
    filter_repository: Any,
) -> dict[str, Any]:
    executions = execution_repository.list_source_executions(run_id)
    execution_by_id = {item.source_execution_id: item for item in executions}
    statuses = filter_repository.list_run_filter_statuses(run_id)
    decisions = filter_repository.list_filter_decisions_for_run(run_id)
    status_by_item = {item.source_item_id: item for item in statuses}
    decision_by_item = {item.source_item_id: item for item in decisions}

    connection = open_database_connection(database_path)
    try:
        source_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT si.source_item_id, si.fingerprint, si.title,
                       si.organization, si.url, si.source_type, si.payload_json
                  FROM source_items AS si
                  JOIN run_source_item_filter_statuses AS rs
                    ON rs.source_item_id = si.source_item_id
                 WHERE rs.run_id = ?
                 ORDER BY si.source_item_id
                """,
                (run_id,),
            ).fetchall()
        ]
        discovery_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT d.source_execution_id, d.source_item_id,
                       d.result_position, d.discovered_at
                  FROM source_item_discoveries AS d
                  JOIN source_executions AS se
                    ON se.source_execution_id = d.source_execution_id
                 WHERE se.run_id = ?
                 ORDER BY d.source_execution_id,
                          d.result_position,
                          d.source_item_id
                """,
                (run_id,),
            ).fetchall()
        ]
        filter_execution_rows = load_filter_execution_rows(connection, run_id)
    finally:
        connection.close()

    discoveries_by_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in discovery_rows:
        discoveries_by_item[row["source_item_id"]].append(row)

    new_item_details = []
    for row in source_rows:
        item_id = row["source_item_id"]
        first_discovery = discoveries_by_item[item_id][0]
        execution = execution_by_id[first_discovery["source_execution_id"]]
        status = status_by_item.get(item_id)
        decision = decision_by_item.get(item_id)
        payload = json.loads(row["payload_json"])
        failure = next(
            (
                item
                for item in filter_execution_rows
                if item["source_item_id"] == item_id
                and item["filter_execution_status"] == "failed"
            ),
            None,
        )
        new_item_details.append(
            {
                "source_item_id": item_id,
                "fingerprint": row["fingerprint"],
                "source_execution_id": execution.source_execution_id,
                "handoff_id": execution.metadata.get(
                    "source_monitoring_handoff_id"
                ),
                "candidate_source_id": execution.metadata.get(
                    "candidate_source_id"
                ),
                "entity_id": execution.metadata.get("entity_id"),
                "acquisition_method": execution.metadata.get(
                    "acquisition_method"
                ),
                "source_role": execution.metadata.get("source_role"),
                "title": row["title"],
                "organization": row["organization"],
                "url": row["url"],
                "source_type": row["source_type"],
                "published_at": payload.get("published_at"),
                "ai_filter_attempted": bool(
                    status and status.filter_execution_id is not None
                ),
                "filter_status": status.status if status else None,
                "deferred_reason": status.deferred_reason if status else None,
                "decision": decision.decision if decision else None,
                "suggested_category": (
                    decision.metadata.get("suggested_category")
                    if decision
                    else None
                ),
                "confidence": decision.confidence if decision else None,
                "matched_career_path_ids": (
                    list(decision.matched_career_path_ids or ())
                    if decision
                    else []
                ),
                "reason": (
                    decision.reason
                    if decision
                    else failure.get("error_message") if failure else None
                ),
                "action": (
                    decision.metadata.get("suggested_action")
                    if decision
                    else None
                ),
                "discovery_count": len(discoveries_by_item[item_id]),
            }
        )

    http_by_url = {item["url"]: item for item in telemetry["http"]}
    source_details = []
    for source_result in source_results:
        execution = execution_by_id[source_result.source_execution_id]
        handoff = next(
            item for item in handoffs if item["handoff_id"] == source_result.handoff_id
        )
        items = [
            item
            for item in new_item_details
            if item["source_execution_id"] == source_result.source_execution_id
        ]
        source_details.append(
            {
                **asdict(source_result),
                "acquisition_method": execution.metadata.get(
                    "acquisition_method"
                ),
                "canonical_acquisition_url": handoff[
                    "canonical_acquisition_url"
                ],
                "http": http_by_url.get(handoff["canonical_acquisition_url"]),
                "same_run_rediscovery_count": (
                    source_result.existing_source_item_count
                ),
                "empty_acquisition": (
                    source_result.returned_candidate_count == 0
                ),
                "ai_filter_attempt_count": sum(
                    item["ai_filter_attempted"] for item in items
                ),
                "accepted_count": sum(
                    item["decision"] == "accepted" for item in items
                ),
                "rejected_count": sum(
                    item["decision"] == "rejected" for item in items
                ),
                "failed_filter_count": sum(
                    item["filter_status"] == "failed" for item in items
                ),
                "career_signal_count": sum(
                    signal.metadata.get("raw_item_metadata", {}).get(
                        "monitoring_handoff_id"
                    )
                    == source_result.handoff_id
                    for signal in career_signals
                ),
            }
        )

    return {
        "source_accounting": source_details,
        "new_source_items": new_item_details,
        "filter_execution_rows": filter_execution_rows,
        "filter_execution_failures": [
            item
            for item in filter_execution_rows
            if item["filter_execution_status"] == "failed"
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run isolated Monitoring full-coverage validation."
    )
    parser.add_argument("--handoff-file", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--execute-live",
        action="store_true",
        help="Required explicit guard for live HTTP and model calls.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = parse_args(argv)
    if not args.execute_live:
        raise SystemExit(
            "Live validation is disabled without the explicit --execute-live guard."
        )
    report = execute_live_validation(
        handoff_path=args.handoff_file.resolve(),
        database_path=args.database.resolve(),
        report_path=args.report.resolve(),
    )
    diagnostic_print(f"REPORT_PATH {args.report.resolve()}")
    diagnostic_print(
        f"RUN_STATUS {report.get('status')} "
        f"runtime={report.get('total_runtime_seconds')}s"
    )
    diagnostic_print(
        f"HTTP_CALLS {len(report['telemetry']['http'])} "
        f"MODEL_CALLS {len(report['telemetry']['model'])}"
    )
    return int(
        report.get("status") == "failed" or "report_assembly_error" in report
    )


def _handoff_payload(handoff: Any) -> dict[str, Any]:
    return {
        "handoff_id": handoff.phase7_monitoring_handoff_id,
        "acquisition_method": handoff.acquisition_method.value,
        "canonical_acquisition_url": str(
            handoff.provenance.get("verified_feed_url")
            or handoff.provenance.get("feed_candidate_url")
            or handoff.source_url
        ),
        "contributing_candidate_source_ids": [
            item["candidate_source_id"]
            for item in handoff.provenance.get(
                "contributing_candidate_sources", []
            )
        ]
        or [handoff.candidate_source_id],
        "entity_id": handoff.entity_id,
        "source_role": handoff.source_role.value,
        "source_url": handoff.source_url,
    }


def _usage_payload(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump(mode="json")
    if isinstance(usage, dict):
        return _json_ready(usage)
    return {
        key: getattr(usage, key)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if getattr(usage, key, None) is not None
    }


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_ready(value.to_dict())
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _json_default(value: Any) -> Any:
    converted = _json_ready(value)
    if converted is value:
        raise TypeError(f"Cannot serialize {type(value).__name__}")
    return converted


def _file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _production_hashes() -> dict[str, str | None]:
    return {key: _file_sha256(path) for key, path in PRODUCTION_PATHS.items()}


if __name__ == "__main__":
    raise SystemExit(main())
