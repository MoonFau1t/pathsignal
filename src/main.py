import sys
import hashlib
from pathlib import Path


if __package__ is None or __package__ == "":
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


from src.ai_filter import AIFilterClient, execute_ai_filter
from src.career_path_generator import generate_target_career_paths
from src.config import (
    AI_FILTER_DRY_RUN,
    AI_FILTER_MODEL,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
    MOCK_PIPELINE_OUTPUT_FILE,
    RSS_DRY_RUN,
    RSS_MAX_FEEDS,
    RSS_MAX_ITEMS_PER_FEED,
    BRAVE_API_KEY,
    SEARCH_API_DRY_RUN,
    SEARCH_API_MAX_PLANS,
    SEARCH_API_PLAN_OFFSET,
    SEARCH_API_TIMEOUT_SECONDS,
    SEARCH_SCOPE_FILE,
    SELECTED_WEBSITE_DRY_RUN,
    SELECTED_WEBSITE_MAX_ITEMS_PER_SITE,
    SELECTED_WEBSITE_MAX_SITES,
    TARGET_CAREER_PATH_MODEL,
    TARGET_CAREER_PATH_FORCE_REFRESH,
    TARGET_CAREER_PATH_PROMPT_VERSION,
    TARGET_CAREER_PATH_SCHEMA_VERSION,
    TARGET_CAREER_PATHS_FILE,
    USER_PREFERENCES_FILE,
    USER_PROFILE_FILE,
    ensure_project_directories,
    get_database_path,
)
from src.database.migrations import initialize_database
from src.database.repositories.career_signal_repository import CareerSignalRepository
from src.database.repositories.filter_decision_repository import (
    FilterDecisionRepository,
)
from src.database.repositories.planning_bundle_repository import (
    PlanningArtifactWrite,
    PlanningBundleRepository,
)
from src.database.repositories.pipeline_run_repository import PipelineRunRepository
from src.database.repositories.source_execution_repository import (
    SourceExecutionRepository,
)
from src.database.repositories.source_item_repository import SourceItemRepository
from src.mock_data import load_mock_raw_items
from src.normalizer import normalize_raw_items_to_career_signals
from src.pipeline import MockPipeline, execute_pipeline_runtime
from src.career_signal_priority import assess_and_score_career_signal_batch
from src.career_intelligence_interpretation import (
    CareerIntelligenceInterpretationClient,
)
from src.profile_loader import (
    load_user_preferences_from_json,
    load_user_profile_from_json,
)
from src.priority_assessment import PriorityAssessmentClient
from src.rss_client import RSSClient, execute_rss_feeds
from src.scope_loader import load_effective_search_scope_from_json
from src.search_api_client import BraveSearchClient, execute_search_api_plans
from src.search_plan_builder import build_search_plans
from src.search_query_generator import generate_search_queries
from src.selected_website_client import (
    SelectedWebsiteClient,
    execute_selected_websites,
)
from src.storage import save_json


def validate_required_planning_inputs() -> None:
    """
    Fail fast when required local planning inputs are missing.
    """

    required_inputs = [
        ("User profile", USER_PROFILE_FILE),
        ("User preferences", USER_PREFERENCES_FILE),
        ("Search scope", SEARCH_SCOPE_FILE),
    ]

    missing_inputs = [
        (label, path)
        for label, path in required_inputs
        if not path.exists()
    ]

    if missing_inputs:
        missing_messages = [
            f"{label} file is missing: {path}"
            for label, path in missing_inputs
        ]
        raise FileNotFoundError(
            "Required planning input file(s) are missing. "
            "Create the real local files before running the pipeline. "
            "Do not use sanitized example files for real runs. "
            + " ".join(missing_messages)
        )


def _file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None

    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _load_planning_artifacts() -> tuple[PlanningArtifactWrite, ...]:
    content_hash = _file_sha256(TARGET_CAREER_PATHS_FILE)

    if content_hash is None:
        return ()

    return (
        PlanningArtifactWrite(
            artifact_type="target_career_paths_cache",
            file_path=str(TARGET_CAREER_PATHS_FILE),
            content_hash=content_hash,
        ),
    )


def _report_pipeline_success(result, output_path) -> None:
    print("V1 Phase 10 Normalizer completed.")
    print(f"User profile loaded from: {USER_PROFILE_FILE}")
    print(f"User preferences loaded from: {USER_PREFERENCES_FILE}")
    print(f"Search scope loaded from: {SEARCH_SCOPE_FILE}")
    print(f"Target career path cache: {TARGET_CAREER_PATHS_FILE}")

    print("Search API provider: brave")
    print(f"Search API dry run: {SEARCH_API_DRY_RUN}")
    if SEARCH_API_DRY_RUN:
        print("Search API dry-run mode active: skipping Brave Search requests.")
    print(f"RSS dry run: {RSS_DRY_RUN}")
    print(f"Selected website dry run: {SELECTED_WEBSITE_DRY_RUN}")

    print(f"LLM provider: {LLM_PROVIDER}")
    print(f"TargetCareerPath model: {TARGET_CAREER_PATH_MODEL}")
    print(f"AI Filter model: {AI_FILTER_MODEL}")
    print(f"AI Filter dry run: {AI_FILTER_DRY_RUN}")

    print(f"Target career paths generated: {result.summary.total_target_career_paths}")
    print(f"Search queries generated: {result.summary.total_search_queries}")
    print(f"Search plans generated: {result.summary.total_search_plans}")

    print(f"Search API plans executed: {result.summary.total_search_api_plans_executed}")
    print(f"Search API plan offset: {SEARCH_API_PLAN_OFFSET}")
    print(f"Search API plans deferred: {result.summary.total_search_api_plans_deferred}")
    print(
        "Search API result parse failures: "
        f"{result.summary.total_search_api_result_failures}"
    )
    print(f"RSS feeds executed: {result.summary.total_rss_feeds_executed}")
    print(
        "Selected websites executed: "
        f"{result.summary.total_selected_websites_executed}"
    )

    print(f"Total raw items collected: {result.summary.total_raw_items}")
    print(
        "Raw items sent to AI Filter: "
        f"{result.summary.total_raw_items_sent_to_ai_filter}"
    )
    print(f"AI Filter results generated: {result.summary.total_ai_filter_results}")
    print(f"Filtered raw items kept: {result.summary.total_filtered_raw_items}")
    print(f"Raw items rejected: {result.summary.total_rejected_raw_items}")
    print(f"CareerSignals normalized: {result.summary.total_career_signals}")

    print(f"Output saved to: {output_path}")


def main() -> None:
    """
    Entry point for V1 Phase 10.
    """

    ensure_project_directories()
    validate_required_planning_inputs()
    database_path = get_database_path()
    initialize_database(database_path=database_path)
    pipeline_run_repository = PipelineRunRepository(database_path=database_path)
    source_execution_repository = SourceExecutionRepository(
        database_path=database_path
    )
    planning_bundle_repository = PlanningBundleRepository(
        database_path=database_path
    )
    source_item_repository = SourceItemRepository(database_path=database_path)
    career_signal_repository = CareerSignalRepository(database_path=database_path)
    filter_decision_repository = FilterDecisionRepository(
        database_path=database_path
    )

    search_client = BraveSearchClient(
        api_key=BRAVE_API_KEY,
        timeout_seconds=SEARCH_API_TIMEOUT_SECONDS,
        dry_run=SEARCH_API_DRY_RUN,
    )

    rss_client = RSSClient(
        timeout_seconds=SEARCH_API_TIMEOUT_SECONDS,
        dry_run=RSS_DRY_RUN,
        max_items_per_feed=RSS_MAX_ITEMS_PER_FEED,
    )

    selected_website_client = SelectedWebsiteClient(
        timeout_seconds=SEARCH_API_TIMEOUT_SECONDS,
        dry_run=SELECTED_WEBSITE_DRY_RUN,
        max_items_per_site=SELECTED_WEBSITE_MAX_ITEMS_PER_SITE,
    )

    ai_filter_client = AIFilterClient(
        provider=LLM_PROVIDER,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        model=AI_FILTER_MODEL,
        dry_run=AI_FILTER_DRY_RUN,
    )

    user_preferences = load_user_preferences_from_json(USER_PREFERENCES_FILE)
    priority_assessment_client: PriorityAssessmentClient | None = None
    interpretation_client: CareerIntelligenceInterpretationClient | None = None

    def priority_assessor(**kwargs):
        nonlocal priority_assessment_client
        if priority_assessment_client is None:
            priority_assessment_client = PriorityAssessmentClient(
                provider=LLM_PROVIDER,
                api_key=LLM_API_KEY,
                base_url=LLM_BASE_URL,
                model=AI_FILTER_MODEL,
            )
        return assess_and_score_career_signal_batch(
            priority_assessment_client=priority_assessment_client,
            **kwargs,
        )

    def interpretation_executor(context):
        nonlocal interpretation_client
        if interpretation_client is None:
            interpretation_client = CareerIntelligenceInterpretationClient(
                provider=LLM_PROVIDER,
                api_key=LLM_API_KEY,
                base_url=LLM_BASE_URL,
                model=AI_FILTER_MODEL,
            )
        return interpretation_client.interpret(context)

    pipeline = MockPipeline(
        raw_item_loader=load_mock_raw_items,
        user_profile_loader=lambda: load_user_profile_from_json(USER_PROFILE_FILE),
        search_scope_loader=lambda: load_effective_search_scope_from_json(
            SEARCH_SCOPE_FILE,
            user_preferences,
        ),
        career_path_generator=(
            lambda user_profile: generate_target_career_paths(
                user_profile=user_profile,
                user_preferences=user_preferences,
            )
        ),
        search_query_generator=generate_search_queries,
        search_plan_builder=build_search_plans,
        search_api_executor=(
            lambda search_plans, lifecycle=None: execute_search_api_plans(
                search_plans=search_plans,
                client=search_client,
                max_plans=SEARCH_API_MAX_PLANS,
                plan_offset=SEARCH_API_PLAN_OFFSET,
                execution_lifecycle=lifecycle,
            )
        ),
        rss_executor=(
            lambda search_scope, search_plans, lifecycle=None: execute_rss_feeds(
                rss_feeds=search_scope.rss_feeds,
                search_plans=search_plans,
                client=rss_client,
                max_feeds=RSS_MAX_FEEDS,
                execution_lifecycle=lifecycle,
            )
        ),
        selected_website_executor=(
            lambda search_scope, search_plans, lifecycle=None: (
                execute_selected_websites(
                    selected_websites=search_scope.selected_websites,
                    search_plans=search_plans,
                    client=selected_website_client,
                    max_sites=SELECTED_WEBSITE_MAX_SITES,
                    execution_lifecycle=lifecycle,
                )
            )
        ),
        ai_filter_executor=(
            lambda raw_items, user_profile, target_career_paths: execute_ai_filter(
                raw_items=raw_items,
                user_profile=user_profile,
                target_career_paths=target_career_paths,
                client=ai_filter_client,
            )
        ),
        normalizer=normalize_raw_items_to_career_signals,
        source_item_repository=source_item_repository,
        career_signal_repository=career_signal_repository,
        planning_bundle_repository=planning_bundle_repository,
        user_preferences_loader=lambda: user_preferences,
        planning_model_provider=LLM_PROVIDER,
        planning_model_name=TARGET_CAREER_PATH_MODEL,
        planning_prompt_version=TARGET_CAREER_PATH_PROMPT_VERSION,
        planning_generator_config={
            "target_career_path_schema_version": (
                TARGET_CAREER_PATH_SCHEMA_VERSION
            ),
            "search_query_max_queries_per_path": 8,
            "search_plan_builder": "rule_based_phase_6",
        },
        planning_force_refresh=TARGET_CAREER_PATH_FORCE_REFRESH,
        profile_source_path=str(USER_PROFILE_FILE),
        profile_source_file_hash=_file_sha256(USER_PROFILE_FILE),
        profile_schema_version=None,
        planning_artifact_loader=_load_planning_artifacts,
        pipeline_run_repository=pipeline_run_repository,
        source_execution_repository=source_execution_repository,
        execution_mode="live",
        filter_decision_repository=filter_decision_repository,
        ai_filter_execution_mode=(
            "dry_run" if AI_FILTER_DRY_RUN else "live"
        ),
        ai_filter_provider=LLM_PROVIDER,
        ai_filter_model=AI_FILTER_MODEL,
        priority_assessor=priority_assessor,
        interpretation_executor=interpretation_executor,
    )

    execute_pipeline_runtime(
        pipeline,
        output_persister=lambda result: save_json(
            data=result,
            output_path=MOCK_PIPELINE_OUTPUT_FILE,
        ),
        success_reporter=_report_pipeline_success,
    )


if __name__ == "__main__":
    main()
