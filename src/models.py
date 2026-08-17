from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SourceType(str, Enum):
    """
    Type of raw information source.
    """

    MOCK_JOB = "mock_job"
    MOCK_NEWS = "mock_news"
    SEARCH_API = "search_api"
    RSS = "rss"
    SELECTED_WEBSITE = "selected_website"


class SignalCategory(str, Enum):
    """
    Category of career-related signal.
    """

    JOB = "job"
    NEWS = "news"
    COMPANY = "company"
    FUNDING = "funding"
    MARKET_TREND = "market_trend"
    UNKNOWN = "unknown"


class CareerPathCategory(str, Enum):
    """
    High-level career path category generated from the user profile.
    """

    CORPORATE_STRATEGY = "corporate_strategy"
    AI_STRATEGY = "ai_strategy"
    VENTURE_CAPITAL = "venture_capital"
    TECH_CONSULTING = "tech_consulting"
    BOUTIQUE_FA = "boutique_fa"
    MARKET_RESEARCH = "market_research"
    UNKNOWN = "unknown"


class SearchQueryType(str, Enum):
    """
    Type of search query generated from a TargetCareerPath.
    """

    JOB_SEARCH = "job_search"
    COMPANY_DISCOVERY = "company_discovery"
    INDUSTRY_NEWS = "industry_news"
    FUNDING_SIGNAL = "funding_signal"
    GENERAL_RESEARCH = "general_research"


def _safe_string(value: Any, default: str = "") -> str:
    """
    Convert a value into a string while treating None as missing.
    """

    if value is None:
        return default

    return str(value)


def _safe_list(value: Any) -> list[Any]:
    """
    Return a list value or an empty list when input is missing/invalid.
    """

    if isinstance(value, list):
        return value

    return []


def _safe_dict(value: Any) -> dict[str, Any]:
    """
    Return a dictionary value or an empty dictionary.
    """

    if isinstance(value, dict):
        return value

    return {}


def _safe_string_list(value: Any) -> list[str]:
    """
    Return a list of strings, dropping missing values.
    """

    return [
        str(item)
        for item in _safe_list(value)
        if item is not None
    ]


@dataclass
class UserProfile:
    """
    User background input for the V1 pipeline.
    """

    profile_id: str
    name: str
    background_summary: str
    education: list[dict[str, Any]] = field(default_factory=list)
    work_experience: list[dict[str, Any]] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    preferred_locations: list[str] = field(default_factory=list)
    preferred_roles: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    raw_resume_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserProfile":
        if not isinstance(data, dict):
            data = {}

        return cls(
            profile_id=_safe_string(
                data.get("profile_id"),
                "default_user_profile",
            ),
            name=_safe_string(data.get("name")),
            background_summary=_safe_string(data.get("background_summary")),
            education=[
                item
                for item in _safe_list(data.get("education"))
                if isinstance(item, dict)
            ],
            work_experience=[
                item
                for item in _safe_list(data.get("work_experience"))
                if isinstance(item, dict)
            ],
            skills=_safe_string_list(data.get("skills")),
            interests=_safe_string_list(data.get("interests")),
            preferred_locations=_safe_string_list(
                data.get("preferred_locations")
            ),
            preferred_roles=_safe_string_list(data.get("preferred_roles")),
            constraints=_safe_string_list(data.get("constraints")),
            raw_resume_text=_safe_string(data.get("raw_resume_text")),
            metadata=_safe_dict(data.get("metadata")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TargetCareerPath:
    """
    Career path generated from UserProfile.
    """

    path_id: str
    title: str
    category: CareerPathCategory
    description: str
    fit_score: float
    rationale: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    suggested_roles: list[str] = field(default_factory=list)
    search_seed_terms: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchQuery:
    """
    Search instruction generated from a TargetCareerPath.
    """

    query_id: str
    career_path_id: str
    career_path_title: str
    query_text: str
    query_type: SearchQueryType
    priority: float
    target_roles: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SelectedWebsite:
    """
    A manually selected website that the system may monitor later.
    """

    name: str
    url: str
    source_type: SourceType = SourceType.SELECTED_WEBSITE
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SelectedWebsite":
        return cls(
            name=data.get("name", ""),
            url=data.get("url", ""),
            source_type=SourceType(data.get("source_type", "selected_website")),
            notes=data.get("notes", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RSSFeed:
    """
    RSS feed source that the system may read later.
    """

    name: str
    url: str
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RSSFeed":
        return cls(
            name=data.get("name", ""),
            url=data.get("url", ""),
            notes=data.get("notes", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchScope:
    """
    Search boundary configuration.
    """

    scope_id: str
    name: str
    description: str = ""
    locations: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    source_types: list[SourceType] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    excluded_domains: list[str] = field(default_factory=list)
    selected_websites: list[SelectedWebsite] = field(default_factory=list)
    rss_feeds: list[RSSFeed] = field(default_factory=list)
    freshness_days: int = 30
    max_results_per_query: int = 10
    seniority_levels: list[str] = field(default_factory=list)
    enable_search_api: bool = True
    enable_rss: bool = True
    enable_selected_websites: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchScope":
        return cls(
            scope_id=data.get("scope_id", "default_scope"),
            name=data.get("name", "Default Search Scope"),
            description=data.get("description", ""),
            locations=data.get("locations", []),
            languages=data.get("languages", []),
            source_types=[
                SourceType(source_type)
                for source_type in data.get("source_types", [])
            ],
            allowed_domains=data.get("allowed_domains", []),
            excluded_domains=data.get("excluded_domains", []),
            selected_websites=[
                SelectedWebsite.from_dict(item)
                for item in data.get("selected_websites", [])
            ],
            rss_feeds=[
                RSSFeed.from_dict(item)
                for item in data.get("rss_feeds", [])
            ],
            freshness_days=data.get("freshness_days", 30),
            max_results_per_query=data.get("max_results_per_query", 10),
            seniority_levels=data.get("seniority_levels", []),
            enable_search_api=data.get("enable_search_api", True),
            enable_rss=data.get("enable_rss", True),
            enable_selected_websites=data.get(
                "enable_selected_websites",
                True,
            ),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchPlan:
    """
    Executable search plan created from SearchQuery and SearchScope.
    """

    plan_id: str
    query_id: str
    query_text: str
    query_type: SearchQueryType
    career_path_id: str
    career_path_title: str
    scope_id: str
    source_types: list[SourceType]
    locations: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    excluded_domains: list[str] = field(default_factory=list)
    freshness_days: int = 30
    max_results: int = 10
    priority: float = 0.0
    negative_keywords: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AIFilterResult:
    """
    AI filtering decision for one RawItem.

    This object records whether a raw item should move forward
    into the normalization stage.
    """

    raw_item_fingerprint: str
    title: str
    url: str
    is_relevant: bool
    confidence: float
    reason: str
    suggested_category: SignalCategory = SignalCategory.UNKNOWN
    matched_career_path_ids: list[str] = field(default_factory=list)
    action: str = "review"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchPlanExecutionStatus:
    """
    Execution status for one SearchPlan in one pipeline run.
    """

    plan_id: str
    query_id: str
    career_path_id: str
    career_path_title: str
    status: str
    reason: str
    priority: float
    selection_index: int | None = None
    batch_offset: int = 0
    batch_limit: int = 0
    raw_items_collected: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchAPIResultDiagnostic:
    """
    Parse/conversion diagnostic for one Brave web result.
    """

    plan_id: str
    query_id: str
    position: int
    status: str
    reason: str
    title: str = ""
    url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchAPIExecutionReport:
    """
    Output of Search API execution, including skipped/deferred plans.
    """

    raw_items: list["RawItem"] = field(default_factory=list)
    executed_plan_count: int = 0
    plan_statuses: list[SearchPlanExecutionStatus] = field(default_factory=list)
    result_diagnostics: list[SearchAPIResultDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RawItemFilterStatus:
    """
    AI Filter processing status for one collected RawItem.
    """

    raw_item_fingerprint: str
    raw_item_index: int
    source_type: SourceType
    title: str
    url: str
    status: str
    reason: str
    is_relevant: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AIFilterExecutionReport:
    """
    Output of AI Filter execution.
    """

    filtered_raw_items: list["RawItem"] = field(default_factory=list)
    ai_filter_results: list[AIFilterResult] = field(default_factory=list)
    raw_item_statuses: list[RawItemFilterStatus] = field(default_factory=list)
    executed_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RawItem:
    """
    Raw item collected from mock data, Search API, RSS, or selected websites.
    """

    source_type: SourceType
    title: str
    organization: str
    url: str
    published_at: str | None
    raw_text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CareerSignal:
    """
    Standard normalized career signal.

    This model will become active in Phase 10.
    """

    signal_id: str
    category: SignalCategory
    title: str
    organization: str
    url: str
    published_at: str | None
    summary: str
    source_type: SourceType
    relevance_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineSummary:
    """
    Summary information for one pipeline run.
    """

    total_raw_items: int
    total_mock_raw_items: int = 0
    total_search_api_raw_items: int = 0
    total_rss_raw_items: int = 0
    total_selected_website_raw_items: int = 0

    total_target_career_paths: int = 0
    total_search_queries: int = 0
    total_search_plans: int = 0

    total_search_api_plans_executed: int = 0
    total_search_api_plans_deferred: int = 0
    total_search_api_result_failures: int = 0
    total_rss_feeds_executed: int = 0
    total_selected_websites_executed: int = 0

    total_ai_filter_results: int = 0
    total_raw_items_sent_to_ai_filter: int = 0
    total_raw_items_failed_before_filter: int = 0
    total_filtered_raw_items: int = 0
    total_rejected_raw_items: int = 0
    total_ai_filter_accepted: int = 0
    total_ai_filter_rejected: int = 0
    total_duplicate_raw_item_urls: int = 0

    total_career_signals: int = 0

    user_profile_loaded: bool = False
    search_scope_loaded: bool = False

    search_api_executed: bool = False
    rss_executed: bool = False
    selected_websites_executed: bool = False
    ai_filter_executed: bool = False

    pipeline_status: str = "completed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineRunOutput:
    """
    Structured output of one pipeline run.
    """

    pipeline_version: str
    phase: str
    generated_at: str
    summary: PipelineSummary
    user_profile: UserProfile | None = None
    search_scope: SearchScope | None = None
    target_career_paths: list[TargetCareerPath] = field(default_factory=list)
    search_queries: list[SearchQuery] = field(default_factory=list)
    search_plans: list[SearchPlan] = field(default_factory=list)
    search_api_plan_statuses: list[SearchPlanExecutionStatus] = field(
        default_factory=list
    )
    search_api_result_diagnostics: list[SearchAPIResultDiagnostic] = field(
        default_factory=list
    )
    raw_items: list[RawItem] = field(default_factory=list)
    raw_item_filter_statuses: list[RawItemFilterStatus] = field(default_factory=list)
    ai_filter_results: list[AIFilterResult] = field(default_factory=list)
    filtered_raw_items: list[RawItem] = field(default_factory=list)
    career_signals: list[CareerSignal] = field(default_factory=list)
    scored_career_signals: list[Any] = field(default_factory=list)
    priority_assessment_diagnostics: list[Any] = field(default_factory=list)
    career_signal_routing: Any | None = None
    career_intelligence_interpretation: Any | None = None
    career_intelligence_brief: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_version": self.pipeline_version,
            "phase": self.phase,
            "generated_at": self.generated_at,
            "summary": self.summary.to_dict(),
            "user_profile": (
                self.user_profile.to_dict()
                if self.user_profile is not None
                else None
            ),
            "search_scope": (
                self.search_scope.to_dict()
                if self.search_scope is not None
                else None
            ),
            "target_career_paths": [
                career_path.to_dict()
                for career_path in self.target_career_paths
            ],
            "search_queries": [
                search_query.to_dict()
                for search_query in self.search_queries
            ],
            "search_plans": [
                search_plan.to_dict()
                for search_plan in self.search_plans
            ],
            "search_api_plan_statuses": [
                status.to_dict()
                for status in self.search_api_plan_statuses
            ],
            "search_api_result_diagnostics": [
                diagnostic.to_dict()
                for diagnostic in self.search_api_result_diagnostics
            ],
            "raw_items": [item.to_dict() for item in self.raw_items],
            "raw_item_filter_statuses": [
                status.to_dict()
                for status in self.raw_item_filter_statuses
            ],
            "ai_filter_results": [
                result.to_dict()
                for result in self.ai_filter_results
            ],
            "filtered_raw_items": [
                item.to_dict()
                for item in self.filtered_raw_items
            ],
            "career_signals": [signal.to_dict() for signal in self.career_signals],
            "scored_career_signals": [
                (
                    scored.to_dict()
                    if hasattr(scored, "to_dict")
                    else scored
                )
                for scored in self.scored_career_signals
            ],
            "priority_assessment_diagnostics": [
                (
                    diagnostic.to_dict()
                    if hasattr(diagnostic, "to_dict")
                    else diagnostic
                )
                for diagnostic in self.priority_assessment_diagnostics
            ],
            "career_signal_routing": (
                self.career_signal_routing.to_dict()
                if hasattr(self.career_signal_routing, "to_dict")
                else self.career_signal_routing
            ),
            "career_intelligence_interpretation": (
                self.career_intelligence_interpretation.to_dict()
                if hasattr(self.career_intelligence_interpretation, "to_dict")
                else self.career_intelligence_interpretation
            ),
            "career_intelligence_brief": (
                self.career_intelligence_brief.to_dict()
                if hasattr(self.career_intelligence_brief, "to_dict")
                else self.career_intelligence_brief
            ),
        }


def utc_now_iso() -> str:
    """
    Return current UTC time in ISO format.
    """

    return datetime.now(timezone.utc).isoformat()
