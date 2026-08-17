from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORTS))

from src.config import PROJECT_ROOT
from src.source_monitoring.acquisition_resolver import (
    ACQUISITION_RESOLUTION_DIAGNOSTIC_ROOT,
    ACQUISITION_RESOLUTIONS_FILE,
    FINAL_ACQUISITION_RESOLUTION_POLICY_VERSION,
    file_signature,
    load_phase6d_inputs,
    persist_final_acquisition_resolution_results,
    resolve_acquisition_plans,
)


SUMMARY_FILE = ACQUISITION_RESOLUTION_DIAGNOSTIC_ROOT / "phase6d_final_acquisition_resolution_validation.json"
REPORT_FILE = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "reports"
    / "phase6d_final_acquisition_resolution.md"
)
UPSTREAM_PATHS = (
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "source_evaluations.json",
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "candidate_sources.json",
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "acquisition_resolution_plans.json",
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "feed_verification_results.json",
    PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "selected_website_resolution_results.json",
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase5_source_evaluation"
    / "phase6_source_handoff.json",
)


def main() -> int:
    planning, feed_payload, website_payload = load_phase6d_inputs()
    upstream_before = upstream_signatures()
    manifest = build_manifest(
        planning=planning,
        feed_payload=feed_payload,
        website_payload=website_payload,
    )
    live = resolve_acquisition_plans(
        planning_result=planning,
        feed_verification_result_payload=feed_payload,
        selected_website_result_payload=website_payload,
        generation_mode="phase6d_offline_final_acquisition_resolution",
    )
    persist_final_acquisition_resolution_results(result_set=live)
    replay = resolve_acquisition_plans(
        planning_result=planning,
        feed_verification_result_payload=feed_payload,
        selected_website_result_payload=website_payload,
        generation_mode="phase6d_offline_replay",
    )
    replay_checks = compare_replay(live=live.to_dict(), replay=replay.to_dict())
    upstream_after = upstream_signatures()
    upstream_immutability = upstream_before == upstream_after
    external_calls_zero = all(
        live.generation[key] == 0 and replay.generation[key] == 0
        for key in ("http_calls", "brave_calls", "deepseek_calls", "llm_calls", "browser_calls")
    )
    verdict = (
        "PHASE 6D FINAL ACQUISITION RESOLUTION PASSED"
        if replay_checks["all_match"]
        and upstream_immutability
        and external_calls_zero
        and live.population_accounting["all_plans_resolved_once"]
        and live.phase7_handoff_accounting["handoff_matches_resolved_sources"]
        else "PHASE 6D NEEDS FIX BEFORE CLOSEOUT"
    )
    summary = {
        "schema_version": "phase6d_final_acquisition_resolution_validation_v1",
        "verdict": verdict,
        "git": git_context(),
        "resolution_policy_version": FINAL_ACQUISITION_RESOLUTION_POLICY_VERSION,
        "phase6_input_hashes": {
            "phase6a": planning.output_hash,
            "phase6b": feed_payload.get("output_hash"),
            "phase6c": website_payload.get("output_hash"),
        },
        "manifest": manifest,
        "external_calls": {
            "http": live.generation["http_calls"],
            "brave": live.generation["brave_calls"],
            "deepseek": live.generation["deepseek_calls"],
            "llm": live.generation["llm_calls"],
            "browser": live.generation["browser_calls"],
        },
        "live_result_file": relative(ACQUISITION_RESOLUTIONS_FILE),
        "live_result_output_hash": live.output_hash,
        "population_accounting": live.population_accounting,
        "resolution_distribution": live.resolution_distribution,
        "method_distribution": live.method_distribution,
        "reason_code_summary": live.reason_code_summary,
        "phase7_handoff_accounting": live.phase7_handoff_accounting,
        "final_results": [summarize_execution(item) for item in live.to_dict()["acquisition_resolution_results"]],
        "phase7_handoffs": live.to_dict()["phase7_monitoring_handoffs"],
        "needs_review_backlog": live.needs_review_backlog,
        "unsupported_provenance": live.unsupported_provenance,
        "multi_feed_audits": live.multi_feed_audits,
        "sap_multi_feed_audit": [
            item for item in live.multi_feed_audits
            if item["usable_verified_feed_count"] > 1
        ],
        "ieee_composition": composition_for_reason(live.to_dict(), "feed_empty_or_insufficient"),
        "qianzhan_composition": composition_for_reason(live.to_dict(), "no_known_feed_candidate_verified"),
        "replay": {
            "checks": replay_checks,
            "output_hash": replay.output_hash,
            "external_calls": {
                "http": replay.generation["http_calls"],
                "brave": replay.generation["brave_calls"],
                "deepseek": replay.generation["deepseek_calls"],
                "llm": replay.generation["llm_calls"],
                "browser": replay.generation["browser_calls"],
            },
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
        "phase6d_boundary": {
            "http_calls": 0,
            "brave_calls": 0,
            "deepseek_calls": 0,
            "llm_calls": 0,
            "browser_calls": 0,
            "feed_parser_calls": 0,
            "html_inspection_calls": 0,
            "source_item_creation": False,
            "monitoring_execution_started": False,
            "phase7_started": False,
        },
        "test_gate": {
            "validation_runner_executes_tests": False,
            "required_commands": [
                "python -m unittest tests.test_source_monitoring_acquisition_resolver_phase6d -v",
                "python -m unittest tests.test_source_monitoring_acquisition_phase6a tests.test_source_monitoring_feed_verifier_phase6b tests.test_source_monitoring_selected_website_resolver_phase6c tests.test_source_monitoring_acquisition_resolver_phase6d -v",
                "python -m unittest tests.test_source_monitoring_source_final_evaluator_phase5f tests.test_source_monitoring_acquisition_phase6a tests.test_source_monitoring_feed_verifier_phase6b tests.test_source_monitoring_selected_website_resolver_phase6c tests.test_source_monitoring_acquisition_resolver_phase6d -v",
                "python -B -m unittest discover -s tests -v",
            ],
        },
    }
    write_json(SUMMARY_FILE, summary)
    write_report(summary)
    print(verdict)
    return 0 if verdict != "PHASE 6D NEEDS FIX BEFORE CLOSEOUT" else 1


def build_manifest(*, planning: Any, feed_payload: dict[str, Any], website_payload: dict[str, Any]) -> list[dict[str, Any]]:
    feed_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for execution in feed_payload.get("feed_verification_results", []):
        result = execution["result"]
        feed_by_candidate.setdefault(str(result["candidate_source_id"]), []).append(
            {
                "feed_verification_result_id": result["feed_verification_result_id"],
                "feed_candidate_url": result["feed_candidate_url"],
                "final_url": result["final_url"],
                "verified_feed_format": result["verified_feed_format"],
                "verification_status": result["verification_status"],
                "usable_for_monitoring": result["usable_for_monitoring"],
            }
        )
    website_by_candidate = {
        str(item["result"]["candidate_source_id"]): {
            "selected_website_resolution_result_id": item["result"]["selected_website_resolution_result_id"],
            "feasibility_status": item["result"]["feasibility_status"],
            "selected_website_acquisition_config_id": (
                item["result"].get("selected_website_acquisition_config") or {}
            ).get("selected_website_acquisition_config_id"),
        }
        for item in website_payload.get("selected_website_resolution_results", [])
    }
    return [
        {
            "candidate_source_id": plan.candidate_source_id,
            "entity_id": plan.entity_id,
            "final_source_evaluation_id": plan.final_source_evaluation_id,
            "acquisition_resolution_plan_id": plan.acquisition_resolution_plan_id,
            "source_url": plan.source_url,
            "observed_source_role": plan.observed_source_role.value,
            "supported_information_need_ids": list(plan.supported_information_need_ids),
            "feed_evidence": feed_by_candidate.get(plan.candidate_source_id, []),
            "selected_website_evidence": website_by_candidate.get(plan.candidate_source_id),
        }
        for plan in sorted(planning.acquisition_resolution_plans, key=lambda item: item.candidate_source_id)
    ]


def summarize_execution(payload: dict[str, Any]) -> dict[str, Any]:
    resolution = payload["resolution"]
    handoff = payload.get("phase7_handoff")
    primary_feed = payload.get("primary_feed_evidence")
    website = payload.get("selected_website_result")
    return {
        "candidate_source_id": resolution["candidate_source_id"],
        "entity_id": resolution["entity_id"],
        "final_source_evaluation_id": resolution["final_source_evaluation_id"],
        "acquisition_resolution_plan_id": resolution["acquisition_resolution_plan_id"],
        "acquisition_resolution_id": resolution["acquisition_resolution_id"],
        "resolution_status": resolution["resolution_status"],
        "acquisition_method": resolution["acquisition_method"],
        "feed_verification_result_ids": resolution["feed_verification_result_ids"],
        "selected_website_resolution_result_id": resolution["selected_website_resolution_result_id"],
        "selected_acquisition_config_ref": resolution["selected_acquisition_config_ref"],
        "verified_feed_format": resolution["verified_feed_format"],
        "primary_feed_verification_result_id": (
            primary_feed["feed_verification_result_id"] if primary_feed else None
        ),
        "primary_feed_final_url": primary_feed["final_url"] if primary_feed else None,
        "alternate_verified_feed_result_ids": [
            item["feed_verification_result_id"] for item in payload.get("alternate_feed_evidence", [])
        ],
        "selected_website_feasibility_status": (
            website["feasibility_status"] if website else None
        ),
        "phase7_monitoring_handoff_id": (
            handoff["phase7_monitoring_handoff_id"] if handoff else None
        ),
        "reason_codes": resolution["resolution_reason_codes"],
        "technical_limitation_flags": resolution["technical_limitation_flags"],
    }


def compare_replay(*, live: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    live_results = live["acquisition_resolution_results"]
    replay_results = replay["acquisition_resolution_results"]
    checks = {
        "result_count": len(live_results) == len(replay_results),
        "output_hash": live["output_hash"] == replay["output_hash"],
        "resolution_ids": [item["resolution"]["acquisition_resolution_id"] for item in live_results]
        == [item["resolution"]["acquisition_resolution_id"] for item in replay_results],
        "decisions": [item["resolution"]["resolution_status"] for item in live_results]
        == [item["resolution"]["resolution_status"] for item in replay_results],
        "methods": [item["resolution"]["acquisition_method"] for item in live_results]
        == [item["resolution"]["acquisition_method"] for item in replay_results],
        "primary_feed_selection": [item["resolution_trace"]["selected_primary_feed_result_id"] for item in live_results]
        == [item["resolution_trace"]["selected_primary_feed_result_id"] for item in replay_results],
        "alternate_evidence": [item["resolution_trace"]["alternate_usable_feed_result_ids"] for item in live_results]
        == [item["resolution_trace"]["alternate_usable_feed_result_ids"] for item in replay_results],
        "handoff_ids": [
            item["phase7_handoff"]["phase7_monitoring_handoff_id"]
            if item.get("phase7_handoff")
            else None
            for item in live_results
        ] == [
            item["phase7_handoff"]["phase7_monitoring_handoff_id"]
            if item.get("phase7_handoff")
            else None
            for item in replay_results
        ],
        "reason_codes": [item["resolution"]["resolution_reason_codes"] for item in live_results]
        == [item["resolution"]["resolution_reason_codes"] for item in replay_results],
        "fingerprints": [item["resolution"]["input_fingerprint"] for item in live_results]
        == [item["resolution"]["input_fingerprint"] for item in replay_results],
    }
    checks["all_match"] = all(checks.values())
    return checks


def composition_for_reason(result_payload: dict[str, Any], reason_code: str) -> list[dict[str, Any]]:
    return [
        summarize_execution(item)
        for item in result_payload["acquisition_resolution_results"]
        if reason_code in item["resolution"]["resolution_reason_codes"]
    ]


def upstream_signatures() -> dict[str, dict[str, Any]]:
    paths = list(UPSTREAM_PATHS)
    roots = (
        PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "diagnostics" / "phase5_source_evaluation",
        PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "diagnostics" / "phase6_acquisition" / "feed_verification",
        PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "diagnostics" / "phase6_acquisition" / "selected_website_resolution",
    )
    for root in roots:
        if root.exists():
            paths.extend(path for path in sorted(root.rglob("*")) if path.is_file())
    signatures = {
        relative(path): file_signature(path)
        for path in sorted(set(paths), key=lambda item: str(item))
        if path.exists()
    }
    return dict(sorted(signatures.items()))


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
        ("Phase 6D Final Acquisition Resolution", {"verdict": summary["verdict"]}),
        ("Git Baseline", summary["git"]),
        ("Input Accounting", summary["population_accounting"]),
        ("Phase 6 Input Hashes", summary["phase6_input_hashes"]),
        ("Resolver Policy", {"resolution_policy_version": summary["resolution_policy_version"]}),
        ("Manifest", summary["manifest"]),
        ("SAP Multi-Feed Audit", summary["sap_multi_feed_audit"]),
        ("IEEE Composition", summary["ieee_composition"]),
        ("Qianzhan Composition", summary["qianzhan_composition"]),
        ("Final Results", summary["final_results"]),
        ("Resolution Distribution", summary["resolution_distribution"]),
        ("Method Distribution", summary["method_distribution"]),
        ("Phase 7 Handoffs", summary["phase7_handoffs"]),
        ("Needs Review Backlog", summary["needs_review_backlog"]),
        ("Unsupported Provenance", summary["unsupported_provenance"]),
        ("Replay", summary["replay"]),
        ("Upstream Immutability", summary["upstream_immutability"]),
        ("Tests", summary["test_gate"]),
        ("Boundary", summary["phase6d_boundary"]),
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
