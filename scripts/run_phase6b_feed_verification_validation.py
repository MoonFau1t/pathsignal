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
from src.source_monitoring.feed_verifier import (
    FEED_DOCUMENT_PARSER_VERSION,
    FEED_VERIFICATION_RESULTS_FILE,
    FEED_VERIFIER_POLICY_VERSION,
    artifact_signature,
    build_phase6b_source_fetcher,
    execute_feed_verification_plans,
    load_phase6a_planning_result,
    persist_feed_verification_results,
)


SUMMARY_FILE = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase6_acquisition"
    / "feed_verification"
    / "phase6b_feed_verification_validation.json"
)
REPORT_FILE = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "reports"
    / "phase6b_feed_verification.md"
)
UPSTREAM_PATHS = (
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "source_evaluations.json",
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "candidate_sources.json",
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "acquisition_resolution_plans.json",
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
        raise AssertionError("HTTP attempted during Phase 6B replay")


def main() -> int:
    planning = load_phase6a_planning_result()
    upstream_before = upstream_signatures()
    manifest = build_manifest(planning)
    live = execute_feed_verification_plans(
        planning_result=planning,
        fetcher=build_phase6b_source_fetcher(cache_enabled=False),
        generation_mode="phase6b_live_feed_verification",
    )
    persist_feed_verification_results(result_set=live)
    raw_before = raw_artifact_signatures(live)

    guard = GuardHTTPSession()
    replay = execute_feed_verification_plans(
        planning_result=planning,
        fetcher=build_phase6b_source_fetcher(session=guard),
        generation_mode="phase6b_cache_replay",
    )
    raw_after = raw_artifact_signatures(replay)
    upstream_after = upstream_signatures()
    replay_checks = compare_replay(live=live.to_dict(), replay=replay.to_dict())
    raw_immutability = raw_before == raw_after
    upstream_immutability = upstream_before == upstream_after
    verdict = (
        "PHASE 6B LIVE VERIFICATION PASSED"
        if replay_checks["all_match"]
        and guard.calls == []
        and raw_immutability
        and upstream_immutability
        and len(live.feed_verification_results) == len(planning.feed_verification_plans)
        else "PHASE 6B NEEDS FIX BEFORE PHASE 6C"
    )
    summary = {
        "schema_version": "phase6b_feed_verification_validation_v1",
        "verdict": verdict,
        "git": git_context(),
        "parser_version": FEED_DOCUMENT_PARSER_VERSION,
        "verification_policy_version": FEED_VERIFIER_POLICY_VERSION,
        "phase6a_population": {
            "approved_source_count": planning.approved_input_count,
            "acquisition_resolution_plan_count": len(planning.acquisition_resolution_plans),
            "feed_verification_plan_count": len(planning.feed_verification_plans),
            "selected_website_resolution_plan_count": len(planning.selected_website_resolution_plans),
            "deferred_feed_candidate_count": len(planning.deferred_feed_candidates),
            "phase6a_output_hash": planning.output_hash,
        },
        "manifest": manifest,
        "maximum_external_feed_requests": len(planning.feed_verification_plans),
        "live_result_file": relative(FEED_VERIFICATION_RESULTS_FILE),
        "live_result_output_hash": live.output_hash,
        "result_distribution": live.result_distribution,
        "per_source_summary": live.per_source_summary,
        "phase6c_routing": live.phase6c_routing,
        "live_results": [summarize_execution(item.to_dict()) for item in live.feed_verification_results],
        "replay": {
            "guard_http_calls": len(guard.calls),
            "checks": replay_checks,
            "new_fetch_count": replay.generation["new_fetch_count"],
            "cache_hit_count": replay.generation["cache_hit_count"],
            "output_hash": replay.output_hash,
        },
        "raw_artifact_immutability": {
            "before": raw_before,
            "after": raw_after,
            "unchanged": raw_immutability,
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
        "phase6b_boundary": {
            "brave_calls": 0,
            "deepseek_calls": 0,
            "llm_calls": 0,
            "generic_feed_discovery": False,
            "selected_website_execution": False,
            "acquisition_resolution_created": False,
            "final_acquisition_method_selected": False,
        },
    }
    write_json(SUMMARY_FILE, summary)
    write_report(summary)
    print(verdict)
    return 0 if verdict != "PHASE 6B NEEDS FIX BEFORE PHASE 6C" else 1


def build_manifest(planning: Any) -> list[dict[str, Any]]:
    acquisition_by_id = {
        item.acquisition_resolution_plan_id: item
        for item in planning.acquisition_resolution_plans
    }
    rows = []
    for plan in sorted(planning.feed_verification_plans, key=lambda item: item.feed_verification_plan_id):
        acquisition = acquisition_by_id[plan.acquisition_resolution_plan_id]
        request = build_phase6b_source_fetcher(session=GuardHTTPSession()).build_request(plan.feed_candidate_url)
        rows.append(
            {
                "feed_verification_plan_id": plan.feed_verification_plan_id,
                "parent_acquisition_resolution_plan_id": plan.acquisition_resolution_plan_id,
                "candidate_source_id": plan.candidate_source_id,
                "entity_id": acquisition.entity_id,
                "approved_source_url": acquisition.source_url,
                "feed_candidate_url": plan.feed_candidate_url,
                "candidate_format_hint": plan.candidate_format_hint.value,
                "feed_hint_evidence_refs": [item.to_dict() for item in plan.feed_hint_evidence_refs],
                "verification_policy_version": plan.verification_policy_version,
                "parser_policy_version": plan.parser_policy_version,
                "input_fingerprint": plan.input_fingerprint,
                "fetch_request_fingerprint": request.request_fingerprint,
            }
        )
    return rows


def summarize_execution(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload["result"]
    plan = payload["plan"]
    raw_ref = payload.get("raw_artifact_ref")
    return {
        "feed_verification_result_id": result["feed_verification_result_id"],
        "feed_verification_plan_id": result["feed_verification_plan_id"],
        "candidate_source_id": result["candidate_source_id"],
        "parent_acquisition_resolution_plan_id": plan["acquisition_resolution_plan_id"],
        "requested_url": result["feed_candidate_url"],
        "final_url": result["final_url"],
        "http_status": result["http_status"],
        "fetch_status": result["fetch_status"],
        "fetch_cache_hit": payload["fetch_cache_hit"],
        "content_type": result["content_type"],
        "redirect_count": len(result["redirect_chain"]),
        "byte_size": None if raw_ref is None else raw_ref["byte_size"],
        "body_sha": None if raw_ref is None else raw_ref["sha256"],
        "parse_status": result["parse_status"],
        "verified_feed_format": result["verified_feed_format"],
        "feed_title": result["feed_title"],
        "feed_home_link": result["feed_home_link"],
        "total_entry_count": payload["total_entry_count"],
        "sampled_entry_count": result["sampled_entry_count"],
        "unique_identity_count": payload["unique_entry_identity_count"],
        "duplicate_identity_count": payload["duplicate_entry_identity_count"],
        "usable_url_count": result["valid_entry_url_count"],
        "stable_identity_support": result["stable_item_identity_support"],
        "title_support": result["title_support"],
        "entries_with_titles": payload["entries_with_titles"],
        "publication_date_support": result["publication_date_support"],
        "entries_with_parseable_dates": payload["entries_with_parseable_dates"],
        "source_relationship_status": payload["source_relationship_status"],
        "source_relationship_diagnostic": payload["source_relationship_diagnostic"],
        "verification_status": result["verification_status"],
        "usable_for_monitoring": result["usable_for_monitoring"],
        "reason_codes": payload["reason_codes"],
        "diagnostics": result["diagnostics"],
    }


def compare_replay(*, live: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    live_results = live["feed_verification_results"]
    replay_results = replay["feed_verification_results"]
    checks = {
        "result_count": len(live_results) == len(replay_results),
        "output_hash": live["output_hash"] == replay["output_hash"],
        "result_ids": [item["result"]["feed_verification_result_id"] for item in live_results]
        == [item["result"]["feed_verification_result_id"] for item in replay_results],
        "fingerprints": [item["result"]["input_fingerprint"] for item in live_results]
        == [item["result"]["input_fingerprint"] for item in replay_results],
        "parse_statuses": [item["result"]["parse_status"] for item in live_results]
        == [item["result"]["parse_status"] for item in replay_results],
        "formats": [item["result"]["verified_feed_format"] for item in live_results]
        == [item["result"]["verified_feed_format"] for item in replay_results],
        "sampled_entries": [item["sampled_entries"] for item in live_results]
        == [item["sampled_entries"] for item in replay_results],
        "usability_states": [item["result"]["usable_for_monitoring"] for item in live_results]
        == [item["result"]["usable_for_monitoring"] for item in replay_results],
        "reason_codes": [item["reason_codes"] for item in live_results]
        == [item["reason_codes"] for item in replay_results],
        "ordering": [item["result"]["feed_verification_plan_id"] for item in live_results]
        == [item["result"]["feed_verification_plan_id"] for item in replay_results],
    }
    checks["all_match"] = all(checks.values())
    return checks


def raw_artifact_signatures(result_set: Any) -> dict[str, dict[str, Any]]:
    signatures = {}
    for execution in result_set.feed_verification_results:
        raw_ref = execution.raw_artifact_ref
        if raw_ref:
            path = PROJECT_ROOT / raw_ref["artifact_path"]
            signatures[raw_ref["artifact_path"]] = artifact_signature(path)
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
        ("Phase 6B Feed Verification", {"verdict": summary["verdict"]}),
        ("Branch and HEAD", summary["git"]),
        ("Phase 6A Input Population", summary["phase6a_population"]),
        ("Parser and Policy", {
            "parser_version": summary["parser_version"],
            "verification_policy_version": summary["verification_policy_version"],
        }),
        ("Live 4-Plan Manifest", summary["manifest"]),
        ("Live Network and Verification Outcomes", summary["live_results"]),
        ("Per-Source Summary", summary["per_source_summary"]),
        ("Phase 6C Routing", summary["phase6c_routing"]),
        ("Replay Zero-HTTP Proof", summary["replay"]),
        ("Raw Artifact Immutability", summary["raw_artifact_immutability"]),
        ("Upstream Immutability", summary["upstream_immutability"]),
        ("Boundary", summary["phase6b_boundary"]),
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
