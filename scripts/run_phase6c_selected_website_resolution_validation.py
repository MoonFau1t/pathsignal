from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORTS))

from src.config import PROJECT_ROOT
from src.source_monitoring.selected_website_resolver import (
    DEFAULT_MAX_ITEMS_PER_RUN,
    DEFAULT_MAX_RETAINED_ITEM_CANDIDATES,
    SELECTED_WEBSITE_FEASIBILITY_POLICY_VERSION,
    SELECTED_WEBSITE_ITEM_DISCOVERY_POLICY_VERSION,
    SELECTED_WEBSITE_RESOLUTION_RESULTS_FILE,
    artifact_signature,
    build_phase6c_source_fetcher,
    execute_selected_website_resolution_plans,
    load_phase6c_inputs,
    persist_selected_website_resolution_results,
)


SUMMARY_FILE = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase6_acquisition"
    / "selected_website_resolution"
    / "phase6c_selected_website_resolution_validation.json"
)
REPORT_FILE = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "reports"
    / "phase6c_selected_website_resolution.md"
)
UPSTREAM_PATHS = (
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "source_evaluations.json",
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "candidate_sources.json",
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "acquisition_resolution_plans.json",
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "feed_verification_results.json",
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase5_source_evaluation"
    / "phase6_source_handoff.json",
)


class GuardHTTPSession:
    calls: list[dict[str, Any]]

    def __init__(self) -> None:
        self.calls = []

    def request(self, method: str, url: str, **kwargs: Any) -> None:
        self.calls.append({"method": method, "url": url, **kwargs})
        raise AssertionError("HTTP attempted during Phase 6C replay")


def main() -> int:
    planning, feed_payload, historical_inspections = load_phase6c_inputs()
    upstream_before = upstream_signatures()
    manifest = build_manifest(planning=planning, feed_payload=feed_payload)
    live = execute_selected_website_resolution_plans(
        planning_result=planning,
        feed_verification_result_payload=feed_payload,
        historical_inspections=historical_inspections,
        fetcher=build_phase6c_source_fetcher(cache_enabled=False),
        generation_mode="phase6c_live_selected_website_resolution",
    )
    persist_selected_website_resolution_results(result_set=live)
    artifacts_before = artifact_signatures(live.to_dict())

    guard = GuardHTTPSession()
    replay = execute_selected_website_resolution_plans(
        planning_result=planning,
        feed_verification_result_payload=feed_payload,
        historical_inspections=historical_inspections,
        fetcher=build_phase6c_source_fetcher(session=guard),
        generation_mode="phase6c_cache_replay",
    )
    artifacts_after = artifact_signatures(replay.to_dict())
    upstream_after = upstream_signatures()
    replay_checks = compare_replay(live=live.to_dict(), replay=replay.to_dict())
    artifact_immutability = artifacts_before == artifacts_after
    upstream_immutability = upstream_before == upstream_after
    validation_checks = validate_run(
        eligible_manifest_count=len(manifest),
        live=live,
        replay=replay,
        replay_checks=replay_checks,
        replay_http_calls=guard.calls,
        artifact_immutability=artifact_immutability,
        upstream_immutability=upstream_immutability,
    )
    verdict = (
        "PHASE 6C LIVE RESOLUTION PASSED"
        if validation_checks["all_pass"]
        else "PHASE 6C NEEDS FIX BEFORE PHASE 6D"
    )
    summary = {
        "schema_version": "phase6c_selected_website_resolution_validation_v1",
        "verdict": verdict,
        "git": git_context(),
        "item_discovery_policy_version": SELECTED_WEBSITE_ITEM_DISCOVERY_POLICY_VERSION,
        "feasibility_policy_version": SELECTED_WEBSITE_FEASIBILITY_POLICY_VERSION,
        "max_retained_item_candidates": DEFAULT_MAX_RETAINED_ITEM_CANDIDATES,
        "max_items_per_run": DEFAULT_MAX_ITEMS_PER_RUN,
        "phase6a_population": {
            "approved_source_count": planning.approved_input_count,
            "acquisition_resolution_plan_count": len(planning.acquisition_resolution_plans),
            "feed_verification_plan_count": len(planning.feed_verification_plans),
            "selected_website_resolution_plan_count": len(planning.selected_website_resolution_plans),
            "phase6a_output_hash": planning.output_hash,
        },
        "phase6b_input_hash": feed_payload.get("output_hash"),
        "manifest": manifest,
        "maximum_external_source_surface_requests": len(manifest),
        "live_result_file": relative(SELECTED_WEBSITE_RESOLUTION_RESULTS_FILE),
        "live_result_output_hash": live.output_hash,
        "result_distribution": live.result_distribution,
        "per_source_summary": live.per_source_summary,
        "phase6d_routing": live.phase6d_routing,
        "live_results": [summarize_execution(item) for item in live.to_dict()["selected_website_resolution_results"]],
        "validation_checks": validation_checks,
        "replay": {
            "guard_http_calls": len(guard.calls),
            "checks": replay_checks,
            "new_fetch_count": replay.generation["new_fetch_count"],
            "cache_hit_count": replay.generation["cache_hit_count"],
            "output_hash": replay.output_hash,
        },
        "artifact_immutability": {
            "before": artifacts_before,
            "after": artifacts_after,
            "unchanged": artifact_immutability,
        },
        "upstream_immutability": {
            "before": upstream_before,
            "after": upstream_after,
            "unchanged": upstream_immutability,
            "changed": sorted(
                path
                for path in set(upstream_before) | set(upstream_after)
                if upstream_before.get(path) != upstream_after.get(path)
            ),
        },
        "phase6c_boundary": {
            "brave_calls": 0,
            "deepseek_calls": 0,
            "llm_calls": 0,
            "feed_discovery": False,
            "feed_verification": False,
            "item_page_fetches": 0,
            "pagination_fetches": 0,
            "browser_automation": False,
            "acquisition_resolution_created": False,
            "final_acquisition_method_selected": False,
        },
    }
    write_json(SUMMARY_FILE, summary)
    write_report(summary)
    print(verdict)
    return 0 if verdict != "PHASE 6C NEEDS FIX BEFORE PHASE 6D" else 1


def build_manifest(*, planning: Any, feed_payload: dict[str, Any]) -> list[dict[str, Any]]:
    plan_by_id = {
        item.selected_website_resolution_plan_id: item
        for item in planning.selected_website_resolution_plans
    }
    rows = []
    for route in feed_payload.get("phase6c_routing", []):
        if route.get("routing") != "NO_USABLE_VERIFIED_FEED":
            continue
        plan = plan_by_id[str(route["selected_website_resolution_plan_id"])]
        request = build_phase6c_source_fetcher(session=GuardHTTPSession()).build_request(plan.source_url)
        rows.append(
            {
                "selected_website_resolution_plan_id": plan.selected_website_resolution_plan_id,
                "parent_acquisition_resolution_plan_id": plan.acquisition_resolution_plan_id,
                "candidate_source_id": plan.candidate_source_id,
                "source_url": plan.source_url,
                "observed_source_role": plan.observed_source_role.value,
                "source_inspection_id": plan.source_inspection_id,
                "source_inspection_hash": plan.source_inspection_hash,
                "source_observation_result_id": plan.source_observation_result_id,
                "source_observation_result_hash": plan.source_observation_result_hash,
                "phase6b_routing": dict(route),
                "fetch_request_fingerprint": request.request_fingerprint,
            }
        )
    return rows


def summarize_execution(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload["result"]
    config = result.get("selected_website_acquisition_config")
    current = payload["current_evidence"]
    raw_ref = payload.get("raw_artifact_ref")
    return {
        "selected_website_resolution_result_id": result["selected_website_resolution_result_id"],
        "selected_website_resolution_plan_id": result["selected_website_resolution_plan_id"],
        "candidate_source_id": result["candidate_source_id"],
        "source_url": result["source_url"],
        "fetch_cache_hit": payload["fetch_cache_hit"],
        "fetch_status": current["fetch_status"],
        "inspectable": current["inspectable"],
        "current_inspection_id": current["inspection_id"],
        "current_inspection_hash": current["inspection_hash"],
        "current_inspection_checkpoint": payload["current_inspection_checkpoint"],
        "raw_artifact_ref": raw_ref,
        "selected_candidate_link_count": current["selected_candidate_link_count"],
        "in_scope_candidate_link_count": current["in_scope_candidate_link_count"],
        "candidate_links_with_title_count": current["candidate_links_with_title_count"],
        "candidate_links_with_date_hint_count": current["candidate_links_with_date_hint_count"],
        "normalized_item_url_support": result["normalized_item_url_support"],
        "item_title_support": result["item_title_support"],
        "date_hint_support": result["date_hint_support"],
        "item_type_role_support": result["item_type_role_support"],
        "candidate_item_link_discoverability": result["candidate_item_link_discoverability"],
        "bounded_extraction_consistency": result["bounded_extraction_consistency"],
        "feasibility_status": result["feasibility_status"],
        "selected_website_acquisition_config_id": (
            config["selected_website_acquisition_config_id"] if config else None
        ),
        "reason_codes": result["reason_codes"],
        "technical_limitations": result["technical_limitations"],
    }


def compare_replay(*, live: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    live_results = live["selected_website_resolution_results"]
    replay_results = replay["selected_website_resolution_results"]
    checks = {
        "result_count": len(live_results) == len(replay_results),
        "output_hash": live["output_hash"] == replay["output_hash"],
        "result_ids": [item["result"]["selected_website_resolution_result_id"] for item in live_results]
        == [item["result"]["selected_website_resolution_result_id"] for item in replay_results],
        "fingerprints": [item["result"]["input_fingerprint"] for item in live_results]
        == [item["result"]["input_fingerprint"] for item in replay_results],
        "feasibility_statuses": [item["result"]["feasibility_status"] for item in live_results]
        == [item["result"]["feasibility_status"] for item in replay_results],
        "candidate_metrics": [item["current_evidence"] for item in live_results]
        == [item["current_evidence"] for item in replay_results],
        "configs": [item["result"].get("selected_website_acquisition_config") for item in live_results]
        == [item["result"].get("selected_website_acquisition_config") for item in replay_results],
        "routing": live["phase6d_routing"] == replay["phase6d_routing"],
        "ordering": [item["result"]["selected_website_resolution_plan_id"] for item in live_results]
        == [item["result"]["selected_website_resolution_plan_id"] for item in replay_results],
    }
    checks["all_match"] = all(checks.values())
    return checks


def validate_run(
    *,
    eligible_manifest_count: int,
    live: Any,
    replay: Any,
    replay_checks: dict[str, Any],
    replay_http_calls: list[dict[str, Any]],
    artifact_immutability: bool,
    upstream_immutability: bool,
) -> dict[str, bool]:
    live_result_count = len(live.selected_website_resolution_results)
    replay_result_count = len(replay.selected_website_resolution_results)
    checks = {
        "live_result_count_matches_manifest": live_result_count == eligible_manifest_count,
        "live_executed_plan_count_matches_manifest": (
            live.generation["executed_selected_website_plan_count"] == eligible_manifest_count
        ),
        "live_http_calls_possible_max_matches_manifest": (
            live.generation["http_calls_possible_max"] == eligible_manifest_count
        ),
        "live_new_fetch_count_matches_manifest": (
            live.generation["new_fetch_count"] == eligible_manifest_count
        ),
        "live_cache_hit_count_is_zero": live.generation["cache_hit_count"] == 0,
        "replay_result_count_matches_manifest": replay_result_count == eligible_manifest_count,
        "replay_executed_plan_count_matches_manifest": (
            replay.generation["executed_selected_website_plan_count"] == eligible_manifest_count
        ),
        "replay_http_calls_possible_max_matches_manifest": (
            replay.generation["http_calls_possible_max"] == eligible_manifest_count
        ),
        "replay_new_fetch_count_is_zero": replay.generation["new_fetch_count"] == 0,
        "replay_cache_hit_count_matches_manifest": (
            replay.generation["cache_hit_count"] == eligible_manifest_count
        ),
        "replay_http_guard_is_empty": replay_http_calls == [],
        "replay_outputs_match": replay_checks["all_match"] is True,
        "artifact_immutability": artifact_immutability,
        "upstream_immutability": upstream_immutability,
    }
    checks["all_pass"] = all(checks.values())
    return checks


def artifact_signatures(result_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    signatures = {}
    for execution in result_payload["selected_website_resolution_results"]:
        raw_ref = execution.get("raw_artifact_ref")
        if raw_ref:
            path = PROJECT_ROOT / raw_ref["artifact_path"]
            signatures[raw_ref["artifact_path"]] = artifact_signature(path)
        checkpoint = execution.get("current_inspection_checkpoint")
        if checkpoint:
            path = PROJECT_ROOT / checkpoint
            signatures[checkpoint] = artifact_signature(path)
    return dict(sorted(signatures.items()))


def upstream_signatures() -> dict[str, dict[str, Any]]:
    paths = list(UPSTREAM_PATHS)
    inspection_root = (
        PROJECT_ROOT
        / "outputs"
        / "planning"
        / "source_monitoring"
        / "diagnostics"
        / "phase5_source_evaluation"
        / "inspections"
    )
    paths.extend(sorted(inspection_root.glob("*/inspection.json")))
    signatures = {}
    for path in paths:
        if path.exists():
            signatures[relative(path)] = file_signature(path)
    return dict(sorted(signatures.items()))


def file_signature(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    stat = path.stat()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "mtime_ns": stat.st_mtime_ns,
    }


def git_context() -> dict[str, Any]:
    return {
        "branch": run_git("branch", "--show-current"),
        "head": run_git("rev-parse", "HEAD"),
        "log_10": run_git("log", "-10", "--oneline", "--decorate").splitlines(),
        "status_short": run_git("status", "--short").splitlines(),
        "python": sys.version,
        "root": str(PROJECT_ROOT),
    }


def run_git(*args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=PROJECT_ROOT,
        text=True,
        check=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


def write_report(summary: dict[str, Any]) -> None:
    sections = [
        ("Phase 6C Selected Website Resolution", {"verdict": summary["verdict"]}),
        ("Branch and HEAD", summary["git"]),
        ("Phase 6A Input Population", summary["phase6a_population"]),
        ("Phase 6B Routing Input", {"phase6b_input_hash": summary["phase6b_input_hash"]}),
        ("Policy", {
            "item_discovery_policy_version": summary["item_discovery_policy_version"],
            "feasibility_policy_version": summary["feasibility_policy_version"],
            "max_retained_item_candidates": summary["max_retained_item_candidates"],
            "max_items_per_run": summary["max_items_per_run"],
        }),
        ("Live Eligible Manifest", summary["manifest"]),
        ("Live Network and Resolution Outcomes", summary["live_results"]),
        ("Dynamic Validation Checks", summary["validation_checks"]),
        ("Per-Source Summary", summary["per_source_summary"]),
        ("Phase 6D Routing", summary["phase6d_routing"]),
        ("Replay Zero-HTTP Proof", summary["replay"]),
        ("Artifact Immutability", summary["artifact_immutability"]),
        ("Upstream Immutability", summary["upstream_immutability"]),
        ("Boundary", summary["phase6c_boundary"]),
    ]
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = "\n\n".join(
        f"# {title}\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}\n```"
        if index == 0
        else f"## {title}\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}\n```"
        for index, (title, payload) in enumerate(sections)
    )
    REPORT_FILE.write_text(text + "\n", encoding="utf-8")


def relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
