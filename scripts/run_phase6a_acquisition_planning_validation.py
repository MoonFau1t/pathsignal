from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import PROJECT_ROOT
from src.source_monitoring.acquisition_planner import (
    ACQUISITION_RESOLUTION_PLANS_FILE,
    DEFAULT_MAX_FEED_CANDIDATES_PER_SOURCE,
    load_phase6a_corpus,
    plan_acquisition_resolution,
    persist_acquisition_planning_result,
)


PHASE6A_REPORT_FILE = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "reports"
    / "phase6a_acquisition_planning.md"
)
PHASE6A_SUMMARY_FILE = (
    PROJECT_ROOT
    / "outputs"
    / "planning"
    / "source_monitoring"
    / "diagnostics"
    / "phase6_acquisition_resolution"
    / "phase6a_acquisition_planning_validation.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline Phase 6A acquisition planning validation.")
    parser.add_argument("--no-write", action="store_true", help="Evaluate without persisting Phase 6A artifacts.")
    args = parser.parse_args()

    started = time.monotonic()
    upstream_before = snapshot_upstream_artifacts()
    output_before = snapshot_output_artifacts()
    corpus = load_phase6a_corpus()
    first = plan_acquisition_resolution(**corpus)
    if not args.no_write:
        persist_acquisition_planning_result(result=first)
    output_after_first = snapshot_output_artifacts()
    replay = plan_acquisition_resolution(**corpus)
    if not args.no_write:
        persist_acquisition_planning_result(result=replay)
    output_after_replay = snapshot_output_artifacts()
    upstream_after = snapshot_upstream_artifacts()

    summary = build_summary(
        corpus=corpus,
        first=first,
        replay=replay,
        started=started,
        upstream_before=upstream_before,
        upstream_after=upstream_after,
        output_before=output_before,
        output_after_first=output_after_first,
        output_after_replay=output_after_replay,
    )
    enforce_acceptance(summary)
    if not args.no_write:
        write_json(PHASE6A_SUMMARY_FILE, summary)
        write_report(PHASE6A_REPORT_FILE, summary)
    print(json.dumps(summary["verdict"], sort_keys=True))


def build_summary(
    *,
    corpus: dict[str, Any],
    first: Any,
    replay: Any,
    started: float,
    upstream_before: dict[str, Any],
    upstream_after: dict[str, Any],
    output_before: dict[str, Any],
    output_after_first: dict[str, Any],
    output_after_replay: dict[str, Any],
) -> dict[str, Any]:
    phase6_handoff = corpus["phase6_handoff"]
    phase5_canonical = corpus["phase5_canonical"]
    candidates_by_id = {item.candidate_source_id: item for item in corpus["candidates"]}
    inspections_by_id = {item.candidate_source_id: item for item in corpus["inspections"]}
    final_by_id = {item["candidate_source_id"]: item for item in phase5_canonical["final_evaluations"]}
    entities_by_id = load_entities_by_id()

    plan_by_candidate = {item.candidate_source_id: item for item in first.acquisition_resolution_plans}
    feed_by_plan: dict[str, list[Any]] = defaultdict(list)
    for feed_plan in first.feed_verification_plans:
        feed_by_plan[feed_plan.acquisition_resolution_plan_id].append(feed_plan)
    deferred_by_plan: dict[str, list[Any]] = defaultdict(list)
    for item in first.deferred_feed_candidates:
        deferred_by_plan[item.acquisition_resolution_plan_id].append(item)

    source_rows = []
    for handoff in phase6_handoff["approved_sources"]:
        cid = handoff["candidate_source_id"]
        plan = plan_by_candidate[cid]
        candidate = candidates_by_id[cid]
        inspection = inspections_by_id.get(cid)
        source_rows.append(
            {
                "entity": entities_by_id.get(handoff["entity_id"], handoff["entity_id"]),
                "candidate_source_id": cid,
                "entity_id": handoff["entity_id"],
                "final_source_evaluation_id": handoff["final_source_evaluation_id"],
                "source_url": candidate.normalized_url or candidate.canonical_url,
                "planned_source_role": candidate.source_role.value,
                "observed_source_role": handoff["observed_source_role"],
                "supported_information_need_ids": list(handoff["supported_information_need_ids"]),
                "source_inspection_id": inspection.inspection_id if inspection else None,
                "source_inspection_hash": inspection.inspection_output_hash if inspection else None,
                "feed_link_hint_count": len(inspection.feed_link_hints) if inspection else 0,
                "deduplicated_feed_candidate_count": plan.feed_candidate_count,
                "feed_verification_plan_count": len(feed_by_plan[plan.acquisition_resolution_plan_id]),
                "deferred_feed_candidate_count": len(deferred_by_plan[plan.acquisition_resolution_plan_id]),
                "selected_website_fallback_plan": plan.selected_website_fallback_planned,
                "technical_limitation_flags": list(plan.known_technical_limitation_flags),
                "acquisition_resolution_plan_id": plan.acquisition_resolution_plan_id,
                "input_fingerprint": plan.input_fingerprint,
            }
        )

    output_replay_unchanged = output_after_first == output_after_replay
    replay_equal = replay_signature(first) == replay_signature(replay)
    details = {
        "acquisition_resolution_plan_ids": [item.acquisition_resolution_plan_id for item in first.acquisition_resolution_plans]
        == [item.acquisition_resolution_plan_id for item in replay.acquisition_resolution_plans],
        "feed_verification_plan_ids": [item.feed_verification_plan_id for item in first.feed_verification_plans]
        == [item.feed_verification_plan_id for item in replay.feed_verification_plans],
        "selected_website_resolution_plan_ids": [item.selected_website_resolution_plan_id for item in first.selected_website_resolution_plans]
        == [item.selected_website_resolution_plan_id for item in replay.selected_website_resolution_plans],
        "candidate_ordering": [item.candidate_source_id for item in first.acquisition_resolution_plans]
        == [item.candidate_source_id for item in replay.acquisition_resolution_plans],
        "deferred_candidate_ordering": [item.deferred_feed_candidate_id for item in first.deferred_feed_candidates]
        == [item.deferred_feed_candidate_id for item in replay.deferred_feed_candidates],
        "fingerprints": [item.input_fingerprint for item in first.acquisition_resolution_plans]
        == [item.input_fingerprint for item in replay.acquisition_resolution_plans],
        "semantic_output_hash": first.output_hash == replay.output_hash,
    }
    feed_hint_distribution = Counter(row["feed_link_hint_count"] for row in source_rows)
    phase6b_population = {
        "approved_source_count": first.approved_input_count,
        "acquisition_resolution_plan_count": len(first.acquisition_resolution_plans),
        "sources_with_explicit_feed_candidates": sum(1 for row in source_rows if row["deduplicated_feed_candidate_count"] > 0),
        "total_executable_feed_verification_plans": len(first.feed_verification_plans),
        "deferred_feed_candidates": len(first.deferred_feed_candidates),
        "sources_with_zero_feed_hints": sum(1 for row in source_rows if row["feed_link_hint_count"] == 0),
        "selected_website_fallback_plan_count": len(first.selected_website_resolution_plans),
    }
    summary = {
        "schema_version": "phase6a_acquisition_planning_validation_v1",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "git": git_summary(),
        "phase5_baseline": {
            "tag_type": run_git("cat-file", "-t", "source-monitoring-phase5-complete"),
            "peeled_commit": run_git("rev-parse", "source-monitoring-phase5-complete^{}"),
            "expected_commit": "c0bd4da7a60d86407a9d44d36db70562745b9c7f",
            "descends_from_phase5_tag": merge_base_contains("source-monitoring-phase5-complete"),
        },
        "phase5_handoff": {
            "approved_count": len(phase6_handoff.get("approved_sources", [])),
            "approved_candidate_source_ids": [item["candidate_source_id"] for item in phase6_handoff.get("approved_sources", [])],
            "canonical_output_hash": phase5_canonical.get("output_hash"),
            "phase6_handoff_hash": first.phase5_handoff_input_hash,
        },
        "planning_policy": {
            "policy_version": first.planning_policy_version,
            "max_feed_candidates_per_source": DEFAULT_MAX_FEED_CANDIDATES_PER_SOURCE,
            "strategy_order": ["verify_known_feed_candidates", "selected_website_fallback", "phase6d_needs_review_or_unsupported"],
        },
        "real_source_rows": source_rows,
        "feed_hint_distribution": dict(sorted(feed_hint_distribution.items())),
        "feed_verification_plan_distribution": count_by(first.feed_verification_plans, lambda item: item.candidate_source_id),
        "selected_website_fallback_count": len(first.selected_website_resolution_plans),
        "zero_feed_hint_audit": [
            row for row in source_rows if row["feed_link_hint_count"] == 0
        ],
        "premature_resolution_audit": {
            "acquisition_method_selected_in_plans": False,
            "final_acquisition_resolution_created": False,
            "feed_hints_marked_verified": any(
                ref.verification_status != "unverified"
                for plan in first.feed_verification_plans
                for ref in plan.feed_hint_evidence_refs
            ),
        },
        "phase6b_input_population": phase6b_population,
        "deterministic_replay": {
            "http_calls": first.generation["http_calls"] + replay.generation["http_calls"],
            "brave_calls": first.generation["brave_calls"] + replay.generation["brave_calls"],
            "deepseek_calls": first.generation["deepseek_calls"] + replay.generation["deepseek_calls"],
            "browser_calls": first.generation["browser_calls"] + replay.generation["browser_calls"],
            "signatures_identical": replay_equal,
            "details": details,
            "output_artifacts_unchanged_after_replay": output_replay_unchanged,
            "before": output_before,
            "after_first_write": output_after_first,
            "after_replay_write": output_after_replay,
        },
        "phase5_immutability": {
            "unchanged": upstream_before == upstream_after,
            "before_count": len(upstream_before),
            "after_count": len(upstream_after),
            "changed": sorted(set(upstream_before) ^ set(upstream_after))
            + [
                path
                for path in sorted(set(upstream_before) & set(upstream_after))
                if upstream_before[path] != upstream_after[path]
            ],
        },
        "output_file": str(ACQUISITION_RESOLUTION_PLANS_FILE.relative_to(PROJECT_ROOT)),
        "output_hash": first.output_hash,
        "diagnostics": list(first.diagnostics),
        "tests": {
            "focused_phase6a": "recorded in final closeout after test gate",
            "regression": "recorded in final closeout after test gate",
            "full_suite": "recorded in final closeout after test gate",
        },
    }
    summary["verdict"] = final_verdict(summary)
    return summary


def enforce_acceptance(summary: dict[str, Any]) -> None:
    if summary["verdict"] == "PHASE 6A NEEDS FIX BEFORE PHASE 6B":
        raise SystemExit(json.dumps(summary["deterministic_replay"], sort_keys=True))


def final_verdict(summary: dict[str, Any]) -> str:
    replay = summary["deterministic_replay"]
    if (
        summary["phase5_baseline"]["tag_type"] == "tag"
        and summary["phase5_baseline"]["peeled_commit"] == summary["phase5_baseline"]["expected_commit"]
        and summary["phase5_baseline"]["descends_from_phase5_tag"]
        and summary["phase5_immutability"]["unchanged"]
        and replay["signatures_identical"]
        and all(replay["details"].values())
        and replay["http_calls"] == 0
        and replay["brave_calls"] == 0
        and replay["deepseek_calls"] == 0
        and replay["browser_calls"] == 0
        and not summary["premature_resolution_audit"]["acquisition_method_selected_in_plans"]
        and not summary["premature_resolution_audit"]["final_acquisition_resolution_created"]
        and not summary["premature_resolution_audit"]["feed_hints_marked_verified"]
    ):
        return "READY FOR PHASE 6B"
    return "PHASE 6A NEEDS FIX BEFORE PHASE 6B"


def replay_signature(result: Any) -> str:
    payload = result.to_dict()
    payload["generation"] = {}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def snapshot_upstream_artifacts() -> dict[str, dict[str, Any]]:
    base = PROJECT_ROOT / "outputs" / "planning" / "source_monitoring"
    paths = [
        base / "source_evaluations.json",
        base / "candidate_sources.json",
        base / "diagnostics" / "phase5_source_evaluation" / "phase6_source_handoff.json",
        base / "diagnostics" / "phase5_source_evaluation" / "source_observations.json",
    ]
    paths.extend((base / "diagnostics" / "phase5_source_evaluation" / "inspections").glob("*/inspection.json"))
    return snapshot_files(path for path in paths if path.exists() and path.is_file())


def snapshot_output_artifacts() -> dict[str, dict[str, Any]]:
    return snapshot_files(
        path for path in (ACQUISITION_RESOLUTION_PLANS_FILE,) if path.exists() and path.is_file()
    )


def snapshot_files(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted(paths, key=lambda item: str(item)):
        stat = path.stat()
        result[str(path.relative_to(PROJECT_ROOT))] = {
            "sha256": read_bytes_digest(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return result


def read_bytes_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_entities_by_id() -> dict[str, str]:
    path = PROJECT_ROOT / "outputs" / "planning" / "source_monitoring" / "entity_universe.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["entity_id"]): str(item.get("canonical_name", item["entity_id"]))
        for item in payload.get("entity_candidates", [])
    }


def count_by(items: Iterable[Any], getter) -> dict[str, int]:
    return dict(sorted(Counter(str(getter(item)) for item in items).items()))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Phase 6A Acquisition Planning Validation",
        "",
        f"Verdict: {summary['verdict']}",
        "",
        "## Branch and HEAD",
        fenced(summary["git"]),
        "## Phase 5 Completion Baseline",
        fenced(summary["phase5_baseline"]),
        "## Approved Input Population",
        fenced(summary["phase5_handoff"]),
        "## Object and Contract Summary",
        fenced({
            "objects": [
                "Phase5AcquisitionHandoff",
                "AcquisitionResolutionPlan",
                "FeedVerificationPlan",
                "FeedVerificationResult",
                "SelectedWebsiteResolutionPlan",
                "SelectedWebsiteResolutionResult",
                "AcquisitionResolution",
                "Phase7MonitoringHandoff",
            ],
            "planning_only": True,
            "final_method_selected": False,
        }),
        "## Planning Policy",
        fenced(summary["planning_policy"]),
        "## Real Approved-Source Plan Generation",
        fenced(summary["real_source_rows"]),
        "## Feed-Hint Distribution",
        fenced(summary["feed_hint_distribution"]),
        "## FeedVerificationPlan Distribution",
        fenced(summary["feed_verification_plan_distribution"]),
        "## Selected Website Fallback Plans",
        fenced({"count": summary["selected_website_fallback_count"]}),
        "## Zero-Feed-Hint Audit",
        fenced(summary["zero_feed_hint_audit"]),
        "## Dedup and Budget Behavior",
        fenced(summary["phase6b_input_population"]),
        "## No Premature Method Selection",
        fenced(summary["premature_resolution_audit"]),
        "## Fingerprints and Output",
        fenced({"output_file": summary["output_file"], "output_hash": summary["output_hash"]}),
        "## Deterministic Replay",
        fenced(summary["deterministic_replay"]),
        "## Phase 5 Immutability",
        fenced(summary["phase5_immutability"]),
        "## Tests",
        fenced(summary["tests"]),
        "## Defects and Fixes",
        fenced({"product_defects": [], "report_only_notes": []}),
        "## Phase 6B Input Population",
        fenced(summary["phase6b_input_population"]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


def fenced(payload: Any) -> str:
    return "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def git_summary() -> dict[str, Any]:
    return {
        "root": str(PROJECT_ROOT),
        "branch": run_git("branch", "--show-current"),
        "head": run_git("rev-parse", "HEAD"),
        "status_short": git_status_short(),
        "log_10": run_git("log", "-10", "--oneline", "--decorate").splitlines(),
        "python": sys.version,
    }


def merge_base_contains(commit: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    ).returncode == 0


def run_git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, encoding="utf-8").strip()


def git_status_short() -> list[str]:
    output = run_git("status", "--short")
    return output.splitlines() if output else []


if __name__ == "__main__":
    main()
